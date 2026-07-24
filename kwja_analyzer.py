from __future__ import annotations

import hashlib
import json
import os
import unicodedata
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


ANALYZER_NAME = "kwja"
MODEL_SIZE = "tiny"
ANALYSIS_TASKS = ("char", "seq2seq", "word")
ANALYSIS_MODE = ",".join(ANALYSIS_TASKS)
ANALYSIS_SCHEMA_VERSION = 1
DEFAULT_ANALYZER_URL = "http://analyzer:8100"
DEFAULT_TIMEOUT_SECONDS = 180.0

# KWJA applies these substitutions before inference.  Keeping an explicit
# source projection lets model text differ while every published offset still
# points into the exact user-entered string.
_KWJA_TRANSLATIONS = str.maketrans({'"': "”", "#": "＃", "▁": "▂"})


class KWJAAnalyzerError(RuntimeError):
    """The analyzer rejected a sentence or returned unusable data."""


class KWJAUnavailableError(KWJAAnalyzerError):
    """The single KWJA service cannot currently accept analysis work."""


@dataclass(frozen=True)
class _Projection:
    model_text: str
    boundary_to_source: dict[int, int]


def input_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _model_piece(char: str) -> str:
    # Control characters cannot be represented safely by KWJA's line-based
    # protocol.  Refuse them instead of silently deleting source characters.
    if unicodedata.category(char) == "Cc":
        raise KWJAAnalyzerError("原句包含 KWJA 无法可靠对齐的控制字符")
    return unicodedata.normalize("NFKC", char).translate(_KWJA_TRANSLATIONS)


def _source_projection(text: str) -> _Projection:
    pieces: list[str] = []
    boundaries = {0: 0}
    cursor = 0
    for source_end, char in enumerate(text, start=1):
        piece = _model_piece(char)
        if not piece:
            raise KWJAAnalyzerError("原句包含规范化后消失的字符，无法可靠对齐")
        pieces.append(piece)
        cursor += len(piece)
        boundaries[cursor] = source_end
    model_text = "".join(pieces)

    # Normalizing per source character is what makes the boundary map
    # reversible.  If Unicode composition crosses source-character boundaries,
    # it is unsafe to assign a model boundary to the original text.
    whole = unicodedata.normalize("NFKC", text).translate(_KWJA_TRANSLATIONS)
    if model_text != whole:
        raise KWJAAnalyzerError("原句 Unicode 规范化跨越字符边界，无法可靠对齐")
    return _Projection(model_text=model_text, boundary_to_source=boundaries)


