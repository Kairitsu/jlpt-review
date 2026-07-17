"""Content-driven active CJK font subsets (UI strings + saved sentences).

Builds small self-hosted WOFF2 files covering only characters that appear in
the app chrome and the SQLite corpus. Rebuild is scheduled after sentence
create/update/delete so newly saved text is included without shipping full
CJK fonts to the browser.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import threading
import time
from pathlib import Path

from db import DATA_DIR, get_db

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / "static"
# Source OTFs (full language fonts). Prefer env, then repo font-sources/, then cache.
FONT_SOURCES = Path(os.environ.get("FONT_SOURCES_DIR", ROOT / "font-sources"))
ACTIVE_DIR = DATA_DIR / "fonts" / "active"
MANIFEST_PATH = ACTIVE_DIR / "manifest.json"
FACES_CSS_PATH = ACTIVE_DIR / "faces.css"
BUILD_LOCK_PATH = ACTIVE_DIR / ".build.lock"

SOURCE_FILES = {
    ("sc", 400): "NotoSansSC-Regular.otf",
    ("sc", 700): "NotoSansSC-Bold.otf",
    ("jp", 400): "NotoSansJP-Regular.otf",
    ("jp", 700): "NotoSansJP-Bold.otf",
}
FAMILY = {"sc": "Noto Sans SC", "jp": "Noto Sans JP"}
WEIGHT_CSS = {400: "100 500", 700: "600 900"}

# Extra UI / system strings that may not appear in static files as CJK.
EXTRA_UI_TEXT = (
    "句子重组待复习今日学习开始句子重组开始复习选择本轮复习"
    "句集详情题库报告设置欢迎回来登录用户名密码添加句子编辑句子自动分块"
    "保存正确答案回答正确跳过练习重置核对答案下一题重新练习本题"
    "练习历史本轮练习报告再练一轮忘记模糊认识轻松掌握未计入评分访问认证使用说明"
    # Stats page (also scanned from stats.js; kept as fallback for deploy races).
    "认识模糊忘记本场第一次就拼对曾拼错过最终仍未拼对"
    "认知情况复习新学记忆持久度今日汇总今日认识今日模糊今日忘记今日待学今日时长"
    "评分复习预测稳定度难度保持率今日学习未来天句子数"
)

_state_lock = threading.Lock()
_building = False
_pending = False
_timer: threading.Timer | None = None
_last_error: str | None = None


def active_dir() -> Path:
    ACTIVE_DIR.mkdir(parents=True, exist_ok=True)
    return ACTIVE_DIR


def sources_available() -> bool:
    return all((FONT_SOURCES / name).is_file() for name in SOURCE_FILES.values())


def ui_charset() -> set[str]:
    chars: set[str] = set(EXTRA_UI_TEXT)
    for name in ("index.html", "app.js", "stats.js"):
        path = STATIC_DIR / name
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        chars.update(c for c in text if ord(c) > 127)
    return chars


def db_charset(db=None) -> set[str]:
    chars: set[str] = set()
    close = False
    if db is None:
        db = get_db()
        close = True
    try:
        for row in db.execute(
            "SELECT chinese, japanese, furigana_json FROM sentences"
        ):
            for key in ("chinese", "japanese", "furigana_json"):
                val = row[key]
                if val:
                    chars.update(val)
        for row in db.execute("SELECT name FROM collections"):
            if row["name"]:
                chars.update(row["name"])
    finally:
        if close:
            db.close()
    return chars


def collect_charset(db=None) -> set[str]:
    return ui_charset() | db_charset(db)


def charset_hash(chars: set[str]) -> str:
    payload = "".join(sorted(chars)).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


def read_manifest() -> dict | None:
    if not MANIFEST_PATH.is_file():
        return None
    try:
        return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def faces_css_text() -> str | None:
    if FACES_CSS_PATH.is_file():
        try:
            return FACES_CSS_PATH.read_text(encoding="utf-8")
        except OSError:
            return None
    return None


def status() -> dict:
    man = read_manifest() or {}
    with _state_lock:
        building = _building
        err = _last_error
    return {
        "building": building,
        "ready": bool(man.get("hash") and FACES_CSS_PATH.is_file()),
        "hash": man.get("hash"),
        "charCount": man.get("charCount"),
        "files": man.get("files"),
        "builtAt": man.get("builtAt"),
        "error": err,
        "sourcesAvailable": sources_available(),
    }


def _subset_file(src: Path, dest: Path, codepoints: list[int]) -> int:
    from fontTools import subset

    options = subset.Options()
    options.flavor = "woff2"
    options.with_zopfli = False
    options.desubroutinize = True
    options.hinting = False
    options.layout_features = ["*"]
    options.name_IDs = ["*"]
    options.name_legacy = True
    options.name_languages = ["*"]
    options.notdef_outline = True
    options.recalc_bounds = True
    options.recalc_timestamp = False
    options.canonical_order = True

    font = subset.load_font(str(src), options)
    subsetter = subset.Subsetter(options=options)
    subsetter.populate(unicodes=codepoints)
    subsetter.subset(font)
    subset.save_font(font, str(dest), options)
    font.close()
    return dest.stat().st_size


def _write_faces_css(files: dict[str, str]) -> str:
    """files keys like sc-400 -> filename."""
    lines = [
        "/* Auto-generated content-subset fonts. Do not edit. */",
        "",
    ]
    for lang in ("sc", "jp"):
        for weight in (400, 700):
            key = f"{lang}-{weight}"
            name = files[key]
            lines.append("@font-face {")
            lines.append(f'  font-family: "{FAMILY[lang]}";')
            lines.append("  font-style: normal;")
            lines.append(f"  font-weight: {WEIGHT_CSS[weight]};")
            lines.append("  font-display: swap;")
            lines.append(f'  src: url("/api/fonts/files/{name}") format("woff2");')
            lines.append("}")
            lines.append("")
    return "\n".join(lines)


def build_active_fonts(chars: set[str] | None = None) -> dict:
    """Synchronously rebuild active fonts. Returns manifest."""
    global _last_error
    if not sources_available():
        raise FileNotFoundError(
            f"Font sources missing under {FONT_SOURCES}. "
            f"Need: {', '.join(SOURCE_FILES.values())}"
        )

    active_dir()
    chars = chars if chars is not None else collect_charset()
    # Always keep a minimal ASCII/punctuation set for safety in UI chrome.
    for c in "0123456789%./·—…'（）【】、。，《》？！：；-_":
        chars.add(c)
    # Drop control chars
    chars = {c for c in chars if c.isprintable() or c in "\n\t"}
    chash = charset_hash(chars)
    codepoints = sorted({ord(c) for c in chars})

    work = ACTIVE_DIR / f".tmp-{chash}-{os.getpid()}"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)

    files: dict[str, str] = {}
    sizes: dict[str, int] = {}
    try:
        for (lang, weight), src_name in SOURCE_FILES.items():
            src = FONT_SOURCES / src_name
            out_name = f"{lang}-{weight}-{chash}.woff2"
            out_path = work / out_name
            size = _subset_file(src, out_path, codepoints)
            key = f"{lang}-{weight}"
            files[key] = out_name
            sizes[key] = size
            log.info("font subset %s → %s (%d bytes)", key, out_name, size)

        css = _write_faces_css(files)
        (work / "faces.css").write_text(css, encoding="utf-8")
        manifest = {
            "hash": chash,
            "charCount": len(codepoints),
            "files": files,
            "sizes": sizes,
            "builtAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        (work / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        # Promote files into active dir; keep previous hash files briefly.
        for name in files.values():
            shutil.move(str(work / name), str(ACTIVE_DIR / name))
        FACES_CSS_PATH.write_text(css, encoding="utf-8")
        MANIFEST_PATH.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        # Cleanup old woff2 not referenced by current manifest.
        keep = set(files.values())
        for path in ACTIVE_DIR.glob("*.woff2"):
            if path.name not in keep:
                try:
                    path.unlink()
                except OSError:
                    pass
        _last_error = None
        return manifest
    finally:
        if work.exists():
            shutil.rmtree(work, ignore_errors=True)


def ensure_active_fonts(force: bool = False) -> dict | None:
    """Build if missing or charset hash mismatch. Safe to call at startup."""
    if not sources_available():
        log.warning("Skipping active font build: sources not found at %s", FONT_SOURCES)
        return read_manifest()

    chars = collect_charset()
    chash = charset_hash(chars)
    man = read_manifest()
    if (
        not force
        and man
        and man.get("hash") == chash
        and FACES_CSS_PATH.is_file()
        and all((ACTIVE_DIR / name).is_file() for name in (man.get("files") or {}).values())
    ):
        return man

    try:
        return build_active_fonts(chars)
    except Exception as exc:
        log.exception("Active font build failed: %s", exc)
        global _last_error
        _last_error = str(exc)
        return man


def _run_rebuild():
    global _building, _pending, _last_error
    with _state_lock:
        _building = True
        _pending = False
    try:
        # File lock so gunicorn workers don't thrash each other.
        active_dir()
        lock_fd = None
        try:
            lock_fd = open(BUILD_LOCK_PATH, "a+", encoding="utf-8")
            try:
                import fcntl

                fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX)
            except Exception:
                pass
            ensure_active_fonts(force=True)
        finally:
            if lock_fd is not None:
                try:
                    import fcntl

                    fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)
                except Exception:
                    pass
                lock_fd.close()
    except Exception as exc:
        log.exception("scheduled font rebuild failed: %s", exc)
        _last_error = str(exc)
    finally:
        with _state_lock:
            _building = False
            again = _pending
            _pending = False
        if again:
            schedule_font_rebuild(delay=0.5)


def schedule_font_rebuild(delay: float = 1.5) -> None:
    """Debounced background rebuild after corpus changes."""
    global _timer, _pending
    if not sources_available():
        return
    with _state_lock:
        if _building:
            _pending = True
            return
        if _timer is not None:
            _timer.cancel()
        _timer = threading.Timer(delay, _run_rebuild)
        _timer.daemon = True
        _timer.start()


def safe_font_filename(name: str) -> str | None:
    """Allow only active font basenames: lang-weight-hash.woff2"""
    if not name or "/" in name or "\\" in name or ".." in name:
        return None
    if not re.fullmatch(r"(sc|jp)-(400|700)-[0-9a-f]{16}\.woff2", name):
        return None
    path = ACTIVE_DIR / name
    if not path.is_file():
        return None
    return name
