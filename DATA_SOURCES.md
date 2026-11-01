# Data sources and reuse

The public package contains original code and documentation, signal metadata, episode links, Treasury data and one static summary chart. It excludes podcast transcripts and quotations, transcript Parquet, raw model responses, Yahoo price caches and downloadable market-result datasets. This boundary reduces redistribution risk; it is not a guarantee of legal compliance or permission for every downstream use.

## Podcast signals and episode links

Bloomberg's Odd Lots podcast is the source of the underlying discussion. The signal file records the project's retrospective classifications, company/ticker, direction, dates, provenance and source references. Episode titles and links identify the source episodes. No verbatim transcript quotations are included. The project is independent and is not endorsed by Bloomberg or Odd Lots.

This repository ships no transcripts and no tooling for obtaining them. Any transcript corpus used to replicate the extraction must be obtained under your own rights and kept private; the README describes only the data layout the notebook expects.

Short quotations are not automatically cleared by a word limit. The [US Copyright Office](https://www.copyright.gov/help/faq/faq-fairuse.html) explains that fair use depends on the circumstances, not a fixed number of words. This package avoids transcript quotations rather than relying on a numeric threshold.

## Yahoo Finance

The backtest supports locally obtained Yahoo Finance adjusted closes through `yfinance`. Yahoo's [data-provider notice](https://help.yahoo.com/kb/SLN2310.html) prohibits redistribution of information displayed on or provided by Yahoo Finance. Its [terms](https://legal.yahoo.com/us/en/yahoo/terms/otos/index.html) also address automated collection and reuse. Availability through an open-source client does not grant data rights.

The README includes one static research chart of the calculated baskets, the RSP comparison and the cash proxy. It was rendered from the reviewed calculation through 30 October 2026 using Yahoo Finance adjusted closes and FRED DGS3MO. No downloadable Yahoo price series, rebased benchmark dataset, portfolio return file or statistics file is committed here. Local market inputs and detailed calculated outputs stay in the configured private directory. The repository's code license does not authorize downloading or redistributing provider data. Obtain appropriate rights before enabling network retrieval or publishing outputs; neither rebasing nor labeling an output as derived establishes permission by itself.

## Three-month Treasury data

Original source: **Board of Governors of the Federal Reserve System (US)**, H.15 Selected Interest Rates. Series: **Market Yield on U.S. Treasury Securities at 3-Month Constant Maturity, Quoted on an Investment Basis [DGS3MO]**, distributed through **FRED, Federal Reserve Bank of St. Louis**. [Series and suggested citation](https://fred.stlouisfed.org/series/DGS3MO).

The DGS3MO series is designated public domain with citation requested. The [Federal Reserve Board's copyright policy](https://www.federalreserve.gov/disclaimer.htm) permits copying and distribution of its information unless otherwise indicated and requests source attribution. This statement concerns DGS3MO; it does not imply that all FRED series are unrestricted.

`data/tbill_3m.csv` is exported from the project's retained snapshot labeled **2026-10-30**, not a new download for this publication. This label identifies the snapshot vintage, not a verified retrieval timestamp. Raw yields are annual percentages. The cash index is the project's calculation using a prior available market-session yield and actual/365 accrual; it is not an official Federal Reserve total-return series. Provider revisions can make a later download differ.

## License scope

The [MIT license](LICENSE) covers original code and documentation only. It does not license Bloomberg/YouTube content, third-party names or marks, external data, or dependencies. Source rights and applicable terms continue to govern those materials. FRED DGS3MO attribution is provided above; no redistribution license is asserted for Yahoo data.
