"""Self-check, run inside the container: python test_news.py"""

import os

os.environ["NEWS_DB"] = ":memory:"

import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

import news

UTC = timezone.utc

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
    def a(outlet, title):
        return (1, outlet, None, title, "", "", 0)

    story = [a("AD", "Brand in Zwolle"), a("Tubantia", "Brand in  zwolle"), a("De Stentor", "Brand in Zwolle"),
             a("NOS", "Grote brand in Zwolle"), a("NOS", "Brand in Zwolle geblust")]
    assert news.source_count(story) == 2, "identical headlines from sister papers count once"
    chained = [a("AD", "X"), a("Tubantia", "X"), a("AD", "Y"), a("De Stentor", "Y"), a("NOS", "Z")]
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


test_parse()
test_outlet_key()
test_sources()
test_source_count()
test_collect()
test_collect_deadline()
test_group()
print("ok")
