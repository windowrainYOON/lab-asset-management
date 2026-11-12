"""Import CSV / XLSX / XLS: pick sheets, check the header row, map columns."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QGroupBox,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
    QRadioButton, QSpinBox, QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from ...core.importer import ColumnPlan, Table, import_table, plan_columns, read_tables
from ...core.models import CATEGORY_KINDS, FIELD_ROLES, FIELD_TYPES, SCOPES
from ...core.project import Project
from .fields_dialog import TYPE_LABELS

PREVIEW_ROWS = 8


class ImportDialog(QDialog):
    def __init__(self, project: Project, path: str, parent=None, scope: str = "common",
                 category_id: int | None = None):
        super().__init__(parent)
        self.project = project
        self.path = path
        self.setWindowTitle("파일 가져오기")
        self.resize(1100, 720)
        self.tables: list[Table] = read_tables(path)
        self.plans: dict[int, list[ColumnPlan]] = {i: plan_columns(t) for i, t in enumerate(self.tables)}
        self.current = -1
        self.result_category_id: int | None = None

        layout = QVBoxLayout(self)
        # target
        target = QGroupBox("가져올 위치")
        tform = QFormLayout(target)
        self.new_radio = QRadioButton("새 카테고리 만들기")
        self.existing_radio = QRadioButton("기존 카테고리에 추가")
        self.new_name = QLineEdit(self._default_name())
        self.scope = QComboBox()
        for key, label in SCOPES:
            self.scope.addItem(label, key)
        self.scope.setCurrentIndex(self.scope.findData(scope))
        new_row = QHBoxLayout()
        new_row.addWidget(self.new_radio)
        new_row.addWidget(self.new_name, 1)
        new_row.addWidget(self.scope)
        self.kind = QComboBox()
        for key, label in CATEGORY_KINDS.items():
            self.kind.addItem(label, key)
        self.kind.setToolTip("재고 관리: 부족 알림 / 재고 현황: 보유 여부만 표시")
        new_row.addWidget(self.kind)
        tform.addRow(new_row)
        self.existing = QComboBox()
        for cat in project.categories():
            label = dict(SCOPES)[cat.scope]
            self.existing.addItem(f"{label} / {cat.name}", cat.id)
        ex_row = QHBoxLayout()
        ex_row.addWidget(self.existing_radio)
        ex_row.addWidget(self.existing, 1)
        tform.addRow(ex_row)
        self.sheet_as_group = QCheckBox("시트 이름을 하위 그룹으로 사용")
        self.sheet_as_group.setChecked(len(self.tables) > 1)
        self.skip_dup = QCheckBox("같은 내용의 행은 건너뛰기")
        self.skip_dup.setChecked(True)
        opt_row = QHBoxLayout()
        opt_row.addWidget(self.sheet_as_group)
        opt_row.addWidget(self.skip_dup)
        opt_row.addStretch(1)
        tform.addRow(opt_row)
        if category_id is not None and self.existing.findData(category_id) >= 0:
            self.existing_radio.setChecked(True)
            self.existing.setCurrentIndex(self.existing.findData(category_id))
        else:
            self.new_radio.setChecked(True)
        self.existing_radio.setEnabled(self.existing.count() > 0)
        layout.addWidget(target)

        split = QSplitter()
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.addWidget(QLabel("시트"))
        self.sheet_list = QListWidget()
        for t in self.tables:
            it = QListWidgetItem(f"{t.name}  ({len(t.rows())}행)")
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked)
            self.sheet_list.addItem(it)
        self.sheet_list.currentRowChanged.connect(self._show_sheet)
        ll.addWidget(self.sheet_list)
        split.addWidget(left)

        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        hr = QHBoxLayout()
        hr.addWidget(QLabel("헤더 행 번호"))
        self.header_spin = QSpinBox()
        self.header_spin.setMinimum(1)
        self.header_spin.valueChanged.connect(self._header_changed)
        hr.addWidget(self.header_spin)
        self.rows_label = QLabel()
        self.rows_label.setObjectName("muted")
        hr.addWidget(self.rows_label, 1)
        rl.addLayout(hr)
        rl.addWidget(QLabel("미리보기"))
        self.preview = QTableWidget()
        self.preview.setEditTriggers(QAbstractItemView.NoEditTriggers)
        rl.addWidget(self.preview, 1)
        rl.addWidget(QLabel("컬럼 매핑 (가져올 컬럼, 이름, 형식, 역할)"))
        self.mapping = QTableWidget(0, 4)
        self.mapping.setHorizontalHeaderLabels(["가져오기", "컬럼 이름", "형식", "역할"])
        self.mapping.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.mapping.verticalHeader().setVisible(False)
        rl.addWidget(self.mapping, 1)
        split.addWidget(right)
        split.setSizes([220, 880])
        layout.addWidget(split, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("가져오기")
        buttons.accepted.connect(self._do_import)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        if self.tables:
            self.sheet_list.setCurrentRow(0)

    def _default_name(self) -> str:
        from pathlib import Path
        return Path(self.path).stem

    # ---- sheet view ------------------------------------------------------
    def _store_mapping(self) -> None:
        if self.current < 0:
            return
        plans = self.plans[self.current]
        for r, plan in enumerate(plans):
            plan.include = self.mapping.item(r, 0).checkState() == Qt.Checked
            plan.header = self.mapping.item(r, 1).text().strip() or plan.header
            plan.ftype = self.mapping.cellWidget(r, 2).currentData()
            plan.role = self.mapping.cellWidget(r, 3).currentData()

    def _show_sheet(self, idx: int) -> None:
        self._store_mapping()
        self.current = idx
        if idx < 0:
            return
        t = self.tables[idx]
        self.header_spin.blockSignals(True)
        self.header_spin.setMaximum(max(1, len(t.raw)))
        self.header_spin.setValue(t.header_row + 1)
        self.header_spin.blockSignals(False)
        self._render(t, self.plans[idx])

    def _header_changed(self, value: int) -> None:
        if self.current < 0:
            return
        t = self.tables[self.current]
        t.header_row = value - 1
        self.plans[self.current] = plan_columns(t)
        self._render(t, self.plans[self.current])

    def _render(self, t: Table, plans: list[ColumnPlan]) -> None:
        rows = t.rows()
        self.rows_label.setText(f"데이터 {len(rows)}행")
        cols = [p.index for p in plans]
        self.preview.clear()
        self.preview.setColumnCount(len(cols))
        self.preview.setRowCount(min(PREVIEW_ROWS, len(rows)))
        self.preview.setHorizontalHeaderLabels([p.header for p in plans])
        for r, row in enumerate(rows[:PREVIEW_ROWS]):
            for c, col in enumerate(cols):
                self.preview.setItem(r, c, QTableWidgetItem(row[col]))
        self.mapping.setRowCount(0)
        for plan in plans:
            r = self.mapping.rowCount()
            self.mapping.insertRow(r)
            chk = QTableWidgetItem()
            chk.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            chk.setCheckState(Qt.Checked if plan.include else Qt.Unchecked)
            self.mapping.setItem(r, 0, chk)
            self.mapping.setItem(r, 1, QTableWidgetItem(plan.header))
            tcombo = QComboBox()
            for key in FIELD_TYPES:
                tcombo.addItem(TYPE_LABELS[key], key)
            tcombo.setCurrentIndex(tcombo.findData(plan.ftype))
            self.mapping.setCellWidget(r, 2, tcombo)
            rcombo = QComboBox()
            for key, label in FIELD_ROLES.items():
                rcombo.addItem(label, key)
            rcombo.setCurrentIndex(max(rcombo.findData(plan.role), 0))
            self.mapping.setCellWidget(r, 3, rcombo)

    # ---- import ----------------------------------------------------------
    def _do_import(self) -> None:
        self._store_mapping()
        chosen = [i for i in range(self.sheet_list.count())
                  if self.sheet_list.item(i).checkState() == Qt.Checked]
        if not chosen:
            QMessageBox.information(self, "가져오기", "가져올 시트를 하나 이상 선택하세요.")
            return
        if self.new_radio.isChecked():
            name = self.new_name.text().strip() or self._default_name()
            cat = self.project.add_category(self.scope.currentData(), name, kind=self.kind.currentData())
            cat_id = cat.id
        else:
            cat_id = self.existing.currentData()
        total, dups = 0, 0
        try:
            for i in chosen:
                t = self.tables[i]
                sub = t.name if self.sheet_as_group.isChecked() else ""
                res = import_table(self.project, cat_id, t, self.plans[i], subgroup=sub,
                                   skip_duplicates=self.skip_dup.isChecked())
                total += res.added
                dups += res.skipped_duplicates
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "가져오기 실패", f"가져오는 중 오류가 났습니다.\n{exc}")
            return
        self.result_category_id = cat_id
        msg = f"{total}개 항목을 가져왔습니다."
        if dups:
            msg += f" 중복 {dups}개는 건너뛰었습니다."
        QMessageBox.information(self, "가져오기 완료", msg)
        self.accept()
