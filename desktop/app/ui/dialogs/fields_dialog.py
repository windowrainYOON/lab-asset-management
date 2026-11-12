"""Manage a category's columns: add, rename, retype, set role, reorder, delete."""
from __future__ import annotations

from ..alerts import confirm_delete
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QHBoxLayout, QHeaderView, QLabel, QMessageBox,
    QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from ...core.models import FIELD_ROLES, FIELD_TYPES
from ...core.project import Project

TYPE_LABELS = {"text": "문자", "number": "숫자", "date": "날짜", "bool": "예/아니오"}


class FieldsDialog(QDialog):
    def __init__(self, project: Project, category_id: int, parent=None):
        super().__init__(parent)
        self.project = project
        self.category_id = category_id
        self.setWindowTitle("컬럼 관리")
        self.resize(560, 460)
        layout = QVBoxLayout(self)
        hint = QLabel("역할을 지정하면 대시보드가 그 컬럼을 이해합니다. "
                      "‘수량’ 컬럼 값이 부족 기준 이하이면 부족, 0이면 소진으로 표시됩니다.")
        hint.setWordWrap(True)
        hint.setObjectName("muted")
        layout.addWidget(hint)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["컬럼 이름", "형식", "역할"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        layout.addWidget(self.table)
        row = QHBoxLayout()
        for text, slot in (("컬럼 추가", self._add), ("삭제", self._delete),
                           ("▲ 위로", lambda: self._move(-1)), ("▼ 아래로", lambda: self._move(1))):
            b = QPushButton(text)
            b.clicked.connect(slot)
            row.addWidget(b)
        row.addStretch(1)
        layout.addLayout(row)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._deleted: list[int] = []
        for f in project.fields(category_id):
            self._append(f.id, f.name, f.ftype, f.role)

    def _append(self, field_id: int | None, name: str, ftype: str, role: str) -> None:
        r = self.table.rowCount()
        self.table.insertRow(r)
        cell = QTableWidgetItem(name)
        cell.setData(Qt.UserRole, field_id)
        self.table.setItem(r, 0, cell)
        t = QComboBox()
        for key in FIELD_TYPES:
            t.addItem(TYPE_LABELS[key], key)
        t.setCurrentIndex(t.findData(ftype))
        self.table.setCellWidget(r, 1, t)
        ro = QComboBox()
        for key, label in FIELD_ROLES.items():
            ro.addItem(label, key)
        ro.setCurrentIndex(max(ro.findData(role), 0))
        self.table.setCellWidget(r, 2, ro)

    def _row(self, r: int) -> tuple:
        return (self.table.item(r, 0).data(Qt.UserRole), self.table.item(r, 0).text(),
                self.table.cellWidget(r, 1).currentData(), self.table.cellWidget(r, 2).currentData())

    def _add(self) -> None:
        self._append(None, f"새 컬럼 {self.table.rowCount() + 1}", "text", "")
        self.table.editItem(self.table.item(self.table.rowCount() - 1, 0))

    def _delete(self) -> None:
        r = self.table.currentRow()
        if r < 0:
            return
        fid, name, *_ = self._row(r)
        if fid is not None:
            ok = confirm_delete(self, "컬럼 삭제", f"‘{name}’ 컬럼과 그 값을 모두 지울까요?")
            if ok != QMessageBox.Yes:
                return
            self._deleted.append(fid)
        self.table.removeRow(r)

    def _move(self, delta: int) -> None:
        r = self.table.currentRow()
        target = r + delta
        if r < 0 or not 0 <= target < self.table.rowCount():
            return
        a, b = self._row(r), self._row(target)
        for row, vals in ((r, b), (target, a)):
            self.table.removeRow(row)
            self.table.insertRow(row)
            cell = QTableWidgetItem(vals[1])
            cell.setData(Qt.UserRole, vals[0])
            self.table.setItem(row, 0, cell)
            t = QComboBox()
            for key in FIELD_TYPES:
                t.addItem(TYPE_LABELS[key], key)
            t.setCurrentIndex(t.findData(vals[2]))
            self.table.setCellWidget(row, 1, t)
            ro = QComboBox()
            for key, label in FIELD_ROLES.items():
                ro.addItem(label, key)
            ro.setCurrentIndex(max(ro.findData(vals[3]), 0))
            self.table.setCellWidget(row, 2, ro)
        self.table.setCurrentCell(target, 0)

    def _save(self) -> None:
        rows = [self._row(r) for r in range(self.table.rowCount())]
        roles = [role for *_, role in rows if role]
        if len(roles) != len(set(roles)):
            QMessageBox.warning(self, "컬럼 관리", "같은 역할을 두 컬럼에 지정할 수 없습니다.")
            return
        for fid in self._deleted:
            self.project.delete_field(fid)
        ordered: list[int] = []
        # Clear roles first so reassigning a role between columns works.
        for fid, *_ in rows:
            if fid is not None:
                self.project.update_field(fid, role="")
        for fid, name, ftype, role in rows:
            name = name.strip() or "컬럼"
            if fid is None:
                fid = self.project.add_field(self.category_id, name, ftype, role).id
            else:
                self.project.update_field(fid, name=name, ftype=ftype)
                if role:
                    self.project.update_field(fid, role=role)
            ordered.append(fid)
        self.project.reorder_fields(ordered)
        self.accept()
