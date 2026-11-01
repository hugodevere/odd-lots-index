"""Build PRIVATE transcript Parquet and an explicit coverage manifest.

No network calls. Apple ingestion requires both --apple-db and --apple-ttml.
The published links CSV is the episode universe; incomplete linked coverage is
an error unless --allow-incomplete is explicitly requested.
"""
import argparse
import csv
import hashlib
import json
import math
import os
import re
import shutil
import sqlite3
import tempfile
import xml.etree.ElementTree as ET
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent.parent


def private_dir():
    """Resolve blank/relative environment settings consistently from the project."""
    path = Path(os.environ.get("ODD_LOTS_PRIVATE_DIR") or "private").expanduser()
    return (path if path.is_absolute() else ROOT / path).resolve()


PRIVATE = private_dir()
APPLE_EPOCH = 978307200
CHUNK_SECONDS = 60.0
# Independently verified source defects; never generalize re-zeroing to all videos.
OFFSET_VIDEO = "paN2aYVoUkw"
OFFSET_SECONDS = 1706.669
TRUNCATED_APPLE_ID = "1000780414506"


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def norm_title(title):
    title = title.casefold().replace("’", "'")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", title)).strip()


def episode_id(day, title):
    return f"{day}_{sha(norm_title(title))[:10]}"


def video_id(url):
    parsed = urlparse(url)
    value = parsed.path.lstrip("/") if parsed.netloc in {"youtu.be", "www.youtu.be"} else parse_qs(parsed.query).get("v", [""])[0]
    if not re.fullmatch(r"[\w-]{11}", value):
        raise ValueError("invalid YouTube URL in episode index")
    return value


def seconds(value):
    result = 0.0
    for part in str(value).split(":"):
        result = result * 60 + float(part)
    return result


def clean_text(text):
    return re.sub(r"\s+", " ", re.sub(r"\[music\]|>>", " ", text, flags=re.I)).strip()


def read_actor(path):
    raw = json.loads(path.read_text(encoding="utf-8"))
    fetched_at = None
    if isinstance(raw, dict) and "items" in raw:
        fetched_at = datetime.fromisoformat(raw["fetched_at"]) if raw.get("fetched_at") else None
        raw = raw["items"]
    if not isinstance(raw, list) or len(raw) != 1 or not isinstance(raw[0], dict):
        raise ValueError("expected exactly one actor result")
    item = raw[0]
    if item.get("success") is not True:
        raise ValueError("actor result is not success=true")
    if not item.get("transcript_segments"):
        raise ValueError("actor result has no transcript segments")
    return item, fetched_at


def youtube_chunks(item):
    segments = item["transcript_segments"]
    first_start = float(segments[0].get("start", 0))
    offset = 0.0
    if item.get("video_id") == OFFSET_VIDEO and first_start != 0:
        if not math.isclose(first_start, OFFSET_SECONDS, rel_tol=0, abs_tol=0.01):
            raise ValueError("unexpected Goldman opening timestamp; review source before applying an offset")
        offset = first_start
    chunks, current, start, end, previous = [], [], None, 0.0, -1.0
    for segment in segments:
        at, duration = float(segment.get("start", 0)) - offset, float(segment.get("dur", 0))
        if not (math.isfinite(at) and math.isfinite(duration) and at >= 0 and duration >= 0 and at >= previous):
            raise ValueError("invalid or unordered transcript timings")
        previous = at
        if start is None:
            start = at
        if at - start >= CHUNK_SECONDS and current:
            chunks.append((start, end, None, " ".join(current)))
            current, start = [], at
        text = clean_text(segment.get("text", ""))
        if text:
            current.append(text)
        end = max(end, at + duration)
    if current:
        chunks.append((start, end, None, " ".join(current)))
    if not chunks:
        raise ValueError("no transcript words after removing caption tags")
    return chunks, offset


def youtube_rows(raw_dir, index, manifest):
    seen = set()
    for path in sorted(raw_dir.glob("*.json")):
        try:
            item, fetched_at = read_actor(path)
            vid = item.get("video_id")
            if vid not in index:
                raise ValueError("video is absent from the episode index")
            if vid in seen:
                raise ValueError("duplicate video result")
            chunks, offset = youtube_chunks(item)
            seen.add(vid)
            manifest.append({"source_file": path.name, "video_id": vid, "status": "included", "detail": "timestamp_rezeroed" if offset else ""})
            yield index[vid], "youtube_auto_captions", vid, None, path, fetched_at, chunks, offset, False
        except (OSError, ValueError, TypeError, KeyError, IndexError) as error:
            manifest.append({"source_file": path.name, "video_id": "", "status": "skipped_error", "detail": str(error) if isinstance(error, ValueError) and not isinstance(error, json.JSONDecodeError) else type(error).__name__})
    for vid in sorted(set(index) - seen):
        manifest.append({"source_file": "", "video_id": vid, "status": "missing_linked_video", "detail": index[vid]["date"]})


