"""App settings and the project that opens on launch.

Launch order: the last opened project, else the sample project in the
development folder (data/sample.labproj), else the sample bundled in the
.app (copied to the user's data folder, since an app bundle is read-only),
else a new empty project.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from .project import Project, copy_project

APP_NAME = "Asset Manager"
APP_DIR_NAME = "AssetManager"


def source_root() -> Path:
    """Development folder when running from source, bundle resources when frozen."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parents[2]


STATE_ENV = "ASSET_MANAGER_HOME"
STATE_FILE = "state_dir.txt"  # written into the .app by scripts/build_macos.sh


def _state_override() -> Path | None:
    """The project's own state folder, when there is one.

    On the developer's Mac everything this app keeps (Google login token, settings,
    Drive cache, logs) lives in the project folder, next to the source:
    <project>/state. The build bakes that path into the .app (state_dir.txt); running
    from source uses ../state (or ../../state) when it exists; $ASSET_MANAGER_HOME wins over both.
    Without any of them (a lab member's build) the usual per-user folder is used."""
    import os
    env = os.environ.get(STATE_ENV)
    if env:
        return Path(env).expanduser()
    if getattr(sys, "frozen", False):
        f = source_root() / STATE_FILE
        try:
            text = f.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        return Path(text) if text else None
    # <project>/<repo>/desktop/app (git checkout) or <project>/<repo>/app (older copies)
    for base in (source_root().parent, source_root().parent.parent):
        local = base / "state"
        if local.is_dir():
            return local
    return None


def user_data_dir() -> Path:
    override = _state_override()
    if override is not None:
        try:
            override.mkdir(parents=True, exist_ok=True)
            return override
        except OSError:
            pass  # e.g. the project folder was moved: fall back to the per-user folder
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    elif sys.platform == "win32":
        base = Path.home() / "AppData" / "Roaming"
    else:
        base = Path.home() / ".local" / "share"
    path = base / APP_DIR_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def _settings_path() -> Path:
    return user_data_dir() / "settings.json"


def load_settings() -> dict:
    try:
        return json.loads(_settings_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_settings(data: dict) -> None:
    try:
        _settings_path().write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


def remember_project(path: Path) -> None:
    data = load_settings()
    data["last_project"] = str(path)
    recent = [p for p in data.get("recent_projects", []) if p != str(path)]
    data["recent_projects"] = [str(path)] + recent[:9]
    save_settings(data)


def remember_cloud(folder_id: str, name: str) -> None:
    """Make a Google Drive folder the startup project and a favorite."""
    data = load_settings()
    data["last_project"] = f"gdrive:{folder_id}"
    favs = [f for f in data.get("cloud_favorites", []) if f.get("folder_id") != folder_id]
    data["cloud_favorites"] = [{"folder_id": folder_id, "name": name}] + favs
    save_settings(data)


def cloud_favorites() -> list[dict]:
    return load_settings().get("cloud_favorites", [])


def remove_cloud_favorite(folder_id: str) -> None:
    data = load_settings()
    data["cloud_favorites"] = [f for f in data.get("cloud_favorites", []) if f.get("folder_id") != folder_id]
    if data.get("last_project") == f"gdrive:{folder_id}":
        data.pop("last_project")
    save_settings(data)


def last_cloud_folder() -> str | None:
    last = load_settings().get("last_project", "")
    return last[len("gdrive:"):] if last.startswith("gdrive:") else None


def is_first_run() -> bool:
    return "last_project" not in load_settings()


def recent_projects() -> list[Path]:
    return [Path(p) for p in load_settings().get("recent_projects", []) if Path(p).exists()]


def sample_project_path() -> Path:
    return source_root() / "data" / "sample.labproj"


def startup_project_path() -> Path:
    """Local project to open when no cloud project is used."""
    last = load_settings().get("last_project")
    if last and not last.startswith("gdrive:") and Path(last).exists():
        return Path(last)
    sample = sample_project_path()
    if sample.exists():
        if getattr(sys, "frozen", False):
            dest = user_data_dir() / "Sample.labproj"
            if not dest.exists():
                copy_project(sample, dest)
            return dest
        return sample
    default = user_data_dir() / "My Assets.labproj"
    if not default.exists():
        Project.create(default, "My Assets").close()
    return default
