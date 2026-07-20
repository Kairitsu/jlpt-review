from __future__ import annotations

import hashlib
import logging
import threading
import unicodedata


LOGGER = logging.getLogger(__name__)
CHUNK_SCHEMA_VERSION = 2
TOKENIZER_NAME = "ginza-ja_ginza-bunsetu"
FIXED_EXTRA = set("〜～")

_nlp = None
_ginza = None
_nlp_unavailable = False
_nlp_lock = threading.RLock()


def _get_nlp():
    """Load one standard ja_ginza model per process and reuse it.

    NER is not needed for bunsetu boundaries or readings, so keeping that
    component disabled saves memory on this small deployment.
    """
    global _nlp, _ginza, _nlp_unavailable
    if _nlp_unavailable:
        raise RuntimeError("GiNZA model is unavailable in this process")
    if _nlp is None:
        with _nlp_lock:
            if _nlp is None:
                try:
                    import ginza
                    import spacy

                    _nlp = spacy.load("ja_ginza", disable=["ner"])
                    _ginza = ginza
                except Exception:
                    _nlp_unavailable = True
                    raise
    return _nlp


def _document(text: str):
    # spaCy pipeline components are shared by all Gunicorn threads. Serialize
    # inference so component state is never mutated concurrently.
    with _nlp_lock:
        return _get_nlp()(text)


def is_fixed_char(char: str) -> bool:
    """Return whether one source character is fixed, never user-sortable."""
    return bool(char) and (
        char.isspace()
        or char in FIXED_EXTRA
        or unicodedata.category(char).startswith("P")
    )


def stable_chunk_id(sentence: str, start: int, end: int, text: str, *, prefix: str = "g") -> str:
    """Create a deterministic, position-aware id for one sentence chunk."""
    material = f"{sentence}\0{start}\0{end}\0{text}".encode("utf-8")
    return prefix + hashlib.blake2s(material, digest_size=8).hexdigest()


def _append_fixed(structure: list[dict], sentence: str, start: int, end: int) -> None:
    if start >= end:
        return
    text = sentence[start:end]
    if structure and structure[-1]["type"] == "fixed" and structure[-1]["end"] == start:
        structure[-1]["end"] = end
        structure[-1]["text"] += text
    else:
        structure.append({"type": "fixed", "text": text, "start": start, "end": end})


def _append_slot(
    chunks: list[dict],
    structure: list[dict],
    sentence: str,
    start: int,
    end: int,
    *,
    identifier: str | None = None,
    prefix: str = "g",
) -> None:
    if start >= end:
        return
    text = sentence[start:end]
    identifier = identifier or stable_chunk_id(sentence, start, end, text, prefix=prefix)
    chunk = {"id": identifier, "text": text, "start": start, "end": end}
    chunks.append(chunk)
    structure.append({"type": "slot", "chunkId": identifier, "start": start, "end": end})


def _append_range(
    chunks: list[dict],
    structure: list[dict],
    sentence: str,
    start: int,
    end: int,
    *,
    preferred_id: str | None = None,
    prefix: str = "g",
) -> None:
    """Split one bunsetu/manual range into fixed and sortable character runs."""
    cursor = start
    run_start = start
    run_fixed = is_fixed_char(sentence[start]) if start < end else False
    runs: list[tuple[int, int, bool]] = []
    while cursor < end:
        fixed = is_fixed_char(sentence[cursor])
        if fixed != run_fixed:
            runs.append((run_start, cursor, run_fixed))
            run_start = cursor
            run_fixed = fixed
        cursor += 1
    if run_start < end:
        runs.append((run_start, end, run_fixed))

    slot_runs = [run for run in runs if not run[2]]
    for run_start, run_end, fixed in runs:
        if fixed:
            _append_fixed(structure, sentence, run_start, run_end)
        else:
            keep_id = preferred_id if len(slot_runs) == 1 and len(runs) == 1 else None
            _append_slot(
                chunks,
                structure,
                sentence,
                run_start,
                run_end,
                identifier=keep_id,
                prefix=prefix,
            )


