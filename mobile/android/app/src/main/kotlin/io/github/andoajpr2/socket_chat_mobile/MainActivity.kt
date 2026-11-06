package io.github.andoajpr2.socket_chat_mobile

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.net.wifi.WifiManager
import android.os.Build
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
 *   téléchargé et vérifié. Android demande toujours la confirmation de l'utilisateur.
 */
class MainActivity : FlutterActivity() {
    private var multicastLock: WifiManager.MulticastLock? = null

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
