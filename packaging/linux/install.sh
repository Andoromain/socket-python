#!/bin/sh
# Installe, met à jour (en relançant simplement le script d'une version plus récente)
# ou désinstalle (--uninstall) Socket Chat pour l'utilisateur courant, à partir de
# l'archive Linux produite par PyInstaller. Aucun droit root nécessaire.
# --relaunch : démarre Socket Chat après l'installation (mise à jour automatique).
# Les préférences et l'historique (~/.config/SocketChat, ~/.local/share/SocketChat)
# ne sont jamais touchés.
set -eu

HERE=$(cd "$(dirname "$0")" && pwd)
DATA_HOME=${XDG_DATA_HOME:-$HOME/.local/share}
APP_DIR="$DATA_HOME/socket-chat"
BIN_DIR="$HOME/.local/bin"
DESKTOP_FILE="$DATA_HOME/applications/socket-chat.desktop"
ICON_FILE="$DATA_HOME/icons/hicolor/256x256/apps/socket-chat.png"

# Arrête l'instance installée si elle tourne (elle reste souvent dans la zone de
# notification). Seuls les processus de cet utilisateur lancés depuis $APP_DIR sont visés.
stop_running() {
    [ -x "$APP_DIR/socket-chat" ] || return 1
    target=$(readlink -f "$APP_DIR/socket-chat")
    pids=""
    for pid in $(pgrep -u "$(id -u)" -x socket-chat 2>/dev/null); do
        if [ "$(readlink -f "/proc/$pid/exe" 2>/dev/null)" = "$target" ]; then
            pids="$pids $pid"
        fi
    done
    [ -n "$pids" ] || return 1
    # Mise à jour automatique : l'application est en train de se fermer d'elle-même ;
    # on lui laisse le temps de prévenir ses correspondants avant d'intervenir.
    if [ "$RELAUNCH" = 1 ] && wait_exit $pids; then
        return 0
    fi
    # shellcheck disable=SC2086
    kill $pids 2>/dev/null || true
    wait_exit $pids && return 0
    # shellcheck disable=SC2086
    kill -9 $alive 2>/dev/null || true
    return 0
}

# Attend (5 s au plus) la fin des processus donnés ; laisse les survivants dans $alive.
wait_exit() {
    for _ in 1 2 3 4 5 6 7 8 9 10; do
        alive=""
        for pid in "$@"; do
            # Un processus zombie (« Z ») est déjà arrêté : il attend seulement son parent.
            state=$(cut -d' ' -f3 "/proc/$pid/stat" 2>/dev/null || true)
            [ -n "$state" ] && [ "$state" != Z ] && alive="$alive $pid"
        done
        [ -z "$alive" ] && return 0
        sleep 0.5
    done
    return 1
}

RELAUNCH=0
[ "${1:-}" = "--relaunch" ] && RELAUNCH=1

if [ "${1:-}" = "--uninstall" ]; then
    stop_running || true
    rm -rf "$APP_DIR"
    rm -f "$BIN_DIR/socket-chat" "$DESKTOP_FILE" "$ICON_FILE"
    echo "Socket Chat a été désinstallé."
    exit 0
fi

if [ ! -x "$HERE/socket-chat/socket-chat" ]; then
    echo "Erreur : dossier socket-chat/ introuvable à côté de ce script." >&2
    exit 1
fi

UPDATE=0
[ -x "$APP_DIR/socket-chat" ] && UPDATE=1
WAS_RUNNING=0
stop_running && WAS_RUNNING=1

rm -rf "$APP_DIR"
mkdir -p "$APP_DIR" "$BIN_DIR" "$(dirname "$DESKTOP_FILE")" "$(dirname "$ICON_FILE")"
cp -R "$HERE/socket-chat/." "$APP_DIR/"
ln -sf "$APP_DIR/socket-chat" "$BIN_DIR/socket-chat"
cp "$HERE/socket-chat.png" "$ICON_FILE"
sed "s|^Exec=.*|Exec=$APP_DIR/socket-chat|" "$HERE/socket-chat.desktop" > "$DESKTOP_FILE"
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$(dirname "$DESKTOP_FILE")" || true

if [ "$UPDATE" = 1 ]; then
    echo "Socket Chat a été mis à jour (préférences et historique conservés)."
else
    echo "Socket Chat est installé."
fi
if [ "$RELAUNCH" = 1 ]; then
    # Détaché du terminal et de l'application qui a lancé la mise à jour.
    if command -v setsid >/dev/null 2>&1; then
        setsid nohup "$APP_DIR/socket-chat" >/dev/null 2>&1 </dev/null &
    else
        nohup "$APP_DIR/socket-chat" >/dev/null 2>&1 </dev/null &
    fi
    echo "Socket Chat a été relancé."
elif [ "$WAS_RUNNING" = 1 ]; then
    echo "Socket Chat était ouvert : il a été fermé pour la mise à jour, relancez-le."
fi
echo "Lancez-le depuis le menu des applications ou avec : socket-chat"
case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *) echo "Remarque : ajoutez $BIN_DIR à votre PATH pour utiliser la commande socket-chat." ;;
esac
