"""Small counterexamples for timing, gaps, renewals, and portfolio arithmetic."""
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from backtest_utils import active, annual_return, build_index, cache_retry_tickers, clean_ticker, expiry_dates, prepare_calls, read_price_inputs, run_baskets


class BasketTests(unittest.TestCase):
    def test_fixed_shares_and_rebalance(self):
        days = pd.bdate_range("2024-01-01", periods=5)
        prices = pd.DataFrame({"X": [10, 11, 12, 12, 13], "Y": [20, 20, 18, 18, 18]}, index=days)
        calls = pd.DataFrame({"ticker": ["X", "Y", "X"], "entry_date": [days[0], days[0], days[3]]})
        daily, held = build_index(calls, prices, None, max_weight=1.0)
        np.testing.assert_allclose(daily.level, [100, 105, 105, 105, 109.375])
        np.testing.assert_allclose(held.groupby("date").weight.sum(), 1)

    def test_missing_price_is_not_a_cash_exit(self):
        days = pd.bdate_range("2024-01-01", periods=3)
        prices = pd.DataFrame({"X": [10, np.nan, 12]}, index=days)
        calls = pd.DataFrame({"ticker": ["X"], "entry_date": days[0]})
        with self.assertRaisesRegex(ValueError, "Unresolved held-stock"):
            build_index(calls, prices, None)
        calls.entry_date = days[1]
        with self.assertRaisesRegex(ValueError, "Unresolved active-stock"):
            build_index(calls, prices, None)

    def test_rolloff_renewal_cash_and_cash_turnover(self):
        days = pd.bdate_range("2024-01-01", "2024-08-30")
        prices = pd.DataFrame({"X": 10.0, "BILL": np.linspace(1, 1.1, len(days))}, index=days)
        calls = pd.DataFrame({"ticker": ["X", "X"], "entry_date": pd.to_datetime(["2024-01-02", "2024-03-15"])})
        daily, held = build_index(calls.iloc[:1], prices, 3, max_weight=1.0)
        self.assertEqual(held.loc[held.symbol.eq("X"), "date"].tolist(), [pd.Timestamp("2024-01-02")])
        self.assertAlmostEqual(daily.loc["2024-04-02", "turnover"], 1.0)
        expected = 100 * prices.BILL.iloc[-1] / prices.BILL.loc["2024-04-02"]
        self.assertAlmostEqual(daily.level.iloc[-1], expected)
        _, renewed = build_index(calls, prices, 3)
        self.assertEqual(renewed.loc[renewed.symbol.eq("X"), "date"].tolist(), list(calls.entry_date))
        self.assertNotIn(pd.Timestamp("2024-04-02"), set(renewed.date))  # superseded expiry is not a trade
        self.assertEqual(renewed.date.max(), pd.Timestamp("2024-06-17"))  # Saturday expiry
        self.assertEqual(active(calls, pd.Timestamp("2024-06-15"), 3), [])

    def test_expiry_holiday_moves_forward(self):
        days = pd.bdate_range("2024-08-01", "2024-11-29").difference(pd.DatetimeIndex(["2024-11-28"]))
        calls = pd.DataFrame({"ticker": ["X"], "entry_date": [pd.Timestamp("2024-08-28")]})
        self.assertEqual(expiry_dates(calls, days, 3), {pd.Timestamp("2024-11-29")})
        prices = pd.DataFrame({"X": 10.0}, index=days)
        daily, held = build_index(calls, prices, 3)
        self.assertEqual(daily.index[0], pd.Timestamp("2024-08-28"))
        self.assertEqual(held.date.max(), pd.Timestamp("2024-11-29"))
        self.assertEqual(daily.n_names.iloc[-1], 0)

    def test_cache_retries_all_nan(self):
        prices = pd.DataFrame({"OK": [1, 2], "EMPTY": [np.nan, np.nan]})
        self.assertEqual(cache_retry_tickers(prices, ["OK", "EMPTY", "MISSING"]), ["EMPTY", "MISSING"])

    def test_next_session_and_rejected_duplicate(self):
        days = pd.bdate_range("2024-01-25", periods=5)
        prices = pd.DataFrame({"X": 10.0}, index=days)
        raw = pd.DataFrame({"date": ["2024-01-25"] * 3, "doc_id": ["doc"] * 3,
                            "company": ["Example"] * 3, "direction": ["bullish", "bearish", "bullish"],
                            "ticker": ["X", "X", np.nan], "n_models": [2] * 3, "avg_confidence": [3, 10, 8],
                            "quote_score": [70, 100, 100], "is_call": [True, True, True]})
        metadata = pd.DataFrame({"ticker": ["X"], "exchange": ["NYQ"], "type": ["EQUITY"], "currency": ["USD"]}).set_index("ticker")
        ledger = prepare_calls(raw, prices, metadata)
        self.assertEqual(ledger.entry_date.iloc[0], pd.Timestamp("2024-01-26"))
        self.assertTrue(ledger.admitted.iloc[0])  # judge-only: no hidden confidence/quote threshold
        self.assertFalse(ledger.admitted.iloc[1])
        self.assertTrue(ledger.unresolved_eligible.iloc[2])
        for field, value in [("type", "ETF"), ("currency", "EUR"), ("exchange", "OQB")]:
            bad_metadata = metadata.copy()
            bad_metadata.loc["X", field] = value
            bad = prepare_calls(raw, prices, bad_metadata)
            self.assertFalse(bad.admitted.iloc[0])
            self.assertTrue(bad.unresolved_eligible.iloc[0])  # current metadata cannot prove historical exclusion
        with self.assertRaisesRegex(ValueError, "is_call"):
            prepare_calls(raw.drop(columns="is_call"), prices, metadata)

    def test_buy_at_t1_close_without_earning_pre_entry_move(self):
        days = pd.bdate_range("2024-01-01", periods=5)
        prices = pd.DataFrame({"X": [10, 20, 30, 40, 50]}, index=days)
        calls = pd.DataFrame({"ticker": ["X"], "entry_date": days[2]})
        daily, _ = build_index(calls, prices, None, max_weight=1.0)
        self.assertEqual(daily.index[0], days[2])
        np.testing.assert_allclose(daily.level, [100, 400/3, 500/3])
        empty, _ = build_index(calls.iloc[:0], prices, None)
        self.assertTrue(empty.empty)

    def test_allocation_limit_cash_and_allowed_weight_drift(self):
        days = pd.bdate_range("2024-01-01", periods=4)
        prices = pd.DataFrame({"X": [10, 20, 30, 30], "BILL": [1, 1, 1, 1]}, index=days)
        calls = pd.DataFrame({"ticker": ["X"], "entry_date": [days[0]]})
        daily, held = build_index(calls, prices, None)
        np.testing.assert_allclose(daily.level, [100, 110, 120, 120])
        self.assertAlmostEqual(held.loc[held.symbol.eq("X"), "weight"].iloc[0], .10)
        self.assertAlmostEqual(held.loc[held.symbol.eq("CASH"), "weight"].iloc[0], .90)
        self.assertAlmostEqual(daily.max_weight.iloc[-1], .25)
        self.assertEqual(daily.rebalanced.sum(), 1)  # no daily cap reset
        calls = pd.concat([calls, pd.DataFrame({"ticker": ["X"], "entry_date": [days[-1]]})], ignore_index=True)
        renewed, held = build_index(calls, prices, None)
        self.assertAlmostEqual(renewed.max_weight.iloc[-1], .10)
        self.assertAlmostEqual(renewed.cash.iloc[-1], .90)
        self.assertAlmostEqual(renewed.turnover.iloc[-1], .15)

    def test_two_and_twenty_names_equal_weights(self):
        days = pd.bdate_range("2024-01-01", periods=2)
        for count, weight, cash in [(2, .1, .8), (20, .05, 0)]:
            names = [f"X{i}" for i in range(count)]
            prices = pd.DataFrame(10.0, index=days, columns=names)
            calls = pd.DataFrame({"ticker": names, "entry_date": days[0]})
            daily, held = build_index(calls, prices, None)
            np.testing.assert_allclose(held.loc[held.symbol.ne("CASH"), "weight"], weight)
            self.assertAlmostEqual(daily.cash.iloc[0], cash)
            self.assertAlmostEqual(held.weight.sum(), 1)

    def test_new_call_rebalances_without_buying_past_returns(self):
        days = pd.bdate_range("2024-01-01", periods=4)
        prices = pd.DataFrame({"X": [10, 20, 20, 20], "Y": [1, 2, 100, 200]}, index=days)
        calls = pd.DataFrame({"ticker": ["X", "Y"], "entry_date": [days[0], days[2]]})
        daily, held = build_index(calls, prices, None)
        np.testing.assert_allclose(daily.level, [100, 110, 110, 121])
        weights = held.loc[held.date.eq(days[2])].set_index("symbol").weight
        np.testing.assert_allclose(weights.loc[["X", "Y", "CASH"]], [.1, .1, .8])

    def test_no_undated_cross_listing_alias(self):
        self.assertEqual(clean_ticker("CRH.IR"), ("CRH.IR", "foreign"))

    def test_common_start_and_leave_one_out_cash(self):
        days = pd.bdate_range("2024-01-01", "2024-08-30")
        prices = pd.DataFrame({"X": np.linspace(1, 2, len(days)), "Y": np.linspace(1, 1.5, len(days)),
                              "SPY": np.linspace(1, 1.2, len(days)), "RSP": np.linspace(1, 1.1, len(days)),
                              "BILL": np.linspace(1, 1.04, len(days))}, index=days)
        calls = pd.DataFrame({"ticker": ["X", "Y"], "entry_date": pd.to_datetime(["2024-01-02", "2024-03-15"]), "admitted": True})
        returns, _, _, sensitivity = run_baskets(calls, prices)
        level_cols = [c for c in returns if not c.endswith("_ret")]
        np.testing.assert_allclose(returns[level_cols].iloc[0], 100)
        common = returns.index[0]
        reduced = calls.loc[calls.ticker.ne("X")]
        loo, _ = build_index(reduced, prices, None, start_date=common)
        self.assertEqual(loo.index[0], common)
        self.assertAlmostEqual(loo.level.loc["2024-03-14"], 100 * prices.BILL.loc["2024-03-14"] / prices.BILL.loc[common])
        actual = sensitivity.loc[sensitivity.variant.eq("A_ew_forever") & sensitivity.excluded_ticker.eq("X"), "ann_return"].iloc[0]
        self.assertAlmostEqual(actual, annual_return(loo.level))

    def test_cash_uses_prior_yield_and_preserves_stock_gaps(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            days = pd.to_datetime(["2024-01-05", "2024-01-08", "2024-01-09"])
            pd.DataFrame({"SPY": [100, 101, 102], "RSP": [100, 100, 101], "X": [10, np.nan, 12]}, index=days).to_csv(folder / "yahoo_adj_close.csv")
            pd.DataFrame({"ticker": ["X"], "exchange": ["NYQ"], "type": ["EQUITY"], "currency": ["USD"]}).to_csv(folder / "yahoo_meta.csv", index=False)
            pd.DataFrame({"DGS3MO": [10.0, 20.0, 30.0]}, index=days).to_csv(folder / "DGS3MO.csv")
            levels, _, _, _ = read_price_inputs(folder, ["X", "SPY", "RSP"])
            self.assertTrue(pd.isna(levels.X.iloc[1]))
            self.assertAlmostEqual(levels.BILL.iloc[1], 1 + .10 * 3 / 365)
            self.assertAlmostEqual(levels.BILL.iloc[2] / levels.BILL.iloc[1], 1 + .20 / 365)
            broken = pd.read_csv(folder / "yahoo_adj_close.csv", index_col=0)
            broken.loc["2024-01-08", "SPY"] = np.nan
            broken.to_csv(folder / "yahoo_adj_close.csv")
            with self.assertRaisesRegex(ValueError, "SPY is missing"):
                read_price_inputs(folder, ["X", "SPY", "RSP"])
            broken.loc["2024-01-08", "SPY"] = 101
            broken.loc["2024-01-09", "SPY"] = np.nan
            broken.to_csv(folder / "yahoo_adj_close.csv")
            with self.assertRaisesRegex(ValueError, "SPY is missing"):
                read_price_inputs(folder, ["X", "SPY", "RSP"])

    def test_calendar_annualization(self):
        levels = pd.Series([100, 121], index=pd.to_datetime(["2020-01-01", "2022-01-01"]))
        self.assertAlmostEqual(annual_return(levels), 1.21 ** (365.25 / 731) - 1)


if __name__ == "__main__":
    unittest.main()
