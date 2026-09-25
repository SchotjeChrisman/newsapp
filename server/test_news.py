"""Self-check, run inside the container: python test_news.py"""

import os

os.environ["NEWS_DB"] = ":memory:"

import gzip
import io
import json
import tempfile
import threading
import time
import tomllib
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

import news

UTC = timezone.utc
SOURCES = news.SOURCES

RSS = b"""<?xml version="1.0"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom"><channel>
<item><title>Brand in Zwolle &amp; omgeving</title><atom:link href="https://x.nl/feed" rel="self"/>
  <link>https://x.nl/a</link><description><![CDATA[<p>Een <b>grote</b> brand.</p>]]></description>
  <pubDate>Tue, 22 Sep 2026 10:00:00 +0200</pubDate></item>
<item><title>No link</title></item>
<item><title>Script link</title><link>javascript:alert(1)</link></item>
<item><title>Photo caption</title><link>https://f24.com/p</link><description>Kept</description>
  <source url="https://f24.com">&#169; Reuters</source></item>
<item><title>Storm hits coast - Reuters</title><link>https://news.google.com/rss/articles/abc</link>
  <description>Storm hits coast Reuters</description><source url="https://www.reuters.com">Reuters</source></item>
</channel></rss>"""

ATOM = b"""<feed xmlns="http://www.w3.org/2005/Atom"><entry><title type="html">A &amp;amp; B</title>
<link rel="alternate" href="https://y.com/b"/><summary>Short</summary><published>2026-09-22T08:00:00Z</published></entry></feed>"""

RDF = b"""<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xmlns="http://purl.org/rss/1.0/"
  xmlns:dc="http://purl.org/dc/elements/1.1/"><channel><title>DW</title></channel>
<item><title>C</title><link>https://dw.com/c</link><dc:date>2026-09-22T09:00:00+00:00</dc:date></item></rdf:RDF>"""


def test_parse():
    rss = news.parse_feed(RSS)
    assert [a["url"] for a in rss] == ["https://x.nl/a", "https://f24.com/p", "https://news.google.com/rss/articles/abc"], rss
    assert rss[0]["title"] == "Brand in Zwolle & omgeving"
    assert rss[0]["summary"] == "Een grote brand."
    assert rss[0]["published"] == datetime(2026, 9, 22, 8, tzinfo=UTC)
    assert (rss[1]["outlet"], rss[1]["summary"]) == (None, "Kept"), "a <source> that isn't the title's outlet is ignored"
    assert rss[2] | {"published": None} == dict(url="https://news.google.com/rss/articles/abc", title="Storm hits coast",
                                                 summary="", published=None, outlet="Reuters")
    [atom] = news.parse_feed(ATOM)
    assert (atom["url"], atom["title"], atom["summary"]) == ("https://y.com/b", "A & B", "Short")
    assert atom["published"] == datetime(2026, 9, 22, 8, tzinfo=UTC)
    [rdf] = news.parse_feed(RDF)
    assert (rdf["url"], rdf["published"]) == ("https://dw.com/c", datetime(2026, 9, 22, 9, tzinfo=UTC))


def test_outlet_key():
    assert news.outlet_key("telegraaf.nl") == news.outlet_key("De Telegraaf")
    assert news.outlet_key("de Stentor") == news.outlet_key("De Stentor")
    assert news.outlet_key("cbsnews.com") == news.outlet_key("CBS News")
    assert news.outlet_key("NRC - Nieuws, achtergronden") == news.outlet_key("NRC")
    assert news.outlet_key("w3.newsmax.com") == news.outlet_key("Newsmax")
    assert news.outlet_key("NOS") != news.outlet_key("NOS Sport")


def test_sources():
    sources, names, _ = news.load_sources()
    for source in sources:
        assert names[news.outlet_key(source["name"])] == source["name"], f"{source['name']} collides with another name"
    rated = [name for group in tomllib.loads(SOURCES.read_text())["lean"].values() for name in group]
    assert len(news.leans()) == len(rated), "each outlet has one lean"
    assert set(news.leans()) <= set(names), "every rated outlet is one of the sources"
    assert {s["region"] for s in sources} == set(news.REGIONS) | {None}


def test_source_count():
    story = [("AD", "Brand in Zwolle"), ("Tubantia", "Brand in  zwolle"), ("De Stentor", "Brand in Zwolle"),
             ("NOS", "Grote brand in Zwolle"), ("NOS", "Brand in Zwolle geblust")]
    assert news.source_count(story) == 2, "identical headlines from sister papers count once"
    chained = [("AD", "X"), ("Tubantia", "X"), ("AD", "Y"), ("De Stentor", "Y"), ("NOS", "Z")]
    assert news.source_count(chained) == 2
    lean = {news.outlet_key("AD"): "center", news.outlet_key("NOS"): "center"}
    copies_first = [("Tubantia", "Brand in Zwolle"), ("AD", "Brand in Zwolle"), ("NOS", "Grote brand"), ("RTV Oost", "Brand")]
    assert news.lean_counts(copies_first, lean) == {"left": 0, "center": 2, "right": 0}, \
        "a group of copies takes the lean of its rated outlet, whoever published first"


def test_collect():
    t = news.now()

    def item(url, title, outlet=None, age=timedelta(hours=1)):
        return dict(url=url, title=title, summary="", published=t - age, outlet=outlet)

    feeds = {
        "https://t.nl/rss": ([item("https://t.nl/a", "Kabinet valt"), item("https://t.nl/opinie/b", "Waarom het viel"),
                              item("https://t.nl/old", "Oud nieuws", age=timedelta(days=5))], None),
        "https://news.google.com/rss": ([item("https://news.google.com/x", "Kabinet valt", "telegraaf.nl"),
                                         item("https://news.google.com/y", "Opinion | Cabinet falls", "BBC"),
                                         item("https://news.google.com/z", "Rams win", "Rams Wire")], None),
        "https://broken.nl/rss": ([], "HTTPError: HTTP Error 404: Not Found"),
        "https://dw.com/rss": ([item("https://dw.com/a", "Listed outlets are never ignored")], None),
    }
    news.fetch = lambda source: feeds[source["url"]]
    with tempfile.TemporaryDirectory() as tmp:
        news.SOURCES = Path(tmp, "sources.toml")
        news.SOURCES.write_text("""ignore = ["* Wire"]
NL = [{ name = "De Telegraaf", url = "https://t.nl/rss" }, { name = "Google News: NL", url = "https://news.google.com/rss" },
      { name = "The Daily Wire", url = "https://dw.com/rss" }]
Topics = [{ name = "Broken", url = "https://broken.nl/rss" }]
[aliases]
"BBC" = "BBC News"
""")
        db = news.connect()
        news.collect(db)
    assert db.execute("SELECT url, outlet, region, opinion FROM articles ORDER BY url").fetchall() == [
        ("https://dw.com/a", "The Daily Wire", "NL", 0),  # matches "* Wire" but is a listed outlet
        ("https://news.google.com/y", "BBC News", "NL", 1),  # alias applied, opinion from the headline label
        ("https://t.nl/a", "De Telegraaf", "NL", 0),  # the Google copy of this headline was dropped as a duplicate
        ("https://t.nl/opinie/b", "De Telegraaf", "NL", 1),  # opinion from the URL; the old item and Rams Wire are gone
    ]
    assert db.execute("SELECT name, error FROM sources WHERE error IS NOT NULL").fetchall() == [
        ("Broken", "HTTPError: HTTP Error 404: Not Found")]


