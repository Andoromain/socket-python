package io.github.andoajpr2.socket_chat_mobile

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.graphics.Color
import android.graphics.drawable.Icon
import android.os.Build

/**
 * Notifications de Socket Chat :
 * - permanente, du service qui garde l'application joignable (discrète) ;
 * - nouveaux messages, une par conversation (un toucher l'ouvre) ;
 * - appel entrant : plein écran sur l'écran verrouillé, Répondre / Refuser. La sonnerie
 *   et la vibration sont jouées à part (Platform.startRinging), selon le mode du téléphone.
 */
object Notifications {
    const val SERVICE_ID = 1
    private const val CALL_ID = 2
    private const val SERVICE_CHANNEL = "service"
    private const val MESSAGES_CHANNEL = "messages"
    private const val CALLS_CHANNEL = "calls"
    private val ACCENT = Color.rgb(0x2B, 0x7B, 0xBF)

    fun createChannels(context: Context) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
        val manager = context.getSystemService(NotificationManager::class.java)
        manager.createNotificationChannel(
            NotificationChannel(SERVICE_CHANNEL, "Socket Chat actif", NotificationManager.IMPORTANCE_MIN).apply {
                description = "Notification permanente : Socket Chat reste joignable application fermée."
                setShowBadge(false)
            },
        )
        manager.createNotificationChannel(
            NotificationChannel(MESSAGES_CHANNEL, "Messages", NotificationManager.IMPORTANCE_HIGH).apply {
                description = "Nouveaux messages et appels manqués."
            },
        )
        manager.createNotificationChannel(
            NotificationChannel(CALLS_CHANNEL, "Appels entrants", NotificationManager.IMPORTANCE_HIGH).apply {
                description = "Appel entrant, affiché en plein écran sur l'écran verrouillé."
                setSound(null, null) // sonnerie jouée par l'application, selon le mode du téléphone
                enableVibration(false)
                lockscreenVisibility = Notification.VISIBILITY_PUBLIC
            },
        )
    }

    fun service(context: Context): Notification =
        builder(context, SERVICE_CHANNEL)
            .setContentTitle("Socket Chat est actif")
            .setContentText("Vous recevez messages et appels, même application fermée.")
            .setContentIntent(openApp(context, 0, null))
            .setOngoing(true)
            .setShowWhen(false)
            .apply { legacyPriority(this, Notification.PRIORITY_MIN) }
            .build()

    /** Derniers messages non lus d'une conversation (un toucher l'ouvre). */
    fun messages(context: Context, contactId: String, title: String, lines: List<String>, count: Int) {
        val style = Notification.InboxStyle()
        lines.takeLast(6).forEach { style.addLine(it) }
        if (count > 1) style.setSummaryText("$count nouveaux messages")
        val notification = builder(context, MESSAGES_CHANNEL)
            .setContentTitle(title)
            .setContentText(lines.lastOrNull() ?: "")
            .setStyle(style)
            .setNumber(count)
            .setCategory(Notification.CATEGORY_MESSAGE)
            .setContentIntent(openApp(context, requestCode(contactId), contactId))
            .setAutoCancel(true)
            .apply { legacyPriority(this, Notification.PRIORITY_HIGH) }
            .build()
        manager(context).notify(contactId, MESSAGES_TAG_ID, notification)
    }

    fun cancelMessages(context: Context, contactId: String) {
        manager(context).cancel(contactId, MESSAGES_TAG_ID)
    }

    /** Appel entrant : plein écran (écran verrouillé), sinon bandeau avec Répondre / Refuser. */
    fun incomingCall(context: Context, callId: String, contactId: String, name: String) {
        val screen = Intent(context, MainActivity::class.java)
            .putExtra(MainActivity.EXTRA_CALL, callId)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP)
        val fullScreen = PendingIntent.getActivity(context, 10, screen, FLAGS)
        val answer = PendingIntent.getActivity(
            context,
            11,
            Intent(screen).putExtra(MainActivity.EXTRA_ANSWER, true),
            FLAGS,
        )
        val decline = PendingIntent.getBroadcast(
            context,
            12,
            Intent(context, CallActionReceiver::class.java).putExtra(MainActivity.EXTRA_CALL, callId),
            FLAGS,
        )
        val notification = builder(context, CALLS_CHANNEL)
            .setContentTitle(name.ifEmpty { "Appel entrant" })
            .setContentText("Appel audio entrant")
            .setCategory(Notification.CATEGORY_CALL)
            .setVisibility(Notification.VISIBILITY_PUBLIC)
            .setOngoing(true)
            .setAutoCancel(false)
            .setContentIntent(fullScreen)
            .setFullScreenIntent(fullScreen, true)
            .addAction(Notification.Action.Builder(null as Icon?, "Refuser", decline).build())
            .addAction(Notification.Action.Builder(null as Icon?, "Répondre", answer).build())
            .apply { legacyPriority(this, Notification.PRIORITY_MAX) }
            .build()
        manager(context).notify(CALL_ID, notification)
    }

    fun cancelIncomingCall(context: Context) {
        manager(context).cancel(CALL_ID)
    }

    // -- Interne ---------------------------------------------------------------------
    private const val MESSAGES_TAG_ID = 100
    private const val FLAGS = PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE

    private fun manager(context: Context) = context.getSystemService(NotificationManager::class.java)

    private fun builder(context: Context, channel: String): Notification.Builder {
        val builder = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            Notification.Builder(context, channel)
        } else {
            @Suppress("DEPRECATION")
            Notification.Builder(context)
        }
        return builder.setSmallIcon(R.drawable.ic_notification).setColor(ACCENT)
    }

    /** Priorité des notifications avant Android 8 (ensuite : importance du canal). */
    private fun legacyPriority(builder: Notification.Builder, priority: Int) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) {
            @Suppress("DEPRECATION")
            builder.setPriority(priority)
        }
    }

    /** Ouvre l'application, et la conversation [contactId] si elle est donnée. */
    private fun openApp(context: Context, requestCode: Int, contactId: String?): PendingIntent {
        val intent = Intent(context, MainActivity::class.java)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP)
        if (contactId != null) intent.putExtra(MainActivity.EXTRA_CONTACT, contactId)
        return PendingIntent.getActivity(context, requestCode, intent, FLAGS)
    }

    /** Un PendingIntent distinct par conversation (sinon le dernier remplacerait les autres). */
    private fun requestCode(contactId: String) = 1000 + (contactId.hashCode() and 0xFFFFF)
}
