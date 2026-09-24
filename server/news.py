"""Private news server: collects feeds, groups articles about the same event into stories,
has Mistral write them up, and serves a check page."""

import email.utils
import fnmatch
import gzip
import html
import copy
import http.client
import json
import os
import re
import signal
import sqlite3
import subprocess
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
from datetime import datetime, time as clock, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import numpy as np

DB = os.environ.get("NEWS_DB", "/data/news.db")
SOURCES = Path(__file__).with_name("sources.toml")
# Mean cosine similarity between an article and a story's articles needed to join it.
THRESHOLD = float(os.environ.get("NEWS_THRESHOLD", "0.6"))
MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
COLLECT_EVERY = 30 * 60
COLLECT_WITHIN = 120  # seconds for all feeds together
# Grouping and writing run every 3 hours at fixed local times, one of them at REPORT_HOUR; the morning report is built
# right after that run, and the app notifies at 05:30.
REPORT_HOUR = 5
REPORT_PER_REGION = 5
REPORT_PER_INTEREST = 3
# The S&P 500's last close: CNBC's quote service, Yahoo's chart API when CNBC doesn't answer (Yahoo rate-limits often).
CNBC = ("https://quote.cnbc.com/quote-html-webservice/restQuote/symbolType/symbol?symbols=.SPX&requestMethod=itv"
        "&noform=1&partnerId=2&fund=1&exthrs=1&output=json")
YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/%5EGSPC?range=1d&interval=1d"
# A story accepts articles published up to this long after its latest one; older feed items are skipped.
OPEN_FOR = timedelta(hours=72)
SHOW = timedelta(hours=48)
# The app lists stories by how many outlets cover them now: each independent source counts half as much for every FADE
# since its newest article on the story.
FADE = timedelta(hours=12)
# A subject's notable stories count as at least NOTABLE_LIFT times the biggest story's recent coverage in the subject's
# own list, fading with their newest article like coverage does; in the All list, only its NOTABLE_TOP freshest of
# those the lift at least doubles.
NOTABLE_TOP = 3
NOTABLE_LIFT = 0.4
# The writer files a story about a place the reader doesn't follow as Elsewhere. The app leaves those out, unless
# ELSEWHERE_SOURCES independent sources cover one, or ELSEWHERE_SUBJECT_SOURCES and it fits a subject.
ELSEWHERE = "Elsewhere"
ELSEWHERE_SOURCES = 30
ELSEWHERE_SUBJECT_SOURCES = 5
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36"
OPINION = re.compile(r"/(columns?-opinie|opinie|opinions?|columns?|commentisfree|commentary)/", re.I)
# Google News links hide the section, so its opinion pieces are recognized by the label in the headline.
OPINION_TITLE = re.compile(r"^(opinion|opinie|column)\s*[|:]|\|\s*(opinion|opinie|column)\s*$", re.I)
TAG = re.compile(r"<[^>]+>")
# The places the reader follows, each a part of the morning report: what belongs in it (the writer files a story under
# the most specific place it's about, or Elsewhere), and how many independent sources make a story major enough for the
# report. The feeds in sources.toml are grouped by these names. The app's settings can change them, like the interests
# below.
PLACES = {
    "Zwolle": {"about": "the city of Zwolle", "major": 2},
    "Overijssel": {"about": "the province of Overijssel outside Zwolle", "major": 2},
    "NL": {"about": "the Netherlands outside Overijssel, or the country as a whole", "major": 5},
    "EU": {"about": "the EU's institutions and politics, and news from other European countries that matters beyond their "
                    "borders", "major": 6},
    "US": {"about": "the United States", "major": 8},
    "Global": {"about": "world news that matters beyond the country it happens in: wars, diplomacy, disasters, major "
                        "elections", "major": 10},
}
REGIONS = list(PLACES)
DUTCH_REGIONS = {"Zwolle", "Overijssel", "NL"}  # sources there write Dutch unless sources.toml says otherwise
# The reader's interests, each a tab in the app; the description tells the model what belongs in it. The app's settings
# can replace them, like the places above, the spending caps below, the prompts and the feeds.
INTERESTS = {
    "AI": "artificial intelligence: AI models and products, AI companies, chips for AI, AI rules, AI research",
    "Tech": "technology: tech companies, software, apps, gadgets, the internet, telecom, cybersecurity",
    "S&P 500": "the US stock market itself: moves of the S&P 500 and other US indexes, results and share moves of "
               "large US-listed companies, Federal Reserve rate decisions. Not general economic or political news",
    "Science": "science: research findings, space, medicine and health research, climate and nature science",
    "Football": "association football (soccer) anywhere in the world: clubs, players, transfers, leagues, national "
                "teams. Not American football",
    "Music": "music: artists, releases, charts, concerts, festivals, the music industry",
}

MISTRAL_URL = "https://api.mistral.ai/v1/chat/completions"
WRITER = "mistral-small-2603"
TRANSLATOR = "ministral-8b-2512"
# USD per million input and output tokens (mistral.ai/pricing/api, September 2026).
PRICES = {"mistral-small-2603": (0.15, 0.6), "ministral-14b-2512": (0.2, 0.2), "ministral-8b-2512": (0.15, 0.15)}
# The account has a monthly limit too; this keeps one busy day from eating the month.
DAILY_BUDGET = 0.60
# With CLAUDE_CODE_OAUTH_TOKEN set, Claude writes the stories through Claude Code on the user's subscription, and
# Mistral takes over when Claude is unavailable. The cap is in API-equivalent dollars as Claude Code reports them;
# it only guards the subscription against a runaway loop.
CLAUDE_WRITER = "claude-sonnet-5"
CLAUDE_EFFORT = "low"
CLAUDE_DAILY_BUDGET = 50.00

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
CREATE TABLE IF NOT EXISTS spend(day TEXT PRIMARY KEY, usd REAL DEFAULT 0);
CREATE TABLE IF NOT EXISTS reports(day TEXT PRIMARY KEY, body TEXT);
CREATE VIRTUAL TABLE IF NOT EXISTS search_index USING fts5(text, tokenize='unicode61 remove_diacritics 2');
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
"""
# Columns added after the first release; connect() adds them to older databases.
COLUMNS = {"articles": ["lang TEXT", "title_en TEXT", "summary_en TEXT", "written INTEGER DEFAULT 0"],
           "stories": ["headline TEXT", "summary TEXT", "region TEXT", "summary_articles TEXT", "interests TEXT",
                       "notable INTEGER"],
           "spend": ["claude_usd REAL DEFAULT 0"]}


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


def setting(db, key, default):
    """What the app's settings changed, or the default. Only changes are stored, so new defaults still arrive."""
    row = db.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return json.loads(row[0]) if row else default


def places(db):
    """The places, name -> {"about", "major"}."""
    return setting(db, "places", PLACES)


def interests(db):
    """The reader's interests, name -> what belongs in it."""
    return setting(db, "interests", INTERESTS)


def fold(name):
    """A tab name as the server compares them: letters and digits only, any case."""
    return re.sub(r"\W", "", name).casefold()


