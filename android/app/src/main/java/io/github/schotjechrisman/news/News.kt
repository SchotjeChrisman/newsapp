package io.github.schotjechrisman.news

import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL
import java.time.Instant
import java.time.LocalDate
import java.time.OffsetDateTime
import org.json.JSONArray
import org.json.JSONObject

data class Ref(val outlet: String, val url: String)

data class Update(val at: Instant, val text: String, val from: List<Ref>)

data class Quote(val text: String, val outlet: String, val url: String, val translated: Boolean)

data class Article(
    val outlet: String,
    val title: String,
    val url: String,
    val published: Instant,
    val opinion: Boolean,
    val lean: String?,
)

/** Independent sources per lean; sources without a rating aren't in it. */
data class Lean(val left: Int, val center: Int, val right: Int) {
    val rated get() = left + center + right
}

data class Story(
    val id: Long,
    val headline: String,
    val summary: String?,
    val tabs: List<String>,
    val sources: Int,
    val updated: Instant,
    val lean: Lean,
    val summaryFrom: List<Ref>,
    val updates: List<Update>,
    val quotes: List<Quote>,
    val articles: List<Article>,
)

data class Feed(val name: String, val url: String, val items: Int, val error: String?)

/** The S&P 500's last close; change is in percent. */
data class Market(val name: String, val close: Double, val change: Double, val date: LocalDate)

data class ReportStory(val id: Long, val headline: String, val gist: String, val sources: Int)

data class Section(val title: String, val stories: List<ReportStory>)

data class Report(val day: LocalDate, val market: Market?, val sections: List<Section>)

data class News(
    val built: Instant,
    val tabs: List<String>,
    val stories: List<Story>,
    val feeds: List<Feed>,
    val report: Report?,
)

data class Interest(val name: String, val about: String)

/** A place tab. major: the independent sources a story needs for the morning report. was: its name on the server, null
 *  for a new one, so the server can move its feeds and stories along when it's renamed. */
data class Place(val name: String, val about: String, val major: Int, val was: String?)

/** A feed the server collects; region null is Topics. The lean belongs to the outlet, so its feeds share it. */
data class Source(val region: String?, val name: String, val url: String, val lang: String, val opinion: Boolean, val lean: String?)

data class Prompt(val kind: String, val title: String, val about: String, val text: String, val default: String)

/** What the server lets the app change; the caps are dollars a day. */
data class Settings(
    val regions: List<String>,
    val places: List<Place>,
    val interests: List<Interest>,
    val sources: List<Source>,
    val claudeCap: Double,
    val mistralCap: Double,
    val prompts: List<Prompt>,
)

/** The server turned a change down or couldn't make it; the message says why. */
class Refused(message: String) : IOException(message)

/** A document from the server: the whole news (/api/stories) or the morning report (/api/report).
 *  HttpURLConnection asks for gzip and unpacks it by itself. */
fun download(server: String, path: String = "/api/stories", timeout: Int = 60_000): String {
    val connection = URL("${server.trimEnd('/')}$path").openConnection() as HttpURLConnection
    connection.connectTimeout = minOf(15_000, timeout)
    connection.readTimeout = timeout
    try {
        if (connection.responseCode != 200) throw IOException("the server answered ${connection.responseCode}")
        return connection.inputStream.bufferedReader().use { it.readText() }
    } finally {
        connection.disconnect()
    }
}

/** Sends a JSON document and returns the server's answer. */
fun upload(server: String, path: String, json: String, timeout: Int = 30_000): String {
    val connection = URL("${server.trimEnd('/')}$path").openConnection() as HttpURLConnection
    connection.connectTimeout = minOf(15_000, timeout)
    connection.readTimeout = timeout
    connection.requestMethod = "POST"
    connection.doOutput = true
    connection.setRequestProperty("Content-Type", "application/json")
    try {
        connection.outputStream.use { it.write(json.toByteArray()) }
        val code = connection.responseCode
        if (code != 200) {
            val why = runCatching { JSONObject(connection.errorStream.bufferedReader().use { it.readText() }).getString("error") }
            throw why.map { Refused(it) }.getOrElse { IOException("the server answered $code") }
        }
        return connection.inputStream.bufferedReader().use { it.readText() }
    } finally {
        connection.disconnect()
    }
}

fun parse(json: String): News {
    val root = JSONObject(json)
    return News(
        built = time(root.getString("built")),
        tabs = root.getJSONArray("tabs").strings(),
        stories = root.getJSONArray("stories").objects().map(::story),
        feeds = root.getJSONArray("feeds").objects().map {
            Feed(it.getString("name"), it.getString("url"), it.optInt("items"), it.text("error"))
        },
        report = root.optJSONObject("report")?.let(::report),
    )
}

