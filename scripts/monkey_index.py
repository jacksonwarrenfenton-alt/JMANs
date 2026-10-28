#!/usr/bin/env python3
"""Monkey Price Index: weekly run. See README.md in this directory for method.

Layout (relative to repo root, override with MPI_ROOT):
  data/inputs/nifdc_tenders.csv   verified NIFDC award ledger (primary input, append-only)
  data/inputs/cites/*.csv         CITES Trade Database exports (manual download)
  data/inputs/cro_crosscheck.csv  CRO / analyst reference prices (validation only)
  data/inputs/nmpa_trials.csv     NMPA clinical trial counts (context only)
  data/raw/YYYY-MM-DD/            raw fetches + meta.json (source URL, fetch time)
  data/monkey_price_index.csv     the index (append-only)
  data/secondary_series.csv       rhesus / Cambodia-origin medians (append-only)
  data/state.json                 per-source freshness markers, base value
  reports/YYYY-MM-DD.md, reports/charts/
"""
import csv, datetime as dt, io, json, os, re, shutil, statistics, subprocess, sys, tempfile, time, traceback, zipfile
from pathlib import Path

import requests

ROOT = Path(os.environ.get("MPI_ROOT") or Path(__file__).resolve().parent.parent)
CFG = json.loads((Path(__file__).resolve().parent / "config.json").read_text())
TODAY = dt.date.fromisoformat(os.environ["MPI_TODAY"]) if os.environ.get("MPI_TODAY") else dt.datetime.now(dt.timezone.utc).date()
D = ROOT / "data"
INDEX_CSV = D / "monkey_price_index.csv"
SECONDARY_CSV = D / "secondary_series.csv"
STATE_JSON = D / "state.json"
RAW = D / "raw" / TODAY.isoformat()
INDEX_COLS = ["date", "index_value", "price_cny_median", "price_usd", "species_used",
              "sources_used", "stale_inputs", "flags", "notes"]
LEDGER_COLS = ["notice_id", "lot_id", "award_date", "species", "origin", "quantity",
               "unit_price_cny", "source_url"]
SPECIES = {"cynomolgus", "rhesus"}
UA = {"User-Agent": "Mozilla/5.0 (monkey-price-index; research)"}


class SourceError(Exception):
    pass


def now_iso():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def save_raw(name, content, url):
    RAW.mkdir(parents=True, exist_ok=True)
    p = RAW / name
    p.write_bytes(content if isinstance(content, bytes) else content.encode())
    metas = RAW / "meta.json"
    m = json.loads(metas.read_text()) if metas.exists() else {}
    m[name] = {"source": url, "fetched_at": now_iso()}
    metas.write_text(json.dumps(m, indent=2, ensure_ascii=False))


def load_state():
    return json.loads(STATE_JSON.read_text()) if STATE_JSON.exists() else {}


