"""Private news server: collects feeds, groups articles about the same event into stories,
has Mistral write them up, and serves a check page."""

import email.utils
import fnmatch
import html
import http.client
import json
import os
import re
import signal
import sqlite3
import sys
import threading
import time
import tomllib
import traceback
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
import zlib
from collections import defaultdict
from contextlib import closing
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import numpy as np

DB = os.environ.get("NEWS_DB", "/data/news.db")
SOURCES = Path(__file__).with_name("sources.toml")
# Mean cosine similarity between an article and a story's articles needed to join it.
THRESHOLD = float(os.environ.get("NEWS_THRESHOLD", "0.6"))
MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
COLLECT_EVERY = 30 * 60
COLLECT_WITHIN = 120  # seconds for all feeds together
GROUP_EVERY = 3 * 3600
# A story accepts articles published up to this long after its latest one; older feed items are skipped.
OPEN_FOR = timedelta(hours=72)
SHOW = timedelta(hours=48)
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36"
OPINION = re.compile(r"/(columns?-opinie|opinie|opinions?|columns?|commentisfree|commentary)/", re.I)
# Google News links hide the section, so its opinion pieces are recognized by the label in the headline.
OPINION_TITLE = re.compile(r"^(opinion|opinie|column)\s*[|:]|\|\s*(opinion|opinie|column)\s*$", re.I)
TAG = re.compile(r"<[^>]+>")
REGIONS = ["Zwolle", "Overijssel", "NL", "EU", "US", "Global"]
DUTCH_REGIONS = {"Zwolle", "Overijssel", "NL"}  # sources there write Dutch unless sources.toml says otherwise

MISTRAL_URL = "https://api.mistral.ai/v1/chat/completions"
WRITER = "mistral-small-2603"
TRANSLATOR = "ministral-8b-2512"
# USD per million input and output tokens (mistral.ai/pricing/api, September 2026).
PRICES = {"mistral-small-2603": (0.15, 0.6), "ministral-14b-2512": (0.2, 0.2), "ministral-8b-2512": (0.15, 0.15)}
# The account has a monthly limit too; this keeps one busy day from eating the month.
DAILY_BUDGET = 0.60

SCHEMA = """
CREATE TABLE IF NOT EXISTS articles(
  id INTEGER PRIMARY KEY, url TEXT UNIQUE, outlet TEXT, region TEXT, title TEXT, summary TEXT,
  published TEXT, opinion INTEGER, vec BLOB, story INTEGER);
CREATE INDEX IF NOT EXISTS articles_outlet_title ON articles(outlet, title);
CREATE INDEX IF NOT EXISTS articles_story ON articles(story);
CREATE TABLE IF NOT EXISTS stories(id INTEGER PRIMARY KEY, updated TEXT, n INTEGER, vec BLOB);
CREATE TABLE IF NOT EXISTS sources(url TEXT PRIMARY KEY, name TEXT, checked TEXT, items INTEGER, error TEXT);
CREATE TABLE IF NOT EXISTS updates(id INTEGER PRIMARY KEY, story INTEGER, at TEXT, text TEXT, articles TEXT);
CREATE TABLE IF NOT EXISTS quotes(id INTEGER PRIMARY KEY, story INTEGER, article INTEGER, quote TEXT, quote_en TEXT);
CREATE TABLE IF NOT EXISTS spend(day TEXT PRIMARY KEY, usd REAL);
"""
# Columns added after the first release; connect() adds them to older databases.
COLUMNS = {"articles": ["lang TEXT", "title_en TEXT", "summary_en TEXT", "written INTEGER DEFAULT 0"],
           "stories": ["headline TEXT", "summary TEXT", "region TEXT", "summary_articles TEXT"]}


def now():
    return datetime.now(timezone.utc)


def iso(d):
    return d.astimezone(timezone.utc).isoformat(timespec="seconds")


