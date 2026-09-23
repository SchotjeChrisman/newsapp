package io.github.schotjechrisman.news

import android.content.res.Configuration.UI_MODE_NIGHT_YES
import androidx.compose.foundation.lazy.LazyListState
import androidx.compose.runtime.Composable
import androidx.compose.ui.tooling.preview.Preview
import com.android.tools.screenshot.PreviewTest
import java.time.Duration
import java.time.Instant
import java.time.LocalDate

private fun hoursAgo(h: Long): Instant = Instant.now().minus(Duration.ofMinutes(h * 60 + 7))

private val iran = Story(
    id = 1,
    headline = "Trump threatens to 'annihilate' Iran in UN speech, cites possible deal",
    summary = "In his address to the UN General Assembly, President Trump said he was deciding whether to strike a deal " +
        "with Iran or \"annihilate the Islamic Republic.\" Most of Iran's delegation walked out during his speech. " +
        "Iran's Armed Forces dismissed his comments as \"propaganda.\"",
    tabs = listOf("Global"),
    sources = 28,
    updated = hoursAgo(1),
    lean = Lean(6, 12, 7),
    summaryFrom = listOf("BNR", "USA Today", "Newsmax", "Reuters", "NOS", "BBC News", "Fox News", "CNN").map { Ref(it, "https://x/$it") },
    updates = listOf(
        Update(hoursAgo(3), "Iran's foreign minister said Tehran would respond to any attack \"within hours\".", listOf(Ref("Reuters", "https://x"))),
        Update(hoursAgo(1), "Oil prices rose 2% after the speech, according to Bloomberg.", listOf(Ref("Bloomberg", "https://x"))),
    ),
    quotes = listOf(Quote("This speech made a deal less likely, not more", "de Volkskrant", "https://x", translated = true)),
    articles = listOf(
        Article("Reuters", "Trump tells UN he may 'annihilate' Iran or strike a deal", "https://x", hoursAgo(5), false, "center"),
        Article("De Telegraaf", "Trump dreigt Iran te vernietigen in VN-toespraak", "https://x", hoursAgo(4), false, "right"),
        Article("de Volkskrant", "Opinion: This speech made a deal less likely", "https://x", hoursAgo(3), true, "left"),
        Article("Omroep Gelderland", "Reacties op VN-toespraak", "https://x", hoursAgo(2), false, null),
    ),
)

private val stories = listOf(
    iran,
    iran.copy(
        id = 2, headline = "Dutch cabinet presents 2027 budget on Prinsjesdag with tax cuts for middle incomes",
        summary = "The cabinet presented its budget for 2027, cutting income tax for middle incomes.",
        tabs = listOf("NL"), sources = 11, lean = Lean(2, 6, 2), updates = emptyList(), updated = hoursAgo(6),
    ),
    iran.copy(
        id = 3, headline = "PEC Zwolle signs Norwegian striker until 2029",
        summary = "PEC Zwolle signed a striker from Molde on a contract until 2029, the club said.",
        tabs = listOf("Zwolle"), sources = 2, lean = Lean(0, 0, 0), updates = emptyList(), updated = hoursAgo(9),
    ),
    iran.copy(
        id = 4, headline = "Road works close the IJsselallee in Zwolle for three weeks", summary = null,
        tabs = listOf("Zwolle"), sources = 1, lean = Lean(0, 0, 0), updates = emptyList(), updated = hoursAgo(30),
    ),
)

private val report = Report(
    day = LocalDate.now(),
    market = Market("S&P 500", 7746.07, -0.26, LocalDate.now().minusDays(1)),
    sections = listOf(
        Section("Zwolle", listOf(ReportStory(3, "Zwolle court briefly evacuated after fire in parking garage",
            "A fire in the parking garage under the Zwolle courthouse forced a short evacuation during a hearing; no one was hurt.", 8))),
        Section("NL", listOf(ReportStory(2, stories[1].headline,
            "The cabinet's 2027 budget cuts income tax for middle incomes and raises spending on defence.", 11))),
        Section("Global", listOf(ReportStory(1, iran.headline,
            "Trump told the UN he may strike a deal with Iran or attack it; US and Iranian officials later met.", 28))),
        Section("AI", listOf(ReportStory(5, "Anthropic launches Claude Opus 5.5, cheaper and faster than predecessor",
            "Anthropic released a new model it says is faster and cheaper than the one before.", 7))),
    ),
)

private val news = News(
    built = hoursAgo(0),
    tabs = listOf("Zwolle", "Overijssel", "NL", "EU", "US", "Global"),
    stories = stories,
    feeds = listOf(
        Feed("Euractiv", "https://x", 0, "HTTP Error 403: Forbidden"),
        Feed("AD", "https://x", 30, null),
        Feed("BBC News", "https://x", 42, null),
    ),
    report = report,
)

@PreviewTest
@Preview(name = "light", widthDp = 400, heightDp = 860)
@Preview(name = "dark", widthDp = 400, heightDp = 860, uiMode = UI_MODE_NIGHT_YES)
@Composable
fun StoriesPreview() {
    NewsTheme {
        StoriesScreen(news, loading = false, error = null, tab = "All", listState = LazyListState(),
            onTab = {}, onRefresh = {}, onOpen = {}, onReport = {}, onFeeds = {}, onServer = {})
    }
}

@PreviewTest
@Preview(name = "light", widthDp = 400, heightDp = 860)
@Preview(name = "dark", widthDp = 400, heightDp = 860, uiMode = UI_MODE_NIGHT_YES)
@Composable
fun StoriesOfflinePreview() {
    NewsTheme {
        StoriesScreen(news, loading = false, error = "Can't find http://newsbox:8080. Is Tailscale on?", tab = "Zwolle",
            listState = LazyListState(), onTab = {}, onRefresh = {}, onOpen = {}, onReport = {}, onFeeds = {}, onServer = {})
    }
}

@PreviewTest
@Preview(name = "light", widthDp = 400, heightDp = 1500)
@Preview(name = "dark", widthDp = 400, heightDp = 1500, uiMode = UI_MODE_NIGHT_YES)
@Composable
fun StoryPreview() {
    NewsTheme { StoryScreen(iran, onBack = {}) }
}

@PreviewTest
@Preview(name = "light", widthDp = 400, heightDp = 860)
@Preview(name = "dark", widthDp = 400, heightDp = 860, uiMode = UI_MODE_NIGHT_YES)
@Composable
fun ReportPreview() {
    NewsTheme { ReportScreen(report, onOpen = {}, onBack = {}) }
}

@PreviewTest
@Preview(name = "light", widthDp = 400, heightDp = 500)
@Composable
fun FeedsPreview() {
    NewsTheme { FeedsScreen(news.feeds, onBack = {}) }
}

@PreviewTest
@Preview(name = "light", widthDp = 400, heightDp = 500)
@Preview(name = "dark", widthDp = 400, heightDp = 500, uiMode = UI_MODE_NIGHT_YES)
@Composable
fun FirstStartPreview() {
    NewsTheme { ServerScreen(current = "", onBack = null, onSave = {}) }
}
