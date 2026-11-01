# Odd Lots basket method

Method version: `t1-entry-expiry-capped-v2`.

## Admission and timing

The input is the approved signal metadata, including unresolved tickers. Admission requires a bullish judge-approved call, a resolved US-listed equity identity, USD listing metadata and an observed entry price. US-listed does not mean US-domiciled. Confidence, quote score and model-count thresholds are not additional basket filters; extraction already requires agreement from at least two models on company and direction.

A call enters at the close of the first market session strictly after publication. Existing shares and cash are valued before trading; a new position earns only subsequent returns. Every new or renewed admitted call rebalances the whole basket. There is no scheduled monthly or daily rebalance. Benchmark price observations establish the trading calendar, and missing observed benchmark sessions are errors.

A holds indefinitely. B and C expire three and twelve calendar months after entry, respectively. Exit occurs at the first session's close on or after expiry and triggers a rebalance. A later admitted call on the same ticker renews its clock; a superseded expiry does not create an extra trade. A ticker is counted once. Only admitted calls compete when removing duplicate same-episode recommendations. Bearish calls do not close existing longs. These horizons are portfolio rules, not the speakers' intended investment horizons.

## Weights and cash

At every rebalance, each of the `N` active tickers receives `min(1/N, 0.10)` of portfolio value. Cash receives the remainder, or 100% when there are no stocks. Five stocks receive 10% each with 50% cash; twenty receive 5% each. Shares remain fixed between events: the 10% allocation limit does not prevent subsequent weight drift above 10%.

Cash accrues the prior market session's available FRED DGS3MO yield over elapsed calendar days on an actual/365 basis. This models yield accrual without Treasury-bill price risk. A blank raw yield denotes no observation; a blank modeled cash level in the bundled Treasury file denotes a non-market date.

## Prices and statistics

Yahoo adjusted closes supply local stock and benchmark total-return proxies. Prices are never backward filled, and missing stock observations are not silently forward filled. Missing prices for a held or active stock stop the engine. A takeover requires documented consideration, dates and any successor security; the last stock price is not assumed to be cash proceeds.

Portfolios and references start at 100 at the first admitted entry close. Annual returns use elapsed calendar days divided by 365.25; annualized daily volatility uses 252 sessions. Beta is the daily excess-return slope against SPY, subtracting modeled cash returns from both series and including an intercept. RSP supplies the equal-weight S&P 500 comparison. The call universe is broader than that index and includes MLPs. One-way turnover is half the sum of absolute target-minus-pretrade weights, including cash and the initial investment.

Leave-one-out runs remove every call for one ticker and retain the full basket's start and end dates. They earn cash until their first eligible investment. This measures sample concentration, not performance on unseen data.

## Coverage and reproducibility

There are 36 approved calls across 17 episodes: 23 bullish and 13 bearish. The pipeline admits 21 bullish calls across 13 episodes. BYD is outside the US-listing scope. ATVI remains unresolved: historical data was found separately, but historical identity and acquisition-payment handling are not integrated.

The current Yahoo exchange allowlist is `NYQ`, `NMS`, `NGM`, `NCM`, `ASE`, `PCX`, `TEX`. `TEX` is retained because it labels ET in the reviewed private snapshot; this is not historical venue verification. Unsupported current venues, changed security types/currencies and missing metadata require review. A current security master is not a point-in-time listing history.

The original calculation used a window from 2023-06-20 through 2026-10-30. Provider revisions may change later runs. Model adjudication and portfolio rules were refined retrospectively, and no costs or taxes are modeled. These limitations remain even with next-session execution.

The notebook reads public signal metadata and writes all basket outputs to the configured private directory, under `baskets/`. Price caches stay under its `data/` subdirectory. Returns, summaries, call disposition, concentration checks, charts and run metadata are not public release artifacts. The separately bundled Treasury CSV is attributed in [DATA_SOURCES.md](../DATA_SOURCES.md). Original market snapshots are intentionally not redistributed.
