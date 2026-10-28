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
| NIFDC awards | `data/inputs/nifdc_tenders.csv` (columns: `notice_id,lot_id,award_date,species,origin,quantity,unit_price_cny,source_url`), one row per lot copied from the official ccgp.gov.cn notice | The notice text isn't reliably machine-readable and ccgp.gov.cn rate-limits automated access. Every row must carry its source URL. |
| New-notice detection | script queries ccgp.gov.cn search for NIFDC primate award notices missing from the ledger and flags them (`UNLEDGERED_NOTICES`) | Freshness check. Parser is best-effort and unverified against a live response. If it is blocked the run FAILS (strict reading of the error rule). Set `discovery.enabled=false` in `config.json` to run on the ledger alone; this is recorded in `notes`. |
| CITES | CSV export(s) from trade.cites.org into `data/inputs/cites/` (Term=live, taxon Macaca fascicularis) | No open API. Data is annual and lags 1-2 years; the lag is recorded each run. |
| CRO / analyst | `data/inputs/cro_crosscheck.csv` (free-form columns) | Validation only; never in the index. |
| NMPA trial counts | `data/inputs/nmpa_trials.csv` (`period,count`) | Context only; nmpa.gov.cn blocks scripted access. |