def _string(value: Any, field: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise KWJAAnalyzerError(f"KWJA 返回的 {field} 字段格式无效")
    return value


def align_service_result(text: str, raw: dict[str, Any]) -> dict[str, Any]:
    """Align a structured KWJA service response back to the original string."""
    if not isinstance(raw, dict):
        raise KWJAAnalyzerError("KWJA 服务返回格式无效")
    if raw.get("analyzer") != ANALYZER_NAME or raw.get("modelSize") != MODEL_SIZE:
        raise KWJAAnalyzerError("KWJA 服务模型配置与应用要求不一致")
    if raw.get("analysisMode") != ANALYSIS_MODE:
        raise KWJAAnalyzerError("KWJA 服务分析任务与应用要求不一致")
    if raw.get("inputSha256") != input_sha256(text):
        raise KWJAAnalyzerError("KWJA 服务结果与当前原句不匹配")

    projection = _source_projection(text)
    returned_model_text = _string(raw.get("modelText"), "modelText")
    if returned_model_text != projection.model_text:
        raise KWJAAnalyzerError("KWJA 模型文本无法无损映射回原句")

    raw_morphemes = raw.get("morphemes")
    if not isinstance(raw_morphemes, list) or (text and not raw_morphemes):
        raise KWJAAnalyzerError("KWJA 未返回有效形态素")

    morphemes: list[dict[str, Any]] = []
    model_cursor = 0
    for index, item in enumerate(raw_morphemes):
        if not isinstance(item, dict):
            raise KWJAAnalyzerError("KWJA 形态素格式无效")
        model_surface = _string(item.get("text"), "morpheme.text")
        model_end = model_cursor + len(model_surface)
        if (
            projection.model_text[model_cursor:model_end] != model_surface
            or model_cursor not in projection.boundary_to_source
            or model_end not in projection.boundary_to_source
        ):
            raise KWJAAnalyzerError(f"第 {index + 1} 个形态素无法可靠对齐原句")
        start = projection.boundary_to_source[model_cursor]
        end = projection.boundary_to_source[model_end]
        morphemes.append(
            {
                "text": text[start:end],
                "reading": _string(item.get("reading"), "morpheme.reading"),
                "lemma": _string(item.get("lemma"), "morpheme.lemma"),
                "pos": _string(item.get("pos"), "morpheme.pos"),
                "subpos": _string(item.get("subpos"), "morpheme.subpos"),
                "start": start,
                "end": end,
            }
        )
        model_cursor = model_end
    if model_cursor != len(projection.model_text):
        raise KWJAAnalyzerError("KWJA 形态素未完整覆盖原句")

    raw_phrases = raw.get("phrases")
    if not isinstance(raw_phrases, list) or (text and not raw_phrases):
        raise KWJAAnalyzerError("KWJA 未返回有效文节")
    phrases: list[dict[str, Any]] = []
    flattened_indexes: list[int] = []
    previous_end = 0
    for phrase_index, item in enumerate(raw_phrases):
        if not isinstance(item, dict):
            raise KWJAAnalyzerError("KWJA 文节格式无效")
        indexes = item.get("morphemeIndexes")
        if (
            not isinstance(indexes, list)
            or not indexes
            or any(not isinstance(value, int) for value in indexes)
            or indexes != list(range(indexes[0], indexes[-1] + 1))
            or indexes[0] != previous_end
            or indexes[-1] >= len(morphemes)
        ):
            raise KWJAAnalyzerError(f"第 {phrase_index + 1} 个文节边界无效")
        start = morphemes[indexes[0]]["start"]
        end = morphemes[indexes[-1]]["end"]
        phrases.append(
            {
                "start": start,
                "end": end,
                "text": text[start:end],
                "morphemeIndexes": indexes,
            }
        )
        flattened_indexes.extend(indexes)
        previous_end = indexes[-1] + 1
    if flattened_indexes != list(range(len(morphemes))):
        raise KWJAAnalyzerError("KWJA 文节未连续覆盖全部形态素")
    if "".join(item["text"] for item in phrases) != text:
        raise KWJAAnalyzerError("KWJA 文节无法无损还原原句")

    analyzer_version = _string(raw.get("analyzerVersion"), "analyzerVersion")
    if not analyzer_version:
        raise KWJAAnalyzerError("KWJA 服务未报告模型版本")
    return {
        "analyzer": ANALYZER_NAME,
        "modelSize": MODEL_SIZE,
        "analyzerVersion": analyzer_version,
        "schemaVersion": ANALYSIS_SCHEMA_VERSION,
        "inputSha256": input_sha256(text),
        "analysisMode": ANALYSIS_MODE,
        "morphemes": morphemes,
        "phrases": phrases,
        "furigana": [],
        "chunks": [],
        "practiceStructure": [],
    }


class KWJAAnalyzerClient:
    def __init__(
        self,
        base_url: str | None = None,
        *,
        timeout_seconds: float | None = None,
    ) -> None:
        self.base_url = (base_url or os.environ.get("ANALYZER_URL") or DEFAULT_ANALYZER_URL).rstrip("/")
        self.timeout_seconds = float(
            timeout_seconds
            if timeout_seconds is not None
            else os.environ.get("ANALYZER_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS)
        )

    def _json_request(
        self,
        path: str,
        *,
        method: str = "GET",
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        data = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=data,
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                body = response.read()
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise KWJAUnavailableError("KWJA 解析服务暂时不可用，请稍后重试") from exc
        try:
            result = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise KWJAAnalyzerError("KWJA 服务返回了无效响应") from exc
        if not isinstance(result, dict):
            raise KWJAAnalyzerError("KWJA 服务返回格式无效")
        if result.get("ok") is False:
            message = result.get("error")
            raise KWJAAnalyzerError(
                message if isinstance(message, str) and message else "KWJA 分析失败"
            )
        return result

    def health(self) -> dict[str, Any]:
        return self._json_request("/health")

    def warmup(self) -> dict[str, Any]:
        return self._json_request("/warmup", method="POST", payload={})

    def analyze(self, text: str) -> dict[str, Any]:
        if not isinstance(text, str):
            raise TypeError("日语原句必须是字符串")
        if not text:
            return {
                "analyzer": ANALYZER_NAME,
                "modelSize": MODEL_SIZE,
                "analyzerVersion": "empty",
                "schemaVersion": ANALYSIS_SCHEMA_VERSION,
                "inputSha256": input_sha256(text),
                "analysisMode": ANALYSIS_MODE,
                "morphemes": [],
                "phrases": [],
                "furigana": [],
                "chunks": [],
                "practiceStructure": [],
            }
        raw = self._json_request("/analyze", method="POST", payload={"text": text})
        return align_service_result(text, raw)


def analyze_with_kwja(text: str) -> dict[str, Any]:
    return KWJAAnalyzerClient().analyze(text)
