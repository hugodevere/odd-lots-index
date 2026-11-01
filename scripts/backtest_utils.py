"""Deterministic basket arithmetic; no network access or transcript dependencies."""
from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np
import pandas as pd

VARIANTS = {"A_ew_forever": None, "B_ew_3m_rolloff": 3, "C_ew_12m_rolloff": 12}
BENCHMARKS = ["SPY", "RSP"]
MAX_STOCK_WEIGHT = 0.10
METHOD_VERSION = "t1-entry-expiry-capped-v2"


def clean_ticker(value):
    symbol = "" if pd.isna(value) else str(value).strip().upper().replace("$", "")
    symbol = re.sub(r"^[A-Z ]+:", "", symbol).strip()
    match = re.fullmatch(r"([A-Z0-9]+)[.\-/]([A-Z]{1,3})", symbol)
    if not symbol:
        return "", "unresolved"
    if symbol[0].isdigit() or (match and (len(match[2]) > 1 or match[2] in {"T", "L", "F", "V"})):
        return symbol, "foreign"
    return (f"{match[1]}-{match[2]}" if match else symbol), "us"


def cache_retry_tickers(prices, tickers):
    """A failed download is missing data, never evidence of delisting."""
    return sorted(t for t in tickers if t not in prices or prices[t].dropna().empty)


def read_price_inputs(cache_dir, tickers, allow_network=False, refresh=False):
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    price_file, yield_file = cache_dir / "yahoo_adj_close.csv", cache_dir / "DGS3MO.csv"
    meta_file = cache_dir / "yahoo_meta.csv"
    metadata = pd.read_csv(meta_file).set_index("ticker") if meta_file.exists() else pd.DataFrame()
    prices = pd.read_csv(price_file, index_col=0, parse_dates=True) if price_file.exists() else pd.DataFrame()
    retry = sorted(set(tickers)) if refresh else cache_retry_tickers(prices, tickers)
    if retry and allow_network:
        import yfinance as yf
        downloaded = yf.download(retry, start="2023-01-01", auto_adjust=False, actions=False,
                                 progress=False)["Adj Close"]
        if isinstance(downloaded, pd.Series):
            downloaded = downloaded.to_frame(retry[0])
        prices = downloaded.combine_first(prices)
        prices.to_csv(price_file)
    redo_meta = sorted(t for t in tickers if refresh or t in retry or t not in metadata.index
                       or metadata.loc[t].get("exchange") is None or pd.isna(metadata.loc[t].get("exchange")))
    if redo_meta and allow_network:
        import yfinance as yf
        rows = []
        for ticker in redo_meta:
            try:
                info = yf.Ticker(ticker).get_history_metadata() or {}
                rows.append({"ticker": ticker, "name": info.get("longName"), "exchange": info.get("exchangeName"),
                             "type": info.get("instrumentType"), "currency": info.get("currency"), "lookup_error": ""})
            except Exception as error:
                rows.append({"ticker": ticker, "lookup_error": type(error).__name__})
        updated = pd.DataFrame(rows).set_index("ticker")
        metadata = updated.combine_first(metadata)
        metadata.to_csv(meta_file)
    if (refresh or not yield_file.exists()) and allow_network:
        pd.read_csv("https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS3MO").to_csv(yield_file, index=False)
    if prices.empty or not yield_file.exists():
        raise FileNotFoundError("Provide private Yahoo and DGS3MO caches, or explicitly set ODD_LOTS_ALLOW_NETWORK=1.")
    prices = prices.apply(pd.to_numeric, errors="raise").sort_index()
    if not prices.index.is_unique or not prices.columns.is_unique:
        raise ValueError("Duplicate dates or symbols in price cache")
    missing_bench = cache_retry_tickers(prices, BENCHMARKS)
    if missing_bench:
        raise ValueError(f"Missing benchmark prices: {missing_bench}")
    observed = prices.loc[pd.Timestamp("2023-01-01"):]
    if (observed.SPY.isna() & observed.drop(columns="SPY").notna().any(axis=1)).any():
        raise ValueError("SPY is missing on an observed price session; do not silently shrink the trading calendar")
    cal = prices.SPY.dropna().index
    cal = cal[cal >= pd.Timestamp("2023-01-01")]
    prices = prices.reindex(cal)
    if len(cal) < 2 or prices[BENCHMARKS].isna().any().any():
        raise ValueError("Benchmark calendar is incomplete")
    if ((prices <= 0) & prices.notna()).any().any():
        raise ValueError("Prices must be strictly positive")
    # No fill: a price gap or truncated listing needs explicit investigation.
    levels = prices.div(prices.apply(lambda s: s.dropna().iloc[0] if s.notna().any() else np.nan))
    yields = pd.read_csv(yield_file, index_col=0, parse_dates=True, na_values=".").iloc[:, 0]
    yields = pd.to_numeric(yields, errors="raise").sort_index()
    if not yields.index.is_unique:
        raise ValueError("Duplicate Treasury observation dates")
    aligned = yields.reindex(yields.index.union(cal)).ffill().reindex(cal)
    prior = aligned.shift(1)
    if prior.iloc[1:].isna().any():
        raise ValueError("Treasury yields do not cover the backtest")
    days = cal.to_series().diff().dt.days.fillna(0)
    cash_return = prior.div(100).mul(days).div(365)
    cash_return.iloc[0] = 0.0
    levels["BILL"] = (1 + cash_return).cumprod()
    if metadata.empty:
        raise FileNotFoundError("Provide a private yahoo_meta.csv security-metadata cache, or enable network access explicitly.")
    if not metadata.index.is_unique:
        raise ValueError("Duplicate symbols in security metadata")
    return levels, yields, cache_retry_tickers(prices, tickers), metadata


