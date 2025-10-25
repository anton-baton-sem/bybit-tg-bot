# main.py — сохраняет рыночные снапшоты ETH/USDT и BTC/USDT с Bybit на GitHub
# Два запуска в день: 9:00 (MODE=forecast) и 21:00 (MODE=review)
# ENV: GITHUB_TOKEN, GITHUB_REPO, GITHUB_PATH, MODE

import os, json, math, statistics, base64, urllib.request, urllib.parse
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

BYBIT  = "https://api.bybit.com"

# ---------- вспомогательные ----------
def http_get(url, params=None, timeout=10):
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    headers = {'User-Agent': 'Mozilla/5.0 (compatible; RenderBot/1.0; +https://render.com)'}
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())
        
def fmt(x, d=2):
    try: return f"{x:,.{d}f}".replace(",", " ")
    except: return str(x)

# ---------- bybit: spot & derivatives ----------
def get_spot_ticker(symbol):
    j = http_get(f"{BYBIT}/v5/market/tickers", {"category":"spot","symbol":symbol})
    it = j["result"]["list"][0]
    return {
        "last": float(it["lastPrice"]),
        "high24h": float(it.get("highPrice24h","nan")),
        "low24h":  float(it.get("lowPrice24h","nan")),
        "turnover24h": float(it.get("turnover24h","nan")),
        "pcnt24h": float(it.get("price24hPcnt","0"))*100.0
    }

def get_spot_kline(symbol, interval="5", limit=288):
    j = http_get(f"{BYBIT}/v5/market/kline",
                 {"category":"spot","symbol":symbol,"interval":interval,"limit":limit})
    out=[]
    for row in j["result"]["list"]:
        ts,o,h,l,c,vol,turn = row
        out.append({"ts":int(ts),"o":float(o),"h":float(h),"l":float(l),"c":float(c),
                    "vol":float(vol),"turnover":float(turn)})
    out.sort(key=lambda x:x["ts"])
    return out

def calc_atr(klines, period=14):
    trs=[]; prev_c=None
    for k in klines:
        if prev_c is None: trs.append(k["h"]-k["l"])
        else: trs.append(max(k["h"]-k["l"], abs(k["h"]-prev_c), abs(k["l"]-prev_c)))
        prev_c=k["c"]
    if len(trs)<period: return float("nan")
    return sum(trs[-period:])/period

def calc_vwap_today(klines):
    today = datetime.utcnow().date()
    num=den=0.0
    for k in klines:
        dt=datetime.fromtimestamp(k["ts"]/1000, tz=timezone.utc).date()
        if dt==today:
            price=k["c"]; vol_q=k["turnover"]
            num+=price*vol_q; den+=vol_q
    return num/den if den>0 else float("nan")

def get_funding(symbol):
    j=http_get(f"{BYBIT}/v5/market/funding/history",
               {"category":"linear","symbol":symbol,"limit":1})
    lst=j["result"]["list"]
    return float(lst[0].get("fundingRate","0"))*100.0 if lst else float("nan")

def get_open_interest(symbol, interval="1h"):
    j=http_get(f"{BYBIT}/v5/market/open-interest",
               {"category":"linear","symbol":symbol,"interval":interval,"limit":1})
    lst=j["result"]["list"]
    return float(lst[0].get("openInterest","nan")) if lst else float("nan")

def get_orderbook_imbalance(symbol, depth=50):
    j=http_get(f"{BYBIT}/v5/market/orderbook",
               {"category":"spot","symbol":symbol,"limit":depth})
    bids=j["result"]["b"]; asks=j["result"]["a"]
    sb = sum(float(p[1]) for p in bids) if bids else 0.0
    sa = sum(float(p[1]) for p in asks) if asks else 0.0
    return (sb-sa)/(sb+sa)*100.0 if (sb+sa)>0 else 0.0

# ---------- GitHub upload ----------
def upload_to_github(repo, path, token, content, message="auto snapshot"):
    api_url = f"https://api.github.com/repos/{repo}/contents/{path}"
    headers = {
        "Authorization": f"token {token}",
        "Content-Type": "application/json"
    }
    # проверим, существует ли файл
    req = urllib.request.Request(api_url, headers=headers)
    try:
        with urllib.request.urlopen(req) as r:
            resp = json.loads(r.read().decode())
            sha = resp.get("sha")
    except:
        sha = None

    payload = {
        "message": message,
        "content": base64.b64encode(content.encode()).decode(),
        "branch": "main"
    }
    if sha: payload["sha"] = sha
    data = json.dumps(payload).encode()
    req = urllib.request.Request(api_url, data=data, headers=headers, method="PUT")
    with urllib.request.urlopen(req) as r:
        print("Uploaded to GitHub:", r.status)

# ---------- MAIN ----------
def main():
    mode = os.environ.get("MODE","forecast")  # forecast / review
    repo = os.environ["GITHUB_REPO"]
    path_prefix = os.environ.get("GITHUB_PATH","snapshots/")
    token = os.environ["GITHUB_TOKEN"]

    # Дата и время
    tz = ZoneInfo("Europe/Podgorica")
    now_local = datetime.now(tz)
    now_utc = datetime.utcnow()
    date_tag = now_local.strftime("%Y-%m-%d")

    # --- Получаем данные ---
    eth = get_spot_ticker("ETHUSDT")
    btc = get_spot_ticker("BTCUSDT")
    k5 = get_spot_kline("ETHUSDT","5",288)
    k60 = get_spot_kline("ETHUSDT","60",48)
    atr_d = calc_atr(k60[-24:],14) if len(k60)>=24 else calc_atr(k5,60)
    vwap  = calc_vwap_today(k5)
    f_eth = get_funding("ETHUSDT")
    f_btc = get_funding("BTCUSDT")
    oi_eth = get_open_interest("ETHUSDT","1h")
    oi_btc = get_open_interest("BTCUSDT","1h")
    imb = get_orderbook_imbalance("ETHUSDT",50)

    snapshot = {
        "timestamp_utc": now_utc.strftime("%Y-%m-%d %H:%M:%S"),
        "timestamp_local": now_local.strftime("%Y-%m-%d %H:%M:%S (%Z)"),
        "mode": mode,
        "eth_spot": eth,
        "btc_spot": btc,
        "calc": {"atr_1d": atr_d, "vwap_today": vwap, "orderbook_imbalance_pct": imb},
        "derivs": {
            "funding_eth_pct": f_eth,
            "funding_btc_pct": f_btc,
            "oi_eth": oi_eth,
            "oi_btc": oi_btc
        },
        "levels": {"support": [3780,3700], "resistance": [3950,4050]}
    }

    filename = f"{date_tag}_{mode}.json"
    local_path = f"/tmp/{filename}"
    with open(local_path, "w") as f:
        json.dump(snapshot, f, indent=2)
    print("Snapshot saved locally:", local_path)

    # --- Загрузка в GitHub ---
    remote_path = f"{path_prefix}{filename}"
    with open(local_path, "r") as f:
        content = f.read()
    upload_to_github(repo, remote_path, token, content, f"auto snapshot {mode} {date_tag}")

    print("Done", mode, date_tag)

if __name__ == "__main__":
    main()
