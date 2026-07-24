import urllib.error

import pytest

from kwja_analyzer import (
    ANALYSIS_MODE,
    KWJAAnalyzerClient,
    KWJAAnalyzerError,
    KWJAUnavailableError,
    _source_projection,
    align_service_result,
    input_sha256,
)


def raw_result(text, *, phrase_indexes=None):
    projection = _source_projection(text)
    model_chars = list(projection.model_text)
    if phrase_indexes is None:
        phrase_indexes = [list(range(len(model_chars)))]
    return {
        "ok": True,
        "analyzer": "kwja",
        "modelSize": "tiny",
        "analyzerVersion": "kwja-2.5.1|model=tiny",
        "analysisMode": ANALYSIS_MODE,
        "inputSha256": input_sha256(text),
        "modelText": projection.model_text,
        "morphemes": [
            {
                "text": char,
                "reading": char,
                "lemma": char,
                "pos": "名詞",
                "subpos": "",
            }
            for char in model_chars
        ],
        "phrases": [
            {"morphemeIndexes": indexes} for indexes in phrase_indexes
        ],
    }


@pytest.mark.parametrize(
    "text",
    [
        "銀行と銀行。",
        "「ＡB」 #1 ～ 半角ABC",
        "（空白）  と半角ABC",
    ],
)
def test_alignment_offsets_restore_exact_original_with_duplicates_and_width(text):
    aligned = align_service_result(text, raw_result(text))
    assert "".join(item["text"] for item in aligned["morphemes"]) == text
    assert "".join(item["text"] for item in aligned["phrases"]) == text
    assert all(
        text[item["start"] : item["end"]] == item["text"]
        for item in aligned["morphemes"]
    )
    duplicate_offsets = [
        (item["start"], item["end"])
        for item in aligned["morphemes"]
        if item["text"] == "銀"
    ]
    if text.startswith("銀行"):
        assert duplicate_offsets == [(0, 1), (3, 4)]


def test_phrase_boundaries_must_be_contiguous_and_cover_every_morpheme():
    text = "世界中。"
    malformed = raw_result(text, phrase_indexes=[[0, 1], [3]])
    with pytest.raises(KWJAAnalyzerError, match="文节边界无效"):
        align_service_result(text, malformed)


def test_model_text_or_hash_mismatch_rejects_the_whole_analysis():
    text = "同じ字が二回ある。"
    malformed = raw_result(text)
    malformed["modelText"] += "!"
    with pytest.raises(KWJAAnalyzerError, match="无法无损映射"):
        align_service_result(text, malformed)

    malformed = raw_result(text)
    malformed["inputSha256"] = "0" * 64
    with pytest.raises(KWJAAnalyzerError, match="当前原句不匹配"):
        align_service_result(text, malformed)


def test_cross_character_unicode_composition_is_rejected_instead_of_guessing():
    # Half-width kana plus a separate voiced mark composes across source
    # boundaries under NFKC, so a model offset cannot be mapped unambiguously.
    with pytest.raises(KWJAAnalyzerError, match="跨越字符边界"):
        _source_projection("ﾊﾞ")


def test_unavailable_service_is_reported_explicitly(monkeypatch):
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            urllib.error.URLError("connection refused")
        ),
    )
    with pytest.raises(KWJAUnavailableError, match="暂时不可用"):
        KWJAAnalyzerClient("http://127.0.0.1:1", timeout_seconds=0.01).analyze(
            "解析できません。"
        )


def test_kwja_runtime_configuration_is_explicit_and_cpu_only():
    from pathlib import Path

    config = (Path(__file__).parents[1] / "kwja-config.yaml").read_text()
    assert "model_size: tiny" in config
    assert "device: cpu" in config
    assert "num_workers: 0" in config
    assert "torch_compile: false" in config
    assert "seq2seq_batch_size: 1" in config
    assert "word_batch_size: 1" in config
