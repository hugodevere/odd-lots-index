"""No network or copyrighted fixtures: synthetic timing/failure regressions."""
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
from build_parquet import APPLE_EPOCH, apple_rows, clean_text, norm_title, read_actor, youtube_chunks, youtube_rows
from apify_transcripts import fetch, usage


def actor(video="abcdefghijk", success=True, start=0):
    return {"video_id": video, "success": success,
            "transcript_segments": [{"start": start, "dur": 5, "text": "[Music] >> Synthetic test words"}]}


def test_known_offset_only():
    chunks, offset = youtube_chunks(actor("paN2aYVoUkw", start=1706.67))
    assert offset == 1706.67
    assert chunks == [(0.0, 5.0, None, "Synthetic test words")]
    chunks, offset = youtube_chunks(actor(start=1706.67))
    assert offset == 0
    assert chunks[0][0] == 1706.67


@pytest.mark.parametrize("start", [0.1, 100, 1600, 1800])
def test_unknown_goldman_timestamp_requires_review(start):
    with pytest.raises(ValueError, match="unexpected Goldman opening timestamp"):
        youtube_chunks(actor("paN2aYVoUkw", start=start))
    chunks, offset = youtube_chunks(actor("paN2aYVoUkw", start=0))
    assert offset == 0
    assert chunks[0][0] == 0


def test_failure_and_missing_coverage_are_explicit(tmp_path):
    (tmp_path / "failed.json").write_text(json.dumps([actor(success=False)]))
    manifest = []
    rows = list(youtube_rows(tmp_path, {"abcdefghijk": {"date": "2026-01-01"}}, manifest))
    assert rows == []
    assert [row["status"] for row in manifest] == ["skipped_error", "missing_linked_video"]


def test_fetch_timestamp_is_not_file_mtime(tmp_path):
    path = tmp_path / "legacy.json"
    path.write_text(json.dumps([actor()]))
    assert read_actor(path)[1] is None
    path.write_text(json.dumps({"fetched_at": "2026-01-01T12:00:00+00:00", "items": [actor()]}))
    assert read_actor(path)[1].isoformat() == "2026-01-01T12:00:00+00:00"


def test_bad_order_rejected():
    item = actor(start=5)
    item["transcript_segments"].append({"start": 2, "dur": 1, "text": "more words"})
    with pytest.raises(ValueError, match="unordered"):
        youtube_chunks(item)


def test_cached_raw_fetch_rebuilds_text_without_network(tmp_path, monkeypatch):
    raw = tmp_path / "raw_json"
    raw.mkdir()
    (raw / "2026-01-01 abcdefghijk.json").write_text(json.dumps([actor()]))
    monkeypatch.setattr("apify_transcripts.call", lambda *args, **kwargs: pytest.fail("network call"))
    row = {"date": "2026-01-01", "episode_title": "Synthetic fixture", "youtube_url": "https://www.youtube.com/watch?v=abcdefghijk"}
    _, status, words = fetch(row, "synthetic-token", tmp_path)
    assert status == "cached"
    assert words > 0
    assert len(list(tmp_path.glob("*.txt"))) == 1


@pytest.mark.parametrize("value", [None, float("nan"), -1, True, "unknown"])
def test_unavailable_usage_stops_paid_fetch(value, tmp_path, monkeypatch):
    from apify_transcripts import main
    links = tmp_path / "links.csv"
    links.write_text("date,episode_title,youtube_url\n2026-01-01,Synthetic,https://www.youtube.com/watch?v=abcdefghijk\n")
    monkeypatch.setenv("APIFY_TOKEN", "synthetic-token")
    monkeypatch.setattr("apify_transcripts.call", lambda *args, **kwargs: {"data": {"current": {"monthlyUsageUsd": value}}})
    monkeypatch.setattr("apify_transcripts.fetch", lambda *args, **kwargs: pytest.fail("paid fetch must not start"))
    monkeypatch.setattr(sys, "argv", ["apify_transcripts.py", str(links), str(tmp_path / "out")])
    with pytest.raises(RuntimeError, match="usage is unavailable or invalid"):
        main()


def test_zero_usage_is_valid(monkeypatch):
    monkeypatch.setattr("apify_transcripts.call", lambda *args, **kwargs: {"data": {"current": {"monthlyUsageUsd": 0}}})
    assert usage("synthetic-token") == (0.0, None)


def test_apple_manifest_records_optional_and_invalid_inputs(tmp_path):
    db = tmp_path / "library.sqlite"
    published = datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp() - APPLE_EPOCH
    with sqlite3.connect(db) as connection:
        connection.execute("CREATE TABLE ZMTPODCAST (Z_PK INTEGER, ZTITLE TEXT)")
        connection.execute("CREATE TABLE ZMTEPISODE (ZTRANSCRIPTIDENTIFIER TEXT, ZTITLE TEXT, ZPUBDATE REAL, ZPODCAST INTEGER)")
        connection.execute("INSERT INTO ZMTPODCAST VALUES (1, 'Odd Lots')")
        connection.executemany("INSERT INTO ZMTEPISODE VALUES (?, ?, ?, 1)", [
            ("transcript_1.ttml", "Included", published),
            ("transcript_2.ttml", "Uncached", published),
            ("transcript_3.ttml", "Outside index", published),
            (None, "No transcript", published),
            ("transcript_4.ttml", "Malformed", published),
        ])
    cache = tmp_path / "ttml"
    cache.mkdir()
    text = '<tt xmlns="http://www.w3.org/ns/ttml" xmlns:a="http://podcasts.apple.com/transcript-ttml-internal"><p begin="0" end="1"><span a:unit="word">Synthetic</span></p></tt>'
    for ident in [1, 3, 5]:
        (cache / f"transcript_{ident}.ttml").write_text(text)
    (cache / "transcript_4.ttml").write_text("<malformed")
    (cache / "unknown.ttml").write_text(text)
    titles = ["Included", "Uncached", "Malformed"]
    index = {("2026-01-01", norm_title(title)): {"date": "2026-01-01", "episode_title": title} for title in titles}
    manifest = []
    rows = list(apple_rows(db, cache, index, manifest))
    assert len(rows) == 1
    assert rows[0][3] == "1"
    statuses = {entry["source_file"]: entry["status"] for entry in manifest}
    assert statuses == {
        "transcript_1.ttml": "included_apple",
        "transcript_2.ttml": "apple_transcript_not_cached",
        "transcript_3.ttml": "apple_outside_episode_index",
        "": "apple_no_usable_identifier",
        "transcript_4.ttml": "skipped_error",
        "transcript_5.ttml": "apple_cache_without_episode",
        "unknown.ttml": "apple_unrecognized_cache_name",
    }


@pytest.mark.parametrize("setting,relative", [("", "private"), ("local-data", "local-data")])
def test_private_directory_uses_project_root(setting, relative, tmp_path, monkeypatch):
    from build_parquet import ROOT, private_dir
    monkeypatch.setenv("ODD_LOTS_PRIVATE_DIR", setting)
    monkeypatch.chdir(tmp_path)
    assert private_dir() == ROOT / relative


def test_private_directory_default_and_absolute(tmp_path, monkeypatch):
    from build_parquet import ROOT, private_dir
    monkeypatch.delenv("ODD_LOTS_PRIVATE_DIR", raising=False)
    assert private_dir() == ROOT / "private"
    monkeypatch.setenv("ODD_LOTS_PRIVATE_DIR", str(tmp_path))
    assert private_dir() == tmp_path.resolve()