def read_csv(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


# ---------- inputs ----------
def load_ledger(flags):
    """Primary: NIFDC award ledger. Malformed rows => SourceError (no partial index)."""
    p = D / "inputs" / "nifdc_tenders.csv"
    if not p.exists():
        raise SourceError(f"NIFDC ledger missing: {p}")
    save_raw("nifdc_tenders.csv", p.read_bytes(), f"local ledger {p.relative_to(ROOT)}")
    rows = read_csv(p)
    if not rows:
        raise SourceError("NIFDC ledger is empty")
    missing = [c for c in LEDGER_COLS if c not in rows[0]]
    if missing:
        raise SourceError(f"NIFDC ledger missing columns: {missing}")
    lots, seen = [], set()
    for i, r in enumerate(rows, start=2):
        try:
            lot = {
                "key": (r["notice_id"].strip(), r["lot_id"].strip()),
                "award_date": dt.date.fromisoformat(r["award_date"].strip()),
                "species": r["species"].strip().lower(),
                "origin": r["origin"].strip().lower(),
                "quantity": int(r["quantity"]),
                "price": float(r["unit_price_cny"]),
                "url": r["source_url"].strip(),
                "buyer": (r.get("buyer") or "NIFDC").strip(),
            }
            lot["nifdc"] = "nifdc" in lot["buyer"].lower() or "检定" in lot["buyer"]
        except (ValueError, KeyError, AttributeError) as e:
            raise SourceError(f"ledger line {i}: unparseable value ({e})")
        if lot["price"] <= 0 or lot["quantity"] <= 0 or lot["species"] not in SPECIES or not lot["url"]:
            raise SourceError(f"ledger line {i}: unexpected value (price/qty/species/url): {r}")
        if lot["award_date"] > TODAY:
            raise SourceError(f"ledger line {i}: award_date {lot['award_date']} is in the future")
        if lot["key"] in seen:
            flags.append(f"DUPLICATE_LOT {lot['key'][0]}/{lot['key'][1]} (line {i}; later copy excluded)")
            continue
        seen.add(lot["key"])
        lots.append(lot)
    return lots


def discover_notices(lots):
    """Freshness check: scrape CCGP search for NIFDC primate award notices not in the ledger.
    Parser is best-effort; a block page or zero parseable results raises SourceError."""
    cfg = CFG["discovery"]
    if not cfg["enabled"]:
        return None
    r = requests.get(cfg["url"], headers=UA, timeout=30)
    save_raw("ccgp_search.html", r.content, cfg["url"])
    if r.status_code != 200:
        raise SourceError(f"CCGP search HTTP {r.status_code}")
    if "访问过于频繁" in r.text:
        raise SourceError("CCGP search rate-limited this IP (频繁访问)")
    found = re.findall(r'<a href="(https?://www\.ccgp\.gov\.cn/[^"]+)"[^>]*>\s*(.*?)\s*</a>', r.text, flags=re.S)
    found = [(u, re.sub(r"<[^>]+>", "", t)) for u, t in found]
    found = [(u, t) for u, t in found if re.search(r"中标|成交", t) and "猴" in t]
    if not found:
        raise SourceError("CCGP search returned no parseable award notices (format change or empty)")
    known = " ".join(l["url"] for l in lots)
    return [(u, t) for u, t in found if u not in known]


def fetch_fx(flags, notes):
    r = requests.get(CFG["fx_url"], headers=UA, timeout=30)
    save_raw("fx.json", r.content, CFG["fx_url"])
    if r.status_code != 200:
        raise SourceError(f"FX HTTP {r.status_code}")
    j = r.json()
    rate, rdate = j["rates"]["CNY"], j["date"]
    lo, hi = CFG["fx_sane_range"]
    if not (lo < rate < hi):
        raise SourceError(f"FX rate {rate} outside sane range {lo}-{hi}")
    if rdate != TODAY.isoformat():
        notes.append(f"FX rate dated {rdate} (ECB reference, latest published; not run date)")
    return float(rate), rdate


def fetch_equities(errors):
    """Context only: CRO share prices (Yahoo chart API, public). Failures never block the index."""
    out = {}
    for i, (tk, name) in enumerate(CFG["equities"].items()):
        if i:
            time.sleep(3)
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{tk}?range=6mo&interval=1d"
        try:
            for wait in (20, 40, None):
                r = requests.get(url, headers={"User-Agent": "monkey-price-index research"}, timeout=30)
                if r.status_code == 429 and wait:
                    time.sleep(wait)  # honest backoff on rate limit; give up after two retries
                    continue
                break
            if r.status_code != 200:
                raise SourceError(f"HTTP {r.status_code}")
            save_raw(f"equity_{tk}.json", r.content, url)
            res = r.json()["chart"]["result"][0]
            pts = [(dt.date.fromtimestamp(t), c) for t, c in zip(res["timestamp"], res["indicators"]["quote"][0]["close"]) if c]
            if not pts:
                raise SourceError("no closes")
            out[tk] = {"name": name, "pts": pts, "ccy": res["meta"].get("currency", "")}
        except Exception as e:
            errors.append(f"equity {tk}: {type(e).__name__}: {e}")
    return out


def eq_change(pts, weeks):
    tgt = pts[-1][0] - dt.timedelta(weeks=weeks)
    base = [c for d, c in pts if d <= tgt]
    return pct(pts[-1][1], base[-1]) if base else None


def cites_remote_version():
    r = requests.head(CFG["cites_download_url"], headers=UA, timeout=30, allow_redirects=True)
    m = re.search(r'filename="?([^";]+)', r.headers.get("content-disposition", ""))
    if r.status_code != 200 or not m:
        raise SourceError(f"CITES version check failed (HTTP {r.status_code})")
    return m.group(1)


def update_cites(state, notes):
    """Refresh data/inputs/cites/macaca_live_annual.csv when the CITES full-database version changes.
    Streams the ~0.5GB zip, keeps only Macaca fascicularis live-animal rows, sums by year/exporter/reporter."""
    ver = cites_remote_version()
    if ver == state.get("cites_version") and (D / "inputs" / "cites" / "macaca_live_annual.csv").exists():
        notes.append(f"CITES db {ver} unchanged since {state.get('cites_checked_new', 'earlier')}")
        return None
    zpath = os.environ.get("MPI_CITES_ZIP")
    tmp = None
    if not zpath:
        tmp = tempfile.mkdtemp()
        zpath = os.path.join(tmp, "cites.zip")
        subprocess.run(["curl", "-sS", "-f", "-L", "-m", "1500", "-o", zpath, CFG["cites_download_url"]], check=True)
    agg = {}
    try:
        with zipfile.ZipFile(zpath) as zf:
            names = sorted(n for n in zf.namelist() if n.endswith(".csv"))
            if not names:
                raise SourceError("CITES zip has no CSV members")
            hdr = None
            for n in names:
                with zf.open(n) as raw:
                    for line in io.TextIOWrapper(raw, encoding="utf-8", newline=""):
                        if hdr is None:
                            hdr = next(csv.reader([line]))
                            ix = {k: hdr.index(k) for k in ("Year", "Taxon", "Term", "Quantity", "Unit", "Exporter", "Reporter.type")}
                            continue
                        if "Macaca fascicularis" not in line:
                            continue
                        f = next(csv.reader([line]))
                        if f[ix["Taxon"]] != "Macaca fascicularis" or f[ix["Term"]].lower() != "live" or f[ix["Unit"]] not in ("",):
                            continue
                        try:
                            q = float(f[ix["Quantity"]])
                        except ValueError:
                            continue
                        k = (int(f[ix["Year"]]), f[ix["Exporter"]], f[ix["Reporter.type"]])
                        agg[k] = agg.get(k, 0.0) + q
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)
    if not agg:
        raise SourceError("CITES parse produced no Macaca fascicularis live rows (format change?)")
    out = D / "inputs" / "cites" / "macaca_live_annual.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    keys = sorted({(y, e) for y, e, _ in agg})
    with open(out, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["Year", "Taxon", "Term", "Exporter", "Exporter reported quantity", "Importer reported quantity"])
        for y, e in keys:
            w.writerow([y, "Macaca fascicularis", "live", e, agg.get((y, e, "E"), ""), agg.get((y, e, "I"), "")])
    notes.append(f"CITES db {ver} parsed: {len(keys)} exporter-years, data through {max(y for y, _ in keys)}")
    return ver