def _fallback_ranges(text: str) -> list[tuple[int, int]]:
    """Conservative lossless fallback: one slot per non-fixed text run."""
    if not text:
        return []
    ranges = []
    start = 0
    current_fixed = is_fixed_char(text[0])
    for index in range(1, len(text)):
        fixed = is_fixed_char(text[index])
        if fixed != current_fixed:
            ranges.append((start, index))
            start = index
            current_fixed = fixed
    ranges.append((start, len(text)))
    return ranges


def _ranges_from_ginza(doc, text: str) -> list[tuple[int, int]]:
    spans = list(_ginza.bunsetu_spans(doc))
    if not spans:
        raise ValueError("GiNZA did not return bunsetu spans")
    ranges: list[tuple[int, int]] = []
    cursor = 0
    for span in spans:
        start, end = int(span.start_char), int(span.end_char)
        if start < cursor or end <= start or end > len(text):
            raise ValueError("GiNZA returned overlapping or invalid source offsets")
        if start > cursor:
            ranges.append((cursor, start))
        ranges.append((start, end))
        cursor = end
    if cursor < len(text):
        ranges.append((cursor, len(text)))
    if "".join(text[start:end] for start, end in ranges) != text:
        raise ValueError("GiNZA ranges cannot reconstruct the source sentence")
    return ranges


def _structure_from_ranges(text: str, ranges: list[tuple[int, int]], *, prefix: str) -> tuple[list[dict], list[dict]]:
    chunks: list[dict] = []
    structure: list[dict] = []
    for start, end in ranges:
        _append_range(chunks, structure, text, start, end, prefix=prefix)
    return chunks, structure


