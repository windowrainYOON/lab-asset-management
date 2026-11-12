"""Create a category or edit its name, scope and low-stock threshold."""
from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QLabel, QVBoxLayout,
)

from ...core.models import (
    CATEGORY_KINDS, DEFAULT_THRESHOLD, KIND_DESCRIPTIONS, PRESET_CATEGORIES, SCOPES, Category,
)

# Starter columns for a new empty category.
STARTER_FIELDS = [
    ("Name", "text", "name"),
    ("수량", "number", "quantity"),
    ("Location", "text", "location"),
    ("Company", "text", ""),
    ("Cat#", "text", "catalog"),
    ("담당자", "text", "owner"),
    ("Remarks", "text", ""),
]

# Personal Asset template: one file per person (or per kind), rows sorted by 분류
PERSONAL_FIELDS = [
    ("이름", "text", "name"),
    ("분류", "text", ""),
    ("제조사", "text", ""),
    ("Cat#", "text", "catalog"),
    ("수량", "number", "quantity"),
    ("위치", "text", "location"),
    ("구입일", "date", ""),
    ("메모", "text", ""),
]


class CategoryDialog(QDialog):
    def __init__(self, parent=None, scope: str = "common", category: Category | None = None):
        super().__init__(parent)
        self.setWindowTitle("카테고리 설정" if category else "카테고리 추가")
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.name = QComboBox()
        self.name.setEditable(True)
        self.name.addItems(PRESET_CATEGORIES)
        self.name.setCurrentText(category.name if category else "")
        self.name.lineEdit().setPlaceholderText("예: 단백질 (Protein)")
        form.addRow("이름", self.name)
        self.scope = QComboBox()
        for key, label in SCOPES:
            self.scope.addItem(label, key)
        self.scope.setCurrentIndex(self.scope.findData(category.scope if category else scope))
        form.addRow("구분", self.scope)
        self.kind = QComboBox()
        for key, label in CATEGORY_KINDS.items():
            self.kind.addItem(label, key)
        self.kind.setCurrentIndex(self.kind.findData(category.kind if category else "managed"))
        form.addRow("재고 타입", self.kind)
        self.kind_hint = QLabel()
        self.kind_hint.setObjectName("muted")
        self.kind_hint.setWordWrap(True)
        form.addRow("", self.kind_hint)
        self.threshold = QDoubleSpinBox()
        self.threshold.setRange(0, 100000)
        self.threshold.setDecimals(1)
        self.threshold.setValue(category.threshold if category else DEFAULT_THRESHOLD)
        self.threshold_label = QLabel("부족 기준 (이하)")
        form.addRow(self.threshold_label, self.threshold)
        layout.addLayout(form)
        self.kind.currentIndexChanged.connect(self._kind_changed)
        self._kind_changed()
        if category is None:
            hint = QLabel("새 카테고리는 기본 컬럼(Name, 수량, Location, Company, Cat#, 담당자, Remarks)으로 "
                          "시작하며, 컬럼 관리에서 언제든 바꿀 수 있습니다.")
            hint.setWordWrap(True)
            hint.setObjectName("muted")
            layout.addWidget(hint)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _kind_changed(self) -> None:
        kind = self.kind.currentData()
        self.kind_hint.setText(KIND_DESCRIPTIONS[kind])
        self.threshold.setVisible(kind == "managed")
        self.threshold_label.setVisible(kind == "managed")

    def _accept(self) -> None:
        if self.name.currentText().strip():
            self.accept()

    def values(self) -> dict:
        return {
            "name": self.name.currentText().strip(),
            "scope": self.scope.currentData(),
            "threshold": self.threshold.value(),
            "kind": self.kind.currentData(),
        }
