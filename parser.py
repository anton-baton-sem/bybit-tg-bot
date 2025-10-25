# parser.py — агрегирует snapshots/*.json в analytics/daily_summary.csv
# и генерирует analytics/README.md с таблицей ссылок и метрик.
# ENV: GITHUB_TOKEN, GITHUB_REPO, GITHUB_PATH (напр. "snapshots/")

import os, json, base64, csv, io, urllib.request, urllib.parse
from collections import defaultdict

GITHUB_API = "https://api.github.com"

def gh_request(url, token, method="GET", payload=None):
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "snapshot-parser/1.0"
    }
    data = None
    if payload is not None:
        data = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode())

def list_snapshot_files(repo, path, token):
    url = f"{GITHUB_API}/repos/{repo}/contents/{path}"
    items = gh_request(url, token)
    return [it for it in items if it.get("type")=="file" and it["name"].endswith(".json")]

def get_file_json(repo, path, token):
    url = f"{GITHUB_API}/repos/{repo}/contents/{path}"
    item = gh_request(url, token)
    raw = base64.b64decode(item.get("content","")).decode()
    return json.loads(raw)

def upload_file(repo, path, token, content_str, message="update file"):
    get_url = f"{GITHUB_API}/repos/{repo}/contents/{path}"
    sha = None
    try:
        res = gh_request(get_url, token)
        sha = res.get("sha")
    except Exception:
        sha = None
    payload = {
        "message": message,
        "content": base64.b64encode(content_str.encode()).decode(),
        "branch": "main"
    }
    if sha:
        payload["sha"] = sha
    return gh_request(get_url, token, method="PUT", payload=payload)

def safe(d, path, default=""):
    cur = d
    try:
        for p in path: cur = cur[p]
        return cur if cur is not None else default
    except: return default

def pct(a, b):
    try:
        return (float(b)/float(a)-1.0)*100.0
    except: return ""

def main():
    token = os.environ["GITHUB_TOKEN"]
    repo  = os.environ["GITHUB_REPO"]
    snap_path = os.environ.get("GITHUB_PATH","snapshots/").rstrip("/") + "/"

    files = list_snapshot_files(repo, snap_path, token)

    by_date = defaultdict(dict)
    for it in files:
        name = it["name"]  # YYYY-MM-DD_mode.json
        if "_" not in name: continue
        date_part, rest = name.split("_", 1)
        mode = "forecast" if "forecast" in rest else ("review" if "review" in rest else None)
        if not mode: continue
        data = get_file_json(repo, f"{snap_path}{name}", token)
        by_date[date_part][mode] = data

    # ---- CSV ----
    headers = [
        "date",
        "eth_last_forecast","eth_last_review","eth_change_pct",
        "btc_last_forecast","btc_last_review","btc_change_pct",
        "funding_eth_forecast","funding_eth_review",
        "oi_eth_forecast","oi_eth_review",
        "atr_1d_forecast","vwap_review","orderbook_imbalance_forecast",
        "support_lvl1","support_lvl2","resist_lvl1","resist_lvl2"
    ]
    out_csv = io.StringIO()
    w = csv.writer(out_csv); w.writerow(headers)

    # ---- Markdown README ----
    rows_md = []
    rows_md.append("# Ежедневная сводка снапшотов\n")
    rows_md.append("> Автоматически сгенерировано парсером из папки `snapshots/`.\n\n")
    rows_md.append("| Дата | Forecast | Review | ETH Δ% | BTC Δ% | Funding ETH f/r | OI ETH f/r | ATR(1D) | VWAP (review) | Levels |\n")
    rows_md.append("|---|---|---|---:|---:|---:|---:|---:|---:|---|\n")

    def n(x):
        try: return float(x)
        except: return ""

    for date_key in sorted(by_date.keys()):
        f = by_date[date_key].get("forecast", {})
        r = by_date[date_key].get("review",   {})

        eth_f = safe(f, ["eth_spot","last"], ""); eth_r = safe(r, ["eth_spot","last"], "")
        btc_f = safe(f, ["btc_spot","last"], ""); btc_r = safe(r, ["btc_spot","last"], "")
        d_eth = pct(eth_f, eth_r); d_btc = pct(btc_f, btc_r)

        row = [
            date_key,
            n(eth_f), n(eth_r), d_eth,
            n(btc_f), n(btc_r), d_btc,
            n(safe(f, ["derivs","funding_eth_pct"], "")),
            n(safe(r, ["derivs","funding_eth_pct"], "")),
            n(safe(f, ["derivs","oi_eth"], "")),
            n(safe(r, ["derivs","oi_eth"], "")),
            n(safe(f, ["calc","atr_1d"], "")),
            n(safe(r, ["calc","vwap_today"], "")),
            n(safe(f, ["calc","orderbook_imbalance_pct"], "")),
            n(safe(f, ["levels","support",0], "")),
            n(safe(f, ["levels","support",1], "")),
            n(safe(f, ["levels","resistance",0], "")),
            n(safe(f, ["levels","resistance",1], "")),
        ]
        w.writerow(row)

        # Markdown-строка
        link_f = f"[forecast]({snap_path}{date_key}_forecast.json)"
        link_r = f"[review]({snap_path}{date_key}_review.json)" if "review" in by_date[date_key] else "—"
        levels = f"S: {safe(f,['levels','support',0],'')}/{safe(f,['levels','support',1],'')} • R: {safe(f,['levels','resistance',0],'')}/{safe(f,['levels','resistance',1],'')}"
        rows_md.append(
            f"| {date_key} | {link_f} | {link_r} | "
            f"{'' if d_eth=='' else f'{d_eth:.2f}%'} | {'' if d_btc=='' else f'{d_btc:.2f}%'} | "
            f"{safe(f,['derivs','funding_eth_pct'],'')} / {safe(r,['derivs','funding_eth_pct'],'')} | "
            f"{safe(f,['derivs','oi_eth'],'')} / {safe(r,['derivs','oi_eth'],'')} | "
            f"{safe(f,['calc','atr_1d'],'')} | {safe(r,['calc','vwap_today'],'')} | {levels} |\n"
        )

    csv_str = out_csv.getvalue(); out_csv.close()
    md_str  = "".join(rows_md)

    upload_file(repo, "analytics/daily_summary.csv", token, csv_str, "build analytics/daily_summary.csv")
    upload_file(repo, "analytics/README.md",       token, md_str,  "build analytics/README.md")
    print("OK: analytics CSV & README updated")

if __name__ == "__main__":
    main()