def prepare_calls(raw, prices, metadata):
    calls = raw.copy().reset_index(drop=True)
    calls = calls.rename(columns={"date": "publish_date", "avg_confidence": "confidence"})
    required = {"publish_date", "doc_id", "company", "direction", "ticker", "n_models", "confidence", "quote_score", "is_call"}
    if missing := required - set(calls):
        raise ValueError(f"Missing call fields: {sorted(missing)}")
    calls["publish_date"] = pd.to_datetime(calls.publish_date, errors="raise")
    if calls.publish_date.isna().any():
        raise ValueError("Missing publication date")
    for column in ["n_models", "confidence", "quote_score"]:
        calls[column] = pd.to_numeric(calls[column], errors="raise")
    calls["ticker_llm"] = calls.ticker
    normalized = [clean_ticker(t) for t in calls.ticker]
    calls["ticker"], calls["ticker_kind"] = zip(*normalized) if normalized else ([], [])
    calls["call_id"] = calls.doc_id.astype(str) + "_" + calls.index.astype(str)
    cal = prices.index
    calls["entry_date"] = [cal[i] if i < len(cal) else pd.NaT for i in cal.searchsorted(calls.publish_date, side="right")]
    valid_judge = calls.is_call.astype(str).str.lower().isin(["true", "1"])
    calls["signal_eligible"] = calls.direction.eq("bullish") & valid_judge

    def price_status(row):
        if row.ticker_kind == "foreign":
            return "foreign listing outside US-only scope"
        if row.ticker_kind == "unresolved":
            return "unresolved security identity; historical review required"
        if row.ticker not in metadata.index:
            return "unresolved listing metadata; historical review required"
        security = metadata.loc[row.ticker]
        if any(pd.isna(security.get(k)) for k in ["exchange", "type", "currency"]):
            return "unresolved listing metadata; historical review required"
        if security["type"] != "EQUITY" or security["currency"] != "USD":
            return "current type/currency cannot establish historical eligibility; review required"
        if security["exchange"] not in {"NYQ", "NMS", "NGM", "NCM", "ASE", "PCX", "TEX"}:
            return "unresolved exchange eligibility; historical review required"
        if pd.isna(row.entry_date):
            return "publication after available price window"
        if row.ticker not in prices or prices[row.ticker].dropna().empty:
            return "missing historical prices; download failure or corporate action unresolved"
        if pd.isna(prices.at[row.entry_date, row.ticker]):
            return "missing entry price; listing dates or corporate action unresolved"
        return "priced at entry"

    calls["security_metadata_basis"] = "current Yahoo snapshot; not point-in-time membership"
    for field in ["exchange", "type", "currency"]:
        calls["yahoo_" + field] = calls.ticker.map(metadata[field]) if field in metadata else np.nan
    calls["price_status"] = calls.apply(price_status, axis=1)
    calls["admitted"] = calls.signal_eligible & calls.price_status.eq("priced at entry")
    calls["reject_reason"] = np.where(calls.signal_eligible, calls.price_status, "not a judge-approved bullish call")
    calls.loc[calls.admitted, "reject_reason"] = ""
    # Only eligible rows can supersede another eligible row.
    eligible = calls[calls.admitted].sort_values(["confidence", "call_id"], ascending=[False, True], kind="stable")
    duplicates = eligible.index[eligible.duplicated(["doc_id", "ticker"])]
    calls.loc[duplicates, ["admitted", "reject_reason"]] = [False, "same ticker twice in one episode"]
    admitted = calls[calls.admitted].sort_values(["entry_date", "call_id"], kind="stable")
    calls["renewal"] = False
    calls.loc[admitted.index, "renewal"] = admitted.duplicated("ticker")
    calls["unresolved_eligible"] = (calls.signal_eligible & ~calls.admitted
                                     & ~calls.ticker_kind.eq("foreign")
                                     & ~calls.price_status.str.startswith("outside scope:")
                                     & ~calls.reject_reason.eq("same ticker twice in one episode"))
    return calls


