# Odd Lots index

A retrospective study of equity calls in Bloomberg's Odd Lots podcast, January 2023 to October 2026. Three language models extract candidate calls, a judge reviews them, and the code constructs three long-only baskets. This is independent research, with no affiliation with Bloomberg or Odd Lots.

The public signals contain **36 approved calls across 17 episodes: 23 bullish and 13 bearish**. Of the bullish calls, 21 across 13 episodes are admitted to the baskets. BYD is outside the US-listing scope; Activision Blizzard (ATVI) remains unresolved in this pipeline. The episode file retains 736 feed records: 665 matched YouTube videos and 71 unmatched records, which include trailers and cross-promotions as well as episodes.

This repository publishes code, signal metadata, episode links and Treasury data. **It contains no transcripts, verbatim transcript quotes, raw model responses, Yahoo price histories, market-derived results or charts.** Market results are generated locally and kept private. See [data sources and reuse](DATA_SOURCES.md).

## Files

- `notebooks/odd_lots_backtest.ipynb`: basket construction and local results; saved outputs are cleared.
- `notebooks/odd_lots_stock_recs.ipynb`: optional extraction, judge and ticker validation; saved outputs are cleared.
- `scripts/`: basket functions, optional local transcript processing and public-export checks.
- `data/trade_recommendations.csv`: approved signal metadata, provenance, source links and timestamps, without transcript quotations.
- `data/odd_lots_youtube_links_2023_2026.csv`: matched and unmatched episode records.
- `data/tbill_3m.csv`: FRED DGS3MO yield observations and a modeled cash index.
- `notes/BASKET_PLAN.md`: calculation details. `tests/`: offline regression checks.

## Setup

Use Python 3.12:

```sh
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m pytest -q
jupyter lab
```

Open notebooks from the project root or `notebooks/`. Private inputs and results default to the ignored `private/` folder. Set `ODD_LOTS_PRIVATE_DIR` to choose another private directory; relative values resolve from the project root.

Keys come only from environment variables: `OPENROUTER_API_KEY` for model requests and `APIFY_TOKEN` for transcript fetching. `.env.example` lists the settings; the code does not automatically load `.env` or personal key files. Keep credentials out of Git.

Notebook network access is off by default. Set `ODD_LOTS_ALLOW_NETWORK=1` only when you have appropriate access rights and want to allow downloads or model requests. Paid services may incur charges. Retrying cached model failures additionally requires `ODD_LOTS_RETRY_ERRORS=1`.

## Run the baskets locally

The backtest uses the public signals and locally obtained Yahoo/FRED inputs; transcripts are unnecessary. With appropriate access rights, enable network access for a first run of `notebooks/odd_lots_backtest.ipynb`, or supply the expected private caches. Inputs are stored under `private/data/`; returns, statistics, call disposition, concentration checks and run metadata stay under `private/baskets/`. Do not copy those market-derived files into this public repository.

Fresh downloads may differ because providers revise data. Exact reproduction of the original calculation requires its private input snapshot, which is not redistributed. Missing or interrupted price histories are not proof of delisting: the engine stops on missing prices for an active holding rather than inventing acquisition proceeds.

## Basket rules

Calls enter at the **close of the first trading session after publication**. The entire basket rebalances when an admitted call enters or renews, or a holding expires. There is no scheduled monthly or daily rebalance.

- A holds indefinitely.
- B exits three calendar months after entry.
- C exits twelve calendar months after entry.

Expiry occurs at the first session's close on or after the expiry date. A later admitted call renews that stock's clock. Bearish calls remain in the signal file but do not open shorts or cancel existing longs.

At each allocation, every active stock receives `min(1 / number_of_stocks, 10%)` of portfolio value. The rest stays in Treasury cash. Five stocks therefore receive 10% each with 50% cash; twenty receive 5% each. **Weights drift between events and can exceed 10%; this is an allocation limit, not a continuous cap.**

Cash accrues the previous market session's available three-month Treasury yield over actual calendar days divided by 365. This is a yield-accrual proxy, not an observed Treasury-bill total-return index. The local calculation uses Yahoo adjusted closes and includes SPY and RSP as reference series; RSP represents the equal-weight S&P 500 comparison. Portfolio levels start at 100 at the first admitted entry close.

## Extraction and limitations

The extractors are `openai/gpt-5-nano`, `deepseek/deepseek-v4.1-flash` and `google/gemini-2.5-flash-lite`. At least two must agree on company and direction before a judge checks the supporting text. The judge is also `deepseek/deepseek-v4.1-flash`; agreement is not evidence of independent model errors. The public metadata records decisions, not independently measured extraction accuracy.

This is an incomplete-coverage, retrospective study. Model selection, judge criteria and portfolio rules were not preregistered; criteria were refined after reviewing candidates, with some return effects already known. No complete new human adjudication of every candidate was performed. Current security metadata and model knowledge do not establish a point-in-time stock universe. ATVI's historical identity and acquisition payment are not integrated, so it remains excluded.

The sample is small, and concentration matters. Missing episodes, missed calls and unresolved acquired securities can affect results. Holding periods are portfolio rules rather than speakers' intended horizons. Trading costs, taxes, spreads and market impact are not modeled. Trading after publication does not remove retrospective selection bias.

## Optional extraction

Only obtain and process content you have rights to use. Keep transcripts, Parquet files and model responses private. A paid extraction service does not itself grant rights to redistribute the underlying content.

```sh
python scripts/apify_transcripts.py data/odd_lots_youtube_links_2023_2026.csv private/youtube --workers 4
python scripts/build_parquet.py
```

Fetching is separately invoked and may incur charges. Its budget is checked between batches, not a guaranteed spending cap. The builder records coverage and stops on missing or invalid inputs unless `--allow-incomplete` is explicitly supplied. Optional Apple inputs must be supplied explicitly. Run the extraction notebook afterward; extraction, grouping and judge errors stop production.

`python scripts/export_public.py private/llm_extractions/trade_recs_final.csv` exports approved metadata without transcript quotations. Rebuilding transcripts can change request hashes and require new model calls; existing decisions came from private cached model evidence.

## Publication and license

Run `python scripts/release_check.py` on the publication package. The check enforces the file list and data schemas and rejects notebook outputs, prohibited content, recognizable secrets and personal paths. A successful scan is not a legal clearance.

Original code and documentation are [MIT licensed](LICENSE). Third-party content and data are outside that license. [DATA_SOURCES.md](DATA_SOURCES.md) explains source attribution and the publication boundary; Yahoo market outputs and podcast text must not be added to the public package without appropriate rights.
