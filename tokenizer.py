from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass

from sudachipy import Dictionary, SplitMode

from chunk_rules import merge_boundaries


PUNCT = set("。、！？!?，,．.：:；;「」『』（）()［］【】〈〉《》…・〜～—―\"'“”‘’")
MODE_MAP = {"A": SplitMode.A, "B": SplitMode.B, "C": SplitMode.C}
_tokenizer = None
_tokenizer_lock = threading.Lock()


@dataclass(frozen=True)
class Token:
    text: str
    start: int
    end: int
    pos: tuple[str, ...]
    dictionary_form: str
    normalized_form: str
    mode: str
    is_punctuation: bool = False


def chunk_id() -> str:
    return uuid.uuid4().hex[:12]


def _get_tokenizer():
    global _tokenizer
    if _tokenizer is None:
        with _tokenizer_lock:
            if _tokenizer is None:
                # Explicitly select the full edition; never silently fall back
                # to the core/small dictionaries.
                _tokenizer = Dictionary(dict="full").create()
    return _tokenizer


def _append_surface(result: list[Token], surface: str, start: int, pos: tuple[str, ...], dictionary_form: str, normalized_form: str, mode: str) -> None:
    if surface and all(char in PUNCT for char in surface):
        cursor = start
        for char in surface:
            result.append(Token(char, cursor, cursor + len(char), pos, char, char, mode, True))
            cursor += len(char)
        return
    # 全标点 surface 已在上方逐字拆出并 return；能走到这里则必然不是整段标点
    result.append(Token(surface, start, start + len(surface), pos, dictionary_form, normalized_form, mode, False))


def analyze(text: str, mode: str) -> list[Token]:
    if not isinstance(text, str):
        raise TypeError("日语原句必须是字符串")
    if mode not in MODE_MAP:
        raise ValueError("Sudachi SplitMode 必须是 A、B 或 C")
    if not text:
        return []

    result: list[Token] = []
    cursor = 0
    for morpheme in _get_tokenizer().tokenize(text, MODE_MAP[mode]):
        surface = morpheme.surface()
        start = text.find(surface, cursor)
        if start < 0:
            raise ValueError(f"Sudachi SplitMode.{mode} 无法定位原文片段")
        if start > cursor:
            gap = text[cursor:start]
            _append_surface(result, gap, cursor, ("空白", "*", "*", "*", "*", "*"), gap, gap, mode)
        _append_surface(
            result,
            surface,
            start,
            tuple(morpheme.part_of_speech()),
            morpheme.dictionary_form(),
            morpheme.normalized_form(),
            mode,
        )
        cursor = start + len(surface)
    if cursor < len(text):
        gap = text[cursor:]
        _append_surface(result, gap, cursor, ("空白", "*", "*", "*", "*", "*"), gap, gap, mode)
    if "".join(token.text for token in result) != text:
        raise ValueError(f"Sudachi SplitMode.{mode} 无法无损还原原句")
    return result


def analyze_modes(text: str) -> dict[str, list[Token]]:
    return {mode: analyze(text, mode) for mode in ("A", "B", "C")}


def local_tokenize(text: str) -> list[dict]:
    if not isinstance(text, str):
        raise TypeError("日语原句必须是字符串")
    if not text:
        return []
    modes = analyze_modes(text)
    base = modes["B"]
    joins = merge_boundaries(text, base, modes["A"], modes["C"])
    texts: list[str] = []
    current = ""
    for index, token in enumerate(base):
        current += token.text
        if index == len(base) - 1 or not joins[index]:
            texts.append(current)
            current = ""
    chunks = [{"id": chunk_id(), "text": value} for value in texts]
    if not chunks or "".join(item["text"] for item in chunks) != text:
        raise ValueError("Sudachi 多粒度分块无法无损还原原句")
    return chunks


def validate_chunks(sentence: str, chunks) -> tuple[bool, str]:
    if not isinstance(chunks, list) or not chunks:
        return False, "至少需要一个词块"
    ids = []
    texts = []
    for item in chunks:
        if not isinstance(item, dict):
            return False, "词块格式无效"
        identifier, text = item.get("id"), item.get("text")
        if not isinstance(identifier, str) or not identifier:
            return False, "每个词块都需要唯一 ID"
        if not isinstance(text, str) or not text:
            return False, "词块文字不能为空"
        ids.append(identifier)
        texts.append(text)
    if len(ids) != len(set(ids)):
        return False, "词块 ID 必须唯一"
    if "".join(texts) != sentence:
        return False, "词块按顺序拼接后必须与日语原句完全一致"
    return True, ""


