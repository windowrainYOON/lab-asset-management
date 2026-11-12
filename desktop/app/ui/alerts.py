"""Confirmation alerts in the HIG shape (components/Alert).

For a destructive action the red button names the action and the default (bold,
Return) button is 취소, so pressing Return never deletes anything.
"""
from __future__ import annotations

from PySide6.QtWidgets import QMessageBox


def confirm_delete(parent, title: str, message: str, action: str = "삭제"):
    """Ask before deleting. Returns QMessageBox.Yes (confirmed) or QMessageBox.No, like
    QMessageBox.question, so callers can compare the same way."""
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Warning)
    box.setWindowTitle(title)
    box.setText(message)
    destroy = box.addButton(action, QMessageBox.DestructiveRole)
    destroy.setObjectName("destructive")
    cancel = box.addButton("취소", QMessageBox.RejectRole)
    box.setDefaultButton(cancel)
    box.setEscapeButton(cancel)
    box.exec()
    return QMessageBox.Yes if box.clickedButton() is destroy else QMessageBox.No
