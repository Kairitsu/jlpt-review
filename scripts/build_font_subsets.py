#!/usr/bin/env python3
"""Build unicode-range WOFF2 subsets for self-hosted Noto Sans SC / JP.

Splits full language fonts into small chunks so browsers only download the
ranges needed for the current page, while keeping complete glyph coverage
across all chunks (union == full font).

Usage (from repo root):
  python3 scripts/build_font_subsets.py
  python3 scripts/build_font_subsets.py --keep-source
"""

from __future__ import annotations

import argparse
import shutil
import sys
import zipfile
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from urllib.request import urlretrieve

from fontTools import subset
from fontTools.ttLib import TTFont

ROOT = Path(__file__).resolve().parents[1]
FONTS_DIR = ROOT / "static" / "fonts"
CSS_PATH = ROOT / "static" / "fonts.css"
CACHE_DIR = ROOT / ".font-build-cache"

RELEASE = "https://github.com/notofonts/noto-cjk/releases/download/Sans2.004"
SOURCES = {
    "sc": {
        "zip_url": f"{RELEASE}/18_NotoSansSC.zip",
        "zip_name": "18_NotoSansSC.zip",
        "family": "Noto Sans SC",
        "files": {
            400: "NotoSansSC-Regular.otf",
            700: "NotoSansSC-Bold.otf",
        },
    },
    "jp": {
        "zip_url": f"{RELEASE}/16_NotoSansJP.zip",
        "zip_name": "16_NotoSansJP.zip",
        "family": "Noto Sans JP",
        "files": {
            400: "NotoSansJP-Regular.otf",
            700: "NotoSansJP-Bold.otf",
        },
    },
}

# Bucket size for CJK and other large planes (256 code points).
BUCKET = 0x100

# Preferred named ranges first (still only emitted if font has glyphs there).
NAMED_RANGES: list[tuple[str, list[tuple[int, int]]]] = [
    ("latin", [(0x0000, 0x024F), (0x1E00, 0x1EFF)]),
    ("punct", [(0x2000, 0x206F), (0x2E00, 0x2E7F), (0x3000, 0x303F), (0xFE30, 0xFE4F), (0xFF00, 0xFFEF)]),
    ("kana", [(0x3040, 0x30FF), (0x31F0, 0x31FF), (0xFF65, 0xFF9F)]),
    ("cjk-rad", [(0x2E80, 0x2FDF), (0x2FF0, 0x2FFF)]),
    ("cjk-sym", [(0x3190, 0x319F), (0x31C0, 0x31EF), (0x3200, 0x32FF), (0x3300, 0x33FF), (0xFE10, 0xFE1F)]),
]


def download_and_extract(lang: str, meta: dict) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    zip_path = CACHE_DIR / meta["zip_name"]
    out_dir = CACHE_DIR / lang
    needed = [out_dir / name for name in meta["files"].values()]
    if all(p.is_file() for p in needed):
        print(f"[skip] sources ready for {lang}")
        return out_dir
    if not zip_path.is_file():
        print(f"[dl] {meta['zip_url']}")
        urlretrieve(meta["zip_url"], zip_path)
    out_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        for name in meta["files"].values():
            # Zip may nest files; match by basename.
            members = [m for m in zf.namelist() if m.rstrip("/").endswith(name)]
            if not members:
                raise FileNotFoundError(f"{name} not in {zip_path}")
            member = members[0]
            data = zf.read(member)
            (out_dir / name).write_bytes(data)
            print(f"[extract] {lang}/{name} ({len(data)/1024/1024:.1f} MB)")
    return out_dir


def font_codepoints(otf_path: Path) -> set[int]:
    font = TTFont(otf_path, recalcBBoxes=False, recalcTimestamp=False)
    try:
        cmap = font.getBestCmap() or {}
        return set(cmap.keys())
    finally:
        font.close()


def plan_buckets(codepoints: set[int]) -> list[tuple[str, list[int]]]:
    """Return list of (slug, sorted codepoints) covering all glyphs."""
    remaining = set(codepoints)
    buckets: list[tuple[str, list[int]]] = []

    for name, ranges in NAMED_RANGES:
        selected: list[int] = []
        for start, end in ranges:
            for cp in range(start, end + 1):
                if cp in remaining:
                    selected.append(cp)
                    remaining.discard(cp)
        if selected:
            buckets.append((name, sorted(selected)))

    # Residual codepoints in aligned 256-codepoint buckets.
    by_bucket: dict[int, list[int]] = {}
    for cp in remaining:
        key = cp // BUCKET
        by_bucket.setdefault(key, []).append(cp)
    for key in sorted(by_bucket):
        cps = sorted(by_bucket[key])
        start = key * BUCKET
        end = start + BUCKET - 1
        slug = f"u{start:04x}-{end:04x}"
        buckets.append((slug, cps))
    return buckets


def css_unicode_range(codepoints: list[int]) -> str:
    """Compact list of codepoints into CSS unicode-range value."""
    if not codepoints:
        return ""
    ranges: list[str] = []
    start = prev = codepoints[0]
    for cp in codepoints[1:]:
        if cp == prev + 1:
            prev = cp
            continue
        ranges.append(_fmt_range(start, prev))
        start = prev = cp
    ranges.append(_fmt_range(start, prev))
    return ", ".join(ranges)


def _fmt_range(start: int, end: int) -> str:
    if start == end:
        return f"U+{start:04X}"
    return f"U+{start:04X}-{end:04X}"