def budgets(db):
    """The daily spending caps in dollars."""
    return {"claude": CLAUDE_DAILY_BUDGET, "mistral": DAILY_BUDGET} | setting(db, "budgets", {})


def outlet_key(name):
    """'De Telegraaf', 'telegraaf.nl' and 'NRC - Nieuws, achtergronden' reduce to the same key as their source."""
    name = re.sub(r"\s+-\s.*", "", name.lower())
    return re.sub(r"^(www\.|w3\.)|\.(com|nl|org|net|co\.uk)$|^(the|de|het) ", "", name).replace(" ", "")


def load_sources(db=None, edited=True):
    """sources.toml holds one array per region (Topics: outlets without a region), outlet aliases, ignored outlets
    and the outlets' lean. With db, the places the app renamed or deleted apply (a deleted place's feeds have no
    region), and unless edited is False, so do the feeds it removed and added."""
    data = tomllib.loads(SOURCES.read_text())
    aliases, ignore = data.pop("aliases", {}), data.pop("ignore", [])
    data.pop("lean", None)
    edits = setting(db, "sources", {}) if db is not None and edited else {}
    renamed = setting(db, "renamed", {}) if db is not None else {}
    # A feed the app added that sources.toml later lists too: the app's version counts.
    removed = set(edits.get("removed", [])) | {s["url"] for s in edits.get("added", [])}
    sources = [s | {"region": renamed.get(region, region) if region != "Topics" else None,
                    "lang": s.get("lang", "nl" if region in DUTCH_REGIONS else "en"), "opinion": s.get("opinion", False)}
               for region, items in data.items() for s in items if s["url"] not in removed] + edits.get("added", [])
    if db is not None:
        known = places(db)
        sources = [s if s["region"] in known else s | {"region": None} for s in sources]
    names = {outlet_key(s["name"]): s["name"] for s in sources} | {outlet_key(a): n for a, n in aliases.items()}
    return sources, names, ignore


def leans(db=None):
    """Outlet key -> left, center or right, relative to the outlet's own country. Outlets not rated have no entry.
    With db, the app's changes count too."""
    table = tomllib.loads(SOURCES.read_text()).get("lean", {})
    rated = {outlet_key(name): lean for lean, names in table.items() for name in names}
    for name, lean in (setting(db, "sources", {}).get("lean", {}) if db is not None else {}).items():
        rated[outlet_key(name)] = lean
    return {key: lean for key, lean in rated.items() if lean}


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
    sources, names, ignore = load_sources(db)
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
    # Committed before the slow parts (the model, the embedding), so a settings change from the app doesn't wait on them.
    db.commit()
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
        db.commit()

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
    db.execute("DELETE FROM search_index")  # new stories reuse the old ids; the next index() starts over
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


class ClaudeUnavailable(Exception):
    """Claude can't write for now (a usage limit, the login, no CLI): Mistral writes the rest of the run."""


class ClaudeFailed(ValueError):
    """One request went wrong (a timeout, an unreadable reply): that batch is tried again next run."""


def call_claude(model, system, payload, schema):
    """One request through Claude Code in print mode: our system prompt instead of Claude Code's, no tools, one turn.
    Returns the reply text and Claude Code's API-equivalent cost estimate."""
    # --tools "" removes Claude Code's own tools; the JSON schema is answered through its structured-output tool,
    # which takes a second turn (a third if the first answer doesn't validate).
    cmd = ["claude", "-p", "--model", model, "--effort", CLAUDE_EFFORT, "--system-prompt", system,
           "--output-format", "json", "--json-schema", json.dumps(schema), "--tools", "",
           "--max-turns", "3", "--no-session-persistence", "--disable-slash-commands", "--strict-mcp-config"]
    try:
        run = subprocess.run(cmd, input=json.dumps(payload, ensure_ascii=False), capture_output=True, text=True,
                             timeout=600)
    except OSError as e:
        raise ClaudeUnavailable(f"{type(e).__name__}: {e}") from e
    except subprocess.TimeoutExpired as e:
        raise ClaudeFailed("no reply within 10 minutes") from e
    try:
        reply = json.loads(run.stdout)
    except ValueError:
        reply = None
    if not isinstance(reply, dict):
        detail = f"unreadable reply: {(run.stdout or run.stderr).strip()[:200]}"
        # Claude Code itself failing (a rejected flag, a crash) won't fix itself between batches.
        raise (ClaudeUnavailable if run.returncode else ClaudeFailed)(detail)
    if reply.get("is_error") or run.returncode:
        detail = str(reply.get("result") or run.stderr.strip() or f"exit {run.returncode}")[:300]
        if reply.get("api_error_status") in (401, 403, 429) or re.search(
                r"usage limit|rate limit|limit reached|authenticat|login|credit|billing", detail, re.I):
            raise ClaudeUnavailable(detail)
        raise ClaudeFailed(detail)
    if not isinstance(reply.get("structured_output"), dict):
        raise ClaudeFailed("the reply has no structured output")
    return json.dumps(reply["structured_output"]), float(reply.get("total_cost_usd") or 0)


def ask(db, model, kind, payload, max_tokens):
    """One JSON request with the prompt of this kind, within the day's budget; the cost is recorded before the reply is
    parsed."""
    system, caps = system_prompt(db, kind), budgets(db)
    day = now().date().isoformat()
    # Only read before the request: a write here would hold the database lock for as long as the model takes.
    usd, claude_usd = db.execute("SELECT COALESCE(usd, 0), COALESCE(claude_usd, 0) FROM spend WHERE day = ?",
                                 (day,)).fetchone() or (0, 0)
    if model.startswith("claude"):
        if claude_usd >= caps["claude"]:
            raise ClaudeUnavailable(f"daily Claude budget of ${caps['claude']:.2f} (API-equivalent) reached")
        content, cost = call_claude(model, system, payload, reply_schema(db, kind))
        db.execute("INSERT OR IGNORE INTO spend(day) VALUES (?)", (day,))
        db.execute("UPDATE spend SET claude_usd = COALESCE(claude_usd, 0) + ? WHERE day = ?", (cost, day))
    else:
        if usd >= caps["mistral"]:
            raise OverBudget(f"daily Mistral budget of ${caps['mistral']:.2f} reached")
        content, (tokens_in, tokens_out) = call_mistral(model, system, payload, max_tokens)
        cost = (tokens_in * PRICES[model][0] + tokens_out * PRICES[model][1]) / 1e6
        db.execute("INSERT OR IGNORE INTO spend(day) VALUES (?)", (day,))
        db.execute("UPDATE spend SET usd = COALESCE(usd, 0) + ? WHERE day = ?", (cost, day))
    db.commit()
    return json.loads(content)


TRANSLATE_PROMPT = """Translate each item's Dutch title and text into English.
Keep names, numbers and meaning exactly; add nothing, leave out nothing, keep the tone neutral."""


