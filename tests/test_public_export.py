import csv
import hashlib
import json
import platform
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from export_public import FIELDS, export_calls
from release_check import PERSONAL_PATH, allowed, check


def test_export_omits_quotes_and_keeps_unresolved_source_references(tmp_path):
    source, links, dest = [tmp_path / f for f in ["source.csv", "links.csv", "public.csv"]]
    fields = ["date", "company", "direction", "doc_id", "quote", "is_call", "ticker", "proposed_ticker", "title", "extraction_version", "start_s"]
    with source.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields); w.writeheader()
        for company, quote in [("A", "word " * 100), ("B", "short excerpt"), ("C", "")]:
            w.writerow(dict(date="2023-01-01", company=company, direction="bullish", doc_id="doc", quote=quote,
                            is_call="True", ticker="", proposed_ticker="OLD", title="Episode",
                            extraction_version="cache-only fixture", start_s="123"))
    links.write_text("date,episode_title,youtube_title,youtube_url\n2023-01-01,Episode,Episode,https://example.org\n")
    assert export_calls(source, dest, links) == 3
    rows = list(csv.DictReader(dest.open()))
    assert list(rows[0]) == FIELDS
    assert all(r["quote"] == "" for r in rows)
    assert [r["quote_truncated"] for r in rows] == ["true", "true", "false"]
    assert all(r["ticker"] == "" and r["proposed_ticker"] == "OLD" for r in rows)
    assert all(r["extraction_version"] == "cache-only fixture" for r in rows)
    assert all(r["youtube_url"] == "https://example.org" and r["start_s"] == "123" for r in rows)


@pytest.mark.parametrize("missing", ["link", "provenance"])
def test_export_stops_before_writing_incomplete_provenance(tmp_path, missing):
    source, links, dest = [tmp_path / f for f in ["source.csv", "links.csv", "public.csv"]]
    record = dict(date="2023-01-01", company="A", direction="bullish", doc_id="doc",
                  quote="short excerpt", is_call="True", title="Episode", extraction_version="cache-only fixture")
    if missing == "provenance":
        record["extraction_version"] = ""
    with source.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(record)); writer.writeheader(); writer.writerow(record)
    title = "Another episode" if missing == "link" else "Episode"
    links.write_text(f"date,episode_title,youtube_title,youtube_url\n2023-01-01,{title},{title},https://example.org\n")
    with pytest.raises(ValueError, match="Missing"):
        export_calls(source, dest, links)
    assert not dest.exists()


@pytest.mark.parametrize("name", ["data/yahoo_adj_close.csv", "odd_lots_transcripts.parquet", "private/raw.json", "episode.txt", ".env", "scripts/make_index_folder.py"])
def test_forbidden_assets_are_not_allowed(name):
    assert not allowed(Path(name))


def test_scanner_rejects_raw_files(tmp_path):
    (tmp_path / "transcript.txt").write_text("private fixture")
    with pytest.raises(ValueError, match="Not on public allowlist"):
        check(tmp_path)


def test_personal_paths_include_windows_and_escaped_notebook_strings():
    for sep in ["/", "\\", "\\\\"]:
        path = "C:" + sep + "Users" + sep + "fixture" + sep + "data"
        assert PERSONAL_PATH.search(path)
    assert PERSONAL_PATH.search("/" + "Users" + "/fixture/data")


def test_backtest_notebook_keeps_generated_market_outputs_private(tmp_path):
    notebook_path = Path(__file__).resolve().parents[1] / "notebooks" / "odd_lots_backtest.ipynb"
    notebook = json.loads(notebook_path.read_text())
    export_cell, = ["".join(cell["source"]) for cell in notebook["cells"]
                   if cell["cell_type"] == "code" and "= export_results(" in "".join(cell["source"])]
    public_data = tmp_path / "data"
    public_data.mkdir()
    (public_data / "trade_recommendations.csv").write_text("fixture signals\n")
    private = tmp_path / "private"
    (private / "data").mkdir(parents=True)
    for name in ["yahoo_adj_close.csv", "DGS3MO.csv", "yahoo_meta.csv"]:
        (private / "data" / name).write_text("fixture input\n")
    for name in ["scripts/backtest_utils.py", "notebooks/odd_lots_backtest.ipynb"]:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture code\n")

    def export_fixture(calls, prices, yields, destination):
        assert destination == private / "baskets"
        destination.mkdir(parents=True)
        for name in ["portfolio_returns.csv", "portfolio_summary.csv", "tbill_3m.csv",
                     "leave_one_out.csv", "call_ledger.csv", "missing_prices.csv", "holdings.csv"]:
            (destination / name).write_text("fixture market result\n")
        (destination / "run_metadata.json").write_text("{}\n")
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame({"variant": ["A"], "vs_spy": [0.0]})

    before = {p.name: p.read_bytes() for p in public_data.iterdir()}
    namespace = dict(ROOT=tmp_path, PRIVATE=private, ALLOW_NETWORK=False,
                     export_results=export_fixture, calls=None, P=None, treasury_yields=None,
                     hashlib=hashlib, json=json, platform=platform, version=lambda _: "fixture",
                     display=lambda _: None)
    exec(compile(export_cell, str(notebook_path), "exec"), namespace)
    assert {p.name: p.read_bytes() for p in public_data.iterdir()} == before
    assert (private / "baskets" / "portfolio_returns.csv").exists()
    provenance = json.loads((private / "baskets" / "run_metadata.json").read_text())
    assert provenance["call_input_sha256"] == hashlib.sha256(before["trade_recommendations.csv"]).hexdigest()
