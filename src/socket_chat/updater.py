"""Mise à jour automatique.

Les installeurs sont publiés avec un fichier ``latest.json`` dans un dépôt GitHub
public dédié (le code reste privé). L'application lit ce manifeste, compare la
version, télécharge le fichier de sa plate-forme, vérifie taille et SHA-256, puis
lance l'installation silencieuse qui la relance ensuite.

La source est configurable (clé ``update/url``) : adresse HTTPS, ``file://`` ou chemin
de dossier réseau, pour héberger les mises à jour ailleurs sans nouvelle version.
"""

from __future__ import annotations

import hashlib
import json
import logging
import platform
import re
import shutil
import sys
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, urljoin

from PySide6.QtCore import QObject, QProcess, QTimer, QUrl, Signal
from PySide6.QtNetwork import (
    QNetworkAccessManager,
    QNetworkProxyFactory,
    QNetworkReply,
    QNetworkRequest,
)

from .protocol import safe_file_name

log = logging.getLogger(__name__)

RELEASES_REPO = "andoajpr2/socket-chat-releases"
DOWNLOAD_PREFIX = "socket-chat-maj-"
DEFAULT_UPDATE_URL = f"https://github.com/{RELEASES_REPO}/releases/latest/download/latest.json"

#: Première vérification après le démarrage, puis intervalle (ms).
FIRST_CHECK_MS = 30_000
CHECK_INTERVAL_MS = 6 * 3600 * 1000
#: Taille maximale du manifeste et d'un installeur (octets).
MAX_MANIFEST_SIZE = 256 * 1024
MAX_ASSET_SIZE = 1024**3
ALLOWED_SCHEMES = {"https", "file"}

_VERSION = re.compile(r"(\d+)\.(\d+)\.(\d+)")
_SHA256 = re.compile(r"[0-9a-f]{64}")


class ManifestError(ValueError):
    """Manifeste de mise à jour absent, illisible ou invalide."""


# -- Partie pure (sans Qt) ---------------------------------------------------
def parse_version(text: str) -> tuple[int, int, int]:
    match = _VERSION.fullmatch(text.strip())
    if match is None:
        raise ValueError(f"Version invalide : {text!r}")
    major, minor, patch = (int(part) for part in match.groups())
    return major, minor, patch


def is_newer(candidate: str, current: str) -> bool:
    return parse_version(candidate) > parse_version(current)


def platform_key() -> str | None:
    """Identifiant de plate-forme des fichiers publiés (``None`` si non prise en charge)."""
    machine = platform.machine().lower()
    if sys.platform == "win32" and machine in ("amd64", "x86_64"):
        return "windows-x64"
    if sys.platform.startswith("linux") and machine in ("x86_64", "amd64"):
        return "linux-x86_64"
    return None


def can_self_update() -> bool:
    """Vrai pour l'exécutable autonome (nos installeurs) sur une plate-forme prise en charge."""
    return bool(getattr(sys, "frozen", False)) and platform_key() is not None


@dataclass(frozen=True)
class Asset:
    name: str
    size: int
    sha256: str
    url: str


@dataclass(frozen=True)
class Manifest:
    version: str
    published: str
    notes: str
    assets: dict[str, Asset]

    def asset_for(self, key: str | None) -> Asset | None:
        return self.assets.get(key) if key else None


def parse_manifest(data: bytes, manifest_url: str) -> Manifest:
    """Valide un ``latest.json`` ; les fichiers sont cherchés à côté du manifeste."""
    if len(data) > MAX_MANIFEST_SIZE:
        raise ManifestError("Manifeste trop volumineux.")
    try:
        payload = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ManifestError("Manifeste illisible.") from exc
    if not isinstance(payload, dict):
        raise ManifestError("Manifeste invalide.")

    version = payload.get("version")
    if not isinstance(version, str):
        raise ManifestError("Version absente.")
    try:
        parse_version(version)
    except ValueError as exc:
        raise ManifestError(str(exc)) from exc
    published = payload.get("published", "")
    notes = payload.get("notes", "")
    if not isinstance(published, str) or len(published) > 40:
        raise ManifestError("Date de publication invalide.")
    if not isinstance(notes, str) or len(notes) > 20_000:
        raise ManifestError("Notes de version invalides.")

    base = asset_base(manifest_url, version)
    raw_assets = payload.get("assets")
    if not isinstance(raw_assets, dict) or not raw_assets:
        raise ManifestError("Aucun fichier dans le manifeste.")
    assets: dict[str, Asset] = {}
    for key, raw in raw_assets.items():
        if not isinstance(key, str) or not isinstance(raw, dict):
            raise ManifestError("Fichier invalide dans le manifeste.")
        name, size, digest = raw.get("name"), raw.get("size"), raw.get("sha256")
        if not isinstance(name, str) or not name or safe_file_name(name) != name:
            raise ManifestError(f"Nom de fichier refusé : {name!r}")
        if not isinstance(size, int) or isinstance(size, bool) or not 0 < size <= MAX_ASSET_SIZE:
            raise ManifestError(f"Taille invalide pour {name}.")
        if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
            raise ManifestError(f"Empreinte invalide pour {name}.")
        assets[key] = Asset(name, size, digest, urljoin(base, quote(name)))
    return Manifest(version, published, notes, assets)


