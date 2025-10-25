# parser.py — агрегирует snapshots/*.json в analytics/daily_summary.csv и пушит в GitHub
# ENV: GITHUB_TOKEN, GITHUB_REPO, GITHUB_PATH (путь к папке со снапшотами, напр. "snapshots/")

import os, json, base64, csv, io, urllib.request, urllib.parse
from collections import defaultdict
from datetime import datetime

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
    files = [it for it in items if it.get("type")=="file" and it["name"].endswith(".json")]
    return files

def get_file_json(repo, path, token):
    url = f"{GITHUB_API}/repos/{repo}/contents/{path}"
    item = gh_request(url, token)
    content_b64 = item.get("content","")
    raw = base64.b64decode(content_b64).decode()
    return json.loads(raw)

def upload_file(repo, path, token, content_str, message="update file"):
    # получим sha, если файл есть
    get_url = f"{GITHUB_API}/repos/{repo}/contents/{path}"
    sha = None
    try:
        res = gh_request(get_url, token)
        sha = res.get("sha")
    except Exception:
        sha = None
    put_url = get_url
    payload = {
        "message": message,
        "content": base64.b64encode(content_str.encode()).decode(),
        "branch": "main"
    }
    if sha:
        payload["sha"] = sha
    return gh_request(put_url, token, method="PUT", payload=payload)

def safe_get(d, path, default=""):
    cur = d
    try:
        for p in path:
            cur = cur[p]
        return cur if cur is not None else default
    except Exception:
        return default

def main():
    token = os.environ["GITHUB_TOKEN"]
    repo  = os.environ["GITHUB_REPO"]
    snap_path = os.environ.get("GITHUB_PATH","snapshots/").rstrip("/") + "/"

    files = list_snapshot_files(repo, snap_path, token)
    # группируем по дате
    by_date = defaultdict(dict)
    for it in files:
        name = it["name"]  # YYYY-MM-DD_mode.json
        if "_" not in name: 
            continue
        date_part, rest = name.split("_", 1)
        mode = "forecast" if "forecast" in rest else ("review" if "review" in rest else None)
        if not mode:
            continue
        data = get_file_json(repo, f"{snap_path}{name}", token)
        by_date[date_part][mode] = data

    # сформируем CSV
    headers = [
        "date",
        "eth_last_forecast","eth_last_review","eth_change_pct",
        "btc_last_forecast","btc_last_review","btc_change_pct",
        "funding_eth_forecast","funding_eth_review",
        "oi_eth_forecast","oi_eth_review",
        "atr_1d_forecast","vwap_review",
        "orderbook_imbalance_forecast",
        "support_lvl1","support_lvl2","resist_lvl1","resist_lvl2"
    ]
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(headers)

    def num(x):
        try: return float(x)
        except: return ""

    for date_key in sorted(by_date.keys()):
        f = by_date[date_key].get("forecast", {})
        r = by_date[date_key].get("review",   {})

        eth_f = safe_get(f, ["eth_spot","last"], "")
        eth_r = safe_get(r, ["eth_spot","last"], "")
        btc_f = safe_get(f, ["btc_spot","last"], "")
        btc_r = safe_get(r, ["btc_spot","last"], "")

        def pct(a,b):
            try:
                if a=="" or b=="":
                    return ""
                return (float(b)/float(a)-1.0)*100.0
            except:
                return ""

        row = [
            date_key,
            num(eth_f), num(eth_r), pct(eth_f, eth_r),
            num(btc_f), num(btc_r), pct(btc_f, btc_r),
            num(safe_get(f, ["derivs","funding_eth_pct"], "")),
            num(safe_get(r, ["derivs","funding_eth_pct"], "")),
            num(safe_get(f, ["derivs","oi_eth"], "")),
            num(safe_get(r, ["derivs","oi_eth"], "")),
            num(safe_get(f, ["calc","atr_1d"], "")),
            num(safe_get(r, ["calc","vwap_today"], "")),
            num(safe_get(f, ["calc","orderbook_imbalance_pct"], "")),
            num(safe_get(f, ["levels","support",0], "")),
            num(safe_get(f, ["levels","support",1], "")),
            num(safe_get(f, ["levels","resistance",0], "")),
            num(safe_get(f, ["levels","resistance",1], "")),
        ]
        writer.writerow(row)

    csv_str = output.getvalue()
    output.close()

    # заливаем в репозиторий
    out_path = "analytics/daily_summary.csv"
    upload_file(repo, out_path, token, csv_str, "build analytics/daily_summary.csv")
    print("OK: analytics/daily_summary.csv updated")

if __name__ == "__main__":
    main()