def reconstruct_sentence(chunks, structure) -> str:
    by_id = {
        item.get("id"): item.get("text")
        for item in (chunks or [])
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    parts = []
    for element in structure or []:
        if not isinstance(element, dict):
            raise ValueError("练习结构格式无效")
        if element.get("type") == "fixed":
            text = element.get("text")
        elif element.get("type") == "slot":
            text = by_id.get(element.get("chunkId"))
        else:
            raise ValueError("练习结构元素类型无效")
        if not isinstance(text, str):
            raise ValueError("练习结构引用了无效词块")
        parts.append(text)
    return "".join(parts)


def validate_practice_data(sentence: str, chunks, structure, correct_order=None) -> tuple[bool, str]:
    if not isinstance(sentence, str):
        return False, "日语原句必须是字符串"
    if not isinstance(chunks, list) or not chunks:
        return False, "至少需要一个非标点词块"
    if not isinstance(structure, list) or not structure:
        return False, "练习结构不能为空"

    ids: list[str] = []
    by_id: dict[str, dict] = {}
    for item in chunks:
        if not isinstance(item, dict):
            return False, "词块格式无效"
        identifier, text = item.get("id"), item.get("text")
        start, end = item.get("start"), item.get("end")
        if not isinstance(identifier, str) or not identifier:
            return False, "每个词块都需要唯一 ID"
        if not isinstance(text, str) or not text:
            return False, "词块文字不能为空"
        if any(is_fixed_char(char) for char in text):
            return False, "可练习词块中不能包含标点或空白"
        if not isinstance(start, int) or not isinstance(end, int) or start < 0 or end <= start:
            return False, "词块原文位置无效"
        if end > len(sentence) or sentence[start:end] != text:
            return False, "词块文字和原句位置不一致"
        ids.append(identifier)
        by_id[identifier] = item
    if len(ids) != len(set(ids)):
        return False, "词块 ID 必须唯一"

    slot_ids = []
    cursor = 0
    for element in structure:
        if not isinstance(element, dict):
            return False, "练习结构格式无效"
        kind = element.get("type")
        start, end = element.get("start"), element.get("end")
        if not isinstance(start, int) or not isinstance(end, int) or start != cursor or end <= start:
            return False, "练习结构必须连续覆盖原句"
        if kind == "slot":
            identifier = element.get("chunkId")
            chunk = by_id.get(identifier)
            if not chunk or chunk["start"] != start or chunk["end"] != end:
                return False, "槽位引用的词块位置无效"
            slot_ids.append(identifier)
        elif kind == "fixed":
            text = element.get("text")
            if not isinstance(text, str) or sentence[start:end] != text:
                return False, "固定元素和原句位置不一致"
            if not text or any(not is_fixed_char(char) for char in text):
                return False, "固定元素只能包含标点或空白"
        else:
            return False, "练习结构元素类型无效"
        cursor = end
    if cursor != len(sentence):
        return False, "练习结构必须完整覆盖原句"
    if slot_ids != ids:
        return False, "槽位顺序必须与词块顺序一致"
    if correct_order is not None and correct_order != ids:
        return False, "正确词块顺序必须与槽位顺序一致"
    try:
        if reconstruct_sentence(chunks, structure) != sentence:
            return False, "练习结构无法无损还原日语原句"
    except ValueError as exc:
        return False, str(exc)
    return True, ""


def structure_from_manual_chunks(sentence: str, old_chunks) -> tuple[list[dict], list[dict]]:
    """Preserve manual non-punctuation boundaries while extracting fixed text."""
    if not isinstance(old_chunks, list) or not old_chunks:
        raise ValueError("人工词块为空")
    chunks: list[dict] = []
    structure: list[dict] = []
    cursor = 0
    seen_ids: set[str] = set()
    for item in old_chunks:
        if not isinstance(item, dict) or not isinstance(item.get("text"), str):
            raise ValueError("人工词块格式无效")
        text = item["text"]
        start, end = cursor, cursor + len(text)
        if not text or sentence[start:end] != text:
            raise ValueError("人工词块无法无损还原原句")
        identifier = item.get("id")
        preferred_id = identifier if isinstance(identifier, str) and identifier and identifier not in seen_ids else None
        before = len(chunks)
        _append_range(
            chunks,
            structure,
            sentence,
            start,
            end,
            preferred_id=preferred_id,
            prefix="m",
        )
        for chunk in chunks[before:]:
            if chunk["id"] in seen_ids:
                chunk["id"] = stable_chunk_id(
                    sentence, chunk["start"], chunk["end"], chunk["text"], prefix="m"
                )
                structure[-1]["chunkId"] = chunk["id"]
            seen_ids.add(chunk["id"])
        cursor = end
    if cursor != len(sentence):
        raise ValueError("人工词块无法完整覆盖原句")
    valid, message = validate_practice_data(
        sentence, chunks, structure, [chunk["id"] for chunk in chunks]
    )
    if not valid:
        raise ValueError(message)
    return chunks, structure


def analyze_sentence(text: str) -> dict:
    """Return learner chunks, fixed/slot structure, furigana and source metadata."""
    if not isinstance(text, str):
        raise TypeError("日语原句必须是字符串")
    if not text:
        return {
            "chunks": [],
            "structure": [],
            "correctOrder": [],
            "furigana": [],
            "source": "ginza",
            "schemaVersion": CHUNK_SCHEMA_VERSION,
        }

    try:
        doc = _document(text)
        ranges = _ranges_from_ginza(doc, text)
        chunks, structure = _structure_from_ranges(text, ranges, prefix="g")
        furigana = _furigana_from_doc(text, doc)
        source = "ginza"
    except Exception:
        LOGGER.exception("GiNZA analysis failed; using lossless fallback")
        ranges = _fallback_ranges(text)
        chunks, structure = _structure_from_ranges(text, ranges, prefix="f")
        furigana = [{"text": text}]
        source = "fallback"

    correct_order = [chunk["id"] for chunk in chunks]
    valid, message = validate_practice_data(text, chunks, structure, correct_order)
    if not valid:
        raise ValueError(message)
    return {
        "chunks": chunks,
        "structure": structure,
        "correctOrder": correct_order,
        "furigana": furigana,
        "source": source,
        "schemaVersion": CHUNK_SCHEMA_VERSION,
    }


def local_tokenize(text: str) -> list[dict]:
    """Compatibility wrapper: return only learner-sortable GiNZA chunks."""
    return analyze_sentence(text)["chunks"]


def validate_chunks(sentence: str, chunks, structure=None) -> tuple[bool, str]:
    """Compatibility wrapper for callers that now also provide structure."""
    if structure is None:
        return False, "缺少固定标点和槽位结构"
    return validate_practice_data(sentence, chunks, structure)


def katakana_to_hiragana(text: str) -> str:
    chars = []
    for char in text or "":
        code = ord(char)
        chars.append(chr(code - 0x60) if 0x30A1 <= code <= 0x30F6 else char)
    return "".join(chars)


def _is_kanji(char: str) -> bool:
    return ("\u4e00" <= char <= "\u9fff") or char in "々〆〤"


def _has_kanji(text: str) -> bool:
    return any(_is_kanji(char) for char in text)


def _merge_plain_segments(segments: list[dict]) -> list[dict]:
    merged: list[dict] = []
    for segment in segments:
        if merged and "ruby" not in segment and "ruby" not in merged[-1]:
            merged[-1] = {"text": merged[-1]["text"] + segment["text"]}
        else:
            merged.append(dict(segment))
    return merged


def _annotate_morpheme(surface: str, reading: str) -> list[dict]:
    if not surface:
        return []
    reading = katakana_to_hiragana(reading or "")
    if not reading or reading == surface or not _has_kanji(surface):
        return [{"text": surface}]

    left: list[dict] = []
    right: list[dict] = []
    middle = surface
    remaining = reading
    while middle and not _is_kanji(middle[0]):
        index = 0
        while index < len(middle) and not _is_kanji(middle[index]):
            index += 1
        prefix = middle[:index]
        if not remaining.startswith(prefix):
            return [{"text": surface, "ruby": reading}]
        left.append({"text": prefix})
        middle = middle[index:]
        remaining = remaining[len(prefix):]
    while middle and not _is_kanji(middle[-1]):
        index = len(middle)
        while index > 0 and not _is_kanji(middle[index - 1]):
            index -= 1
        suffix = middle[index:]
        if not remaining.endswith(suffix):
            return [{"text": surface, "ruby": reading}]
        right.insert(0, {"text": suffix})
        middle = middle[:index]
        remaining = remaining[:-len(suffix)] if suffix else remaining
    if not middle:
        return left + right
    if all(_is_kanji(char) for char in middle):
        annotated = [{"text": middle, "ruby": remaining}] if remaining else [{"text": middle}]
        return left + annotated + right
    return [{"text": surface, "ruby": reading}]


def _furigana_from_doc(text: str, doc) -> list[dict]:
    result: list[dict] = []
    cursor = 0
    for token in doc:
        start, end = int(token.idx), int(token.idx + len(token.text))
        if start > cursor:
            result.append({"text": text[cursor:start]})
        reading_values = token.morph.get("Reading")
        reading = reading_values[0] if reading_values else ""
        result.extend(_annotate_morpheme(text[start:end], reading))
        cursor = end
    if cursor < len(text):
        result.append({"text": text[cursor:]})
    result = _merge_plain_segments(result)
    if "".join(segment["text"] for segment in result) != text:
        return [{"text": text}]
    return result


def furigana_segments(text: str) -> list[dict]:
    if not isinstance(text, str):
        raise TypeError("日语文本必须是字符串")
    if not text:
        return []
    try:
        return _furigana_from_doc(text, _document(text))
    except Exception:
        LOGGER.exception("GiNZA furigana analysis failed; returning plain text")
        return [{"text": text}]
