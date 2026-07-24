from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Iterable
from typing import Any


GENERATOR_VERSION = 1
READING_SOURCE = "kwja-tiny-seq2seq"
KANJI_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff々〆〤]")
PURE_NUMBER_RE = re.compile(r"[0-9０-９]+")
HIRAGANA_RE = re.compile(r"[ぁ-ゖー]+")
EXCLUDED_POS = {"助詞", "助動詞", "特殊", "補助記号", "記号"}
SMALL_KANA = set("ぁぃぅぇぉゃゅょゎ")
COMBINING_BASES = set("きぎしじちぢにひびぴみりいくぐすずつづぬふぶぷむるてで")

_VOICE_GROUPS = (
    "かが",
    "きぎ",
    "くぐ",
    "けげ",
    "こご",
    "さざ",
    "しじ",
    "すず",
    "せぜ",
    "そぞ",
    "ただ",
    "ちぢ",
    "つづ",
    "てで",
    "とど",
    "はばぱ",
    "ひびぴ",
    "ふぶぷ",
    "へべぺ",
    "ほぼぽ",
)
_NEAR_GROUPS = (
    "あいうえお",
    "かきくけこ",
    "さしすせそ",
    "たちつてと",
    "なにぬねの",
    "はひふへほ",
    "まみむめも",
    "やゆよ",
    "らりるれろ",
    "わを",
)
_KATAKANA_OFFSET = 0x60


def normalize_reading(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value or "").strip()
    chars = []
    for char in normalized:
        code = ord(char)
        chars.append(
            chr(code - _KATAKANA_OFFSET)
            if 0x30A1 <= code <= 0x30F6
            else char
        )
    return "".join(chars)


def morae(reading: str) -> list[str]:
    result: list[str] = []
    for char in reading:
        if char in SMALL_KANA:
            if not result:
                return []
            result[-1] += char
        else:
            result.append(char)
    return result


def is_valid_reading(value: str) -> bool:
    reading = normalize_reading(value)
    if not reading or HIRAGANA_RE.fullmatch(reading) is None:
        return False
    if reading[0] in SMALL_KANA | {"っ", "ー"}:
        return False
    if reading[-1] in SMALL_KANA | {"っ"}:
        return False
    previous = ""
    for char in reading:
        if char in SMALL_KANA and previous not in COMBINING_BASES:
            return False
        if char == "ー" and previous in {"", "ー", "っ"}:
            return False
        if char == "っ" and previous == "っ":
            return False
        previous = char
    return bool(morae(reading))


def _edit_distance(left: str, right: str) -> int:
    previous = list(range(len(right) + 1))
    for left_index, left_char in enumerate(left, start=1):
        current = [left_index]
        for right_index, right_char in enumerate(right, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[right_index] + 1,
                    previous[right_index - 1] + (left_char != right_char),
                )
            )
        previous = current
    return previous[-1]


def _replace_one(reading: str, groups: tuple[str, ...]) -> set[str]:
    candidates: set[str] = set()
    for index, char in enumerate(reading):
        for group in groups:
            if char not in group:
                continue
            for replacement in group:
                if replacement != char:
                    candidates.add(reading[:index] + replacement + reading[index + 1 :])
    return candidates


