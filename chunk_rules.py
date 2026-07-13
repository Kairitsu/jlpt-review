"""Maintainable local merge rules for Sudachi sentence-order chunks.

The tokenizer supplies exact-position A/B/C morphemes.  This module only
decides which B-mode boundaries should disappear; it never rewrites text.
"""

from __future__ import annotations

from collections.abc import Sequence


# Longest expressions first.  Variants live here so extending the grammar
# inventory does not require changing the tokenizer or API layer.
FIXED_EXPRESSIONS = (
    "なければなりません",
    "なくてはいけません",
    "なくてはならない",
    "なければならない",
    "ことにしています",
    "ことにしている",
    "かもしれません",
    "てはいけません",
    "ではいけません",
    "ほうがいい",
    "方がいい",
    "てもいい",
    "でもいい",
    "てもよい",
    "でもよい",
    "てはいけない",
    "ではいけない",
    "かもしれない",
    "つもりです",
    "つもりだ",
)


def _pos(token, index: int) -> str:
    return token.pos[index] if len(token.pos) > index else ""


def _is_separator(token) -> bool:
    return token.is_punctuation or _pos(token, 0) == "空白"


def _fine_inside(token, fine_tokens: Sequence):
    return [part for part in fine_tokens if token.start <= part.start and part.end <= token.end]


def _first_fine(token, fine_tokens: Sequence):
    parts = _fine_inside(token, fine_tokens)
    return parts[0] if parts else token


def _last_fine(token, fine_tokens: Sequence):
    parts = _fine_inside(token, fine_tokens)
    return parts[-1] if parts else token


def _join_range(tokens: Sequence, joins: list[bool], start: int, end: int) -> None:
    """Join all token boundaries strictly inside the character range."""
    for index in range(len(tokens) - 1):
        left, right = tokens[index], tokens[index + 1]
        if start < left.end == right.start < end and not (_is_separator(left) or _is_separator(right)):
            joins[index] = True


def _fixed_expression_ranges(text: str):
    occupied: set[tuple[int, int]] = set()
    for expression in FIXED_EXPRESSIONS:
        offset = 0
        while True:
            start = text.find(expression, offset)
            if start < 0:
                break
            span = (start, start + len(expression))
            if span not in occupied:
                occupied.add(span)
                yield span
            offset = start + 1


def merge_boundaries(text: str, base_tokens: Sequence, fine_tokens: Sequence, coarse_tokens: Sequence) -> list[bool]:
    """Return one boolean per B-mode boundary; true means merge it."""
    joins = [False] * max(0, len(base_tokens) - 1)

    # C mode protects dictionary-recognized compound/proper nouns and long
    # lexical items.  Punctuation and whitespace are never absorbed.
    for coarse in coarse_tokens:
        if _is_separator(coarse):
            continue
        pos0, pos1 = _pos(coarse, 0), _pos(coarse, 1)
        if pos0 in {"名詞", "接頭辞", "接尾辞"} or pos1 == "固有名詞":
            _join_range(base_tokens, joins, coarse.start, coarse.end)

    # Fixed grammar is matched by exact surface ranges, independent of the
    # dictionary's current token-boundary choices.
    for start, end in _fixed_expression_ranges(text):
        _join_range(base_tokens, joins, start, end)

    # A-mode boundary features drive conjugation and suffix rules.  We apply
    # them to B-mode boundaries, preserving B as the primary granularity.
    for index, (left, right) in enumerate(zip(base_tokens, base_tokens[1:])):
        if _is_separator(left) or _is_separator(right):
            continue
        left_fine = _last_fine(left, fine_tokens)
        right_fine = _first_fine(right, fine_tokens)
        left_pos, right_pos = _pos(left_fine, 0), _pos(right_fine, 0)
        right_pos1 = _pos(right_fine, 1)
        left_dict, right_dict = left_fine.dictionary_form, right_fine.dictionary_form

        # 名詞 + 接尾辞 (田中 + さん, 2026 + 年, etc.).
        if left_pos in {"名詞", "代名詞"} and right_pos == "接尾辞":
            joins[index] = True

        # サ変可能名詞 + する and all conjugated surfaces of する.
        if left_pos == "名詞" and "サ変可能" in left_fine.pos and right_pos == "動詞" and right_dict == "する":
            joins[index] = True

        # Verb/adjective inflection plus auxiliary (ます, た, ない, etc.).
        if left_pos in {"動詞", "形容詞", "形状詞"} and right_pos == "助動詞":
            joins[index] = True

        # Negative non-independent adjective sometimes is tagged 形容詞 rather
        # than 助動詞 (for example 静かではない).
        if left_pos in {"動詞", "形容詞", "形状詞"} and right_dict == "ない" and right_pos in {"助動詞", "形容詞"}:
            joins[index] = True

        # Copula/polite auxiliary + conjunctive particle (ですが, だけど).
        if left_dict in {"だ", "です"} and right_pos == "助詞" and right_pos1 == "接続助詞":
            joins[index] = True

    # 動詞連用形 + て/で + いる/ある.  The auxiliary following いる/ある is
    # joined by the general conjugation rule above.
    for index in range(len(base_tokens) - 2):
        first, middle, last = base_tokens[index:index + 3]
        if any(_is_separator(token) for token in (first, middle, last)):
            continue
        first_fine = _last_fine(first, fine_tokens)
        middle_fine = _first_fine(middle, fine_tokens)
        last_fine = _first_fine(last, fine_tokens)
        if (
            _pos(first_fine, 0) == "動詞"
            and middle.text in {"て", "で"}
            and _pos(middle_fine, 0) == "助詞"
            and last_fine.dictionary_form in {"いる", "ある"}
            and _pos(last_fine, 0) == "動詞"
        ):
            joins[index] = joins[index + 1] = True

    return joins
