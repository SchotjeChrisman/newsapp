"""Private news server: collects feeds, groups articles about the same event into stories, serves a check page."""

import email.utils
import fnmatch
import html
import os
import re
import signal
import sqlite3
import sys
import threading
import time
import tomllib
import traceback
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

SCHEMA = """
CREATE TABLE IF NOT EXISTS articles(
  id INTEGER PRIMARY KEY, url TEXT UNIQUE, outlet TEXT, region TEXT, title TEXT, summary TEXT,
  published TEXT, opinion INTEGER, vec BLOB, story INTEGER);
CREATE INDEX IF NOT EXISTS articles_outlet_title ON articles(outlet, title);
CREATE INDEX IF NOT EXISTS articles_story ON articles(story);
CREATE TABLE IF NOT EXISTS stories(id INTEGER PRIMARY KEY, updated TEXT, n INTEGER, vec BLOB);
CREATE TABLE IF NOT EXISTS sources(url TEXT PRIMARY KEY, name TEXT, checked TEXT, items INTEGER, error TEXT);
"""


def now():
    return datetime.now(timezone.utc)


def iso(d):
    return d.astimezone(timezone.utc).isoformat(timespec="seconds")


def connect(path=None):
    db = sqlite3.connect(path or DB, timeout=30)
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript(SCHEMA)
    return db


def outlet_key(name):
    """'De Telegraaf', 'telegraaf.nl' and 'NRC - Nieuws, achtergronden' reduce to the same key as their source."""
    name = re.sub(r"\s+-\s.*", "", name.lower())
    return re.sub(r"^(www\.|w3\.)|\.(com|nl|org|net|co\.uk)$|^(the|de|het) ", "", name).replace(" ", "")


def load_sources():
    """sources.toml holds one array per region (Topics: outlets without a region), outlet aliases and ignored outlets."""
    data = tomllib.loads(SOURCES.read_text())
    aliases, ignore = data.pop("aliases", {}), data.pop("ignore", [])
    sources = [s | {"region": None if region == "Topics" else region} for region, items in data.items() for s in items]
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
                "INSERT OR IGNORE INTO articles(url, outlet, region, title, summary, published, opinion) VALUES (?,?,?,?,?,?,?)",
                (a["url"], outlet, s["region"], a["title"], a["summary"], iso(published), opinion)).rowcount
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
    todo = db.execute("SELECT id, title, summary FROM articles WHERE vec IS NULL AND story IS NULL").fetchall()
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
    db.execute("UPDATE articles SET story = NULL WHERE story IN (SELECT id FROM stories WHERE vec IS NOT NULL)")
    db.execute("DELETE FROM stories WHERE vec IS NOT NULL")
    group(db)


def esc(s):
    return html.escape(str(s))


def when(published):
    return datetime.fromisoformat(published).astimezone().strftime("%a %H:%M")


def source_count(arts):
    """Outlets covering a story, with identical headlines (syndicated copies, like one DPG article
    in AD, Tubantia and De Stentor) counting as one source."""
    parent = {a[1]: a[1] for a in arts}

    def root(outlet):
        while parent[outlet] != outlet:
            outlet = parent[outlet]
        return outlet

    first = {}
    for a in arts:
        title = " ".join(a[3].casefold().split())
        if title in first:
            parent[root(a[1])] = root(first[title])
        else:
            first[title] = a[1]
    return len({root(outlet) for outlet in parent})


def article_html(a):
    _, outlet, _, title, url, published, opinion = a
    tag = ' <span class="tag">opinion</span>' if opinion else ""
    return (f'<li><span class="outlet">{esc(outlet)}</span> <a href="{esc(url)}" target="_blank" rel="noreferrer">'
            f'{esc(title)}</a>{tag} <time>{when(published)}</time></li>')


def story_html(arts):
    sources = source_count(arts)
    regions = " ".join(sorted({a[2] for a in arts if a[2]}))
    items = "".join(article_html(a) for a in arts[:5])
    more = "".join(article_html(a) for a in arts[5:])
    if more:
        items += f'<li><details><summary>{len(arts) - 5} more</summary><ul>{more}</ul></details></li>'
    return (f'<article class="story" data-r="{esc(regions)}"><div class="count"><b>{sources}</b>'
            f'<span>{"sources" if sources > 1 else "source"}</span></div>'
            f'<div><h2>{esc(arts[0][3])}</h2><ul>{items}</ul></div></article>')


def render(db):
    """The page body; the server adds the document head, the artifact snapshot uses it as is."""
    rows = db.execute("""SELECT a.story, a.outlet, a.region, a.title, a.url, a.published, a.opinion
                         FROM articles a JOIN stories s ON s.id = a.story
                         WHERE s.updated >= ? ORDER BY a.published""", (iso(now() - SHOW),)).fetchall()
    by_story = defaultdict(list)
    for r in rows:
        by_story[r[0]].append(r)
    counted = sorted(((source_count(arts), arts) for arts in by_story.values()),
                     key=lambda c: (c[0], c[1][-1][5]), reverse=True)
    multi = [arts for n, arts in counted if n > 1]
    single = [arts for n, arts in counted if n == 1]
    sources = db.execute("SELECT name, url, items, error FROM sources ORDER BY error IS NULL, name").fetchall()
    failing = [s for s in sources if s[3]]
    chips = f'<button type="button" data-filter="All" aria-pressed="true">All <span>{len(multi)}</span></button>'
    for r in REGIONS:
        n = sum(1 for s in multi if any(a[2] == r for a in s))
        chips += f'<button type="button" data-filter="{r}" aria-pressed="false">{r} <span>{n}</span></button>'
    source_rows = "".join(
        f'<tr class="{"bad" if e else ""}"><td><a href="{esc(u)}" target="_blank" rel="noreferrer">{esc(n)}</a></td>'
        f'<td class="num">{i}</td><td>{esc(e or "ok")}</td></tr>' for n, u, i, e in sources)
    return f"""<title>News grouping check</title>
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
  <h1>Grouping check</h1>
  <p>Stories with news in the last 48 hours. Stories with the most independent sources come first; copies of one article in sister papers count once.</p>
  <div class="stats">
    <span><b>{len(rows)}</b> articles</span><span><b>{len(multi)}</b> stories with 2+ sources</span>
    <span><b>{len(single)}</b> single-source stories</span><span>threshold <b>{THRESHOLD:.2f}</b></span>
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
                {"collect": collect, "group": group, "regroup": regroup}[command](db)