def load_cites(notes):
    """Annual live-macaque exports by exporter from manual CITES Trade Database CSV exports."""
    files = sorted((D / "inputs" / "cites").glob("*.csv"))
    if not files:
        return None
    agg = {}
    for f in files:
        save_raw(f"cites_{f.name}", f.read_bytes(), f"local CITES export {f.name}")
        for r in read_csv(f):
            r = {k.strip().lower(): (v or "").strip() for k, v in r.items()}
            if "macaca" not in r.get("taxon", "").lower() or r.get("term", "live").lower() != "live":
                continue
            q = r.get("exporter reported quantity") or r.get("importer reported quantity") or ""
            if not q:
                continue
            try:
                agg[(r["exporter"], int(r["year"]))] = agg.get((r["exporter"], int(r["year"])), 0) + float(q)
            except (ValueError, KeyError):
                raise SourceError(f"CITES file {f.name}: unparseable row {r}")
    return agg or None


# ---------- calc ----------
def window(lots, species=None, origin=None):
    lo = TODAY - dt.timedelta(days=CFG["window_days"])
    return [l for l in lots if lo <= l["award_date"] <= TODAY
            and (species is None or l["species"] == species)
            and (origin is None or l["origin"] == origin)]


def med(lots):
    return statistics.median(l["price"] for l in lots) if lots else None