/** The stories /api/search found. */
fun parseSearch(json: String): List<Story> = JSONObject(json).getJSONArray("stories").objects().map(::story)

private fun story(s: JSONObject): Story {
    val lean = s.getJSONObject("lean")
    return Story(
        id = s.getLong("id"),
        headline = s.getString("headline"),
        summary = s.text("summary"),
        tabs = s.getJSONArray("tabs").strings(),
        sources = s.getInt("sources"),
        updated = time(s.getString("updated")),
        lean = Lean(lean.getInt("left"), lean.getInt("center"), lean.getInt("right")),
        summaryFrom = refs(s.getJSONArray("summary_from")),
        updates = s.getJSONArray("updates").objects().map {
            Update(time(it.getString("at")), it.getString("text"), refs(it.getJSONArray("from")))
        },
        quotes = s.getJSONArray("quotes").objects().map {
            Quote(it.getString("text"), it.getString("outlet"), it.getString("url"), it.getBoolean("translated"))
        },
        articles = s.getJSONArray("articles").objects().map {
            Article(
                outlet = it.getString("outlet"),
                title = it.getString("title"),
                url = it.getString("url"),
                published = time(it.getString("published")),
                opinion = it.getBoolean("opinion"),
                lean = it.text("lean"),
            )
        },
    )
}

/** The morning report, from /api/report or inside the news document; null before the first report. */
fun parseReport(json: String): Report? = if (json.trim() == "null") null else report(JSONObject(json))

private fun report(r: JSONObject) = Report(
    day = LocalDate.parse(r.getString("day")),
    market = r.optJSONObject("market")?.let {
        Market(it.getString("name"), it.getDouble("close"), it.getDouble("change"), LocalDate.parse(it.getString("date")))
    },
    sections = r.getJSONArray("sections").objects().map { section ->
        Section(section.getString("title"), section.getJSONArray("stories").objects().map {
            ReportStory(it.getLong("id"), it.getString("headline"), it.getString("gist"), it.getInt("sources"))
        })
    },
)

fun parseSettings(json: String): Settings {
    val root = JSONObject(json)
    val caps = root.getJSONObject("budgets")
    return Settings(
        regions = root.getJSONArray("regions").strings(),
        places = root.optJSONArray("places")?.objects()?.map {
            Place(it.getString("name"), it.getString("about"), it.getInt("major"), it.getString("name"))
        }.orEmpty(),
        interests = root.getJSONArray("interests").objects().map { Interest(it.getString("name"), it.getString("about")) },
        sources = root.getJSONArray("sources").objects().map {
            Source(it.text("region"), it.getString("name"), it.getString("url"), it.getString("lang"), it.getBoolean("opinion"), it.text("lean"))
        },
        claudeCap = caps.getDouble("claude"),
        mistralCap = caps.getDouble("mistral"),
        prompts = root.getJSONArray("prompts").objects().map {
            Prompt(it.getString("kind"), it.getString("title"), it.getString("about"), it.getString("text"), it.getString("default"))
        },
    )
}

// The parts of the settings as the server takes them; each is sent on its own, whole.

fun placesJson(places: List<Place>): JSONObject = JSONObject().put("places", JSONArray(places.map {
    JSONObject().put("name", it.name).put("about", it.about).put("major", it.major).put("was", it.was ?: JSONObject.NULL)
}))

fun interestsJson(interests: List<Interest>): JSONObject =
    JSONObject().put("interests", JSONArray(interests.map { JSONObject().put("name", it.name).put("about", it.about) }))

fun sourcesJson(sources: List<Source>): JSONObject = JSONObject().put("sources", JSONArray(sources.map {
    JSONObject().put("region", it.region ?: JSONObject.NULL).put("name", it.name).put("url", it.url).put("lang", it.lang)
        .put("opinion", it.opinion).put("lean", it.lean ?: JSONObject.NULL)
}))

fun capsJson(claude: Double, mistral: Double): JSONObject =
    JSONObject().put("budgets", JSONObject().put("claude", claude).put("mistral", mistral))

fun promptJson(kind: String, text: String): JSONObject = JSONObject().put("prompts", JSONObject().put(kind, text))

private fun time(iso: String): Instant = OffsetDateTime.parse(iso).toInstant()

/** getString turns JSON null into "null". */
private fun JSONObject.text(name: String): String? = if (isNull(name)) null else getString(name)

private fun JSONArray.objects() = (0 until length()).map { getJSONObject(it) }

private fun JSONArray.strings() = (0 until length()).map { getString(it) }

private fun refs(array: JSONArray) = array.objects().map { Ref(it.getString("outlet"), it.getString("url")) }