def connect(path=None):
    db = sqlite3.connect(path or DB, timeout=30)
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript(SCHEMA)
    for table, columns in COLUMNS.items():
        have = {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
        for column in columns:
            if column.split()[0] not in have:
                db.execute(f"ALTER TABLE {table} ADD COLUMN {column}")
    return db


def outlet_key(name):
    """'De Telegraaf', 'telegraaf.nl' and 'NRC - Nieuws, achtergronden' reduce to the same key as their source."""
    name = re.sub(r"\s+-\s.*", "", name.lower())
    return re.sub(r"^(www\.|w3\.)|\.(com|nl|org|net|co\.uk)$|^(the|de|het) ", "", name).replace(" ", "")


def load_sources():
    """sources.toml holds one array per region (Topics: outlets without a region), outlet aliases and ignored outlets."""
    data = tomllib.loads(SOURCES.read_text())
    aliases, ignore = data.pop("aliases", {}), data.pop("ignore", [])
    sources = [s | {"region": None if region == "Topics" else region,
                    "lang": s.get("lang", "nl" if region in DUTCH_REGIONS else "en")}
               for region, items in data.items() for s in items]
    names = {outlet_key(s["name"]): s["name"] for s in sources} | {outlet_key(a): n for a, n in aliases.items()}
    return sources, names, ignore


def text(el):
    if el is None:
        return ""
    return " ".join(html.unescape(TAG.sub(" ", "".join(el.itertext()))).split())


def parse_date(s):
    if not s:
        return None
    try:
        d = email.utils.parsedate_to_datetime(s)
    except (TypeError, ValueError):
        try:
            d = datetime.fromisoformat(s.strip())
        except ValueError:
            return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def parse_feed(data):
    """RSS 2.0, RSS 1.0 (RDF) and Atom. Items without a title or an http(s) link are dropped."""
    root = ET.fromstring(data.lstrip())
    for el in root.iter():
        if isinstance(el.tag, str):
            el.tag = el.tag.rpartition("}")[2]
    out = []
    for it in (e for e in root.iter() if e.tag in ("item", "entry")):
        link = ""
        for l in it.findall("link"):
            if (l.text or "").strip():
                link = l.text.strip()
                break
            if l.get("href") and l.get("rel", "alternate") == "alternate":
                link = l.get("href")
                break
        title = text(it.find("title"))
        if not title or not link.startswith(("https://", "http://")):
            continue
        # Aggregators such as Google News name the real outlet in <source> and append it to the title.
        outlet = text(it.find("source")) or None
        if outlet and title.endswith(f" - {outlet}"):
            title = title.removesuffix(f" - {outlet}")
        else:
            outlet = None
        summary = next((e for t in ("description", "summary", "content", "encoded") if (e := it.find(t)) is not None),
                       it.find(".//description"))
        date = next((d for t in ("pubDate", "date", "published", "updated") if (d := it.findtext(t))), None)
        out.append(dict(url=link, title=title, summary="" if outlet else text(summary)[:1000],
                        published=parse_date(date), outlet=outlet))
    return out


def fetch(source):
    req = urllib.request.Request(source["url"], headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            data, deadline = b"", time.monotonic() + 60
            while len(data) < 5_000_000 and (chunk := r.read1(65536)):
                if time.monotonic() > deadline:
                    raise TimeoutError("download took over 60 seconds")
                data += chunk
        if data[:2] == b"\x1f\x8b":  # some servers send gzip unasked; cap what it unpacks to
            data = zlib.decompressobj(wbits=31).decompress(data, 20_000_000)
        return parse_feed(data), None
    except Exception as e:
        return [], f"{type(e).__name__}: {e}"


def collect(db):
    sources, names, ignore = load_sources()
    listed = set(names.values())
    t = now()
    # Socket timeouts only limit each read, so a server that drips its headers could hold a fetch forever.
    # Wait COLLECT_WITHIN in total and move on. ponytail: a stuck daemon thread lingers until its server gives up.
    results = [([], f"no answer within {COLLECT_WITHIN} s")] * len(sources)
    gate = threading.Semaphore(16)  # all at once overwhelms DNS

    def work(i):
        with gate:
            results[i] = fetch(sources[i])

    threads = [threading.Thread(target=work, args=(i,), daemon=True) for i in range(len(sources))]
    for thread in threads:
        thread.start()
    deadline = time.monotonic() + COLLECT_WITHIN
    for thread in threads:
        thread.join(max(0, deadline - time.monotonic()))
    fetched = list(results)  # a thread that finishes late must not change what we store
    new = 0
    for s, (items, error) in zip(sources, fetched):
        for a in items:
            published = min(a["published"] or t, t)
            if published < t - OPEN_FOR:
                continue
            outlet = a["outlet"] or s["name"]
            outlet = names.get(outlet_key(outlet), outlet)
            if outlet not in listed and any(fnmatch.fnmatch(outlet.lower(), pattern.lower()) for pattern in ignore):
                continue
            # The same headline from the same outlet reached us twice, e.g. directly and through Google News.
            if db.execute("SELECT 1 FROM articles WHERE outlet = ? AND title = ? AND published >= ?",
                          (outlet, a["title"], iso(t - OPEN_FOR))).fetchone():
                continue
            opinion = (s.get("opinion", False) or bool(OPINION.search(urlsplit(a["url"]).path))
                       or bool(OPINION_TITLE.search(a["title"])))
            new += db.execute(
                "INSERT OR IGNORE INTO articles(url, outlet, region, title, summary, published, opinion, lang)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (a["url"], outlet, s["region"], a["title"], a["summary"], iso(published), opinion, s["lang"])).rowcount
        db.execute("INSERT OR REPLACE INTO sources VALUES (?,?,?,?,?)", (s["url"], s["name"], iso(t), len(items), error))
    db.execute(f"DELETE FROM sources WHERE url NOT IN ({','.join('?' * len(sources))})", [s["url"] for s in sources])
    db.commit()
    failed = sum(1 for _, e in fetched if e)
    print(f"collect: {new} new articles, {len(sources) - failed}/{len(sources)} sources ok", flush=True)


_model = None


def embed(texts):
    global _model
    if _model is None:
        from fastembed import TextEmbedding
        _model = TextEmbedding(MODEL, cache_dir=os.environ.get("NEWS_MODELS"))
    v = np.array(list(_model.embed(texts)), np.float32)
    return v / np.linalg.norm(v, axis=1, keepdims=True)


def group(db):
    """Give new articles a vector, then join each to the most similar open story or start a new one."""
    # Vectors are only needed while stories are open (and for regroup). ponytail: article text is kept forever,
    # roughly 0.5 GB a year; prune old rows if the disk ever minds.
    db.execute("UPDATE articles SET vec = NULL WHERE published < ?", (iso(now() - 3 * OPEN_FOR),))
    db.execute("UPDATE stories SET vec = NULL WHERE updated < ?", (iso(now() - 3 * OPEN_FOR),))
    if os.environ.get("MISTRAL_API_KEY"):
        try:
            translate(db)
        except Exception as e:  # grouping on the Dutch text still works, just less well across languages
            print(f"translate: {e}", flush=True)
    todo = db.execute("SELECT id, COALESCE(title_en, title), COALESCE(summary_en, summary) FROM articles"
                      " WHERE vec IS NULL AND story IS NULL").fetchall()
    for i in range(0, len(todo), 256):
        batch = todo[i:i + 256]
        vecs = embed([f"{title}. {summary[:300]}" for _, title, summary in batch])
        db.executemany("UPDATE articles SET vec = ? WHERE id = ?", [(v.tobytes(), r[0]) for r, v in zip(batch, vecs)])

    new = db.execute("SELECT id, published, vec FROM articles WHERE story IS NULL AND vec IS NOT NULL ORDER BY published").fetchall()
    if not new:
        db.commit()
        return
    old = db.execute("SELECT id, updated, n, vec FROM stories WHERE updated >= ? AND vec IS NOT NULL",
                     (iso(datetime.fromisoformat(new[0][1]) - OPEN_FOR),)).fetchall()
    size, dim = len(old) + len(new), len(np.frombuffer(new[0][2], np.float32))
    sums, counts, latest = np.zeros((size, dim), np.float32), np.zeros(size), np.zeros(size)
    ids = [r[0] for r in old]
    for j, r in enumerate(old):
        sums[j], counts[j] = np.frombuffer(r[3], np.float32), r[2]
        latest[j] = datetime.fromisoformat(r[1]).timestamp()
    k, changed, assigned = len(old), set(), []
    for article, published, blob in new:
        v, ts = np.frombuffer(blob, np.float32), datetime.fromisoformat(published).timestamp()
        # Mean similarity to the story's articles. Similarity to a centroid would let loose topic clusters
        # ("anything about AI") keep growing, because their centroid sits close to every article on the topic.
        sims = np.where(latest[:k] >= ts - OPEN_FOR.total_seconds(), sums[:k] @ v / counts[:k], -1)
        j = int(sims.argmax()) if k else -1
        if j < 0 or sims[j] < THRESHOLD:
            j, k = k, k + 1
            ids.append(None)
        sums[j] += v
        latest[j] = max(latest[j], ts)
        counts[j] += 1
        changed.add(j)
        assigned.append((j, article))
    for j in changed:
        row = (iso(datetime.fromtimestamp(latest[j], timezone.utc)), int(counts[j]), sums[j].tobytes())
        if ids[j] is None:
            ids[j] = db.execute("INSERT INTO stories(updated, n, vec) VALUES (?,?,?)", row).lastrowid
        else:
            db.execute("UPDATE stories SET updated = ?, n = ?, vec = ? WHERE id = ?", (*row, ids[j]))
    db.executemany("UPDATE articles SET story = ? WHERE id = ?", [(ids[j], a) for j, a in assigned])
    db.commit()
    print(f"group: {len(todo)} embedded, {len(new)} assigned, {k - len(old)} new stories", flush=True)


def regroup(db):
    """Start grouping over for the stories that still have vectors (the last 9 days), e.g. after changing NEWS_THRESHOLD."""
    rebuilt = "SELECT id FROM stories WHERE vec IS NOT NULL"
    db.execute(f"DELETE FROM updates WHERE story IN ({rebuilt})")
    db.execute(f"DELETE FROM quotes WHERE story IN ({rebuilt})")
    db.execute(f"UPDATE articles SET story = NULL, written = 0 WHERE story IN ({rebuilt})")
    db.execute("DELETE FROM stories WHERE vec IS NOT NULL")
    group(db)


class OverBudget(Exception):
    pass


def call_mistral(model, system, payload, max_tokens):
    """One JSON-mode chat request. Returns the reply text and (input, output) token counts."""
    body = json.dumps({
        "model": model, "temperature": 0, "max_tokens": max_tokens, "response_format": {"type": "json_object"},
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
    }).encode()
    headers = {"Authorization": f"Bearer {os.environ['MISTRAL_API_KEY']}", "Content-Type": "application/json"}
    for attempt in range(4):
        try:
            with urllib.request.urlopen(urllib.request.Request(MISTRAL_URL, body, headers), timeout=180) as r:
                reply = json.load(r)
            break
        except urllib.error.HTTPError as e:
            if e.code not in (429, 500, 502, 503, 504) or attempt == 3:
                raise
            time.sleep(float(e.headers.get("Retry-After") or 5 * 2 ** attempt))
    content = reply["choices"][0]["message"]["content"]
    if isinstance(content, list):  # models that reason send thinking and text chunks
        content = "".join(c.get("text", "") for c in content if c.get("type") == "text")
    return content, (reply["usage"]["prompt_tokens"], reply["usage"]["completion_tokens"])


def ask(db, model, system, payload, max_tokens):
    """call_mistral within the daily budget; the cost is recorded before the reply is parsed."""
    day = now().date().isoformat()
    spent = db.execute("SELECT usd FROM spend WHERE day = ?", (day,)).fetchone()
    if spent and spent[0] >= DAILY_BUDGET:
        raise OverBudget(f"daily Mistral budget of ${DAILY_BUDGET:.2f} reached")
    content, (tokens_in, tokens_out) = call_mistral(model, system, payload, max_tokens)
    usd = (tokens_in * PRICES[model][0] + tokens_out * PRICES[model][1]) / 1e6
    db.execute("INSERT INTO spend VALUES (?, ?) ON CONFLICT(day) DO UPDATE SET usd = usd + excluded.usd", (day, usd))
    db.commit()
    return json.loads(content)


TRANSLATE_PROMPT = """Translate each item's Dutch title and text into English.
Keep names, numbers and meaning exactly; add nothing, leave out nothing, keep the tone neutral.
Return JSON: {"items": [{"id": <id>, "title": "<English title>", "text": "<English text>"}]}"""


def translate(db):
    """English titles and teasers for Dutch articles, shown on the page and used for grouping."""
    if db.execute("SELECT 1 FROM articles WHERE lang IS NULL LIMIT 1").fetchone():  # collected before lang existed
        db.executemany("UPDATE articles SET lang = ? WHERE lang IS NULL AND outlet = ?",
                       [(src["lang"], src["name"]) for src in load_sources()[0]])
        db.execute("UPDATE articles SET lang = CASE WHEN region IN ('Zwolle', 'Overijssel', 'NL') THEN 'nl' ELSE 'en' END"
                   " WHERE lang IS NULL")
    todo = db.execute("SELECT id, title, summary FROM articles WHERE lang = 'nl' AND title_en IS NULL"
                      " AND published >= ?", (iso(now() - OPEN_FOR),)).fetchall()
    for i in range(0, len(todo), 20):
        batch = {r[0]: r for r in todo[i:i + 20]}
        result = ask(db, TRANSLATOR, TRANSLATE_PROMPT,
                     {"items": [{"id": a, "title": t, "text": s[:300]} for a, t, s in batch.values()]}, 4000)
        for item in result.get("items", []):
            if item.get("id") in batch and isinstance(item.get("title"), str) and item["title"].strip():
                db.execute("UPDATE articles SET title_en = ?, summary_en = ? WHERE id = ?",
                           (item["title"].strip(), str(item.get("text") or "").strip(), item["id"]))
        db.commit()
    print(f"translate: {len(todo)} Dutch articles", flush=True)


RULES = """Rules:
- Write English only, in plain neutral language: no loaded or emotive words, no speculation.
- Say only what the articles say. Never add background, significance, reactions, numbers or unknowns they don't state
  (no "has drawn attention", "remains unclear", "has not been disclosed").
- Article texts are often teasers cut off mid-sentence, and some articles are only a headline. Never fill in what isn't
  there: a date, a number or a name the article doesn't give is left out. Use each article's publication time to place
  words like "Saturday" or "today", but it is when the article appeared, not when the event happened.
- Dutch articles may come with english_title, a machine translation to help you; the Dutch is what counts.
  Put Dutch words into English ("Zwolse steeg": an alley in Zwolle), but keep names of people, places and organisations.
- An article can be a roundup of several topics; write only about the topic the story is about.
- source_count is the story's number of independent sources. An article several outlets published word for word is
  given once, with the other outlets in also_in. With more than one source, state facts several sources report plainly
  and name the outlet for any claim only one source reports: "according to Euronews". With one source, don't name it;
  the app shows it.
- Mark disputed points as disputed, naming who disputes them.
- Articles marked opinion are commentary. Never present their claims as facts. If a story has only opinion articles,
  describe what is being debated and the facts the debate is about.
- Quotes: only from articles marked opinion, at most 3 per story, each one sentence or less, copied exactly
  (same words, same language) from that article's title or text, with an English translation (quote_en).
- Refer to stories and articles by their key exactly as given ("s1", "a4")."""

NEW_PROMPT = """You write the stories for a private news app. Each input story is a group of articles about one event,
possibly in Dutch. For every story write:
- headline: neutral and factual, at most 14 words.
- summary: what happened, as far as the articles tell it. One sentence when they give little more than a headline;
  never more than 4 sentences or 90 words. Short beats padded.
- region: the most specific place the story is about. One of: Zwolle (the city of Zwolle), Overijssel (elsewhere in the
  province), NL (elsewhere in the Netherlands, or national), EU (EU institutions or other European countries), US,
  Global (anywhere else, or worldwide), or None when it is about no place (a product launch, a study, an album).
""" + RULES + """
Return JSON: {"stories": [{"key": "s1", "headline": "...", "summary": "...", "region": "...",
"quotes": [{"article": "a1", "quote": "...", "quote_en": "..."}]}]}"""

UPDATE_PROMPT = """You keep a running story in a private news app up to date. You get its current text (headline,
summary, earlier updates) and new articles. For each new article that states something the current text doesn't have
yet, write one English sentence with only what that article itself says, from its own title and text: nothing from
the current text or the other articles, and no day, date, number or name the article doesn't give. Skip articles that
add nothing new; most stories need one or two sentences, some none.
""" + RULES + """
Return JSON: {"stories": [{"key": "s1", "facts": [{"article": "a1", "fact": "..."}],
"quotes": [{"article": "a1", "quote": "...", "quote_en": "..."}]}]}"""

QUOTE_MARKS = str.maketrans({c: "'" for c in "‘’‚‛"} | {c: '"' for c in "“”„‟«»"})


def normalized(s):
    return " ".join(s.translate(QUOTE_MARKS).casefold().split())


def save_quotes(db, story, quotes, articles):
    """Keep a quote only if it comes from an opinion article in this batch and appears there word for word."""
    for q in quotes if isinstance(quotes, list) else []:
        key = q.get("article") if isinstance(q, dict) else None
        a = articles.get(key) if isinstance(key, str) else None
        quote, quote_en = (str(q.get("quote") or ""), str(q.get("quote_en") or "")) if a else ("", "")
        core = normalized(quote).strip("'\" .,")
        if a and a["opinion"] and quote_en and len(core.split()) >= 4 and core in normalized(f"{a['title']} {a['text']}"):
            db.execute("INSERT INTO quotes(story, article, quote, quote_en) VALUES (?,?,?,?)",
                       (story, a["id"], quote.strip(), quote_en.strip()))


def batches(stories, max_stories, max_articles):
    batch, size = [], 0
    for story in stories:
        if batch and (len(batch) == max_stories or size + len(story["articles"]) > max_articles):
            yield batch
            batch, size = [], 0
        batch.append(story)
        size += len(story["articles"])
    if batch:
        yield batch


def mistral_articles(rows):
    """Articles as Mistral gets them. A word-for-word copy in a sister paper (same headline, same or no teaser) goes in
    once, with the longest teaser and its outlet under also_in; ids holds every copy."""
    kept, seen = [], []
    for story, aid, outlet, published, opinion, title, summary, written, title_en, *_ in rows:
        title_key, text_key = normalized(title), normalized(summary[:200])
        match = next((a for a, (t, x) in zip(kept, seen) if t == title_key and (not x or not text_key or x == text_key)), None)
        if match:
            match["ids"].append(aid)
            if outlet != match["outlet"] and outlet not in match.setdefault("also_in", []):
                match["also_in"].append(outlet)
            if len(summary) > len(match["text"]):
                match["text"] = summary[:500]
                seen[kept.index(match)] = (title_key, text_key)
            continue
        kept.append(dict(id=aid, ids=[aid], outlet=outlet, opinion=bool(opinion), title=title, text=summary[:500],
                         published=datetime.fromisoformat(published).astimezone().strftime("%a %d %b %Y %H:%M"))
                    | ({"english_title": title_en} if title_en else {}))
        seen.append((title_key, text_key))
    return kept


def write(db):
    """Mistral writes new stories and adds updates to written ones. Stories only an opinion column covers wait."""
    if not os.environ.get("MISTRAL_API_KEY"):
        return
    rows = db.execute("""SELECT a.story, a.id, a.outlet, a.published, a.opinion, a.title, a.summary, a.written,
                                a.title_en, s.headline, s.summary
                         FROM articles a JOIN stories s ON s.id = a.story
                         WHERE s.updated >= ? ORDER BY a.published""", (iso(now() - SHOW),)).fetchall()
    by_story = defaultdict(list)
    for r in rows:
        by_story[r[0]].append(r)
    new, updates = [], []
    for sid, rs in by_story.items():
        sources = source_count([(r[2], r[5]) for r in rs])
        if sources < 2 and all(r[4] for r in rs):
            continue
        headline, summary = rs[0][9], rs[0][10]
        fresh = ([r for r in rs if not r[7]] if headline else rs)[:30]
        if not fresh:
            continue
        story = dict(id=sid, sources=sources, articles=mistral_articles(fresh), ids=[r[1] for r in fresh])
        if headline:
            earlier = [u[0] for u in db.execute("SELECT text FROM updates WHERE story = ? ORDER BY at", (sid,))]
            updates.append(story | {"current": dict(headline=headline, summary=summary, updates=earlier)})
        else:
            new.append(story)
    new.sort(key=lambda st: -st["sources"])
    updates.sort(key=lambda st: -st["sources"])
    # Stories with several sources first, then updates, then single-source stories, in case the budget runs out.
    # Updates go one story per request: in batches the model left most of them empty.
    work = ([(NEW_PROMPT, b) for b in batches([st for st in new if st["sources"] > 1], 6, 30)]
            + [(UPDATE_PROMPT, b) for b in batches(updates, 1, 30)]
            + [(NEW_PROMPT, b) for b in batches([st for st in new if st["sources"] == 1], 10, 30)])
    written = updated = 0
    try:
        for prompt, batch in work:
            # Short keys per request: models copy "s2" and "a7" back reliably, long database ids not always.
            keyed, stories, n = {}, [], 0
            for i, st in enumerate(batch, 1):
                articles = {}
                for a in st["articles"]:
                    n += 1
                    articles[f"a{n}"] = a
                keyed[f"s{i}"] = (st, articles)
                stories.append({"key": f"s{i}", "source_count": st["sources"],
                                "articles": [{"key": k} | {f: v for f, v in a.items() if f not in ("id", "ids")}
                                             for k, a in articles.items()]}
                               | ({"current": st["current"]} if "current" in st else {}))
            try:
                result = ask(db, WRITER, prompt, {"stories": stories}, 4000)
                items = result.get("stories") if isinstance(result, dict) else None
                for item in items if isinstance(items, list) else []:
                    key = item.get("key") if isinstance(item, dict) else None
                    st, articles = keyed.pop(key, (None, None)) if isinstance(key, str) else (None, None)
                    if not st:
                        continue
                    if prompt is NEW_PROMPT:
                        headline, summary = str(item.get("headline") or "").strip(), str(item.get("summary") or "").strip()
                        if not headline or not summary:
                            continue
                        region = item.get("region") if item.get("region") in REGIONS else None
                        db.execute("UPDATE stories SET headline = ?, summary = ?, region = ?, summary_articles = ? WHERE id = ?",
                                   (headline, summary, region, json.dumps(st["ids"]), st["id"]))
                        written += 1
                    else:
                        # One sentence per new article, each linked to that article (and its word-for-word copies).
                        facts = item.get("facts") if isinstance(item.get("facts"), list) else []
                        for fact in facts:
                            key = fact.get("article") if isinstance(fact, dict) else None
                            text = fact.get("fact") if isinstance(fact, dict) else None
                            if isinstance(key, str) and key in articles and isinstance(text, str) and text.strip():
                                db.execute("INSERT INTO updates(story, at, text, articles) VALUES (?,?,?,?)",
                                           (st["id"], iso(now()), text.strip(), json.dumps(articles[key]["ids"])))
                                updated += 1
                    save_quotes(db, st["id"], item.get("quotes"), articles)
                    db.executemany("UPDATE articles SET written = 1 WHERE id = ?", [(i,) for i in st["ids"]])
                db.commit()
            except (ValueError, TypeError, KeyError, AttributeError, IndexError, OSError, http.client.HTTPException) as e:
                db.rollback()  # a bad reply, a bad request or the network: this batch is tried again next run
                print(f"write: batch skipped ({type(e).__name__}: {e})", flush=True)
    except OverBudget as e:
        print(f"write: {e}", flush=True)
    spent = db.execute("SELECT usd FROM spend WHERE day = ?", (now().date().isoformat(),)).fetchone()
    print(f"write: {written} new stories, {updated} updates, ${spent[0] if spent else 0:.3f} spent today", flush=True)


def esc(s):
    return html.escape(str(s))


def when(published):
    return datetime.fromisoformat(published).astimezone().strftime("%a %H:%M")


def source_count(pairs):
    """Outlets covering a story, from (outlet, headline) pairs. Identical headlines (syndicated copies, like one
    DPG article in AD, Tubantia and De Stentor) make their outlets count as one source."""
    parent = {outlet: outlet for outlet, _ in pairs}

    def root(outlet):
        while parent[outlet] != outlet:
            outlet = parent[outlet]
        return outlet

    first = {}
    for outlet, title in pairs:
        title = " ".join(title.casefold().split())
        if title in first:
            parent[root(outlet)] = root(first[title])
        else:
            first[title] = outlet
    return len({root(outlet) for outlet in parent})


def article_html(a):
    _, outlet, _, title, url, published, opinion, _, _ = a
    tag = ' <span class="tag">opinion</span>' if opinion else ""
    return (f'<li><span class="outlet">{esc(outlet)}</span> <a href="{esc(url)}" target="_blank" rel="noreferrer">'
            f'{esc(title)}</a>{tag} <time>{when(published)}</time></li>')


def link(url, label, cls=""):
    attr = f' class="{cls}"' if cls else ""
    return f'<a{attr} href="{esc(url)}" target="_blank" rel="noreferrer">{esc(label)}</a>'


def sources_line(ids, by_id, most=6):
    """ "Sources: NOS, AD" for the articles a summary or update was written from, one link per outlet."""
    first = {}
    for a in json.loads(ids or "[]"):
        if a in by_id:
            first.setdefault(by_id[a][1], by_id[a][4])
    links = [link(url, outlet) for outlet, url in list(first.items())[:most]]
    more = f" and {len(first) - most} more" if len(first) > most else ""
    return f"Sources: {', '.join(links)}{more}" if links else ""


def story_html(s):
    arts, sources = s["arts"], s["sources"]
    by_id = {a[8]: a for a in arts}
    items = "".join(article_html(a) for a in arts[:5])
    more = "".join(article_html(a) for a in arts[5:])
    if more:
        items += f'<li><details><summary>{len(arts) - 5} more</summary><ul>{more}</ul></details></li>'
    body = f'<h2>{esc(s["headline"] or arts[0][3])}</h2>'
    if s["summary"]:
        body += f'<p class="summary">{esc(s["summary"])}</p>'
        if line := sources_line(s["summary_articles"], by_id):
            body += f'<p class="from">{line}</p>'
    if s["updates"]:
        body += '<ul class="updates">' + "".join(
            f'<li><time>{when(at)}</time> {esc(text)} <span class="from">{sources_line(ids, by_id)}</span></li>'
            for at, text, ids in s["updates"]) + "</ul>"
    if s["quotes"]:
        body += '<ul class="quotes">' + "".join(
            f'<li><q>{esc(quote_en)}</q> <span class="from">{esc(outlet)}'
            f'{", translated" if normalized(quote) != normalized(quote_en) else ""} {link(url, "Source", "source")}</span></li>'
            for quote, quote_en, outlet, url in s["quotes"]) + "</ul>"
    return (f'<article class="story" data-r="{esc(" ".join(s["regions"]))}"><div class="count"><b>{sources}</b>'
            f'<span>{"sources" if sources > 1 else "source"}</span></div>'
            f'<div>{body}<ul class="articles">{items}</ul></div></article>')


def render(db):
    """The page body; the server adds the document head, the artifact snapshot uses it as is."""
    since = (iso(now() - SHOW),)
    rows = db.execute("""SELECT a.story, a.outlet, a.region, COALESCE(a.title_en, a.title), a.url, a.published,
                                a.opinion, a.title, a.id
                         FROM articles a JOIN stories s ON s.id = a.story
                         WHERE s.updated >= ? ORDER BY a.published""", since).fetchall()
    by_story = defaultdict(list)
    for r in rows:
        by_story[r[0]].append(r)
    written = {r[0]: r[1:] for r in db.execute(
        "SELECT id, headline, summary, region, summary_articles FROM stories WHERE updated >= ? AND headline IS NOT NULL",
        since)}
    updates, quotes = defaultdict(list), defaultdict(list)
    for r in db.execute("""SELECT u.story, u.at, u.text, u.articles FROM updates u JOIN stories s ON s.id = u.story
                           WHERE s.updated >= ? ORDER BY u.at""", since):
        updates[r[0]].append(r[1:])
    for r in db.execute("""SELECT q.story, q.quote, q.quote_en, a.outlet, a.url FROM quotes q
                           JOIN articles a ON a.id = q.article JOIN stories s ON s.id = q.story
                           WHERE s.updated >= ? ORDER BY q.id""", since):
        quotes[r[0]].append(r[1:])
    stories = []
    for sid, arts in by_story.items():
        sources = source_count([(a[1], a[7]) for a in arts])
        if sources < 2 and all(a[6] for a in arts):
            continue  # a lone opinion column waits until the topic gets more coverage
        headline, summary, region, summary_articles = written.get(sid, (None, None, None, None))
        # Once written, the region is Mistral's reading of what the story is about; before that, the outlets' regions.
        regions = ([region] if region else []) if headline else sorted({a[2] for a in arts if a[2]})
        stories.append(dict(arts=arts, sources=sources, headline=headline, summary=summary, regions=regions,
                            summary_articles=summary_articles, updates=updates[sid], quotes=quotes[sid]))
    stories.sort(key=lambda s: (s["sources"], s["arts"][-1][5]), reverse=True)
    multi = [s for s in stories if s["sources"] > 1]
    single = [s for s in stories if s["sources"] == 1]
    spent = db.execute("SELECT usd FROM spend WHERE day = ?", (now().date().isoformat(),)).fetchone()
    sources = db.execute("SELECT name, url, items, error FROM sources ORDER BY error IS NULL, name").fetchall()
    failing = [s for s in sources if s[3]]
    chips = f'<button type="button" data-filter="All" aria-pressed="true">All <span>{len(multi)}</span></button>'
    for r in REGIONS:
        n = sum(1 for s in multi if r in s["regions"])
        chips += f'<button type="button" data-filter="{r}" aria-pressed="false">{r} <span>{n}</span></button>'
    source_rows = "".join(
        f'<tr class="{"bad" if e else ""}"><td><a href="{esc(u)}" target="_blank" rel="noreferrer">{esc(n)}</a></td>'
        f'<td class="num">{i}</td><td>{esc(e or "ok")}</td></tr>' for n, u, i, e in sources)
    return f"""<title>News story check</title>
<style>
:root {{
  --ground: #f3f5f8; --surface: #ffffff; --ink: #17202c; --muted: #5a6573; --rule: #dbe0e7;
  --accent: #1d5bbf; --accent-soft: #e3ecfa; --warn: #9a5800; --warn-soft: #fbefdc; --bad: #b42318;
  --serif: Charter, "Bitstream Charter", "Noto Serif", Georgia, serif;
  --sans: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
  --mono: ui-monospace, "SFMono-Regular", "Roboto Mono", Menlo, monospace;
}}
@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) {{
    color-scheme: dark;
    --ground: #0e1217; --surface: #151a21; --ink: #e4e8ee; --muted: #97a1ae; --rule: #28303a;
    --accent: #7eaaff; --accent-soft: #1a2940; --warn: #e3a857; --warn-soft: #33260f; --bad: #ff7b6e;
  }}
}}
:root[data-theme="dark"] {{
  color-scheme: dark;
  --ground: #0e1217; --surface: #151a21; --ink: #e4e8ee; --muted: #97a1ae; --rule: #28303a;
  --accent: #7eaaff; --accent-soft: #1a2940; --warn: #e3a857; --warn-soft: #33260f; --bad: #ff7b6e;
}}
[hidden] {{ display: none !important; }}
body {{ background: var(--ground); color: var(--ink); font: 15px/1.5 var(--sans); padding: 0 16px; }}
main {{ max-width: 46rem; margin: 0 auto; padding-block: 28px 64px; }}
header h1 {{ font: 600 1.9rem/1.15 var(--serif); margin: 0 0 6px; text-wrap: balance; }}
header p {{ color: var(--muted); margin: 0; font-size: 0.9rem; }}
.stats {{ font-family: var(--mono); font-size: 0.8rem; font-variant-numeric: tabular-nums; margin-top: 10px; display: flex; flex-wrap: wrap; gap: 4px 16px; color: var(--muted); }}
.stats b {{ color: var(--ink); font-weight: 600; }}
nav {{ position: sticky; top: env(safe-area-inset-top, 0px); z-index: 1; background: var(--ground); display: flex; flex-wrap: wrap; gap: 6px; padding-block: 12px; margin-top: 16px; border-bottom: 1px solid var(--rule); }}
nav button {{ font: 500 0.85rem var(--sans); color: var(--ink); background: var(--surface); border: 1px solid var(--rule); border-radius: 999px; padding: 4px 12px; cursor: pointer; }}
nav button span {{ font-family: var(--mono); font-size: 0.75rem; color: var(--muted); }}
nav button[aria-pressed="true"] {{ background: var(--accent); border-color: var(--accent); color: var(--surface); }}
nav button[aria-pressed="true"] span {{ color: inherit; }}
button:focus-visible, a:focus-visible, summary:focus-visible {{ outline: 2px solid var(--accent); outline-offset: 2px; }}
.story {{ display: grid; grid-template-columns: 3.2rem 1fr; gap: 12px; padding-block: 16px; border-bottom: 1px solid var(--rule); }}
.count {{ text-align: center; font-family: var(--mono); font-variant-numeric: tabular-nums; }}
.count b {{ display: block; font-size: 1.5rem; line-height: 1.1; font-weight: 600; color: var(--accent); }}
.count span {{ font-size: 0.65rem; letter-spacing: 0.06em; text-transform: uppercase; color: var(--muted); }}
.story h2 {{ font: 600 1.15rem/1.3 var(--serif); margin: 0 0 8px; text-wrap: balance; overflow-wrap: anywhere; }}
.story ul {{ list-style: none; margin: 0; padding: 0; display: grid; gap: 6px; font-size: 0.88rem; }}
.summary {{ margin: 0 0 10px; max-width: 65ch; }}
.story ul.updates {{ margin-bottom: 10px; padding-left: 10px; border-left: 2px solid var(--accent); font-size: 0.92rem; }}
.story ul.quotes {{ margin-bottom: 10px; font-size: 0.92rem; }}
q {{ font-family: var(--serif); font-style: italic; }}
.from {{ color: var(--muted); font-size: 0.8rem; }}
.from a {{ color: var(--muted); }}
p.from {{ margin: 0 0 6px; }}
a.source {{ font: 500 0.72rem var(--sans); color: var(--accent); border: 1px solid var(--accent); border-radius: 4px; padding: 0 5px; text-decoration: none; }}
.story ul.articles {{ padding-top: 8px; border-top: 1px dashed var(--rule); }}
.story li {{ overflow-wrap: anywhere; }}
.story a {{ color: var(--ink); text-decoration-color: var(--rule); text-underline-offset: 3px; }}
.story a:hover {{ text-decoration-color: var(--accent); }}
.outlet {{ font: 600 0.72rem var(--mono); letter-spacing: 0.03em; color: var(--accent); background: var(--accent-soft); padding: 1px 6px; border-radius: 4px; margin-right: 4px; }}
.tag {{ font: 500 0.7rem var(--mono); color: var(--warn); background: var(--warn-soft); padding: 1px 6px; border-radius: 4px; }}
time {{ font-family: var(--mono); font-size: 0.75rem; color: var(--muted); white-space: nowrap; }}
summary {{ cursor: pointer; color: var(--muted); font-size: 0.85rem; }}
details ul {{ margin-top: 6px !important; }}
.section {{ margin-top: 32px; display: grid; gap: 10px; justify-items: start; }}
.section h3 {{ font: 600 1.1rem var(--serif); margin: 0; }}
.section p {{ margin: 0; color: var(--muted); font-size: 0.88rem; max-width: 60ch; }}
.toggle {{ font: 500 0.85rem var(--sans); color: var(--accent); background: none; border: 1px solid var(--accent); border-radius: 6px; padding: 6px 12px; cursor: pointer; }}
.singles {{ width: 100%; }}
.table {{ width: 100%; overflow-x: auto; }}
table {{ border-collapse: collapse; width: 100%; font-size: 0.8rem; }}
td {{ padding: 5px 8px 5px 0; border-bottom: 1px solid var(--rule); vertical-align: top; overflow-wrap: anywhere; }}
td a {{ color: var(--ink); }}
td.num {{ font-family: var(--mono); font-variant-numeric: tabular-nums; text-align: right; padding-right: 12px; }}
tr.bad td {{ color: var(--bad); }}
tr.bad td a {{ color: var(--bad); }}
</style>
<main>
<header>
  <h1>Story check</h1>
  <p>Stories with news in the last 48 hours, grouped from the articles below each one and written by Mistral. Stories with the most independent sources come first; copies of one article in sister papers count once.</p>
  <div class="stats">
    <span><b>{len(rows)}</b> articles</span><span><b>{len(multi)}</b> stories with 2+ sources</span>
    <span><b>{len(single)}</b> single-source stories</span><span>threshold <b>{THRESHOLD:.2f}</b></span>
    <span>Mistral today <b>${spent[0] if spent else 0:.2f}</b></span>
    <span>built <b>{now().astimezone().strftime("%a %d %b %H:%M")}</b></span>
  </div>
</header>
<nav aria-label="Region">{chips}</nav>
<div id="multi">{"".join(story_html(s) for s in multi)}</div>
<section class="section">
  <h3>Single-source stories</h3>
  <p>Stories only one source reported. Look here for two articles about the same event that should have been grouped.</p>
  <button type="button" class="toggle" id="show-singles">Show {len(single)} stories</button>
  <div class="singles" id="singles" hidden>{"".join(story_html(s) for s in single)}</div>
</section>
<section class="section">
  <h3>Sources</h3>
  <p>{len(sources) - len(failing)} of {len(sources)} feeds working at the last check.</p>
  <details{" open" if failing else ""}><summary>Feed status</summary>
    <div class="table"><table><tbody>{source_rows}</tbody></table></div>
  </details>
</section>
</main>
<script>
const chips = document.querySelectorAll("[data-filter]");
chips.forEach(chip => chip.addEventListener("click", () => {{
  const region = chip.dataset.filter;
  chips.forEach(c => c.setAttribute("aria-pressed", c === chip));
  document.querySelectorAll(".story").forEach(s => {{
    s.hidden = region !== "All" && !s.dataset.r.split(" ").includes(region);
  }});
}}));
const toggle = document.getElementById("show-singles"), singles = document.getElementById("singles");
toggle.addEventListener("click", () => {{
  singles.hidden = !singles.hidden;
  toggle.textContent = singles.hidden ? "Show {len(single)} stories" : "Hide single-source stories";
}});
</script>
"""


HEAD = '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">'


class Page(BaseHTTPRequestHandler):
    def do_GET(self):
        if urlsplit(self.path).path != "/":
            return self.send_error(404)
        with closing(connect()) as db:
            body = (HEAD + render(db)).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def run():
    # As the container's first process, Python would otherwise ignore the stop signal.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    server = ThreadingHTTPServer(("", 8080), Page)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    grouped = None
    while True:
        try:
            with closing(connect()) as db:
                collect(db)
                if grouped is None or time.monotonic() - grouped >= GROUP_EVERY:
                    group(db)
                    grouped = time.monotonic()
                    write(db)
        except Exception:
            traceback.print_exc()
        time.sleep(COLLECT_EVERY)


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else "run"
    if command == "run":
        run()
    else:
        with closing(connect()) as db:
            if command == "page":
                print(render(db))
            else:
                {"collect": collect, "group": group, "regroup": regroup, "translate": translate, "write": write}[command](db)
