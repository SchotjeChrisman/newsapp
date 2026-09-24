@file:OptIn(ExperimentalMaterial3Api::class, ExperimentalLayoutApi::class, ExperimentalFoundationApi::class)

package io.github.schotjechrisman.news

import android.os.Build
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.WindowInsetsSides
import androidx.compose.foundation.layout.consumeWindowInsets
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.only
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawing
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.windowInsetsPadding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyListState
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.Checkbox
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ExtendedFloatingActionButton
import androidx.compose.material3.FilterChip
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.MediumTopAppBar
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.NavigationRail
import androidx.compose.material3.NavigationRailItem
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Shapes
import androidx.compose.material3.SuggestionChip
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TextField
import androidx.compose.material3.TextFieldDefaults
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.material3.TopAppBarScrollBehavior
import androidx.compose.material3.TopAppBarState
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.dynamicDarkColorScheme
import androidx.compose.material3.dynamicLightColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.luminance
import androidx.compose.ui.input.nestedscroll.nestedScroll
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.LocalSoftwareKeyboardController
import androidx.compose.ui.platform.LocalUriHandler
import androidx.compose.ui.platform.LocalWindowInfo
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import java.time.Duration
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.util.Locale
import kotlinx.coroutines.launch
import org.json.JSONObject

@Composable
fun NewsTheme(content: @Composable () -> Unit) {
    val dark = isSystemInDarkTheme()
    val context = LocalContext.current
    val colors = when {
        Build.VERSION.SDK_INT >= 31 -> if (dark) dynamicDarkColorScheme(context) else dynamicLightColorScheme(context)
        // Before Android 12 there are no wallpaper colors: a full blue scheme, so no slot falls back to Material's purple.
        dark -> darkColorScheme(
            primary = Color(0xFFAAC7FF), onPrimary = Color(0xFF0A305F),
            primaryContainer = Color(0xFF284777), onPrimaryContainer = Color(0xFFD6E3FF),
            secondary = Color(0xFFBEC6DC), onSecondary = Color(0xFF283141),
            secondaryContainer = Color(0xFF3E4759), onSecondaryContainer = Color(0xFFDAE2F9),
            tertiary = Color(0xFFDDBCE0), tertiaryContainer = Color(0xFF573E5C), onTertiaryContainer = Color(0xFFFAD8FD),
            background = Color(0xFF111318), onBackground = Color(0xFFE2E2E9),
            surface = Color(0xFF111318), onSurface = Color(0xFFE2E2E9),
            surfaceVariant = Color(0xFF44474E), onSurfaceVariant = Color(0xFFC4C6D0),
            outline = Color(0xFF8E9099), outlineVariant = Color(0xFF44474E),
            surfaceContainerLowest = Color(0xFF0C0E13), surfaceContainerLow = Color(0xFF191C20),
            surfaceContainer = Color(0xFF1D2024), surfaceContainerHigh = Color(0xFF282A2F),
            surfaceContainerHighest = Color(0xFF33353A),
        )
        else -> lightColorScheme(
            primary = Color(0xFF415F91), onPrimary = Color.White,
            primaryContainer = Color(0xFFD6E3FF), onPrimaryContainer = Color(0xFF284777),
            secondary = Color(0xFF565F71), onSecondary = Color.White,
            secondaryContainer = Color(0xFFDAE2F9), onSecondaryContainer = Color(0xFF3E4759),
            tertiary = Color(0xFF705575), tertiaryContainer = Color(0xFFFAD8FD), onTertiaryContainer = Color(0xFF573E5C),
            background = Color(0xFFF9F9FF), onBackground = Color(0xFF191C20),
            surface = Color(0xFFF9F9FF), onSurface = Color(0xFF191C20),
            surfaceVariant = Color(0xFFE0E2EC), onSurfaceVariant = Color(0xFF44474E),
            outline = Color(0xFF74777F), outlineVariant = Color(0xFFC4C6D0),
            surfaceContainerLowest = Color.White, surfaceContainerLow = Color(0xFFF3F3FA),
            surfaceContainer = Color(0xFFEDEDF4), surfaceContainerHigh = Color(0xFFE7E8EE),
            surfaceContainerHighest = Color(0xFFE2E2E9),
        )
    }
    val type = Typography()
    MaterialTheme(
        colorScheme = colors,
        shapes = Shapes(medium = RoundedCornerShape(16.dp), large = RoundedCornerShape(24.dp)),
        typography = type.copy(titleMedium = type.titleMedium.copy(fontSize = 17.sp, lineHeight = 23.sp, fontWeight = FontWeight.SemiBold)),
        content = content,
    )
}

/** Left, center and right, in the colors Ground News uses, readable on both grounds. */
@Composable
private fun leanColors(): List<Color> =
    if (MaterialTheme.colorScheme.surface.luminance() < 0.5f) {
        listOf(Color(0xFF6F9EF5), Color(0xFF8A929C), Color(0xFFF07167))
    } else {
        listOf(Color(0xFF2F6FDB), Color(0xFFA3ABB5), Color(0xFFD6453D))
    }

private val clockFormat = DateTimeFormatter.ofPattern("EEE HH:mm", Locale.ENGLISH)
private val longDay = DateTimeFormatter.ofPattern("EEEE d MMMM", Locale.ENGLISH)
private val weekday = DateTimeFormatter.ofPattern("EEE", Locale.ENGLISH)
private val dateFormat = DateTimeFormatter.ofPattern("d MMM yyyy", Locale.ENGLISH)

fun clock(t: Instant): String = clockFormat.format(t.atZone(ZoneId.systemDefault()))

fun ago(t: Instant): String {
    val minutes = Duration.between(t, Instant.now()).toMinutes().coerceAtLeast(0)
    return when {
        minutes < 60 -> "$minutes min ago"
        minutes < 48 * 60 -> "${minutes / 60} h ago"
        minutes < 7 * 24 * 60 -> "${minutes / (24 * 60)} d ago"
        else -> dateFormat.format(t.atZone(ZoneId.systemDefault()))
    }
}

fun plural(n: Int, word: String, words: String = "${word}s") = if (n == 1) "1 $word" else "$n $words"

