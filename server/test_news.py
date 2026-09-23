"""Self-check, run inside the container: python test_news.py"""

import os

os.environ["NEWS_DB"] = ":memory:"

import json
import tempfile
import time
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


def test_source_count():
    story = [("AD", "Brand in Zwolle"), ("Tubantia", "Brand in  zwolle"), ("De Stentor", "Brand in Zwolle"),
             ("NOS", "Grote brand in Zwolle"), ("NOS", "Brand in Zwolle geblust")]
    assert news.source_count(story) == 2, "identical headlines from sister papers count once"
    chained = [("AD", "X"), ("Tubantia", "X"), ("AD", "Y"), ("De Stentor", "Y"), ("NOS", "Z")]
    assert news.source_count(chained) == 2


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

    news.regroup(db)
    assert story("a1") == story("a2") == story("a3") != story("b1"), "regroup rebuilds the same stories"
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
        if system is news.NEW_PROMPT:
            reply = {"stories": [{"key": "s1", "headline": "Dutch cabinet falls", "summary": "The cabinet fell.", "region": "NL",
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
    assert calls[-1][0] is news.UPDATE_PROMPT
    assert [a["title"] for a in calls[-1][1]["stories"][0]["articles"]] == ["Koning aanvaardt ontslag"], \
        "an update only gets the new articles"
    assert db.execute("SELECT headline, summary FROM stories WHERE id = 1").fetchone() == (
        "Dutch cabinet falls", "The cabinet fell."), "updates never rewrite the story"
    assert db.execute("SELECT text, articles FROM updates").fetchall() == [("The king accepted the resignation.", "[5]")], \
        "one sentence per new article, linked to it"

    page = news.render(db)
    assert "The king accepted the resignation." in page and "The cabinet had no plan left" in page
    assert page.count(">Tubantia</a>") == 1, "the summary names the sources it was written from"
    assert "Een column zonder nieuws" not in page

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
    assert news.call_claude("claude-sonnet-5", news.NEW_PROMPT, {}) == ('{"ok": 1}', 0.01)
    assert json.loads(calls[0][calls[0].index("--json-schema") + 1]) == news.SCHEMAS[news.NEW_PROMPT]
    assert calls[0][calls[0].index("--tools") + 1] == "", "Claude Code's own tools are off"
    for run, error in ((Run(json.dumps({"is_error": True, "result": "Claude AI usage limit reached"}), 1), news.ClaudeUnavailable),
                       (Run(json.dumps({"is_error": True, "result": "x", "api_error_status": 401}), 1), news.ClaudeUnavailable),
                       (Run(json.dumps({"is_error": True, "subtype": "error_max_turns"}), 1), news.ClaudeFailed),
                       (Run(json.dumps({"is_error": False, "result": "done"})), news.ClaudeFailed),
                       (Run("not json"), news.ClaudeFailed)):
        news.subprocess.run = lambda cmd, **kw: run
        try:
            news.call_claude("claude-sonnet-5", news.NEW_PROMPT, {})
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

    def claude(model, system, payload):
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

    def flaky(model, system, payload):
        used.append(model)
        if len(used) == 1:
            raise news.ClaudeFailed("no reply within 10 minutes")
        return reply(system, payload), 0.25

    news.call_claude = flaky
    news.write(db)
    assert used == [news.CLAUDE_WRITER, news.CLAUDE_WRITER], "one failed request doesn't hand the run to Mistral"
    assert db.execute("SELECT COUNT(*) FROM stories WHERE headline = 'H'").fetchone()[0] == 1, "that batch waits"
    news.call_claude, news.call_mistral, news.batches = originals
    del os.environ["MISTRAL_API_KEY"], os.environ["CLAUDE_CODE_OAUTH_TOKEN"]


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
print("ok")