def translate(db):
    """English titles and teasers for Dutch articles, shown on the page and used for grouping."""
    if db.execute("SELECT 1 FROM articles WHERE lang IS NULL LIMIT 1").fetchone():  # collected before lang existed
        db.executemany("UPDATE articles SET lang = ? WHERE lang IS NULL AND outlet = ?",
                       [(src["lang"], src["name"]) for src in load_sources(db)[0]])
        db.execute("UPDATE articles SET lang = CASE WHEN region IN ('Zwolle', 'Overijssel', 'NL') THEN 'nl' ELSE 'en' END"
                   " WHERE lang IS NULL")
    todo = db.execute("SELECT id, title, summary FROM articles WHERE lang = 'nl' AND title_en IS NULL"
                      " AND published >= ?", (iso(now() - OPEN_FOR),)).fetchall()
    for i in range(0, len(todo), 20):
        batch = {r[0]: r for r in todo[i:i + 20]}
        result = ask(db, TRANSLATOR, "translate",
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
- Keep the hedges and attributions the articles use: "if", "for now", "according to experts", "officials say".
  An article's view or analysis is reported as its view: "The Verge writes that ...".
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
- region: the most specific place the story is about, from the places below. Elsewhere when it is about a place none
  of them covers; the reader doesn't follow it. None when it is about no place (a product launch, a study, an album)."""

UPDATE_PROMPT = """You keep a running story in a private news app up to date. You get its current text (headline,
summary, earlier updates) and new articles. For each new article that states something the current text doesn't have
yet, write at most one English sentence with only what that article itself says, from its own title and text: nothing from
the current text or the other articles, and no day, date, number or name the article doesn't give. Skip articles that
add nothing new; most stories need one or two sentences, some none."""

TAG_PROMPT = """You sort the stories of a private news app into the reader's interests. For each story, list the
interests it is about, from the list below. A story can have several interests or none; most stories have none. Pick an
interest only when the story itself is about it, not when it comes up in passing. Include every story, with an empty
list when no interest fits, and refer to stories by their key exactly as given ("s1").
Also say whether each story is notable: true when someone who follows one of its interests closely would want to know
about it even if only one outlet reports it, like a new AI model, a major launch, breach or ruling, or a big transfer or
result; false for routine items like reviews, how-tos, deals, rumours, previews, opinion and minor updates, and for
stories without an interest."""

REPORT_PROMPT = """You write the morning report of a private news app. For each story write one plain English sentence of at
most 18 words, two lines on a phone, that says what happened, including the latest update if there is one. Use only the given headline,
summary and updates: no new facts, no opinions, no loaded words. The headline is shown above your sentence, so don't
repeat it. Refer to stories by their key exactly as given ("s1")."""

# The prompts by kind, with their name and what the app says about them. The app can replace each text; the code adds
# the writing rules, the interests and the reply format, which it depends on and so can't be changed.
PROMPTS = {"translate": TRANSLATE_PROMPT, "rules": RULES, "new": NEW_PROMPT, "update": UPDATE_PROMPT, "tag": TAG_PROMPT,
           "report": REPORT_PROMPT}
PROMPT_NOTES = {
    "translate": ("Translation", "Mistral turns Dutch headlines and teasers into English. The reply format is added after it."),
    "rules": ("Writing rules", "Added to the prompts for new stories and for updates."),
    "new": ("New stories", "Writes a new story's headline and summary. The places, the writing rules and the reply format are "
                           "added after it."),
    "update": ("Updates", "Adds a sentence to a story for each new article. The writing rules and the reply format are added "
                          "after it."),
    "tag": ("Interests", "Sorts stories into your interests. The interests and the reply format are added after it."),
    "report": ("Morning report", "Writes the one-sentence gists of the morning report. The reply format is added after it."),
}
FORMATS = {
    "translate": 'Return JSON: {"items": [{"id": <id>, "title": "<English title>", "text": "<English text>"}]}',
    "new": """Return JSON: {"stories": [{"key": "s1", "headline": "...", "summary": "...", "region": "...",
"quotes": [{"article": "a1", "quote": "...", "quote_en": "..."}]}]}""",
    "update": """Return JSON: {"stories": [{"key": "s1", "facts": [{"article": "a1", "fact": "..."}],
"quotes": [{"article": "a1", "quote": "...", "quote_en": "..."}]}]}""",
    "tag": 'Return JSON: {"stories": [{"key": "s1", "interests": ["AI", "Tech"], "notable": false}]}',
    "report": 'Return JSON: {"stories": [{"key": "s1", "gist": "..."}]}',
}

QUOTES_SCHEMA = {"type": "array", "items": {"type": "object", "required": ["article", "quote", "quote_en"],
                                             "properties": {"article": {"type": "string"}, "quote": {"type": "string"},
                                                            "quote_en": {"type": "string"}}}}
SCHEMAS = {
    "new": {"type": "object", "required": ["stories"], "properties": {"stories": {"type": "array", "items": {
        "type": "object", "required": ["key", "headline", "summary", "region"],
        "properties": {"key": {"type": "string"}, "headline": {"type": "string"}, "summary": {"type": "string"},
                       "region": {"enum": REGIONS + [ELSEWHERE, "None"]}, "quotes": QUOTES_SCHEMA}}}}},
    "update": {"type": "object", "required": ["stories"], "properties": {"stories": {"type": "array", "items": {
        "type": "object", "required": ["key", "facts"],
        "properties": {"key": {"type": "string"}, "quotes": QUOTES_SCHEMA, "facts": {"type": "array", "items": {
            "type": "object", "required": ["article", "fact"],
            "properties": {"article": {"type": "string"}, "fact": {"type": "string"}}}}}}}}},
    "report": {"type": "object", "required": ["stories"], "properties": {"stories": {"type": "array", "items": {
        "type": "object", "required": ["key", "gist"],
        "properties": {"key": {"type": "string"}, "gist": {"type": "string"}}}}}},
}


def system_prompt(db, kind):
    """A prompt as the model gets it: its text (the app's when it changed it), then the places, the writing rules or the
    interests where they belong, then the reply format."""
    edited = setting(db, "prompts", {})
    parts = [edited.get(kind) or PROMPTS[kind]]
    if kind == "new":
        parts.append("Places:\n" + "\n".join(f"- {name}: {place['about']}" for name, place in places(db).items()))
    if kind in ("new", "update"):
        parts.append(edited.get("rules") or RULES)
    if kind == "tag":
        parts.append("Interests:\n" + "\n".join(f"- {name}: {about}" for name, about in interests(db).items()))
    return "\n".join(parts + [FORMATS[kind]])


def reply_schema(db, kind):
    """What Claude Code checks a reply against; the places and interests can change, so their names go in here."""
    if kind == "new":
        schema = copy.deepcopy(SCHEMAS["new"])
        schema["properties"]["stories"]["items"]["properties"]["region"] = {"enum": list(places(db)) + [ELSEWHERE, "None"]}
        return schema
    if kind != "tag":
        return SCHEMAS[kind]
    return {"type": "object", "required": ["stories"], "properties": {"stories": {"type": "array", "items": {
        "type": "object", "required": ["key", "interests", "notable"],
        "properties": {"key": {"type": "string"}, "interests": {"type": "array", "items": {"enum": list(interests(db))}},
                       "notable": {"type": "boolean"}}}}}}

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


def available_writers():
    return ([CLAUDE_WRITER] if os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") else []) + (
        [WRITER] if os.environ.get("MISTRAL_API_KEY") else [])


def write(db):
    """Claude or Mistral writes new stories and adds updates to written ones. Stories only an opinion column covers wait."""
    writers = available_writers()
    if not writers:
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
    work = ([("new", b) for b in batches([st for st in new if st["sources"] > 1], 6, 30)]
            + [("update", b) for b in batches(updates, 1, 30)]
            + [("new", b) for b in batches([st for st in new if st["sources"] == 1], 10, 30)])
    written = updated = failures = 0
    try:
        for kind, batch in work:
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
                while True:
                    try:
                        result = ask(db, writers[0], kind, {"stories": stories}, 4000)
                        failures = 0
                        break
                    except ClaudeFailed as e:
                        failures += 1
                        if failures < 3 or writers[0] != CLAUDE_WRITER or len(writers) == 1:
                            raise
                        print(f"write: Claude failed three times in a row ({e}), switching to Mistral", flush=True)
                        writers.pop(0)
                    except ClaudeUnavailable as e:  # a usage limit or the login: Mistral writes the rest of this run
                        print(f"write: Claude unavailable ({e}), switching to Mistral", flush=True)
                        writers.pop(0)
                        if not writers:
                            raise OverBudget("no writer available") from e
                items = result.get("stories") if isinstance(result, dict) else None
                known = places(db)  # as they are now: the app may have renamed one while the model was writing
                for item in items if isinstance(items, list) else []:
                    key = item.get("key") if isinstance(item, dict) else None
                    st, articles = keyed.pop(key, (None, None)) if isinstance(key, str) else (None, None)
                    if not st:
                        continue
                    if kind == "new":
                        headline, summary = str(item.get("headline") or "").strip(), str(item.get("summary") or "").strip()
                        if not headline or not summary:
                            continue
                        region = item.get("region")
                        region = region if isinstance(region, str) and region in [*known, ELSEWHERE] else None
                        db.execute("UPDATE stories SET headline = ?, summary = ?, region = ?, summary_articles = ? WHERE id = ?",
                                   (headline, summary, region, json.dumps(st["ids"]), st["id"]))
                        written += 1
                    else:
                        # One sentence per new article, each linked to that article (and its word-for-word copies).
                        facts = item.get("facts") if isinstance(item.get("facts"), list) else []
                        cited = set()
                        for fact in facts:
                            key = fact.get("article") if isinstance(fact, dict) else None
                            text = fact.get("fact") if isinstance(fact, dict) else None
                            if (isinstance(key, str) and key in articles and key not in cited and isinstance(text, str)
                                    and 0 < len(text.split()) <= 50):  # one short sentence per article, the first one
                                cited.add(key)
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
    spent = db.execute("SELECT COALESCE(usd, 0), COALESCE(claude_usd, 0) FROM spend WHERE day = ?",
                       (now().date().isoformat(),)).fetchone() or (0, 0)
    print(f"write: {written} new stories, {updated} updates; today Mistral ${spent[0]:.3f},"
          f" Claude ${spent[1]:.3f} API-equivalent", flush=True)


def tag(db):
    """Sorts the written stories into the reader's interests, 100 per request, and marks the notable ones. A story is
    tagged once; one the reply leaves out is sent again next run, like one sorted before notable existed. When Claude
    fails, Mistral tags the rest of the run."""
    writers = available_writers()
    wanted = interests(db)
    if not wanted:
        return
    names = {re.sub(r"\W", "", name).casefold(): name for name in wanted}  # Mistral may write "S&P500" or "football"
    rows = db.execute("SELECT id, headline, summary FROM stories WHERE updated >= ? AND headline IS NOT NULL"
                      " AND (interests IS NULL OR notable IS NULL AND interests != '[]') ORDER BY id",
                      (iso(now() - SHOW),)).fetchall()
    tagged, start = 0, 0
    while start < len(rows) and writers:
        batch = rows[start:start + 100]
        keyed = {f"s{i}": sid for i, (sid, _, _) in enumerate(batch, 1)}
        payload = {"stories": [{"key": f"s{i}", "headline": headline, "summary": summary}
                               for i, (_, headline, summary) in enumerate(batch, 1)]}
        try:
            result = ask(db, writers[0], "tag", payload, 4000)
            # The app may have changed the interests while the model was sorting; then the pass it started sorts
            # these stories again. Checked with the write lock held, so a change can't slip in before the writes.
            db.execute("BEGIN IMMEDIATE")
            if interests(db) != wanted:
                db.rollback()
                print("tag: the interests changed, sorted again in the next pass", flush=True)
                break
            items = result.get("stories") if isinstance(result, dict) else None
            for item in items if isinstance(items, list) else []:
                key = item.get("key") if isinstance(item, dict) else None
                sid = keyed.pop(key, None) if isinstance(key, str) else None
                answer = item.get("interests") if sid else None
                if isinstance(answer, list):
                    given = {names.get(re.sub(r"\W", "", x).casefold()) for x in answer if isinstance(x, str)}
                    picked = [name for name in wanted if name in given]
                    db.execute("UPDATE stories SET interests = ?, notable = ? WHERE id = ?",
                               (json.dumps(picked), int(bool(picked) and item.get("notable") is True), sid))
                    tagged += bool(picked)
            db.commit()
            if keyed:
                print(f"tag: {len(keyed)} of {len(batch)} stories missing from the reply, sent again next run", flush=True)
            start += 100
        except (ClaudeUnavailable, ClaudeFailed) as e:
            print(f"tag: Claude failed ({e}), switching to Mistral", flush=True)
            writers.pop(0)
        except (OverBudget, ValueError, TypeError, KeyError, AttributeError, OSError, http.client.HTTPException) as e:
            db.rollback()
            print(f"tag: stopped until the next run ({type(e).__name__}: {e})", flush=True)
            break
    print(f"tag: {tagged} stories have an interest", flush=True)


def market():
    """The S&P 500's last close and its change from the close before, or None when neither source answers."""
    for url in (CNBC, YAHOO):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=20) as response:
                data = json.load(response)
            if url == CNBC:
                quote = data["FormattedQuoteResult"]["FormattedQuote"][0]
                close, before = (float(quote[k].replace(",", "")) for k in ("last", "previous_day_closing"))
                date = datetime.fromisoformat(quote["last_time"][:10]).date()
            else:
                meta = data["chart"]["result"][0]["meta"]
                close, before = float(meta["regularMarketPrice"]), float(meta["chartPreviousClose"])
                date = datetime.fromtimestamp(meta["regularMarketTime"], timezone(timedelta(seconds=meta["gmtoffset"]))).date()
            return {"name": "S&P 500", "close": round(close, 2), "change": round((close / before - 1) * 100, 2),
                    "date": date.isoformat()}
        except (OSError, ValueError, KeyError, IndexError, TypeError, AttributeError, ZeroDivisionError) as e:
            print(f"report: no S&P 500 close from {urlsplit(url).hostname} ({type(e).__name__}: {e})", flush=True)
    return None