@Composable
private fun rememberOpener(): (String) -> Unit {
    val uris = LocalUriHandler.current
    return remember(uris) { { url -> runCatching { uris.openUri(url) } } }
}

@Composable
private fun BackButton(onBack: () -> Unit) {
    IconButton(onClick = onBack) { Icon(painterResource(R.drawable.ic_back), contentDescription = "Back") }
}

/** The app's three main places; a story, the feed status and the server setting open on top of them. */
private val places = listOf(
    Triple(Screen.Stories, "News", R.drawable.ic_notification),
    Triple(Screen.Report, "Report", R.drawable.ic_report),
    Triple(Screen.Search, "Search", R.drawable.ic_search),
)

/** A bar at the bottom on a phone held upright. */
@Composable
fun NavBar(current: Screen, onSelect: (Screen) -> Unit) {
    NavigationBar {
        places.forEach { (screen, label, icon) ->
            NavigationBarItem(
                selected = current == screen,
                onClick = { onSelect(screen) },
                icon = { Icon(painterResource(icon), contentDescription = null) },
                label = { Text(label) },
            )
        }
    }
}

/** A rail at the side on wide screens (a phone on its side, a tablet), which leaves the height to the news. */
@Composable
fun NavRail(current: Screen, onSelect: (Screen) -> Unit) {
    NavigationRail {
        Spacer(Modifier.weight(1f))
        places.forEach { (screen, label, icon) ->
            NavigationRailItem(
                selected = current == screen,
                onClick = { onSelect(screen) },
                icon = { Icon(painterResource(icon), contentDescription = null) },
                label = { Text(label) },
            )
        }
        Spacer(Modifier.weight(1f))
    }
}

/** Wide enough for the rail instead of the bottom bar. */
@Composable
fun wide() = with(LocalDensity.current) { LocalWindowInfo.current.containerSize.width.toDp() } >= 600.dp

/** Too short for a big title: a phone on its side. */
@Composable
private fun short() = with(LocalDensity.current) { LocalWindowInfo.current.containerSize.height.toDp() } < 480.dp

/** The title's scrolling: it shrinks as the page scrolls, except on a short screen, where the one-row bar stays put so
 *  its menu stays in reach. Turning the phone starts with the title open. */
@Composable
private fun titleScroll(state: TopAppBarState): TopAppBarScrollBehavior {
    val short = short()
    LaunchedEffect(short) { state.heightOffset = 0f }
    return if (short) TopAppBarDefaults.pinnedScrollBehavior(state) else TopAppBarDefaults.exitUntilCollapsedScrollBehavior(state)
}

/** A big title that shrinks as the page scrolls, on the page's own color; a small one when the screen is short. */
@Composable
private fun TitleBar(title: String, scroll: TopAppBarScrollBehavior, actions: @Composable () -> Unit = {}) {
    val ground = MaterialTheme.colorScheme.surface
    val colors = TopAppBarDefaults.topAppBarColors(containerColor = ground, scrolledContainerColor = ground)
    if (short()) {
        TopAppBar(title = { Text(title) }, actions = { actions() }, scrollBehavior = scroll, colors = colors)
    } else {
        MediumTopAppBar(title = { Text(title) }, actions = { actions() }, scrollBehavior = scroll, colors = colors)
    }
}

@Composable
fun StoriesScreen(
    news: News?,
    loading: Boolean,
    error: String?,
    tab: String,
    listState: LazyListState,
    barState: TopAppBarState,
    onTab: (String) -> Unit,
    onRefresh: () -> Unit,
    onOpen: (Story) -> Unit,
    onFeeds: () -> Unit,
    onSettings: () -> Unit,
    onServer: () -> Unit,
    bottomBar: @Composable () -> Unit,
) {
    var menu by remember { mutableStateOf(false) }
    val scroll = titleScroll(barState)
    Scaffold(
        topBar = {
            TitleBar("News", scroll) {
                IconButton(onClick = { menu = true }) { Icon(painterResource(R.drawable.ic_more), "More") }
                DropdownMenu(expanded = menu, onDismissRequest = { menu = false }) {
                    DropdownMenuItem(text = { Text("Feed status") }, onClick = { menu = false; onFeeds() })
                    DropdownMenuItem(text = { Text("Settings") }, onClick = { menu = false; onSettings() })
                }
            }
        },
        bottomBar = bottomBar,
        contentWindowInsets = WindowInsets.safeDrawing,
    ) { padding ->
        PullToRefreshBox(isRefreshing = loading, onRefresh = onRefresh, modifier = Modifier.fillMaxSize().padding(padding)) {
            if (news == null) {
                if (!loading) Empty(error ?: "No news yet.", onRefresh, onServer)
                return@PullToRefreshBox
            }
            val tabs = listOf("All") + news.tabs
            // The All list comes in the server's order, which lifts every subject's notable stories; a tab's own list
            // lifts only its own, the same way.
            val shown = remember(news, tab) {
                val top = news.stories.maxOfOrNull { it.coverage }?.takeIf { it > 0 } ?: 1.0
                if (tab == "All") news.stories
                else news.stories.filter { tab in it.tabs }
                    .sortedWith(compareByDescending<Story> { maxOf(it.coverage / top, it.lift[tab] ?: 0.0) }.thenByDescending { it.coverage })
            }
            // The title is closer to the list than pull-to-refresh: a pull first opens the title, then refreshes.
            LazyColumn(
                state = listState,
                contentPadding = PaddingValues(bottom = 16.dp),
                modifier = Modifier.fillMaxSize().nestedScroll(scroll.nestedScrollConnection),
            ) {
                stickyHeader { TabChips(tabs, tab, onTab) }
                if (error != null) item { Banner(error, Modifier.padding(16.dp, 4.dp)) }
                items(shown, key = { it.id }) { story ->
                    StoryCard(story, Modifier.padding(16.dp, 5.dp)) { onOpen(story) }
                }
                item {
                    Text(
                        if (shown.isEmpty()) "No stories here in the last two days." else "Updated ${clock(news.built)}",
                        style = MaterialTheme.typography.labelMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.padding(20.dp, 16.dp),
                    )
                }
            }
        }
    }
}