def test_collect_deadline():
    def fetch(source):
        if source["name"] == "Slow":
            time.sleep(2)
        return [dict(url=source["url"] + "/a", title=source["name"], summary="", published=None, outlet=None)], None

    news.fetch, news.COLLECT_WITHIN = fetch, 1
    with tempfile.TemporaryDirectory() as tmp:
        news.SOURCES = Path(tmp, "sources.toml")
        news.SOURCES.write_text('NL = [{ name = "Slow", url = "https://slow.nl" }, { name = "Fast", url = "https://fast.nl" }]\n')
        db = news.connect()
        started = time.monotonic()
        news.collect(db)
        assert time.monotonic() - started < 1.9, "collect doesn't wait for a slow feed"
        time.sleep(1.5)  # the slow feed finishes after collect has moved on
    assert db.execute("SELECT name, error FROM sources ORDER BY name").fetchall() == [
        ("Fast", None), ("Slow", "no answer within 1 s")]
    assert db.execute("SELECT outlet FROM articles").fetchall() == [("Fast",)]
    news.COLLECT_WITHIN = 120


def test_group():
    directions = {"a1": [1, 0, 0], "a2": [0.95, 0.31, 0], "b1": [0, 1, 0], "old": [1, 0, 0], "a3": [0.95, 0.31, 0]}
    news.embed = lambda texts: np.array([directions[t.split()[0]] for t in texts], np.float32)
    db = news.connect()
    t0 = news.now() - timedelta(hours=10)

    def add(title, published):
        db.execute("INSERT INTO articles(url, outlet, region, title, summary, published, opinion) VALUES (?,?,?,?,?,?,0)",
                   (f"https://x.nl/{title.split()[0]}", "NOS", "NL", title, "", news.iso(published)))

    def story(key):
        return db.execute("SELECT story FROM articles WHERE url = ?", (f"https://x.nl/{key}",)).fetchone()[0]

    add("old story", t0 - timedelta(days=5))
    add("a1 fire", t0)
    add("a2 fire", t0 + timedelta(hours=1))
    add("b1 <script>alert(1)</script>", t0 + timedelta(hours=2))
    news.group(db)
    assert story("a1") == story("a2"), "similar articles share a story"
    assert story("b1") != story("a1"), "different articles get separate stories"
    assert story("old") != story("a1"), "a story closes after OPEN_FOR"

    add("a3 fire", t0 + timedelta(hours=3))
    news.group(db)
    assert story("a3") == story("a1"), "a later run joins an existing story"
    assert db.execute("SELECT n FROM stories WHERE id = ?", (story("a1"),)).fetchone()[0] == 3

    news.index(db)
    news.regroup(db)
    assert story("a1") == story("a2") == story("a3") != story("b1"), "regroup rebuilds the same stories"
    assert db.execute("SELECT COUNT(*) FROM search_index").fetchone() == (0,), "and the search index starts over"
    assert db.execute("SELECT COUNT(*) FROM stories s WHERE n != (SELECT COUNT(*) FROM articles WHERE story = s.id)"
                      ).fetchone()[0] == 0

    page = news.render(db)
    assert "<script>alert(1)" not in page and "&lt;script&gt;alert(1)" in page


def add_article(db, aid, story, outlet, title, opinion=0, lang="en", summary="", age=timedelta(hours=1)):
    db.execute("INSERT INTO articles(id, url, outlet, region, title, summary, published, opinion, lang, story)"
               " VALUES (?,?,?,?,?,?,?,?,?,?)",
               (aid, f"https://x.nl/{aid}", outlet, "NL", title, summary, news.iso(news.now() - age), opinion, lang, story))


def test_translate_before_grouping():
    os.environ["MISTRAL_API_KEY"] = "test"
    db = news.connect()
    add_article(db, 1, None, "NOS", "Kabinet valt", lang="nl", summary="Het kabinet is gevallen.")
    add_article(db, 2, None, "BBC", "Dutch cabinet falls", summary="The Dutch cabinet has fallen.")

    def fake(model, system, payload, max_tokens):
        assert model == news.TRANSLATOR and [i["id"] for i in payload["items"]] == [1], "only Dutch articles"
        return json.dumps({"items": [{"id": 1, "title": "Cabinet falls", "text": "The cabinet has fallen."}]}), (100, 50)

    embedded = []
    news.call_mistral = fake
    news.embed = lambda texts: embedded.extend(texts) or np.array([[1, 0, 0]] * len(texts), np.float32)
    news.group(db)
    assert db.execute("SELECT title_en FROM articles WHERE id = 1").fetchone() == ("Cabinet falls",)
    assert sorted(embedded) == ["Cabinet falls. The cabinet has fallen.", "Dutch cabinet falls. The Dutch cabinet has fallen."]
    del os.environ["MISTRAL_API_KEY"]


def test_language_backfill():
    with tempfile.TemporaryDirectory() as tmp:
        path = str(Path(tmp, "old.db"))
        old = news.sqlite3.connect(path)
        old.execute("CREATE TABLE articles(id INTEGER PRIMARY KEY, url TEXT UNIQUE, outlet TEXT, region TEXT, title TEXT,"
                    " summary TEXT, published TEXT, opinion INTEGER, vec BLOB, story INTEGER)")
        old.executemany("INSERT INTO articles(url, outlet, region) VALUES (?, ?, ?)",
                        [("https://a", "RTV Oost", "Zwolle"), ("https://b", "CNN", "US"), ("https://c", "Tweakers", None),
                         ("https://d", "NL Times", "NL")])
        old.commit()
        old.close()
        db = news.connect(path)
        news.SOURCES = SOURCES
        news.translate(db)  # nothing recent to translate, so no request goes out
        assert db.execute("SELECT lang FROM articles ORDER BY id").fetchall() == [("nl",), ("en",), ("nl",), ("en",)], \
            "regions decide the language, except where sources.toml says otherwise"


def test_mistral_articles():
    now = news.iso(news.now())
    rows = [(1, 1, "1Zwolle", now, 0, "Brand bij rechtbank", "", 0, None), (1, 2, "Weblog Zwolle", now, 0, "Brand bij rechtbank", "Kort ontruimd.", 0, None),
            (1, 3, "SCMP", now, 0, "Actress dies", "Died of an overdose.", 0, None), (1, 4, "PBS", now, 0, "Actress dies", "Found at home.", 0, None)]
    merged, scmp, pbs = news.mistral_articles(rows)
    assert (merged["ids"], merged["text"], merged["also_in"]) == ([1, 2], "Kort ontruimd.", ["Weblog Zwolle"]), \
        "a copy without a teaser merges into one with a teaser, keeping the longest"
    assert (scmp["ids"], pbs["ids"]) == ([3], [4]), "the same headline over different teasers isn't a copy"


