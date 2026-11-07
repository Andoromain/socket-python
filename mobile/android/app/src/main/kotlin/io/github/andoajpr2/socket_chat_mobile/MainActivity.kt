package io.github.andoajpr2.socket_chat_mobile

import android.content.Intent
import android.os.Build
import android.os.Bundle
import android.view.WindowManager
import io.flutter.embedding.android.FlutterActivity

/**
 * L'écran de Socket Chat. Il se rattache au moteur Flutter de l'application
 * ([SocketChatApplication]) : le fermer n'arrête pas la messagerie.
 *
 * Ouvert depuis une notification, il transmet l'action à Dart : ouvrir une conversation,
 * répondre à un appel ; pendant un appel, il s'affiche par-dessus l'écran verrouillé.
 */
class MainActivity : FlutterActivity() {
    private val platform get() = (application as SocketChatApplication).platform

    override fun getCachedEngineId(): String = SocketChatApplication.ENGINE_ID

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        platform.attach(this)
        // Écran visible : Android autorise le démarrage du service au premier plan.
        if (Prefs.keepRunning(this)) MessagingService.start(this)
        handle(intent)
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        handle(intent)
    }

    override fun onDestroy() {
        platform.detach(this)
        super.onDestroy()
    }

    private fun handle(intent: Intent?) {
        if (intent == null) return
        val contact = intent.getStringExtra(EXTRA_CONTACT)
        val call = intent.getStringExtra(EXTRA_CALL)
        when {
            intent.getBooleanExtra(EXTRA_ANSWER, false) && call != null -> {
                showOverLockScreen(true)
                platform.pushAction(mapOf("type" to "answer", "call" to call))
            }
            call != null -> showOverLockScreen(true) // notification plein écran d'un appel entrant
            contact != null -> platform.pushAction(mapOf("type" to "open", "contact" to contact))
        }
        // Une action n'est traitée qu'une fois (pas de nouveau à la recréation de l'écran).
        intent.removeExtra(EXTRA_CONTACT)
        intent.removeExtra(EXTRA_CALL)
        intent.removeExtra(EXTRA_ANSWER)
    }

    /** Pendant un appel : écran allumé et affiché par-dessus le verrouillage. */
    fun showOverLockScreen(on: Boolean) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O_MR1) {
            setShowWhenLocked(on)
            setTurnScreenOn(on)
        } else {
            @Suppress("DEPRECATION")
            val flags = WindowManager.LayoutParams.FLAG_SHOW_WHEN_LOCKED or
                WindowManager.LayoutParams.FLAG_TURN_SCREEN_ON
            if (on) window.addFlags(flags) else window.clearFlags(flags)
        }
    }

    companion object {
        const val EXTRA_CONTACT = "socket_chat.contact"
        const val EXTRA_CALL = "socket_chat.call"
        const val EXTRA_ANSWER = "socket_chat.answer"
    }
}
