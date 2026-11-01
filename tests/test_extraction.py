"""Regression checks for silent loss, symbol collisions, and offline request safety."""

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from extraction_utils import canonical_ticker, company_matches, dated_ticker_override, request_hash, require_stage_success, validate_groups


class ExtractionValidationTests(unittest.TestCase):
    def test_grouping_rejects_missing_duplicate_and_out_of_range_mentions(self):
        for groups in [
            [{"company": "A", "items": [1]}],
            [{"company": "A", "items": [1, 1, 2]}],
            [{"company": "A", "items": [1, 3]}],
            [{"company": "A", "items": [True, 2]}],
        ]:
            with self.assertRaises(ValueError):
                validate_groups(groups, 2)
        validate_groups([{"company": "B", "items": [2]}, {"company": "A", "items": [1]}], 2)

    def test_security_symbols_keep_share_class_and_listing_identity(self):
        self.assertEqual(canonical_ticker("brk.b"), "BRK-B")
        self.assertNotEqual(canonical_ticker("BRK.A"), canonical_ticker("BRK.B"))
        self.assertNotEqual(canonical_ticker("BHP"), canonical_ticker("BHP.AX"))
        self.assertEqual(canonical_ticker("7203.T"), "7203.T")

    def test_ticker_existence_does_not_prove_company_identity(self):
        self.assertTrue(company_matches("Quanta Services", "QUANTA SERVICES, INC."))
        self.assertTrue(company_matches("CRH", "CRH PUBLIC LIMITED CO"))
        self.assertTrue(company_matches("Costco", "COSTCO WHOLESALE CORP /NEW"))
        self.assertTrue(company_matches("Royal Caribbean Group", "ROYAL CARIBBEAN CRUISES LTD"))
        self.assertTrue(company_matches("Seagate", "Seagate Technology Holdings plc"))
        self.assertFalse(company_matches("Nasdaq index", "Nasdaq, Inc."))
        self.assertFalse(company_matches("Oracle", "NVIDIA CORP"))
        self.assertFalse(company_matches("", "INC"))

    def test_common_names_legal_suffixes_and_sec_state_markers(self):
        for candidate, registered in [
            ("Ford", "FORD MOTOR CO"), ("Meta", "Meta Platforms, Inc."),
            ("Amazon", "AMAZON.COM, INC."), ("MPLX", "MPLX LP"),
            ("MPLX", "MPLX L.P."), ("Wells Fargo", "WELLS FARGO & COMPANY/MN"),
        ]:
            self.assertTrue(company_matches(candidate, registered), (candidate, registered))
        self.assertFalse(company_matches("Oracle", "NVIDIA CORP", legal_name="NVIDIA Corporation"))
        self.assertFalse(company_matches("Blackstone", "Blackstone Mortgage Trust Inc.",
                                         legal_name="Blackstone Mortgage Trust"))

    def test_stage_errors_cannot_be_exported_as_rejections(self):
        require_stage_success("judge", [])
        for stage in ["extraction", "matcher", "judge"]:
            with self.assertRaisesRegex(RuntimeError, stage + " incomplete"):
                require_stage_success(stage, ["fixture failure"])

    def test_notebook_matcher_and_judge_errors_stop_before_approved_output(self):
        nb = json.loads((ROOT / "notebooks" / "odd_lots_stock_recs.ipynb").read_text())
        failures = lambda fn, jobs: [(job, None, "fixture failure") for job in jobs]
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory)
            ns = dict(pd=pd, OUT=out, require_stage_success=require_stage_success,
                      MODELS=[{}, {}], run_parallel=failures, validate_groups=validate_groups,
                      long=pd.DataFrame([dict(doc_id="doc", llm="llm1", company="Example", ticker="EX"),
                                         dict(doc_id="doc", llm="llm2", company="Example", ticker="EX")]))
            with self.assertRaisesRegex(RuntimeError, "matcher incomplete"):
                exec(compile("".join(nb["cells"][13]["source"]), "matcher_cell", "exec"), ns)
            self.assertNotIn("clean", ns)
            self.assertEqual(pd.read_csv(out / "match_runs.csv").status.tolist(), ["error"])

            clean = pd.DataFrame([dict(doc_id="doc", company="Example", direction="bullish", title="Episode",
                                       date="2024-01-01", items=[("short quote", 0)])])
            ns = dict(pd=pd, OUT=out, PRICE={}, clean=clean, CLEAN_COLS=list(clean.columns),
                      require_stage_success=require_stage_success, run_parallel=failures,
                      CHUNKS={"doc": [(0, "", "short quote", "short quote")]}, lines=lambda chunks: "short quote")
            with self.assertRaisesRegex(RuntimeError, "judge incomplete"):
                exec(compile("".join(nb["cells"][15]["source"]), "judge_cell", "exec"), ns)
            self.assertNotIn("approved", ns)
            self.assertEqual(pd.read_csv(out / "judge_runs.csv").judge_error.tolist(), ["fixture failure"])

    def test_crh_override_requires_issuer_and_effective_date(self):
        self.assertIsNone(dated_ticker_override("CRH", "2023-09-24", "CRH.IR"))
        self.assertIsNone(dated_ticker_override("Another issuer", "2024-04-05", "CRH.IR"))
        self.assertIsNone(dated_ticker_override("CRH", "2024-04-05", "BHP.AX"))
        mapping = dated_ticker_override("CRH plc", "2023-09-25", "CRH.IR")
        self.assertEqual(mapping["to"], "CRH")
        self.assertEqual(mapping["effective_date"], "2023-09-25")
        self.assertTrue(mapping["source"].startswith("https://www.sec.gov/"))

    def test_request_fingerprint_includes_inference_parameters(self):
        body = {"model": "test", "temperature": 0, "max_tokens": 100, "provider": {"sort": "price"}}
        for field, value in [("temperature", 0.5), ("max_tokens", 200), ("provider", {"sort": "latency"})]:
            self.assertNotEqual(request_hash(body), request_hash({**body, field: value}))
        self.assertEqual(request_hash(body), request_hash(dict(reversed(list(body.items())))))

    def test_offline_miss_never_calls_network_and_cached_failure_is_not_no_call(self):
        import datetime as dt
        import hashlib
        import random
        import time
        nb = json.loads((ROOT / "notebooks" / "odd_lots_stock_recs.ipynb").read_text())
        source = "".join(nb["cells"][9]["source"]).split("\ndef run_parallel", 1)[0]
        network = Mock()
        with tempfile.TemporaryDirectory() as directory:
            ns = dict(CACHE=Path(directory), json=json, hashlib=hashlib, dt=dt, random=random, time=time,
                      request_hash=request_hash, ALLOW_NETWORK=False, RETRY_ERRORS=False,
                      API_KEY="test-key-must-never-be-used", requests=network)
            exec(compile(source, "extraction_cell_10", "exec"), ns)
            model = {"id": "test/model", "reasoning": {"enabled": False}, "temperature": 0}
            with self.assertRaisesRegex(RuntimeError, "Offline cache miss"):
                ns["call_llm"](model, "system", "user", {}, "extract")
            path = ns["cache_path"](ns["build_body"](model, "system", "user", {}), "extract")
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({"status": "error", "error": "timeout"}))
            with self.assertRaisesRegex(RuntimeError, "Cached failure"):
                ns["call_llm"](model, "system", "user", {}, "extract")
            path.write_text(json.dumps({"status": "ok", "data": {"recommendations": []}, "cost": 0}))
            self.assertEqual(ns["call_llm"](model, "system", "user", {}, "extract")["data"], {"recommendations": []})
            self.assertEqual(network.mock_calls, [])


if __name__ == "__main__":
    unittest.main()
