"""Fetch Odd Lots YouTube transcripts through the Apify actor akash9078/youtube-transcript-extractor.

Usage: python3 scripts/apify_transcripts.py LINKS_CSV OUT_DIR [--year 2023] [--from-date 2025-07-28] [--limit 5]
       [--workers 4] [--budget 4.90]

Reads APIFY_TOKEN from the environment. This explicitly invoked command makes paid
API calls. Store OUT_DIR under private/. A valid cached raw result avoids another
API call; an existing text file alone does not establish success. Raw responses
include fetch timestamps; fetch_manifest.csv records every attempted episode.
The budget is a stop threshold checked before each chunk, not a hard billing cap.
"""
import argparse
import csv
import json
import math
import os
import re
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from datetime import datetime, timezone

from build_parquet import read_actor, video_id, youtube_chunks

ACTOR = "akash9078~youtube-transcript-extractor"
API = "https://api.apify.com/v2"


def token():
    value = os.environ.get("APIFY_TOKEN", "").strip()
    if not value:
        raise SystemExit("set APIFY_TOKEN in the environment before fetching")
    return value


def atomic_text(path, text):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def call(path, tok, body=None, timeout=300):
    req = urllib.request.Request(
        f"{API}{path}",
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"},
        method="POST" if body is not None else "GET",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def usage(tok):
    data = call("/users/me/limits", tok)["data"]
    value = data.get("current", {}).get("monthlyUsageUsd")
    try:
        used = float(value) if not isinstance(value, bool) else float("nan")
    except (TypeError, ValueError):
        used = float("nan")
    if not math.isfinite(used) or used < 0:
        raise RuntimeError("Apify monthly usage is unavailable or invalid; refusing another paid fetch batch")
    return used, data.get("limits", {}).get("maxMonthlyUsageUsd")


def paragraphs(item):
    chunks, _ = youtube_chunks(item)
    def clock(value):
        value = int(value)
        return f"{value // 3600:d}:{value % 3600 // 60:02d}:{value % 60:02d}"
    return "\n\n".join(f"[{clock(begin)}] {text}" for begin, _, _, text in chunks)


def write_txt(dest, row, item):
    text = paragraphs(item)
    atomic_text(dest, f"Odd Lots — {row['episode_title']}\nPublished {row['date']}\nSource {row['youtube_url']}\n\n{text}\n")
    return len(text.split())


def fetch(row, tok, out):
    safe = re.sub(r'[\\/:*?"<>|]', "-", row["episode_title"]).strip()
    dest = out / f"{row['date']} Odd Lots - {safe}.txt"
    vid = video_id(row["youtube_url"])
    raw_path = out / "raw_json" / f"{row['date']} {vid}.json"
    if raw_path.exists():
        try:
            item, _ = read_actor(raw_path)
            if item.get("video_id") != vid:
                raise ValueError("cached video mismatch")
            return row, "cached", write_txt(dest, row, item)
        except (OSError, ValueError, TypeError, KeyError, IndexError):
            pass  # an invalid raw result is retried, never mistaken for no content
    for attempt in range(30):
        try:
            items = call(f"/acts/{ACTOR}/run-sync-get-dataset-items", tok,
                         {"videoUrl": row["youtube_url"]})  # the live actor rejects the README's `language` field
            break
        except urllib.error.HTTPError as e:
            # free plan allows 5 concurrent runs; wait for a slot instead of failing
            if e.code != 402 or "concurrent-runs-limit" not in e.read().decode():
                raise
            time.sleep(10)
    else:
        raise RuntimeError("no free Apify run slot after 5 minutes")
    (out / "raw_json").mkdir(exist_ok=True)
    atomic_text(raw_path, json.dumps({"fetched_at": datetime.now(timezone.utc).isoformat(), "items": items}, indent=1))
    try:
        item, _ = read_actor(raw_path)
        if item.get("video_id") != vid:
            raise ValueError("returned video mismatch")
        return row, "ok", write_txt(dest, row, item)
    except (ValueError, TypeError, KeyError, IndexError):
        return row, "failed_actor", 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("links_csv")
    ap.add_argument("out_dir")
    ap.add_argument("--year")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--workers", type=int, default=5)
    ap.add_argument("--from-date", help="only episodes on or after this YYYY-MM-DD")
    ap.add_argument("--chunk", type=int, default=40)
    ap.add_argument("--budget", type=float, default=4.90, help="stop once Apify monthly usage (USD) reaches this")
    a = ap.parse_args()

    rows = [r for r in csv.DictReader(open(a.links_csv, encoding="utf-8")) if r["youtube_url"]]
    if a.year:
        rows = [r for r in rows if r["date"].startswith(a.year)]
    if a.from_date:
        rows = [r for r in rows if r["date"] >= a.from_date]
    if a.limit:
        rows = rows[: a.limit]
    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    if a.workers < 1 or a.chunk < 1 or not math.isfinite(a.budget) or a.budget <= 0 or (a.limit is not None and a.limit < 1):
        ap.error("workers, chunk, budget and limit must be positive")
    tok = token()

    print("usage before (USD used, monthly cap):", usage(tok))
    results = []
    manifest = []
    # work in chunks so spend is checked against --budget before each one
    for i in range(0, len(rows), a.chunk):
        used = usage(tok)[0]
        if used >= a.budget:
            print(f"STOP: usage ${used:.2f} reached budget ${a.budget:.2f}; {len(rows) - i} left unfetched")
            manifest.extend({"date": row["date"], "youtube_url": row["youtube_url"], "status": "not_attempted_budget", "words": 0} for row in rows[i:])
            break
        with ThreadPoolExecutor(a.workers) as pool:
            futs = {pool.submit(fetch, r, tok, out): r for r in rows[i:i + a.chunk]}
            for f in as_completed(futs):
                try:
                    row, status, words = f.result()
                except Exception as e:  # keep going; report failures at the end
                    row = futs[f]
                    print("FAILED:", row["date"], row["youtube_url"], type(e).__name__)
                    results.append(("failed", 0))
                    manifest.append({"date": row["date"], "youtube_url": row["youtube_url"], "status": "failed_" + type(e).__name__, "words": 0})
                    continue
                print(f"{status:7} {words:6} words  {row['date']} {row['episode_title'][:60]}", flush=True)
                results.append((status, words))
                manifest.append({"date": row["date"], "youtube_url": row["youtube_url"], "status": status, "words": words})
        with (out / "fetch_manifest.csv").open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=["date", "youtube_url", "status", "words"])
            writer.writeheader()
            writer.writerows(manifest)
    with (out / "fetch_manifest.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["date", "youtube_url", "status", "words"])
        writer.writeheader()
        writer.writerows(manifest)
    print({s: sum(1 for x in results if x[0] == s) for s in {x[0] for x in results}})
    print("usage after (USD used, monthly cap):", usage(tok))
    if any(row["status"].startswith(("failed", "not_attempted")) for row in manifest):
        raise SystemExit("incomplete fetch; inspect fetch_manifest.csv and rerun")


if __name__ == "__main__":
    main()