def gists(db, stories):
    """One sentence per story for the report, from Claude or Mistral; the summary's first sentence when neither answers."""
    first = {s["id"]: (re.match(r".{20,}?[.!?](?=\s|$)", s["summary"]) or [s["summary"]])[0] for s in stories}
    keyed = {f"s{i}": s["id"] for i, s in enumerate(stories, 1)}
    payload = {"stories": [{"key": f"s{i}", "headline": s["headline"], "summary": s["summary"],
                            "updates": [text for _, text, _ in s["updates"]]} for i, s in enumerate(stories, 1)]}
    for writer in available_writers() if stories else []:
        try:
            result = ask(db, writer, "report", payload, 4000)
            items = result.get("stories") if isinstance(result, dict) else None
            for item in items if isinstance(items, list) else []:
                key, gist = (item.get("key"), item.get("gist")) if isinstance(item, dict) else (None, None)
                if isinstance(key, str) and key in keyed and isinstance(gist, str) and 0 < len(gist.split()) <= 30:
                    first[keyed[key]] = gist.strip()
            break
        except (ClaudeUnavailable, OverBudget, ValueError, TypeError, KeyError, AttributeError, OSError,
                http.client.HTTPException) as e:
            print(f"report: {writer} wrote no gists ({type(e).__name__}: {e})", flush=True)
    return first


