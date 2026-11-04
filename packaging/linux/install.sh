#!/bin/sh
# Installe (ou désinstalle avec --uninstall) Socket Chat pour l'utilisateur courant,
# à partir de l'archive Linux produite par PyInstaller. Aucun droit root nécessaire.
set -eu

HERE=$(cd "$(dirname "$0")" && pwd)
DATA_HOME=${XDG_DATA_HOME:-$HOME/.local/share}
APP_DIR="$DATA_HOME/socket-chat"
BIN_DIR="$HOME/.local/bin"
DESKTOP_FILE="$DATA_HOME/applications/socket-chat.desktop"
ICON_FILE="$DATA_HOME/icons/hicolor/256x256/apps/socket-chat.png"

if [ "${1:-}" = "--uninstall" ]; then
    rm -rf "$APP_DIR"
    rm -f "$BIN_DIR/socket-chat" "$DESKTOP_FILE" "$ICON_FILE"
    echo "Socket Chat a été désinstallé."
    exit 0
fi

if [ ! -x "$HERE/socket-chat/socket-chat" ]; then
    echo "Erreur : dossier socket-chat/ introuvable à côté de ce script." >&2
    exit 1
fi

rm -rf "$APP_DIR"
mkdir -p "$APP_DIR" "$BIN_DIR" "$(dirname "$DESKTOP_FILE")" "$(dirname "$ICON_FILE")"
cp -R "$HERE/socket-chat/." "$APP_DIR/"
ln -sf "$APP_DIR/socket-chat" "$BIN_DIR/socket-chat"
cp "$HERE/socket-chat.png" "$ICON_FILE"
sed "s|^Exec=.*|Exec=$APP_DIR/socket-chat|" "$HERE/socket-chat.desktop" > "$DESKTOP_FILE"
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$(dirname "$DESKTOP_FILE")" || true

echo "Socket Chat est installé. Lancez-le depuis le menu des applications ou avec : socket-chat"
case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *) echo "Remarque : ajoutez $BIN_DIR à votre PATH pour utiliser la commande socket-chat." ;;
esac
