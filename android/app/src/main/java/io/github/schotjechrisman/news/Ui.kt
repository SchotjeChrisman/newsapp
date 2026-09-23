@file:OptIn(ExperimentalMaterial3Api::class, ExperimentalLayoutApi::class, ExperimentalFoundationApi::class)

package io.github.schotjechrisman.news

import android.os.Build
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.isSystemInDarkTheme
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
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
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
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.MediumTopAppBar
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
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
import androidx.compose.ui.platform.LocalSoftwareKeyboardController
import androidx.compose.ui.platform.LocalUriHandler
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

@Composable
fun NewsTheme(content: @Composable () -> Unit) {
    val dark = isSystemInDarkTheme()
    val context = LocalContext.current
    val colors = when {
        Build.VERSION.SDK_INT >= 31 -> if (dark) dynamicDarkColorScheme(context) else dynamicLightColorScheme(context)
        dark -> darkColorScheme(primary = Color(0xFF9DBBFF))
        else -> lightColorScheme(primary = Color(0xFF1D5BBF))
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
@Composable
fun NavBar(current: Screen, onSelect: (Screen) -> Unit) {
    NavigationBar {
        listOf(
            Triple(Screen.Stories, "News", R.drawable.ic_notification),
            Triple(Screen.Report, "Report", R.drawable.ic_report),
            Triple(Screen.Search, "Search", R.drawable.ic_search),
        ).forEach { (screen, label, icon) ->
            NavigationBarItem(
                selected = current == screen,
                onClick = { onSelect(screen) },
                icon = { Icon(painterResource(icon), contentDescription = null) },
                label = { Text(label) },
            )
        }
    }
}

/** A big title that shrinks as the page scrolls, on the page's own color. */
@Composable
private fun TitleBar(title: String, scroll: TopAppBarScrollBehavior, actions: @Composable () -> Unit = {}) {
    val ground = MaterialTheme.colorScheme.surface
    MediumTopAppBar(
        title = { Text(title) },
        actions = { actions() },
        scrollBehavior = scroll,
        colors = TopAppBarDefaults.topAppBarColors(containerColor = ground, scrolledContainerColor = ground),
    )
}

@Composable
fun StoriesScreen(
    news: News?,
    loading: Boolean,
    error: String?,
    tab: String,
    listState: LazyListState,
    onTab: (String) -> Unit,
    onRefresh: () -> Unit,
    onOpen: (Story) -> Unit,
    onFeeds: () -> Unit,
    onServer: () -> Unit,
    bottomBar: @Composable () -> Unit,
) {
    var menu by remember { mutableStateOf(false) }
    val scroll = TopAppBarDefaults.exitUntilCollapsedScrollBehavior()
    Scaffold(
        modifier = Modifier.nestedScroll(scroll.nestedScrollConnection),
        topBar = {
            TitleBar("News", scroll) {
                IconButton(onClick = { menu = true }) { Icon(painterResource(R.drawable.ic_more), "More") }
                DropdownMenu(expanded = menu, onDismissRequest = { menu = false }) {
                    DropdownMenuItem(text = { Text("Feed status") }, onClick = { menu = false; onFeeds() })
                    DropdownMenuItem(text = { Text("Server") }, onClick = { menu = false; onServer() })
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
            val shown = remember(news, tab) { news.stories.filter { tab == "All" || tab in it.tabs } }
            val firstSingle = shown.indexOfFirst { it.sources < 2 }
            LazyColumn(state = listState, contentPadding = PaddingValues(bottom = 16.dp), modifier = Modifier.fillMaxSize()) {
                stickyHeader { TabChips(tabs, tab, onTab) }
                if (error != null) item { Banner(error, Modifier.padding(16.dp, 4.dp)) }
                itemsIndexed(shown, key = { _, s -> s.id }) { i, story ->
                    if (i == firstSingle) SectionLabel("Reported by one source", Modifier.padding(start = 20.dp, top = 20.dp, bottom = 6.dp))
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
        LazyColumn(Modifier.fillMaxSize().padding(padding), contentPadding = PaddingValues(bottom = 16.dp)) {
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
fun ReportScreen(report: Report?, onOpen: (Long) -> Unit, bottomBar: @Composable () -> Unit) {
    val scroll = TopAppBarDefaults.exitUntilCollapsedScrollBehavior()
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
        LazyColumn(Modifier.fillMaxSize().padding(padding), contentPadding = PaddingValues(16.dp, 0.dp, 16.dp, 16.dp)) {
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
