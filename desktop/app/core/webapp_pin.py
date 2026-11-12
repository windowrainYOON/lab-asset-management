"""The mobile web app's lab password (연구실 비밀번호).

Kept in the Drive folder's settings file under the shared setting ``webapp``:

    "webapp": {
      "pin": {"salt": <base64>, "hash": <base64 PBKDF2-SHA256>, "iterations": 2000, "version": 1},
      "pin_history": [{"at": "2026-12-01 10:14", "by": "이서연", "mac": "Lab Mac mini",
                       "version": 2, "keep_devices": false}]
    }

The plain password is never stored. The web app server (Apps Script) checks a password with the
same PBKDF2 and reads this key about once a minute. ``version`` goes up when "이미 들어온 휴대폰은
계속 열어 두기" is off; every phone then has to enter the new password. While no password was set
here the server uses the first password built into it (``first`` in the history = none yet).
"""
from __future__ import annotations

import base64
import copy
import hashlib
import hmac
import os
import socket
from datetime import datetime

KEY = "webapp"
ITERATIONS = 2000   # Apps Script computes PBKDF2 in script code; this keeps a check under ~1 s
MIN_LENGTH = 4
HISTORY_KEEP = 50


def hash_pin(pin: str, salt: bytes, iterations: int = ITERATIONS) -> str:
    return base64.b64encode(hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), salt, iterations, 32)).decode()


def make_record(pin: str, version: int) -> dict:
    salt = os.urandom(16)
    return {"salt": base64.b64encode(salt).decode(), "hash": hash_pin(pin, salt), "iterations": ITERATIONS,
            "version": int(version)}


def check(record: dict, pin: str) -> bool:
    try:
        salt = base64.b64decode(record["salt"])
        want = record["hash"]
        it = int(record.get("iterations") or ITERATIONS)
    except (KeyError, TypeError, ValueError):
        return False
    return hmac.compare_digest(hash_pin(pin, salt, it), want)


def problem(new: str, again: str) -> str:
    """Why the new password cannot be used ('' when fine)."""
    if len(new) < MIN_LENGTH:
        return f"비밀번호는 {MIN_LENGTH}자 이상이어야 합니다."
    if new != again:
        return "새 비밀번호 두 칸이 같지 않습니다."
    if new.strip() != new:
        return "비밀번호 앞뒤에 빈칸을 넣을 수 없습니다."
    return ""


def mac_name() -> str:
    try:
        from PySide6.QtCore import QSysInfo
        name = QSysInfo.machineHostName()
    except Exception:  # noqa: BLE001  no Qt (tests)
        name = ""
    name = (name or socket.gethostname() or "Mac").strip()
    return name.removesuffix(".local")


def change(setting: dict | None, new_pin: str, keep_devices: bool, by: str, mac: str | None = None,
           now: datetime | None = None) -> dict:
    """The new ``webapp`` setting after a password change (the input is not modified)."""
    out = copy.deepcopy(setting) if isinstance(setting, dict) else {}
    old = out.get("pin") if isinstance(out.get("pin"), dict) else None
    version = int((old or {}).get("version") or 1)
    if not keep_devices:   # the built-in first password is version 1 too
        version += 1
    out["pin"] = make_record(new_pin, version)
    hist = [h for h in out.get("pin_history") or [] if isinstance(h, dict)]
    hist.insert(0, {"at": (now or datetime.now()).strftime("%Y-%m-%d %H:%M"), "by": by or "", "mac": mac if mac is not None else mac_name(),
                    "version": version, "keep_devices": bool(keep_devices), "first": old is None})
    out["pin_history"] = hist[:HISTORY_KEEP]
    return out


def history(setting: dict | None) -> list[dict]:
    return [h for h in ((setting or {}).get("pin_history") or []) if isinstance(h, dict)]


def is_set(setting: dict | None) -> bool:
    p = (setting or {}).get("pin")
    return isinstance(p, dict) and bool(p.get("hash"))
