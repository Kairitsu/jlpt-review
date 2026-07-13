import pytest


def texts(sentence):
    from tokenizer import local_tokenize

    return [chunk["text"] for chunk in local_tokenize(sentence)]


def test_all_split_modes_run_and_restore_exactly():
    from tokenizer import analyze_modes

    sentence = "高輪ゲートウェイ駅へ行きます。"
    modes = analyze_modes(sentence)
    assert set(modes) == {"A", "B", "C"}
    assert all("".join(token.text for token in tokens) == sentence for tokens in modes.values())
    assert len(modes["A"]) >= len(modes["B"]) >= len(modes["C"])


def test_person_name_suffix_and_compound_noun_are_protected():
    chunks = texts("田中さんは東京大学で勉強しています。")
    assert "田中さん" in chunks
    assert "東京大学" in chunks


@pytest.mark.parametrize(
    "sentence,expected",
    [
        ("本を読んでいる。", "読んでいる"),
        ("窓が開けてある。", "開けてある"),
        ("彼は来ない。", "来ない"),
        ("ここで勉強しています。", "勉強しています"),
    ],
)
def test_conjugation_merge_rules(sentence, expected):
    assert expected in texts(sentence)


@pytest.mark.parametrize(
    "sentence,expected",
    [
        ("電車のほうがいい。", "ほうがいい"),
        ("旅行することにしている。", "ことにしている"),
        ("行かなければならない。", "なければならない"),
        ("ここで食べてもいい。", "てもいい"),
        ("入ってはいけない。", "てはいけない"),
        ("雨かもしれない。", "かもしれない"),
        ("旅行するつもりだ。", "つもりだ"),
    ],
)
def test_fixed_grammar_expressions(sentence, expected):
    assert any(expected in chunk for chunk in texts(sentence))


def test_duplicate_particles_have_unique_ids_and_punctuation_is_independent():
    from tokenizer import local_tokenize

    sentence = "私は庭にいて、猫に水をあげた。"
    chunks = local_tokenize(sentence)
    ni = [chunk for chunk in chunks if chunk["text"] == "に"]
    assert len(ni) == 2 and ni[0]["id"] != ni[1]["id"]
    assert "、" in [chunk["text"] for chunk in chunks]
    assert "。" in [chunk["text"] for chunk in chunks]
    assert all(not (len(chunk["text"]) > 1 and any(mark in chunk["text"] for mark in "。、！？")) for chunk in chunks)


@pytest.mark.parametrize(
    "sentence",
    [
        "すべてひらがなだけでかいています。",
        "JLPT N2を2026年12月に受ける。中文OK。",
    ],
)
def test_kana_and_mixed_text_restore_exactly(sentence):
    chunks = texts(sentence)
    assert "".join(chunks) == sentence


def test_empty_and_invalid_input():
    from tokenizer import analyze, local_tokenize

    assert local_tokenize("") == []
    with pytest.raises(TypeError, match="字符串"):
        local_tokenize(None)
    with pytest.raises(ValueError, match="A、B 或 C"):
        analyze("日本語", "D")


def test_manual_split_and_merge_keep_exact_reconstruction():
    from tokenizer import local_tokenize, validate_chunks

    sentence = "田中さんは来ない。"
    chunks = local_tokenize(sentence)
    index = next(i for i, chunk in enumerate(chunks) if chunk["text"] == "田中さん")
    original = chunks[index]
    split = chunks[:index] + [
        {"id": "manual-a", "text": original["text"][:2]},
        {"id": "manual-b", "text": original["text"][2:]},
    ] + chunks[index + 1:]
    assert validate_chunks(sentence, split)[0]
    merged = split[:index] + [{"id": "manual-merged", "text": split[index]["text"] + split[index + 1]["text"]}] + split[index + 2:]
    assert validate_chunks(sentence, merged)[0]