def katakana_to_hiragana(text: str) -> str:
    """Convert katakana (U+30A1–U+30F6) to hiragana; leave other chars as-is (e.g. ー)."""
    if not text:
        return text
    chars = []
    for char in text:
        code = ord(char)
        if 0x30A1 <= code <= 0x30F6:
            chars.append(chr(code - 0x60))
        else:
            chars.append(char)
    return "".join(chars)


def _is_kanji(char: str) -> bool:
    return ("\u4e00" <= char <= "\u9fff") or char in "々〆〤"


def _has_kanji(text: str) -> bool:
    return any(_is_kanji(char) for char in text)


def _merge_plain_segments(segments: list[dict]) -> list[dict]:
    """Merge adjacent plain (no-ruby) text segments to shrink JSON / render work."""
    if not segments:
        return []
    merged: list[dict] = []
    for seg in segments:
        if (
            merged
            and "ruby" not in seg
            and "ruby" not in merged[-1]
        ):
            merged[-1] = {"text": merged[-1]["text"] + seg["text"]}
        else:
            merged.append(dict(seg))
    return merged


def _annotate_morpheme(surface: str, reading: str) -> list[dict]:
    """Split one morpheme into plain / ruby segments (kanji only)."""
    if not surface:
        return []
    reading = katakana_to_hiragana(reading or "")
    if not reading or reading == surface or not _has_kanji(surface):
        return [{"text": surface}]

    left: list[dict] = []
    right: list[dict] = []
    mid = surface
    rem = reading

    while mid and not _is_kanji(mid[0]):
        index = 0
        while index < len(mid) and not _is_kanji(mid[index]):
            index += 1
        prefix = mid[:index]
        if not rem.startswith(prefix):
            return [{"text": surface, "ruby": reading}]
        left.append({"text": prefix})
        mid = mid[index:]
        rem = rem[len(prefix) :]

    while mid and not _is_kanji(mid[-1]):
        index = len(mid)
        while index > 0 and not _is_kanji(mid[index - 1]):
            index -= 1
        suffix = mid[index:]
        if not rem.endswith(suffix):
            return [{"text": surface, "ruby": reading}]
        right.insert(0, {"text": suffix})
        mid = mid[:index]
        rem = rem[: -len(suffix)] if suffix else rem

    if not mid:
        return left + right
    # Single continuous kanji run in the middle → pair with remaining reading.
    if all(_is_kanji(char) for char in mid):
        middle = [{"text": mid, "ruby": rem}] if rem else [{"text": mid}]
        return left + middle + right
    # Kanji/kana still alternate inside the middle — fall back to whole morpheme.
    return [{"text": surface, "ruby": reading}]


def furigana_segments(text: str) -> list[dict]:
    """Return ruby segments for arbitrary Japanese text.

    Each segment is ``{"text": "..."}`` (no annotation) or
    ``{"text": "...", "ruby": "..."}`` (kanji with hiragana reading).
    Concatenating all ``text`` values must equal the input; on any failure
    the function degrades to ``[{"text": text}]`` so callers never break.
    """
    if not isinstance(text, str):
        raise TypeError("日语文本必须是字符串")
    if not text:
        return []

    try:
        result: list[dict] = []
        cursor = 0
        for morpheme in _get_tokenizer().tokenize(text, SplitMode.A):
            try:
                begin = morpheme.begin()
                end = morpheme.end()
            except Exception:
                surface = morpheme.surface()
                begin = text.find(surface, cursor)
                if begin < 0:
                    return [{"text": text}]
                end = begin + len(surface)
            if begin > cursor:
                result.append({"text": text[cursor:begin]})
            surface = text[begin:end]
            try:
                reading = morpheme.reading_form() or ""
            except Exception:
                reading = ""
            result.extend(_annotate_morpheme(surface, reading))
            cursor = end
        if cursor < len(text):
            result.append({"text": text[cursor:]})
        result = _merge_plain_segments(result)
        if "".join(seg["text"] for seg in result) != text:
            return [{"text": text}]
        return result
    except Exception:
        return [{"text": text}]