def last_index_row():
    if not INDEX_CSV.exists():
        return None
    rows = read_csv(INDEX_CSV)
    return rows[-1] if rows else None


def append_row(path, cols, row):
    new = not path.exists()
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        if new:
            w.writeheader()
        w.writerow(row)


def pct(a, b):
    return None if a is None or b in (None, 0) else (a / b - 1) * 100


def fmt(x, nd=2, suf=""):
    return "n/a" if x is None else f"{x:,.{nd}f}{suf}"


def history_change(rows, weeks):
    target = TODAY - dt.timedelta(weeks=weeks)
    prior = [r for r in rows if r["index_value"] and dt.date.fromisoformat(r["date"]) <= target]
    return prior[-1] if prior else None


def make_chart(rows):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    pts = [(dt.date.fromisoformat(r["date"]), float(r["index_value"])) for r in rows if r["index_value"]]
    if len(pts) < 2:
        return None
    out = ROOT / "reports" / "charts" / f"{TODAY.isoformat()}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(8, 4))
    ax.plot(*zip(*pts), marker="o")
    ax.set_title("Monkey Price Index (cynomolgus, CNY median, base=100)")
    ax.set_ylabel("index"); ax.grid(alpha=.3)
    fig.autofmt_xdate(); fig.tight_layout(); fig.savefig(out, dpi=130); plt.close(fig)
    return out