def morning(db, t=None):
    """Builds the day's morning report once the REPORT_HOUR run is done: the S&P 500's last close, the major stories of
    the last 24 hours per region, and the top stories per interest."""
    t = t or datetime.now().astimezone()
    end = datetime.combine(t.date(), clock(REPORT_HOUR)).astimezone()
    day = end.date().isoformat()
    if t < end:
        return
    row = db.execute("SELECT body FROM reports WHERE day = ?", (day,)).fetchone()
    if row:
        body = json.loads(row[0])
        if body["market"] is None and t.hour < 12:  # no close when the report was built: try again until noon
            body["market"] = market()
            if body["market"]:
                db.execute("UPDATE reports SET body = ? WHERE day = ?", (json.dumps(body), day))
                db.commit()
        return
    # Local midnight arithmetic, so a night with a clock change still starts the window at REPORT_HOUR.
    since = iso(datetime.combine(end.date() - timedelta(days=1), clock(REPORT_HOUR)).astimezone())
    stories = [s for s in shown(db) if s["headline"] and followed(s) and any(a[5] >= since for a in s["arts"])]
    sections, picked = [], set()
    for region, place in places(db).items():
        major = [s for s in stories if region in s["regions"] and s["sources"] >= place["major"]][:REPORT_PER_REGION]
        sections.append((region, major))
        picked |= {s["id"] for s in major}
    big = [s for s in stories if s["regions"] == [ELSEWHERE] and s["sources"] >= ELSEWHERE_SOURCES][:REPORT_PER_REGION]
    sections.append((ELSEWHERE, big))
    picked |= {s["id"] for s in big}
    for interest in interests(db):
        top = [s for s in stories if interest in s["interests"] and s["id"] not in picked][:REPORT_PER_INTEREST]
        sections.append((interest, top))
        picked |= {s["id"] for s in top}
    listed = [s for _, section in sections for s in section]
    gist = gists(db, listed)
    body = {"day": day, "built": iso(now()), "since": since, "market": market(),
            "sections": [{"title": title, "stories": [{"id": s["id"], "headline": s["headline"], "gist": gist[s["id"]],
                                                       "sources": s["sources"]} for s in section]}
                         for title, section in sections if section]}
    db.execute("INSERT OR REPLACE INTO reports(day, body) VALUES (?, ?)", (day, json.dumps(body)))
    db.commit()
    print(f"report: {len(listed)} stories for {day}", flush=True)


def latest_report(db):
    row = db.execute("SELECT body FROM reports ORDER BY day DESC LIMIT 1").fetchone()
    return json.loads(row[0]) if row else None


def esc(s):
    return html.escape(str(s))


def when(published):
    return datetime.fromisoformat(published).astimezone().strftime("%a %H:%M")


def independent(pairs):
    """The outlets of a story grouped per independent source, from (outlet, headline) pairs. Identical headlines
    (syndicated copies, like one DPG article in AD, Tubantia and De Stentor) put their outlets in one group."""
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
    groups = defaultdict(list)
    for outlet in parent:
        groups[root(outlet)].append(outlet)
    return list(groups.values())


def source_count(pairs):
    return len(independent(pairs))


def weight(s, t):
    """A story's place in the app's list at time t. A story outlets keep covering stays up; a big one they covered
    yesterday makes way for today's news, further down but still there."""
    newest = {}
    for a in s["arts"]:
        newest[a[1]] = max(newest.get(a[1], a[5]), a[5])
    return sum(0.5 ** ((t - datetime.fromisoformat(max(newest[outlet] for outlet in group))) / FADE)
               for group in independent([(a[1], a[7]) for a in s["arts"]]))


def ranked(db, stories):
    """The All list's order: by recent coverage, relative to the biggest story. A subject's notable stories that few
    outlets cover would sink under that, so they count as NOTABLE_LIFT times the biggest story, fading like coverage
    does: in the subject's own list all of them (the app orders it with each story's coverage and lift), in the All list
    the NOTABLE_TOP freshest per subject of those the lift at least doubles; the subject's list keeps the others."""
    t, subjects = now(), set(interests(db))
    for s in stories:
        s["coverage"], s["lift"] = weight(s, t), {}
    top = max((s["coverage"] for s in stories), default=1)
    candidates = defaultdict(list)
    for s in stories:
        lift = NOTABLE_LIFT * 0.5 ** ((t - datetime.fromisoformat(s["arts"][-1][5])) / FADE)
        if s["notable"] and lift > s["coverage"] / top:
            for subject in set(s["interests"]) & subjects:
                s["lift"][subject] = lift
                if lift >= 2 * s["coverage"] / top:  # the All list's few places go to stories it does much for
                    candidates[subject].append((lift, s["id"]))
    in_all = {sid for entries in candidates.values() for _, sid in sorted(entries, reverse=True)[:NOTABLE_TOP]}

    def key(s):
        lifts = s["lift"].values() if s["id"] in in_all else []
        return max([s["coverage"] / top, *lifts]), s["coverage"]

    return sorted(stories, key=key, reverse=True)


def followed(s):
    """Whether the app lists a story: not when it is minor news from a place the reader doesn't follow."""
    return (s["regions"] != [ELSEWHERE] or s["sources"] >= ELSEWHERE_SOURCES
            or bool(s["interests"]) and s["sources"] >= ELSEWHERE_SUBJECT_SOURCES)


def lean_counts(pairs, lean):
    """Independent sources per lean; a group of copies takes the lean of the first rated outlet in it."""
    counts = dict.fromkeys(["left", "center", "right"], 0)
    for group in independent(pairs):
        rated = [lean[outlet_key(outlet)] for outlet in group if outlet_key(outlet) in lean]
        if rated:
            counts[rated[0]] += 1
    return counts


