package io.github.schotjechrisman.news

import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL
import java.time.Instant
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

data class News(val built: Instant, val tabs: List<String>, val stories: List<Story>, val feeds: List<Feed>)

/** The server's whole news document. HttpURLConnection asks for gzip and unpacks it by itself. */
fun download(server: String): String {
    val connection = URL("${server.trimEnd('/')}/api/stories").openConnection() as HttpURLConnection
    connection.connectTimeout = 15_000
    connection.readTimeout = 60_000
    try {
        if (connection.responseCode != 200) throw IOException("the server answered ${connection.responseCode}")
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
        stories = root.getJSONArray("stories").objects().map { s ->
            val lean = s.getJSONObject("lean")
            Story(
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
        },
        feeds = root.getJSONArray("feeds").objects().map {
            Feed(it.getString("name"), it.getString("url"), it.optInt("items"), it.text("error"))
        },
    )
}

private fun time(iso: String): Instant = OffsetDateTime.parse(iso).toInstant()

/** getString turns JSON null into "null". */
private fun JSONObject.text(name: String): String? = if (isNull(name)) null else getString(name)

private fun JSONArray.objects() = (0 until length()).map { getJSONObject(it) }

private fun JSONArray.strings() = (0 until length()).map { getString(it) }

private fun refs(array: JSONArray) = array.objects().map { Ref(it.getString("outlet"), it.getString("url")) }
