import pytest


def slot_texts(analysis):
    return [chunk["text"] for chunk in analysis["chunks"]]


def fixed_texts(analysis):
    return [item["text"] for item in analysis["structure"] if item["type"] == "fixed"]


def test_kwja_phrases_return_natural_bunsetu_slots_without_punctuation():
    from tokenizer import analyze_sentence, reconstruct_sentence

    sentence = "私は、家で日本語を勉強しています。"
    analysis = analyze_sentence(sentence)
    assert slot_texts(analysis) == ["私は", "家で", "日本語を", "勉強しています"]
    assert fixed_texts(analysis) == ["、", "。"]
    assert reconstruct_sentence(analysis["chunks"], analysis["structure"]) == sentence
    assert analysis["source"] == "kwja_tiny_phrase"
    assert analysis["schemaVersion"] == 3
    assert analysis["analysis"]["analyzer"] == "kwja"


def test_ids_are_stable_unique_and_position_aware_for_duplicate_text():
    from tokenizer import analyze_sentence

    sentence = "庭に猫がいて、家に犬がいる。"
    first = analyze_sentence(sentence)
    second = analyze_sentence(sentence)
    first_ids = [chunk["id"] for chunk in first["chunks"]]
    assert first_ids == [chunk["id"] for chunk in second["chunks"]]
    assert len(first_ids) == len(set(first_ids))
    assert all(chunk["text"] == sentence[chunk["start"]:chunk["end"]] for chunk in first["chunks"])


@pytest.mark.parametrize(
    "sentence,expected_fixed",
    [
        ("本当に！？……はい。", ["！？……", "。"]),
        ("僕が「さよなら」と言った。", ["「", "」", "。"]),
        ("（雨なら）家にいます。", ["（", "）", "。"]),
    ],
)
def test_consecutive_punctuation_quotes_and_parentheses_are_fixed(sentence, expected_fixed):
    from tokenizer import analyze_sentence, is_fixed_char, reconstruct_sentence

    analysis = analyze_sentence(sentence)
    assert fixed_texts(analysis) == expected_fixed
    assert all(not any(is_fixed_char(char) for char in chunk["text"]) for chunk in analysis["chunks"])
    assert reconstruct_sentence(analysis["chunks"], analysis["structure"]) == sentence


@pytest.mark.parametrize(
    "sentence",
    [
        "すべてひらがなだけでかいています。",
        "JLPT N2を2026年12月に受ける。中文OK。",
        "Version 2.0 は OK？",
    ],
)
def test_kana_english_numbers_and_spaces_restore_exactly(sentence):
    from tokenizer import analyze_sentence, reconstruct_sentence, validate_practice_data

    analysis = analyze_sentence(sentence)
    assert reconstruct_sentence(analysis["chunks"], analysis["structure"]) == sentence
    assert validate_practice_data(
        sentence, analysis["chunks"], analysis["structure"], analysis["correctOrder"]
    )[0]


def test_kwja_failure_is_explicit_and_never_uses_lossy_fallback(monkeypatch):
    import tokenizer
    from kwja_analyzer import KWJAUnavailableError

    monkeypatch.setattr(
        tokenizer,
        "analyze_with_kwja",
        lambda text: (_ for _ in ()).throw(KWJAUnavailableError("KWJA 服务不可用")),
    )
    with pytest.raises(KWJAUnavailableError, match="KWJA 服务不可用"):
        tokenizer.analyze_sentence("私は、失敗しても古い分析を守ります。")


def test_manual_boundaries_are_preserved_while_punctuation_is_extracted():
    from tokenizer import structure_from_manual_chunks, validate_practice_data

    sentence = "僕が「さよなら」と言った。"
    old = [
        {"id": "manual-a", "text": "僕が"},
        {"id": "manual-b", "text": "「さよなら」"},
        {"id": "manual-c", "text": "と言った"},
        {"id": "manual-d", "text": "。"},
    ]
    chunks, structure = structure_from_manual_chunks(sentence, old)
    assert [chunk["text"] for chunk in chunks] == ["僕が", "さよなら", "と言った"]
    assert [item["text"] for item in structure if item["type"] == "fixed"] == ["「", "」", "。"]
    assert chunks[0]["id"] == "manual-a"
    assert chunks[2]["id"] == "manual-c"
    assert validate_practice_data(sentence, chunks, structure, [chunk["id"] for chunk in chunks])[0]


def test_invalid_structure_and_punctuation_chunk_are_rejected():
    from tokenizer import validate_practice_data

    sentence = "はい。"
    chunks = [{"id": "bad", "text": "はい。", "start": 0, "end": 3}]
    structure = [{"type": "slot", "chunkId": "bad", "start": 0, "end": 3}]
    valid, message = validate_practice_data(sentence, chunks, structure, ["bad"])
    assert not valid
    assert "标点" in message
