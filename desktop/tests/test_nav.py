"""Sidebar tabs: sections, hiding tabs into 숨긴 항목, and the two dashboards."""
import os

import pytest

from app.core import nav_tabs, settings
from app.core.project import Project


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "_settings_path", lambda: tmp_path / "settings.json")
    p = Project.create(tmp_path / "t.labproj", "t")
    p.add_category("common", "Reagents")
    p.add_category("common", "Antibody")
    p.add_category("personal", "김다은 개인 품목")
    yield p
    p.close()


def test_sections_and_hidden_tabs(isolated):
    p = isolated
    secs = nav_tabs.all_sections(p)
    assert [s.key for s in secs] == ["home", "common", "personal", "sequence"]
    assert [t.key for t in secs[-1].tabs] == ["seq:common", "seq:personal"]
    assert [t.key for t in secs[0].tabs] == ["dash:common", "dash:personal", "schedule", "work",
                                              "reservations", "orders", "budget", "notices", "attendance"]
    assert nav_tabs.collapsed_sections() == {"hidden"}  # 숨긴 항목 starts folded

    nav_tabs.set_tab_hidden("notices", True)
    nav_tabs.set_tab_hidden(nav_tabs.category_key("common", "Antibody"), True)
    shown, hid = nav_tabs.split(nav_tabs.all_sections(p), nav_tabs.hidden_tabs())
    assert [t.label for t in shown[1].tabs] == ["Reagents"]
    assert [t.key for t in hid] == ["notices", "cat:common:Antibody"]  # sidebar order

    nav_tabs.rename_tab("cat:common:Antibody", "cat:common:Antibodies")  # stays hidden after a rename
    assert "cat:common:Antibodies" in nav_tabs.hidden_tabs()
    nav_tabs.set_tab_hidden("notices", False)
    assert nav_tabs.hidden_tabs() == {"cat:common:Antibodies"}