def asset_base(manifest_url: str, version: str) -> str:
    """Adresse à laquelle chercher les fichiers d'une version.

    Sur GitHub, « releases/latest/download/ » suit toujours la dernière publication : on
    vise la version exacte annoncée, pour ne pas mélanger deux versions si une nouvelle
    sort entre la lecture du manifeste et le téléchargement.
    """
    marker = "/releases/latest/download/"
    if marker in manifest_url:
        return manifest_url.replace(marker, f"/releases/download/v{version}/")
    return manifest_url


def normalize_source(source: str) -> str:
    """Accepte une URL ou un chemin (``\\\\serveur\\partage\\latest.json``, ``/srv/maj``)."""
    source = source.strip()
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]+://", source):
        url = source
    else:
        url = QUrl.fromLocalFile(source).toString()
    if not url.endswith(".json"):
        url = url.rstrip("/") + "/latest.json"
    return url


def check_scheme(url: str) -> None:
    scheme = QUrl(url).scheme().lower()
    if scheme not in ALLOWED_SCHEMES:
        raise ManifestError(f"Adresse de mise à jour refusée (« {scheme} ») : HTTPS ou fichier.")


# -- Réseau (Qt) ---------------------------------------------------------------
def _network_manager(parent: QObject) -> QNetworkAccessManager:
    QNetworkProxyFactory.setUseSystemConfiguration(True)  # proxy de l'entreprise
    manager = QNetworkAccessManager(parent)
    manager.setRedirectPolicy(QNetworkRequest.RedirectPolicy.NoLessSafeRedirectPolicy)
    return manager


def _request(url: str) -> QNetworkRequest:
    request = QNetworkRequest(QUrl(url))
    request.setHeader(QNetworkRequest.KnownHeaders.UserAgentHeader, "SocketChat-Updater")
    request.setTransferTimeout(60_000)
    return request


class UpdateChecker(QObject):
    """Vérifie périodiquement la présence d'une version plus récente."""

    update_available = Signal(object)  # Manifest
    up_to_date = Signal()
    failed = Signal(str)

    def __init__(self, url: str, current_version: str, parent: QObject | None = None):
        super().__init__(parent)
        self.url = normalize_source(url)
        self.current_version = current_version
        self.latest: Manifest | None = None
        #: Vrai si la dernière vérification a été demandée par l'utilisateur.
        self.manual = False
        self._reply: QNetworkReply | None = None
        self._manager = _network_manager(self)
        self._timer = QTimer(self, singleShot=True)
        self._timer.timeout.connect(self.check_now)

    def start(self) -> None:
        self._timer.start(FIRST_CHECK_MS)

    def stop(self) -> None:
        self._timer.stop()
        if self._reply is not None:
            self._reply.abort()

    def check_now(self, *, manual: bool = False) -> None:
        self.manual = manual
        self._timer.start(CHECK_INTERVAL_MS)  # prochaine vérification automatique
        if self._reply is not None:
            return
        try:
            check_scheme(self.url)
        except ManifestError as exc:
            self.failed.emit(str(exc))
            return
        log.info("Recherche de mise à jour : %s", self.url)
        self._reply = self._manager.get(_request(self.url))
        self._reply.finished.connect(self._on_finished)

    def _on_finished(self) -> None:
        reply, self._reply = self._reply, None
        assert reply is not None
        reply.deleteLater()
        if reply.error() != QNetworkReply.NetworkError.NoError:
            log.info("Mise à jour : vérification impossible (%s)", reply.errorString())
            self.failed.emit(f"Impossible de vérifier les mises à jour : {reply.errorString()}")
            return
        try:
            manifest = parse_manifest(bytes(reply.readAll().data()), self.url)
        except ManifestError as exc:
            log.warning("Manifeste de mise à jour invalide : %s", exc)
            self.failed.emit(f"Manifeste de mise à jour invalide : {exc}")
            return
        self.latest = manifest
        if is_newer(manifest.version, self.current_version):
            log.info("Nouvelle version disponible : %s", manifest.version)
            self.update_available.emit(manifest)
        else:
            self.up_to_date.emit()


