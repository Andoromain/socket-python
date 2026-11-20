"""Vérification du chiffrement : code de sécurité et bandeaux « clé changée »."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..identity import safety_number
from .theme import current_theme


def format_number(number: str) -> str:
    """Code de sécurité sur trois lignes de quatre groupes."""
    groups = number.split()
    return "\n".join(" ".join(groups[i : i + 4]) for i in range(0, len(groups), 4))


class SafetyNumberDialog(QDialog):
    """Code de sécurité à comparer avec celui affiché chez le contact."""

    def __init__(
        self, name: str, own_key: bytes, contact_key: bytes | None, parent: QWidget | None = None
    ):
        super().__init__(parent)
        self.setWindowTitle(f"Chiffrement — {name}")
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        title = QLabel(f"🔒 Vos échanges avec <b>{_escape(name)}</b> sont chiffrés.", self)
        title.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(title)
        if contact_key is None:
            self.number = None
            text = (
                f"{_escape(name)} ne s'est pas encore connecté avec une version chiffrée de "
                "Socket Chat : le code de sécurité apparaîtra après votre premier échange."
            )
            info = QLabel(text, self)
            info.setWordWrap(True)
            layout.addWidget(info)
        else:
            self.number = safety_number(own_key, contact_key)
            code = QLabel(format_number(self.number), self)
            code.setObjectName("safetyNumber")
            font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
            font.setPointSizeF(font.pointSizeF() * 1.5)
            code.setFont(font)
            code.setAlignment(Qt.AlignmentFlag.AlignCenter)
            code.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            layout.addWidget(code)
            help_text = QLabel(
                f"Comparez ce code avec celui qu'affiche {_escape(name)} (Socket Chat, "
                "🔒 dans votre conversation), en personne ou par téléphone. S'ils sont "
                "identiques, personne ne peut lire ni modifier vos échanges.",
                self,
            )
            help_text.setWordWrap(True)
            layout.addWidget(help_text)
        buttons = QDialogButtonBox(self)
        close = buttons.addButton("Fermer", QDialogButtonBox.ButtonRole.RejectRole)
        close.setDefault(True)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.setMinimumWidth(420)


class KeyBanner(QFrame):
    """Bandeau d'avertissement dans une conversation (clé changée ou refusée)."""

    accept_requested = Signal()

    def __init__(self, *, warning: bool, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("keyBanner")
        dark = current_theme().dark
        if warning:
            background, border, text = (
                ("#4a2c12", "#7a4a1e", "#ffe2c2") if dark else ("#fff1df", "#f0c48a", "#5a3000")
            )
        else:
            background, border, text = (
                ("#1c3247", "#2f4f6b", "#d6e8f7") if dark else ("#e3f0fb", "#b9d6ee", "#0d3350")
            )
        self.setStyleSheet(
            f"#keyBanner {{ background: {background}; border: none;"
            f" border-bottom: 1px solid {border}; }}"
            f"#keyBanner QLabel {{ color: {text}; }}"
        )
        self.label = QLabel(self)
        self.label.setWordWrap(True)
        self.accept_button = QPushButton("Accepter la nouvelle clé…", self)
        self.accept_button.clicked.connect(self.accept_requested)
        self.accept_button.setVisible(warning)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 8, 14, 8)
        layout.addWidget(self.label, 1)
        layout.addWidget(self.accept_button)
        self.hide()


def changed_key_text(name: str) -> str:
    return (
        f"⚠ La clé de sécurité de {name} a changé : vos échanges sont bloqués. C'est normal "
        "après une réinstallation de Socket Chat ou un changement de poste ; sinon, quelqu'un "
        "tente peut-être d'usurper son identité. Vérifiez avec cette personne avant "
        "d'accepter."
    )


def refused_key_text(name: str) -> str:
    return (
        f"{name} ne reconnaît pas encore la clé de ce poste (réinstallation ?) : elle doit "
        "être acceptée de son côté, dans votre conversation, pour que vos messages passent."
    )


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
