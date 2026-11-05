"""Script d'installation Linux : installation, mise à jour par-dessus, désinstallation."""

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
LINUX = ROOT / "packaging" / "linux"

# Faux programme : une copie de perl, exécutable autonome qui fonctionne sous n'importe
# quel nom (contrairement à sleep, qui est un binaire multi-commandes avec uutils).
PERL = shutil.which("perl")
SLEEP_ARGS = ["-e", "sleep 300"]

pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux") or not shutil.which("pgrep") or not PERL,
    reason="script propre à Linux (pgrep et perl requis)",
)


def make_bundle(tmp_path: Path, version: str) -> Path:
    """Archive extraite factice : le « programme » est une copie de perl."""
    bundle = tmp_path / f"bundle-{version}"
    app = bundle / "socket-chat"
    app.mkdir(parents=True)
    shutil.copy(PERL, app / "socket-chat")
    (app / "VERSION").write_text(version)
    for name in ("install.sh", "socket-chat.desktop"):
        shutil.copy(LINUX / name, bundle / name)
    shutil.copy(ROOT / "src" / "socket_chat" / "resources" / "icon.png", bundle / "socket-chat.png")
    return bundle


def run_install(bundle: Path, home: Path, *args: str) -> str:
    env = {**os.environ, "HOME": str(home), "XDG_DATA_HOME": "", "PATH": os.environ["PATH"]}
    result = subprocess.run(
        ["sh", str(bundle / "install.sh"), *args],
        env=env,
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    return result.stdout


def test_install_update_and_uninstall(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    data = home / ".local" / "share"
    app_dir = data / "socket-chat"
    user_data = data / "SocketChat" / "SocketChat"
    user_data.mkdir(parents=True)
    (user_data / "history.sqlite3").write_text("historique")

    out = run_install(make_bundle(tmp_path, "3.1.0"), home)
    assert "est installé" in out
    assert (app_dir / "VERSION").read_text() == "3.1.0"
    assert (home / ".local" / "bin" / "socket-chat").is_symlink()
    desktop = (data / "applications" / "socket-chat.desktop").read_text()
    assert f"Exec={app_dir}/socket-chat" in desktop

    # L'application tourne (elle reste dans la zone de notification) pendant la mise à jour.
    running = subprocess.Popen([str(app_dir / "socket-chat"), *SLEEP_ARGS])
    try:
        time.sleep(0.2)
        assert running.poll() is None, "le faux programme doit tourner"
        out = run_install(make_bundle(tmp_path, "3.1.1"), home)
        assert "mis à jour" in out and "relancez-le" in out
        running.wait(timeout=10)  # arrêtée par le script
    finally:
        if running.poll() is None:
            running.kill()
    assert (app_dir / "VERSION").read_text() == "3.1.1"
    assert (user_data / "history.sqlite3").read_text() == "historique"

    # Sans instance ouverte, pas de message superflu.
    latest = make_bundle(tmp_path, "3.1.2")
    out = run_install(latest, home)
    assert "mis à jour" in out and "relancez-le" not in out

    out = run_install(latest, home, "--uninstall")
    assert "désinstallé" in out
    assert not app_dir.exists()
    assert (user_data / "history.sqlite3").exists()  # les données de l'utilisateur restent


def test_other_processes_named_socket_chat_are_left_alone(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    run_install(make_bundle(tmp_path, "3.1.0"), home)
    other_dir = tmp_path / "ailleurs"
    other_dir.mkdir()
    other = other_dir / "socket-chat"
    shutil.copy(PERL, other)
    stranger = subprocess.Popen([str(other), *SLEEP_ARGS])
    try:
        time.sleep(0.2)
        assert stranger.poll() is None, "le faux programme doit tourner"
        out = run_install(make_bundle(tmp_path, "3.1.1"), home)
        assert "relancez-le" not in out
        assert stranger.poll() is None  # pas installé par ce script : non touché
    finally:
        stranger.kill()
        stranger.wait()


def test_relaunch_after_update(tmp_path):
    """--relaunch (mise à jour automatique) redémarre la nouvelle version."""
    home = tmp_path / "home"
    home.mkdir()
    marker = tmp_path / "relance.txt"
    bundle = make_bundle(tmp_path, "3.2.0")
    program = bundle / "socket-chat" / "socket-chat"
    program.unlink()
    program.write_text(f'#!/bin/sh\necho "$0" > "{marker}"\n')
    program.chmod(0o755)
    out = run_install(bundle, home, "--relaunch")
    assert "relancé" in out and "relancez-le" not in out
    deadline = time.monotonic() + 5
    while not marker.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    expected = home / ".local" / "share" / "socket-chat" / "socket-chat"
    assert marker.read_text().strip() == str(expected)


def test_relaunch_lets_the_app_exit_by_itself(tmp_path):
    """Mise à jour automatique : l'application qui se ferme n'est pas tuée."""
    home = tmp_path / "home"
    home.mkdir()
    run_install(make_bundle(tmp_path, "3.2.0"), home)
    app = home / ".local" / "share" / "socket-chat" / "socket-chat"
    # Simule l'application qui se ferme d'elle-même en 1 s après avoir lancé la mise à jour.
    closing = subprocess.Popen([str(app), "-e", "sleep 1"])
    try:
        time.sleep(0.2)
        assert closing.poll() is None
        bundle = make_bundle(tmp_path, "3.2.1")
        program = bundle / "socket-chat" / "socket-chat"
        program.unlink()
        program.write_text("#!/bin/sh\nexit 0\n")
        program.chmod(0o755)
        run_install(bundle, home, "--relaunch")
        assert closing.wait(timeout=10) == 0  # sortie normale, pas de signal
    finally:
        if closing.poll() is None:
            closing.kill()