def write_report(lines):
    p = ROOT / "reports" / f"{TODAY.isoformat()}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run():
    flags, notes, stale, sources, errors = [], [], [], [], []
    new_cites_version = None
    state = load_state()
    D.mkdir(parents=True, exist_ok=True)
    prior = last_index_row()
    if prior and prior["date"] == TODAY.isoformat():
        raise SourceError(f"row for {TODAY} already exists (append-only; not overwriting)")

    lots = load_ledger(flags)
    new_notices = discover_notices(lots)
    if new_notices is None:
        notes.append("NIFDC discovery disabled in config; freshness of ledger vs CCGP unverified")
    elif new_notices:
        flags.append(f"UNLEDGERED_NOTICES {len(new_notices)} award notices on CCGP not in ledger (enter them, then rerun)")
        for u, t in new_notices[:10]:
            notes.append(f"unledgered: {t} {u}")
    sources.append("nifdc_ledger")
    fx, fx_date = fetch_fx(flags, notes)
    sources.append("frankfurter_ecb_fx")

    # freshness of primary input
    seen_before = {tuple(k) for k in state.get("seen_lots", [])}
    new_lots = [l for l in lots if l["key"] not in seen_before]
    if not new_lots:
        stale.append(f"nifdc(last_updated={state.get('nifdc_last_updated', 'never')})")

    prim = window([l for l in lots if l["nifdc"]], CFG["primary_species"])
    if not prim:
        raise SourceError(f"no NIFDC {CFG['primary_species']} awards in trailing {CFG['window_days']}d window; "
                          "not carrying forward (METHOD)")
    median_cny = med(prim)

    # base
    base = state.get("base")
    if base is None:
        base = {"date": TODAY.isoformat(), "median_cny": median_cny}
        notes.append(f"BASE SET: {base['date']} median {median_cny:,.0f} CNY = {CFG['base_value']}")
    idx = median_cny / base["median_cny"] * CFG["base_value"]
    usd = median_cny / fx

    # outliers (flag only)
    prior_med = float(prior["price_cny_median"]) if prior and prior["price_cny_median"] else None
    thr = CFG["outlier_pct"]
    for l in new_lots:
        if l["species"] == CFG["primary_species"] and prior_med:
            d = pct(l["price"], prior_med)
            if abs(d) > thr:
                flags.append(f"OUTLIER_LOT {l['key'][0]}/{l['key'][1]} {l['price']:,.0f} CNY is {d:+.1f}% vs prior median (review)")
    d = pct(median_cny, prior_med)
    if d is not None and abs(d) > thr:
        flags.append(f"OUTLIER_MEDIAN median {d:+.1f}% vs prior run (review)")

    # secondary series
    sec = {"all_buyers_cynomolgus": window(lots, "cynomolgus"), "rhesus": window(lots, "rhesus"), "cambodia_origin": window(lots, None, "cambodia")}

    # validation / supply / context inputs (never primary)
    try:
        new_cites_version = update_cites(state, notes)
    except Exception as e:
        errors.append(f"cites refresh: {type(e).__name__}: {e}")
    cites = load_cites(notes)
    cites_info = None
    if cites is None:
        stale.append(f"cites(last_updated={state.get('cites_last_updated', 'never')}; no export file in data/inputs/cites)")
    else:
        latest_year = max(y for _, y in cites)
        if state.get("cites_latest_year") == latest_year:
            stale.append(f"cites(latest_year={latest_year}; last_updated={state.get('cites_last_updated', 'never')})")
        else:
            sources.append("cites_trade_db")
        cites_info = (latest_year, TODAY.year - latest_year)
        notes.append(f"CITES lag: latest data year {latest_year}, {TODAY.year - latest_year}y behind run year")
    press = None
    p = D / "inputs" / "press_candidates.csv"
    if p.exists():
        save_raw("press_candidates.csv", p.read_bytes(), f"local {p.relative_to(ROOT)}")
        press = read_csv(p)[-8:]
    cro = None
    p = D / "inputs" / "cro_crosscheck.csv"
    if p.exists():
        save_raw("cro_crosscheck.csv", p.read_bytes(), f"local {p.relative_to(ROOT)}")
        cro = read_csv(p)
        sources.append("cro_crosscheck")
    else:
        stale.append("cro_crosscheck(none provided)")
    nmpa = None
    p = D / "inputs" / "nmpa_trials.csv"
    if p.exists():
        save_raw("nmpa_trials.csv", p.read_bytes(), f"local {p.relative_to(ROOT)}")
        nmpa = read_csv(p)
        key = nmpa[-1]["period"] if nmpa else None
        if key and state.get("nmpa_latest") == key:
            stale.append(f"nmpa_context(latest={key})")
    else:
        stale.append("nmpa_context(none provided)")

    eq = fetch_equities(errors)
    for tk in CFG["equities"]:
        if tk not in eq:
            stale.append(f"equity_{tk}(fetch failed)")
        else:
            sources.append(f"yahoo_{tk}")

    # ---- all validation passed: commit outputs ----
    rows_before = read_csv(INDEX_CSV) if INDEX_CSV.exists() else []
    row = {"date": TODAY.isoformat(), "index_value": f"{idx:.2f}", "price_cny_median": f"{median_cny:.0f}",
           "price_usd": f"{usd:.0f}", "species_used": CFG["primary_species"],
           "sources_used": ";".join(sources), "stale_inputs": ";".join(stale),
           "flags": " | ".join(flags), "notes": " | ".join(notes + [f"fx_usd_cny={fx} ({fx_date}); n_lots_window={len(prim)}"])}
    append_row(INDEX_CSV, INDEX_COLS, row)
    for name, ls in sec.items():
        append_row(SECONDARY_CSV, ["date", "series", "n_lots", "median_cny"],
                   {"date": TODAY.isoformat(), "series": name, "n_lots": len(ls),
                    "median_cny": f"{med(ls):.0f}" if ls else ""})
    for tk, e in eq.items():
        append_row(D / "context_equities.csv", ["date", "ticker", "last_close_date", "close", "currency"],
                   {"date": TODAY.isoformat(), "ticker": tk, "last_close_date": e["pts"][-1][0].isoformat(),
                    "close": f"{e['pts'][-1][1]:.2f}", "currency": e["ccy"]})
    state.update({"base": base, "seen_lots": sorted(l["key"] for l in lots),
                  "last_run": TODAY.isoformat()})
    if new_lots:
        state["nifdc_last_updated"] = TODAY.isoformat()
    if cites_info and state.get("cites_latest_year") != cites_info[0]:
        state["cites_latest_year"], state["cites_last_updated"] = cites_info[0], TODAY.isoformat()
    if new_cites_version:
        state["cites_version"], state["cites_checked_new"] = new_cites_version, TODAY.isoformat()
    if nmpa:
        state["nmpa_latest"] = nmpa[-1]["period"]
    STATE_JSON.write_text(json.dumps(state, indent=2, default=list))

    all_rows = rows_before + [row]
    n_runs = len(all_rows)
    chart = make_chart(all_rows) if n_runs % 4 == 0 else None
    c4, c12 = history_change(all_rows, 4), history_change(all_rows, 12)
    L = [f"# Monkey Price Index {TODAY}", "",
         f"- **Index:** {idx:.2f} (base {base['date']} = {CFG['base_value']:.0f})",
         f"- **Median cynomolgus award price:** {median_cny:,.0f} CNY = {usd:,.0f} USD (USD/CNY {fx}, rate date {fx_date}); "
         f"{len(prim)} lots in trailing {CFG['window_days']}d",
         f"- **Change vs last run:** {fmt(pct(idx, float(prior['index_value'])) if prior and prior['index_value'] else None, 1, '%')}",
         f"- **4-week change:** {fmt(pct(idx, float(c4['index_value'])) if c4 else None, 1, '%')}  |  "
         f"**12-week change:** {fmt(pct(idx, float(c12['index_value'])) if c12 else None, 1, '%')}  (n/a = insufficient history)",
         "", "## Secondary series (trailing window median, CNY)"]
    for name, ls in sec.items():
        L.append(f"- {name}: {fmt(med(ls), 0)} (n={len(ls)})")
    L += ["", "## Supply (CITES live Macaca exports, annual, exporter-reported)"]
    if cites:
        for ex, name in CFG["cites_exporters"].items():
            yrs = sorted(y for e, y in cites if e == ex)[-3:]
            if yrs:
                L.append(f"- {name}: " + ", ".join(f"{y}: {cites[(ex, y)]:,.0f}" for y in yrs))
        L.append(f"- Lag: latest year {cites_info[0]} ({cites_info[1]}y behind)")
    else:
        L.append("- STALE: no CITES export file provided")
    L += ["", "## Cross-checks (validation only, not in index)"]
    L += [f"- {r}" for r in cro] if cro else ["- none provided"]
    L += ["", "## Unverified press / aggregator figures (never in index)"]
    L += [f"- {r}" for r in press] if press else ["- none"]
    L += ["", "## Context: CRO share prices (not in index)"]
    for tk, e in eq.items():
        pts = e["pts"]
        L.append(f"- {e['name']} {tk}: {pts[-1][1]:.2f} {e['ccy']} (close {pts[-1][0]}); "
                 f"4w {fmt(eq_change(pts, 4), 1, '%')}, 12w {fmt(eq_change(pts, 12), 1, '%')}")
    if not eq:
        L.append("- unavailable this run")
    L += ["", "## Context: NMPA clinical trials"]
    L += [f"- {nmpa[-1]}"] if nmpa else ["- none provided"]
    L += ["", "## Stale inputs"] + ([f"- {s}" for s in stale] or ["- none"])
    L += ["", "## Flags"] + ([f"- {s}" for s in flags] or ["- none"])
    L += ["", "## Notes"] + ([f"- {s}" for s in notes] or ["- none"])
    L += ["", "## Errors"] + ([f"- {e}" for e in errors] or ["- none"])
    if chart:
        L += ["", f"Chart: {chart.relative_to(ROOT)}"]
    write_report(L)
    if flags and any(f.startswith(("DUPLICATE", "OUTLIER", "UNLEDGERED")) for f in flags):
        pass
    return ("STALE (" + "; ".join(stale) + ")") if stale else "OK"


def main():
    try:
        status = run()
    except SourceError as e:
        write_report([f"# Monkey Price Index {TODAY}", "", "**No index written (append-only; no partial index).**",
                      "", "## Errors", f"- {e}"])
        status = f"FAILED ({e})"
    except Exception as e:  # network errors, parse errors: log, stop, write nothing to the index
        write_report([f"# Monkey Price Index {TODAY}", "", "**No index written.**", "", "## Errors",
                      f"- {type(e).__name__}: {e}", "```", traceback.format_exc(), "```"])
        status = f"FAILED ({type(e).__name__}: {e})"
    print(status)
    return 0 if status.startswith(("OK", "STALE")) else 1


if __name__ == "__main__":
    sys.exit(main())
