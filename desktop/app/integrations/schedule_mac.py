"""Install the briefing as a macOS LaunchAgent so it runs even when the app is closed."""
from __future__ import annotations

import os
import plistlib
import subprocess
import sys
from pathlib import Path

LABEL = "kr.lab.assetmanager.briefing"


def plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def supported() -> bool:
    return sys.platform == "darwin"


def program_arguments() -> list[str]:
    if getattr(sys, "frozen", False):  # inside Asset Manager.app
        return [sys.executable, "--briefing"]
    return [sys.executable, "-m", "app.main", "--briefing"]


def plist(time_hhmm: str, weekdays: list[int]) -> dict:
    hour, minute = (int(x) for x in time_hhmm.split(":"))
    # launchd: 0 = Sunday; the app: 0 = Monday
    intervals = [{"Hour": hour, "Minute": minute, "Weekday": (d + 1) % 7} for d in sorted(weekdays)]
    data = {
        "Label": LABEL,
        "ProgramArguments": program_arguments(),
        "StartCalendarInterval": intervals,
        "RunAtLoad": False,
        "StandardOutPath": "/tmp/asset-manager-briefing.out",
        "StandardErrorPath": "/tmp/asset-manager-briefing.err",
    }
    if not getattr(sys, "frozen", False):
        data["WorkingDirectory"] = str(Path(__file__).resolve().parents[2])
    return data


def _launchctl(*args: str) -> None:
    subprocess.run(["launchctl", *args], check=False, capture_output=True)


def install(time_hhmm: str, weekdays: list[int]) -> Path:
    path = plist_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    domain = f"gui/{os.getuid()}"
    _launchctl("bootout", f"{domain}/{LABEL}")
    with path.open("wb") as fh:
        plistlib.dump(plist(time_hhmm, weekdays), fh)
    _launchctl("bootstrap", domain, str(path))
    return path


def uninstall() -> None:
    _launchctl("bootout", f"gui/{os.getuid()}/{LABEL}")
    if plist_path().exists():
        plist_path().unlink()


def installed() -> bool:
    return plist_path().exists()


# ---- event alerts: every 5 minutes ----------------------------------------------
REMINDER_LABEL = "kr.lab.assetmanager.reminders"


def reminder_plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{REMINDER_LABEL}.plist"


def reminder_plist() -> dict:
    args = program_arguments()[:-1] + ["--reminders"]
    data = {"Label": REMINDER_LABEL, "ProgramArguments": args, "StartInterval": 300, "RunAtLoad": True,
            "StandardOutPath": "/tmp/asset-manager-reminders.out",
            "StandardErrorPath": "/tmp/asset-manager-reminders.err"}
    if not getattr(sys, "frozen", False):
        data["WorkingDirectory"] = str(Path(__file__).resolve().parents[2])
    return data


def install_reminders() -> Path:
    path = reminder_plist_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    domain = f"gui/{os.getuid()}"
    _launchctl("bootout", f"{domain}/{REMINDER_LABEL}")
    with path.open("wb") as fh:
        plistlib.dump(reminder_plist(), fh)
    _launchctl("bootstrap", domain, str(path))
    return path


def uninstall_reminders() -> None:
    _launchctl("bootout", f"gui/{os.getuid()}/{REMINDER_LABEL}")
    if reminder_plist_path().exists():
        reminder_plist_path().unlink()


def reminders_installed() -> bool:
    return reminder_plist_path().exists()
