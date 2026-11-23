package io.github.andoajpr2.socket_chat_mobile

import android.Manifest
import android.app.Service
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import android.util.Log

/**
 * Service au premier plan qui garde Socket Chat joignable application fermée : tant
 * qu'il tourne, Android n'arrête pas le processus, et la messagerie Dart (moteur Flutter
 * de [SocketChatApplication]) continue de recevoir messages et appels. Il affiche la
 * notification permanente qu'Android exige. Pendant un appel, il déclare aussi l'usage
 * du micro (et de la caméra en vidéo), sans quoi Android 14 les couperait dès que
 * l'écran n'est plus visible.
 */
class MessagingService : Service() {
    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val notification = Notifications.service(this)
        try {
            startForeground(notification, callTypes())
        } catch (e: Exception) {
            // Micro ou caméra refusés hors de l'écran : on garde au moins la messagerie.
            Log.w(Platform.TAG, "Service au premier plan : micro ou caméra refusés", e)
            try {
                startForeground(notification, 0)
            } catch (e2: Exception) {
                // Démarrage refusé (arrière-plan, autorisation) : l'application reste utilisable.
                Log.w(Platform.TAG, "Service au premier plan refusé", e2)
                stopSelf()
            }
        }
        return START_STICKY
    }

    /** Types d'usage déclarés en plus de la messagerie pendant un appel (Android 11+). */
    private fun callTypes(): Int {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.R) return 0
        var type = 0
        if (inCall && granted(Manifest.permission.RECORD_AUDIO)) type = type or ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE
        if (inVideo && granted(Manifest.permission.CAMERA)) type = type or ServiceInfo.FOREGROUND_SERVICE_TYPE_CAMERA
        return type
    }

    private fun startForeground(notification: android.app.Notification, extra: Int) {
        when {
            Build.VERSION.SDK_INT >= 34 -> startForeground(
                Notifications.SERVICE_ID,
                notification,
                ServiceInfo.FOREGROUND_SERVICE_TYPE_REMOTE_MESSAGING or extra,
            )
            Build.VERSION.SDK_INT >= Build.VERSION_CODES.R ->
                startForeground(Notifications.SERVICE_ID, notification, extra)
            else -> startForeground(Notifications.SERVICE_ID, notification)
        }
    }

    private fun granted(permission: String) = checkSelfPermission(permission) == PackageManager.PERMISSION_GRANTED

    companion object {
        /** Un appel est en cours (le service déclare alors le micro). */
        @Volatile
        var inCall = false

        /** L'appel en cours est un appel vidéo (le service déclare aussi la caméra). */
        @Volatile
        var inVideo = false

        fun start(context: Context) {
            val intent = Intent(context, MessagingService::class.java)
            try {
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) context.startForegroundService(intent)
                else context.startService(intent)
            } catch (e: Exception) {
                // Android 12+ refuse le démarrage depuis l'arrière-plan hors cas prévus.
                Log.w(Platform.TAG, "Démarrage du service refusé", e)
            }
        }

        fun stop(context: Context) {
            context.stopService(Intent(context, MessagingService::class.java))
        }
    }
}

/**
 * Redémarrage du téléphone ou mise à jour de Socket Chat : relance le service si
 * « Rester joignable » est activé. Le processus démarre alors avec la messagerie.
 */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        when (intent.action) {
            Intent.ACTION_BOOT_COMPLETED, Intent.ACTION_MY_PACKAGE_REPLACED ->
                if (Prefs.keepRunning(context)) MessagingService.start(context)
        }
    }
}

/** Bouton « Refuser » de la notification d'appel entrant. */
class CallActionReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val call = intent.getStringExtra(MainActivity.EXTRA_CALL) ?: return
        Notifications.cancelIncomingCall(context)
        val app = context.applicationContext as SocketChatApplication
        app.platform.pushAction(mapOf("type" to "decline", "call" to call))
    }
}
