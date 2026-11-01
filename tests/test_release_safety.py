"""Counterexamples for public packaging; credentials below are generated fixtures."""
import csv
import io
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from release_check import CSV_COLUMNS, PERSONAL_PATH, SECRET, allowed, check, check_csv


@pytest.mark.parametrize("prefix", ["/" + "users/", "/" + "HOME/", "c:/" + "uSeRs/", "~" + "/", "/" + "root/"])
def test_personal_paths_are_case_insensitive(prefix):
    assert PERSONAL_PATH.search(prefix + "fixture/data")


@pytest.mark.parametrize("value", [
    "github_" + "pat_" + "a" * 40,
    "AI" + "za" + "a" * 35,
    "xo" + "xb-" + "1234567890-abcdefghij",
    "ey" + "J" + "a" * 12 + ".ey" + "J" + "b" * 12 + "." + "c" * 30,
    "Bear" + "er " + "a" * 30,
    "-----BEGIN " + "RSA PRIVATE KEY-----",
    'api_' + 'key="' + "ab12" * 10 + '"',
])
def test_recognizable_credentials(value):
    assert SECRET.search(value)


def test_provenance_hash_and_environment_lookup_are_not_credentials():
    assert not SECRET.search('"sha256": "' + "a" * 64 + '"')
    assert not SECRET.search('api_key = os.environ["API_KEY"]')


def public_fixture(root):
    for name in ["README.md", "LICENSE", "DATA_SOURCES.md", ".env.example", "requirements.txt"]:
        (root / name).write_text("")
    (root / "data").mkdir()
    for name, fields in CSV_COLUMNS.items():
        with (root / "data" / name).open("w", newline="") as handle:
            csv.writer(handle).writerow(sorted(fields))


def test_git_metadata_is_ignored_and_not_packaged(tmp_path):
    public_fixture(tmp_path)
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("private repository metadata")
    files = check(tmp_path)
    assert files
    assert all(".git" not in path.relative_to(tmp_path).parts for path in files)


def test_git_metadata_does_not_hide_private_files_elsewhere(tmp_path):
    public_fixture(tmp_path)
    (tmp_path / ".git").mkdir()
    private = tmp_path / "notes" / "private"
    private.mkdir(parents=True)
    (private / "transcript.txt").write_text("private fixture")
    with pytest.raises(ValueError, match="Not on public allowlist"):
        check(tmp_path)


def test_git_symlink_is_rejected(tmp_path):
    (tmp_path / ".git").symlink_to(tmp_path / "missing")
    with pytest.raises(ValueError, match="Symlink excluded"):
        check(tmp_path)


def test_symlink_inside_skipped_directory_is_rejected(tmp_path):
    cache = tmp_path / "__pycache__"
    cache.mkdir()
    (cache / "fixture").symlink_to(tmp_path / "missing")
    with pytest.raises(ValueError, match="Symlink excluded"):
        check(tmp_path)


def csv_text(name, updates=None, extra=None):
    fields = sorted(CSV_COLUMNS[name])
    row = dict.fromkeys(fields, "")
    row.update(updates or {})
    if extra:
        fields += list(extra)
        row.update(extra)
    stream = io.StringIO()
    writer = csv.DictWriter(stream, fieldnames=fields)
    writer.writeheader()
    writer.writerow(row)
    return stream.getvalue()


def test_disguised_transcript_column_is_rejected():
    name = "trade_recommendations.csv"
    text = csv_text(name, extra={"episode_notes": "private text"})
    assert "Unexpected CSV schema" in check_csv(text, Path("data") / name)[0]


def test_numeric_column_cannot_hold_transcript_text():
    name = "tbill_3m.csv"
    errors = check_csv(csv_text(name, {"cash_index": "private text"}), Path("data") / name)
    assert any("Invalid numeric cell" in error for error in errors)


def test_known_string_column_cannot_hold_full_transcript():
    name = "trade_recommendations.csv"
    errors = check_csv(csv_text(name, {"title": "spoken word " * 100}), Path("data") / name)
    assert any("Oversized public text cell" in error for error in errors)


@pytest.mark.parametrize("quote", ["word", "one\ntwo", "one " * 21])
def test_all_transcript_quotes_are_excluded(quote):
    name = "trade_recommendations.csv"
    if "quote" in CSV_COLUMNS[name]:
        text = csv_text(name, {"doc_id": "episode", "quote": quote})
    else:
        text = csv_text(name, {"doc_id": "episode"}, extra={"quote": quote})
    errors = check_csv(text, Path("data") / name)
    assert any("Transcript quote excluded" in error or "Unexpected CSV schema" in error for error in errors)


@pytest.mark.parametrize("name", [
    "portfolio_returns.csv", "portfolio_summary.csv", "leave_one_out.csv", "call_ledger.csv",
    "missing_prices.csv", "run_metadata.json", "yahoo_adj_close.csv", "yahoo_meta.csv",
])
def test_price_data_and_generated_results_are_not_public(name):
    assert not allowed(Path("data") / name)


def test_license_and_data_sources_are_public():
    assert allowed(Path("LICENSE"))
    assert allowed(Path("DATA_SOURCES.md"))
    assert not allowed(Path("VALIDATION.md"))
    assert not allowed(Path("REVIEW.md"))


def test_error_does_not_echo_credentials(tmp_path):
    secret = "github_" + "pat_" + "z" * 40
    (tmp_path / "README.md").write_text(secret)
    with pytest.raises(ValueError) as error:
        check(tmp_path)
    assert "Potential credential" in str(error.value)
    assert secret not in str(error.value)