def test_write():
    os.environ["MISTRAL_API_KEY"] = "test"
    db = news.connect()
    db.execute("INSERT INTO stories(id, updated, n) VALUES (1, ?, 3), (2, ?, 1)", (news.iso(news.now()),) * 2)
    add_article(db, 1, 1, "NOS", "Kabinet valt", lang="nl")
    add_article(db, 2, 1, "BBC", "Dutch cabinet falls")
    add_article(db, 3, 1, "Trouw", "Waarom dit kabinet moest vallen", opinion=1, lang="nl",
                summary="Het kabinet had geen plan meer voor de asielcrisis.")
    add_article(db, 4, 2, "Trouw", "Een column zonder nieuws", opinion=1, lang="nl")
    add_article(db, 8, 1, "Tubantia", "Kabinet valt", lang="nl")  # a sister paper's word-for-word copy
    calls = []

    def fake(model, system, payload, max_tokens):
        calls.append((system, payload))
        if system.startswith(news.NEW_PROMPT):
            reply = {"stories": [{"key": "s1", "headline": "Dutch cabinet falls", "summary": "The cabinet fell.", "region": "NL",
                                  "background": " The cabinet is the Dutch government. ",
                                  "quotes": [{"article": "a3", "quote": "“Het kabinet had geen plan meer”",
                                              "quote_en": "The cabinet had no plan left"},
                                             {"article": "a3", "quote": "Dit citaat staat nergens in het stuk", "quote_en": "Made up"},
                                             {"article": "a1", "quote": "Kabinet valt", "quote_en": "Not an opinion piece"}]}]}
        else:
            reply = {"stories": [{"key": "s1", "facts": [{"article": "a1", "fact": "The king accepted the resignation."},
                                                   {"article": "a1", "fact": "A second sentence from the same article."},
                                                   {"article": "a9", "fact": "From an article that isn't there."}]}]}
        return json.dumps(reply), (1000, 500)

    news.call_mistral = fake
    news.write(db)
    [(_, first)] = calls
    [story] = first["stories"]
    assert [a["title"] for a in story["articles"]] == ["Kabinet valt", "Dutch cabinet falls", "Waarom dit kabinet moest vallen"], \
        "a lone opinion column waits for more coverage, and a copy goes in once"
    assert story["articles"][0]["also_in"] == ["Tubantia"] and story["source_count"] == 3
    assert db.execute("SELECT headline, summary, region FROM stories WHERE id = 1").fetchone() == (
        "Dutch cabinet falls", "The cabinet fell.", "NL")
    assert db.execute("SELECT quote_en FROM quotes").fetchall() == [("The cabinet had no plan left",)], \
        "only word-for-word quotes from opinion articles"
    assert db.execute("SELECT id FROM articles WHERE written = 1 ORDER BY id").fetchall() == [(1,), (2,), (3,), (8,)]
    assert abs(db.execute("SELECT usd FROM spend").fetchone()[0] - (1000 * 0.15 + 500 * 0.6) / 1e6) < 1e-12

    add_article(db, 5, 1, "NOS", "Koning aanvaardt ontslag", lang="nl")
    news.write(db)
    assert calls[-1][0].startswith(news.UPDATE_PROMPT)
    assert [a["title"] for a in calls[-1][1]["stories"][0]["articles"]] == ["Koning aanvaardt ontslag"], \
        "an update only gets the new articles"
    assert db.execute("SELECT headline, summary FROM stories WHERE id = 1").fetchone() == (
        "Dutch cabinet falls", "The cabinet fell."), "updates never rewrite the story"
    assert db.execute("SELECT text, articles FROM updates").fetchall() == [("The king accepted the resignation.", "[5]")], \
        "one sentence per new article, linked to it"

    page = news.render(db)
    assert "The king accepted the resignation." in page and "The cabinet had no plan left" in page
    assert "The cabinet is the Dutch government." in page
    assert page.count(">Tubantia</a>") == 1, "the summary names the sources it was written from"
    assert "Een column zonder nieuws" not in page

    data = news.api(db)
    assert [s["id"] for s in data["stories"]] == [1], "the app gets the stories the page shows"
    [story] = data["stories"]
    assert (story["headline"], story["summary"], story["tabs"], story["sources"]) == ("Dutch cabinet falls", "The cabinet fell.", ["NL"], 3)
    assert story["background"] == "The cabinet is the Dutch government."
    assert story["lean"] == {"left": 1, "center": 1, "right": 0}, "Trouw and NOS; the Tubantia copy counts once"
    assert story["updates"] == [{"at": story["updates"][0]["at"], "text": "The king accepted the resignation.",
                                 "from": [{"outlet": "NOS", "url": "https://x.nl/5"}]}]
    assert story["quotes"] == [{"text": "The cabinet had no plan left", "outlet": "Trouw", "url": "https://x.nl/3", "translated": True}]
    assert [a["outlet"] for a in story["summary_from"]] == ["NOS", "BBC", "Trouw", "Tubantia"]
    assert [(a["outlet"], a["lean"], a["opinion"]) for a in story["articles"][:3]] == [
        ("NOS", "center", False), ("BBC", None, False), ("Trouw", "left", True)]
    assert data["tabs"] == list(news.INTERESTS), "the places aren't tabs"

    add_article(db, 7, 1, "AD", "Kabinet valt, koning aanvaardt ontslag", lang="nl")
    for reply in ({"stories": [{"key": ["s1"]}, "junk", {"key": "s1", "facts": [["a1"], {"article": ["a1"], "fact": "y"},
                                                                                   {"article": "a1", "fact": 5}],
                                                          "quotes": [{"article": {}}]}]},
                  {"stories": None}, {"stories": 3}, [], {"stories": [{"key": "s1", "facts": 2}]},
                  {"stories": [{"key": "s1", "facts": [{"article": "a1", "fact": "x"}]}]}):
        news.call_mistral = lambda *_: (json.dumps(reply), (10, 10))
        db.execute("UPDATE articles SET written = 0 WHERE id = 7")
        news.write(db)  # malformed replies are skipped, never fatal
    assert db.execute("SELECT text FROM updates ORDER BY id").fetchall() == [("The king accepted the resignation.",), ("x",)]

    db.execute("UPDATE spend SET usd = ?", (news.DAILY_BUDGET,))
    add_article(db, 6, 1, "BBC", "King accepts resignation")
    requests = len(calls)
    news.write(db)
    assert len(calls) == requests, "no requests once the daily budget is spent"
    del os.environ["MISTRAL_API_KEY"]


