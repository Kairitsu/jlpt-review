import copy

from reading_cards import (
    generate_reading_cards,
    normalize_reading,
    validate_reading_payload,
)


def analysis(*morphemes):
    return {"analyzerVersion": "kwja-2.5.1|model=tiny", "morphemes": list(morphemes)}


def morpheme(surface, reading, start, end, *, lemma=None, pos="名詞"):
    return {
        "text": surface,
        "reading": reading,
        "lemma": lemma or surface,
        "pos": pos,
        "subpos": "",
        "start": start,
        "end": end,
    }


def test_same_kanji_uses_contextual_kwja_reading_not_global_override():
    first_sentence = "今日中に終える。"
    first, _ = generate_reading_cards(
        first_sentence,
        analysis(morpheme("中", "ジュウ", 2, 3)),
    )
    second_sentence = "中に入る。"
    second, _ = generate_reading_cards(
        second_sentence,
        analysis(morpheme("中", "ナカ", 0, 1)),
    )
    assert first[0]["payload"]["correctReading"] == "じゅう"
    assert second[0]["payload"]["correctReading"] == "なか"


def test_duplicate_same_kanji_in_one_sentence_has_position_aware_cards():
    sentence = "日々、日記を書く。"
    cards, _ = generate_reading_cards(
        sentence,
        analysis(
            morpheme("日々", "ヒビ", 0, 2),
            morpheme("日記", "ニッキ", 3, 5),
            morpheme("書く", "カク", 6, 8, pos="動詞"),
        ),
    )
    assert [card["cardKey"] for card in cards] == [
        "reading:0:2:日々",
        "reading:3:5:日記",
        "reading:6:8:書く",
    ]
    assert [card["payload"]["target"]["start"] for card in cards] == [0, 3, 6]


def test_okurigana_and_compound_targets_keep_exact_source_ranges():
    sentence = "銀行で食べ物を買う。"
    cards, _ = generate_reading_cards(
        sentence,
        analysis(
            morpheme("銀行", "ギンコウ", 0, 2),
            morpheme("食べ物", "タベモノ", 3, 6),
            morpheme("買う", "カウ", 7, 9, pos="動詞"),
        ),
    )
    assert [card["payload"]["target"]["surface"] for card in cards] == [
        "銀行",
        "食べ物",
        "買う",
    ]
    for card in cards:
        payload = card["payload"]
        target = payload["target"]
        assert sentence[target["start"] : target["end"]] == target["surface"]
        assert validate_reading_payload(sentence, payload) == (True, "")


def test_kana_only_sentence_creates_no_reading_cards():
    sentence = "これはひらがなだけです。"
    cards, skipped = generate_reading_cards(
        sentence,
        analysis(morpheme("これ", "コレ", 0, 2), morpheme("ひらがな", "ヒラガナ", 3, 7)),
    )
    assert cards == []
    assert {item["reason"] for item in skipped} == {"no_kanji"}


def test_insufficient_distractors_skips_instead_of_random_padding():
    cards, skipped = generate_reading_cards(
        "亜",
        analysis(morpheme("亜", "ン", 0, 1)),
    )
    assert cards == []
    assert skipped == [
        {
            "morphemeIndex": 0,
            "start": 0,
            "end": 1,
            "surface": "亜",
            "reason": "insufficient_distractors",
            "candidateCount": 0,
        }
    ]


def test_options_are_deterministic_normalized_unique_and_exactly_four():
    sentence = "銀行。"
    first, _ = generate_reading_cards(
        sentence, analysis(morpheme("銀行", "ギンコウ", 0, 2))
    )
    second, _ = generate_reading_cards(
        sentence, analysis(morpheme("銀行", "ぎんこう", 0, 2))
    )
    assert first == second
    payload = first[0]["payload"]
    readings = [option["reading"] for option in payload["options"]]
    assert len(readings) == len(set(readings)) == 4
    assert all(reading == normalize_reading(reading) for reading in readings)
    assert readings.count(payload["correctReading"]) == 1

    invalid = copy.deepcopy(payload)
    invalid["options"][3]["reading"] = invalid["options"][2]["reading"]
    valid, message = validate_reading_payload(sentence, invalid)
    assert valid is False
    assert "互不相同" in message

