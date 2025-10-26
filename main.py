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

# ===================== ADD: TA helpers (без внешних библиотек) =====================
def ema(series, period):
    if not series or len(series) < period:
        return None
    k = 2 / (period + 1)
    e = series[0]
    for x in series[1:]:
        e = x * k + e * (1 - k)
    return e

def rsi_wilder(closes, period=14):
    if not closes or len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, period + 1):
        ch = closes[i] - closes[i - 1]
        gains.append(max(ch, 0.0))
        losses.append(max(-ch, 0.0))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    for i in range(period + 1, len(closes)):
        ch = closes[i] - closes[i - 1]
        gain = max(ch, 0.0)
        loss = max(-ch, 0.0)
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))

# ===================== ADD: Bybit доп. источники =====================
def get_spot_kline_closes(symbol, interval="60", limit=300):
    j = http_get(f"{BYBIT}/v5/market/kline",
                 {"category": "spot", "symbol": symbol, "interval": interval, "limit": limit})
    rows = j.get("result", {}).get("list", []) or []
    # Bybit отдаёт в порядке от нового к старому → разворачиваем
    rows = list(reversed(rows))
    closes = [float(r[4]) for r in rows]
    highs  = [float(r[2]) for r in rows]
    lows   = [float(r[3]) for r in rows]
    return closes, highs, lows

def get_linear_oi(symbol):
    try:
        j = http_get(f"{BYBIT}/v5/market/open-interest",
                     {"category": "linear", "symbol": symbol, "interval": "1h", "limit": "1"})
        lst = j.get("result", {}).get("list", []) or []
        return float(lst[-1].get("openInterest")) if lst else None
    except Exception:
        return None

def get_linear_oi_change_24h_pct(symbol):
    try:
        j = http_get(f"{BYBIT}/v5/market/open-interest",
                     {"category": "linear", "symbol": symbol, "interval": "1h", "limit": "24"})
        lst = j.get("result", {}).get("list", []) or []
        if len(lst) < 2:
            return None
        first = float(lst[0].get("openInterest", "nan"))
        last  = float(lst[-1].get("openInterest", "nan"))
        if not (first > 0 and (first == first) and (last == last)):
            return None
        return (last - first) / first * 100.0
    except Exception:
        return None

def get_recent_trades_ratio(symbol, limit=1000):
    # отношение объёма маркет-покупок к маркет-продажам за последние сделки
    try:
        j = http_get(f"{BYBIT}/v5/market/recent-trade",
                     {"category": "linear", "symbol": symbol, "limit": str(limit)})
        trades = j.get("result", {}).get("list", []) or []
        buy_vol = sell_vol = 0.0
        for t in trades:
            side = (t.get("side") or "").lower()  # "Buy"/"Sell"
            qty = float(t.get("qty", "0"))
            if side == "buy":
                buy_vol += qty
            elif side == "sell":
                sell_vol += qty
        if buy_vol + sell_vol == 0:
            return None
        return buy_vol / max(sell_vol, 1e-9)
    except Exception:
        return None

def get_futures_volume_24h(symbol):
    try:
        j = http_get(f"{BYBIT}/v5/market/tickers",
                     {"category": "linear", "symbol": symbol})
        it = (j.get("result", {}) or {}).get("list", []) or []
        return float(it[0].get("turnover24h", "nan")) if it else None
    except Exception:
        return None

def get_liquidations_24h_usd(symbol):
    # Не у всех аккаунтов/регионов эндпоинт доступен одинаково — суммируем грубо.
    try:
        j = http_get(f"{BYBIT}/v5/market/liquidation",
                     {"category": "linear", "symbol": symbol, "limit": "200"})
        lst = j.get("result", {}).get("list", []) or []
        total = 0.0
        for x in lst:
            qty = float(x.get("qty", "0"))
            price = float(x.get("price", "0"))
            total += qty * price
        return total if total > 0 else None
    except Exception:
        return None

