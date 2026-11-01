"""Check and optionally zip the explicit public file set. Never publishes online."""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import re
import zipfile
from datetime import date
from pathlib import Path

from export_public import FIELDS

ROOT_FILES = {"README.md", "LICENSE", "DATA_SOURCES.md", "requirements.txt", ".env.example", ".gitignore", "PUBLIC_MANIFEST.json"}
DATA_FILES = {
    "odd_lots_youtube_links_2023_2026.csv", "trade_recommendations.csv", "tbill_3m.csv",
}
SCRIPT_FILES = {
    "apify_transcripts.py", "build_parquet.py", "match_yt.py", "ttml_to_txt.py",
    "extraction_utils.py", "backtest_utils.py", "export_public.py", "release_check.py",
}
SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", ".ipynb_checkpoints", ".venv"}
PERSONAL_PATH = re.compile(
    r"(?:[A-Za-z]:)?[\\/]{1,2}(?:users|home)[\\/]{1,2}[A-Za-z0-9_.-]+"
    r"|[\\/]{1,2}(?:root|volumes)[\\/]{1,2}|~[\\/]{1,2}", re.IGNORECASE)
# Recognizable credentials, plus long hexadecimal values assigned to credential
# names. Bare hashes are allowed because they are part of the public provenance.
SECRET = re.compile(
    r"sk-(?:or-v1-|ant-api\d+-)?[A-Za-z0-9_-]{20,}"
    r"|apify_api_[A-Za-z0-9]{15,}|gh[pousr]_[A-Za-z0-9]{25,}"
    r"|github_pat_[A-Za-z0-9_]{30,}|(?:AKIA|ASIA)[A-Z0-9]{16}"
    r"|AIza[A-Za-z0-9_-]{30,}|xox[baprs]-[A-Za-z0-9-]{10,}"
    r"|eyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{10,}"
    r"|Bearer[ \t]+[A-Za-z0-9_.~+/=-]{20,}"
    r"|-----BEGIN (?:RSA |EC |OPENSSH |DSA |ENCRYPTED )?PRIVATE KEY-----"
    r"|(?:api[_-]?key|access[_-]?token|auth[_-]?token|secret|password)"
    r"[\"']?\s*[:=]\s*[\"']?[a-f0-9]{24,}\b", re.IGNORECASE)

CSV_COLUMNS = {
    "trade_recommendations.csv": set(FIELDS),
    "odd_lots_youtube_links_2023_2026.csv": {"date", "episode_title", "youtube_url", "youtube_title", "match"},
    "tbill_3m.csv": {"date", "yield_3m_pct", "cash_index", "cash_daily_ret"},
}
NUMERIC_COLUMNS = {"n_models", "avg_confidence", "quote_score", "ticker_votes", "start_s",
                   "yield_3m_pct", "cash_index", "cash_daily_ret"}
BOOL_COLUMNS = {"is_call", "quote_truncated"}
DATE_COLUMNS = {"date", "date_recorded", "ticker_override_effective_date"}


def check_csv(text: str, rel: Path) -> list[str]:
    """Validate known public columns and bounded cells; this is not a rights audit."""
    errors = []
    reader = csv.DictReader(io.StringIO(text))
    fields = reader.fieldnames or []
    expected = CSV_COLUMNS[rel.name]
    if len(fields) != len(set(fields)) or set(fields) != expected:
        return [f"Unexpected CSV schema: {rel}"]
    for number, row in enumerate(reader, 2):
        if None in row or any(value is None for value in row.values()):
            errors.append(f"Malformed CSV row: {rel} row {number}")
            continue
        for key, value in row.items():
            if key == "quote":
                if value.strip():
                    errors.append(f"Transcript quote excluded: {rel} row {number}")
            elif key in NUMERIC_COLUMNS and value:
                try:
                    if not math.isfinite(float(value)):
                        raise ValueError("nonfinite")
                except ValueError:
                    errors.append(f"Invalid numeric cell: {rel} row {number} column {key}")
            elif key in BOOL_COLUMNS and value.lower() not in {"", "true", "false", "0", "1"}:
                errors.append(f"Invalid boolean cell: {rel} row {number} column {key}")
            elif key in DATE_COLUMNS and value:
                try:
                    date.fromisoformat(value)
                except ValueError:
                    errors.append(f"Invalid date cell: {rel} row {number} column {key}")
            elif len(value) > 400 or len(value.split()) > 60:
                errors.append(f"Oversized public text cell: {rel} row {number} column {key}")
    return errors