def test_claude_writer():
    class Run:
        def __init__(self, stdout, returncode=0):
            self.stdout, self.stderr, self.returncode = stdout, "", returncode

    real_run = news.subprocess.run
    calls = []
    news.subprocess.run = lambda cmd, **kw: calls.append(cmd) or Run(json.dumps(
        {"is_error": False, "result": "done", "structured_output": {"ok": 1}, "total_cost_usd": 0.01}))
    assert news.call_claude("claude-sonnet-5", news.NEW_PROMPT, {}, news.SCHEMAS["new"]) == ('{"ok": 1}', 0.01)
    assert json.loads(calls[0][calls[0].index("--json-schema") + 1]) == news.SCHEMAS["new"]
    assert calls[0][calls[0].index("--tools") + 1] == "", "Claude Code's own tools are off"
    for run, error in ((Run(json.dumps({"is_error": True, "result": "Claude AI usage limit reached"}), 1), news.ClaudeUnavailable),
                       (Run(json.dumps({"is_error": True, "result": "x", "api_error_status": 401}), 1), news.ClaudeUnavailable),
                       (Run(json.dumps({"is_error": True, "subtype": "error_max_turns"}), 1), news.ClaudeFailed),
                       (Run(json.dumps({"is_error": False, "result": "done"})), news.ClaudeFailed),
                       (Run("not json"), news.ClaudeFailed),
                       (Run("Error: unknown option '--tools'", 1), news.ClaudeUnavailable),
                       (Run(json.dumps({"is_error": True, "result": "prompt exceeds the context limit"}), 1), news.ClaudeFailed)):
        news.subprocess.run = lambda cmd, **kw: run
        try:
            news.call_claude("claude-sonnet-5", news.NEW_PROMPT, {}, news.SCHEMAS["new"])
            raise AssertionError(f"expected {error.__name__}")
        except error:
            pass
    news.subprocess.run = real_run

    os.environ["MISTRAL_API_KEY"] = os.environ["CLAUDE_CODE_OAUTH_TOKEN"] = "test"
    db = news.connect()
    db.execute("INSERT INTO stories(id, updated, n) VALUES (1, ?, 1), (2, ?, 1)", (news.iso(news.now()),) * 2)
    add_article(db, 1, 1, "NOS", "Kabinet valt")
    add_article(db, 2, 2, "BBC", "Storm hits coast")
    used = []

    def reply(system, payload):
        return json.dumps({"stories": [{"key": st["key"], "headline": "H", "summary": "S", "region": "NL"}
                                       for st in payload["stories"]]})

    def claude(model, system, payload, schema):
        used.append(model)
        if len(used) == 2:
            raise news.ClaudeUnavailable("Claude AI usage limit reached")
        return reply(system, payload), 0.25

    def mistral(model, system, payload, max_tokens):
        used.append(model)
        return reply(system, payload), (100, 10)

    originals = news.call_claude, news.call_mistral, news.batches
    news.call_claude, news.call_mistral = claude, mistral
    news.batches = lambda stories, max_stories, max_articles: ([st] for st in stories)  # one story per request
    news.write(db)
    assert used == [news.CLAUDE_WRITER, news.CLAUDE_WRITER, news.WRITER], "Claude first, Mistral once Claude is out"
    assert db.execute("SELECT COUNT(*) FROM stories WHERE headline = 'H'").fetchone()[0] == 2
    assert db.execute("SELECT claude_usd FROM spend").fetchone()[0] == 0.25

    db.execute("UPDATE stories SET headline = NULL")
    db.execute("UPDATE spend SET claude_usd = ?", (news.CLAUDE_DAILY_BUDGET,))
    used.clear()
    news.write(db)
    assert used == [news.WRITER, news.WRITER], "past the Claude budget, Mistral writes"

    db.execute("UPDATE stories SET headline = NULL")
    db.execute("UPDATE spend SET claude_usd = 0")
    db.commit()
    used.clear()

    def flaky(model, system, payload, schema):
        used.append(model)
        if len(used) == 1:
            raise news.ClaudeFailed("no reply within 10 minutes")
        return reply(system, payload), 0.25

    news.call_claude = flaky
    news.write(db)
    assert used == [news.CLAUDE_WRITER, news.CLAUDE_WRITER], "one failed request doesn't hand the run to Mistral"
    assert db.execute("SELECT COUNT(*) FROM stories WHERE headline = 'H'").fetchone()[0] == 1, "that batch waits"

    for sid in (3, 4, 5):
        db.execute("INSERT INTO stories(id, updated, n) VALUES (?, ?, 1)", (sid, news.iso(news.now())))
        add_article(db, 10 + sid, sid, "AD", f"Story {sid}")
    db.execute("UPDATE stories SET headline = NULL")
    db.commit()
    used.clear()

    def broken(model, system, payload, schema):
        used.append(model)
        raise news.ClaudeFailed("unreadable reply")

    news.call_claude = broken
    news.write(db)
    assert used == [news.CLAUDE_WRITER] * 3 + [news.WRITER] * 3, "three failures in a row hand the run to Mistral"
    news.call_claude, news.call_mistral, news.batches = originals
    del os.environ["MISTRAL_API_KEY"], os.environ["CLAUDE_CODE_OAUTH_TOKEN"]


def test_tag():
    originals = news.call_claude, news.call_mistral
    os.environ["MISTRAL_API_KEY"] = "test"
    os.environ["CLAUDE_CODE_OAUTH_TOKEN"] = "test"
    db = news.connect()
    t = news.iso(news.now())
    db.execute("INSERT INTO stories(id, updated, n, headline, summary) VALUES (1, ?, 1, 'Ajax beat PSV', 'Ajax won.'),"
               " (2, ?, 1, 'Nvidia shares rise', 'Nvidia rose.'), (3, ?, 1, 'Road works', 'A road closes.')", (t,) * 3)
    db.execute("INSERT INTO stories(id, updated, n) VALUES (4, ?, 1)", (t,))
    for aid, sid in ((11, 1), (12, 2), (13, 3), (14, 4)):
        add_article(db, aid, sid, "NOS", f"Article {aid}")
    seen = []

    def unavailable(model, system, payload, schema):
        raise news.ClaudeUnavailable("usage limit")

    def fake(model, system, payload, max_tokens):
        seen.append([st["key"] for st in payload["stories"]])
        return json.dumps({"stories": [{"key": "s1", "interests": ["football", "Basketball"]},
                                       {"key": "s2", "interests": ["S&P500", "AI"]}]}), (10, 10)

    news.call_claude, news.call_mistral = unavailable, fake
    news.tag(db)
    assert seen == [["s1", "s2", "s3"]], "Mistral takes over; only written stories are sorted"
    assert db.execute("SELECT id, interests FROM stories ORDER BY id").fetchall() == [
        (1, '["Football"]'), (2, '["AI", "S&P 500"]'), (3, None), (4, None)], \
        "near-miss names count, unknown ones are dropped, and a story left out of the reply stays untagged"
    news.tag(db)
    assert seen[-1] == ["s1"], "a story is sorted once; one left out is sent again"

    def failing(model, system, payload, schema):
        raise news.ClaudeFailed("unreadable reply")

    db.execute("UPDATE stories SET interests = NULL")
    news.call_claude = failing
    news.tag(db)
    assert db.execute("SELECT interests FROM stories WHERE id = 1").fetchone() == ('["Football"]',), \
        "Mistral takes over when a Claude request fails"
    data = news.api(db)
    assert data["tabs"] == list(news.INTERESTS), "the places aren't tabs"
    assert {st["id"]: st["tabs"] for st in data["stories"]}[2] == ["AI", "S&P 500"]

    def renamed_meanwhile(model, system, payload, max_tokens):
        news.save_settings(db, {"interests": [{"name": "Soccer", "about": "association football"}]})
        return fake(model, system, payload, max_tokens)

    db.execute("UPDATE stories SET interests = NULL")
    news.call_mistral = renamed_meanwhile
    news.tag(db)
    assert db.execute("SELECT COUNT(*) FROM stories WHERE interests IS NOT NULL").fetchone() == (0,), \
        "tags from interests the app has since changed are thrown away"
    news.call_claude, news.call_mistral = originals
    del os.environ["MISTRAL_API_KEY"], os.environ["CLAUDE_CODE_OAUTH_TOKEN"]


