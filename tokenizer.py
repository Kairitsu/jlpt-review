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
    result.append(Token(surface, start, start + len(surface), pos, dictionary_form, normalized_form, mode, bool(surface) and all(char in PUNCT for char in surface)))


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