def expiry_dates(calls, calendar, hold_months):
    """Possible expiry sessions; a later call can supersede an earlier expiry."""
    if hold_months is None:
        return set()
    dates = set()
    for entry in calls.entry_date:
        expiry = entry + pd.DateOffset(months=hold_months)
        i = calendar.searchsorted(expiry)
        if i < len(calendar):
            dates.add(calendar[i])
    return dates


def active(calls, day, hold_months):
    last = calls.loc[calls.entry_date <= day].groupby("ticker").entry_date.max()
    return sorted(k for k, date in last.items() if hold_months is None or day < date + pd.DateOffset(months=hold_months))


def build_index(calls, prices, hold_months, start_date=None, max_weight=MAX_STOCK_WEIGHT):
    """Trade at t+1 closes and expiry closes, with capped initial allocations.

    Mark existing holdings before each trade. Newly purchased shares earn only
    subsequent returns. All active names receive equal weights up to max_weight;
    excess capital stays in cash. Weights may drift above that allocation limit
    until the next entry or expiry. There is no daily or monthly rebalancing.
    """
    if prices.index.has_duplicates or not prices.index.is_monotonic_increasing:
        raise ValueError("Prices require a unique, sorted session calendar")
    if not 0 < max_weight <= 1:
        raise ValueError("Maximum stock weight must be in (0, 1]")
    entries = set(calls.entry_date)
    expiries = expiry_dates(calls, prices.index, hold_months)
    bill = prices["BILL"] if "BILL" in prices else pd.Series(1.0, index=prices.index)
    if bill.isna().any() or (bill <= 0).any():
        raise ValueError("Cash index is incomplete or nonpositive")
    growth = bill.div(bill.shift()).fillna(1)
    shares, cash, started, daily, holdings = {}, 0.0, False, [], []
    for day, point in prices.iterrows():
        if start_date is not None and day < start_date:
            continue
        first_session = start_date is not None and not started
        if first_session:
            cash, started = 100.0, True
        elif started:
            cash *= growth.at[day]
        missing_held = [k for k in shares if k not in point or pd.isna(point[k]) or point[k] <= 0]
        if missing_held:
            raise ValueError(f"Unresolved held-stock price on {day.date()}: {missing_held}; do not assume cash proceeds")
        level = sum(count * point[k] for k, count in shares.items()) + cash
        turnover = 0.0
        pre_max = max((count * point[k] / level for k, count in shares.items()), default=0.0)
        names = active(calls, day, hold_months) if day in entries | expiries or first_session else sorted(shares)
        reasons = []
        if day in entries:
            reasons.append("call")
        if set(names) != set(shares):
            reasons.append("membership")
        if reasons:
            missing = [k for k in names if k not in point or pd.isna(point[k]) or point[k] <= 0]
            if missing:
                raise ValueError(f"Unresolved active-stock price on {day.date()}: {missing}")
            if names and not started:
                level, cash, started = 100.0, 100.0, True
            if started:
                if level <= 0:
                    raise ValueError("Portfolio value must remain positive")
                old = {k: count * point[k] / level for k, count in shares.items()}
                old["CASH"] = cash / level
                weight = min(1 / len(names), max_weight) if names else 0.0
                target = {k: weight for k in names}
                target["CASH"] = max(0.0, 1 - len(names) * weight)
                turnover = 0.5 * sum(abs(target.get(k, 0) - old.get(k, 0)) for k in target.keys() | old.keys())
                shares = {k: level * target[k] / point[k] for k in names}
                cash = level * target["CASH"]
                reason = "+".join(reasons)
                holdings.extend({"date": day, "symbol": k, "shares": count, "weight": target[k],
                                 "rebalance_reason": reason} for k, count in shares.items())
                holdings.append({"date": day, "symbol": "CASH", "shares": cash, "weight": target["CASH"],
                                 "rebalance_reason": reason})
        if started:
            weights = [count * point[k] / level for k, count in shares.items()]
            daily.append({"date": day, "level": level, "n_names": len(shares), "max_weight": max(weights, default=0),
                          "cash": cash / level, "turnover": turnover, "rebalanced": bool(reasons),
                          "max_weight_before_rebalance": pre_max})
    return (pd.DataFrame(daily).set_index("date") if daily else pd.DataFrame(), pd.DataFrame(holdings))