def test_notable():
    originals = news.call_claude, news.call_mistral
    os.environ["MISTRAL_API_KEY"] = "test"
    db = news.connect()
    t = news.iso(news.now())
    db.execute("INSERT INTO stories(id, updated, n, headline, summary, interests) VALUES (1, ?, 1, 'New model', 'S', '[\"AI\"]'),"
               " (2, ?, 1, 'Road works', 'S', NULL), (3, ?, 1, 'Old', 'S', '[]'), (4, ?, 1, 'A how-to', 'S', NULL)", (t,) * 4)
    sent = []

    def fake(model, system, payload, max_tokens):
        sent.append([st["headline"] for st in payload["stories"]])
        return json.dumps({"stories": [{"key": "s1", "interests": ["AI"], "notable": True},
                                       {"key": "s2", "interests": [], "notable": True},
                                       {"key": "s3", "interests": ["AI"], "notable": False}]}), (10, 10)

    news.call_mistral = fake
    news.tag(db)
    assert sent == [["New model", "Road works", "A how-to"]], "stories sorted before notable existed are sent once more"
    assert db.execute("SELECT id, interests, notable FROM stories ORDER BY id").fetchall() == [
        (1, '["AI"]', 1), (2, "[]", 0), (3, "[]", None), (4, '["AI"]', 0)], "a story without an interest is never notable"
    news.tag(db)
    assert len(sent) == 1, "each story once"
    assert news.reply_schema(db, "tag")["properties"]["stories"]["items"]["required"] == ["key", "interests", "notable"]
    news.call_claude, news.call_mistral = originals
    del os.environ["MISTRAL_API_KEY"]


def test_morning():
    originals = news.now, news.market, news.call_mistral
    fixed = datetime(2026, 9, 23, 3, 30, tzinfo=UTC)
    news.now = lambda: fixed
    news.market = lambda: {"name": "S&P 500", "close": 6000.0, "change": -0.5, "date": "2026-09-22"}
    db = news.connect()
    t = news.iso(fixed)
    for sid, region, interests, outlets in ((1, "NL", [], ["NOS", "AD", "Trouw", "NRC", "RTL Nieuws", "NU.nl"]),
                                             (2, "NL", ["Football"], ["NOS", "AD"]),
                                             (3, "Zwolle", [], ["De Stentor", "1Zwolle"]),
                                             (4, "US", ["AI"], ["CNN", "Fox News", "NPR"]),
                                             (5, "Global", [], [f"Outlet {i}" for i in range(12)]),
                                             (6, "Elsewhere", ["AI"], ["BBC", "DW"]),
                                             (7, "Elsewhere", [], [f"Outlet {i}" for i in range(30)])):
        db.execute("INSERT INTO stories(id, updated, n, headline, summary, region, interests) VALUES (?,?,?,?,?,?,?)",
                   (sid, t, len(outlets), f"Headline {sid}", f"Story number {sid} happened today. Then more.", region,
                    json.dumps(interests)))
        age = timedelta(hours=30 if sid == 5 else 1)
        for i, outlet in enumerate(outlets):
            add_article(db, sid * 100 + i, sid, outlet, f"Title {sid} {i}", age=age)
    news.morning(db, datetime(2026, 9, 23, 4, 50, tzinfo=UTC).astimezone() - timedelta(hours=2))
    assert db.execute("SELECT COUNT(*) FROM reports").fetchone() == (0,), "no report before REPORT_HOUR"
    news.morning(db, fixed.astimezone())
    report = news.latest_report(db)
    assert report["day"] == "2026-09-23" and report["market"]["change"] == -0.5
    assert [(sec["title"], [st["id"] for st in sec["stories"]]) for sec in report["sections"]] == [
        ("Zwolle", [3]), ("NL", [1]), ("Elsewhere", [7]), ("AI", [4]), ("Football", [2])], \
        "major stories per region, then interests; old news, small stories and minor ones from elsewhere stay out"
    assert report["sections"][1]["stories"][0] == {"id": 1, "headline": "Headline 1", "gist": "Story number 1 happened today.",
                                                  "sources": 6}, "without a writer the gist is the summary's first sentence"
    news.morning(db, fixed.astimezone())
    assert db.execute("SELECT COUNT(*) FROM reports").fetchone() == (1,), "one report a day"
    assert news.api(db)["report"] == report
    assert report["since"] == "2026-09-22T03:00:00+00:00", "the 24 hours before 05:00 local"

    news.market = lambda: None
    db.execute("DELETE FROM reports")
    news.morning(db, fixed.astimezone())
    assert news.latest_report(db)["market"] is None
    news.market = lambda: {"name": "S&P 500", "close": 6000.0, "change": 0.1, "date": "2026-09-22"}
    news.morning(db, fixed.astimezone())
    assert news.latest_report(db)["market"]["change"] == 0.1, "a missing close is fetched again later that morning"

    os.environ["MISTRAL_API_KEY"] = "test"
    news.call_mistral = lambda *_: (json.dumps({"stories": [{"key": "s2", "gist": "The cabinet fell."},
                                                            {"key": "s3", "gist": ""}]}), (10, 10))
    db.execute("DELETE FROM reports")
    news.morning(db, fixed.astimezone())
    gist = {st["id"]: st["gist"] for sec in news.latest_report(db)["sections"] for st in sec["stories"]}
    assert gist[1] == "The cabinet fell." and gist[4] == "Story number 4 happened today.", "empty gists fall back"
    del os.environ["MISTRAL_API_KEY"]
    news.now, news.market, news.call_mistral = originals

    local = datetime(2026, 9, 23, 5, 20).astimezone()
    assert news.group_slot(local).hour == 5
    assert news.group_slot(local.replace(hour=4, minute=59)).hour == 2
    assert news.group_slot(local.replace(hour=1)) == local.replace(hour=23, minute=0) - timedelta(days=1)

    urlopen = news.urllib.request.urlopen
    cnbc = {"FormattedQuoteResult": {"FormattedQuote": [{"last": "6,030.00", "previous_day_closing": "6,000.00",
                                                         "last_time": "2026-09-23T16:59:59.000-0400"}]}}
    yahoo = {"chart": {"result": [{"meta": {"regularMarketPrice": 5970.0, "chartPreviousClose": 6000.0,
                                            "regularMarketTime": 1790193600, "gmtoffset": -14400}}]}}

    def answers(replies):
        return lambda request, **_: io.BytesIO(replies[request.full_url])

    news.urllib.request.urlopen = answers({news.CNBC: json.dumps(cnbc).encode(), news.YAHOO: b"Too Many Requests"})
    assert news.market() == {"name": "S&P 500", "close": 6030.0, "change": 0.5, "date": "2026-09-23"}
    news.urllib.request.urlopen = answers({news.CNBC: b"<html>", news.YAHOO: json.dumps(yahoo).encode()})
    assert news.market() == {"name": "S&P 500", "close": 5970.0, "change": -0.5, "date": "2026-09-23"}, "Yahoo stands in"
    news.urllib.request.urlopen = answers({news.CNBC: b"<html>", news.YAHOO: b"Too Many Requests"})
    assert news.market() is None
    news.urllib.request.urlopen = urlopen


