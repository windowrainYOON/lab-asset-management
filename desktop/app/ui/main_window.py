"""Main window: left sidebar (menu tabs + Common/Personal files + hidden tabs), search bar, pages."""
from __future__ import annotations

from pathlib import Path

from .theme import icons, tokens
from .alerts import confirm_delete
from PySide6.QtCore import QEvent, Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QCheckBox, QFileDialog, QFrame, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
    QMainWindow, QMessageBox, QPushButton, QSplitter, QStackedWidget, QVBoxLayout, QWidget,
)

from ..core import settings
from ..core.project import FILE_SUFFIX, Project
from . import cloud as cloud_ui
from .category_view import CategoryPage
from .dashboard import DashboardPage
from .search_view import SearchPage
from .sequence_view import SequenceView
from .sidebar import Sidebar, sidebar_width
from .widgets import InspectorBox, glass_capsule

FILE_FILTER = f"Asset Manager 프로젝트 (*{FILE_SUFFIX})"

# Sequence Map tabs (app/core/nav_tabs.py) and the Drive subfolder each one shows.
SEQUENCE_GROUPS = {"seq:common": "Common Plasmid", "seq:personal": "Personal Plasmid"}


# components/Toolbar: the search field is 200 wide; SplitView: content keeps at least 360.
TOOLBAR_SEARCH_WIDTH = 200
CONTENT_MIN_WIDTH = 360


