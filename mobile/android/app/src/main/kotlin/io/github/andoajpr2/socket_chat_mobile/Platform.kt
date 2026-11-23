package io.github.andoajpr2.socket_chat_mobile

import android.Manifest
import android.app.NotificationManager
import android.content.Context
import android.content.Intent
import android.content.RestrictionsManager
import android.content.pm.PackageManager
import android.media.AudioAttributes
import android.media.AudioManager
import android.media.Ringtone
import android.media.RingtoneManager
import android.net.Uri
import android.net.wifi.WifiManager
import android.os.Build
import android.os.PowerManager
import android.os.VibrationEffect
import android.os.Vibrator
import android.os.VibratorManager
import android.provider.Settings
import android.util.Log
import androidx.core.content.FileProvider
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel
import java.io.File
import java.lang.ref.WeakReference
import java.net.Inet4Address
import java.net.NetworkInterface

/**
 * Fonctions propres à Android appelées depuis Dart, enregistrées une seule fois sur le
 * moteur Flutter de l'application (il survit à l'activité) :
 *
 * - « socket_chat/network » (lib/app/platform.dart) : verrou multicast Wi-Fi, sans lequel
 *   Android ignore le broadcast UDP reçu (détection des collègues), adresses de diffusion ;
 * - « socket_chat/update » (lib/updates/installer.dart) : installation de l'APK téléchargé
 *   et vérifié ; Android demande toujours la confirmation de l'utilisateur ;
 * - « socket_chat/calls » (lib/app/call_platform.dart) : sonnerie et vibration d'un appel
 *   entrant, écran éteint contre l'oreille ;
 * - « socket_chat/background » (lib/app/background.dart) : service au premier plan,
 *   notifications, autorisations, actions venues des notifications (ouvrir une
 *   conversation, répondre, refuser).
 */
class Platform(private val app: Context, engine: FlutterEngine) {
    private val messenger = engine.dartExecutor.binaryMessenger
    private val background = MethodChannel(messenger, "socket_chat/background")
    private var multicastLock: WifiManager.MulticastLock? = null
    private var ringtone: Ringtone? = null
    private var vibrator: Vibrator? = null
    private var proximityLock: PowerManager.WakeLock? = null
    private var activity: WeakReference<MainActivity>? = null

    /** Action venue d'une notification, en attente d'être lue par Dart. */
    private var pendingAction: Map<String, String>? = null
    private var callMode = false

    init {
        MethodChannel(messenger, "socket_chat/network").setMethodCallHandler { call, result ->
            when (call.method) {
                "acquireMulticastLock" -> result.done { acquireMulticastLock() }
                "releaseMulticastLock" -> result.done { releaseMulticastLock() }
                "broadcastAddresses" -> result.success(broadcastAddresses())
                "managedAllowedNetworks" -> result.success(managedAllowedNetworks())
                else -> result.notImplemented()
            }
        }
        MethodChannel(messenger, "socket_chat/update").setMethodCallHandler { call, result ->
            when (call.method) {
                "canInstall" -> result.success(canInstallPackages())
                // Types de processeur, par ordre de préférence : choix de l'APK le plus léger.
                "supportedAbis" -> result.success(Build.SUPPORTED_ABIS.toList())
                "openInstallSettings" -> result.done { openInstallSettings() }
                "installApk" -> {
                    val path = call.argument<String>("path")
                    if (path == null) result.error("argument", "Chemin absent", null)
                    else result.success(installApk(path))
                }
                else -> result.notImplemented()
            }
        }
        MethodChannel(messenger, "socket_chat/calls").setMethodCallHandler { call, result ->
            when (call.method) {
                "startRinging" -> result.done { startRinging() }
                "stopRinging" -> result.done { stopRinging() }
                "setProximity" -> result.done { setProximity(call.argument<Boolean>("on") == true) }
                else -> result.notImplemented()
            }
        }
        background.setMethodCallHandler { call, result ->
            when (call.method) {
                "setKeepRunning" -> result.done { setKeepRunning(call.argument<Boolean>("on") == true) }
                "setInCall" -> result.done {
                    setInCall(call.argument<Boolean>("on") == true, call.argument<Boolean>("video") == true)
                }
                "notifyMessages" -> {
                    Notifications.messages(
                        app,
                        call.argument<String>("contact") ?: "",
                        call.argument<String>("title") ?: "",
                        call.argument<List<String>>("lines") ?: emptyList(),
                        call.argument<Int>("count") ?: 1,
                    )
                    result.success(null)
                }
                "cancelMessages" -> result.done { Notifications.cancelMessages(app, call.argument<String>("contact") ?: "") }
                "showIncomingCall" -> {
                    Notifications.incomingCall(
                        app,
                        call.argument<String>("call") ?: "",
                        call.argument<String>("contact") ?: "",
                        call.argument<String>("name") ?: "",
                        call.argument<Boolean>("video") == true,
                    )
                    result.success(null)
                }
                "cancelIncomingCall" -> result.done { Notifications.cancelIncomingCall(app) }
                "setCallMode" -> result.done { setCallMode(call.argument<Boolean>("on") == true) }
                "status" -> result.success(status())
                "requestNotificationPermission" -> result.done { requestNotificationPermission() }
                "openNotificationSettings" -> result.done { openNotificationSettings() }
                "openFullScreenSettings" -> result.done { openFullScreenSettings() }
                "requestBatteryUnrestricted" -> result.done { requestBatteryUnrestricted() }
                "takePendingAction" -> {
                    result.success(pendingAction)
                    pendingAction = null
                }
                else -> result.notImplemented()
            }
        }
    }