def apple_rows(db_path, ttml_dir, index_by_title, manifest):
    if db_path is None:
        return
    cached = {}
    for path in sorted(ttml_dir.rglob("*.ttml")):
        match = re.search(r"transcript_(\d+)\.ttml", path.name)
        if match:
            ident = match.group(1)
            if ident in cached:
                manifest.append({"source_file": path.name, "video_id": "", "status": "skipped_error", "detail": "duplicate Apple transcript ID"})
                continue
            cached[ident] = path
        else:
            manifest.append({"source_file": path.name, "video_id": "", "status": "apple_unrecognized_cache_name", "detail": "not a transcript_<id>.ttml file"})
    seen = set()
    with sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True) as connection:
        query = """select e.ZTRANSCRIPTIDENTIFIER, e.ZTITLE, e.ZPUBDATE from ZMTEPISODE e
                   join ZMTPODCAST p on e.ZPODCAST = p.Z_PK where p.ZTITLE like '%Odd Lots%'"""
        for ident, title, published in connection.execute(query):
            match = re.search(r"transcript_(\d+)\.ttml", ident or "")
            if not match:
                manifest.append({"source_file": "", "video_id": "", "status": "apple_no_usable_identifier", "detail": title or ""})
                continue
            apple_id = match.group(1)
            seen.add(apple_id)
            path = cached.get(apple_id)
            source_file = path.name if path else f"transcript_{apple_id}.ttml"
            try:
                day = datetime.fromtimestamp(published + APPLE_EPOCH, timezone.utc).date().isoformat()
                episode = index_by_title.get((day, norm_title(title)))
            except (TypeError, ValueError, OverflowError, AttributeError) as error:
                manifest.append({"source_file": source_file, "video_id": "", "status": "skipped_error", "detail": "invalid Apple episode metadata: " + type(error).__name__})
                continue
            if episode is None:
                manifest.append({"source_file": source_file, "video_id": "", "status": "apple_outside_episode_index", "detail": day})
                continue  # restrict Apple to the declared episode universe
            if path is None:
                manifest.append({"source_file": source_file, "video_id": "", "status": "apple_transcript_not_cached", "detail": day})
                continue  # optional Apple cache; linked YouTube coverage is checked separately
            try:
                chunks = []
                for paragraph in ET.parse(path).getroot().iter("{http://www.w3.org/ns/ttml}p"):
                    words = [node.text for node in paragraph.iter() if node.get("{http://podcasts.apple.com/transcript-ttml-internal}unit") == "word" and node.text]
                    text = clean_text(" ".join(words))
                    if not text:
                        continue
                    speaker = paragraph.get("{http://www.w3.org/ns/ttml#metadata}agent")
                    begin, end = seconds(paragraph.get("begin", 0)), seconds(paragraph.get("end", 0))
                    if chunks and chunks[-1][2] == speaker:
                        chunks[-1] = (chunks[-1][0], end, speaker, chunks[-1][3] + " " + text)
                    else:
                        chunks.append((begin, end, speaker, text))
                if not chunks:
                    raise ValueError("empty Apple transcript")
            except (OSError, ValueError, TypeError, ET.ParseError) as error:
                manifest.append({"source_file": source_file, "video_id": "", "status": "skipped_error", "detail": type(error).__name__})
                continue
            truncated = apple_id == TRUNCATED_APPLE_ID
            manifest.append({"source_file": path.name, "video_id": "", "status": "included_truncated" if truncated else "included_apple", "detail": day})
            yield episode, "apple_podcasts_ttml", None, apple_id, path, None, chunks, 0.0, truncated
    for apple_id in sorted(set(cached) - seen):
        manifest.append({"source_file": cached[apple_id].name, "video_id": "", "status": "apple_cache_without_episode", "detail": "no matching Odd Lots database row"})