def article_html(a):
    _, outlet, _, title, url, published, opinion, _, _ = a
    tag = ' <span class="tag">opinion</span>' if opinion else ""
    return (f'<li><span class="outlet">{esc(outlet)}</span> <a href="{esc(url)}" target="_blank" rel="noreferrer">'
            f'{esc(title)}</a>{tag} <time>{when(published)}</time></li>')


def link(url, label, cls=""):
    attr = f' class="{cls}"' if cls else ""
    return f'<a{attr} href="{esc(url)}" target="_blank" rel="noreferrer">{esc(label)}</a>'


def cited(ids, by_id):
    """Outlet -> its first article's link, for the articles a summary or update was written from."""
    first = {}
    for a in json.loads(ids or "[]"):
        if a in by_id:
            first.setdefault(by_id[a][1], by_id[a][4])
    return first


def sources_line(ids, by_id, most=6):
    """ "Sources: NOS, AD" for the articles a summary or update was written from, one link per outlet."""
    first = cited(ids, by_id)
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
    return (f'<article class="story" data-r="{esc("|".join(s["regions"]))}"><div class="count"><b>{sources}</b>'
            f'<span>{"sources" if sources > 1 else "source"}</span></div>'
            f'<div>{body}<ul class="articles">{items}</ul></div></article>')


def shown(db, ids=None):
    """The stories with news in the last SHOW hours, or the stories with these ids, most independent sources first."""
    if ids is None:
        where, params = "s.updated >= ?", (iso(now() - SHOW),)
    else:
        where, params = f"s.id IN ({','.join('?' * len(ids))})", tuple(ids)
    rows = db.execute(f"""SELECT a.story, a.outlet, a.region, COALESCE(a.title_en, a.title), a.url, a.published,
                                 a.opinion, a.title, a.id
                          FROM articles a JOIN stories s ON s.id = a.story
                          WHERE {where} ORDER BY a.published""", params).fetchall()
    by_story = defaultdict(list)
    for r in rows:
        by_story[r[0]].append(r)
    written = {r[0]: r[1:] for r in db.execute(
        "SELECT s.id, s.headline, s.summary, s.region, s.summary_articles, s.interests, s.notable FROM stories s"
        f" WHERE {where} AND s.headline IS NOT NULL", params)}
    updates, quotes = defaultdict(list), defaultdict(list)
    for r in db.execute(f"""SELECT u.story, u.at, u.text, u.articles FROM updates u JOIN stories s ON s.id = u.story
                            WHERE {where} ORDER BY u.at, u.id""", params):
        updates[r[0]].append(r[1:])
    for r in db.execute(f"""SELECT q.story, q.quote, q.quote_en, a.outlet, a.url FROM quotes q
                            JOIN articles a ON a.id = q.article JOIN stories s ON s.id = q.story
                            WHERE {where} ORDER BY q.id""", params):
        quotes[r[0]].append(r[1:])
    stories = []
    for sid, arts in by_story.items():
        sources = source_count([(a[1], a[7]) for a in arts])
        if sources < 2 and all(a[6] for a in arts):
            continue  # a lone opinion column waits until the topic gets more coverage
        headline, summary, region, summary_articles, interests, notable = written.get(sid, (None,) * 6)
        # Once written, the region is Mistral's reading of what the story is about; before that, the outlets' regions.
        regions = ([region] if region else []) if headline else sorted({a[2] for a in arts if a[2]})
        stories.append(dict(id=sid, arts=arts, sources=sources, headline=headline, summary=summary, regions=regions,
                            summary_articles=summary_articles, interests=json.loads(interests or "[]"), notable=bool(notable),
                            updates=updates[sid], quotes=quotes[sid]))
    stories.sort(key=lambda s: (s["sources"], s["arts"][-1][5]), reverse=True)
    return stories


def render(db):
    """The page body; the server adds the document head, the artifact snapshot uses it as is."""
    stories = shown(db)
    multi = [s for s in stories if s["sources"] > 1]
    single = [s for s in stories if s["sources"] == 1]
    spent = db.execute("SELECT COALESCE(usd, 0), COALESCE(claude_usd, 0) FROM spend WHERE day = ?",
                       (now().date().isoformat(),)).fetchone() or (0, 0)
    sources = db.execute("SELECT name, url, items, error FROM sources ORDER BY error IS NULL, name").fetchall()
    failing = [s for s in sources if s[3]]
    chips = f'<button type="button" data-filter="All" aria-pressed="true">All <span>{len(multi)}</span></button>'
    for r in places(db):
        n = sum(1 for s in multi if r in s["regions"])
        chips += f'<button type="button" data-filter="{esc(r)}" aria-pressed="false">{esc(r)} <span>{n}</span></button>'
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
    <span><b>{sum(len(s["arts"]) for s in stories)}</b> articles</span><span><b>{len(multi)}</b> stories with 2+ sources</span>
    <span><b>{len(single)}</b> single-source stories</span><span>threshold <b>{THRESHOLD:.2f}</b></span>
    <span>Mistral today <b>${spent[0]:.2f}</b></span><span>Claude today <b>${spent[1]:.2f}</b> API-equivalent</span>
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
    s.hidden = region !== "All" && !s.dataset.r.split("|").includes(region);
  }});
}}));
const toggle = document.getElementById("show-singles"), singles = document.getElementById("singles");
toggle.addEventListener("click", () => {{
  singles.hidden = !singles.hidden;
  toggle.textContent = singles.hidden ? "Show {len(single)} stories" : "Hide single-source stories";
}});
</script>
"""


def story_json(s, lean):
    """A story as the app gets it."""
    arts = s["arts"]
    by_id = {a[8]: a for a in arts}

    def refs(ids):
        return [{"outlet": outlet, "url": url} for outlet, url in cited(ids, by_id).items()]

    return {
        "id": s["id"], "headline": s["headline"] or arts[0][3], "summary": s["summary"],
        "tabs": s["regions"] + s["interests"], "coverage": round(s.get("coverage", 0), 3),
        "lift": {subject: round(lift, 3) for subject, lift in s.get("lift", {}).items()},
        "sources": s["sources"], "updated": arts[-1][5], "lean": lean_counts([(a[1], a[7]) for a in arts], lean), "summary_from": refs(s["summary_articles"]),
        "updates": [{"at": at, "text": text, "from": refs(ids)} for at, text, ids in s["updates"]],
        "quotes": [{"text": quote_en, "outlet": outlet, "url": url, "translated": normalized(quote) != normalized(quote_en)}
                   for quote, quote_en, outlet, url in s["quotes"]],
        "articles": [{"outlet": a[1], "title": a[3], "url": a[4], "published": a[5], "opinion": bool(a[6]),
                      "lean": lean.get(outlet_key(a[1]))} for a in arts]}


def index(db):
    """Puts each story's headline, summary, updates and article titles (original and English) in the search index.
    Stories that can still change are indexed again every run; the first run indexes all of them."""
    first = db.execute("SELECT COUNT(*) FROM search_index").fetchone()[0] == 0
    where, params = ("1", ()) if first else ("s.updated >= ?", (iso(now() - OPEN_FOR - timedelta(days=1)),))
    texts = defaultdict(list)
    for sid, *parts in db.execute(f"SELECT s.id, s.headline, s.summary FROM stories s WHERE {where}", params):
        texts[sid] += parts
    for sid, text in db.execute(f"SELECT u.story, u.text FROM updates u JOIN stories s ON s.id = u.story WHERE {where}", params):
        texts[sid].append(text)
    for sid, *titles in db.execute(f"SELECT a.story, a.title, a.title_en FROM articles a JOIN stories s ON s.id = a.story"
                                   f" WHERE {where}", params):
        texts[sid] += titles
    db.executemany("DELETE FROM search_index WHERE rowid = ?", [(sid,) for sid in texts])
    db.executemany("INSERT INTO search_index(rowid, text) VALUES (?, ?)",
                   [(sid, " ".join(t for t in parts if t)) for sid, parts in texts.items()])
    db.commit()


def search(db, query, most=100):
    """The 100 most recent stories, of any age, with every word of the query. Words of 4 letters or more also match as the
    start of a word ("zwol" finds Zwolle); accents don't matter."""
    words = re.findall(r"\w+", query)[:8]
    if not words:
        return []
    match = " ".join(f'"{word}"*' if len(word) >= 4 else f'"{word}"' for word in words)
    ids = [r[0] for r in db.execute("SELECT s.id FROM search_index JOIN stories s ON s.id = search_index.rowid"
                                    " WHERE search_index MATCH ? ORDER BY s.updated DESC LIMIT ?", (match, most))]
    order = {sid: i for i, sid in enumerate(ids)}
    lean = leans(db)
    return [story_json(s, lean) for s in sorted(shown(db, ids), key=lambda s: order[s["id"]])] if ids else []