def test_sidebar_and_dashboards(isolated, monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    from app.ui.main_window import MainWindow
    from app.ui.sidebar import KIND_ROLE, KEY_ROLE

    app = QApplication.instance() or QApplication([])
    p = isolated
    monkeypatch.setattr(settings, "remember_project", lambda *_: None)
    nav_tabs.set_tab_hidden("schedule", True)
    w = MainWindow(p)

    def rows():
        t = w.sidebar.tree
        out = []
        for i in range(t.topLevelItemCount()):
            head = t.topLevelItem(i)
            out.append((head.data(0, KEY_ROLE), [head.child(j).data(0, KEY_ROLE) for j in range(head.childCount())]))
        return out

    assert rows() == [("home", ["dash:common", "dash:personal", "work", "reservations", "orders",
                                "budget", "notices", "attendance"]),
                      ("common", ["cat:common:Reagents", "cat:common:Antibody"]),
                      ("personal", ["cat:personal:김다은 개인 품목"]),
                      ("sequence", ["seq:common", "seq:personal"]),
                      ("hidden", ["schedule"])]
    assert w.stack.currentWidget() is w.dashboard and w.dashboard.scope == "common"
    w.open_tab("dash:personal")
    assert w.stack.currentWidget() is w.personal_dashboard and w.personal_dashboard.scope == "personal"
    assert w.sidebar.tree.currentItem().data(0, KEY_ROLE) == "dash:personal"
    w.open_tab("schedule")  # a hidden tab still opens from 숨긴 항목
    assert w.stack.currentWidget() is w.schedule_page
    cid = next(c.id for c in p.categories("common") if c.name == "Antibody")
    w.sidebar._set_hidden("cat:common:Antibody", True)
    w.show_category(cid)
    cur = w.sidebar.tree.currentItem()
    assert cur.data(0, KEY_ROLE) == "cat:common:Antibody" and cur.parent().data(0, KEY_ROLE) == "hidden"
    assert cur.data(0, KIND_ROLE) == "tab"
    w.close()


def test_dashboard_tabs_and_batch_cart(isolated, monkeypatch):
    """재고 관리 / 재고 현황 are separate tabs; ticked shortages go into the cart at once."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication
    from app.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    p = isolated
    monkeypatch.setattr(settings, "remember_project", lambda *_: None)
    cat = next(c for c in p.categories("common") if c.name == "Reagents")
    name = p.add_field(cat.id, "Name", role="name")
    qty = p.add_field(cat.id, "Qty", "number", role="quantity")
    low = [p.add_item(cat.id, {name.id: n, qty.id: q}) for n, q in (("PBS", "0"), ("Tris", "1"))]
    p.add_item(cat.id, {name.id: "EDTA", qty.id: "9"})
    w = MainWindow(p)
    d = w.dashboard
    d.refresh()
    assert d.kind == "managed" and not d.sections["status"].isVisibleTo(d)
    sec = d.sections["managed"]
    assert sec.table.rowCount() == 2 and not sec.cart_btn.isEnabled()
    sec.table.item(0, 0).setCheckState(Qt.Checked)
    assert sec.checked_ids() == [low[0]] and sec.cart_btn.text() == "장바구니에 담기 (1)"
    sec._check_all()
    assert sorted(sec.checked_ids()) == sorted(low)
    sec.cart_btn.click()
    assert len(w.cart) == 2 and sec.checked_ids() == []

    d.set_kind("status")
    assert settings.load_settings()["dashboard_kind"] == "status"
    assert not sec.isVisibleTo(d) and not d.export_btn.isVisibleTo(d)
    assert d.empty_hint.isVisibleTo(d)  # no 재고 현황 category yet
    d.set_kind("managed")
    w.close()


def test_state_folder_override(tmp_path, monkeypatch):
    """The app's state (token, settings, cache) can live in the project folder."""
    monkeypatch.setenv(settings.STATE_ENV, str(tmp_path / "state"))
    assert settings.user_data_dir() == tmp_path / "state" and (tmp_path / "state").is_dir()
    monkeypatch.delenv(settings.STATE_ENV)
    assert settings.user_data_dir() != tmp_path / "state"


def test_region_boxes(isolated, monkeypatch):
    """RegionLayout 띄운 상자: canvas box + inspector box on the window base; the inspector
    box shows only on pages with an inspector (목록 always, 일정 in the day view or editing)."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from app.ui.main_window import MainWindow
    from app.ui.theme import tokens
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(settings, "remember_project", lambda *_: None)
    w = MainWindow(isolated)
    w.show()
    assert tokens.space("region-box-gap") == 10 and tokens.radius("region-box-radius") == 16
    assert w.box_split.handleWidth() == 10 and not w.inspector_box.isVisible()  # dashboard
    w.show_category(isolated.categories("common")[0].id)
    assert w.inspector_box.isVisible() and w.category_page.detail.isVisibleTo(w)
    w.open_tab("schedule")
    w.schedule_page.set_view("month")
    app.processEvents()
    assert not w.inspector_box.isVisible()
    w.schedule_page.set_view("day")
    assert w.inspector_box.isVisible()
    w.close()


def test_header_search_filters_the_list(isolated, monkeypatch):
    """이 카테고리에서 찾기: the header search filters the shown list; the sheet/state Selects and
    the checkbox are in the header only while a list is shown."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from app.ui.main_window import MainWindow
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(settings, "remember_project", lambda *_: None)
    p = isolated
    cid = p.categories("common")[0].id
    w = MainWindow(p)
    w.show()
    assert not w.list_filters.isVisibleTo(w) and not w.in_category.isVisibleTo(w)
    w.show_category(cid)
    assert w.list_filters.isVisibleTo(w) and w.in_category.isVisibleTo(w) and w.in_category.isChecked()
    w.search_edit.setText("abc")
    w.run_search()
    assert w.stack.currentWidget() is w.category_page and w.category_page.filter_text == "abc"
    w.in_category.setChecked(False)  # off: the same text searches everything
    assert w.stack.currentWidget() is w.search_page and w.category_page.filter_text == ""
    assert not w.list_filters.isVisibleTo(w)
    w.in_category.setChecked(True)
    w.close()


def test_hidden_lists_leave_the_dashboards(isolated):
    from app.core.services.inventory import build_report
    from app.ui.dashboard import hidden_categories
    p = isolated
    nav_tabs.set_tab_hidden(nav_tabs.category_key("personal", "김다은 개인 품목"), True)
    gone = hidden_categories(p)
    assert [c.name for c in p.categories() if c.id in gone] == ["김다은 개인 품목"]
    assert build_report(p, "personal", None, gone).summaries == []
    assert len(build_report(p, "common", None, gone).summaries) == 2