def test_search():
    db = news.connect()
    old = news.iso(news.now() - timedelta(days=30))
    db.execute("INSERT INTO stories(id, updated, n, headline, summary) VALUES (1, ?, 2, 'PEC Zwolle beats Ajax', 'A 2-1 win.'),"
               " (2, ?, 1, 'Cabinet falls', 'The coalition broke up over 100% of the plan.'), (3, ?, 1, NULL, NULL),"
               " (4, ?, 1, 'Column on PEC Zwolle', 'An opinion.')", (old, news.iso(news.now()), old, old))
    add_article(db, 1, 1, "RTV Oost", "PEC Zwolle wint van Ajax", age=timedelta(days=30))
    add_article(db, 2, 1, "De Stentor", "PEC verslaat Ajax", age=timedelta(days=30))
    add_article(db, 3, 2, "NOS", "Kabinet gevallen")
    add_article(db, 5, 3, "AD", "Brand in Zwolle", age=timedelta(days=30))
    add_article(db, 6, 4, "Trouw", "Column over PEC Zwolle", opinion=1, age=timedelta(days=30))
    news.index(db)
    assert [st["id"] for st in news.search(db, "pec ajax")] == [1], "every word, any case, older than the app's 48 hours"
    assert [st["id"] for st in news.search(db, "kabinet")] == [2], "Dutch article titles count"
    assert [st["id"] for st in news.search(db, "zwol")] == [1, 3], "the start of a word; a lone opinion column stays out"
    assert not news.search(db, "ov") and not news.search(db, "   ") and news.search(db, '"cabinet* (')
    [story] = news.search(db, "brand")
    assert story["headline"] == "Brand in Zwolle" and story["summary"] is None, "unwritten stories show their article title"
    db.execute("INSERT INTO updates(story, at, text, articles) VALUES (2, ?, 'The king accepted the resignation.', '[3]')",
               (news.iso(news.now()),))
    db.execute("UPDATE stories SET headline = 'Mbappé scores' WHERE id = 1")
    news.index(db)
    assert [st["id"] for st in news.search(db, "king resignation")] == [2], "recent stories are indexed again with updates"
    assert not news.search(db, "mbappe"), "old stories keep their index"
    db.execute("DELETE FROM search_index")
    news.index(db)
    assert [st["id"] for st in news.search(db, "mbappe")] == [1], "accents don't matter"


def test_elsewhere():
    os.environ["MISTRAL_API_KEY"] = "test"
    db = news.connect()
    for sid, sources, subjects in ((1, 1, []), (2, 29, []), (3, 30, []), (4, 4, ["Football"]), (5, 5, ["Football"])):
        db.execute("INSERT INTO stories(id, updated, n, interests) VALUES (?, ?, ?, ?)",
                   (sid, news.iso(news.now()), sources, json.dumps(subjects)))
        for i in range(sources):
            add_article(db, sid * 100 + i, sid, f"Outlet {i}", f"Story {sid} from outlet {i}")
    news.call_mistral = lambda model, system, payload, max_tokens: (json.dumps({"stories": [
        {"key": st["key"], "headline": "H", "summary": "S", "region": "Elsewhere"} for st in payload["stories"]]}), (10, 10))
    news.write(db)
    assert db.execute("SELECT DISTINCT region FROM stories").fetchall() == [("Elsewhere",)]
    assert sorted(s["id"] for s in news.api(db)["stories"]) == [3, 5], \
        "from a place the reader doesn't follow, only what 30 sources cover, or 5 and it fits a subject"
    db.execute("UPDATE stories SET region = NULL WHERE id = 1")
    assert sorted(s["id"] for s in news.api(db)["stories"]) == [1, 3, 5], "a story about no place stays"
    del os.environ["MISTRAL_API_KEY"]


def test_order():
    db = news.connect()
    t = news.iso(news.now())
    db.execute("INSERT INTO stories(id, updated, n) VALUES (1, ?, 6), (5, ?, 2), (7, ?, 3)", (t,) * 3)
    for i, outlet in enumerate(("NOS", "AD", "NRC", "Trouw", "BBC", "CNN")):
        add_article(db, 10 + i, 1, outlet, f"Yesterday's big story {i}", age=timedelta(hours=26))
    add_article(db, 16, 1, "NOS", "A late follow-up", age=timedelta(hours=1))
    add_article(db, 50, 5, "AD", "One article, two papers", age=timedelta(hours=10))
    add_article(db, 51, 5, "Tubantia", "One article, two papers", age=timedelta(hours=2))
    for i, outlet in enumerate(("NOS", "AD", "BBC")):
        add_article(db, 70 + i, 7, outlet, f"Breaking {i}")
    data = news.api(db)
    assert [s["id"] for s in data["stories"]] == [7, 1, 5], "today's news beats yesterday's bigger story"
    assert data["stories"][0]["coverage"] > data["stories"][1]["coverage"] > 0, "the app orders a tab's list by it"
    fade, shown = news.FADE / timedelta(hours=1), {s["id"]: s for s in news.shown(db)}
    assert abs(news.weight(shown[1], news.now()) - (0.5 ** (1 / fade) + 5 * 0.5 ** (26 / fade))) < 0.01, \
        "each outlet counts once, at its newest"
    assert abs(news.weight(shown[5], news.now()) - 0.5 ** (2 / fade)) < 0.01, "copies count once, at the newest"

    db = news.connect()
    stories = {"G": (1, "Global", 20, 1, "AI", 1), "A": (2, "NL", 6, 1, None, 0), "B9": (3, "Global", 5, 1, "AI", 1),
               "N3": (4, "Global", 3, 1, "AI", 1), "N2": (5, "Global", 2, 6, "AI", 1), "N1": (6, "Global", 1, 2, "AI", 1),
               "N4": (7, "Global", 1, 3, "AI", 1), "N5": (8, "Global", 1, 5, "AI", 1), "F": (9, "NL", 2, 1, None, 0),
               "R": (10, "Global", 1, 1, "AI", 0), "K": (11, "Global", 1, 2, "Knitting", 1)}
    for sid, region, sources, hours, subject, notable in stories.values():
        db.execute("INSERT INTO stories(id, updated, n, headline, summary, region, interests, notable) VALUES (?,?,?,?,?,?,?,?)",
                   (sid, t, sources, f"H{sid}", "S", region, json.dumps([subject] if subject else []), notable))
        for i in range(sources):
            add_article(db, sid * 100 + i, sid, f"Outlet {i}", f"Story {sid} from outlet {i}", age=timedelta(hours=hours))
    db.execute("UPDATE articles SET published = ? WHERE id = 400", (news.iso(news.now() - timedelta(hours=30)),))
    names = {sid: name for name, (sid, *_) in stories.items()}
    data = {names[s["id"]]: s for s in news.api(db)["stories"]}
    assert list(data) == ["G", "N3", "N1", "N4", "A", "B9", "F", "N2", "R", "K", "N5"], \
        "in All, a subject's three freshest notable stories that the lift at least doubles rise, also one three outlets " \
        "cover; not a routine one, an older fourth, a bigger one that needs little, or one of a subject the settings no " \
        "longer have"
    assert data["N3"]["lift"] == {"AI": round(news.NOTABLE_LIFT * 0.5 ** (1 / fade), 3)}, "the lift fades from the newest article"
    assert (data["G"]["lift"], set(data["B9"]["lift"]), set(data["N5"]["lift"]), data["K"]["lift"]) == ({}, {"AI"}, {"AI"}, {}), \
        "a big notable story needs no lift; in the AI tab's own list, the others all rise"