def annual_return(levels):
    years = (levels.index[-1] - levels.index[0]).days / 365.25
    return (levels.iloc[-1] / levels.iloc[0]) ** (1 / years) - 1 if years > 0 else np.nan


def run_baskets(calls, prices, max_weight=MAX_STOCK_WEIGHT):
    admitted = calls.loc[calls.admitted]
    if admitted.empty:
        raise ValueError("No admitted calls; no basket result can be produced")
    common = admitted.entry_date.min()
    diagnostics, holdings = {}, []
    for variant, months in VARIANTS.items():
        daily, held = build_index(admitted, prices, months, start_date=common,
                                  max_weight=max_weight)
        if daily.empty:
            raise ValueError(f"No rebalance yet for {variant}")
        diagnostics[variant] = daily
        holdings.append(held.assign(variant=variant))
    levels = pd.DataFrame({k: d.level for k, d in diagnostics.items()}).loc[common:]
    for benchmark in BENCHMARKS + ["BILL"]:
        levels[benchmark] = prices.loc[common:, benchmark]
    levels = levels.div(levels.iloc[0]).mul(100)
    if levels.isna().any().any():
        raise ValueError("Incomplete portfolio or benchmark levels")
    years = (levels.index[-1] - levels.index[0]).days / 365.25
    excess = levels.pct_change(fill_method=None).iloc[1:]
    excess = excess.sub(excess.BILL, axis=0)
    rows = []
    for name in levels:
        series = levels[name]
        row = {"variant": name, "ann_return": annual_return(series), "ann_vol": series.pct_change(fill_method=None).std() * np.sqrt(252),
               "max_drawdown": (series / series.cummax() - 1).min(), "vs_spy": annual_return(series) - annual_return(levels.SPY),
               "total_return": series.iloc[-1] / series.iloc[0] - 1, "ending_index": series.iloc[-1],
               "beta_to_spy": excess[name].cov(excess.SPY) / excess.SPY.var(),
               "sharpe": excess[name].mean() / excess[name].std() * np.sqrt(252) if excess[name].std() > 0 else np.nan}
        if name in diagnostics:
            d = diagnostics[name].loc[common:]
            row.update(avg_names=d.n_names.mean(), min_names=d.n_names.min(), pct_days_lt5=d.n_names.lt(5).mean(),
                       avg_cash=d.cash.mean(), turnover_per_yr=d.turnover.sum() / years if years else np.nan,
                       max_weight=d.max_weight.max(), rebalance_count=int(d.rebalanced.sum()),
                       max_weight_before_rebalance=d.max_weight_before_rebalance.max())
        rows.append(row)
    sensitivity = []
    for ticker in sorted(admitted.ticker.unique()):
        reduced = admitted.loc[admitted.ticker.ne(ticker)]
        for variant, months in VARIANTS.items():
            d, _ = build_index(reduced, prices, months, start_date=common,
                               max_weight=max_weight)
            ann = annual_return(d.level)
            sensitivity.append({"variant": variant, "excluded_ticker": ticker, "ann_return": ann,
                                "change_from_full": ann - annual_return(levels[variant]), "vs_spy": ann - annual_return(levels.SPY)})
    returns = levels.join(levels.pct_change(fill_method=None).add_suffix("_ret"))
    returns.index.name = "date"
    return returns, pd.DataFrame(rows), pd.concat(holdings, ignore_index=True), pd.DataFrame(sensitivity)


