"""Transferts de fichiers entre deux postes.

Déroulement (le fichier est proposé comme un message, voir ``Messenger``) :

1. l'expéditeur envoie ``file_offer`` (nom, taille, aperçu éventuel) ;
2. le destinataire accepte (en choisissant où enregistrer) ou refuse : ``file_answer`` ;
3. l'expéditeur envoie des ``file_chunk`` de 32 Kio, au rythme où la socket se vide,
   puis ``file_end`` avec l'empreinte SHA-256 ;
4. le destinataire vérifie taille et empreinte, puis renomme le fichier ``.part``.

Chaque côté peut interrompre avec ``file_cancel``. Une coupure de connexion fait
échouer le transfert ; le destinataire peut alors réessayer depuis le début.
"""

from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, BinaryIO

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QObject, Qt, Signal
from PySide6.QtGui import QImage, QPainter

from .network import PeerConnection
from .protocol import CHUNK_SIZE, MAX_FILE_SIZE, MAX_THUMB_SIZE, Message, MessageType
from .store import IMAGE_EXTENSIONS, FileState, Store

log = logging.getLogger(__name__)

#: Données en attente dans la socket au-delà desquelles on n'envoie plus de bloc.
HIGH_WATER = 512 * 1024
#: Fréquence des notifications de progression (octets).
PROGRESS_STEP = 256 * 1024
#: Côté le plus long de l'aperçu d'une image (pixels).
THUMB_SIDE = 360

INTEGRITY_REASON = "integrity"
MISSING_REASON = "missing"
CANCEL_MESSAGES = {
    INTEGRITY_REASON: "fichier reçu corrompu",
    MISSING_REASON: "fichier introuvable chez l'expéditeur",
}


