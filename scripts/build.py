"""Construit l'application autonome pour la plate-forme courante.

Usage : python scripts/build.py
Résultat :
  * Windows : dist/socket-chat/ puis, si Inno Setup est installé,
              dist/socket-chat-<version>-windows-setup.exe
  * Linux   : dist/socket-chat-<version>-linux-x86_64.tar.gz
              (contient install.sh pour une installation utilisateur)
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
sys.path.insert(0, str(ROOT / "src"))

from socket_chat import __version__  # noqa: E402


def run(*cmd: str) -> None:
    print("+", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True, cwd=ROOT)


def pyinstaller() -> Path:
    run(
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
        "--distpath", str(DIST), "--workpath", str(ROOT / "build"),
        str(ROOT / "packaging" / "socket-chat.spec"),
    )  # fmt: skip
    return DIST / "socket-chat"


def find_iscc() -> str | None:
    found = shutil.which("iscc")
    if found:
        return found
    for base in (os.environ.get("PROGRAMFILES(X86)"), os.environ.get("PROGRAMFILES")):
        if base and (candidate := Path(base) / "Inno Setup 6" / "ISCC.exe").exists():
            return str(candidate)
    return None


def build_windows() -> None:
    pyinstaller()
    iscc = find_iscc()
    if iscc is None:
        print("Inno Setup introuvable : seul le dossier dist/socket-chat a été produit.")
        return
    run(iscc, f"/DAppVersion={__version__}", str(ROOT / "packaging" / "windows" / "installer.iss"))


def build_linux() -> None:
    app_dir = pyinstaller()
    machine = platform.machine().lower() or "x86_64"
    name = f"socket-chat-{__version__}-linux-{machine}"
    archive = DIST / f"{name}.tar.gz"
    linux = ROOT / "packaging" / "linux"
    resources = ROOT / "src" / "socket_chat" / "resources"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(app_dir, arcname=f"{name}/socket-chat")
        tar.add(linux / "install.sh", arcname=f"{name}/install.sh")
        tar.add(linux / "socket-chat.desktop", arcname=f"{name}/socket-chat.desktop")
        tar.add(resources / "icon.png", arcname=f"{name}/socket-chat.png")
    print("Archive créée :", archive)


if __name__ == "__main__":
    if sys.platform == "win32":
        build_windows()
    elif sys.platform.startswith("linux"):
        build_linux()
    else:
        sys.exit(f"Plate-forme non prise en charge : {sys.platform}")
