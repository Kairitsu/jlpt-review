#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p backups
stamp=$(date +%Y%m%d-%H%M%S)
target="backups/japanese_sentence_review-${stamp}.sqlite3"
python3 - "$target" <<'PY'
import sqlite3
import sys

with sqlite3.connect("data/japanese_sentence_review.sqlite3") as source:
    with sqlite3.connect(sys.argv[1]) as target:
        source.backup(target)
PY
echo "$target"
