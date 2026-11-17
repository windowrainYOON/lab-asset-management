"""Offline text reading (OCR) for 일정표 이미지 가져오기.

Runs entirely on this computer, with no account, key or network: EasyOCR's free Korean +
English models (PyTorch, CPU). Works the same on macOS, Windows and Linux. The models are
bundled into the built app (``ocr_models/`` next to the code); when running from source
they are downloaded once into the app's data folder.

The engine is optional: ``available()`` says whether it can run, and the rest of the app
(the calendar, everything else) works without it.
"""
from __future__ import annotations

import threading
from pathlib import Path

from . import settings as app_settings
from .schedule_image import Box

LANGS = ["ko", "en"]
MAX_SIDE = 3200      # upscale small screenshots (a phone photo of a schedule is ~1000 px)
SCALE = 3

_reader = None
_lock = threading.Lock()


def model_dir() -> Path:
    bundled = app_settings.source_root() / "ocr_models"
    if bundled.is_dir() and any(bundled.glob("*.pth")):
        return bundled
    path = app_settings.user_data_dir() / "ocr_models"
    path.mkdir(parents=True, exist_ok=True)
    return path


last_error = ""


def available() -> bool:
    global last_error
    try:
        import easyocr  # noqa: F401
    except Exception as exc:  # noqa: BLE001  (missing, or torch failing to load)
        import traceback
        last_error = f"{type(exc).__name__}: {exc}"
        traceback.print_exc()
        return False
    return True


def self_check(path: str = "") -> int:
    """`Asset Manager --ocr-check [picture]`: import the engine (and read a picture) from the
    built app, printing what fails. For checking a build; no window."""
    import time
    if not available():
        print("OCR import failed:", last_error)
        return 1
    print("OCR import ok; models in", model_dir())
    if path:
        t = time.time()
        boxes = read(load_image(path))
        print(f"read {len(boxes)} boxes in {time.time() - t:.1f}s:", " | ".join(b.text for b in boxes[:20]))
    return 0


def _get_reader():
    global _reader
    with _lock:
        if _reader is None:
            import easyocr
            d = model_dir()
            _reader = easyocr.Reader(LANGS, gpu=False, verbose=False, model_storage_directory=str(d),
                                     download_enabled=True)  # from source: fetched once
        return _reader


def read(image) -> list[Box]:
    """Text boxes of a PIL image, in the image's own pixel coordinates."""
    import numpy as np
    from PIL import Image

    img = image.convert("RGB")
    scale = max(1.0, min(SCALE, MAX_SIDE / max(img.size)))
    big = img.resize((round(img.width * scale), round(img.height * scale)), Image.LANCZOS) if scale > 1 else img
    reader = _get_reader()
    res = reader.readtext(np.asarray(big), paragraph=False, text_threshold=0.5, low_text=0.3)
    out = []
    for pts, text, score in res:
        xs = [p[0] / scale for p in pts]
        ys = [p[1] / scale for p in pts]
        out.append(Box(min(xs), min(ys), max(xs), max(ys), str(text), float(score)))
    return out


def load_image(path_or_bytes):
    """A PIL image from a file path or raw bytes (PNG, JPG, …; HEIC when pillow-heif is there)."""
    import io
    from PIL import Image, ImageOps
    try:
        import pillow_heif  # noqa: F401
        pillow_heif.register_heif_opener()
    except Exception:  # noqa: BLE001
        pass
    src = io.BytesIO(path_or_bytes) if isinstance(path_or_bytes, (bytes, bytearray)) else path_or_bytes
    img = Image.open(src)
    img = ImageOps.exif_transpose(img)  # phone photos carry their rotation in EXIF
    if img.mode in ("RGBA", "LA", "P"):
        bg = Image.new("RGB", img.size, "white")
        rgba = img.convert("RGBA")
        bg.paste(rgba, mask=rgba.split()[-1])
        img = bg
    return img.convert("RGB")