def build(args):
    import pyarrow as pa
    import pyarrow.dataset as ds
    import tiktoken

    with args.links.open(encoding="utf-8", newline="") as stream:
        links = list(csv.DictReader(stream))
    by_video = {}
    by_title = {}
    manifest = []
    for row in links:
        key = (row["date"], norm_title(row["episode_title"]))
        if key in by_title:
            raise ValueError("duplicate episode in links CSV")
        by_title[key] = row
        if row["youtube_url"]:
            vid = video_id(row["youtube_url"])
            if vid in by_video:
                raise ValueError("YouTube video assigned to multiple episodes")
            by_video[vid] = row
        else:
            manifest.append({"source_file": "", "video_id": "", "status": "no_video_in_index", "detail": row["date"]})
    encoder = tiktoken.get_encoding("o200k_base")
    docs, chunks = [], []
    sources = list(youtube_rows(args.raw_dir, by_video, manifest))
    sources.extend(apple_rows(args.apple_db, args.apple_ttml, by_title, manifest))
    for episode, source, vid, apple_id, path, fetched_at, parts, offset, truncated in sources:
        eid = episode_id(episode["date"], episode["episode_title"])
        doc_id = f"{eid}_{'yt' if source.startswith('youtube') else 'apple'}"
        text = "\n\n".join(part[3] for part in parts)
        day = date.fromisoformat(episode["date"])
        docs.append({"doc_id": doc_id, "episode_id": eid, "show": "Odd Lots", "source": source,
                     "date": day, "year": day.year, "title": episode["episode_title"],
                     "youtube_url": episode.get("youtube_url") or None, "video_id": vid,
                     "apple_transcript_id": apple_id, "duration_s": round(parts[-1][1], 2),
                     "n_chunks": len(parts), "has_speakers": source == "apple_podcasts_ttml",
                     "word_count": len(text.split()), "token_count_o200k": len(encoder.encode(text, disallowed_special=())),
                     "content_hash": sha(text), "fetched_at": fetched_at,
                     "source_file_modified_at": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc),
                     "timestamp_offset_s": offset, "is_truncated": truncated, "text": text})
        for index, (begin, end, speaker, text) in enumerate(parts):
            chunks.append({"doc_id": doc_id, "episode_id": eid, "source": source, "date": day,
                           "year": day.year, "chunk_idx": index, "start_s": round(begin, 2),
                           "end_s": round(end, 2), "speaker": speaker, "word_count": len(text.split()),
                           "token_count_o200k": len(encoder.encode(text, disallowed_special=())), "text": text})
    args.out_dir.mkdir(parents=True, exist_ok=True)
    with (args.out_dir / "build_manifest.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["source_file", "video_id", "status", "detail"])
        writer.writeheader()
        writer.writerows(manifest)
    problems = sum(row["status"] in {"skipped_error", "missing_linked_video"} for row in manifest)
    print(f"Coverage: {len(by_video)} linked videos, {sum(row['status'] == 'included' for row in manifest)} included; {problems} problems. See build_manifest.csv.")
    if problems and not args.allow_incomplete:
        raise ValueError("incomplete or invalid input; existing datasets left unchanged (inspect manifest, or use --allow-incomplete)")
    if not docs:
        raise ValueError("no usable transcripts; existing datasets left unchanged")
    docs.sort(key=lambda row: (row["date"], row["doc_id"]))
    chunks.sort(key=lambda row: (row["date"], row["doc_id"], row["chunk_idx"]))
    fmt = ds.ParquetFileFormat().make_write_options(compression="zstd", compression_level=9)
    part = ds.partitioning(pa.schema([("year", pa.int16()), ("source", pa.string())]), flavor="hive")
    staging = Path(tempfile.mkdtemp(prefix=".build-", dir=args.out_dir))
    try:
        for name, rows in (("docs", docs), ("chunks", chunks)):
            table = pa.Table.from_pylist(rows)
            # Arrow inference otherwise changes an all-null speaker/fetch-time column's type.
            types = {"year": pa.int16(), "fetched_at": pa.timestamp("us", "UTC"), "speaker": pa.string(), "apple_transcript_id": pa.string()}
            for column, kind in types.items():
                if column in table.column_names:
                    table = table.set_column(table.schema.get_field_index(column), column, table[column].cast(kind))
            ds.write_dataset(table, staging / name, format="parquet", partitioning=part, file_options=fmt)
        for name in ("docs", "chunks"):
            target = args.out_dir / name
            if target.exists():
                shutil.rmtree(target)
            (staging / name).replace(target)
        print(f"Wrote {len(docs)} documents and {len(chunks)} chunks to the private data directory.")
    finally:
        shutil.rmtree(staging)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--links", type=Path, default=ROOT / "data/odd_lots_youtube_links_2023_2026.csv")
    parser.add_argument("--raw-dir", type=Path, default=PRIVATE / "youtube/raw_json")
    parser.add_argument("--out-dir", type=Path, default=PRIVATE / "parquet")
    parser.add_argument("--apple-db", type=Path, help="optional MTLibrary.sqlite, opened read-only")
    parser.add_argument("--apple-ttml", type=Path, help="optional directory containing cached TTML files")
    parser.add_argument("--allow-incomplete", action="store_true", help="explicitly allow recorded input failures")
    args = parser.parse_args()
    if bool(args.apple_db) != bool(args.apple_ttml):
        parser.error("--apple-db and --apple-ttml must be supplied together")
    build(args)


if __name__ == "__main__":
    main()