class MainWindow(QMainWindow):
    def __init__(self, project: Project, session=None):
        super().__init__()
        self.project = project
        self.session = None  # FolderSession when the data is a Google Drive folder
        self.current_category: int | None = None
        self.setWindowTitle("Asset Manager")
        self.resize(1440, 900)

        # Window (components/RegionLayout): Sidebar | Canvas, split by tone and depth, no lines.
        #   sidebar = raised face (background): logo + app name + sidebar toggle, nav tree
        #   canvas = recessed face (background-secondary) with the sidebar's soft shade on its
        #     start edge; its header row (page · where the data lives … search, 구성원, ⚙, cart)
        #     then the page. Inspectors inside pages are raised faces again.
        central = QWidget()
        central.setObjectName("windowBack")
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.sidebar_btn = icons.set_icon(QPushButton(), "sidebar.left", "사이드바 숨기기")
        self.sidebar_btn.setObjectName("chromeIcon")
        self.sidebar_btn.setToolTip("사이드바 숨기기 (⌃⌘S)")
        self.sidebar_btn.clicked.connect(self.toggle_sidebar)
        self.toolbar_subtitle = QLabel()  # where the data lives, under the app name
        self.toolbar_subtitle.setObjectName("brandSubtitle")
        # no controls at the sidebar's bottom edge (components/Sidebar): 구성원 sits in the header
        self.members_btn = icons.set_icon(QPushButton(), "person.crop.circle", "구성원 관리")
        self.members_btn.setToolTip("구성원 관리… (메일 메뉴에도 있습니다)")
        self.members_btn.clicked.connect(self.edit_members)

        # content header (end = most important last): search, refresh, ⚙, cart
        self.search_edit = QLineEdit()
        self.search_edit.setObjectName("globalSearch")
        self.search_edit.setPlaceholderText("검색 (⌘K)")
        self.search_edit.setToolTip("이름, Cat#, 위치, 세부 조건, 메모에서 찾습니다")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.setMinimumWidth(TOOLBAR_SEARCH_WIDTH)
        # magnifier at the start of the field (HIG: Search); the text starts gap-tight after it
        self._search_icon = QLabel(self.search_edit)
        self._search_icon.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._search_icon.setPixmap(self._magnifier())
        self.search_edit.setTextMargins(tokens.space("icon-sm") + tokens.space("gap-tight"), 0, 0, 0)
        self.search_edit.setAttribute(Qt.WA_MacShowFocusRect, False)  # the 3px tahoe ring instead
        self.search_edit.installEventFilter(self)
        self.search_edit.returnPressed.connect(self.run_search)
        self._search_timer = QTimer(self, singleShot=True, interval=250)
        self._search_timer.timeout.connect(self.run_search)
        self.search_edit.textEdited.connect(lambda _: self._search_timer.start())
        self.refresh_btn = icons.set_icon(QPushButton(), "arrow.clockwise", "최신화")
        self.refresh_btn.setToolTip("최신화: 이 Mac에 저장해 둔 수정을 드라이브에 올리고, 바뀐 파일만 받아옵니다 "
                                    "(2분마다 자동) (⌘R)")
        self.refresh_btn.clicked.connect(self.sync_folder)
        self.refresh_btn.setVisible(False)
        self.tabs_btn = icons.set_icon(QPushButton(), "gearshape", "설정")
        self.tabs_btn.setToolTip("설정: 탭 표시 설정… (보기 메뉴에도 있습니다)")
        self.tabs_btn.clicked.connect(lambda: self.sidebar.edit_tabs())
        from .mail_ui import Cart
        self.cart = Cart()
        self.cart_btn = icons.set_icon(QPushButton(), "cart", "장바구니")
        self.cart_btn.setToolTip("장바구니: 담은 품목을 담당자에게 주문 요청 메일로 보냅니다 (메일 메뉴에도 있습니다)")
        self.cart_btn.clicked.connect(self.open_cart)
        # shown in the header only while the sidebar is hidden, so it can come back
        self.show_sidebar_btn = icons.set_icon(QPushButton(), "sidebar.left", "사이드바 보기")
        self.show_sidebar_btn.setObjectName("chromeIcon")
        self.show_sidebar_btn.setToolTip("사이드바 보기 (⌃⌘S)")
        self.show_sidebar_btn.clicked.connect(self.toggle_sidebar)
        self.show_sidebar_btn.setVisible(False)

        self.splitter = QSplitter(Qt.Horizontal)
        self.splitter.setHandleWidth(1)  # on the window base, unseen
        self.splitter.setChildrenCollapsible(False)
        outer.addWidget(self.splitter, 1)
        self.sidebar = Sidebar(self)
        self.splitter.addWidget(self.sidebar)
        holder = QWidget()
        holder.setMinimumWidth(CONTENT_MIN_WIDTH)
        hl = QVBoxLayout(holder)
        gap = tokens.space("region-box-gap")
        hl.setContentsMargins(0, gap, gap, gap)  # 0 on the sidebar side (RegionLayout boxes)
        hl.setSpacing(gap)
        # search bar (RegionLayout boxes, user ds19): one long box above the canvas and the
        # inspector, always shown on every page: list filters, the search field, its scope,
        # [최신화 · 구성원 · 설정] and the cart
        self.search_bar = QFrame()
        self.search_bar.setObjectName("searchBar")
        sb = QHBoxLayout(self.search_bar)
        sb.setContentsMargins(tokens.space("space-2"), 0, tokens.space("space-2"), 0)
        sb.setSpacing(tokens.space("space-2"))
        hl.addWidget(self.search_bar)
        # canvas box | inspector box, split by a 10-wide unpainted gap (the splitter handle)
        self.box_split = QSplitter(Qt.Horizontal)
        self.box_split.setObjectName("boxSplit")
        self.box_split.setHandleWidth(gap)
        self.box_split.setChildrenCollapsible(False)
        hl.addWidget(self.box_split)
        main = QFrame()
        main.setObjectName("mainArea")  # the canvas box
        self.box_split.addWidget(main)
        self.inspector_box = InspectorBox()
        self.box_split.addWidget(self.inspector_box)
        self.box_split.setStretchFactor(0, 1)
        self.box_split.setStretchFactor(1, 0)
        ml = QVBoxLayout(main)
        ml.setContentsMargins(0, 0, 0, 0)
        ml.setSpacing(0)

        head = QFrame()
        head.setObjectName("contentHeader")
        th = QHBoxLayout(head)
        th.setContentsMargins(tokens.space("margin"), 0, tokens.space("margin"), 0)
        th.setSpacing(tokens.space("space-2"))  # toolbar item gap 8
        th.addWidget(self.show_sidebar_btn)
        self.header_icon = QLabel()
        th.addWidget(self.header_icon)
        self.header_title = QLabel()
        self.header_title.setObjectName("headerTitle")
        th.addWidget(self.header_title)
        dot = QLabel("·")
        dot.setObjectName("headerMeta")
        th.addWidget(dot)
        self.header_meta = QLabel()
        self.header_meta.setObjectName("headerMeta")
        th.addWidget(self.header_meta)
        th.addStretch(1)
        # while a list is shown: its sheet / state Selects and 이 카테고리에서 찾기 beside the search
        self.list_filters = QWidget()
        self._lf = QHBoxLayout(self.list_filters)
        self._lf.setContentsMargins(0, 0, 0, 0)
        self._lf.setSpacing(tokens.space("space-2"))
        self.in_category = QCheckBox("이 카테고리에서 찾기")
        self.in_category.setToolTip("켜면 검색어로 지금 보는 목록만 거릅니다. 끄면 모든 카테고리에서 찾습니다")
        self.in_category.setChecked(settings.load_settings().get("search_in_category", True))
        self.in_category.toggled.connect(self._scope_toggled)
        sb.addWidget(self.search_edit, 1)  # the long field takes the bar's free width
        sb.addWidget(self.list_filters)
        sb.addWidget(self.in_category)
        sb.addWidget(glass_capsule(self.refresh_btn, self.members_btn, self.tabs_btn))
        sb.addWidget(glass_capsule(self.cart_btn))
        ml.addWidget(head)
        from .widgets import CountBadge
        self.cart_badge = CountBadge(self.cart_btn, self.search_bar)  # how many items are in the cart

        self._sequences = None   # SequenceLibrary, read when a Sequence Map tab is opened
        self.stack = QStackedWidget()
        from .work_view import WorkController, WorkPage
        self.work = WorkController(self)
        self.work.parent_widget = self
        self.dashboard = DashboardPage(project, scope="common", work=self.work)
        self.personal_dashboard = DashboardPage(project, scope="personal")
        for d in (self.dashboard, self.personal_dashboard):
            d.open_category.connect(self.show_category)
            d.open_item.connect(self.show_item)
            d.add_to_cart.connect(self.add_to_cart)
        self.category_page = CategoryPage(project)
        self._lf.addWidget(self.category_page.group_combo)
        self._lf.addWidget(self.category_page.state_combo)
        self.category_page.filter_cleared.connect(self._filter_cleared)
        self.category_page.structure_changed.connect(self._structure_changed)
        self.category_page.data_changed.connect(lambda: None)
        self.category_page.group_changed.connect(self._group_changed)
        self.category_page.add_to_cart.connect(self.add_to_cart)
        self.search_page = SearchPage(project)
        self.search_page.open_item.connect(self.show_item)
        self.search_page.open_record.connect(self.open_record_hit)
        self.search_page.sources = self._search_sources
        from .notices_view import NoticesPage
        from .schedule_view import SchedulePage
        self.schedule_page = SchedulePage()
        self.schedule_page.set_project(project)
        self.notices_page = NoticesPage()
        self.notices_page.changed.connect(self._notices_changed)
        # 장비 예약 · 주문 · 예산: each its own Excel file of the location set
        from .budget_view import BudgetPage
        from .orders_view import OrdersPage
        from .reservation_view import ReservationPage
        self.reservation_page = ReservationPage()
        self.reservation_page.changed.connect(self._records_changed)
        self.orders_page = OrdersPage()
        self.orders_page.changed.connect(self._records_changed)
        self.budget_page = BudgetPage()
        from .attendance_view import AttendancePage
        self.attendance_page = AttendancePage()
        self.attendance_page.create_requested.connect(self.create_attendance)
        self.schedule_page.reservations = lambda: self.session.reservations if self.session else None
        self.schedule_page.open_reservation.connect(self.open_reservation)
        self.dashboard.notice_card.open_notices.connect(self.show_notices)
        self.dashboard.notice_card.open_notice.connect(self.open_notice)
        self.sequence_pages = {key: SequenceView(self, group)
                               for key, group in SEQUENCE_GROUPS.items()}
        self.work_page = WorkPage(self.work)
        self.work_page.set_project(project)
        self.dashboard.work_section.open_project.connect(self.show_work)
        # the 일정 calendar shows the work tasks as its own calendar (업무), edits go back here
        self.schedule_page.work = self.work
        self.schedule_page.open_work.connect(self.show_work)
        self.work.changed.connect(self.schedule_page.work_changed)
        for page in (self.dashboard, self.personal_dashboard, self.category_page, self.search_page,
                     self.schedule_page, self.notices_page, self.work_page, self.reservation_page,
                     self.orders_page, self.budget_page, self.attendance_page, *self.sequence_pages.values()):
            self.stack.addWidget(page)
        self.dashboard.reservation_card.open_tab.connect(self.show_reservations)
        self.dashboard.reservation_card.open_equipment.connect(lambda eid: self.show_reservations(eid))
        self.dashboard.order_card.open_tab.connect(self.show_orders)
        self.dashboard.order_card.open_order.connect(lambda rid: self.show_orders(rid))
        ml.addWidget(self.stack, 1)
        self.stack.currentChanged.connect(lambda _: self._update_header())
        # the pages' inspectors live in the inspector box beside the canvas box
        for page in (self.category_page, self.schedule_page, self.reservation_page, self.orders_page,
                     self.budget_page, self.attendance_page, self.search_page):
            self.inspector_box.add(page, page.inspector_panel)
        for page in self.sequence_pages.values():
            self.inspector_box.add(page, page.inspector)
        self.stack.currentChanged.connect(lambda _: self.inspector_box.show_for(self.stack.currentWidget()))
        self.inspector_box.show_for(self.stack.currentWidget())
        self.splitter.addWidget(holder)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([sidebar_width() + tokens.space("tahoe-inset"), 1440 - sidebar_width()])
        self.setCentralWidget(central)

        self._build_menu()
        from .theme import notifier
        notifier.changed.connect(self._theme_changed)
        QShortcut(QKeySequence("Ctrl+K"), self, activated=self.focus_search)
        self.sync_label = QLabel()
        self.sync_label.setObjectName("muted")
        self.statusBar().addPermanentWidget(self.sync_label)
        if session is not None:
            self._switch(project, session)
        else:
            self._show_location()
            self.work.set_board(self._work_board())
            self.sidebar.refresh(project, None)
            self.show_dashboard()

    def project_title(self) -> str:
        if self.session is not None and self.session.location_set is not None and not self.session.location_set.legacy:
            return f"Google Drive · {self.session.location_set.name}"
        return f"Google Drive · {self.session.name}" if self.session is not None else self.project.name

    def _show_location(self) -> None:
        self.refresh_btn.setVisible(self.session is not None)
        self._update_header()
        if self.session is not None:
            msg = f"Google Drive · {self.session.name} · 폴더의 파일을 직접 편집합니다"
            if self.session.orders is not None:
                n = sum(self.session.order_matches.values())
                msg += f" · 주문 기록 {self.session.orders.count:,}건 읽음, {n:,}개 항목에 반영"
            elif self.session.order_error:
                msg += f" · 주문 기록을 읽지 못했습니다 ({self.session.order_error})"
            self.statusBar().showMessage(msg)
            self.sync_label.setText("☁ 최신 상태")
        else:
            self.statusBar().showMessage(str(self.project.path))
            self.sync_label.setText("로컬 파일")

    # ---- menu ------------------------------------------------------------
    def _build_menu(self) -> None:
        bar = self.menuBar()
        file_menu = bar.addMenu("파일")
        self._action(file_menu, "새 프로젝트…", self.new_project, QKeySequence.New)
        self._action(file_menu, "프로젝트 열기…", self.open_project, QKeySequence.Open)
        self.recent_menu = file_menu.addMenu("최근 프로젝트")
        self.recent_menu.aboutToShow.connect(self._fill_recent)
        self._action(file_menu, "다른 이름으로 저장…", self.save_as, QKeySequence.SaveAs)
        self._action(file_menu, "샘플 프로젝트 열기", self.open_sample)
        file_menu.addSeparator()
        self._action(file_menu, "파일 가져오기 (CSV / Excel)…", self.import_file, QKeySequence("Ctrl+I"))
        drive_menu = bar.addMenu("Google Drive")
        self._action(drive_menu, "위치 세트 열기…", self.choose_location_set, QKeySequence("Ctrl+Shift+O"))
        self.locations_action = self._action(drive_menu, "데이터 위치…", self.edit_locations)
        self._action(drive_menu, "폴더 링크로 연결…", self.connect_cloud)
        self.fav_menu = drive_menu.addMenu("즐겨찾는 프로젝트")
        self.fav_menu.aboutToShow.connect(self._fill_favorites)
        drive_menu.addSeparator()
        self.sync_action = self._action(drive_menu, "지금 최신화", self.sync_folder, QKeySequence("Ctrl+R"))
        self.drive_import_action = self._action(drive_menu, "폴더 전체 다시 읽기", self.reload_folder,
                                                QKeySequence("Ctrl+Shift+R"))
        self.drive_open_action = self._action(drive_menu, "브라우저에서 폴더 열기", self.open_drive_folder)
        drive_menu.addSeparator()
        self.orders_action = self._action(drive_menu, "주문 기록 폴더 연결…", self.set_order_folder)
        self.order_lists_action = self._action(drive_menu, "주문 기록으로 기타·개인 품목 파일 만들기…",
                                               self.make_order_lists)
        drive_menu.addSeparator()
        self.webapp_action = self._action(drive_menu, "모바일 웹앱 비밀번호…", self.edit_webapp)
        drive_menu.addSeparator()
        self._action(drive_menu, "Google 계정 로그아웃", self.sign_out)
        drive_menu.aboutToShow.connect(self._update_drive_menu)
        mail_menu = bar.addMenu("메일")
        self._action(mail_menu, "구성원 관리…", self.edit_members)
        self._action(mail_menu, "담당자 지정…", lambda: self.edit_owners(None))
        self._action(mail_menu, "운영 브리핑 메일…", self.edit_briefing)
        self._action(mail_menu, "장바구니 · 주문 요청…", self.open_cart, QKeySequence("Ctrl+Shift+C"))
        edit_menu = bar.addMenu("편집")
        self._action(edit_menu, "Common Asset에 카테고리 추가…", lambda: self.add_category("common"))
        self._action(edit_menu, "Personal Asset에 카테고리 추가…", lambda: self.add_category("personal"))
        view_menu = bar.addMenu("보기")
        self._action(view_menu, "공용 대시보드", self.show_dashboard, QKeySequence("Ctrl+1"))
        self._action(view_menu, "개인 품목 대시보드", lambda: self.show_dashboard("personal"), QKeySequence("Ctrl+2"))
        self._action(view_menu, "탭 표시 설정…", lambda: self.sidebar.edit_tabs())
        # the sidebar can be hidden and brought back three ways: toolbar button, menu, shortcut
        self._action(view_menu, "사이드바 보기/숨기기", self.toggle_sidebar, QKeySequence("Ctrl+Meta+S"))
        view_menu.addSeparator()
        from . import theme
        look = view_menu.addMenu("화면 모양")
        self._look_actions: list[tuple[QAction, str, str]] = []
        for kind, choices in (("mode", theme.APPEARANCES), ("contrast", theme.CONTRASTS)):
            for key, label in choices.items():
                a = look.addAction(label, lambda kd=kind, k=key: theme.set_appearance(**{kd: k}))
                a.setCheckable(True)
                self._look_actions.append((a, kind, key))
            look.addSeparator()
        look.aboutToShow.connect(self._sync_look_menu)
        self._action(view_menu, "통합 검색", self.focus_search, QKeySequence("Ctrl+F"))

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.ActivationChange and hasattr(self, "sidebar"):
            self.sidebar._select()  # tahoe: accent pill while active, gray fill when not

    def toggle_sidebar(self) -> None:
        show = not self.sidebar.isVisible()
        self.sidebar.setVisible(show)
        self.show_sidebar_btn.setVisible(not show)

    HEADER_ICONS = {"dash:common": "square.grid.2x2", "dash:personal": "person", "schedule": "calendar",
                    "work": "checkmark.circle", "notices": "bell", "reservations": "clock",
                    "orders": "shippingbox", "budget": "wonsign", "attendance": "alarm",
                    "seq:common": "scissors", "seq:personal": "scissors"}

    def _update_header(self) -> None:
        """Content header: the page's symbol and name · where the data lives."""
        page = self.stack.currentWidget()
        key, title = None, ""
        if page is self.dashboard:
            key, title = "dash:common", "공용 대시보드"
        elif page is self.personal_dashboard:
            key, title = "dash:personal", "개인 품목 대시보드"
        elif page is self.schedule_page:
            key, title = "schedule", "일정"
        elif page is self.notices_page:
            key, title = "notices", "공지사항"
        elif page is self.work_page:
            key, title = "work", "업무"
        elif page is self.reservation_page:
            key, title = "reservations", "장비 예약"
        elif page is self.orders_page:
            key, title = "orders", "주문"
        elif page is self.budget_page:
            key, title = "budget", "예산"
        elif page is self.attendance_page:
            key, title = "attendance", "출퇴근 기록"
        elif page in self.sequence_pages.values():
            key = next(k for k, v in self.sequence_pages.items() if v is page)
            title = SEQUENCE_GROUPS[key]
        elif page is self.search_page:
            title = "통합 검색"
        elif page is self.category_page:
            cat = self.project.category(self.current_category) if self.current_category else None
            title = cat.name if cat else ""
        symbol = self.HEADER_ICONS.get(key, "magnifyingglass" if page is self.search_page else "list.bullet")
        self.header_icon.setPixmap(icons.pixmap(
            symbol, tokens.text("label-secondary", "region-canvas", large=True), size=tokens.space("icon-md")))
        self.header_title.setText(title)
        self.header_meta.setText(self.project_title())
        # list filters and 이 카테고리에서 찾기 only while a list is shown; elsewhere just search
        listing = page is self.category_page
        was = getattr(self, "_was_listing", False)
        self.list_filters.setVisible(listing)
        self.in_category.setVisible(listing)
        if listing != was and self.in_category.isChecked():
            self.search_edit.clear()  # the text was a filter of the other screen
            self.category_page.set_text_filter("")
        self.search_edit.setPlaceholderText("이 카테고리에서 찾기" if listing and self.in_category.isChecked()
                                            else "검색 (⌘K)")
        self._was_listing = listing

    def _sync_look_menu(self) -> None:
        from . import theme
        now = dict(zip(("mode", "contrast"), theme.appearance()))
        for a, kind, key in self._look_actions:
            a.setChecked(now[kind] == key)

    @staticmethod
    def _magnifier():  # HIG: Search
        return icons.pixmap("magnifyingglass", tokens.text("label-secondary", "region-canvas", large=True),
                            size=tokens.space("icon-sm"))

    def eventFilter(self, obj, event):
        if obj is self.search_edit and event.type() == QEvent.Resize:
            lbl = self._search_icon
            lbl.adjustSize()
            lbl.move(tokens.space("control-pad"), (obj.height() - lbl.height()) // 2)
        return super().eventFilter(obj, event)

    def _theme_changed(self) -> None:
        """Repaint what draws its own colors (charts, state colors, sidebar fonts)."""
        self._search_icon.setPixmap(self._magnifier())
        self._update_header()
        self.sidebar.refresh(self.project, self.current_category, self.sidebar.current_group)
        for d in (self.dashboard, self.personal_dashboard):
            d.refresh()
        self.category_page.table.viewport().update()
        self._notices_changed()
        self.schedule_page.theme_changed()

    def _action(self, menu, text, slot, shortcut=None) -> QAction:
        act = QAction(text, self)
        if shortcut is not None:
            act.setShortcut(shortcut)
        act.triggered.connect(slot)
        menu.addAction(act)
        return act

    def _update_drive_menu(self) -> None:
        on = self.session is not None
        for act in (self.sync_action, self.drive_import_action, self.drive_open_action, self.orders_action,
                    self.order_lists_action, self.locations_action, self.webapp_action):
            act.setEnabled(on)

    def _fill_favorites(self) -> None:
        self.fav_menu.clear()
        favs = settings.cloud_favorites()
        if not favs:
            a = self.fav_menu.addAction("(없음)")
            a.setEnabled(False)
        for f in favs:
            label = f"☁ {f.get('name') or f['folder_id']}"
            act = self.fav_menu.addAction(label, lambda fid=f["folder_id"]: self.open_cloud(fid))
            act.setCheckable(True)
            act.setChecked(self.session is not None and self.session.folder_id == f["folder_id"])

    def _fill_recent(self) -> None:
        self.recent_menu.clear()
        recent = settings.recent_projects()
        if not recent:
            a = self.recent_menu.addAction("(없음)")
            a.setEnabled(False)
        for p in recent:
            self.recent_menu.addAction(str(p), lambda p=p: self.load_project(p))

    # ---- navigation ------------------------------------------------------
    def open_tab(self, key: str, category_id: int | None = None) -> None:
        if category_id is not None:
            self.show_category(category_id)
        elif key == "dash:common":
            self.show_dashboard("common")
        elif key == "dash:personal":
            self.show_dashboard("personal")
        elif key == "schedule":
            self.show_schedule()
        elif key == "notices":
            self.show_notices()
        elif key == "work":
            self.show_work()
        elif key == "reservations":
            self.show_reservations()
        elif key == "orders":
            self.show_orders()
        elif key == "budget":
            self.show_budget()
        elif key == "attendance":
            self.show_attendance()
        elif key in SEQUENCE_GROUPS:
            self.show_sequences(key)

    def show_dashboard(self, scope: str = "common") -> None:
        self.current_category = None
        page = self.personal_dashboard if scope == "personal" else self.dashboard
        self.sidebar.set_nav(f"dash:{'personal' if scope == 'personal' else 'common'}")
        page.refresh()
        self.stack.setCurrentWidget(page)

    def show_schedule(self) -> None:
        self.current_category = None
        self.sidebar.set_nav("schedule")
        self.stack.setCurrentWidget(self.schedule_page)

    def show_notices(self) -> None:
        self.current_category = None
        self.sidebar.set_nav("notices")
        self.notices_page.set_board(self.session.notices if self.session else None, self.project)
        self.stack.setCurrentWidget(self.notices_page)

    def open_notice(self, nid: str) -> None:
        self.show_notices()
        self.notices_page.show_notice(nid)

    def show_work(self, project_id: str = "") -> None:
        self.current_category = None
        self.sidebar.set_nav("work")
        if project_id:
            self.work_page.show_project(project_id)
        else:
            self.work_page.refresh()
        self.stack.setCurrentWidget(self.work_page)

    def show_reservations(self, equipment_id: str = "") -> None:
        self.current_category = None
        self.sidebar.set_nav("reservations")
        if equipment_id:
            self.reservation_page.show_equipment(equipment_id)
        else:
            self.reservation_page.refresh()
        self.stack.setCurrentWidget(self.reservation_page)

    def open_reservation(self, rid: str) -> None:
        board = self.session.reservations if self.session else None
        r = next((x for x in board.reservations() if x.id == rid), None) if board else None
        self.show_reservations()
        if r is not None:
            self.reservation_page.show_equipment(r.equipment_id, r.start)
            self.reservation_page._picked(r)

    def show_orders(self, order_id: str = "") -> None:
        self.current_category = None
        self.sidebar.set_nav("orders")
        self.orders_page.refresh()
        self.stack.setCurrentWidget(self.orders_page)
        if order_id:
            self.orders_page.open(order_id)

    def show_budget(self) -> None:
        self.current_category = None
        self.sidebar.set_nav("budget")
        self.budget_page.refresh()
        self.stack.setCurrentWidget(self.budget_page)

    def show_attendance(self) -> None:
        self.current_category = None
        self.sidebar.set_nav("attendance")
        s = self.session
        loc = ""
        if s is not None and s.location_set is not None and not s.location_set.legacy:
            loc = s.location_set.path("attendance").strip("/").replace("/", " / ")
        self.attendance_page.set_store(s.attendance if s else None, self.project, loc)
        self.stack.setCurrentWidget(self.attendance_page)

    def create_attendance(self) -> None:
        """No 출퇴근 기록 sheet yet: make one in the lab folder, one column per member."""
        from .cloud import run_blocking
        s = self.session
        if s is None:
            return
        people = [m.get("name") or m.get("email", "") for m in self.project.members()]
        people = [p for p in people if p]
        if not people:
            QMessageBox.information(self, "출퇴근 기록", "구성원이 없습니다. 먼저 ‘구성원’에서 연구실 구성원을 추가해 주세요.")
            return
        if QMessageBox.question(self, "출퇴근 기록",
                                f"Lab Management 폴더에 ‘출퇴근 기록’ Google 시트를 만들까요?\n"
                                f"구성원 {len(people)}명이 열이 되고, 이번 달·다음 달 시트와 설정 탭이 생깁니다.") \
                != QMessageBox.Yes:
            return
        try:
            run_blocking(self, lambda: s.create_attendance(people), "출퇴근 기록 시트를 만드는 중…")
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "출퇴근 기록", f"시트를 만들지 못했습니다.\n{exc}")
            return
        from ..core import locations
        if s.location_set is not None:
            locations.remember_set(s.location_set)
        self.show_attendance()

    def _search_sources(self) -> dict:
        """What 통합 검색 reads besides the inventory."""
        s = self.session
        if s is None:
            return {"events": list(self.schedule_page.events)}
        return {"order_requests": s.order_requests, "orders": s.orders, "reservations": s.reservations,
                "notices": s.notices.notices if s.notices else None, "work": s.work, "budget": s.budget,
                "events": list(self.schedule_page.events)}

    def open_record_hit(self, hit) -> None:
        """‘열기’ on a 통합 검색 record: go to its tab and show it there."""
        from datetime import date as _date, datetime as _dt
        k = hit.kind
        if k == "order_request":
            self.show_orders(hit.ref)
        elif k == "reservation":
            self.open_reservation(hit.ref)
        elif k == "equipment":
            self.show_reservations(hit.ref)
        elif k == "notice":
            self.open_notice(hit.ref)
        elif k == "work":
            self.show_work(hit.ref[0] if hit.ref else "")
        elif k == "event":
            ev = hit.ref
            self.show_schedule()
            d = ev.start.date() if isinstance(ev.start, _dt) else ev.start
            if isinstance(d, _date):
                self.schedule_page.go_date(d)
            found = next((e for e in self.schedule_page.events if e.id == ev.id), None)
            if found is not None:
                self.schedule_page.select(found)
        elif k in ("ledger", "fee_round"):
            self.show_budget()

    def _records_changed(self) -> None:
        """장비 예약 / 주문 changed: the dashboard boxes and the 일정 overlay follow."""
        s = self.session
        self.dashboard.reservation_card.set_board(s.reservations if s else None)
        self.dashboard.order_card.set_board(s.order_requests if s else None)
        self.schedule_page.work_changed()

    def _set_record_pages(self) -> None:
        s = self.session
        self.reservation_page.set_board(s.reservations if s else None, self.project)
        self.orders_page.set_board(s.order_requests if s else None, self.project)
        self.budget_page.set_board(s.budget if s else None, self.project)
        self._records_changed()
    def show_sequences(self, key: str = "seq:common") -> None:
        """A Sequence Map tab. The maps are read from Drive the first time one is
        opened rather than at launch, since the folder can hold a lot of them."""
        page = self.sequence_pages.get(key)
        if page is None:
            return
        self.current_category = None
        self.sidebar.set_nav(key)
        if self._sequences is None:
            self._sequences = self._load_sequences()
        page.set_library(self._sequences)
        self.stack.setCurrentWidget(page)
        self._update_header()

    def _load_sequences(self):
        """Read every .dna map the folder holds, then index the Oligos and the
        restriction enzyme stock against them."""
        from ..core.sequence import SequenceLibrary
        library = SequenceLibrary(getattr(self.session, "remote", None),
                                  getattr(getattr(self.session, "model", None), "cache_dir", None))
        # the user's thresholds: at least this many bases in a row, and a run
        # shorter than 15 nt only counts when its Tm reaches the minimum
        prefs = settings.load_settings()
        library.set_limits(min_match=prefs.get("sequence_min_match"),
                           min_tm=prefs.get("sequence_min_tm"),
                           full_length=prefs.get("sequence_full_length"))

        def work():
            client = getattr(self.session, "client", None)
            root = self._sequence_root()
            if client is not None and root:
                from ..integrations.gdrive.folder_remote import sequence_files
                _, files = sequence_files(client, root)
                library.load_entries(files, lambda fid, dest: client.download(fid, dest))
            elif library.remote is not None and hasattr(library.remote, "list_sequence_files"):
                library.load_remote()
            else:
                folder = settings.user_data_dir() / "Sequence Map"
                if folder.is_dir():
                    library.load_folder(folder)
            library.read_oligos(self.project)
            library.read_enzymes(self.project)
            library.reindex()

        try:
            cloud_ui.run_blocking(self, work, "Sequence Map을 읽는 중…")
        except Exception:  # noqa: BLE001  a folder we cannot reach must not block the tab
            try:
                work()
            except Exception:  # noqa: BLE001
                library.errors.append("Sequence Map 폴더를 읽지 못했습니다.")
        return library

    def _sequence_root(self) -> str:
        """Folder to look for "Sequence Map" in: the folder the app opened, or
        the one a chosen location set points at when there is one."""
        if self.session is None:
            return ""
        chosen = getattr(self.session, "location_set", None)
        get = getattr(chosen, "get", None)
        if callable(get):
            try:
                folder_id = get("sequence")
            except TypeError:          # a mapping that wants a default
                folder_id = get("sequence", "")
            if folder_id:
                return str(folder_id)
        return getattr(self.session, "folder_id", "") or ""

    def _work_board(self):
        """업무.xlsx in the Drive folder, or a local file for a project on this Mac."""
        if self.session is not None and getattr(self.session, "work", None) is not None:
            return self.session.work
        from ..core.work import WorkBoard
        board = WorkBoard(None, settings.user_data_dir(), local=settings.user_data_dir() / "업무.xlsx")
        try:
            board.load()
        except Exception:  # noqa: BLE001
            pass
        return board

    def _notices_changed(self) -> None:
        board = self.session.notices if self.session is not None else None
        names = [m.get("name") or m.get("email", "") for m in self.project.members()] if self.project else []
        self.dashboard.notice_card.set_notices(board.notices if board is not None else None, names)

    def show_category(self, category_id: int, item_id: int | None = None, group: str | None = None) -> None:
        if self.project.category(category_id) is None:
            return
        self.current_category = category_id
        self.sidebar.set_nav(None)
        self.category_page.show_category(category_id, item_id, group=group)
        self.sidebar.refresh(self.project, category_id, self.category_page.group_combo.currentData())
        self.stack.setCurrentWidget(self.category_page)
        self._update_header()

    def make_order_lists(self) -> None:
        """기타 품목 (common orders no inventory file lists) and one list per person
        from the personal order sheet, as new Excel files in the Drive folder."""
        from ..core import order_items
        if self.session is None:
            return
        if self.session.orders is None:
            QMessageBox.information(self, "품목 파일 만들기", "먼저 Google Drive → 주문 기록 폴더 연결…로 주문 폴더를 연결하세요.")
            return
        plans = order_items.plan(self.project, self.session.orders)
        if not plans:
            QMessageBox.information(self, "품목 파일 만들기", "새로 정리할 품목이 없습니다. 이미 모두 들어 있어요.")
            return
        new = [p for p in plans if p.category_id is None]
        old = [p for p in plans if p.category_id is not None]
        lines = [f"· {p.name}: {len(p.rows)}개" + (" (기존 파일에 추가)" if p.category_id else "") for p in plans[:12]]
        if len(plans) > 12:
            lines.append(f"· … 외 {len(plans) - 12}개 파일")
        personal = [p for p in plans if p.scope == "personal"]
        text = ("주문 기록을 정리해 아래 엑셀 파일을 드라이브 폴더에 만듭니다.\n\n" + "\n".join(lines)
                + f"\n\n기타 품목은 Common Asset, 개인 품목 {len(personal)}개는 폴더 안 "
                f"‘{order_items.PERSONAL_FOLDER}’ 하위 폴더에 넣고 Personal Asset으로 표시합니다.\n"
                "기존 재고 파일은 바꾸지 않습니다. 계속할까요?")
        if QMessageBox.question(self, "품목 파일 만들기", text) != QMessageBox.Yes:
            return
        try:
            if old:
                order_items.apply(self.session.model, self.project, old)
            if new:
                cloud_ui.run_blocking(self, lambda: order_items.apply(self.session.model, None, new),
                                      f"엑셀 파일 {len(new)}개를 만들어 올리는 중…")
            cloud_ui.run_blocking(self, self.session.model.flush, "설정을 저장하는 중…")
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "품목 파일 만들기", f"파일을 만들지 못했습니다.\n{exc}")
            return
        self.reload_folder()
        QMessageBox.information(self, "품목 파일 만들기",
                                f"{len(plans)}개 파일에 {sum(len(p.rows) for p in plans):,}개 품목을 정리했습니다.")

    # ---- mail: owners, briefing, cart -------------------------------------
    def edit_members(self) -> None:
        from .mail_ui import MembersDialog
        if MembersDialog(self.project, self).exec():
            self.sync_label.setText(f"구성원 {len(self.project.members())}명을 저장했습니다")

    def edit_owners(self, cid: int | None) -> None:
        from .mail_ui import OwnersDialog
        if OwnersDialog(self.project, self, focus_category=cid).exec():
            self.sync_label.setText("담당자를 저장했습니다")
            if self.category_page.category_id is not None:
                self.category_page._refresh_header()

    def edit_briefing(self) -> None:
        from .mail_ui import BriefingDialog
        BriefingDialog(self.project, self, drive_mode=self.session is not None,
                       sources=self._briefing_sources).exec()

    def _briefing_sources(self) -> dict:
        from ..core.services.briefing_run import today_events
        s = self.session
        out = {"events": today_events(self.project)}
        if s is not None:
            out.update(reservations=s.reservations, work=s.work, order_requests=s.order_requests)
        return out

    def add_to_cart(self, item_ids: list) -> None:
        n = self.cart.add(self.project, list(item_ids))
        self._update_cart_btn()
        self.statusBar().showMessage(f"장바구니에 {n}개를 담았습니다 (전체 {len(self.cart)}개)", 4000)

    def open_cart(self) -> None:
        from .mail_ui import CartDialog
        if not len(self.cart):
            QMessageBox.information(self, "장바구니", "장바구니가 비어 있습니다.\n표에서 항목을 우클릭하고 ‘장바구니에 담기’를 고르세요.")
            return
        CartDialog(self.project, self.cart, self, on_sent=self._cart_sent).exec()
        self._update_cart_btn()

    def _cart_sent(self, lines, requester: str, note: str) -> None:
        """The cart's order request mail went out: one row per item in 주문 요청."""
        board = self.session.order_requests if self.session else None
        if board is None:
            return
        from ..core.order_requests import from_cart_lines
        from .orders_view import email_of
        for req in from_cart_lines(lines, requester, email_of(self.project, requester), note):
            board.save(req)
        self.orders_page.refresh()

    def _update_cart_btn(self) -> None:
        n = len(self.cart)  # icon only: the count is the red badge on the button
        self.cart_badge.set_count(n)
        self.cart_btn.setAccessibleName(f"장바구니 {n}개" if n else "장바구니")
        self.cart_btn.setToolTip((f"장바구니 {n}개 · " if n else "장바구니 · ")
                                 + "담은 품목을 담당자에게 주문 요청 메일로 보냅니다 (메일 메뉴에도 있습니다)")

    def _group_changed(self, group) -> None:
        if self.category_page.category_id is not None:
            self.sidebar.refresh(self.project, self.category_page.category_id, group)

    # ---- right-click actions on categories and sheets ----------------------
    def rename_category(self, cid: int) -> None:
        cat = self.project.category(cid)
        name, ok = QInputDialog.getText(self, "이름 바꾸기", "카테고리 이름" +
                                        (" (드라이브 파일 이름도 바뀝니다)" if self.session else ""), text=cat.name)
        if ok and name.strip() and name.strip() != cat.name:
            from ..core import nav_tabs
            nav_tabs.rename_tab(nav_tabs.category_key(cat.scope, cat.name), nav_tabs.category_key(cat.scope, name.strip()))
            self.project.update_category(cid, name=name.strip())
            self.show_category(cid)

    def edit_category(self, cid: int) -> None:
        self.show_category(cid)
        self.category_page.edit_category()

    def delete_category(self, cid: int) -> None:
        cat = self.project.category(cid)
        n = self.project.count_items(cid)
        where = "\n드라이브의 파일은 휴지통으로 이동합니다." if self.session else ""
        if confirm_delete(self, "카테고리 삭제", f"‘{cat.name}’과 항목 {n}개를 삭제할까요?{where}") \
                != QMessageBox.Yes:
            return
        self.project.delete_category(cid)
        if self.category_page.category_id == cid:
            self.category_page.category_id = None
            self.category_page.model = None
        self._structure_changed()

    def add_sheet(self, cid: int) -> None:
        name, ok = QInputDialog.getText(self, "시트 추가", "새 시트(하위 그룹) 이름")
        name = name.strip()
        if not ok or not name:
            return
        if name in self.project.subgroups(cid):
            QMessageBox.information(self, "시트 추가", f"‘{name}’ 시트가 이미 있습니다.")
            return
        self.project.add_subgroup(cid, name)
        self.show_category(cid, group=name)

    def rename_sheet(self, cid: int, group: str) -> None:
        name, ok = QInputDialog.getText(self, "시트 이름 바꾸기", "시트 이름", text=group)
        name = name.strip()
        if not ok or not name or name == group:
            return
        if name in self.project.subgroups(cid):
            QMessageBox.information(self, "시트 이름 바꾸기", f"‘{name}’ 시트가 이미 있습니다.")
            return
        self.project.rename_subgroup(cid, group, name)
        self.show_category(cid, group=name)

    def delete_sheet(self, cid: int, group: str) -> None:
        if len(self.project.subgroups(cid)) <= 1:
            QMessageBox.information(self, "시트 삭제", "마지막 시트는 지울 수 없습니다. 카테고리를 삭제하세요.")
            return
        n = sum(1 for it in self.project.items(cid) if it.subgroup == group)
        if confirm_delete(self, "시트 삭제", f"‘{group}’ 시트와 항목 {n}개를 삭제할까요?"
                                + ("\n엑셀 파일에서도 이 시트가 지워집니다." if self.session else "")) != QMessageBox.Yes:
            return
        self.project.delete_subgroup(cid, group)
        self.show_category(cid)

    def add_item_to(self, cid: int, group: str) -> None:
        self.show_category(cid, group=group)
        self.category_page.add_item()

    def show_item(self, category_id: int, item_id: int) -> None:
        self.show_category(category_id, item_id)

    def focus_search(self) -> None:
        self.search_edit.setFocus()
        self.search_edit.selectAll()

    def _list_shown(self) -> bool:
        return self.stack.currentWidget() is self.category_page and self.current_category is not None

    def _scope_toggled(self, on: bool) -> None:
        data = settings.load_settings()
        data["search_in_category"] = on
        settings.save_settings(data)
        if self._list_shown():
            self.category_page.set_text_filter(self.search_edit.text().strip() if on else "")
            self.search_edit.setPlaceholderText("이 카테고리에서 찾기" if on else "검색 (⌘K)")
            if not on and self.search_edit.text().strip():
                self.run_search()

    def _filter_cleared(self) -> None:
        if self._list_shown() and self.in_category.isChecked():
            self.search_edit.clear()

    def run_search(self) -> None:
        text = self.search_edit.text().strip()
        if self._list_shown() and self.in_category.isChecked():  # filter the list in place
            self.category_page.set_text_filter(text)
            return
        if not text:
            if self.stack.currentWidget() is self.search_page:
                self.show_dashboard()
            return
        self.search_page.run(text)
        self.sidebar.clear_selection()
        self.sidebar.set_nav(None)
        self.stack.setCurrentWidget(self.search_page)

    def _structure_changed(self) -> None:
        if self.category_page.category_id is None:
            self.sidebar.refresh(self.project, None)
            self.show_dashboard()
        else:
            self.sidebar.refresh(self.project, self.category_page.category_id,
                                 self.category_page.group_combo.currentData())

    # ---- categories / import --------------------------------------------
    def add_category(self, scope: str) -> None:
        from .dialogs.category_dialog import PERSONAL_FIELDS, STARTER_FIELDS, CategoryDialog
        dlg = CategoryDialog(self, scope=scope)
        if not dlg.exec():
            return
        v = dlg.values()
        cat = self.project.add_category(v["scope"], v["name"], threshold=v["threshold"], kind=v["kind"])
        for name, ftype, role in (PERSONAL_FIELDS if v["scope"] == "personal" else STARTER_FIELDS):
            self.project.add_field(cat.id, name, ftype, role)
        self.show_category(cat.id)

    def import_file(self) -> None:
        from .dialogs.import_dialog import ImportDialog
        path, _ = QFileDialog.getOpenFileName(self, "가져올 파일", "", "표 파일 (*.xlsx *.xls *.xlsm *.csv *.tsv)")
        if not path:
            return
        if self.session is not None:
            # Drive folder: the file itself goes into the folder and shows up as a category.
            name = Path(path).name
            try:
                cloud_ui.run_blocking(self, lambda: self.session.remote.upload_new(name, Path(path)),
                                      f"{name}을(를) 폴더에 올리는 중…")
            except Exception as exc:  # noqa: BLE001
                QMessageBox.warning(self, "Google Drive", f"올리지 못했습니다.\n{exc}")
                return
            self.reload_folder()
            return
        scope = "common"
        if self.current_category:
            scope = self.project.category(self.current_category).scope
        try:
            dlg = ImportDialog(self.project, path, self, scope=scope, category_id=self.current_category)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "가져오기", f"파일을 읽지 못했습니다.\n{exc}")
            return
        if dlg.exec() and dlg.result_category_id:
            self.show_category(dlg.result_category_id)

    # ---- projects --------------------------------------------------------
    def load_project(self, path: Path | str) -> None:
        path = Path(path)
        try:
            project = Project(path)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "프로젝트 열기", f"열 수 없습니다.\n{exc}")
            return
        if not self._leave_cloud():
            project.close()
            return
        self._switch(project)

    def _leave_cloud(self) -> bool:
        """Save pending edits before switching away from a Drive folder."""
        if self.session is None:
            return True
        if not self.session.flush(self):
            return False
        self.session.stop()
        return True

    def _switch(self, project: Project, session=None) -> None:
        old = self.project
        if self.session is not None and self.session is not session:
            self.session.stop()
            self.session.deleteLater()
        self.project = project
        self.session = session
        if session is not None:
            session.setParent(self)
            session.status_changed.connect(self._sync_status, Qt.UniqueConnection)
            session.remote_changed.connect(self._rebuild_folder, Qt.UniqueConnection)
            session.boards_changed.connect(self._boards_changed, Qt.UniqueConnection)
        for page in (self.dashboard, self.personal_dashboard, self.category_page, self.search_page,
                     self.schedule_page, self.work_page):
            page.set_project(project)
        self._sequences = None   # a different folder has its own maps
        for page in self.sequence_pages.values():
            page.set_library(None)
        self._notices_changed()
        self.work.set_board(self._work_board())
        self._set_record_pages()
        if old is not project:
            old.observer = None
            old.close()
        if session is None:
            settings.remember_project(project.path)
        self._show_location()
        self.search_edit.clear()
        self.sidebar.refresh(project, None)
        self.show_dashboard()

    # ---- Google Drive ----------------------------------------------------
    def connect_cloud(self) -> None:
        dlg = cloud_ui.CloudConnectDialog(self)
        if dlg.exec() and dlg.folder_id:
            self.open_cloud(dlg.folder_id)

    def choose_location_set(self) -> None:
        from .locations_ui import StartDialog
        dlg = StartDialog(self)
        if dlg.exec() and dlg.chosen is not None:
            self.open_location_set(dlg.chosen)

    def edit_locations(self) -> None:
        """데이터 위치: change where each feature's file is, then read again from there."""
        from ..core import locations
        from ..core.locations import LocationSet
        from .locations_ui import LocationSetDialog
        if self.session is None:
            return
        s = self.session.location_set or LocationSet.legacy_folder(self.session.folder_id, self.session.name)
        dlg = LocationSetDialog(s, self, client=self.session.client)
        if not dlg.exec():
            return
        locations.remember_set(dlg.result_set)
        self.open_location_set(dlg.result_set, force=True)

    def open_location_set(self, s, force: bool = False) -> None:
        cur = self.session.location_set if self.session else None
        if not force and cur is not None and cur.id == s.id:
            return
        if not self._leave_cloud():
            return
        opened = cloud_ui.open_folder_session(self, s.root_id, location_set=s)
        if opened is None:
            if self.session is not None:
                self.session.start()
            return
        session, project = opened
        self._switch(project, session)

    def open_cloud(self, folder_id: str) -> None:
        if self.session is not None and self.session.folder_id == folder_id:
            return
        if not self._leave_cloud():
            return
        opened = cloud_ui.open_folder_session(self, folder_id)
        if opened is None:
            if self.session is not None:  # stay on the current folder
                self.session.start()
            return
        session, project = opened
        self._switch(project, session)

    def hidden_tabs_changed(self) -> None:
        """A list was hidden or shown in the sidebar: the dashboards follow."""
        for page in (self.dashboard, self.personal_dashboard):
            page.refresh()

    def edit_webapp(self) -> None:
        """설정 · 웹앱: the mobile web app's lab password, kept in the folder's settings file."""
        from .webapp_dialog import WebAppDialog
        if self.session is None:
            QMessageBox.information(self, "모바일 웹앱", "Google Drive 폴더 프로젝트를 열면 웹앱 비밀번호를 바꿀 수 있습니다.")
            return
        WebAppDialog(self.project, lambda: self.session.sync_now(self), self).exec()

    def sync_folder(self) -> None:
        """최신화: send what is queued here and fetch only the files that changed."""
        if self.session is not None:
            self.session.sync_now(self)

    def _boards_changed(self) -> None:
        """공지사항 / 업무 came in changed from Drive: show them (nothing else is rebuilt)."""
        self._notices_changed()
        if self.stack.currentWidget() is self.notices_page:
            self.notices_page.set_board(self.session.notices if self.session else None, self.project)
        self.work.changed.emit()
        for page in (self.reservation_page, self.orders_page, self.budget_page):
            if page.inspector_panel.wanted:
                continue  # don't pull the form out from under the user
            page.refresh()
        if self.stack.currentWidget() is self.attendance_page:
            self.attendance_page.refresh()
        self._records_changed()

    def _rebuild_folder(self) -> None:
        """Someone else changed inventory files: rebuild from the files just downloaded
        (no network) and stay on the page that was open."""
        if self.session is None:
            return
        page = self.stack.currentWidget()
        cur = self.current_category
        cur_file = self.session.model.cat_file.get(cur) if cur else None
        work_pid = self.work_page.pid
        self._switch(self.session.rebuild(), self.session)
        if cur_file:
            for cid, fid in self.session.model.cat_file.items():
                if fid == cur_file:
                    self.show_category(cid)
                    break
        elif page is self.personal_dashboard:
            self.show_dashboard("personal")
        elif page is self.schedule_page:
            self.show_schedule()
        elif page is self.notices_page:
            self.show_notices()
        elif page is self.work_page:
            self.show_work(work_pid)
        elif page is self.reservation_page:
            self.show_reservations()
        elif page is self.orders_page:
            self.show_orders()
        elif page is self.budget_page:
            self.show_budget()

    def reload_folder(self) -> None:
        """Read every file in the folder again (someone else changed it, or on request)."""
        if self.session is None:
            return
        cur = self.current_category
        cur_file = self.session.model.cat_file.get(cur) if cur else None
        try:
            project = self.session.reload(self)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Google Drive", f"폴더를 다시 읽지 못했습니다.\n{exc}")
            return
        self._switch(project, self.session)
        self.sync_label.setText("☁ 폴더와 주문 기록을 최신으로 불러왔습니다")
        if self.stack.currentWidget() is self.budget_page:
            self.show_budget()
        if cur_file:
            for cid, fid in self.session.model.cat_file.items():
                if fid == cur_file:
                    self.show_category(cid)

    def set_order_folder(self) -> None:
        from PySide6.QtWidgets import QInputDialog
        from ..integrations.gdrive.drive import DriveError, folder_url, parse_folder_id
        if self.session is None:
            return
        cur = self.session.order_folder_id
        text, ok = QInputDialog.getText(
            self, "주문 기록 폴더",
            "주문 기록 엑셀이 있는 Google Drive 폴더 링크를 붙여넣으세요.\n"
            "각 항목의 ‘마지막 주문 날짜 · 수량’을 이 폴더에서 찾아 앱 안에만 보여 줍니다(파일은 바뀌지 않음).\n"
            "비워 두면 연결을 해제합니다.",
            text=folder_url(cur) if cur else "")
        if not ok:
            return
        try:
            fid = parse_folder_id(text) if text.strip() else ""
        except DriveError as exc:
            QMessageBox.warning(self, "주문 기록 폴더", str(exc))
            return
        self.session.set_order_folder(fid)
        self.reload_folder()

    def open_drive_folder(self) -> None:
        if self.session is not None:
            from ..integrations.gdrive.drive import folder_url
            QDesktopServices.openUrl(QUrl(folder_url(self.session.folder_id)))

    def sign_out(self) -> None:
        from ..integrations.gdrive import drive
        if QMessageBox.question(self, "로그아웃", "Google 계정에서 로그아웃할까요? 다음 연결 때 다시 로그인합니다.") \
                == QMessageBox.Yes:
            drive.sign_out()

    def _sync_status(self, text: str) -> None:
        if self.session is not None:
            self.sync_label.setText(f"☁ {text}")

    def new_project(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "새 프로젝트", str(settings.user_data_dir() / f"새 프로젝트{FILE_SUFFIX}"),
                                              FILE_FILTER)
        if not path:
            return
        if not path.endswith(FILE_SUFFIX):
            path += FILE_SUFFIX
        if not self._leave_cloud():
            return
        self._switch(Project.create(path))

    def open_project(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "프로젝트 열기", str(self.project.path.parent), FILE_FILTER)
        if path:
            self.load_project(path)

    def open_sample(self) -> None:
        sample = settings.sample_project_path()
        if not sample.exists():
            QMessageBox.information(self, "샘플 프로젝트", "샘플 프로젝트가 없습니다.")
            return
        self.load_project(sample)

    def save_as(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "다른 이름으로 저장", str(self.project.path.with_name(
            self.project.path.stem + " 사본" + FILE_SUFFIX)), FILE_FILTER)
        if not path:
            return
        if not path.endswith(FILE_SUFFIX):
            path += FILE_SUFFIX
        if not self._leave_cloud():
            return
        self.project.observer = None
        project = self.project.save_as(path)
        self.project = project  # the old connection is already closed
        self._switch(project)

    def closeEvent(self, event) -> None:
        if self.session is not None:
            if not self.session.flush(self):
                event.ignore()
                return
        else:
            settings.remember_project(self.project.path)
        self.project.close()
        super().closeEvent(event)
