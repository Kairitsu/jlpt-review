#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def table_counts(conn: sqlite3.Connection) -> dict[str, int]:
    names = [
        row[0]
        for row in conn.execute(
            """SELECT name FROM sqlite_master
               WHERE type='table' AND name NOT LIKE 'sqlite_%'
               ORDER BY name"""
        )
    ]
    return {
        name: conn.execute(
            f'SELECT COUNT(*) FROM "{name.replace(chr(34), chr(34) * 2)}"'
        ).fetchone()[0]
        for name in names
    }


def command_output(args: list[str], *, cwd: Path) -> str:
    try:
        return subprocess.run(
            args,
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def docker_image_id(container_name: str, *, cwd: Path) -> str:
    args = [
        "docker",
        "inspect",
        "--format",
        "{{.Image}}",
        container_name,
    ]
    value = command_output(args, cwd=cwd)
    if value:
        return value
    return command_output(["sudo", "-n", *args], cwd=cwd)


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(
        description="Create and restore-verify a KWJA production migration backup."
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=root / "data" / "japanese_sentence_review.sqlite3",
    )
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--container-name", default="japanese-sentence-review-app")
    parser.add_argument(
        "--require-maintenance-flag",
        action=argparse.BooleanOptionalAction,
        default=True,
    )
    args = parser.parse_args()
    database = args.database.resolve()
    data_dir = database.parent
    maintenance_flag = data_dir / "maintenance.flag"
    if args.require_maintenance_flag and not maintenance_flag.exists():
        raise SystemExit(
            f"refusing backup: maintenance flag is missing: {maintenance_flag}"
        )
    if not database.is_file():
        raise SystemExit(f"database does not exist: {database}")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    output_root = (
        args.output_root.resolve()
        if args.output_root
        else data_dir / "backups"
    )
    target = output_root / f"kwja-migration-{stamp}"
    target.mkdir(parents=True, exist_ok=False)
    target.chmod(0o700)

    online = target / "database-online-backup.sqlite3"
    source = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    counts_before = table_counts(source)
    integrity_before = source.execute("PRAGMA integrity_check").fetchone()[0]
    with sqlite3.connect(online) as destination:
        source.backup(destination)
    source.close()

    raw_files = []
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(f"{database}{suffix}")
        if candidate.exists():
            raw_target = target / f"database-raw{suffix or '.sqlite3'}"
            shutil.copy2(candidate, raw_target)
            raw_files.append(raw_target)

    logical_dump = target / "database-logical.sql"
    schema_dump = target / "schema.sql"
    with sqlite3.connect(online) as backup:
        logical_dump.write_text(
            "\n".join(backup.iterdump()) + "\n",
            encoding="utf-8",
        )
        schema_rows = backup.execute(
            """SELECT sql FROM sqlite_master
               WHERE sql IS NOT NULL ORDER BY type,name"""
        )
        schema_dump.write_text(
            "\n\n".join(row[0] + ";" for row in schema_rows) + "\n",
            encoding="utf-8",
        )

    restored = target / "restore-verification.sqlite3"
    shutil.copy2(online, restored)
    with sqlite3.connect(restored) as verification:
        integrity_restored = verification.execute(
            "PRAGMA integrity_check"
        ).fetchone()[0]
        foreign_keys = [
            list(row) for row in verification.execute("PRAGMA foreign_key_check")
        ]
        counts_restored = table_counts(verification)
    restore_ok = (
        integrity_before == "ok"
        and integrity_restored == "ok"
        and not foreign_keys
        and counts_before == counts_restored
    )

    git_commit = command_output(["git", "rev-parse", "HEAD"], cwd=root)
    image_id = docker_image_id(args.container_name, cwd=root)
    committed_compose = command_output(
        ["git", "show", "HEAD:docker-compose.yml"], cwd=root
    )
    compose_backup = target / "docker-compose-before-migration.yml"
    compose_backup.write_text(committed_compose + "\n", encoding="utf-8")
    environment_backup = target / "app.env.before-migration"
    environment_source = root / "secrets" / "app.env"
    if environment_source.is_file():
        shutil.copy2(environment_source, environment_backup)
        environment_backup.chmod(0o600)
    else:
        environment_backup.write_text("", encoding="utf-8")
        environment_backup.chmod(0o600)
    source_archive = target / "source-before-migration.tar.gz"
    subprocess.run(
        [
            "git",
            "archive",
            "--format=tar.gz",
            f"--output={source_archive}",
            "HEAD",
        ],
        cwd=root,
        check=True,
        timeout=60,
    )
    restore_script = target / "restore_database.py"
    restore_script.write_text(
        """#!/usr/bin/env python3
import argparse
import shutil
import sqlite3
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--target", type=Path, required=True)
args = parser.parse_args()
source = Path(__file__).with_name("database-online-backup.sqlite3")
target = args.target.resolve()
for suffix in ("-wal", "-shm"):
    Path(f"{target}{suffix}").unlink(missing_ok=True)
shutil.copy2(source, target)
with sqlite3.connect(target) as db:
    integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
    foreign_keys = db.execute("PRAGMA foreign_key_check").fetchall()
if integrity != "ok" or foreign_keys:
    raise SystemExit(
        f"restored database validation failed: integrity={integrity}, "
        f"foreign_keys={len(foreign_keys)}"
    )
print(f"restored and verified: {target}")
""",
        encoding="utf-8",
    )
    restore_script.chmod(0o700)
    counts_path = target / "table-row-counts.json"
    counts_path.write_text(
        json.dumps(counts_before, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    recovery = target / "RESTORE.md"
    recovery.write_text(
        "\n".join(
            (
                "# KWJA migration recovery",
                "",
                "1. Stop the new app and analyzer containers.",
                f"2. Run `python3 restore_database.py --target '{database}'`.",
                "3. Restore `docker-compose-before-migration.yml` and `app.env.before-migration` to the project.",
                f"4. Restore Git commit `{git_commit or '<recorded commit unavailable>'}` from `source-before-migration.tar.gz`.",
                f"5. Tag and start the previous image `{image_id or '<recorded image unavailable>'}`.",
                "6. Run `PRAGMA integrity_check`, `PRAGMA foreign_key_check`, compare row counts, then open an old report.",
                "",
                "Example while containers are stopped:",
                "",
                "```bash",
                f"python3 '{restore_script}' --target '{database}'",
                f"cp '{compose_backup}' '{root / 'docker-compose.yml'}'",
                f"install -m 600 '{environment_backup}' '{environment_source}'",
                f"sudo docker tag '{image_id or '<old-image-id>'}' jlpt-review-app:latest",
                f"sudo docker compose -f '{root / 'docker-compose.yml'}' up -d",
                "```",
                "",
            )
        ),
        encoding="utf-8",
    )

    files = [
        online,
        *raw_files,
        logical_dump,
        schema_dump,
        restored,
        counts_path,
        recovery,
        compose_backup,
        environment_backup,
        source_archive,
        restore_script,
    ]
    manifest = {
        "createdAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "database": str(database),
        "gitCommit": git_commit,
        "dockerImageId": image_id,
        "integrityBefore": integrity_before,
        "restoreVerification": {
            "ok": restore_ok,
            "integrity": integrity_restored,
            "foreignKeyErrors": foreign_keys,
            "rowCountsMatch": counts_before == counts_restored,
        },
        "files": {
            path.name: {
                "size": path.stat().st_size,
                "sha256": sha256(path),
            }
            for path in files
        },
    }
    manifest_path = target / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (target / "SHA256SUMS").write_text(
        "\n".join(
            f"{sha256(path)}  {path.name}"
            for path in [*files, manifest_path]
        )
        + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"backupDirectory": str(target), **manifest}, ensure_ascii=False, indent=2))
    return 0 if restore_ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