def test_settings():
    db = news.connect()
    data = news.settings_json(db)
    assert [s["url"] for s in data["sources"]] == [s["url"] for s in news.load_sources()[0]]
    assert news.save_settings(db, {"sources": data["sources"]}) is False
    assert news.setting(db, "sources", None) == {"removed": [], "added": [], "lean": {}}, "the list as it came changes nothing"

    feeds = [s for s in data["sources"] if s["name"] != "GeenStijl"]
    feeds = [s | {"lean": "left"} if s["name"] == "NOS" else s for s in feeds]
    feeds.append({"region": None, "name": "Quanta Blog", "url": "https://quanta.example/feed", "lang": "en",
                  "opinion": False, "lean": "center"})
    news.save_settings(db, {"sources": feeds})
    sources, names, _ = news.load_sources(db)
    assert "GeenStijl" not in {s["name"] for s in sources} and sources[-1]["name"] == "Quanta Blog"
    news.SOURCES = Path(tempfile.mkdtemp(), "sources.toml")
    news.SOURCES.write_text(SOURCES.read_text().replace("Topics = [", 'Topics = [\n  { name = "Quanta", url = "https://quanta.example/feed" },', 1))
    assert [s["name"] for s in news.load_sources(db)[0] if "quanta.example" in s["url"]] == ["Quanta Blog"], \
        "a feed the app added that sources.toml later lists too is there once, as the app has it"
    news.SOURCES = SOURCES
    assert names[news.outlet_key("Quanta Blog")] == "Quanta Blog", "articles are credited to the new feed's outlet"
    lean = news.leans(db)
    assert (lean[news.outlet_key("NOS")], lean[news.outlet_key("Quanta Blog")]) == ("left", "center")
    assert news.leans()[news.outlet_key("NOS")] == "center", "sources.toml itself is untouched"
    assert all(s["lean"] == "left" for s in news.settings_json(db)["sources"] if s["name"] == "NOS")

    nos = next(s for s in feeds if s["name"] == "NOS")
    for bad in ([*feeds, nos], [*feeds, nos | {"url": "file:///etc/passwd"}], [*feeds, nos | {"url": "https://nos.nl/x", "region": "Mars"}],
                [*feeds, nos | {"url": "https://nos.nl/x", "name": "nos.nl"}], [*feeds, nos | {"url": "https://nos.nl/x", "lean": "right"}],
                [*feeds, "junk"], "junk"):
        try:
            news.save_settings(db, {"sources": bad})
            raise AssertionError(f"accepted {bad if isinstance(bad, str) else bad[-1]}")
        except ValueError:
            pass
    assert news.load_sources(db)[0][-1]["name"] == "Quanta Blog", "a refused change stores nothing"

    t = news.iso(news.now())
    db.execute("INSERT INTO stories(id, updated, n, headline, interests) VALUES (1, ?, 1, 'H', '[\"AI\"]')", (t,))
    assert news.save_settings(db, {"interests": [{"name": "Politics", "about": "elections and governments"}]}) is True
    assert news.api(db)["tabs"] == ["Politics"]
    assert "- Politics: elections and governments" in news.system_prompt(db, "tag")
    assert news.reply_schema(db, "tag")["properties"]["stories"]["items"]["properties"]["interests"]["items"]["enum"] == ["Politics"]
    assert db.execute("SELECT interests FROM stories").fetchone() == (None,), "recent stories are sorted again"
    for bad in ([{"name": "AI", "about": "x"}, {"name": "ai", "about": "y"}], [{"name": "NL", "about": "x"}],
                [{"name": "Film", "about": " "}], [{"name": "!!", "about": "x"}], {"name": "Film"}):
        try:
            news.save_settings(db, {"interests": bad})
            raise AssertionError(f"accepted {bad}")
        except ValueError:
            pass

    db.execute("DELETE FROM settings WHERE key = 'interests'")
    db.execute("INSERT INTO stories(id, updated, n, headline, region) VALUES (2, ?, 1, 'H', 'NL'), (3, ?, 1, 'H', 'Overijssel'),"
               " (4, ?, 1, 'H', 'Zwolle')", (t,) * 3)
    add_article(db, 30, 2, "NOS", "Kabinet valt", lang="nl")
    db.execute("UPDATE articles SET region = 'NL' WHERE id = 30")
    edited = news.settings_json(db)["places"]
    assert edited[2] == {"name": "NL", "about": news.PLACES["NL"]["about"], "major": 5}
    edited = [p | {"was": p["name"]} for p in edited if p["name"] != "Overijssel"]
    edited[1] |= {"name": "Netherlands"}
    edited.append({"name": "Deventer", "about": "the city of Deventer", "major": 2, "was": None})
    assert news.save_settings(db, {"places": edited}) is False
    assert [p["name"] for p in news.settings_json(db)["places"]] == ["Zwolle", "Netherlands", "EU", "US", "Global", "Deventer"]
    regions = {s["url"]: s["region"] for s in news.load_sources(db)[0]}
    bundled = {s["url"]: s["region"] for s in news.load_sources()[0]}
    assert {regions[u] for u, r in bundled.items() if r == "NL" and u in regions} == {"Netherlands"}, "a renamed place keeps its feeds"
    assert {regions[u] for u, r in bundled.items() if r == "Overijssel"} == {None}, "a deleted place's feeds have none"
    assert regions["https://quanta.example/feed"] is None
    assert db.execute("SELECT id, region FROM stories WHERE id > 1 ORDER BY id").fetchall() == [
        (2, "Netherlands"), (3, None), (4, "Zwolle")]
    assert db.execute("SELECT region FROM articles WHERE id = 30").fetchone() == ("Netherlands",)
    assert "- Deventer: the city of Deventer" in news.system_prompt(db, "new")
    assert news.reply_schema(db, "new")["properties"]["stories"]["items"]["properties"]["region"]["enum"] == [
        "Zwolle", "Netherlands", "EU", "US", "Global", "Deventer", "Elsewhere", "None"]
    feeds = news.settings_json(db)["sources"]
    news.save_settings(db, {"sources": feeds})
    assert news.setting(db, "sources", None)["removed"] == ["https://www.geenstijl.nl/feeds/recent.atom"], \
        "a rename doesn't turn its feeds into edits"

    swapped = [p | {"was": p["name"]} for p in news.settings_json(db)["places"]]
    swapped[0]["name"], swapped[1]["name"] = "Netherlands", "Zwolle"
    news.save_settings(db, {"places": swapped})
    assert db.execute("SELECT id, region FROM stories WHERE id IN (2, 4) ORDER BY id").fetchall() == [
        (2, "Zwolle"), (4, "Netherlands")], "two names swapped in one go"
    assert {r for u, r in ((s["url"], s["region"]) for s in news.load_sources(db)[0]) if bundled.get(u) == "NL"} == {"Zwolle"}
    swapped = [p | {"was": p["name"]} for p in news.settings_json(db)["places"]] + [{"name": "NL", "about": "x", "major": 3, "was": None}]
    news.save_settings(db, {"places": swapped})
    assert {s["region"] for s in news.load_sources(db)[0] if bundled.get(s["url"]) == "NL"} == {"Zwolle"}, \
        "a new place with an old name doesn't take the feeds that moved"
    current = [p | {"was": p["name"]} for p in news.settings_json(db)["places"]]
    for bad in ({"places": current + [{"name": "Topics", "about": "x", "major": 1, "was": None}]},
                {"places": current + [{"name": "elsewhere", "about": "x", "major": 1, "was": None}]},
                {"places": current + [{"name": "ai", "about": "x", "major": 1, "was": None}]},
                {"places": [current[0] | {"major": 0}]}, {"places": [current[0] | {"major": 2.5}]},
                {"places": [current[0] | {"was": "Mars"}]}, {"places": [current[0], current[0] | {"name": "Z2"}]},
                {"places": current, "sources": feeds}, {"interests": [{"name": "Deventer", "about": "x"}]},
                {"places": current + [current[0] | {"was": None}]}, {"places": [current[1] | {"name": current[0]["name"]}, current[0]]},
                {"places": [current[0] | {"was": ["x"]}]},
                {"interests": [{"name": "Film", "about": "x"}, {"name": "Film", "about": "y"}]}):
        try:
            news.save_settings(db, bad)
            raise AssertionError(f"accepted {bad}")
        except ValueError:
            pass

    news.save_settings(db, {"budgets": {"claude": 5, "mistral": 0}})
    assert news.budgets(db) == {"claude": 5.0, "mistral": 0.0}
    for bad in ({"claude": -1, "mistral": 0}, {"claude": float("nan"), "mistral": 0}, {"claude": "5", "mistral": 0}, {"claude": 5}):
        try:
            news.save_settings(db, {"budgets": bad})
            raise AssertionError(f"accepted {bad}")
        except ValueError:
            pass

    news.save_settings(db, {"prompts": {"rules": "Rules: be brief.", "report": news.REPORT_PROMPT + "\n"}})
    new = news.system_prompt(db, "new")
    assert new.startswith(news.NEW_PROMPT + "\nPlaces:\n- ") and new.endswith("\nRules: be brief.\n" + news.FORMATS["new"])
    assert news.setting(db, "prompts", None) == {"rules": "Rules: be brief."}, "a prompt set back to its default isn't stored"
    news.save_settings(db, {"prompts": {"rules": ""}})
    assert news.system_prompt(db, "new").endswith("\n".join(["", news.RULES, news.FORMATS["new"]]))
    for bad in ({"prompts": {"secret": "x"}}, {"prompts": "x"}, {"threshold": 0.5}, []):
        try:
            news.save_settings(db, bad)
            raise AssertionError(f"accepted {bad}")
        except ValueError:
            pass


