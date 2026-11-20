package io.github.andoajpr2.socket_chat_mobile

import android.content.Context
import android.content.RestrictionsManager
import android.net.wifi.WifiManager
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel
import java.net.Inet4Address
import java.net.NetworkInterface

/**
 * Fonctions réseau appelées par lib/app/platform.dart (canal « socket_chat/network »).
 *
 * Android ignore le broadcast UDP reçu tant qu'aucune application ne détient un verrou
 * multicast Wi-Fi : sans lui, les postes du réseau ne seraient jamais détectés.
 *
 * Lit aussi la configuration gérée par l'administrateur (MDM, voir
 * res/xml/app_restrictions.xml) : réseaux autorisés.
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
                    "managedAllowedNetworks" -> result.success(managedAllowedNetworks())
                    else -> result.notImplemented()
                }
            }
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

    /** Réseaux autorisés fixés par l'administrateur (configuration gérée), ou null. */
    private fun managedAllowedNetworks(): String? = try {
        val manager = getSystemService(Context.RESTRICTIONS_SERVICE) as RestrictionsManager
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
}