    // -- Activité ------------------------------------------------------------
    fun attach(activity: MainActivity) {
        this.activity = WeakReference(activity)
        activity.showOverLockScreen(callMode)
    }

    fun detach(activity: MainActivity) {
        if (this.activity?.get() === activity) this.activity = null
    }

    /** Transmet à Dart une action venue d'une notification (ouvrir, répondre, refuser). */
    fun pushAction(action: Map<String, String>) {
        pendingAction = action
        background.invokeMethod("action", null)
    }

    /** Pendant un appel entrant ou en cours : l'activité s'affiche par-dessus l'écran verrouillé. */
    private fun setCallMode(on: Boolean) {
        callMode = on
        activity?.get()?.showOverLockScreen(on)
    }

    // -- Service et autorisations -----------------------------------------------
    private fun setKeepRunning(on: Boolean) {
        Prefs.setKeepRunning(app, on)
        if (on) MessagingService.start(app) else if (!MessagingService.inCall) MessagingService.stop(app)
    }

    /** En appel, le service déclare le micro, et la caméra en vidéo (exigé par Android 14). */
    private fun setInCall(on: Boolean, video: Boolean) {
        MessagingService.inCall = on
        MessagingService.inVideo = on && video
        if (on || Prefs.keepRunning(app)) MessagingService.start(app) else MessagingService.stop(app)
    }

    private fun status(): Map<String, Boolean> {
        val notifications = app.getSystemService(NotificationManager::class.java)
        val power = app.getSystemService(Context.POWER_SERVICE) as PowerManager
        return mapOf(
            "notifications" to notifications.areNotificationsEnabled(),
            "fullScreen" to (Build.VERSION.SDK_INT < 34 || notifications.canUseFullScreenIntent()),
            "battery" to power.isIgnoringBatteryOptimizations(app.packageName),
        )
    }

