"""Export a small, explicit public call ledger from a local extraction CSV.

Never copy transcript data, raw responses, price caches, or verbatim quotes.
Keep source links and timestamps so readers can locate the original episode.
"""
from __future__ import annotations

import argparse
import csv
from pathlib import Path

FIELDS = [
    "date", "date_recorded", "company", "direction", "ticker", "proposed_ticker",
    "quote", "quote_truncated", "n_models", "avg_confidence", "quote_score",
    "ticker_votes", "ticker_llm1", "ticker_llm2", "ticker_llm3", "is_call",
    "ticker_status", "ticker_name", "models", "direction_consensus", "start_s",
    "title", "doc_id", "run_date", "prompt_hash", "judge_model",
    "pipeline_hash", "code_hash", "security_master_scope", "security_master_hash",
    "ticker_src", "ticker_lookup_hash", "listing_status_at_episode",
    "ticker_override_source", "ticker_override_effective_date",
    "extraction_version", "youtube_url",
]


def export_calls(source: Path, destination: Path, links: Path) -> int:
    with links.open(newline="") as handle:
        link_rows = list(csv.DictReader(handle))
    by_title = {(r["date"], r["episode_title"]): r["youtube_url"] for r in link_rows}
    by_video_title = {(r["date"], r["youtube_title"]): r["youtube_url"] for r in link_rows}
    with source.open(newline="") as handle:
        source_rows = list(csv.DictReader(handle))
    rows = []
    for original in sorted(source_rows, key=lambda r: (r["date"], r["company"], r["direction"])):
        if original.get("is_call", "").lower() not in {"true", "1"}:
            raise ValueError("Input must contain all judge-approved calls, including unresolved tickers")
        row = {key: original.get(key, "") for key in FIELDS}
        episode = row["doc_id"]
        if not episode:
            raise ValueError("Missing document ID")
        if not row["extraction_version"].strip():
            raise ValueError("Missing extraction_version; rerun the extraction notebook to record model provenance")
        row["quote_truncated"] = str(bool(row["quote"])).lower()
        row["quote"] = ""
        key = (row["date"], row["title"])
        row["youtube_url"] = row["youtube_url"] or by_title.get(key, "") or by_video_title.get(key, "")
        if not row["youtube_url"]:
            raise ValueError(f"Missing episode source link: {row['date']} / {row['title']}")
        rows.append(row)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Private final extraction CSV, including unresolved calls")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    count = export_calls(args.source, args.root / "data/trade_recommendations.csv",
                         args.root / "data/odd_lots_youtube_links_2023_2026.csv")
    print(f"Exported {count} calls without transcript quotes; run the release check next.")