def api(db):
    """Everything the app shows in one document: the stories on the check page the reader follows, in the app's order,
    and the feed status. The places aren't tabs: they decide which stories come."""
    lean = leans(db)
    stories = [story_json(s, lean) for s in ranked(db, [s for s in shown(db) if followed(s)])]
    feeds = [{"name": name, "url": url, "items": items, "error": error, "checked": checked} for name, url, items, error, checked
             in db.execute("SELECT name, url, items, error, checked FROM sources ORDER BY error IS NULL, name")]
    return {"built": iso(now()), "tabs": list(interests(db)), "stories": stories, "feeds": feeds,
            "report": latest_report(db)}


def settings_json(db):
    """What the app's settings show: the interests, the feeds with their outlet's lean, the spending caps and the prompts."""
    lean, edited = leans(db), setting(db, "prompts", {})
    return {
        "regions": list(places(db)),
        "places": [{"name": name, "about": place["about"], "major": place["major"]} for name, place in places(db).items()],
        "interests": [{"name": name, "about": about} for name, about in interests(db).items()],
        "sources": [{"region": s["region"], "name": s["name"], "url": s["url"], "lang": s["lang"], "opinion": s["opinion"],
                     "lean": lean.get(outlet_key(s["name"]))} for s in load_sources(db)[0]],
        "budgets": budgets(db),
        "prompts": [{"kind": kind, "title": title, "about": about, "text": edited.get(kind, PROMPTS[kind]),
                     "default": PROMPTS[kind]} for kind, (title, about) in PROMPT_NOTES.items()],
    }


def field(item, key):
    value = item.get(key) if isinstance(item, dict) else None
    return value.strip() if isinstance(value, str) else ""


def save_settings(db, changes):
    """Checks and stores the parts of the settings the app sends; a ValueError's message is shown in the app. Returns
    whether the interests changed: the recent stories are then sorted again."""
    if not isinstance(changes, dict) or not set(changes) <= {"places", "interests", "sources", "budgets", "prompts"}:
        raise ValueError("The app sent settings the server doesn't know.")
    if "places" in changes and "sources" in changes:
        raise ValueError("Save the places and the feeds one at a time.")
    new, before, where = {}, interests(db), places(db)
    moves = {}  # old place -> its new name, or None when it's gone
    if "places" in changes:
        found = {}
        for item in changes["places"] if isinstance(changes["places"], list) else [None]:
            name, about = field(item, "name"), field(item, "about")
            major, was = (item.get("major"), item.get("was")) if isinstance(item, dict) else (None, None)
            if not fold(name) or not about:
                raise ValueError("Each place needs a name and a description.")
            if len(name) > 40 or len(about) > 1000:
                raise ValueError(f"{name[:40]}: keep the name under 40 characters and the description under 1000.")
            if isinstance(major, bool) or not isinstance(major, int) or not 1 <= major <= 1000:
                raise ValueError(f"{name}: the morning report needs a number of sources from 1 to 1000.")
            if name in found:
                raise ValueError(f"There's already a tab called {name}.")
            if was is not None and (not isinstance(was, str) or was not in where or was in moves):
                raise ValueError("The app sent a place the server doesn't know.")
            if was is not None:
                moves[was] = name
            found[name] = {"about": about, "major": major}
        moves = {old: name for old, name in (moves | {old: None for old in where if old not in moves}).items() if old != name}
        new["places"] = found
    if "interests" in changes:
        found = {}
        for item in changes["interests"] if isinstance(changes["interests"], list) else [None]:
            name, about = field(item, "name"), field(item, "about")
            if not fold(name) or not about:
                raise ValueError("Each interest needs a name and a description.")
            if len(name) > 40 or len(about) > 1000:
                raise ValueError(f"{name[:40]}: keep the name under 40 characters and the description under 1000.")
            if name in found:
                raise ValueError(f"There's already a tab called {name}.")
            found[name] = about
        new["interests"] = found
    # tag() matches the model's answers on letters and digits only, and no two tabs can have the same name. All is the
    # app's first tab, None and Elsewhere the writer's answers for no place and one the reader doesn't follow, and
    # Topics the feeds without one.
    tabs = ["All", "None", ELSEWHERE, "Topics"] + list(new.get("places", where)) + list(new.get("interests", before))
    taken = set()
    for tab in tabs if "places" in changes or "interests" in changes else []:
        if fold(tab) in taken:
            raise ValueError(f"There's already a tab called {tab}.")
        taken.add(fold(tab))
    if moves:
        # Feeds and stories go along with a renamed place; a deleted place's have none. The feeds in sources.toml are
        # grouped by their place's first name, the ones the app added by its current one.
        renamed = {raw: moves.get(cur, cur) if cur is not None else None for raw, cur in setting(db, "renamed", {}).items()}
        for old, name in moves.items():
            renamed.setdefault(old, name)
        new["renamed"] = {raw: name for raw, name in renamed.items() if raw != name}
        edits = setting(db, "sources", None)
        if edits:
            edits["added"] = [s | {"region": moves.get(s["region"], s["region"])} for s in edits["added"]]
            new["sources"] = edits
    if "sources" in changes:
        bundled, rated = load_sources(db, edited=False)[0], leans()
        feeds, urls, outlets, lean = [], set(), {}, {}
        for item in changes["sources"] if isinstance(changes["sources"], list) else [None]:
            name, url = field(item, "name"), field(item, "url")
            region, lang, opinion, rating = (item.get(k) for k in ("region", "lang", "opinion", "lean")) \
                if isinstance(item, dict) else (0, 0, 0, 0)
            if not name:
                raise ValueError("Each feed needs a name.")
            address = urlsplit(url)
            if address.scheme not in ("http", "https") or not address.hostname:
                raise ValueError(f"{name}: the address needs to start with http:// or https://.")
            if region not in list(where) + [None] or lang not in ("nl", "en") or not isinstance(opinion, bool) \
                    or rating not in (None, "left", "center", "right"):
                raise ValueError(f"{name}: the app sent a region, language or lean the server doesn't know.")
            if url in urls:
                raise ValueError(f"{url} is in the list twice.")
            urls.add(url)
            # Articles are credited to an outlet by its loosely matched name, so one outlet can't have two names.
            if outlets.setdefault(outlet_key(name), name) != name:
                raise ValueError(f"{name} and {outlets[outlet_key(name)]} are the same outlet; give them the same name.")
            if lean.setdefault(name, rating) != rating:
                raise ValueError(f"The feeds of {name} have different leans.")
            feeds.append({"region": region, "name": name, "url": url, "lang": lang, "opinion": opinion})

        def fields(s):
            return s["region"], s["name"], s["url"], s["lang"], s["opinion"]

        kept, listed = {fields(s) for s in feeds}, {fields(s) for s in bundled}
        new["sources"] = {"removed": [s["url"] for s in bundled if fields(s) not in kept],
                          "added": [s for s in feeds if fields(s) not in listed],
                          "lean": {name: rating for name, rating in lean.items() if rated.get(outlet_key(name)) != rating}}
    if "budgets" in changes:
        caps = changes["budgets"] if isinstance(changes["budgets"], dict) else {}
        for writer in ("claude", "mistral"):
            cap = caps.get(writer)
            if isinstance(cap, bool) or not isinstance(cap, (int, float)) or not 0 <= cap < float("inf"):
                raise ValueError("A spending cap is an amount of dollars, 0 or more.")
        new["budgets"] = {writer: float(caps[writer]) for writer in ("claude", "mistral")}
    if "prompts" in changes:
        texts, edited = changes["prompts"], setting(db, "prompts", {})
        if not isinstance(texts, dict) or not set(texts) <= set(PROMPTS):
            raise ValueError("The app sent a prompt the server doesn't know.")
        for kind, text in texts.items():
            text = text.strip() if isinstance(text, str) else ""
            if len(text) > 20_000:
                raise ValueError("Keep a prompt under 20,000 characters.")
            if text and text != PROMPTS[kind]:
                edited[kind] = text
            else:  # empty or the default: back to the default, and to later versions of it
                edited.pop(kind, None)
        new["prompts"] = edited
    db.executemany("INSERT OR REPLACE INTO settings(key, value) VALUES (?, ?)",
                   [(key, json.dumps(value, ensure_ascii=False)) for key, value in new.items()])
    if moves:  # one statement per table, so swapping two names works
        case, params = " ".join("WHEN ? THEN ?" for _ in moves), [x for move in moves.items() for x in move]
        for table in ("articles", "stories"):
            db.execute(f"UPDATE {table} SET region = CASE region {case} END WHERE region IN ({','.join('?' * len(moves))})",
                       params + list(moves))
    changed = "interests" in new and new["interests"] != before
    if changed:
        db.execute("UPDATE stories SET interests = NULL WHERE updated >= ?", (iso(now() - SHOW),))
    db.commit()
    return changed