def _controlled_variants(reading: str) -> set[str]:
    candidates = _replace_one(reading, _VOICE_GROUPS)
    candidates.update(_replace_one(reading, _NEAR_GROUPS))

    if "っ" in reading:
        candidates.add(reading.replace("っ", "", 1))
    units = morae(reading)
    if len(units) >= 2:
        boundary = len(units[0])
        candidates.add(reading[:boundary] + "っ" + reading[boundary:])

    if "ー" in reading:
        candidates.add(reading.replace("ー", "", 1))
    elif reading:
        last = reading[-1]
        if last in "あかがさざただなはばぱまやらわ":
            candidates.add(reading + "あ")
        elif last in "いきぎしじちぢにひびぴみり":
            candidates.add(reading + "い")
        elif last in "うくぐすずつづぬふぶぷむゆる":
            candidates.add(reading + "う")
        elif last in "えけげせぜてでねへべぺめれ":
            candidates.add(reading + "い")
        elif last in "おこごそぞとどのほぼぽもよろを":
            candidates.add(reading + "う")

    for small, ordinary in (
        ("きゃ", "きや"),
        ("きゅ", "きゆ"),
        ("きょ", "きよ"),
        ("しゃ", "しや"),
        ("しゅ", "しゆ"),
        ("しょ", "しよ"),
        ("ちゃ", "ちや"),
        ("ちゅ", "ちゆ"),
        ("ちょ", "ちよ"),
        ("にゃ", "にや"),
        ("ひゃ", "ひや"),
        ("みゃ", "みや"),
        ("りゃ", "りや"),
        ("ぎゃ", "ぎや"),
        ("じゃ", "じや"),
        ("びゃ", "びや"),
        ("ぴゃ", "ぴや"),
    ):
        if small in reading:
            candidates.add(reading.replace(small, ordinary, 1))
        if ordinary in reading:
            candidates.add(reading.replace(ordinary, small, 1))

    # A second controlled change is only considered when the first pass cannot
    # offer enough nearby choices.  Validation and ranking still apply.
    first_pass = list(candidates)
    if len(candidates) < 8:
        for candidate in first_pass:
            candidates.update(_replace_one(candidate, _VOICE_GROUPS))
    return candidates


def _option_id(card_key: str, reading: str) -> str:
    material = f"{card_key}\0{reading}".encode("utf-8")
    return "r" + hashlib.blake2s(material, digest_size=8).hexdigest()


def _candidate_pool(
    correct: str,
    surface: str,
    corpus_readings: Iterable[dict[str, Any]],
) -> list[tuple[str, int]]:
    ranked: dict[str, int] = {}
    corpus: list[tuple[str, str]] = []
    for item in corpus_readings:
        if not isinstance(item, dict):
            continue
        reading = normalize_reading(str(item.get("reading") or ""))
        item_surface = str(item.get("surface") or "")
        if reading == correct or not is_valid_reading(reading):
            continue
        corpus.append((item_surface, reading))
        priority = 0 if item_surface == surface else 1
        ranked[reading] = min(ranked.get(reading, 99), priority)
    for candidate in _controlled_variants(correct):
        normalized = normalize_reading(candidate)
        if normalized != correct and is_valid_reading(normalized):
            ranked[normalized] = min(ranked.get(normalized, 99), 2)

    correct_mora = len(morae(correct))
    candidates = []
    for candidate, priority in ranked.items():
        mora_difference = abs(len(morae(candidate)) - correct_mora)
        if mora_difference > 1:
            continue
        if abs(len(candidate) - len(correct)) > 2:
            continue
        candidates.append(
            (
                candidate,
                priority * 100
                + mora_difference * 20
                + _edit_distance(correct, candidate),
            )
        )
    candidates.sort(key=lambda item: (item[1], item[0]))
    return candidates


