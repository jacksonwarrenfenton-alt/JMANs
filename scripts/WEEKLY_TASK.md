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
For each pair below, (a) upload the repo file with the Artifact tool (`url` = the dashboard, `asset: true`, `file_path` = the file;
one CSV per call), (b) `ArtifactData` `get` collection `datasets` doc `<id>` to read its `version`, then (c) `update` that doc
with `source.url` = the new `/_blob/...` url exactly as returned (keep `kind`, `name`, `format`) and `updated` =
`{at: <now ISO>, by: "Claude"}`, passing `if_version`. Delete the replaced asset afterwards (Artifact `delete`, `path` = old id) only if the update succeeded.

| dataset id | repo file |
|---|---|
| index | data/monkey_price_index.csv |
| secondary | data/secondary_series.csv |
| ledger | data/inputs/nifdc_tenders.csv |
| press | data/inputs/press_candidates.csv |

Current asset ids (replace as you refresh): index b9128ef5a820a585e6c2cb07d0a5e397, secondary 7415fb44495f9eb40df5f3a177fb856d,
ledger dbb29d26541e110ef81b678a62b9484f, press c123abb521243aa4357fe9a6cdf1ef09.
If the Artifact or ArtifactData tools are unavailable in the run, say so in the status and continue; the repo data is the source of truth.