@Composable
private fun TabChips(tabs: List<String>, tab: String, onTab: (String) -> Unit) {
    LazyRow(
        modifier = Modifier.fillMaxWidth().background(MaterialTheme.colorScheme.surface),
        contentPadding = PaddingValues(16.dp, 4.dp, 16.dp, 8.dp),
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        items(tabs) { t -> FilterChip(selected = t == tab, onClick = { onTab(t) }, label = { Text(t) }) }
    }
}

@Composable
fun StoryCard(story: Story, modifier: Modifier = Modifier, onOpen: () -> Unit) {
    Card(
        onClick = onOpen,
        shape = MaterialTheme.shapes.large,
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceContainer),
        modifier = modifier.fillMaxWidth(),
    ) {
        Column(Modifier.padding(18.dp, 16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text(
                (story.tabs.take(2) + ago(story.updated)).joinToString(" · "),
                style = MaterialTheme.typography.labelMedium,
                color = MaterialTheme.colorScheme.primary,
            )
            Text(story.headline, style = MaterialTheme.typography.titleMedium)
            story.summary?.let {
                Text(
                    it,
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    maxLines = 3,
                    overflow = TextOverflow.Ellipsis,
                )
            }
            Row(Modifier.padding(top = 2.dp), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                Text(plural(story.sources, "source"), style = MaterialTheme.typography.labelLarge)
                if (story.lean.rated > 0) CoverageBar(story.lean, Modifier.weight(1f)) else Spacer(Modifier.weight(1f))
                if (story.updates.isNotEmpty()) {
                    Text(plural(story.updates.size, "update"), style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
        }
    }
}

@Composable
fun CoverageBar(lean: Lean, modifier: Modifier = Modifier, height: Dp = 6.dp) {
    val colors = leanColors()
    Row(modifier.height(height).clip(CircleShape), horizontalArrangement = Arrangement.spacedBy(2.dp)) {
        listOf(lean.left, lean.center, lean.right).forEachIndexed { i, n ->
            if (n > 0) Box(Modifier.weight(n.toFloat()).fillMaxHeight().background(colors[i]))
        }
    }
}

/** One item of a group that reads as one rounded block: the first and last items get the big corners. */
@Composable
private fun Segment(index: Int, count: Int, onClick: (() -> Unit)? = null, content: @Composable ColumnScope.() -> Unit) {
    val big = 20.dp
    val small = 6.dp
    val shape = RoundedCornerShape(
        topStart = if (index == 0) big else small, topEnd = if (index == 0) big else small,
        bottomEnd = if (index == count - 1) big else small, bottomStart = if (index == count - 1) big else small,
    )
    Surface(shape = shape, color = MaterialTheme.colorScheme.surfaceContainer, modifier = Modifier.fillMaxWidth().padding(bottom = 3.dp)) {
        Column(
            Modifier.then(if (onClick != null) Modifier.clickable(onClick = onClick) else Modifier).padding(18.dp, 14.dp),
            verticalArrangement = Arrangement.spacedBy(4.dp),
            content = content,
        )
    }
}

@Composable
private fun SectionLabel(text: String, modifier: Modifier = Modifier) {
    Text(text, style = MaterialTheme.typography.titleSmall, color = MaterialTheme.colorScheme.primary, modifier = modifier)
}

@Composable
fun SearchScreen(
    query: String,
    onQuery: (String) -> Unit,
    onSearch: () -> Unit,
    results: List<Story>?,
    searching: Boolean,
    error: String?,
    listState: LazyListState,
    onOpen: (Story) -> Unit,
    bottomBar: @Composable () -> Unit,
) {
    val focus = remember { FocusRequester() }
    val keyboard = LocalSoftwareKeyboardController.current
    LaunchedEffect(Unit) { if (results == null) focus.requestFocus() }
    Scaffold(
        topBar = {
            Surface(
                shape = CircleShape,
                color = MaterialTheme.colorScheme.surfaceContainerHigh,
                modifier = Modifier
                    .windowInsetsPadding(WindowInsets.safeDrawing.only(WindowInsetsSides.Top + WindowInsetsSides.Horizontal))
                    .padding(16.dp, 8.dp)
                    .fillMaxWidth(),
            ) {
                Row(Modifier.padding(start = 16.dp, end = 4.dp), verticalAlignment = Alignment.CenterVertically) {
                    Icon(painterResource(R.drawable.ic_search), contentDescription = null, tint = MaterialTheme.colorScheme.onSurfaceVariant)
                    val clear = Color.Transparent
                    TextField(
                        value = query,
                        onValueChange = onQuery,
                        placeholder = { Text("Search all stories") },
                        singleLine = true,
                        keyboardOptions = KeyboardOptions(imeAction = ImeAction.Search),
                        keyboardActions = KeyboardActions(onSearch = { keyboard?.hide(); onSearch() }),
                        colors = TextFieldDefaults.colors(
                            focusedContainerColor = clear, unfocusedContainerColor = clear,
                            focusedIndicatorColor = clear, unfocusedIndicatorColor = clear,
                        ),
                        modifier = Modifier.weight(1f).focusRequester(focus),
                    )
                    if (query.isNotEmpty()) {
                        IconButton(onClick = { onQuery(""); focus.requestFocus() }) { Icon(painterResource(R.drawable.ic_close), "Clear") }
                    }
                }
            }
        },
        bottomBar = bottomBar,
        contentWindowInsets = WindowInsets.safeDrawing,
    ) { padding ->
        // The keyboard covers the bottom bar, so the list keeps clear of the keyboard itself.
        LazyColumn(
            Modifier.fillMaxSize().padding(padding).consumeWindowInsets(padding).imePadding(),
            state = listState,
            contentPadding = PaddingValues(bottom = 16.dp),
        ) {
            if (searching) item { LinearProgressIndicator(Modifier.fillMaxWidth().padding(16.dp, 4.dp)) }
            if (error != null) item { Banner(error, Modifier.padding(16.dp, 4.dp)) }
            if (results.isNullOrEmpty()) {
                item {
                    Text(
                        if (results == null) "Searches every story since the server started: headlines, summaries, updates and " +
                            "article titles, Dutch ones too." else if (searching) "" else "No stories found.",
                        style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.padding(20.dp, 12.dp),
                    )
                }
            } else {
                items(results, key = { it.id }) { story -> StoryCard(story, Modifier.padding(16.dp, 5.dp)) { onOpen(story) } }
            }
        }
    }
}

@Composable
fun ReportScreen(
    report: Report?,
    listState: LazyListState,
    barState: TopAppBarState,
    onOpen: (Long) -> Unit,
    bottomBar: @Composable () -> Unit,
) {
    val scroll = titleScroll(barState)
    Scaffold(
        modifier = Modifier.nestedScroll(scroll.nestedScrollConnection),
        topBar = { TitleBar("Morning report", scroll) },
        bottomBar = bottomBar,
        contentWindowInsets = WindowInsets.safeDrawing,
    ) { padding ->
        if (report == null) {
            Text(
                "No report yet. The first one comes at 05:30.",
                style = MaterialTheme.typography.bodyLarge,
                modifier = Modifier.padding(padding).padding(20.dp),
            )
            return@Scaffold
        }
        LazyColumn(Modifier.fillMaxSize().padding(padding), state = listState, contentPadding = PaddingValues(16.dp, 0.dp, 16.dp, 16.dp)) {
            item {
                Text(
                    longDay.format(report.day),
                    style = MaterialTheme.typography.titleMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(start = 4.dp, bottom = 12.dp),
                )
            }
            report.market?.let { item { MarketCard(it) } }
            report.sections.forEach { section ->
                item(key = section.title) { SectionLabel(section.title, Modifier.padding(start = 4.dp, top = 20.dp, bottom = 8.dp)) }
                itemsIndexed(section.stories, key = { _, s -> s.id }) { i, story ->
                    Segment(i, section.stories.size, onClick = { onOpen(story.id) }) {
                        Text(story.headline, style = MaterialTheme.typography.titleMedium)
                        Text(story.gist, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                }
            }
        }
    }
}

@Composable
private fun MarketCard(market: Market) {
    val dark = MaterialTheme.colorScheme.surface.luminance() < 0.5f
    val (container, content) = when {
        market.change > 0 -> if (dark) Color(0xFF1B3D26) to Color(0xFF9CE0AE) else Color(0xFFD5F2DC) to Color(0xFF14592A)
        market.change < 0 -> if (dark) Color(0xFF4A1F1C) to Color(0xFFF5B7B1) else Color(0xFFFBDAD6) to Color(0xFF8C1D18)
        else -> MaterialTheme.colorScheme.surfaceContainerHighest to MaterialTheme.colorScheme.onSurface
    }
    Card(
        shape = MaterialTheme.shapes.large,
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceContainer),
        modifier = Modifier.fillMaxWidth(),
    ) {
        Row(Modifier.padding(18.dp, 16.dp), verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text(
                    "${market.name} · ${weekday.format(market.date)} close",
                    style = MaterialTheme.typography.labelLarge,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                Text("%,.2f".format(Locale.ENGLISH, market.close), style = MaterialTheme.typography.headlineSmall)
            }
            Surface(shape = CircleShape, color = container, contentColor = content) {
                Text(
                    marketLine(market).removePrefix(market.name).trim(),
                    style = MaterialTheme.typography.titleSmall,
                    modifier = Modifier.padding(14.dp, 8.dp),
                )
            }
        }
    }
}

@Composable
private fun Empty(message: String, onRetry: () -> Unit, onServer: () -> Unit) {
    Column(
        Modifier.fillMaxSize().padding(24.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp, Alignment.CenterVertically),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Text(message, style = MaterialTheme.typography.bodyLarge)
        Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            Button(onClick = onRetry) { Text("Try again") }
            OutlinedButton(onClick = onServer) { Text("Server") }
        }
    }
}

@Composable
private fun Banner(message: String, modifier: Modifier = Modifier) {
    Surface(
        shape = MaterialTheme.shapes.medium,
        color = MaterialTheme.colorScheme.errorContainer,
        contentColor = MaterialTheme.colorScheme.onErrorContainer,
        modifier = modifier.fillMaxWidth(),
    ) {
        Text(message, style = MaterialTheme.typography.bodyMedium, modifier = Modifier.padding(16.dp, 12.dp))
    }
}

@Composable
fun StoryScreen(story: Story, onBack: () -> Unit) {
    val open = rememberOpener()
    Scaffold(
        topBar = { TopAppBar(title = {}, navigationIcon = { BackButton(onBack) }) },
        contentWindowInsets = WindowInsets.safeDrawing,
    ) { padding ->
        LazyColumn(Modifier.fillMaxSize().padding(padding), contentPadding = PaddingValues(16.dp, 0.dp, 16.dp, 24.dp)) {
            item {
                Column(Modifier.padding(start = 4.dp, end = 4.dp, bottom = 16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Text(
                        (story.tabs + "updated ${clock(story.updated)}").joinToString(" · "),
                        style = MaterialTheme.typography.labelLarge,
                        color = MaterialTheme.colorScheme.primary,
                    )
                    Text(story.headline, style = MaterialTheme.typography.headlineSmall)
                }
            }
            item { CoverageCard(story) }
            story.summary?.let { summary ->
                item {
                    Column(Modifier.padding(4.dp, 20.dp, 4.dp, 0.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
                        Text(summary, style = MaterialTheme.typography.bodyLarge)
                        Sources(story.summaryFrom, open)
                    }
                }
            }
            if (story.updates.isNotEmpty()) {
                item { SectionLabel("Updates", Modifier.padding(start = 4.dp, top = 24.dp, bottom = 8.dp)) }
                itemsIndexed(story.updates) { i, update ->
                    Segment(i, story.updates.size) {
                        Text(clock(update.at), style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.primary)
                        Text(update.text, style = MaterialTheme.typography.bodyMedium)
                        Sources(update.from, open)
                    }
                }
            }
            if (story.quotes.isNotEmpty()) {
                item { SectionLabel("Opinion", Modifier.padding(start = 4.dp, top = 24.dp, bottom = 8.dp)) }
                items(story.quotes) { quote -> QuoteCard(quote) { open(quote.url) } }
            }
            item {
                SectionLabel("Coverage · ${plural(story.articles.size, "article")}", Modifier.padding(start = 4.dp, top = 24.dp, bottom = 8.dp))
            }
            itemsIndexed(story.articles) { i, article -> ArticleSegment(article, i, story.articles.size) { open(article.url) } }
        }
    }
}

@Composable
private fun CoverageCard(story: Story) {
    val lean = story.lean
    val colors = leanColors()
    Card(
        shape = MaterialTheme.shapes.large,
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceContainer),
        modifier = Modifier.fillMaxWidth(),
    ) {
        Column(Modifier.padding(18.dp, 16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Row(verticalAlignment = Alignment.Bottom, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Text("${story.sources}", style = MaterialTheme.typography.headlineMedium, color = MaterialTheme.colorScheme.primary)
                Text(
                    if (story.sources == 1) "source" else "independent sources",
                    style = MaterialTheme.typography.bodyLarge,
                    modifier = Modifier.padding(bottom = 4.dp),
                )
            }
            if (lean.rated > 0) {
                CoverageBar(lean, Modifier.fillMaxWidth(), height = 10.dp)
                FlowRow(horizontalArrangement = Arrangement.spacedBy(16.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                    listOf("left" to lean.left, "center" to lean.center, "right" to lean.right).forEachIndexed { i, (name, n) ->
                        if (n > 0) Legend(colors[i], "$n $name")
                    }
                    (story.sources - lean.rated).takeIf { it > 0 }?.let { Legend(MaterialTheme.colorScheme.outlineVariant, "$it not rated") }
                }
            }
        }
    }
}

@Composable
private fun Legend(color: Color, text: String) {
    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
        Box(Modifier.size(8.dp).background(color, CircleShape))
        Text(text, style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

@Composable
private fun QuoteCard(quote: Quote, onSource: () -> Unit) {
    Card(
        shape = MaterialTheme.shapes.large,
        colors = CardDefaults.cardColors(
            containerColor = MaterialTheme.colorScheme.secondaryContainer,
            contentColor = MaterialTheme.colorScheme.onSecondaryContainer,
        ),
        modifier = Modifier.fillMaxWidth().padding(bottom = 8.dp),
    ) {
        Column(Modifier.padding(18.dp, 14.dp, 8.dp, 4.dp)) {
            Text(
                "“${quote.text}”",
                style = MaterialTheme.typography.bodyLarge.copy(fontFamily = FontFamily.Serif, fontStyle = FontStyle.Italic),
                modifier = Modifier.padding(end = 10.dp),
            )
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(
                    quote.outlet + if (quote.translated) ", translated" else "",
                    style = MaterialTheme.typography.labelLarge,
                    modifier = Modifier.weight(1f),
                )
                TextButton(onClick = onSource) { Text("Source") }
            }
        }
    }
}

@Composable
private fun Sources(refs: List<Ref>, open: (String) -> Unit) {
    if (refs.isEmpty()) return
    var all by rememberSaveable { mutableStateOf(false) }
    val most = 6
    FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        (if (all) refs else refs.take(most)).forEach { ref ->
            SuggestionChip(onClick = { open(ref.url) }, label = { Text(ref.outlet) })
        }
        if (!all && refs.size > most) SuggestionChip(onClick = { all = true }, label = { Text("${refs.size - most} more") })
    }
}

@Composable
private fun ArticleSegment(article: Article, index: Int, count: Int, onOpen: () -> Unit) {
    val colors = leanColors()
    val dot = when (article.lean) {
        "left" -> colors[0]
        "center" -> colors[1]
        "right" -> colors[2]
        else -> MaterialTheme.colorScheme.outlineVariant
    }
    Segment(index, count, onClick = onOpen) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Row(Modifier.weight(1f), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Box(Modifier.size(8.dp).background(dot, CircleShape))
                Text(
                    article.outlet,
                    style = MaterialTheme.typography.labelLarge,
                    color = MaterialTheme.colorScheme.primary,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                    modifier = Modifier.weight(1f, fill = false),
                )
                if (article.opinion) {
                    Surface(shape = CircleShape, color = MaterialTheme.colorScheme.tertiaryContainer, contentColor = MaterialTheme.colorScheme.onTertiaryContainer) {
                        Text("opinion", style = MaterialTheme.typography.labelSmall, modifier = Modifier.padding(8.dp, 2.dp))
                    }
                }
            }
            Text(clock(article.published), style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        Text(article.title, style = MaterialTheme.typography.bodyMedium)
    }
}

@Composable
fun FeedsScreen(feeds: List<Feed>, onBack: () -> Unit) {
    val open = rememberOpener()
    Scaffold(
        topBar = { TopAppBar(title = { Text("Feed status") }, navigationIcon = { BackButton(onBack) }) },
        contentWindowInsets = WindowInsets.safeDrawing,
    ) { padding ->
        LazyColumn(Modifier.fillMaxSize().padding(padding), contentPadding = PaddingValues(16.dp, 0.dp, 16.dp, 16.dp)) {
            item {
                Text(
                    "${feeds.count { it.error == null }} of ${feeds.size} feeds working at the last check",
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(4.dp, 4.dp, 4.dp, 12.dp),
                )
            }
            itemsIndexed(feeds) { i, feed ->
                Segment(i, feeds.size, onClick = { open(feed.url) }) {
                    Text(feed.name, style = MaterialTheme.typography.bodyLarge)
                    Text(
                        feed.error ?: plural(feed.items, "item"),
                        style = MaterialTheme.typography.bodySmall,
                        color = if (feed.error != null) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
        }
    }
}

/** onBack is null on first start, when there's no server to go back to. */
@Composable
fun ServerScreen(current: String, onBack: (() -> Unit)?, onSave: (String) -> Unit) {
    var address by rememberSaveable { mutableStateOf(current) }
    val context = LocalContext.current
    val version = remember { runCatching { context.packageManager.getPackageInfo(context.packageName, 0).versionName }.getOrNull() }
    Scaffold(
        topBar = { TopAppBar(title = { Text("Server") }, navigationIcon = { onBack?.let { BackButton(it) } }) },
        contentWindowInsets = WindowInsets.safeDrawing,
    ) { padding ->
        Column(Modifier.padding(padding).padding(20.dp, 8.dp), verticalArrangement = Arrangement.spacedBy(16.dp)) {
            Text("The address of your news server on your tailnet, with its port.", style = MaterialTheme.typography.bodyMedium)
            OutlinedTextField(
                value = address,
                onValueChange = { address = it },
                label = { Text("Server address") },
                placeholder = { Text("http://newsbox:8080") },
                singleLine = true,
                shape = MaterialTheme.shapes.medium,
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri, imeAction = ImeAction.Done),
                keyboardActions = KeyboardActions(onDone = { if (address.isNotBlank()) onSave(address) }),
                modifier = Modifier.fillMaxWidth(),
            )
            Button(onClick = { onSave(address) }, enabled = address.isNotBlank()) { Text("Save") }
            version?.let {
                Text("Version $it", style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
    }
}

private fun dollars(amount: Double) = String.format(Locale.ENGLISH, "$%.2f", amount)

/** An amount typed in a field: "0,60" works too. Null when it isn't one. */
private fun String.amount() = trim().removePrefix("$").replace(',', '.').toDoubleOrNull()?.takeIf { it >= 0 && it.isFinite() }

@Composable
private fun Hint(text: String, modifier: Modifier = Modifier) {
    Text(text, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = modifier)
}

@Composable
private fun Row2(title: String, detail: String, index: Int, count: Int, onClick: () -> Unit) {
    Segment(index, count, onClick = onClick) {
        Text(title, style = MaterialTheme.typography.bodyLarge)
        Text(detail, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 2, overflow = TextOverflow.Ellipsis)
    }
}

@Composable
fun SettingsScreen(
    server: String,
    settings: Settings?,
    error: String?,
    onBack: () -> Unit,
    onRetry: () -> Unit,
    onServer: () -> Unit,
    onInterests: () -> Unit,
    onSources: () -> Unit,
    onPrompts: () -> Unit,
    save: suspend (JSONObject) -> String?,
) {
    var caps by rememberSaveable { mutableStateOf(false) }
    Scaffold(
        topBar = { TopAppBar(title = { Text("Settings") }, navigationIcon = { BackButton(onBack) }) },
        contentWindowInsets = WindowInsets.safeDrawing,
    ) { padding ->
        LazyColumn(Modifier.fillMaxSize().padding(padding), contentPadding = PaddingValues(16.dp, 0.dp, 16.dp, 16.dp)) {
            item { Row2("Server", server, 0, 1, onServer) }
            item { SectionLabel("On the server", Modifier.padding(4.dp, 24.dp, 4.dp, 8.dp)) }
            if (error != null) {
                item {
                    Banner(error, Modifier.padding(bottom = 8.dp))
                    OutlinedButton(onClick = onRetry) { Text("Try again") }
                }
            }
            if (settings == null) {
                if (error == null) item { LinearProgressIndicator(Modifier.fillMaxWidth().padding(4.dp, 8.dp)) }
            } else {
                val changed = settings.prompts.count { it.text != it.default }
                val rows = listOf(
                    Triple("Interests", (settings.places.map { it.name } + settings.interests.map { it.name }).joinToString(", ").ifEmpty { "None" }, onInterests),
                    Triple("News sources", plural(settings.sources.size, "feed"), onSources),
                    Triple("Spending caps", "Claude ${dollars(settings.claudeCap)}, Mistral ${dollars(settings.mistralCap)} a day") { caps = true },
                    Triple("Prompts", if (changed == 0) "As they came" else "$changed changed", onPrompts),
                )
                itemsIndexed(rows) { i, (title, detail, open) -> Row2(title, detail, i, rows.size, open) }
            }
        }
    }
    if (caps && settings != null) CapsDialog(settings, onDismiss = { caps = false }, save = save)
}

/** A dialog that changes one item of a list and saves the whole list. It stays open with the reason when the server
 *  or the network turns the change down. */
@Composable
private fun EditDialog(
    title: String,
    canSave: Boolean,
    onDismiss: () -> Unit,
    onSave: suspend () -> String?,
    onDelete: (suspend () -> String?)? = null,
    content: @Composable ColumnScope.() -> Unit,
) {
    val scope = rememberCoroutineScope()
    var busy by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    fun run(action: suspend () -> String?) {
        busy = true
        error = null
        scope.launch {
            error = action()
            busy = false
            if (error == null) onDismiss()
        }
    }
    AlertDialog(
        onDismissRequest = { if (!busy) onDismiss() },
        title = { Text(title) },
        text = {
            Column(Modifier.verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(12.dp)) {
                content()
                error?.let { Text(it, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.error) }
            }
        },
        confirmButton = { TextButton(onClick = { run(onSave) }, enabled = canSave && !busy) { Text("Save") } },
        dismissButton = {
            Row {
                onDelete?.let { TextButton(onClick = { run(it) }, enabled = !busy) { Text("Delete") } }
                TextButton(onClick = onDismiss, enabled = !busy) { Text("Cancel") }
            }
        },
    )
}

@Composable
private fun CapsDialog(settings: Settings, onDismiss: () -> Unit, save: suspend (JSONObject) -> String?) {
    var claude by rememberSaveable { mutableStateOf(settings.claudeCap.toBigDecimal().stripTrailingZeros().toPlainString()) }
    var mistral by rememberSaveable { mutableStateOf(settings.mistralCap.toBigDecimal().stripTrailingZeros().toPlainString()) }
    EditDialog(
        title = "Spending caps",
        canSave = claude.amount() != null && mistral.amount() != null,
        onDismiss = onDismiss,
        onSave = { save(capsJson(claude.amount()!!, mistral.amount()!!)) },
    ) {
        Hint("The most each may spend in a day, in dollars. Past Claude's cap, Mistral writes; past both, new stories " +
            "wait until the next day to be written.")
        listOf(Triple("Claude (API-equivalent)", claude) { v: String -> claude = v }, Triple("Mistral", mistral) { v: String -> mistral = v })
            .forEach { (label, value, change) ->
                OutlinedTextField(
                    value = value,
                    onValueChange = change,
                    label = { Text(label) },
                    prefix = { Text("$") },
                    isError = value.amount() == null,
                    singleLine = true,
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal),
                    modifier = Modifier.fillMaxWidth(),
                )
            }
    }
}

@Composable
fun InterestsScreen(places: List<Place>, interests: List<Interest>, onBack: () -> Unit, save: suspend (JSONObject) -> String?) {
    // The one being changed: its place in its list, or -1 for a new one.
    var place by rememberSaveable { mutableStateOf<Int?>(null) }
    var interest by rememberSaveable { mutableStateOf<Int?>(null) }
    Scaffold(
        topBar = { TopAppBar(title = { Text("Interests") }, navigationIcon = { BackButton(onBack) }) },
        contentWindowInsets = WindowInsets.safeDrawing,
    ) { padding ->
        LazyColumn(Modifier.fillMaxSize().padding(padding), contentPadding = PaddingValues(16.dp, 0.dp, 16.dp, 16.dp)) {
            item {
                Hint(
                    "Each one is a tab and a part of the morning report. Claude files a new story under the most specific " +
                        "place it's about, and sorts stories into the subjects by their descriptions.",
                    Modifier.padding(4.dp, 4.dp, 4.dp, 4.dp),
                )
            }
            item { SectionLabel("Places", Modifier.padding(4.dp, 20.dp, 4.dp, 8.dp)) }
            itemsIndexed(places) { i, p -> Row2(p.name, p.about, i, places.size) { place = i } }
            item { OutlinedButton(onClick = { place = -1 }, modifier = Modifier.padding(top = 8.dp)) { Text("Add place") } }
            item { SectionLabel("Subjects", Modifier.padding(4.dp, 24.dp, 4.dp, 8.dp)) }
            itemsIndexed(interests) { i, it -> Row2(it.name, it.about, i, interests.size) { interest = i } }
            item { OutlinedButton(onClick = { interest = -1 }, modifier = Modifier.padding(top = 8.dp)) { Text("Add subject") } }
        }
    }
    place?.let { i ->
        val current = places.getOrNull(i)
        var name by rememberSaveable(i) { mutableStateOf(current?.name ?: "") }
        var about by rememberSaveable(i) { mutableStateOf(current?.about ?: "") }
        var major by rememberSaveable(i) { mutableStateOf(current?.major?.toString() ?: "3") }
        val sources = major.trim().toIntOrNull()?.takeIf { it in 1..1000 }
        EditDialog(
            title = if (current == null) "New place" else "Place",
            canSave = name.isNotBlank() && about.isNotBlank() && sources != null,
            onDismiss = { place = null },
            onSave = {
                val changed = Place(name.trim(), about.trim(), sources!!, current?.was)
                save(placesJson(if (current == null) places + changed else places.map { if (it == current) changed else it }))
            },
            onDelete = current?.let { { save(placesJson(places - it)) } },
        ) {
            OutlinedTextField(name, { name = it }, label = { Text("Name") }, singleLine = true, modifier = Modifier.fillMaxWidth())
            OutlinedTextField(about, { about = it }, label = { Text("What belongs in it") }, minLines = 2, modifier = Modifier.fillMaxWidth())
            OutlinedTextField(
                major, { major = it },
                label = { Text("Sources a story needs for the morning report") },
                isError = sources == null,
                singleLine = true,
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                modifier = Modifier.fillMaxWidth(),
            )
            Hint(
                if (current == null) "Stories written from now on can be filed here."
                else "Renaming keeps its feeds and stories. Deleting moves its feeds to Topics.",
            )
        }
    }
    interest?.let { i ->
        val current = interests.getOrNull(i)
        var name by rememberSaveable(i) { mutableStateOf(current?.name ?: "") }
        var about by rememberSaveable(i) { mutableStateOf(current?.about ?: "") }
        EditDialog(
            title = if (current == null) "New subject" else "Subject",
            canSave = name.isNotBlank() && about.isNotBlank(),
            onDismiss = { interest = null },
            onSave = {
                val changed = Interest(name.trim(), about.trim())
                save(interestsJson(if (current == null) interests + changed else interests.map { if (it == current) changed else it }))
            },
            onDelete = current?.let { { save(interestsJson(interests - it)) } },
        ) {
            OutlinedTextField(name, { name = it }, label = { Text("Name") }, singleLine = true, modifier = Modifier.fillMaxWidth())
            OutlinedTextField(about, { about = it }, label = { Text("What belongs in it") }, minLines = 3, modifier = Modifier.fillMaxWidth())
            Hint("After a change, the stories of the last two days are sorted again, which takes a few minutes.")
        }
    }
}

private val leanNames = listOf("" to "None", "left" to "Left", "center" to "Center", "right" to "Right")

@Composable
private fun Choices(label: String, options: List<Pair<String, String>>, selected: String, onSelect: (String) -> Unit) {
    Text(label, style = MaterialTheme.typography.labelLarge)
    FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        options.forEach { (value, text) -> FilterChip(selected = value == selected, onClick = { onSelect(value) }, label = { Text(text) }) }
    }
}

@Composable
fun SourcesScreen(settings: Settings, onBack: () -> Unit, save: suspend (JSONObject) -> String?) {
    val regions = settings.regions + "Topics"
    var region by rememberSaveable { mutableStateOf("All") }
    // The address of the feed being changed, or "" for a new one.
    var editing by rememberSaveable { mutableStateOf<String?>(null) }
    val shown = remember(settings, region) {
        settings.sources.filter { region == "All" || (it.region ?: "Topics") == region }.sortedBy { regions.indexOf(it.region ?: "Topics") }
    }
    Scaffold(
        topBar = { TopAppBar(title = { Text("News sources") }, navigationIcon = { BackButton(onBack) }) },
        floatingActionButton = { ExtendedFloatingActionButton(onClick = { editing = "" }) { Text("Add feed") } },
        contentWindowInsets = WindowInsets.safeDrawing,
    ) { padding ->
        LazyColumn(Modifier.fillMaxSize().padding(padding), contentPadding = PaddingValues(bottom = 88.dp)) {
            stickyHeader { TabChips(listOf("All") + regions, region) { region = it } }
            item {
                Hint(
                    "The server uses changes from its next collection, within half an hour. A lean belongs to the outlet, " +
                        "so all its feeds share it.",
                    Modifier.padding(20.dp, 4.dp, 20.dp, 12.dp),
                )
            }
            itemsIndexed(shown, key = { _, source -> source.url }) { i, source ->
                val detail = listOfNotNull(
                    (source.region ?: "Topics").takeIf { region == "All" },
                    if (source.lang == "nl") "Dutch" else "English",
                    source.lean,
                    "opinion only".takeIf { source.opinion },
                ).joinToString(" · ")
                Box(Modifier.padding(horizontal = 16.dp)) {
                    Segment(i, shown.size, onClick = { editing = source.url }) {
                        Text(source.name, style = MaterialTheme.typography.bodyLarge)
                        Text(detail, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        Text(source.url, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 1, overflow = TextOverflow.Ellipsis)
                    }
                }
            }
        }
    }
    editing?.let { url -> SourceDialog(settings, settings.sources.find { it.url == url }, region.takeIf { it in regions }, { editing = null }, save) }
}

@Composable
private fun SourceDialog(settings: Settings, source: Source?, region: String?, onDismiss: () -> Unit, save: suspend (JSONObject) -> String?) {
    // A new feed's language follows what most of its place's feeds are in.
    fun language(region: String) =
        settings.sources.filter { (it.region ?: "Topics") == region }.groupingBy { it.lang }.eachCount().maxByOrNull { it.value }?.key ?: "en"
    var name by rememberSaveable { mutableStateOf(source?.name ?: "") }
    var url by rememberSaveable { mutableStateOf(source?.url ?: "") }
    var where by rememberSaveable { mutableStateOf(source?.region ?: region ?: "Topics") }
    var lang by rememberSaveable { mutableStateOf(source?.lang ?: language(where)) }
    var opinion by rememberSaveable { mutableStateOf(source?.opinion ?: false) }
    // A new feed of an outlet the server already has takes that outlet's lean, unless it's picked here.
    var picked by rememberSaveable { mutableStateOf(source?.let { it.lean ?: "" }) }
    val lean = picked ?: settings.sources.find { it.name == name.trim() }?.lean ?: ""
    EditDialog(
        title = if (source == null) "New feed" else "Feed",
        canSave = name.isNotBlank() && url.isNotBlank(),
        onDismiss = onDismiss,
        onSave = {
            val feed = Source(where.takeIf { it != "Topics" }, name.trim(), url.trim(), lang, opinion, lean.ifEmpty { null })
            val all = if (source == null) settings.sources + feed else settings.sources.map { if (it.url == source.url) feed else it }
            save(sourcesJson(all.map { if (it.name == feed.name) it.copy(lean = feed.lean) else it }))
        },
        onDelete = source?.let { { save(sourcesJson(settings.sources.filter { s -> s.url != it.url })) } },
    ) {
        OutlinedTextField(name, { name = it }, label = { Text("Outlet") }, singleLine = true, modifier = Modifier.fillMaxWidth())
        OutlinedTextField(
            url, { url = it },
            label = { Text("Feed address") },
            singleLine = true,
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri),
            modifier = Modifier.fillMaxWidth(),
        )
        Choices("Region", (settings.regions + "Topics").map { it to it }, where) {
            where = it
            if (source == null) lang = language(it)
        }
        Choices("Language", listOf("nl" to "Dutch", "en" to "English"), lang) { lang = it }
        Choices("Lean", leanNames, lean) { picked = it }
        Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.clickable { opinion = !opinion }) {
            Checkbox(checked = opinion, onCheckedChange = { opinion = it })
            Text("Only opinion pieces", style = MaterialTheme.typography.bodyMedium)
        }
    }
}

@Composable
fun PromptsScreen(prompts: List<Prompt>, onBack: () -> Unit, onOpen: (String) -> Unit) {
    Scaffold(
        topBar = { TopAppBar(title = { Text("Prompts") }, navigationIcon = { BackButton(onBack) }) },
        contentWindowInsets = WindowInsets.safeDrawing,
    ) { padding ->
        LazyColumn(Modifier.fillMaxSize().padding(padding), contentPadding = PaddingValues(16.dp, 0.dp, 16.dp, 16.dp)) {
            item {
                Hint(
                    "What Claude and Mistral are told. The reply format is added by the server and can't change, so it can " +
                        "still read the answers.",
                    Modifier.padding(4.dp, 4.dp, 4.dp, 12.dp),
                )
            }
            itemsIndexed(prompts) { i, prompt ->
                Row2(prompt.title, if (prompt.text != prompt.default) "Changed" else "As it came", i, prompts.size) { onOpen(prompt.kind) }
            }
        }
    }
}

@Composable
fun PromptScreen(prompt: Prompt, onBack: () -> Unit, save: suspend (JSONObject) -> String?) {
    var text by rememberSaveable(prompt.kind) { mutableStateOf(prompt.text) }
    var busy by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    val scope = rememberCoroutineScope()
    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text(prompt.title) },
                navigationIcon = { BackButton(onBack) },
                actions = {
                    TextButton(onClick = { text = prompt.default }, enabled = text != prompt.default) { Text("Default") }
                    TextButton(
                        onClick = {
                            busy = true
                            error = null
                            scope.launch {
                                error = save(promptJson(prompt.kind, text))
                                busy = false
                                if (error == null) onBack()
                            }
                        },
                        enabled = !busy && text != prompt.text,
                    ) { Text("Save") }
                },
            )
        },
        contentWindowInsets = WindowInsets.safeDrawing,
    ) { padding ->
        Column(Modifier.fillMaxSize().padding(padding).padding(16.dp, 0.dp, 16.dp, 16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Hint(prompt.about)
            error?.let { Banner(it) }
            OutlinedTextField(text, { text = it }, textStyle = MaterialTheme.typography.bodyMedium, modifier = Modifier.fillMaxWidth().weight(1f))
        }
    }
}