# The processing run and a change of interests in the app can both start sorting stories; one at a time.
TAGGING = threading.Lock()


def retag():
    """Sorts the recent stories into the changed interests in the background, so their tabs fill within minutes instead
    of at the next processing run."""
    with TAGGING, closing(connect()) as db:
        tag(db)


HEAD = '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">'


class Page(BaseHTTPRequestHandler):
    def do_GET(self):
        url = urlsplit(self.path)
        path = url.path
        if path not in ("/", "/api/stories", "/api/report", "/api/search", "/api/settings"):
            return self.send_error(404)
        with closing(connect()) as db:
            if path == "/":
                body, kind = (HEAD + render(db)).encode(), "text/html; charset=utf-8"
            else:
                if path == "/api/search":
                    data = {"stories": search(db, " ".join(parse_qs(url.query).get("q", [])))}
                elif path == "/api/settings":
                    data = settings_json(db)
                else:
                    data = api(db) if path == "/api/stories" else latest_report(db)
                body, kind = json.dumps(data, ensure_ascii=False).encode(), "application/json"
        self.send(body, kind)

    def do_POST(self):
        """Changes the settings. Only a JSON body is taken: a web page has to ask a server first before it sends one
        there, and this server never says yes, so no site open in a browser on the network can change them."""
        if urlsplit(self.path).path != "/api/settings":
            return self.send_error(404)
        if self.headers.get("Content-Type", "").split(";")[0].strip().lower() != "application/json":
            return self.send_error(415)
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if not 0 < length <= 1_000_000:
                raise ValueError("The settings the app sent are empty or too big.")
            changes = json.loads(self.rfile.read(length))
            with closing(connect()) as db:
                db.execute("PRAGMA busy_timeout = 10000")  # well within the app's 30 seconds
                if save_settings(db, changes):
                    threading.Thread(target=retag, daemon=True).start()
                data, status = settings_json(db), 200
        except (ValueError, OverflowError, RecursionError) as e:  # a number too big for a float, JSON nested too deep
            data, status = {"error": str(e)}, 400
        except sqlite3.OperationalError:
            data, status = {"error": "The server is busy with the news. Try again in a minute."}, 503
        self.send(json.dumps(data, ensure_ascii=False).encode(), "application/json", status)

    def send(self, body, kind, status=200):
        packed = "gzip" in self.headers.get("Accept-Encoding", "")
        if packed:
            body = gzip.compress(body)
        try:
            self.send_response(status)
            self.send_header("Content-Type", kind)
            if packed:
                self.send_header("Content-Encoding", "gzip")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass  # the browser stopped loading the page


def group_slot(t):
    """The latest processing time at or before t: every 3 hours on the hour, one of them at REPORT_HOUR."""
    return t.replace(minute=0, second=0, microsecond=0) - timedelta(hours=(t.hour - REPORT_HOUR) % 3)


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
                started = datetime.now().astimezone()
                if grouped is None or grouped < group_slot(started):
                    group(db)
                    grouped = started
                    write(db)
                    with TAGGING:
                        tag(db)
                    index(db)
                morning(db)
        except Exception:
            traceback.print_exc()
        time.sleep(COLLECT_EVERY - time.time() % COLLECT_EVERY)  # on the hour and half hour


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else "run"
    if command == "run":
        run()
    else:
        with closing(connect()) as db:
            if command == "page":
                print(render(db))
            else:
                {"collect": collect, "group": group, "regroup": regroup, "translate": translate, "write": write,
                 "tag": tag, "morning": morning, "index": index}[command](db)