class UpdateDownloader(QObject):
    """Télécharge un fichier publié et vérifie taille et empreinte."""

    progress = Signal(int, int)  # reçus, total
    finished = Signal(str)  # chemin du fichier vérifié
    failed = Signal(str)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._manager = _network_manager(self)
        self._reply: QNetworkReply | None = None
        self._file = None
        self._sha = hashlib.sha256()
        self._received = 0
        self._asset: Asset | None = None
        self._target: Path | None = None
        self._cancelled = False

    @property
    def running(self) -> bool:
        return self._reply is not None

    def start(self, asset: Asset, directory: Path | None = None) -> None:
        if self.running:
            return
        try:
            check_scheme(asset.url)
        except ManifestError as exc:
            self.failed.emit(str(exc))
            return
        directory = Path(directory or tempfile.mkdtemp(prefix=DOWNLOAD_PREFIX))
        directory.mkdir(parents=True, exist_ok=True)
        self._asset = asset
        self._target = directory / asset.name
        self._part = directory / (asset.name + ".part")
        self._file = self._part.open("wb")
        self._sha = hashlib.sha256()
        self._received = 0
        self._cancelled = False
        log.info("Téléchargement de la mise à jour : %s", asset.url)
        self._reply = self._manager.get(_request(asset.url))
        self._reply.readyRead.connect(self._on_ready_read)
        self._reply.finished.connect(self._on_finished)

    def cancel(self) -> None:
        if self._reply is not None:
            self._cancelled = True
            self._reply.abort()

    def _on_ready_read(self) -> None:
        assert self._reply is not None and self._file is not None and self._asset is not None
        data = bytes(self._reply.readAll().data())
        self._received += len(data)
        if self._received > self._asset.size:
            self._fail("Le fichier téléchargé est plus gros qu'annoncé.")
            return
        self._file.write(data)
        self._sha.update(data)
        self.progress.emit(self._received, self._asset.size)

    def _on_finished(self) -> None:
        reply = self._reply
        if reply is None:
            return  # déjà traité par _fail
        self._on_ready_read_safe()
        self._reply = None
        reply.deleteLater()
        assert self._file is not None and self._asset is not None and self._target is not None
        self._file.close()
        if self._cancelled:
            self._part.unlink(missing_ok=True)
            self.failed.emit("Téléchargement annulé.")
            return
        if reply.error() != QNetworkReply.NetworkError.NoError:
            self._part.unlink(missing_ok=True)
            self.failed.emit(f"Téléchargement impossible : {reply.errorString()}")
            return
        if self._received != self._asset.size or self._sha.hexdigest() != self._asset.sha256:
            self._part.unlink(missing_ok=True)
            self.failed.emit(
                "Le fichier téléchargé est corrompu (empreinte incorrecte) : mise à jour annulée."
            )
            return
        self._part.replace(self._target)
        self.finished.emit(str(self._target))

    def _on_ready_read_safe(self) -> None:
        if self._reply is not None and self._reply.bytesAvailable():
            self._on_ready_read()

    def _fail(self, message: str) -> None:
        reply, self._reply = self._reply, None
        if reply is not None:
            reply.abort()
            reply.deleteLater()
        if self._file is not None:
            self._file.close()
        self._part.unlink(missing_ok=True)
        self.failed.emit(message)


def cleanup_downloads(temp_dir: str | Path | None = None) -> int:
    """Supprime les téléchargements de mises à jour précédentes ; renvoie leur nombre.

    Un dossier encore utilisé (installeur Windows en cours) est simplement ignoré.
    """
    removed = 0
    for folder in Path(temp_dir or tempfile.gettempdir()).glob(f"{DOWNLOAD_PREFIX}*"):
        if folder.is_dir():
            shutil.rmtree(folder, ignore_errors=True)
            removed += not folder.exists()
    return removed


def _extract_archive(archive: Path) -> Path:
    """Extrait l'archive Linux et renvoie le chemin de son ``install.sh``."""
    destination = archive.parent / "extrait"
    with tarfile.open(archive) as tar:
        tar.extractall(destination, filter="data")  # refuse chemins absolus et « .. »
    scripts = sorted(destination.glob("*/install.sh"))
    if len(scripts) != 1:
        raise ManifestError("Archive de mise à jour inattendue (install.sh introuvable).")
    return scripts[0]


def install_update(path: str | Path) -> None:
    """Lance l'installation silencieuse détachée ; l'application doit ensuite se fermer.

    Lève ``ManifestError`` (ou ``OSError``) si le lancement est impossible.
    """
    path = Path(path)
    if sys.platform == "win32":
        args = ["/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/RELAUNCH=1"]
        ok = QProcess.startDetached(str(path), args)
    else:
        script = _extract_archive(path)
        ok = QProcess.startDetached("sh", [str(script), "--relaunch"], str(script.parent))
    if isinstance(ok, tuple):  # PySide6 renvoie (succès, pid)
        ok = ok[0]
    if not ok:
        raise ManifestError("Impossible de lancer l'installation de la mise à jour.")
    log.info("Installation de la mise à jour lancée : %s", path)
