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
 * du micro, sans quoi Android 14 couperait le micro dès que l'écran n'est plus visible.
 */
class MessagingService : Service() {
    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        try {
            val notification = Notifications.service(this)
            if (Build.VERSION.SDK_INT >= 34) {
                var type = ServiceInfo.FOREGROUND_SERVICE_TYPE_REMOTE_MESSAGING
                if (inCall && hasMicrophone()) type = type or ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE
                startForeground(Notifications.SERVICE_ID, notification, type)
            } else if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
                val type = if (inCall && hasMicrophone()) ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE else 0
                startForeground(Notifications.SERVICE_ID, notification, type)
            } else {
                startForeground(Notifications.SERVICE_ID, notification)
            }
        } catch (e: Exception) {
            // Démarrage refusé (arrière-plan, autorisation) : l'application reste utilisable.
            Log.w(Platform.TAG, "Service au premier plan refusé", e)
            stopSelf()
        }
        return START_STICKY
    }

    private fun hasMicrophone() =
        checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED

    companion object {
        /** Un appel est en cours (le service déclare alors le micro). */
        @Volatile
        var inCall = false

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