    private fun requestNotificationPermission() {
        if (Build.VERSION.SDK_INT < 33) return
        if (app.checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED) return
        activity?.get()?.requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), NOTIFICATION_REQUEST)
    }

    private fun openNotificationSettings() {
        val intent = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            Intent(Settings.ACTION_APP_NOTIFICATION_SETTINGS).putExtra(Settings.EXTRA_APP_PACKAGE, app.packageName)
        } else {
            Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.parse("package:${app.packageName}"))
        }
        start(intent)
    }

    /** Android 14 : autorisation d'afficher les appels en plein écran (écran verrouillé). */
    private fun openFullScreenSettings() {
        if (Build.VERSION.SDK_INT < 34) return
        start(Intent(Settings.ACTION_MANAGE_APP_USE_FULL_SCREEN_INTENT, Uri.parse("package:${app.packageName}")))
    }

    /** Demande à ne pas restreindre Socket Chat en arrière-plan (économiseur de batterie). */
    private fun requestBatteryUnrestricted() {
        start(Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, Uri.parse("package:${app.packageName}")))
    }

    private fun start(intent: Intent) {
        val current = activity?.get()
        try {
            if (current != null) current.startActivity(intent)
            else app.startActivity(intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
        } catch (e: Exception) {
            Log.w(TAG, "Écran de réglage indisponible : $intent", e)
        }
    }

    // -- Sonnerie et écran ---------------------------------------------------------
    /** Sonnerie du téléphone (muette en mode silencieux) et vibration (sauf en silencieux). */
    private fun startRinging() {
        stopRinging()
        val audio = app.getSystemService(Context.AUDIO_SERVICE) as AudioManager
        if (audio.ringerMode == AudioManager.RINGER_MODE_NORMAL) {
            val uri = RingtoneManager.getActualDefaultRingtoneUri(app, RingtoneManager.TYPE_RINGTONE)
                ?: RingtoneManager.getDefaultUri(RingtoneManager.TYPE_RINGTONE)
            ringtone = RingtoneManager.getRingtone(app, uri)?.apply {
                audioAttributes = AudioAttributes.Builder()
                    .setUsage(AudioAttributes.USAGE_NOTIFICATION_RINGTONE)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION)
                    .build()
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) isLooping = true
                play()
            }
        }
        if (audio.ringerMode != AudioManager.RINGER_MODE_SILENT) {
            val device = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                (app.getSystemService(Context.VIBRATOR_MANAGER_SERVICE) as VibratorManager).defaultVibrator
            } else {
                @Suppress("DEPRECATION")
                app.getSystemService(Context.VIBRATOR_SERVICE) as Vibrator
            }
            val pattern = longArrayOf(0, 800, 1200)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                device.vibrate(VibrationEffect.createWaveform(pattern, 0))
            } else {
                @Suppress("DEPRECATION")
                device.vibrate(pattern, 0)
            }
            vibrator = device
        }
    }

    private fun stopRinging() {
        ringtone?.stop()
        ringtone = null
        vibrator?.cancel()
        vibrator = null
    }

    /** Écran éteint quand le téléphone est contre l'oreille (appel sur l'écouteur). */
    private fun setProximity(on: Boolean) {
        val power = app.getSystemService(Context.POWER_SERVICE) as PowerManager
        if (on) {
            if (proximityLock?.isHeld == true) return
            if (!power.isWakeLockLevelSupported(PowerManager.PROXIMITY_SCREEN_OFF_WAKE_LOCK)) return
            proximityLock = power.newWakeLock(PowerManager.PROXIMITY_SCREEN_OFF_WAKE_LOCK, "socket-chat:call")
                .apply { acquire(4 * 60 * 60 * 1000L) }
        } else {
            proximityLock?.let { if (it.isHeld) it.release() }
            proximityLock = null
        }
    }

    // -- Mise à jour -----------------------------------------------------------------
    /** Android 8+ : Socket Chat doit être autorisée à installer des applications. */
    private fun canInstallPackages(): Boolean =
        Build.VERSION.SDK_INT < Build.VERSION_CODES.O || app.packageManager.canRequestPackageInstalls()

    private fun openInstallSettings() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
        start(Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES, Uri.parse("package:${app.packageName}")))
    }

    /** Ouvre l'installateur d'Android ; seul un fichier du dossier des mises à jour est accepté. */
    private fun installApk(path: String): Boolean {
        val updates = File(app.cacheDir, "updates").canonicalFile
        val file = File(path).canonicalFile
        if (!file.isFile || file.parentFile != updates || !file.name.endsWith(".apk")) return false
        val uri = FileProvider.getUriForFile(app, "${app.packageName}.updates", file)
        val intent = Intent(Intent.ACTION_VIEW)
            .setDataAndType(uri, "application/vnd.android.package-archive")
            .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_ACTIVITY_NEW_TASK)
        app.startActivity(intent)
        return true
    }

    // -- Réseau ----------------------------------------------------------------------
    private fun acquireMulticastLock() {
        if (multicastLock?.isHeld == true) return
        val wifi = app.getSystemService(Context.WIFI_SERVICE) as WifiManager
        multicastLock = wifi.createMulticastLock("socket-chat-discovery").apply {
            setReferenceCounted(false)
            acquire()
        }
    }

    private fun releaseMulticastLock() {
        multicastLock?.let { if (it.isHeld) it.release() }
        multicastLock = null
    }

    /**
     * Réseaux autorisés fixés par l'administrateur (configuration gérée par MDM, voir
     * res/xml/app_restrictions.xml), ou null.
     */
    private fun managedAllowedNetworks(): String? = try {
        val manager = app.getSystemService(Context.RESTRICTIONS_SERVICE) as RestrictionsManager
        manager.applicationRestrictions.getString("allowed_networks")?.takeIf { it.isNotBlank() }
    } catch (_: Exception) {
        null
    }

    /** Adresses de diffusion réelles (masque de sous-réseau compris) des réseaux IPv4. */
    private fun broadcastAddresses(): List<String> {
        val result = mutableSetOf<String>()
        try {
            for (iface in NetworkInterface.getNetworkInterfaces()) {
                if (!iface.isUp || iface.isLoopback) continue
                for (entry in iface.interfaceAddresses) {
                    if (entry.address is Inet4Address) {
                        entry.broadcast?.hostAddress?.let { result.add(it) }
                    }
                }
            }
        } catch (_: Exception) {
            // Interfaces illisibles : la diffusion globale reste utilisée.
        }
        return result.sorted()
    }

    companion object {
        const val TAG = "SocketChat"
        private const val NOTIFICATION_REQUEST = 4242
    }
}

/** Réglages lus côté Android sans Dart (démarrage du téléphone). */
object Prefs {
    private const val FILE = "socket_chat_native"
    private const val KEEP_RUNNING = "keep_running"

    fun keepRunning(context: Context): Boolean =
        context.getSharedPreferences(FILE, Context.MODE_PRIVATE).getBoolean(KEEP_RUNNING, false)

    fun setKeepRunning(context: Context, on: Boolean) {
        context.getSharedPreferences(FILE, Context.MODE_PRIVATE).edit().putBoolean(KEEP_RUNNING, on).apply()
    }
}

/** Réponse vide à Dart après une action sans résultat. */
private inline fun MethodChannel.Result.done(block: () -> Unit) {
    block()
    success(null)
}
