"""Asset Manager entry point:  python -m app.main  [project.labproj]"""
from __future__ import annotations

import sys
from pathlib import Path

if __package__ in (None, ""):  # running as a script / frozen app
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    __package__ = "app"

from PySide6.QtGui import QIcon  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app.core import settings  # noqa: E402
from app.core.project import Project  # noqa: E402
from app.ui.main_window import MainWindow  # noqa: E402
from app.ui.theme import apply_theme  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv if argv is None else argv
    if "--reminders" in argv:  # event alert mails, every few minutes from launchd
        from app.core.services.reminders import run as run_reminders
        return run_reminders()
    if "--briefing" in argv:  # scheduled run from launchd: no window
        from app.core.services.briefing_run import run
        return run(force="--force" in argv)
    app = QApplication(argv)
    app.setApplicationName(settings.APP_NAME)
    app.setOrganizationName(settings.APP_DIR_NAME)
    icon = settings.source_root() / "assets" / "icon" / "icon.png"
    if icon.exists():
        app.setWindowIcon(QIcon(str(icon)))
    app.setStyle("Fusion")  # draws with the token palette in every theme
    apply_theme(app)
    args = [a for a in argv[1:] if not a.startswith("-")]
    window = None
    if args:
        project = Project(Path(args[0]))
        settings.remember_project(project.path)
        window = MainWindow(project)
    else:
        window = _start_cloud()
    if window is None:
        project = Project(settings.startup_project_path())
        settings.remember_project(project.path)
        window = MainWindow(project)
    window.show()
    return app.exec()


def _start_cloud():
    """Every launch: ask which data location set to open (the last one is on top) and read
    the Drive files only after the user confirms. Returns None for a local project."""
    from app.ui import cloud as cloud_ui
    from app.ui.locations_ui import StartDialog

    error = ""
    while True:
        dlg = StartDialog(None, error=error)
        if not dlg.exec():
            raise SystemExit(0)
        if dlg.use_local or dlg.chosen is None:
            return None
        s = dlg.chosen
        opened = cloud_ui.open_folder_session(None, s.root_id, location_set=s)
        if opened is not None:
            session, project = opened
            return MainWindow(project, session)
        error = "열지 못했습니다. 위치와 로그인 상태를 확인해 주세요."


if __name__ == "__main__":
    sys.exit(main())
