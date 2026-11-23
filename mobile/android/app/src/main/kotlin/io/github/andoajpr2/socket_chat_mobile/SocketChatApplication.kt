package io.github.andoajpr2.socket_chat_mobile

import android.app.Application
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.embedding.engine.FlutterEngineCache
import io.flutter.embedding.engine.dart.DartExecutor

/**
 * Démarre la messagerie (le code Dart) avec le processus, sans attendre l'écran : le
 * moteur Flutter est créé ici et mis en cache, puis l'activité s'y rattache quand elle
 * s'ouvre. Fermer l'écran ne l'arrête pas ; le service au premier plan
 * ([MessagingService]) empêche Android d'arrêter le processus. Socket Chat reçoit ainsi
 * messages et appels application fermée, et même après un redémarrage ([BootReceiver]).
 */
class SocketChatApplication : Application() {
    lateinit var platform: Platform
        private set

    override fun onCreate() {
        super.onCreate()
        Notifications.createChannels(this)
        val engine = FlutterEngine(this)
        platform = Platform(this, engine)
        engine.dartExecutor.executeDartEntrypoint(DartExecutor.DartEntrypoint.createDefault())
        FlutterEngineCache.getInstance().put(ENGINE_ID, engine)
    }

    companion object {
        const val ENGINE_ID = "socket_chat"
    }
}