# ===================== ADD: расширение snapshot в main() =====================
# ВСТАВЬ этот блок внутри main(), ПОСЛЕ того как у тебя уже есть:
#  - eth = get_spot_ticker("ETHUSDT")
#  - btc = get_spot_ticker("BTCUSDT")
#  - snapshot = {...}  (твой базовый словарь с calc/derivs/levels)

# --- TA по H1/H4 ---
try:
    cl1, hi1, lo1 = get_spot_kline_closes("ETHUSDT", interval="60", limit=300)  # H1 клоузы
except Exception:
    cl1, hi1, lo1 = [], [], []

rsi_1h = rsi_wilder(cl1, 14) if cl1 else None
# H4 сделаем даунсэмплом H1 (каждая 4-я свеча)
cl4 = cl1[::4] if cl1 else []
rsi_4h = rsi_wilder(cl4, 14) if cl4 and len(cl4) >= 15 else None

ema_50_1h  = ema(cl1[-200:], 50)  if cl1 and len(cl1) >= 50  else None
ema_200_1h = ema(cl1[-400:], 200) if cl1 and len(cl1) >= 200 else None
if ema_50_1h is not None and ema_200_1h is not None:
    ema_cross = "bullish" if ema_50_1h > ema_200_1h else ("bearish" if ema_50_1h < ema_200_1h else "flat")
else:
    ema_cross = None

# --- Деривативы / объёмы ---
oi_eth = get_linear_oi("ETHUSDT")
oi_btc = get_linear_oi("BTCUSDT")
oi_change_24h_pct = get_linear_oi_change_24h_pct("ETHUSDT")
taker_ratio = get_recent_trades_ratio("ETHUSDT", limit=1000)
fut_vol_24h = get_futures_volume_24h("ETHUSDT")
liq_24h_usd = get_liquidations_24h_usd("ETHUSDT")

# --- Диапазон / сессия ---
try:
    hi24 = float(snapshot["eth_spot"].get("high24h")) if snapshot.get("eth_spot") else None
    lo24 = float(snapshot["eth_spot"].get("low24h"))  if snapshot.get("eth_spot") else None
    range_mid = ((hi24 + lo24) / 2.0) if (hi24 is not None and lo24 is not None) else None
except Exception:
    range_mid = None

# последние ~6 H1-часов как "сессионные" экстремумы
try:
    session_high = max(hi1[-6:]) if hi1 else None
    session_low  = min(lo1[-6:]) if lo1 else None
    session_hl = [session_high, session_low] if (session_high is not None and session_low is not None) else None
except Exception:
    session_hl = None

# --- Обновляем snapshot новыми полями ---
snapshot.setdefault("calc", {}).update({
    "rsi_1h": rsi_1h,
    "rsi_4h": rsi_4h,
    "ema_50_1h": ema_50_1h,
    "ema_200_1h": ema_200_1h,
    "ema_cross": ema_cross
})

snapshot.setdefault("derivs", {}).update({
    "oi_eth": oi_eth if oi_eth is not None else snapshot["derivs"].get("oi_eth"),
    "oi_btc": oi_btc if oi_btc is not None else snapshot["derivs"].get("oi_btc"),
    "oi_change_24h_pct": oi_change_24h_pct,
    "taker_buy_sell_ratio": taker_ratio
})

snapshot["volume_analysis"] = {
    "spot_volume_24h": snapshot.get("eth_spot", {}).get("turnover24h"),
    "futures_volume_24h": fut_vol_24h,
    "cumulative_delta_1h": None,       # при желании можно реализовать
    "liquidations_24h_usd": liq_24h_usd
}

if "levels" not in snapshot:
    snapshot["levels"] = {}
if range_mid is not None:
    snapshot["levels"]["range_mid"] = range_mid
if session_hl:
    snapshot["levels"]["session_high_low"] = session_hl
# ===================== /END ADD =====================


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
