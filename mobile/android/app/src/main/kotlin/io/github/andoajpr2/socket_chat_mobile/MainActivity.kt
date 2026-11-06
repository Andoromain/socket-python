package io.github.andoajpr2.socket_chat_mobile

import android.content.Context
import android.content.Intent
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
import androidx.core.content.FileProvider
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel
import java.io.File
import java.net.Inet4Address
import java.net.NetworkInterface

/**
 * Fonctions propres à Android appelées depuis Dart :
 *
 * - canal « socket_chat/network » (lib/app/platform.dart) : Android ignore le broadcast
 *   UDP reçu tant qu'aucune application ne détient un verrou multicast Wi-Fi ; sans lui,
 *   les postes du réseau ne seraient jamais détectés ;
 * - canal « socket_chat/update » (lib/updates/installer.dart) : installation de l'APK
 *   téléchargé et vérifié. Android demande toujours la confirmation de l'utilisateur ;
 * - canal « socket_chat/calls » (lib/app/call_platform.dart) : sonnerie et vibration
 *   du téléphone pour un appel entrant, écran éteint contre l'oreille pendant un appel.
 */
class MainActivity : FlutterActivity() {
    private var multicastLock: WifiManager.MulticastLock? = null
    private var ringtone: Ringtone? = null
    private var vibrator: Vibrator? = null
    private var proximityLock: PowerManager.WakeLock? = null

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, "socket_chat/network")
            .setMethodCallHandler { call, result ->
                when (call.method) {
                    "acquireMulticastLock" -> {
                        acquireMulticastLock()
                        result.success(null)
                    }
                    "releaseMulticastLock" -> {
                        releaseMulticastLock()
                        result.success(null)
                    }
                    "broadcastAddresses" -> result.success(broadcastAddresses())
                    else -> result.notImplemented()
                }
            }
        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, "socket_chat/update")
            .setMethodCallHandler { call, result ->
                when (call.method) {
                    "canInstall" -> result.success(canInstallPackages())
                    "openInstallSettings" -> {
                        openInstallSettings()
                        result.success(null)
                    }
                    "installApk" -> {
                        val path = call.argument<String>("path")
                        if (path == null) {
                            result.error("argument", "Chemin absent", null)
                        } else {
                            result.success(installApk(path))
                        }
                    }
                    else -> result.notImplemented()
                }
            }
        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, "socket_chat/calls")
            .setMethodCallHandler { call, result ->
                when (call.method) {
                    "startRinging" -> {
                        startRinging()
                        result.success(null)
                    }
                    "stopRinging" -> {
                        stopRinging()
                        result.success(null)
                    }
                    "setProximity" -> {
                        setProximity(call.argument<Boolean>("on") == true)
                        result.success(null)
                    }
                    else -> result.notImplemented()
                }
            }
    }

    /** Sonnerie du téléphone (muette en mode silencieux) et vibration (sauf en silencieux). */
    private fun startRinging() {
        stopRinging()
        val audio = getSystemService(Context.AUDIO_SERVICE) as AudioManager
        if (audio.ringerMode == AudioManager.RINGER_MODE_NORMAL) {
            val uri = RingtoneManager.getActualDefaultRingtoneUri(this, RingtoneManager.TYPE_RINGTONE)
                ?: RingtoneManager.getDefaultUri(RingtoneManager.TYPE_RINGTONE)
            ringtone = RingtoneManager.getRingtone(this, uri)?.apply {
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
                (getSystemService(Context.VIBRATOR_MANAGER_SERVICE) as VibratorManager).defaultVibrator
            } else {
                @Suppress("DEPRECATION")
                getSystemService(Context.VIBRATOR_SERVICE) as Vibrator
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
        val power = getSystemService(Context.POWER_SERVICE) as PowerManager
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

    /** Android 8+ : Socket Chat doit être autorisée à installer des applications. */
    private fun canInstallPackages(): Boolean =
        Build.VERSION.SDK_INT < Build.VERSION_CODES.O || packageManager.canRequestPackageInstalls()

    private fun openInstallSettings() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
        val intent = Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES, Uri.parse("package:$packageName"))
        startActivity(intent)
    }

    /** Ouvre l'installateur d'Android ; seul un fichier du dossier des mises à jour est accepté. */
    private fun installApk(path: String): Boolean {
        val updates = File(cacheDir, "updates").canonicalFile
        val file = File(path).canonicalFile
        if (!file.isFile || file.parentFile != updates || !file.name.endsWith(".apk")) return false
        val uri = FileProvider.getUriForFile(this, "$packageName.updates", file)
        val intent = Intent(Intent.ACTION_VIEW)
            .setDataAndType(uri, "application/vnd.android.package-archive")
            .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_ACTIVITY_NEW_TASK)
        startActivity(intent)
        return true
    }

    override fun onDestroy() {
        stopRinging()
        setProximity(false)
        releaseMulticastLock()
        super.onDestroy()
    }

    private fun acquireMulticastLock() {
        if (multicastLock?.isHeld == true) return
        val wifi = applicationContext.getSystemService(Context.WIFI_SERVICE) as WifiManager
        multicastLock = wifi.createMulticastLock("socket-chat-discovery").apply {
            setReferenceCounted(false)
            acquire()
        }
    }

    private fun releaseMulticastLock() {
        multicastLock?.let { if (it.isHeld) it.release() }
        multicastLock = null
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
}
