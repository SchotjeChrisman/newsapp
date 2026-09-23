@file:OptIn(ExperimentalMaterial3Api::class, ExperimentalLayoutApi::class)

package io.github.schotjechrisman.news

import android.os.Build
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.IntrinsicSize
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyListState
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.Button
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.PrimaryScrollableTabRow
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.SuggestionChip
import androidx.compose.material3.Tab
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.dynamicDarkColorScheme
import androidx.compose.material3.dynamicLightColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.luminance
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalUriHandler
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextOverflow
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
        typography = type.copy(
            headlineSmall = type.headlineSmall.copy(fontFamily = FontFamily.Serif, fontWeight = FontWeight.SemiBold),
            titleMedium = type.titleMedium.copy(fontFamily = FontFamily.Serif, fontWeight = FontWeight.SemiBold, fontSize = 18.sp, lineHeight = 24.sp),
        ),
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
private val shortDay = DateTimeFormatter.ofPattern("EEE d MMM", Locale.ENGLISH)
private val longDay = DateTimeFormatter.ofPattern("EEEE d MMMM", Locale.ENGLISH)
private val weekday = DateTimeFormatter.ofPattern("EEE", Locale.ENGLISH)

fun clock(t: Instant): String = clockFormat.format(t.atZone(ZoneId.systemDefault()))

fun ago(t: Instant): String {
    val minutes = Duration.between(t, Instant.now()).toMinutes().coerceAtLeast(0)
    return when {
        minutes < 60 -> "$minutes min ago"
        minutes < 48 * 60 -> "${minutes / 60} h ago"
        else -> "${minutes / (24 * 60)} d ago"
    }
}

private fun plural(n: Int, word: String, words: String = "${word}s") = if (n == 1) "1 $word" else "$n $words"

@Composable
private fun rememberOpener(): (String) -> Unit {
    val uris = LocalUriHandler.current
    return remember(uris) { { url -> runCatching { uris.openUri(url) } } }
}

@Composable
private fun BackButton(onBack: () -> Unit) {
    IconButton(onClick = onBack) { Icon(painterResource(R.drawable.ic_back), contentDescription = "Back") }
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
    onReport: () -> Unit,
    onFeeds: () -> Unit,
    onServer: () -> Unit,
) {
    var menu by remember { mutableStateOf(false) }
    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("News") },
                actions = {
                    IconButton(onClick = { menu = true }) { Icon(painterResource(R.drawable.ic_more), "More") }
                    DropdownMenu(expanded = menu, onDismissRequest = { menu = false }) {
                        DropdownMenuItem(text = { Text("Feed status") }, onClick = { menu = false; onFeeds() })
                        DropdownMenuItem(text = { Text("Server") }, onClick = { menu = false; onServer() })
                    }
                },
            )
        },
    ) { padding ->
        Column(Modifier.padding(top = padding.calculateTopPadding())) {
            val tabs = listOf("All") + news?.tabs.orEmpty()
            PrimaryScrollableTabRow(selectedTabIndex = tabs.indexOf(tab).coerceAtLeast(0), edgePadding = 8.dp) {
                tabs.forEach { t -> Tab(selected = t == tab, onClick = { onTab(t) }, text = { Text(t) }) }
            }
            PullToRefreshBox(isRefreshing = loading, onRefresh = onRefresh, modifier = Modifier.fillMaxSize()) {
                if (news == null) {
                    if (!loading) Empty(error ?: "No news yet.", onRefresh, onServer)
                    return@PullToRefreshBox
                }
                val shown = remember(news, tab) { news.stories.filter { tab == "All" || tab in it.tabs } }
                val firstSingle = shown.indexOfFirst { it.sources < 2 }
                LazyColumn(
                    state = listState,
                    contentPadding = PaddingValues(bottom = padding.calculateBottomPadding() + 16.dp),
                    modifier = Modifier.fillMaxSize(),
                ) {
                    if (error != null) item { Banner(error) }
                    if (tab == "All" && news.report != null) item { ReportCard(news.report, onReport) }
                    itemsIndexed(shown, key = { _, s -> s.id }) { i, story ->
                        if (i == firstSingle) SectionLabel("Reported by one source", Modifier.padding(16.dp, 20.dp, 16.dp, 4.dp))
                        StoryRow(story) { onOpen(story) }
                        HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)
                    }
                    item {
                        Text(
                            "Updated ${clock(news.built)}",
                            style = MaterialTheme.typography.labelMedium,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                            modifier = Modifier.padding(16.dp),
                        )
                    }
                }
            }
        }
    }
}