def make_thumbnail(path: str | Path) -> bytes:
    """Aperçu JPEG d'une image (vide si ce n'en est pas une ou s'il est illisible)."""
    if Path(path).suffix.lower().lstrip(".") not in IMAGE_EXTENSIONS:
        return b""
    image = QImage(str(path))
    if image.isNull():
        return b""
    side = THUMB_SIDE
    for quality in (80, 65, 50, 40):
        scaled = image.scaled(
            side,
            side,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        if scaled.hasAlphaChannel():
            flat = QImage(scaled.size(), QImage.Format.Format_RGB32)
            flat.fill(Qt.GlobalColor.white)
            painter = QPainter(flat)
            painter.drawImage(0, 0, scaled)
            painter.end()
            scaled = flat
        data = QByteArray()
        buffer = QBuffer(data)
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        scaled.save(buffer, "JPEG", quality)
        buffer.close()
        if 0 < data.size() <= MAX_THUMB_SIZE:
            return bytes(data.data())
        side = int(side * 0.8)
    return b""


@dataclass
class _Upload:
    message_id: str
    conn: PeerConnection
    file: BinaryIO
    size: int
    offset: int = 0
    sha: Any = field(default_factory=hashlib.sha256)
    reported: int = 0


@dataclass
class _Download:
    message_id: str
    conn: PeerConnection
    file: BinaryIO
    part: Path
    destination: Path
    size: int
    received: int = 0
    sha: Any = field(default_factory=hashlib.sha256)
    reported: int = 0


class FileTransfers(QObject):
    changed = Signal(str)  # identifiant du message dont l'état a changé
    progress = Signal(str, int, int)  # identifiant, octets transférés, taille totale

    def __init__(self, store: Store, parent: QObject | None = None):
        super().__init__(parent)
        self._store = store
        self._uploads: dict[str, _Upload] = {}
        self._downloads: dict[str, _Download] = {}

    # -- État ------------------------------------------------------------
    @property
    def active_count(self) -> int:
        """Nombre de transferts en cours (envois et réceptions)."""
        return len(self._uploads) + len(self._downloads)

    def progress_of(self, message_id: str) -> tuple[int, int] | None:
        if (up := self._uploads.get(message_id)) is not None:
            return up.offset, up.size
        if (down := self._downloads.get(message_id)) is not None:
            return down.received, down.size
        return None

    # -- Actions locales -------------------------------------------------
    def accept(self, message_id: str, destination: Path, conn: PeerConnection) -> None:
        self._abort_download(message_id)
        destination = Path(destination)
        part = destination.with_name(destination.name + ".part")
        record = self._store.message(message_id)
        assert record is not None and record.file is not None
        destination.parent.mkdir(parents=True, exist_ok=True)
        handle = part.open("wb")
        self._downloads[message_id] = _Download(
            message_id, conn, handle, part, destination, record.file.size
        )
        self._set_state(message_id, FileState.ACCEPTED, path=str(destination))
        conn.send(Message.file_answer(message_id, True))
        if record.file.size == 0:
            self._emit_progress(self._downloads[message_id], force=True)

    def cancel(self, message_id: str, conn: PeerConnection | None) -> None:
        active = message_id in self._uploads or message_id in self._downloads
        self._abort_upload(message_id)
        self._abort_download(message_id)
        record = self._store.message(message_id)
        if record is None or record.file is None:
            return
        if record.file.state in (FileState.DONE, FileState.DECLINED, FileState.CANCELLED):
            return
        if conn is not None and conn.is_ready:
            conn.send(Message.file_cancel(message_id))
        if active or record.file.state is FileState.OFFERED:
            self._set_state(message_id, FileState.CANCELLED)

    def connection_closed(self, conn: PeerConnection) -> None:
        for upload in [u for u in self._uploads.values() if u.conn is conn]:
            self._abort_upload(upload.message_id)
            self._set_state(upload.message_id, FileState.FAILED)
        for download in [d for d in self._downloads.values() if d.conn is conn]:
            self._abort_download(download.message_id)
            self._set_state(download.message_id, FileState.FAILED)

    def stop(self) -> None:
        for message_id in list(self._uploads):
            self._abort_upload(message_id)
            self._set_state(message_id, FileState.FAILED)
        for message_id in list(self._downloads):
            self._abort_download(message_id)
            self._set_state(message_id, FileState.FAILED)

    # -- Trames reçues ---------------------------------------------------
    def handle(self, conn: PeerConnection, peer_id: str, message: Message) -> None:
        record = self._store.message(message.id)
        if record is None or record.file is None or record.contact_id != peer_id:
            return
        match message.type:
            case MessageType.FILE_ANSWER if record.outgoing:
                if message.accept:
                    self._start_upload(conn, message.id)
                elif record.file.state is FileState.OFFERED:
                    self._set_state(message.id, FileState.DECLINED)
            case MessageType.FILE_CHUNK if not record.outgoing:
                self._receive_chunk(conn, message)
            case MessageType.FILE_END if not record.outgoing:
                self._finish_download(conn, message)
            case MessageType.FILE_CANCEL:
                was_active = message.id in self._uploads or message.id in self._downloads
                self._abort_upload(message.id)
                self._abort_download(message.id)
                if message.reason == INTEGRITY_REASON:
                    # Même un envoi terminé échoue si le destinataire a rejeté le fichier.
                    self._set_state(message.id, FileState.FAILED)
                    return
                if record.file.state in (FileState.DONE, FileState.DECLINED):
                    return
                if was_active or record.file.state in (FileState.OFFERED, FileState.FAILED):
                    self._set_state(message.id, FileState.CANCELLED)

    def pump(self, conn: PeerConnection) -> None:
        """Envoie des blocs tant que la socket n'est pas saturée."""
        for upload in [u for u in self._uploads.values() if u.conn is conn]:
            self._pump_one(upload)

    # -- Envoi -----------------------------------------------------------
    def _start_upload(self, conn: PeerConnection, message_id: str) -> None:
        record = self._store.message(message_id)
        assert record is not None and record.file is not None
        if record.file.state in (FileState.DECLINED, FileState.CANCELLED):
            conn.send(Message.file_cancel(message_id))
            return
        self._abort_upload(message_id)
        path = record.file.path
        try:
            if not path or os.path.getsize(path) != record.file.size:
                raise FileNotFoundError(path)
            handle = open(path, "rb")  # noqa: SIM115 - fermé par _abort_upload
        except OSError:
            log.warning("Fichier à envoyer introuvable ou modifié : %s", path)
            conn.send(Message.file_cancel(message_id, MISSING_REASON))
            self._set_state(message_id, FileState.FAILED)
            return
        upload = _Upload(message_id, conn, handle, record.file.size)
        self._uploads[message_id] = upload
        self._set_state(message_id, FileState.TRANSFERRING)
        self._pump_one(upload)

    def _pump_one(self, upload: _Upload) -> None:
        conn = upload.conn
        while upload.offset < upload.size and conn.pending_bytes < HIGH_WATER:
            if not conn.is_ready:
                return
            chunk = upload.file.read(min(CHUNK_SIZE, upload.size - upload.offset))
            if not chunk:
                conn.send(Message.file_cancel(upload.message_id, MISSING_REASON))
                self._abort_upload(upload.message_id)
                self._set_state(upload.message_id, FileState.FAILED)
                return
            upload.sha.update(chunk)
            conn.send(Message.file_chunk(upload.message_id, upload.offset, chunk))
            upload.offset += len(chunk)
            self._emit_progress(upload)
        if upload.offset >= upload.size and upload.message_id in self._uploads:
            conn.send(Message.file_end(upload.message_id, upload.sha.hexdigest()))
            self._emit_progress(upload, force=True)
            self._abort_upload(upload.message_id)
            self._set_state(upload.message_id, FileState.DONE)

    # -- Réception -------------------------------------------------------
    def _receive_chunk(self, conn: PeerConnection, message: Message) -> None:
        download = self._downloads.get(message.id)
        if download is None or download.conn is not conn:
            return
        if (
            message.offset != download.received
            or download.received + len(message.data) > download.size
        ):
            self._fail_download(download, INTEGRITY_REASON)
            return
        try:
            download.file.write(message.data)
        except OSError as exc:
            log.warning("Écriture impossible (%s) : %s", download.part, exc)
            self._fail_download(download, "")
            return
        download.sha.update(message.data)
        download.received += len(message.data)
        if download.received == len(message.data):
            self._set_state(message.id, FileState.TRANSFERRING)
        self._emit_progress(download)

    def _finish_download(self, conn: PeerConnection, message: Message) -> None:
        download = self._downloads.get(message.id)
        if download is None or download.conn is not conn:
            return
        if download.received != download.size or download.sha.hexdigest() != message.sha256:
            self._fail_download(download, INTEGRITY_REASON)
            return
        download.file.close()
        del self._downloads[message.id]
        try:
            os.replace(download.part, download.destination)
        except OSError as exc:
            log.warning("Impossible de finaliser %s : %s", download.destination, exc)
            self._set_state(message.id, FileState.FAILED)
            return
        self._emit_progress(download, force=True)
        self._set_state(message.id, FileState.DONE)
        log.info("Fichier reçu : %s", download.destination)

    def _fail_download(self, download: _Download, reason: str) -> None:
        if download.conn.is_ready:
            download.conn.send(Message.file_cancel(download.message_id, reason))
        self._abort_download(download.message_id)
        self._set_state(download.message_id, FileState.FAILED)

    # -- Utilitaires -----------------------------------------------------
    def _abort_upload(self, message_id: str) -> None:
        upload = self._uploads.pop(message_id, None)
        if upload is not None:
            upload.file.close()

    def _abort_download(self, message_id: str) -> None:
        download = self._downloads.pop(message_id, None)
        if download is not None:
            download.file.close()
            download.part.unlink(missing_ok=True)

    def _set_state(self, message_id: str, state: FileState, *, path: str | None = None) -> None:
        if self._store.update_file(message_id, state=state, path=path):
            self.changed.emit(message_id)

    def _emit_progress(self, transfer: _Upload | _Download, *, force: bool = False) -> None:
        done = transfer.offset if isinstance(transfer, _Upload) else transfer.received
        if force or done - transfer.reported >= PROGRESS_STEP:
            transfer.reported = done
            self.progress.emit(transfer.message_id, done, transfer.size)


def check_sendable(path: str | Path) -> int:
    """Vérifie qu'un fichier peut être envoyé et renvoie sa taille (``ValueError`` sinon)."""
    path = Path(path)
    if not path.is_file():
        raise ValueError(f"« {path.name} » n'est pas un fichier.")
    size = path.stat().st_size
    if size > MAX_FILE_SIZE:
        raise ValueError(f"« {path.name} » dépasse la taille maximale de 2 Go.")
    return size
