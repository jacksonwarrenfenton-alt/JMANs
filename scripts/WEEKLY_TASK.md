# Weekly run: agent runbook

Goal: add any NEW NIFDC primate award notices to the ledger, then run the script. Keep it cheap:
at most 4 searches and 8 fetches per run; stop early when nothing new turns up.

## 0. Primary discovery (works; use this first)
WebFetch the CCGP search page (one plain request per keyword, no custom headers):
`https://search.ccgp.gov.cn/bxsearch?searchtype=1&kw=<URL-encoded keyword>&page_index=1&bidSort=0&buyerName=&projectId=&pinMu=0&bidType=0&dbselect=bidx&timeType=5`
Keywords: `食蟹猴`, `实验猴`, `恒河猴`, `猕猴`. Ask the fetch prompt to list title, date, buyer, notice URL, and notice type.
Open every 中标/成交 公告 whose URL is not already in the ledger and extract per section 2. Ignore feed, housing and
service contracts (青饲料, 购养服务). If the page is ever a block/captcha page, log it and stop: do not retry or alter requests.

## 1. Find (fallback pointers)
Search (Chinese first): `中国食品药品检定研究院 食蟹猴 中标公告`, `中检院 实验用猴 采购 成交结果`,
plus `恒河猴` / `猕猴` variants. Prefer results on ccgp.gov.cn; tender aggregators that repost the notice
(e.g. yzb.caigou2003.com) are acceptable; news/research articles are NOT a source of ledger rows.
Skip any URL already in `data/inputs/nifdc_tenders.csv` or `data/inputs/press_candidates.csv`.

Also search for other public buyers (中科院 药物研究所, 大学, 医院, 实验动物中心 + 食蟹猴/实验猴 采购 中标). Record the buyer in the `buyer` column; non-NIFDC rows feed only the all-buyer composite.

## 2. Extract (WebFetch the notice page itself)
Needed: notice_id (project/tender number), lot_id, award_date, species (cynomolgus|rhesus),
buyer, origin (china|cambodia|unknown), quantity, unit_price_cny, source_url.
Rules:
- Only append a row when quantity AND per-animal unit price are visible on the notice.
- If a total is shown, check unit_price x quantity ~= total (within 1%). If not, do not add to the ledger;
  put it in press_candidates.csv with note "price basis unclear".
- The notice usually states no award date: use the publication date as award_date and say so in press_candidates note.
- Total masked (***) is allowed only when the page labels the price per unit; note `total_masked`.
- Never use budgets (预算) or tender announcements (招标公告) as prices; they may go to press_candidates.csv with basis=budget.
- Never estimate, back-solve, or fill missing fields. Unknown origin = `unknown`.
- Append only. Never edit or delete existing rows.

## 3. Press / secondary figures
News, broker, or research-site figures go to `data/inputs/press_candidates.csv`
(`date,buyer,quantity,unit_price_cny,basis,source_url,note`). They appear in the report as unverified and
never enter the index. Do not copy third-party compiled tables (e.g. research-vendor chart pages): they are
secondhand, may be paywalled or copyrighted, and repeat the same few notices.

## 4. Run and publish
`python scripts/monkey_index.py` (venv from `scripts/requirements.txt`), then commit data/ and reports/ and push
to the working branch. Report the one-line status.
If a fetch is blocked (403/429/頻繁訪問), log it in the report and move on; do not retry with altered headers
or other circumvention.

## 5. Refresh the dashboard (after the script succeeds and you have pushed)
Dashboard: https://claude.ai/artifact/2DU59sJNJciP2apLaRYSh5 (artifact type Dashboard; datasets are attached CSV files).
For each pair below: (a) upload the repo file with the Artifact tool (`url` = the dashboard, `asset: true`, `file_path` = the file;
one CSV per call); (b) `ArtifactData` `get` collection `datasets` doc `<id>` to read its `version` and old `source.url`;
(c) `update` that doc with `source.url` = the new `/_blob/...` url exactly as returned (keep `kind`, `name`, `format`) and
`updated` = `{at: <now ISO>, by: "Claude"}`, passing `if_version`; (d) delete the replaced asset (Artifact `delete`, `path` = the
old blob id, the part after `/_blob/`) only if the update succeeded. Skip a pair whose repo file did not change.

| dataset id | repo file |
|---|---|
| idxfx | data/index_fx_adjusted.csv |
| lots | data/lot_prices.csv |
| fx | data/fx_usd_cny.csv |
| health | data/source_health.csv |
| press | data/inputs/press_candidates.csv |

If the Artifact or ArtifactData tools are unavailable in the run, say so in the status and continue; the repo data is the source of truth.

## Ledger columns
`notice_id,lot_id,award_date,species,origin,quantity,unit_price_cny,source_url,buyer,price_basis`.
`price_basis` is `stated` when the notice prints a per-animal price. It is `derived_from_total` only when the notice has a single
line item, states quantity and total, and total / quantity is an exact whole number; say so in the commit message.
After editing the ledger, `python scripts/monkey_index.py --context-only` regenerates `data/lot_prices.csv`.

## Source health (every run)
Record whether each source answered. For the CCGP search fetch, run
`python scripts/monkey_index.py --log-health ccgp_search ok "<n> results"` (or `fail "<reason>"`).
The script logs FX, the ledger, both share-price tickers and the CITES database itself. A source that fails two runs in a row
must be called out in the final status line.

## Daily mode (Tuesday to Sunday; Monday runs the full weekly flow)
1. Section 0 only, keyword `食蟹猴`, `timeType=5`: look for award notices not yet in the ledger. Open and add them per section 2.
2. `python scripts/monkey_index.py --context-only` (refreshes FX, lot prices, share prices, source health; writes no index row).
3. Commit data/ and push. Refresh dashboard pairs (section 5): always `health` and `fx`; the others only if their file changed.
4. Final status: ccgp_search ok/fail, new ledger rows, any failing source.
