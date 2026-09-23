package io.github.schotjechrisman.news

import android.Manifest
import android.app.AlarmManager
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import java.time.LocalDate
import java.time.ZonedDateTime
import java.util.Locale
import kotlin.concurrent.thread
import kotlin.math.abs

/** The morning report's notification: an alarm at 05:30 fetches the day's report and shows its first headlines. When the
 *  report isn't there yet, it tries again every 15 minutes for two hours. */
object MorningReport {
    private const val HOUR = 5
    private const val MINUTE = 30
    private const val RETRIES = 8
    private const val CHANNEL = "report"
    const val OPEN = "open_report"

    /** One alarm at a time: the next 05:30, or a retry when the day's report wasn't there yet. */
    fun schedule(context: Context, at: Long = next(), attempt: Int = 0) {
        val intent = Intent(context, ReportReceiver::class.java).putExtra("attempt", attempt)
        val pending = PendingIntent.getBroadcast(context, 0, intent, PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        val alarms = context.getSystemService(AlarmManager::class.java)
        // Both fire in Doze and let the app use the network for a few seconds; only the exact one is on time.
        if (Build.VERSION.SDK_INT < 31 || alarms.canScheduleExactAlarms()) {
            alarms.setExactAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, at, pending)
        } else {
            alarms.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, at, pending)
        }
    }

    /** At app start, after a restart or an app update: alarms don't survive the last two. Between 05:30 and noon on a
     *  morning without a report notification yet, it fetches now; otherwise it waits for the next 05:30. */
    fun ensure(context: Context) {
        val now = ZonedDateTime.now()
        val due = now.withHour(HOUR).withMinute(MINUTE).withSecond(0).withNano(0)
        val notified = prefs(context).getString("notified", null) == now.toLocalDate().toString()
        if (now.isAfter(due) && now.hour < 12 && !notified) schedule(context, System.currentTimeMillis() + 60_000)
        else schedule(context)
    }

    private fun next(): Long {
        val now = ZonedDateTime.now()
        val today = now.withHour(HOUR).withMinute(MINUTE).withSecond(0).withNano(0)
        return (if (today.isAfter(now)) today else today.plusDays(1)).toInstant().toEpochMilli()
    }

    private fun prefs(context: Context) = context.getSharedPreferences("news", Context.MODE_PRIVATE)

    fun fetchAndNotify(context: Context, attempt: Int) {
        val server = prefs(context).getString("server", "").orEmpty()
        if (server.isBlank()) return schedule(context)
        val fetched = runCatching { parseReport(download(server, "/api/report", timeout = 20_000)) }
        val today = fetched.getOrNull()?.takeIf { it.day == LocalDate.now() }
        if (today == null && attempt < RETRIES && ZonedDateTime.now().hour < 12) {
            return schedule(context, System.currentTimeMillis() + 15 * 60_000, attempt + 1)
        }
        notify(
            context, today,
            if (fetched.isFailure) "Couldn't reach $server. Open the app to try again." else "Today's report isn't ready yet.",
        )
        prefs(context).edit().putString("notified", LocalDate.now().toString()).apply()
        schedule(context)
    }

    private fun notify(context: Context, report: Report?, problem: String) {
        if (Build.VERSION.SDK_INT >= 33 &&
            context.checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
        ) return
        val manager = context.getSystemService(NotificationManager::class.java)
        // Low importance: it shows up without a sound, so it doesn't wake anyone at 05:30.
        manager.createNotificationChannel(NotificationChannel(CHANNEL, "Morning report", NotificationManager.IMPORTANCE_LOW))
        val open = PendingIntent.getActivity(
            context, 0,
            Intent(context, MainActivity::class.java)
                .putExtra(OPEN, true)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP or Intent.FLAG_ACTIVITY_SINGLE_TOP),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        val builder = Notification.Builder(context, CHANNEL)
            .setSmallIcon(R.drawable.ic_notification)
            .setContentTitle("Morning report")
            .setContentIntent(open)
            .setAutoCancel(true)
        if (report == null) {
            builder.setContentText(problem)
        } else {
            val headlines = report.sections.flatMap { it.stories }.map { it.headline }
            builder.setContentText(listOfNotNull(report.market?.let(::marketLine), if (headlines.size == 1) "1 story" else "${headlines.size} stories").joinToString(" · "))
            builder.setStyle(Notification.InboxStyle().also { style -> headlines.take(5).forEach { style.addLine(it) } })
        }
        manager.notify(1, builder.build())
    }
}

/** "S&P 500 −0.24%" */
fun marketLine(market: Market): String {
    val sign = if (market.change > 0) "+" else if (market.change < 0) "−" else ""
    return "%s %s%.2f%%".format(Locale.ENGLISH, market.name, sign, abs(market.change))
}

class ReportReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action == Intent.ACTION_BOOT_COMPLETED || intent.action == Intent.ACTION_MY_PACKAGE_REPLACED) {
            return MorningReport.ensure(context)
        }
        val result = goAsync()
        thread {
            try {
                MorningReport.fetchAndNotify(context, intent.getIntExtra("attempt", 0))
            } finally {
                result.finish()
            }
        }
    }
}
