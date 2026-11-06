"""Modèles Qt : liste des contacts et fil d'une conversation."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime
from enum import IntEnum

from PySide6.QtCore import QAbstractListModel, QModelIndex, QPersistentModelIndex, Qt

from ..messenger import Messenger
from ..store import ContactSummary, MessageRecord, Status

#: Deux messages du même expéditeur plus proches que ce délai sont regroupés.
GROUP_INTERVAL_MS = 5 * 60 * 1000

Index = QModelIndex | QPersistentModelIndex


class Role(IntEnum):
    ID = Qt.ItemDataRole.UserRole + 1
    SUMMARY = Qt.ItemDataRole.UserRole + 2
    ONLINE = Qt.ItemDataRole.UserRole + 3
    TYPING = Qt.ItemDataRole.UserRole + 4
    ITEM = Qt.ItemDataRole.UserRole + 5
    PROGRESS = Qt.ItemDataRole.UserRole + 6


# -- Contacts -------------------------------------------------------------
class ContactListModel(QAbstractListModel):
    def __init__(self, messenger: Messenger, parent=None):
        super().__init__(parent)
        self._messenger = messenger
        self._rows: list[ContactSummary] = []

    def refresh(self) -> None:
        summaries = self._messenger.store.contacts()
        if [s.contact.id for s in summaries] == [s.contact.id for s in self._rows]:
            self._rows = summaries  # même ordre : simple mise à jour, sélection conservée
            if self._rows:
                self.dataChanged.emit(self.index(0), self.index(len(self._rows) - 1))
            return
        self.beginResetModel()
        self._rows = summaries
        self.endResetModel()

    def contact_changed(self, contact_id: str) -> None:
        row = self.row_of(contact_id)
        if row is not None:
            self.dataChanged.emit(self.index(row), self.index(row))

    def row_of(self, contact_id: str) -> int | None:
        for row, summary in enumerate(self._rows):
            if summary.contact.id == contact_id:
                return row
        return None

    def ids(self) -> list[str]:
        return [s.contact.id for s in self._rows]

    def rowCount(self, parent: Index = QModelIndex()) -> int:  # noqa: N802, B008
        return 0 if parent.isValid() else len(self._rows)

    def data(self, index: Index, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        summary = self._rows[index.row()]
        contact_id = summary.contact.id
        match role:
            case Qt.ItemDataRole.DisplayRole:
                return summary.contact.name
            case Role.ID:
                return contact_id
            case Role.SUMMARY:
                return summary
            case Role.ONLINE:
                return self._messenger.is_online(contact_id)
            case Role.TYPING:
                return self._messenger.is_typing(contact_id)
        return None


# -- Conversation ---------------------------------------------------------
@dataclass(frozen=True)
class DayItem:
    day: date


@dataclass(frozen=True)
class MessageItem:
    record: MessageRecord
    first_in_group: bool = True
    last_in_group: bool = True


def _day(record: MessageRecord) -> date:
    return datetime.fromtimestamp(record.ts / 1000).date()


def _grouped(previous: MessageRecord, current: MessageRecord) -> bool:
    return (
        previous.outgoing == current.outgoing
        and _day(previous) == _day(current)
        and current.ts - previous.ts < GROUP_INTERVAL_MS
    )


class MessageListModel(QAbstractListModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._items: list[DayItem | MessageItem] = []
        self._rows_by_id: dict[str, int] = {}
        self._progress: dict[str, tuple[int, int]] = {}
        self.contact_id: str | None = None

    # -- Remplissage -----------------------------------------------------
    def load(self, contact_id: str | None, records: list[MessageRecord]) -> None:
        self.beginResetModel()
        self.contact_id = contact_id
        self._items = []
        self._rows_by_id = {}
        self._progress = {}
        for record in records:
            self._push(record)
        self.endResetModel()

    def append(self, record: MessageRecord) -> None:
        if record.contact_id != self.contact_id or record.id in self._rows_by_id:
            return
        start = len(self._items)
        previous_row = self._last_message_row()
        new_day = previous_row is None or _day(self._items[previous_row].record) != _day(record)
        count = 2 if new_day else 1
        self.beginInsertRows(QModelIndex(), start, start + count - 1)
        self._push(record)
        self.endInsertRows()
        if previous_row is not None and not self._items[previous_row].last_in_group:
            self.dataChanged.emit(self.index(previous_row), self.index(previous_row))

    def update_status(self, message_id: str, status: str) -> None:
        row = self._rows_by_id.get(message_id)
        if row is None:
            return
        item = self._items[row]
        assert isinstance(item, MessageItem)
        record = replace(item.record, status=Status(status))
        self._items[row] = replace(item, record=record)
        self.dataChanged.emit(self.index(row), self.index(row))

    def update_record(self, record: MessageRecord) -> None:
        """Remplace un message affiché (réactions, état du fichier…)."""
        row = self._rows_by_id.get(record.id)
        if row is None:
            return
        item = self._items[row]
        assert isinstance(item, MessageItem)
        self._items[row] = replace(item, record=record)
        if record.file is None or not record.file.state.active:
            self._progress.pop(record.id, None)
        self.dataChanged.emit(self.index(row), self.index(row))

    def set_progress(self, message_id: str, done: int, total: int) -> None:
        row = self._rows_by_id.get(message_id)
        if row is None:
            return
        self._progress[message_id] = (done, total)
        self.dataChanged.emit(self.index(row), self.index(row), [Role.PROGRESS])

    def row_of(self, message_id: str) -> int | None:
        return self._rows_by_id.get(message_id)

    def _last_message_row(self) -> int | None:
        for row in range(len(self._items) - 1, -1, -1):
            if isinstance(self._items[row], MessageItem):
                return row
        return None

    def _push(self, record: MessageRecord) -> None:
        previous_row = self._last_message_row()
        previous = self._items[previous_row] if previous_row is not None else None
        if previous is None or _day(previous.record) != _day(record):
            self._items.append(DayItem(_day(record)))
            grouped = False
        else:
            grouped = _grouped(previous.record, record)
        if grouped and previous_row is not None:
            self._items[previous_row] = replace(previous, last_in_group=False)
        self._rows_by_id[record.id] = len(self._items)
        self._items.append(MessageItem(record, first_in_group=not grouped))

    # -- Accès -----------------------------------------------------------
    def item(self, row: int) -> DayItem | MessageItem:
        return self._items[row]

    def rowCount(self, parent: Index = QModelIndex()) -> int:  # noqa: N802, B008
        return 0 if parent.isValid() else len(self._items)

    def data(self, index: Index, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        item = self._items[index.row()]
        if role == Role.ITEM:
            return item
        if role == Qt.ItemDataRole.DisplayRole and isinstance(item, MessageItem):
            return item.record.display_text
        if role == Role.ID and isinstance(item, MessageItem):
            return item.record.id
        if role == Role.PROGRESS and isinstance(item, MessageItem):
            return self._progress.get(item.record.id)
        return None