def test_http():
    server = news.ThreadingHTTPServer(("127.0.0.1", 0), news.Page)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    with urllib.request.urlopen(urllib.request.Request(base + "/api/stories", headers={"Accept-Encoding": "gzip"})) as r:
        assert r.headers["Content-Encoding"] == "gzip" and r.headers["Content-Type"] == "application/json"
        assert json.loads(gzip.decompress(r.read()))["stories"] == []
    with urllib.request.urlopen(base + "/api/search?q=pec+zwolle") as r:
        assert json.loads(r.read()) == {"stories": []}
    with urllib.request.urlopen(base + "/api/report") as r:
        assert r.read() == b"null"
    with urllib.request.urlopen(base + "/") as r:
        assert r.headers["Content-Encoding"] is None and r.read().startswith(b"<!doctype html>")
    try:
        urllib.request.urlopen(base + "/api/nothing")
        raise AssertionError("unknown paths are not found")
    except urllib.error.HTTPError as e:
        assert e.code == 404
    with urllib.request.urlopen(base + "/api/settings") as r:
        assert json.loads(r.read())["budgets"] == {"claude": news.CLAUDE_DAILY_BUDGET, "mistral": news.DAILY_BUDGET}

    def post(body, kind="application/json"):
        request = urllib.request.Request(base + "/api/settings", body, {"Content-Type": kind})
        try:
            with urllib.request.urlopen(request) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    status, data = post(b'{"budgets": {"claude": 7, "mistral": 1}}')
    assert status == 200 and data["budgets"] == {"claude": 7.0, "mistral": 1.0}
    status, data = post(b'{"budgets": {"claude": -7, "mistral": 1}}')
    assert status == 400 and json.loads(data)["error"].startswith("A spending cap")
    assert post(b"{not json")[0] == 400
    assert post(b'{"budgets": {"claude": 1' + b"0" * 400 + b', "mistral": 1}}')[0] == 400
    assert post(b"[" * 100_000)[0] == 400
    with tempfile.TemporaryDirectory() as tmp:
        news.DB = str(Path(tmp, "news.db"))
        busy = news.connect()
        busy.execute("UPDATE stories SET n = 0")  # a write transaction the processing run holds
        started = time.monotonic()
        status, data = post(b'{"budgets": {"claude": 7, "mistral": 1}}')
        assert status == 503 and b"busy" in data and time.monotonic() - started < 15, "a busy server says so in time"
        busy.rollback()
        assert post(b'{"budgets": {"claude": 7, "mistral": 1}}')[0] == 200
        busy.close()
        news.DB = ":memory:"
    assert post(b'{"budgets": {"claude": 7, "mistral": 1}}', "text/plain")[0] == 415, "what a web page can send unasked"
    server.shutdown()


test_parse()
test_outlet_key()
test_sources()
test_source_count()
test_collect()
test_collect_deadline()
test_group()
test_translate_before_grouping()
test_language_backfill()
test_mistral_articles()
test_write()
test_claude_writer()
test_tag()
test_notable()
test_morning()
test_search()
test_elsewhere()
test_order()
test_settings()
test_http()
print("ok")