def allowed(path: Path) -> bool:
    parts = path.parts
    if len(parts) == 1:
        return path.name in ROOT_FILES
    if len(parts) != 2:
        return False
    return (
        parts[0] == "data" and path.name in DATA_FILES
        or parts[0] == "scripts" and path.name in SCRIPT_FILES
        or parts[0] == "tests" and path.name in {"test_transcripts.py", "test_extraction.py", "test_backtest.py", "test_public_export.py", "test_release_safety.py"}
        or parts[0] == "notebooks" and path.name in {"odd_lots_stock_recs.ipynb", "odd_lots_backtest.ipynb"}
        or parts[0] == "notes" and path.name == "BASKET_PLAN.md"
    )


def check(root: Path) -> list[Path]:
    files, errors = [], []
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if path.is_symlink():
            errors.append(f"Symlink excluded: {rel}")
            continue
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        if not path.is_file():
            continue
        if not allowed(rel):
            errors.append(f"Not on public allowlist: {rel}")
            continue
        files.append(path)
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            errors.append(f"Non-text public file: {rel}")
            continue
        visible_text = text.replace('\\"', '"').replace("\\'", "'")
        if SECRET.search(visible_text):
            errors.append(f"Potential credential: {rel} (value suppressed)")
        # This exact Apify API route resembles a Unix home path. Limit the
        # exemption to its call site in the fetcher, not arbitrary public text.
        path_text = text
        if rel == Path("scripts/apify_transcripts.py"):
            path_text = path_text.replace('call("/' + 'users/me/limits",', 'call(API_USAGE_ENDPOINT,')
        if PERSONAL_PATH.search(path_text):
            errors.append(f"Personal absolute path: {rel}")
        if path.suffix == ".ipynb":
            nb = json.loads(text)
            for number, cell in enumerate(nb["cells"], 1):
                if cell.get("outputs") or cell.get("execution_count") is not None or cell.get("attachments"):
                    errors.append(f"Notebook output or attachment: {rel} cell {number}")
        if path.suffix == ".csv":
            errors.extend(check_csv(text, rel))
    required = {"README.md", "LICENSE", "DATA_SOURCES.md", ".env.example", "requirements.txt",
                "data/trade_recommendations.csv", "data/tbill_3m.csv",
                "data/odd_lots_youtube_links_2023_2026.csv"}
    present = {str(p.relative_to(root)) for p in files}
    errors.extend(f"Missing required file: {p}" for p in sorted(required - present))
    if errors:
        raise ValueError("\n".join(errors))
    return files


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--archive", type=Path, help="Optional new zip path outside the project")
    args = parser.parse_args()
    root = args.root.resolve()
    files = check(root)
    manifest = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in files if p.name != "PUBLIC_MANIFEST.json"}
    manifest_path = root / "PUBLIC_MANIFEST.json"
    manifest_path.write_text(json.dumps({"sha256": manifest}, indent=2) + "\n")
    files = check(root)
    if args.archive:
        archive = args.archive.resolve()
        if archive.is_relative_to(root) or archive.exists():
            raise ValueError("Choose a new archive path outside the project")
        with zipfile.ZipFile(archive, "x", zipfile.ZIP_DEFLATED) as handle:
            for path in files:
                handle.write(path, "odd-lots-index/" + str(path.relative_to(root)))
    print(f"PASS: {len(files)} allowlisted public files; schemas, quote exclusion and notebook clearing checked; "
          "no recognized credential or personal-path patterns detected. This is not a rights audit or an exhaustive secret scan.")


if __name__ == "__main__":
    main()