def export_results(calls, prices, yields, output_dir):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    returns, summary, holdings, sensitivity = run_baskets(calls, prices)
    unresolved = calls.loc[calls.unresolved_eligible]
    summary["unresolved_eligible_calls"] = len(unresolved)
    summary["coverage_complete"] = unresolved.empty
    returns.to_csv(output_dir / "portfolio_returns.csv")
    summary.to_csv(output_dir / "portfolio_summary.csv", index=False)
    sensitivity.to_csv(output_dir / "leave_one_out.csv", index=False)
    calls.to_csv(output_dir / "call_ledger.csv", index=False)
    calls.loc[calls.price_status.ne("priced at entry")].to_csv(output_dir / "missing_prices.csv", index=False)
    holdings.to_csv(output_dir / "holdings.csv", index=False)
    start, end = returns.index[0], returns.index[-1]
    cash = prices.BILL.loc[start:end].div(prices.BILL.at[start]).mul(100)
    dates = cash.index.union(yields.loc[start:end].index)
    treasury = pd.DataFrame({"yield_3m_pct": yields.reindex(dates), "cash_index": cash.reindex(dates),
                            "cash_daily_ret": cash.pct_change(fill_method=None).reindex(dates)})
    treasury.index.name = "date"
    treasury.to_csv(output_dir / "tbill_3m.csv")
    metadata = {"method_version": METHOD_VERSION,
                "execution": "first trading session strictly after publication, at its close",
                "expiry": "3 or 12 calendar months from entry; first session on or after expiry, at its close",
                "weighting": "equal target weights min(1 / active names, 0.10); remaining weight earns Treasury cash",
                "max_stock_weight": MAX_STOCK_WEIGHT,
                "cap_enforcement": "10% target at entry/expiry allocations only; weights drift between rebalances",
                "rebalance_schedule": "new or renewed call or actual expiry; no daily or monthly schedule",
                "admission": "judge-approved bullish US-listed company calls; no confidence/model/quote thresholds",
                "call_rows": len(calls), "admitted_calls": int(calls.admitted.sum()),
                "unresolved_eligible_calls": len(unresolved), "coverage_complete": unresolved.empty,
                "start": str(start.date()), "end": str(end.date()),
                "price_source": "private Yahoo adjusted closes", "security_metadata": "current Yahoo snapshot; not point-in-time membership",
                "cash_source": "FRED DGS3MO prior session yield, act/365", "leave_one_out": "common full-basket start; cash before first retained position"}
    (output_dir / "run_metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
    return returns, summary, sensitivity
