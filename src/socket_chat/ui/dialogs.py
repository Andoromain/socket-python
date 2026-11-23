"""Boîtes de dialogue : préférences et ajout d'un contact par son adresse."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..protocol import MAX_NAME_LENGTH, validate_name


def _port_spinbox(value: int, parent: QWidget) -> QSpinBox:
    spin = QSpinBox(parent)
    spin.setRange(1, 65535)
    spin.setValue(value)
    return spin


class SettingsDialog(QDialog):
    """Nom d'affichage, port d'écoute et comportement de la fenêtre."""

    def __init__(
        self,
        name: str,
        port: int,
        parent: QWidget | None = None,
        *,
        close_to_tray: bool = True,
        tray_available: bool = True,
        first_run: bool = False,
    ):
        super().__init__(parent)
        self.setWindowTitle("Bienvenue" if first_run else "Préférences")

        self.name_edit = QLineEdit(name, self)
        self.name_edit.setMaxLength(MAX_NAME_LENGTH)
        self.name_edit.setPlaceholderText("Votre nom")
        self.port_spin = _port_spinbox(port, self)
        self.port_spin.setToolTip("Port TCP d'écoute. Un changement s'applique au redémarrage.")
        self.tray_check = QCheckBox(
            "Garder Socket Chat actif dans la zone de notification à la fermeture", self
        )
        self.tray_check.setChecked(close_to_tray and tray_available)
        self.tray_check.setEnabled(tray_available)

        form = QFormLayout()
        form.addRow("&Nom d'affichage :", self.name_edit)
        form.addRow("&Port :", self.port_spin)
        form.addRow(self.tray_check)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        if first_run:
            intro = QLabel("Choisissez le nom que verront vos correspondants.", self)
            intro.setWordWrap(True)
            layout.addWidget(intro)
        layout.addLayout(form)
        layout.addWidget(buttons)

    @property
    def name(self) -> str:
        return validate_name(self.name_edit.text())

    @property
    def port(self) -> int:
        return self.port_spin.value()

    @property
    def close_to_tray(self) -> bool:
        return self.tray_check.isChecked()

    def accept(self) -> None:
        try:
            validate_name(self.name_edit.text())
        except ValueError as exc:
            QMessageBox.warning(self, "Nom invalide", str(exc))
            self.name_edit.setFocus()
            return
        super().accept()


class AddContactDialog(QDialog):
    """Ajout d'un collègue par son adresse (autre sous-réseau, VPN…)."""

    def __init__(self, recent_hosts: list[str], port: int, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Ajouter un collègue")

        self.host_combo = QComboBox(self)
        self.host_combo.setEditable(True)
        self.host_combo.addItems(recent_hosts)
        self.host_combo.lineEdit().setPlaceholderText("ex. 192.168.1.20 ou poste-dupont")
        self.port_spin = _port_spinbox(port, self)

        form = QFormLayout()
        form.addRow("&Adresse :", self.host_combo)
        form.addRow("&Port :", self.port_spin)

        hint = QLabel(
            "Inutile pour les collègues du même réseau : ils apparaissent automatiquement. "
            "Leur adresse est affichée en bas de leur liste de contacts.",
            self,
        )
        hint.setWordWrap(True)
        hint.setEnabled(False)

        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self
        )
        self._buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Ajouter")
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)
        self.host_combo.editTextChanged.connect(self._update_ok)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(hint)
        layout.addWidget(self._buttons)
        self.setMinimumWidth(380)
        self._update_ok()

    @property
    def host(self) -> str:
        return self.host_combo.currentText().strip()

    @property
    def port(self) -> int:
        return self.port_spin.value()

    def _update_ok(self) -> None:
        self._buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(bool(self.host))