@Composable
private fun ReportCard(report: Report, onOpen: () -> Unit) {
    val count = report.sections.sumOf { it.stories.size }
    Surface(
        onClick = onOpen,
        color = MaterialTheme.colorScheme.secondaryContainer,
        contentColor = MaterialTheme.colorScheme.onSecondaryContainer,
        shape = RoundedCornerShape(12.dp),
        modifier = Modifier.fillMaxWidth().padding(16.dp, 12.dp, 16.dp, 4.dp),
    ) {
        Column(Modifier.padding(16.dp, 12.dp), verticalArrangement = Arrangement.spacedBy(2.dp)) {
            Text("Morning report", style = MaterialTheme.typography.titleSmall)
            Text(
                listOfNotNull(shortDay.format(report.day), report.market?.let(::marketLine), plural(count, "story", "stories"))
                    .joinToString(" \u00B7 "),
                style = MaterialTheme.typography.bodySmall,
            )
        }
    }
}

@Composable
fun ReportScreen(report: Report?, onOpen: (Long) -> Unit, onBack: () -> Unit) {
    Scaffold(topBar = { TopAppBar(title = { Text("Morning report") }, navigationIcon = { BackButton(onBack) }) }) { padding ->
        if (report == null) {
            Text(
                "No report yet. The first one comes at 05:30.",
                style = MaterialTheme.typography.bodyLarge,
                modifier = Modifier.padding(padding).padding(16.dp),
            )
            return@Scaffold
        }
        LazyColumn(
            contentPadding = PaddingValues(16.dp, padding.calculateTopPadding(), 16.dp, padding.calculateBottomPadding() + 24.dp),
        ) {
            item { Text(longDay.format(report.day), style = MaterialTheme.typography.headlineSmall) }
            report.market?.let { item { MarketRow(it) } }
            report.sections.forEach { section ->
                item(key = section.title) { SectionLabel(section.title, Modifier.padding(top = 24.dp, bottom = 2.dp)) }
                items(section.stories, key = { it.id }) { story ->
                    Column(
                        Modifier.fillMaxWidth().clip(RoundedCornerShape(8.dp)).clickable { onOpen(story.id) }.padding(vertical = 10.dp),
                        verticalArrangement = Arrangement.spacedBy(4.dp),
                    ) {
                        Text(story.headline, style = MaterialTheme.typography.titleMedium)
                        Text(story.gist, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                }
            }
        }
    }
}

@Composable
private fun MarketRow(market: Market) {
    val dark = MaterialTheme.colorScheme.surface.luminance() < 0.5f
    val color = when {
        market.change > 0 -> if (dark) Color(0xFF81C995) else Color(0xFF1E7B34)
        market.change < 0 -> if (dark) Color(0xFFF28B82) else Color(0xFFB3261E)
        else -> MaterialTheme.colorScheme.onSurfaceVariant
    }
    Row(Modifier.padding(top = 12.dp), verticalAlignment = Alignment.Bottom, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        Text(market.name, style = MaterialTheme.typography.titleSmall)
        Text("%,.2f".format(Locale.ENGLISH, market.close), style = MaterialTheme.typography.titleSmall)
        Text(marketLine(market).removePrefix(market.name).trim(), style = MaterialTheme.typography.titleSmall, color = color)
        Text(
            "${weekday.format(market.date)} close",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
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
private fun Banner(message: String) {
    Text(
        message,
        style = MaterialTheme.typography.bodyMedium,
        color = MaterialTheme.colorScheme.onErrorContainer,
        modifier = Modifier.fillMaxWidth().background(MaterialTheme.colorScheme.errorContainer).padding(16.dp, 10.dp),
    )
}

@Composable
private fun SectionLabel(text: String, modifier: Modifier = Modifier) {
    Text(
        text.uppercase(Locale.ENGLISH),
        style = MaterialTheme.typography.labelMedium,
        color = MaterialTheme.colorScheme.primary,
        modifier = modifier,
    )
}

@Composable
fun StoryRow(story: Story, onOpen: () -> Unit) {
    Column(
        Modifier.fillMaxWidth().clickable(onClick = onOpen).padding(16.dp, 14.dp),
        verticalArrangement = Arrangement.spacedBy(6.dp),
    ) {
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
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            Text(plural(story.sources, "source"), style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.primary)
            if (story.lean.rated > 0) CoverageBar(story.lean, Modifier.width(64.dp))
            Spacer(Modifier.weight(1f))
            val muted = MaterialTheme.colorScheme.onSurfaceVariant
            if (story.updates.isNotEmpty()) Text(plural(story.updates.size, "update"), style = MaterialTheme.typography.labelMedium, color = muted)
            Text(ago(story.updated), style = MaterialTheme.typography.labelMedium, color = muted)
        }
    }
}

@Composable
fun CoverageBar(lean: Lean, modifier: Modifier = Modifier) {
    val colors = leanColors()
    Row(modifier.height(6.dp).clip(RoundedCornerShape(3.dp)), horizontalArrangement = Arrangement.spacedBy(2.dp)) {
        listOf(lean.left, lean.center, lean.right).forEachIndexed { i, n ->
            if (n > 0) Box(Modifier.weight(n.toFloat()).fillMaxHeight().background(colors[i]))
        }
    }
}

@Composable
fun StoryScreen(story: Story, onBack: () -> Unit) {
    val open = rememberOpener()
    Scaffold(topBar = { TopAppBar(title = {}, navigationIcon = { BackButton(onBack) }) }) { padding ->
        LazyColumn(
            contentPadding = PaddingValues(16.dp, padding.calculateTopPadding(), 16.dp, padding.calculateBottomPadding() + 24.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            item {
                Text(
                    (story.tabs + "updated ${clock(story.updated)}").joinToString(" · "),
                    style = MaterialTheme.typography.labelMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            item { Text(story.headline, style = MaterialTheme.typography.headlineSmall) }
            item { Coverage(story) }
            story.summary?.let { summary ->
                item {
                    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                        Text(summary, style = MaterialTheme.typography.bodyLarge)
                        Sources(story.summaryFrom, open)
                    }
                }
            }
            if (story.updates.isNotEmpty()) {
                item { SectionLabel("Updates", Modifier.padding(top = 12.dp)) }
                items(story.updates) { update ->
                    Marked {
                        Text(clock(update.at), style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        Text(update.text, style = MaterialTheme.typography.bodyMedium)
                        Sources(update.from, open)
                    }
                }
            }
            if (story.quotes.isNotEmpty()) {
                item { SectionLabel("Opinion", Modifier.padding(top = 12.dp)) }
                items(story.quotes) { quote ->
                    Marked {
                        Text(
                            "“${quote.text}”",
                            style = MaterialTheme.typography.bodyLarge.copy(fontFamily = FontFamily.Serif, fontStyle = FontStyle.Italic),
                        )
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Text(
                                quote.outlet + if (quote.translated) ", translated" else "",
                                style = MaterialTheme.typography.labelMedium,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                                modifier = Modifier.weight(1f),
                            )
                            TextButton(onClick = { open(quote.url) }) { Text("Source") }
                        }
                    }
                }
            }
            item { SectionLabel("Coverage · ${plural(story.articles.size, "article")}", Modifier.padding(top = 12.dp)) }
            items(story.articles) { ArticleRow(it) { open(it.url) } }
        }
    }
}

@Composable
private fun Coverage(story: Story) {
    val lean = story.lean
    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
        Text(plural(story.sources, "independent source"), style = MaterialTheme.typography.labelLarge, color = MaterialTheme.colorScheme.primary)
        if (lean.rated > 0) {
            CoverageBar(lean, Modifier.fillMaxWidth())
            val parts = listOf("left" to lean.left, "center" to lean.center, "right" to lean.right)
                .filter { it.second > 0 }.map { "${it.second} ${it.first}" } +
                listOfNotNull((story.sources - lean.rated).takeIf { it > 0 }?.let { "$it not rated" })
            Text(parts.joinToString(" · "), style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

/** A block with a line down its left side, for updates and quotes. */
@Composable
private fun Marked(content: @Composable () -> Unit) {
    Row(Modifier.height(IntrinsicSize.Min)) {
        Box(Modifier.width(3.dp).fillMaxHeight().background(MaterialTheme.colorScheme.primary, RoundedCornerShape(2.dp)))
        Column(Modifier.padding(start = 12.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) { content() }
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
private fun ArticleRow(article: Article, onOpen: () -> Unit) {
    val colors = leanColors()
    Row(
        Modifier.fillMaxWidth().clip(RoundedCornerShape(8.dp)).clickable(onClick = onOpen).padding(vertical = 8.dp),
        horizontalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        val dot = when (article.lean) {
            "left" -> colors[0]
            "center" -> colors[1]
            "right" -> colors[2]
            else -> Color.Transparent
        }
        Box(Modifier.padding(top = 6.dp).size(8.dp).background(dot, CircleShape))
        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Row(Modifier.weight(1f), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Text(
                        article.outlet,
                        style = MaterialTheme.typography.labelLarge,
                        color = MaterialTheme.colorScheme.primary,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                        modifier = Modifier.weight(1f, fill = false),
                    )
                    if (article.opinion) {
                        Text(
                            "opinion",
                            style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.onTertiaryContainer,
                            modifier = Modifier.background(MaterialTheme.colorScheme.tertiaryContainer, RoundedCornerShape(4.dp)).padding(6.dp, 1.dp),
                        )
                    }
                }
                Text(clock(article.published), style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            Text(article.title, style = MaterialTheme.typography.bodyMedium)
        }
    }
}

@Composable
fun FeedsScreen(feeds: List<Feed>, onBack: () -> Unit) {
    val open = rememberOpener()
    Scaffold(topBar = { TopAppBar(title = { Text("Feed status") }, navigationIcon = { BackButton(onBack) }) }) { padding ->
        LazyColumn(contentPadding = PaddingValues(16.dp, padding.calculateTopPadding(), 16.dp, padding.calculateBottomPadding() + 16.dp)) {
            item {
                Text(
                    "${feeds.count { it.error == null }} of ${feeds.size} feeds working at the last check",
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(bottom = 8.dp),
                )
            }
            items(feeds) { feed ->
                Column(Modifier.fillMaxWidth().clickable { open(feed.url) }.padding(vertical = 10.dp)) {
                    Text(feed.name, style = MaterialTheme.typography.bodyLarge)
                    Text(
                        feed.error ?: plural(feed.items, "item"),
                        style = MaterialTheme.typography.bodySmall,
                        color = if (feed.error != null) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
                HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)
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
    Scaffold(topBar = { TopAppBar(title = { Text("Server") }, navigationIcon = { onBack?.let { BackButton(it) } }) }) { padding ->
        Column(Modifier.padding(padding).padding(16.dp), verticalArrangement = Arrangement.spacedBy(16.dp)) {
            Text(
                "The address of your news server on your tailnet, with its port.",
                style = MaterialTheme.typography.bodyMedium,
            )
            OutlinedTextField(
                value = address,
                onValueChange = { address = it },
                label = { Text("Server address") },
                placeholder = { Text("http://newsbox:8080") },
                singleLine = true,
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