def weight_css(weight: int) -> str:
    if weight <= 400:
        return "100 500"
    return "600 900"


def subset_one(args: tuple) -> tuple[str, str, int, str, list[int], int]:
    """Worker: create one woff2 subset.

    Returns (lang, family, weight, rel_url, codepoints, size).
    """
    otf_path, out_path, codepoints, family, weight, lang, slug = args
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

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
    options.drop_tables = ["+DSIG"]

    font = subset.load_font(str(otf_path), options)
    subsetter = subset.Subsetter(options=options)
    subsetter.populate(unicodes=codepoints)
    subsetter.subset(font)
    subset.save_font(font, str(out_path), options)
    font.close()

    rel = f"/static/fonts/{lang}/{out_path.name}"
    return lang, family, weight, rel, codepoints, out_path.stat().st_size


def build_css(entries: list[tuple[str, str, int, str, list[int]]]) -> str:
    """entries: family, weight, rel_url, codepoints — also lang for grouping comment."""
    lines = [
        "/* Auto-generated by scripts/build_font_subsets.py — do not edit by hand. */",
        "/* Self-hosted Noto Sans SC / JP unicode-range chunks (SIL OFL). See static/fonts/OFL.txt. */",
        "",
    ]
    # Sort for stable output: family, weight, url
    for family, weight, rel_url, codepoints in sorted(
        [(e[1], e[2], e[3], e[4]) for e in entries],
        key=lambda x: (x[0], x[1], x[2]),
    ):
        ur = css_unicode_range(codepoints)
        lines.append("@font-face {")
        lines.append(f'  font-family: "{family}";')
        lines.append("  font-style: normal;")
        lines.append(f"  font-weight: {weight_css(weight)};")
        lines.append("  font-display: swap;")
        lines.append(f'  src: url("{rel_url}") format("woff2");')
        lines.append(f"  unicode-range: {ur};")
        lines.append("}")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jobs", type=int, default=4, help="parallel subset workers")
    parser.add_argument("--keep-source", action="store_true", help="keep .font-build-cache")
    parser.add_argument("--langs", default="sc,jp", help="comma list: sc,jp")
    args = parser.parse_args()

    langs = [x.strip() for x in args.langs.split(",") if x.strip()]
    work: list[tuple] = []

    # Clean previous generated chunks (keep OFL.txt).
    for sub in ("sc", "jp"):
        d = FONTS_DIR / sub
        if d.exists():
            shutil.rmtree(d)
    for old in FONTS_DIR.glob("NotoSans*.woff2"):
        old.unlink()
        print(f"[rm] {old.name}")

    FONTS_DIR.mkdir(parents=True, exist_ok=True)

    for lang in langs:
        if lang not in SOURCES:
            print(f"unknown lang {lang}", file=sys.stderr)
            return 1
        meta = SOURCES[lang]
        src_dir = download_and_extract(lang, meta)
        out_dir = FONTS_DIR / lang
        out_dir.mkdir(parents=True, exist_ok=True)

        for weight, filename in meta["files"].items():
            otf = src_dir / filename
            cps = font_codepoints(otf)
            buckets = plan_buckets(cps)
            print(f"[plan] {lang} weight={weight}: {len(cps)} glyphs → {len(buckets)} chunks")
            for slug, bucket_cps in buckets:
                out_name = f"{weight}-{slug}.woff2"
                out_path = out_dir / out_name
                work.append(
                    (
                        str(otf),
                        str(out_path),
                        bucket_cps,
                        meta["family"],
                        weight,
                        lang,
                        slug,
                    )
                )

    print(f"[build] {len(work)} subset jobs, jobs={args.jobs}")
    results: list[tuple[str, str, int, str, list[int]]] = []

    done = 0
    with ProcessPoolExecutor(max_workers=max(1, args.jobs)) as ex:
        futures = {ex.submit(subset_one, w): w for w in work}
        for fut in as_completed(futures):
            lang, family, weight, rel, cps, size = fut.result()
            results.append((lang, family, weight, rel, cps))
            done += 1
            if done % 20 == 0 or done == len(work):
                print(f"[build] {done}/{len(work)} ({size/1024:.0f} KB last)")

    CSS_PATH.write_text(build_css(results), encoding="utf-8")
    total = sum(p.stat().st_size for p in FONTS_DIR.rglob("*.woff2"))
    print(f"[done] wrote {CSS_PATH} ({CSS_PATH.stat().st_size} bytes)")
    print(f"[done] {len(results)} woff2 files, total {total/1024/1024:.1f} MB under {FONTS_DIR}")

    # Ensure OFL license present
    ofl = FONTS_DIR / "OFL.txt"
    if not ofl.is_file():
        for lang in langs:
            meta = SOURCES[lang]
            zip_path = CACHE_DIR / meta["zip_name"]
            if zip_path.is_file():
                with zipfile.ZipFile(zip_path) as zf:
                    for m in zf.namelist():
                        if m.endswith("LICENSE") or m.endswith("OFL.txt"):
                            ofl.write_bytes(zf.read(m))
                            print(f"[license] {ofl}")
                            break
                if ofl.is_file():
                    break

    if not args.keep_source and CACHE_DIR.exists():
        # Keep cache by default for rebuild speed; only remove when explicitly not keeping
        # and user wants free disk: use --no-keep via deleting cache externally.
        pass

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
