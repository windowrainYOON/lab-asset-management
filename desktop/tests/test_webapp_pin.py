import base64
import hashlib
from datetime import datetime

from app.core import webapp_pin as wp


def test_hash_is_pbkdf2_sha256():
    rec = wp.make_record("lab-pass", 1)
    salt = base64.b64decode(rec["salt"])
    want = base64.b64encode(hashlib.pbkdf2_hmac("sha256", b"lab-pass", salt, 2000, 32)).decode()
    assert rec["hash"] == want and rec["iterations"] == 2000 and rec["version"] == 1
    assert "lab-pass" not in str(rec)
    assert wp.check(rec, "lab-pass") and not wp.check(rec, "lab-pas")


def test_first_change_keeps_phones_by_default():
    v = wp.change({}, "abcd", True, "이서연", mac="Lab Mac mini", now=datetime(2026, 12, 1, 10, 14))
    assert v["pin"]["version"] == 1
    assert v["pin_history"] == [{"at": "2026-12-01 10:14", "by": "이서연", "mac": "Lab Mac mini", "version": 1,
                                 "keep_devices": True, "first": True}]


def test_change_without_keeping_phones_bumps_version_and_keeps_history():
    a = wp.change({}, "abcd", True, "A", mac="m")
    b = wp.change(a, "efgh", False, "B", mac="m")
    c = wp.change(b, "ijkl", True, "C", mac="m")
    assert [x["pin"]["version"] for x in (a, b, c)] == [1, 2, 2]
    assert [h["by"] for h in wp.history(c)] == ["C", "B", "A"]
    assert wp.check(c["pin"], "ijkl") and not wp.check(c["pin"], "efgh")
    assert a["pin"]["version"] == 1 and len(a["pin_history"]) == 1   # input not modified


def test_unknown_keys_survive():
    v = wp.change({"later": {"x": 1}}, "abcd", True, "A", mac="m")
    assert v["later"] == {"x": 1}


def test_problem():
    assert wp.problem("abc", "abc")
    assert wp.problem("abcd", "abce")
    assert wp.problem(" abcd", " abcd")
    assert wp.problem("abcd", "abcd") == ""


def test_webapp_is_a_shared_setting():
    from app.core.folder_model import SHARED_SETTINGS
    assert "webapp" in SHARED_SETTINGS
