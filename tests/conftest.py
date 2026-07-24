import hashlib
import unicodedata

import pytest


_READINGS = {
    "日本語": "にほんご",
    "勉強": "べんきょう",
    "電気": "でんき",
    "消えた": "きえた",
    "田中": "たなか",
    "東京": "とうきょう",
    "行った": "いった",
    "行きます": "いきます",
    "世界": "せかい",
    "銀行": "ぎんこう",
    "人々": "ひとびと",
    "交流": "こうりゅう",
    "庭": "にわ",
    "猫": "ねこ",
    "犬": "いぬ",
    "家": "いえ",
    "水": "みず",
    "私": "わたし",
    "文": "ぶん",
    "年": "ねん",
}


def _fake_kwja_analysis(text):
    morphemes = []
    cursor = 0
    vocabulary = sorted(_READINGS, key=len, reverse=True)
    while cursor < len(text):
        matched = next(
            (word for word in vocabulary if text.startswith(word, cursor)),
            None,
        )
        surface = matched or text[cursor]
        end = cursor + len(surface)
        category = unicodedata.category(surface[0])
        pos = "特殊" if category.startswith("P") or surface.isspace() else "名詞"
        morphemes.append(
            {
                "text": surface,
                "reading": _READINGS.get(surface, surface),
                "lemma": surface,
                "pos": pos,
                "subpos": "",
                "start": cursor,
                "end": end,
            }
        )
        cursor = end

    ranges = []
    start = 0
    for index, char in enumerate(text):
        if char in "はもでをにへがと、。！？）」』":
            ranges.append((start, index + 1))
            start = index + 1
    if start < len(text):
        ranges.append((start, len(text)))
    ranges = [item for item in ranges if item[0] < item[1]]

    phrases = []
    for start, end in ranges:
        indexes = [
            index
            for index, morpheme in enumerate(morphemes)
            if morpheme["start"] >= start and morpheme["end"] <= end
        ]
        phrases.append(
            {
                "start": start,
                "end": end,
                "text": text[start:end],
                "morphemeIndexes": indexes,
            }
        )
    return {
        "analyzer": "kwja",
        "modelSize": "tiny",
        "analyzerVersion": "kwja-test",
        "schemaVersion": 1,
        "inputSha256": hashlib.sha256(text.encode()).hexdigest(),
        "analysisMode": "char,seq2seq,word",
        "morphemes": morphemes,
        "phrases": phrases,
        "furigana": [],
        "chunks": [],
        "practiceStructure": [],
    }


@pytest.fixture(autouse=True)
def fake_kwja_service(monkeypatch):
    """Keep application tests fast; service/aligner tests opt into real fixtures."""
    import tokenizer

    monkeypatch.setattr(tokenizer, "analyze_with_kwja", _fake_kwja_analysis)
