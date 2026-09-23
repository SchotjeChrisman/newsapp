package io.github.schotjechrisman.news

import android.content.Context
import android.os.Bundle
import android.os.SystemClock
import androidx.activity.ComponentActivity
import androidx.activity.compose.BackHandler
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.lazy.LazyListState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.lifecycle.lifecycleScope
import java.io.File
import java.net.ConnectException
import java.net.SocketTimeoutException
import java.net.UnknownHostException
import kotlin.coroutines.cancellation.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONException

class MainActivity : ComponentActivity() {
    private val state by lazy { NewsState(applicationContext) }

    override fun onCreate(savedInstanceState: Bundle?) {
        enableEdgeToEdge()
        super.onCreate(savedInstanceState)
        setContent { NewsTheme { App(state) } }
    }

    override fun onResume() {
        super.onResume()
        lifecycleScope.launch { state.refresh(staleAfterMinutes = 30) }
    }
}

/** The news on screen: the last download, kept in a file so the app opens with it, even offline. */
class NewsState(context: Context) {
    private val prefs = context.getSharedPreferences("news", Context.MODE_PRIVATE)
    private val cache = File(context.filesDir, "news.json")
    private var fetched = 0L

    var server by mutableStateOf(prefs.getString("server", "") ?: "")
        private set
    var news by mutableStateOf<News?>(null)
        private set
    var loading by mutableStateOf(false)
        private set
    var error by mutableStateOf<String?>(null)
        private set

    fun changeServer(address: String) {
        val trimmed = address.trim().trimEnd('/')
        server = if ("://" in trimmed) trimmed else "http://$trimmed"
        prefs.edit().putString("server", server).apply()
        fetched = 0
    }

    suspend fun refresh(staleAfterMinutes: Long = 0) {
        if (loading) return
        loading = true
        val from = server
        try {
            if (news == null && cache.exists()) {
                news = withContext(Dispatchers.IO) { runCatching { parse(cache.readText()) }.getOrNull() }
            }
            val stale = fetched == 0L || SystemClock.elapsedRealtime() - fetched >= staleAfterMinutes * 60_000
            if (from.isNotBlank() && stale) {
                error = null
                val (fresh, json) = withContext(Dispatchers.IO) { download(from).let { parse(it) to it } }
                if (server == from) {
                    news = fresh
                    fetched = SystemClock.elapsedRealtime()
                    withContext(Dispatchers.IO) { cache.writeText(json) }
                }
            }
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            if (server == from) {
                error = when (e) {
                    is UnknownHostException -> "Can't find $from. Is Tailscale on?"
                    is ConnectException, is SocketTimeoutException -> "Can't reach $from."
                    is JSONException -> "The server sent something the app can't read."
                    else -> "Couldn't load the news: ${e.message ?: e.javaClass.simpleName}"
                }
            }
        } finally {
            loading = false
        }
        // The address changed while this was loading: fetch from the new one.
        if (server != from) refresh()
    }
}

enum class Screen { Stories, Story, Feeds, Server }

@Composable
fun App(state: NewsState) {
    val scope = rememberCoroutineScope()
    var screen by rememberSaveable { mutableStateOf(if (state.server.isBlank()) Screen.Server else Screen.Stories) }
    var storyId by rememberSaveable { mutableLongStateOf(0L) }
    var tab by rememberSaveable { mutableStateOf("All") }
    // One scroll position per tab, kept while a story is open.
    val lists = remember { mutableMapOf<String, LazyListState>() }
    val news = state.news

    BackHandler(enabled = screen != Screen.Stories && state.server.isNotBlank()) { screen = Screen.Stories }

    when (screen) {
        Screen.Stories -> StoriesScreen(
            news = news,
            loading = state.loading,
            error = state.error,
            tab = tab,
            listState = lists.getOrPut(tab) { LazyListState() },
            onTab = { tab = it },
            onRefresh = { scope.launch { state.refresh() } },
            onOpen = { storyId = it.id; screen = Screen.Story },
            onFeeds = { screen = Screen.Feeds },
            onServer = { screen = Screen.Server },
        )
        Screen.Story -> {
            val story = news?.stories?.find { it.id == storyId }
            if (story == null) LaunchedEffect(Unit) { screen = Screen.Stories }
            else StoryScreen(story, onBack = { screen = Screen.Stories })
        }
        Screen.Feeds -> FeedsScreen(news?.feeds.orEmpty(), onBack = { screen = Screen.Stories })
        Screen.Server -> ServerScreen(
            current = state.server,
            onBack = if (state.server.isBlank()) null else ({ screen = Screen.Stories }),
            onSave = {
                state.changeServer(it)
                screen = Screen.Stories
                scope.launch { state.refresh() }
            },
        )
    }
}
