# -*- mode: python ; coding: utf-8 -*-
# Spécification PyInstaller : `pyinstaller packaging/socket-chat.spec`
# Produit dist/socket-chat/ (dossier autonome, démarrage rapide).
import sys
from pathlib import Path

ROOT = Path(SPECPATH).parent
RESOURCES = ROOT / "src" / "socket_chat" / "resources"

a = Analysis(
    [str(ROOT / "packaging" / "launcher.py")],
    pathex=[str(ROOT / "src")],
    datas=[(str(RESOURCES), "socket_chat/resources")],
    excludes=["tkinter", "unittest", "pydoc", "PySide6.QtQml", "PySide6.QtQuick"],
    noarchive=False,
)


def _needed(dest: str) -> bool:
    """Allège Qt WebEngine : français et anglais seulement, sans outils de développement
    ni ressources des versions de débogage de Qt (*.debug.pak, *.debug.bin, sous Windows)."""
    path = dest.replace("\\", "/")
    name = path.rsplit("/", 1)[-1]
    if "/qtwebengine_locales/" in path:
        return name in ("fr.pak", "en-US.pak")
    if "/translations/" in path:
        return name.endswith("_fr.qm")
    if ".debug." in name:
        return False
    return name != "qtwebengine_devtools_resources.pak"


a.datas = [entry for entry in a.datas if _needed(entry[0])]
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="socket-chat",
    console=False,
    icon=str(RESOURCES / "icon.ico"),
    version=str(ROOT / "packaging" / "windows" / "version_info.txt") if sys.platform == "win32" else None,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="socket-chat", upx=False)
