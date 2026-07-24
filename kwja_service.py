from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, request

from kwja_analyzer import (
    ANALYSIS_MODE,
    ANALYSIS_TASKS,
    ANALYZER_NAME,
    MODEL_SIZE,
    input_sha256,
)


LOGGER = logging.getLogger(__name__)
CACHE_DIR = Path(os.environ.get("KWJA_ANALYSIS_CACHE_DIR", "/cache/kwja-analysis"))
KWJA_TIMEOUT_SECONDS = float(os.environ.get("KWJA_INFERENCE_TIMEOUT_SECONDS", "180"))
WARMUP_TEXT = "昨日、銀行に行った。"
KWJA_OPTIONS = [
    "--model-size",
    MODEL_SIZE,
    "--device",
    "cpu",
    "--tasks",
    ANALYSIS_MODE,
    "--char-batch-size",
    "1",
    "--seq2seq-batch-size",
    "1",
    "--word-batch-size",
    "1",
]


class Engine:
    """One serialized rhoknp client backed by one long-lived KWJA process."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._kwja = None
        self._version = ""
        self._warmed = False
        self._loaded_at: float | None = None

    @property
    def version(self) -> str:
        self._ensure_loaded()
        return self._version

    @property
    def warmed(self) -> bool:
        return self._warmed

    def _ensure_loaded(self) -> None:
        if self._kwja is not None:
            return
        with self._lock:
            if self._kwja is not None:
                return
            import kwja as kwja_package
            from rhoknp import KWJA

            self._kwja = KWJA(
                executable=os.environ.get("KWJA_EXECUTABLE", "kwja"),
                options=KWJA_OPTIONS,
                skip_sanity_check=True,
            )
            package_version = getattr(kwja_package, "__version__", "unknown")
            self._version = (
                f"kwja-{package_version}|model={MODEL_SIZE}|tasks={ANALYSIS_MODE}"
            )
            self._loaded_at = time.monotonic()

    def analyze(self, text: str) -> dict[str, Any]:
        with self._lock:
            self._ensure_loaded()
            cache_path = self._cache_path(text)
            cached = self._load_cache(cache_path, text)
            if cached is not None:
                return cached

            started = time.perf_counter()
            document = self._kwja.apply(text, timeout=KWJA_TIMEOUT_SECONDS)
            result = self._serialize(text, document)
            result["processingMs"] = round((time.perf_counter() - started) * 1000, 2)
            result["cacheHit"] = False
            self._write_cache(cache_path, result)
            return result

    def warmup(self) -> dict[str, Any]:
        result = self.analyze(WARMUP_TEXT)
        self._warmed = True
        return {
            "ok": True,
            "ready": True,
            "analyzerVersion": self.version,
            "processingMs": result.get("processingMs"),
            "cacheHit": result.get("cacheHit", False),
        }

    def _cache_path(self, text: str) -> Path:
        material = f"{input_sha256(text)}\0{self.version}\0{ANALYSIS_MODE}".encode("utf-8")
        key = hashlib.sha256(material).hexdigest()
        return CACHE_DIR / key[:2] / f"{key}.json"

    def _load_cache(self, path: Path, text: str) -> dict[str, Any] | None:
        try:
            with path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return None
        if (
            isinstance(data, dict)
            and data.get("inputSha256") == input_sha256(text)
            and data.get("analyzerVersion") == self.version
            and data.get("analysisMode") == ANALYSIS_MODE
        ):
            data["cacheHit"] = True
            return data
        return None

    @staticmethod
    def _write_cache(path: Path, result: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
        )
        temporary = Path(handle.name)
        try:
            with handle:
                json.dump(result, handle, ensure_ascii=False, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def _serialize(self, text: str, document) -> dict[str, Any]:
        morphemes: list[dict[str, str]] = []
        phrases: list[dict[str, Any]] = []
        model_parts: list[str] = []
        global_index = 0

        for sentence in document.sentences:
            sentence_morphemes = list(sentence.morphemes)
            local_to_global = {
                id(morpheme): global_index + index
                for index, morpheme in enumerate(sentence_morphemes)
            }
            for morpheme in sentence_morphemes:
                surface = str(morpheme.text)
                model_parts.append(surface)
                morphemes.append(
                    {
                        "text": surface,
                        "reading": str(morpheme.reading or ""),
                        "lemma": str(morpheme.lemma or ""),
                        "pos": str(morpheme.pos or ""),
                        "subpos": str(morpheme.subpos or ""),
                    }
                )
            for phrase in sentence.phrases:
                indexes = [
                    local_to_global[id(morpheme)]
                    for morpheme in phrase.morphemes
                ]
                phrases.append({"morphemeIndexes": indexes})
            global_index += len(sentence_morphemes)

        model_text = "".join(model_parts)
        return {
            "ok": True,
            "analyzer": ANALYZER_NAME,
            "modelSize": MODEL_SIZE,
            "analyzerVersion": self.version,
            "analysisMode": ANALYSIS_MODE,
            "inputSha256": input_sha256(text),
            "modelText": model_text,
            "morphemes": morphemes,
            "phrases": phrases,
        }


engine = Engine()
app = Flask(__name__)


@app.get("/health")
def health():
    return jsonify(
        {
            "ok": True,
            "ready": engine.warmed,
            "analyzer": ANALYZER_NAME,
            "modelSize": MODEL_SIZE,
            "analysisMode": ANALYSIS_MODE,
            "singleConsumer": True,
        }
    )


@app.post("/warmup")
def warmup():
    try:
        return jsonify(engine.warmup())
    except Exception as exc:
        LOGGER.exception("KWJA warmup failed")
        return jsonify({"ok": False, "error": str(exc)}), 503


@app.post("/analyze")
def analyze():
    payload = request.get_json(silent=True)
    text = payload.get("text") if isinstance(payload, dict) else None
    if not isinstance(text, str) or not text.strip():
        return jsonify({"ok": False, "error": "text 必须是非空字符串"}), 400
    if len(text) > 10_000:
        return jsonify({"ok": False, "error": "原句过长"}), 413
    try:
        return jsonify(engine.analyze(text))
    except TimeoutError:
        LOGGER.exception("KWJA analysis timed out")
        return jsonify({"ok": False, "error": "KWJA 分析超时，请稍后重试"}), 504
    except Exception as exc:
        LOGGER.exception("KWJA analysis failed")
        return jsonify({"ok": False, "error": f"KWJA 分析失败：{exc}"}), 422
