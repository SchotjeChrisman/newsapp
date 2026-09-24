package io.github.schotjechrisman.news

import android.Manifest
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.os.SystemClock
import androidx.activity.ComponentActivity
import androidx.activity.SystemBarStyle
import androidx.activity.compose.BackHandler
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.WindowInsetsSides
import androidx.compose.foundation.layout.consumeWindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.only
import androidx.compose.foundation.layout.safeDrawing
import androidx.compose.foundation.lazy.LazyListState
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.TopAppBarState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.lifecycle.lifecycleScope
import java.io.File
import java.net.ConnectException
import java.net.URLEncoder
import java.net.SocketTimeoutException
import java.net.UnknownHostException
import kotlin.coroutines.cancellation.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONException
import org.json.JSONObject

class MainActivity : ComponentActivity() {
    private val state by lazy { NewsState(applicationContext) }
    private val askToNotify = registerForActivityResult(ActivityResultContracts.RequestPermission()) {}

    override fun onCreate(savedInstanceState: Bundle?) {
        enableEdgeToEdge()
        super.onCreate(savedInstanceState)
        if (savedInstanceState == null) state.openReport = intent.getBooleanExtra(MorningReport.OPEN, false)
        MorningReport.ensure(this)
        if (Build.VERSION.SDK_INT >= 33 && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            askToNotify.launch(Manifest.permission.POST_NOTIFICATIONS)
        }
        setContent {
            // Fixed light or dark bars, set again when the theme changes: the automatic style lets the system lay a
            // grey scrim over three-button navigation, where the app's own bar already is.
            val dark = isSystemInDarkTheme()
            DisposableEffect(dark) {
                val bars = if (dark) SystemBarStyle.dark(android.graphics.Color.TRANSPARENT)
                else SystemBarStyle.light(android.graphics.Color.TRANSPARENT, android.graphics.Color.TRANSPARENT)
                enableEdgeToEdge(bars, bars)
                onDispose {}
            }
            NewsTheme { App(state) }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        if (intent.getBooleanExtra(MorningReport.OPEN, false)) state.openReport = true
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
    private var searches = 0

    var server by mutableStateOf(prefs.getString("server", "") ?: "")
        private set
    var news by mutableStateOf<News?>(null)
        private set
    var loading by mutableStateOf(false)
        private set
    var error by mutableStateOf<String?>(null)
        private set
    /** Search results; null before the first search. */
    var results by mutableStateOf<List<Story>?>(null)
        private set
    var searching by mutableStateOf(false)
        private set
    var searchError by mutableStateOf<String?>(null)
        private set
    /** Set when the morning report's notification opens the app. */
    var openReport by mutableStateOf(false)
    /** The server's settings; null until the settings screen loads them. */
    var settings by mutableStateOf<Settings?>(null)
        private set
    var settingsError by mutableStateOf<String?>(null)
        private set

    fun changeServer(address: String) {
        val trimmed = address.trim().trimEnd('/')
        server = if ("://" in trimmed) trimmed else "http://$trimmed"
        prefs.edit().putString("server", server).apply()
        fetched = 0
        results = null
        settings = null
        settingsError = null
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
            if (server == from) error = explain(e, from)
        } finally {
            loading = false
        }
        // The address changed while this was loading: fetch from the new one.
        if (server != from) refresh()
    }

    /** Searches every story on the server, not only the ones the app holds. */
    suspend fun search(query: String) {
        if (query.isBlank() || server.isBlank()) return
        val from = server
        val mine = ++searches  // an answer to an earlier search that arrives later is dropped
        searching = true
        searchError = null
        try {
            val found = withContext(Dispatchers.IO) {
                parseSearch(download(from, "/api/search?q=" + URLEncoder.encode(query.trim(), "UTF-8"), timeout = 20_000))
            }
            if (mine == searches) results = found
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            if (mine == searches) searchError = explain(e, from)
        } finally {
            if (mine == searches) searching = false
        }
    }

    suspend fun loadSettings() {
        val from = server
        if (from.isBlank()) return
        settingsError = null
        try {
            val loaded = withContext(Dispatchers.IO) { parseSettings(download(from, "/api/settings", timeout = 20_000)) }
            if (server == from) settings = loaded
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            if (server == from) settingsError = explain(e, from, "load the settings")
        }
    }

    /** Sends one part of the settings. Returns why it wasn't saved, or null once the server has it. */
    suspend fun saveSettings(changes: JSONObject): String? {
        val from = server
        return try {
            val saved = withContext(Dispatchers.IO) { parseSettings(upload(from, "/api/settings", changes.toString())) }
            if (server == from) settings = saved
            null
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            explain(e, from, "save")
        }
    }

    private fun explain(e: Exception, server: String, what: String = "load the news") = when (e) {
        is Refused -> e.message ?: "The server turned the change down."
        is UnknownHostException -> "Can't find $server. Is Tailscale on?"
        is ConnectException, is SocketTimeoutException -> "Can't reach $server."
        is JSONException -> "The server sent something the app can't read."
        else -> "Couldn't $what: ${e.message ?: e.javaClass.simpleName}"
    }
}

enum class Screen { Stories, Story, Report, Search, Feeds, Server, Settings, Interests, Sources, Prompts, Prompt }

/** The settings' own pages, which need the settings loaded. */
private val settingsPages = setOf(Screen.Interests, Screen.Sources, Screen.Prompts, Screen.Prompt)

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun App(state: NewsState) {
    val scope = rememberCoroutineScope()
    var screen by rememberSaveable { mutableStateOf(if (state.server.isBlank()) Screen.Server else Screen.Stories) }
    var storyId by rememberSaveable { mutableLongStateOf(0L) }
    var storyFrom by rememberSaveable { mutableStateOf(Screen.Stories) }
    var tab by rememberSaveable { mutableStateOf("All") }
    var query by rememberSaveable { mutableStateOf("") }
    var promptKind by rememberSaveable { mutableStateOf("") }
    var serverFrom by rememberSaveable { mutableStateOf(Screen.Settings) }
    // Scroll positions of the main places (one per tab for the news), kept while a story is open or another place shows.
    val lists = remember { mutableMapOf<String, LazyListState>() }
    // Not saved like the others, because the news lists aren't: after a restart both start at the top.
    val newsBar = remember { TopAppBarState(-Float.MAX_VALUE, 0f, 0f) }
    val reportList = rememberLazyListState()
    val reportBar = remember { TopAppBarState(-Float.MAX_VALUE, 0f, 0f) }
    val searchList = rememberLazyListState()
    val news = state.news
    // An interest removed in the settings takes its tab with it.
    val shownTab = if (news == null || tab == "All" || tab in news.tabs) tab else "All"
    val rail = wide()
    val main = screen == Screen.Stories || screen == Screen.Report || screen == Screen.Search
    val navBar = @Composable { if (!rail) NavBar(screen) { screen = it } }
    val settings = state.settings
    // The interests are tabs and the leans color the stories, so the news comes again once they change.
    val save: suspend (JSONObject) -> String? = { changes ->
        state.saveSettings(changes).also { if (it == null && !changes.has("budgets") && !changes.has("prompts")) scope.launch { state.refresh() } }
    }
    val back = {
        screen = when (screen) {
            Screen.Story -> storyFrom
            Screen.Server -> serverFrom
            Screen.Interests, Screen.Sources, Screen.Prompts -> Screen.Settings
            Screen.Prompt -> Screen.Prompts
            else -> Screen.Stories
        }
    }

    BackHandler(enabled = screen != Screen.Stories && state.server.isNotBlank()) { back() }
    // The settings come fresh each time their screen opens. A page of them restored without them (after the system
    // ended the app) goes back to that screen, which loads them.
    LaunchedEffect(screen) {
        if (screen == Screen.Settings) state.loadSettings()
        if (screen in settingsPages && state.settings == null) screen = Screen.Settings
    }
    LaunchedEffect(state.openReport) {
        if (!state.openReport) return@LaunchedEffect
        state.openReport = false
        if (state.server.isNotBlank()) {
            screen = Screen.Report
            // The last download may be from before the report was built. Launched outside this effect, which ends as
            // soon as openReport is reset.
            scope.launch { state.refresh() }
        }
    }

    Row(Modifier.fillMaxSize()) {
        if (rail && main) NavRail(screen) { screen = it }
        // The rail already keeps clear of the cutout and the system bar on its side.
        val side = if (rail && main) Modifier.consumeWindowInsets(WindowInsets.safeDrawing.only(WindowInsetsSides.Start)) else Modifier
        Box(Modifier.weight(1f).then(side)) {
            when (screen) {
                Screen.Stories -> StoriesScreen(
                    news = news,
                    loading = state.loading,
                    error = state.error,
                    tab = shownTab,
                    listState = lists.getOrPut(shownTab) { LazyListState() },
                    barState = newsBar,
                    onTab = { tab = it },
                    onRefresh = { scope.launch { state.refresh() } },
                    onOpen = { storyId = it.id; storyFrom = Screen.Stories; screen = Screen.Story },
                    onFeeds = { screen = Screen.Feeds },
                    onSettings = { screen = Screen.Settings },
                    onServer = { serverFrom = Screen.Stories; screen = Screen.Server },
                    bottomBar = navBar,
                )
                Screen.Story -> {
                    val story = news?.stories?.find { it.id == storyId } ?: state.results?.find { it.id == storyId }
                    if (story == null) LaunchedEffect(Unit) { screen = storyFrom }
                    else StoryScreen(story, onBack = { screen = storyFrom })
                }
                Screen.Report -> ReportScreen(
                    report = news?.report,
                    listState = reportList,
                    barState = reportBar,
                    onOpen = { storyId = it; storyFrom = Screen.Report; screen = Screen.Story },
                    bottomBar = navBar,
                )
                Screen.Search -> SearchScreen(
                    query = query,
                    onQuery = { query = it },
                    onSearch = { scope.launch { state.search(query) } },
                    results = state.results,
                    searching = state.searching,
                    error = state.searchError,
                    listState = searchList,
                    onOpen = { storyId = it.id; storyFrom = Screen.Search; screen = Screen.Story },
                    bottomBar = navBar,
                )
                Screen.Feeds -> FeedsScreen(news?.feeds.orEmpty(), onBack = { screen = Screen.Stories })
                Screen.Server -> ServerScreen(
                    current = state.server,
                    onBack = if (state.server.isBlank()) null else back,
                    onSave = {
                        state.changeServer(it)
                        screen = Screen.Stories
                        scope.launch { state.refresh() }
                    },
                )
                Screen.Settings -> SettingsScreen(
                    server = state.server,
                    settings = settings,
                    error = state.settingsError,
                    onBack = back,
                    onRetry = { scope.launch { state.loadSettings() } },
                    onServer = { serverFrom = Screen.Settings; screen = Screen.Server },
                    onInterests = { screen = Screen.Interests },
                    onSources = { screen = Screen.Sources },
                    onPrompts = { screen = Screen.Prompts },
                    save = save,
                )
                Screen.Interests -> settings?.let { InterestsScreen(it.places, it.interests, onBack = back, save = save) }
                Screen.Sources -> settings?.let { SourcesScreen(it, onBack = back, save = save) }
                Screen.Prompts -> settings?.let {
                    PromptsScreen(it.prompts, onBack = back, onOpen = { kind -> promptKind = kind; screen = Screen.Prompt })
                }
                Screen.Prompt -> settings?.prompts?.find { it.kind == promptKind }?.let { PromptScreen(it, onBack = back, save = save) }
            }
        }
    }
}
