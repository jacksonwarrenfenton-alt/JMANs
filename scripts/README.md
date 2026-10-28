# Monkey Price Index

Weekly job. `python scripts/monkey_index.py` (deps pinned in `requirements.txt`).
Exit status line: `OK`, `STALE (inputs)`, or `FAILED (reason)`. Paths are repo-relative
(`data/`, `reports/`, `scripts/`); set `MPI_ROOT` to relocate.

## Method (placeholders resolved; edit `config.json` to change)
- **Primary series:** median per-animal winning-bid price (CNY) of *cynomolgus* lots in NIFDC award notices,
  trailing 90 days by award date. Simple median across lots (not quantity-weighted).
- **Base:** set automatically on the first successful run (that run's date and median = 100), stored in `data/state.json`.
- **Index** = median / base median x 100.
- **USD:** CNY median / USD-CNY from Frankfurter (ECB reference rate). ECB publishes once per business day, so
  the "same-day" rate is the latest published; the rate and its date are recorded in `notes`.
- **Outlier flag:** any new lot, or the median, more than 25% from the prior run's median. Flag only, never excluded.
- **Duplicates:** same (notice_id, lot_id) twice: flagged, later copy excluded.
- **Secondary series** (`data/secondary_series.csv`): rhesus median and Cambodia-origin median, same window.
- **No carry-forward.** No cynomolgus lots in the window = FAILED. An input with no new observation = STALE with last-updated date.

## Inputs
| Input | How it gets in | Why |
|---|---|---|
| NIFDC awards | `data/inputs/nifdc_tenders.csv` (columns: `notice_id,lot_id,award_date,species,origin,quantity,unit_price_cny,source_url`, optional `buyer`; blank = NIFDC), one row per lot copied from the official ccgp.gov.cn notice | The notice text isn't reliably machine-readable and ccgp.gov.cn rate-limits automated access. Every row must carry its source URL. |
| New-notice detection | script queries ccgp.gov.cn search for NIFDC primate award notices missing from the ledger and flags them (`UNLEDGERED_NOTICES`) | Freshness check. Parser is best-effort and unverified against a live response. If it is blocked the run FAILS (strict reading of the error rule). Set `discovery.enabled=false` in `config.json` to run on the ledger alone; this is recorded in `notes`. |
| CITES | **Automatic.** Each run HEADs the CITES full-database download (trade.cites.org/cites_trade/download_db); when the version changes it streams the ~0.5 GB zip and rewrites `data/inputs/cites/macaca_live_annual.csv` (live *Macaca fascicularis*, exporter- and importer-reported, by year and exporter). Data is annual with a 1-2 year lag; the lag is recorded each run. | Free, no login, reuse permitted with credit. It is a supply indicator, not a price. A refresh failure is logged and marks CITES STALE. |
| CRO share prices | **Automatic.** Yahoo chart API (public), 昭衍新药 603127.SS and JOINN 6127.HK, weekly last close and 4w/12w change, appended to `data/context_equities.csv`. 429s get two slow retries; a failed ticker marks itself STALE and is logged. | Weekly sentiment context only; never in the index. |
| CRO / analyst prices | `data/inputs/cro_crosscheck.csv` (free-form) | Validation only. No free machine-readable source; stays STALE until filled. |
| NMPA trial counts | `data/inputs/nmpa_trials.csv` (`period,count`) | Context only. chinadrugtrials.org.cn is not readable by the fetch tools and nmpa.gov.cn blocks scripts; stays STALE until filled. |

## Series
- **Headline index:** NIFDC awards only (buyer blank or containing NIFDC / 检定).
- **all_buyers_cynomolgus** (`secondary_series.csv`): median over every public buyer (CAS institutes, universities, hospitals). Updates more often; mixes buyers and specs, so it is never blended into the headline.

## Status line
`OK` / `STALE (...)` / `FAILED (...)`. An input is STALE only if it is overdue for its own cadence
(`config.json` -> `freshness`: NIFDC 75 days since the last new award, NMPA 120 days) or its fetch failed
(CITES refresh, a share-price ticker). "No new data this week" within cadence, and optional inputs you have not
configured (NMPA, CRO/analyst), are listed under "Input freshness" in the report with their last-updated date,
not counted as stale. `stale_inputs` in the CSV carries only the overdue/failed ones.