def generate_reading_cards(
    sentence: str,
    analysis: dict[str, Any],
    *,
    corpus_readings: Iterable[dict[str, Any]] = (),
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Generate deterministic independent reading-card payloads and skips."""
    analyzer_version = str(analysis.get("analyzerVersion") or "")
    cards: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    corpus = list(corpus_readings)

    for index, morpheme in enumerate(analysis.get("morphemes") or []):
        reason = ""
        if not isinstance(morpheme, dict):
            reason = "invalid_morpheme"
            surface = ""
            start = end = -1
        else:
            start, end = morpheme.get("start"), morpheme.get("end")
            if (
                not isinstance(start, int)
                or not isinstance(end, int)
                or start < 0
                or end <= start
                or end > len(sentence)
            ):
                reason = "invalid_offset"
                surface = ""
            else:
                surface = sentence[start:end]

        reading = normalize_reading(str(morpheme.get("reading") or "")) if isinstance(morpheme, dict) else ""
        pos = str(morpheme.get("pos") or "") if isinstance(morpheme, dict) else ""
        if not reason and not KANJI_RE.search(surface):
            reason = "no_kanji"
        elif not reason and (pos in EXCLUDED_POS or any(pos.startswith(value) for value in EXCLUDED_POS)):
            reason = "excluded_pos"
        elif not reason and PURE_NUMBER_RE.fullmatch(surface):
            reason = "pure_number"
        elif not reason and not is_valid_reading(reading):
            reason = "invalid_reading"
        elif not reason and normalize_reading(surface) == reading:
            reason = "reading_equals_surface"

        if reason:
            skipped.append({"morphemeIndex": index, "reason": reason})
            continue

        card_key = f"reading:{start}:{end}:{surface}"
        candidates = _candidate_pool(reading, surface, corpus)
        distractors = [candidate for candidate, _ in candidates[:3]]
        if len(distractors) < 3:
            skipped.append(
                {
                    "morphemeIndex": index,
                    "start": start,
                    "end": end,
                    "surface": surface,
                    "reason": "insufficient_distractors",
                    "candidateCount": len(distractors),
                }
            )
            continue

        all_readings = [reading, *distractors]
        options = [
            {
                "id": _option_id(card_key, option_reading),
                "reading": option_reading,
            }
            for option_reading in all_readings
        ]
        payload = {
            "target": {
                "start": start,
                "end": end,
                "surface": surface,
                "lemma": str(morpheme.get("lemma") or ""),
                "pos": pos,
            },
            "correctReading": reading,
            "correctOptionId": options[0]["id"],
            "options": options,
            "readingSource": READING_SOURCE,
            "analyzerVersion": analyzer_version,
            "generatorVersion": GENERATOR_VERSION,
        }
        cards.append({"cardType": "kanji_reading", "cardKey": card_key, "payload": payload})
    return cards, skipped


def validate_reading_payload(sentence: str, payload: dict[str, Any]) -> tuple[bool, str]:
    if not isinstance(payload, dict):
        return False, "读音题数据格式无效"
    target = payload.get("target")
    if not isinstance(target, dict):
        return False, "读音题缺少目标词"
    start, end, surface = target.get("start"), target.get("end"), target.get("surface")
    if (
        not isinstance(start, int)
        or not isinstance(end, int)
        or not isinstance(surface, str)
        or start < 0
        or end <= start
        or end > len(sentence)
        or sentence[start:end] != surface
    ):
        return False, "读音题目标位置与原句不一致"
    if not KANJI_RE.search(surface):
        return False, "读音题目标不含汉字"
    correct = normalize_reading(str(payload.get("correctReading") or ""))
    options = payload.get("options")
    if not is_valid_reading(correct):
        return False, "正确读音格式无效"
    if not isinstance(options, list) or len(options) != 4:
        return False, "读音题必须恰好有四个选项"
    option_ids: set[str] = set()
    option_readings: list[str] = []
    for option in options:
        if not isinstance(option, dict):
            return False, "读音选项格式无效"
        option_id = option.get("id")
        reading = normalize_reading(str(option.get("reading") or ""))
        if not isinstance(option_id, str) or not option_id or option_id in option_ids:
            return False, "读音选项 ID 必须唯一"
        if not is_valid_reading(reading):
            return False, "读音选项必须是有效平假名"
        option_ids.add(option_id)
        option_readings.append(reading)
    if len(set(option_readings)) != 4:
        return False, "四个读音选项必须互不相同"
    if option_readings.count(correct) != 1:
        return False, "正确读音必须在选项中且只出现一次"
    correct_option_id = payload.get("correctOptionId")
    matching_id = options[option_readings.index(correct)]["id"]
    if correct_option_id != matching_id:
        return False, "正确选项 ID 与正确读音不一致"
    return True, ""
