#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bybit snapshot → GitHub
Собирает метрики по ETH/USDT и BTC/USDT (Bybit v5), считает TA и пушит JSON в репозиторий.

ENV (Render → Environment):
  GITHUB_TOKEN   : GitHub PAT (contents: write)
  GITHUB_REPO    : "anton-baton-sem/bybit-tg-bot"
  GITHUB_BRANCH  : "main"
  GITHUB_PATH    : "snapshots"
  MODE           : "forecast" | "review"   (по умолч. forecast)
  TZ             : "Europe/Podgorica"

Авторский стиль: без внешних зависимостей (urllib+json), аккуратные try/except, NaN→None.
"""

import os, sys, json, math, base64, time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from urllib import request, parse, error

# ------------------ Константы ------------------
BYBIT = "https://api.bybit.com"
UA    = "Mozilla/5.0 (compatible; RenderBot/1.0; +https://render.com)"

REPO   = os.getenv("GITHUB_REPO",   "anton-baton-sem/bybit-tg-bot")
BRANCH = os.getenv("GITHUB_BRANCH", "main")
SNPATH = os.getenv("GITHUB_PATH",   "snapshots")
MODE   = (os.getenv("MODE") or "forecast").strip().lower()
TZ     = os.getenv("TZ", "Europe/Podgorica")
PAT    = os.getenv("GITHUB_TOKEN", "").strip()

ETH = "ETHUSDT"
BTC = "BTCUSDT"

# ------------------ Утилиты ------------------
def http_get(url: str, params: dict | None = None, timeout: int = 12) -> dict:
    if params:
        url = f"{url}?{parse.urlencode(params)}"
    req = request.Request(url, headers={"User-Agent": UA})
    with request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))

def safe_float(x, default=None):
    try:
        v = float(x)
        if math.isnan(v) or math.isinf(v):
            return default
        return v
    except Exception:
        return default

def now_local_utc():
    tz = ZoneInfo(TZ)
    dt_local = datetime.now(tz)
    dt_utc   = datetime.now(timezone.utc)
    return dt_local, dt_utc

def ymd_local():
    return now_local_utc()[0].date().isoformat()

# ------------------ Bybit spot helpers ------------------
def get_spot_ticker(symbol: str) -> dict:
    j = http_get(f"{BYBIT}/v5/market/tickers", {"category":"spot","symbol":symbol})
    it = (j.get("result", {}) or {}).get("list", []) or []
    if not it:
        return {}
    it = it[0]
    return {
        "last":      safe_float(it.get("lastPrice")),
        "high24h":   safe_float(it.get("highPrice24h")),
        "low24h":    safe_float(it.get("lowPrice24h")),
        "turnover24h": safe_float(it.get("turnover24h")),
        "pct24h":    safe_float(it.get("price24hPcnt"), 0.0) * 100.0 if it.get("price24hPcnt") else None
    }

def get_spot_kline(symbol: str, interval="60", limit=300):
    # Bybit возвращает kline от НОВОГО к СТАРОМУ → разворачиваем
    j = http_get(f"{BYBIT}/v5/market/kline",
                 {"category":"spot","symbol":symbol,"interval":interval,"limit":limit})
    lst = (j.get("result", {}) or {}).get("list", []) or []
    lst = list(reversed(lst))
    closes = [safe_float(x[4]) for x in lst if safe_float(x[4]) is not None]
    highs  = [safe_float(x[2]) for x in lst if safe_float(x[2]) is not None]
    lows   = [safe_float(x[3]) for x in lst if safe_float(x[3]) is not None]
    times  = [int(x[0]) for x in lst]  # ms
    return {"closes":closes, "highs":highs, "lows":lows, "times":times}

# ------------------ Простая TA без внешних либ ------------------
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
        gain = max(ch, 0.0); loss = max(-ch, 0.0)
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))

# ------------------ Bybit derivatives helpers ------------------
def get_funding(symbol: str) -> float | None:
    # Funding проценты для линейных перпов
    j = http_get(f"{BYBIT}/v5/market/tickers", {"category":"linear","symbol":symbol})
    it = (j.get("result", {}) or {}).get("list", []) or []
    if not it:
        return None
    return safe_float(it[0].get("fundingRate")) * 100.0 if it[0].get("fundingRate") else None

def get_open_interest(symbol: str) -> float | None:
    # Последняя точка OI (linear)
    j = http_get(f"{BYBIT}/v5/market/open-interest",
                 {"category":"linear","symbol":symbol,"interval":"1h","limit":"1"})
    it = (j.get("result", {}) or {}).get("list", []) or []
    if not it:
        return None
    return safe_float(it[-1].get("openInterest"))

def get_open_interest_change_24h_pct(symbol: str) -> float | None:
    j = http_get(f"{BYBIT}/v5/market/open-interest",
                 {"category":"linear","symbol":symbol,"interval":"1h","limit":"24"})
    it = (j.get("result", {}) or {}).get("list", []) or []
    if len(it) < 2:
        return None
    first = safe_float(it[0].get("openInterest"))
    last  = safe_float(it[-1].get("openInterest"))
    if not first or not last:
        return None
    return (last - first) / first * 100.0

def get_recent_trades_ratio(symbol: str, limit=1000) -> float | None:
    # отношение объёма маркет-покупок к маркет-продажам за последние сделки
    j = http_get(f"{BYBIT}/v5/market/recent-trade",
                 {"category":"linear","symbol":symbol,"limit":str(limit)})
    trades = (j.get("result", {}) or {}).get("list", []) or []
    buy_vol = sell_vol = 0.0
    for t in trades:
        side = (t.get("side") or "").lower()
        qty = safe_float(t.get("qty"), 0.0) or 0.0
        if side == "buy":   buy_vol  += qty
        elif side == "sell": sell_vol += qty
    if buy_vol + sell_vol == 0:
        return None
    return buy_vol / max(sell_vol, 1e-9)

def get_futures_turnover_24h(symbol: str) -> float | None:
    j = http_get(f"{BYBIT}/v5/market/tickers", {"category":"linear","symbol":symbol})
    it = (j.get("result", {}) or {}).get("list", []) or []
    if not it:
        return None
    return safe_float(it[0].get("turnover24h"))

def get_liquidations_24h_usd(symbol: str) -> float | None:
    # агрегированно по последним записям
    try:
        j = http_get(f"{BYBIT}/v5/market/liquidation",
                     {"category":"linear","symbol":symbol,"limit":"200"})
        lst = (j.get("result", {}) or {}).get("list", []) or []
        total = 0.0
        for x in lst:
            qty   = safe_float(x.get("qty"), 0.0) or 0.0
            price = safe_float(x.get("price"), 0.0) or 0.0
            total += qty * price
        return total if total > 0 else None
    except Exception:
        return None

# ------------------ Расчёты ATR и VWAP ------------------
def calc_atr_1d(highs, lows, closes) -> float | None:
    # простой ATR по последнему дню (приближённо: средняя разница High-Low)
    if not highs or not lows:
        return None
    return sum((h - l) for h, l in zip(highs[-24:], lows[-24:])) / min(24, len(highs))

def calc_vwap_today(times_ms, highs, lows, closes) -> float | None:
    # упрощённый VWAP по сегодняшним H1 свечам: (H+L+C)/3 * volume_proxy(=1)
    if not times_ms or not closes:
        return None
    # выделим сегодняшние по локальной дате
    tz = ZoneInfo(TZ)
    today = datetime.now(tz).date()
    v_sum = 0.0
    pv_sum = 0.0
    for t, h, l, c in zip(times_ms, highs, lows, closes):
        dt = datetime.fromtimestamp(t/1000, tz)
        if dt.date() != today:
            continue
        price_typ = (h + l + c) / 3.0
        vol_proxy = 1.0  # без реального объёма: равные веса
        pv_sum += price_typ * vol_proxy
        v_sum  += vol_proxy
    return (pv_sum / v_sum) if v_sum > 0 else None

# ------------------ GitHub: upload ------------------
def github_put_json(repo: str, branch: str, path: str, data: dict, pat: str):
    """
    Создаёт/обновляет файл через GitHub Contents API.
    """
    api = f"https://api.github.com/repos/{repo}/contents/{path}"
    body = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    b64  = base64.b64encode(body).decode("ascii")

    # Получаем sha, если файл существует
    sha = None
    try:
        req = request.Request(f"{api}?ref={branch}", headers={"User-Agent": UA})
        with request.urlopen(req, timeout=12) as r:
            meta = json.loads(r.read().decode("utf-8"))
            sha = meta.get("sha")
    except Exception:
        sha = None

    payload = {
        "message": f"auto snapshot {os.path.basename(path).replace('.json','')}",
        "content": b64,
        "encoding": "base64",   # 🔥 обязательно
        "branch": branch
    }
    if sha:
        payload["sha"] = sha

    data_bytes = json.dumps(payload).encode("utf-8")
    headers = {
        "User-Agent": UA,
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {pat}"
    }

    req = request.Request(api, data=data_bytes, headers=headers, method="PUT")
    with request.urlopen(req, timeout=20) as r:
        resp = json.loads(r.read().decode("utf-8"))
        print("GitHub upload ok:", resp.get("content", {}).get("path"))

# ------------------ main ------------------
def main():
    dt_local, dt_utc = now_local_utc()
    today = dt_local.date().isoformat()
    print("Running snapshot for:", today, MODE, flush=True)

    # ---- spot ----
    eth = get_spot_ticker(ETH)
    btc = get_spot_ticker(BTC)

    k1 = get_spot_kline(ETH, interval="60", limit=300)
    closes = k1["closes"]; highs = k1["highs"]; lows = k1["lows"]; times_ms = k1["times"]

    atr_1d  = calc_atr_1d(highs, lows, closes)
    vwap_td = calc_vwap_today(times_ms, highs, lows, closes)

    # Небольшой суррогат дисбаланса ордербука (если нет своего стакана):
    # используем знак последнего движения close (очень грубо). Лучше заменить на свой стакан.
    orderbook_imbalance_pct = 0.0
    if len(closes) >= 2:
        diff = closes[-1] - closes[-2]
        orderbook_imbalance_pct = (1 if diff > 0 else -1) * 1.0  # ±1% как плейсхолдер

    # ---- derivatives baseline ----
    funding_eth = get_funding(ETH)
    funding_btc = get_funding(BTC)
    oi_eth = get_open_interest(ETH)
    oi_btc = get_open_interest(BTC)

    # ---- базовый skeleton snapshot ----
    snapshot = {
        "timestamp_utc":   dt_utc.replace(tzinfo=timezone.utc).strftime("%Y-%m-%d %H:%M:%S (%Z)"),
        "timestamp_local": dt_local.strftime("%Y-%m-%d %H:%M:%S (%Z)"),
        "mode": MODE,
        "eth_spot": eth,
        "btc_spot": btc,
        "calc": {
            "atr_1d": atr_1d,
            "vwap_today": vwap_td,
            "orderbook_imbalance_pct": orderbook_imbalance_pct
        },
        "derivs": {
            "funding_eth_pct": funding_eth,
            "funding_btc_pct": funding_btc,
            "oi_eth": oi_eth,
            "oi_btc": oi_btc
        },
        "levels": {
            # заполни реальные уровни своей логикой/скриптом; оставляю плейсхолдеры
            "support": [3780, 3700],
            "resistance": [3950, 4050]
        }
    }

    # ---- ДОБАВЛЯЕМ расширенные метрики (RSI/EMA/OI change/taker/объёмы/ликвидации) ----
    # TA H1/H4
    rsi_1h  = rsi_wilder(closes, 14) if closes else None
    cl4     = closes[::4] if closes else []
    rsi_4h  = rsi_wilder(cl4, 14) if cl4 and len(cl4) >= 15 else None
    ema50   = ema(closes[-200:], 50)   if closes and len(closes) >= 50  else None
    ema200  = ema(closes[-400:], 200)  if closes and len(closes) >= 200 else None
    ema_x   = None
    if ema50 is not None and ema200 is not None:
        ema_x = "bullish" if ema50 > ema200 else ("bearish" if ema50 < ema200 else "flat")

    # деривы/объёмы
    oi_change_24h_pct = get_open_interest_change_24h_pct(ETH)
    taker_ratio       = get_recent_trades_ratio(ETH, limit=1000)
    fut_turnover_24h  = get_futures_turnover_24h(ETH)
    liq_24h_usd       = get_liquidations_24h_usd(ETH)

    # диапазон/сессия
    try:
        hi24 = safe_float(eth.get("high24h"))
        lo24 = safe_float(eth.get("low24h"))
        range_mid = ((hi24 + lo24) / 2.0) if (hi24 is not None and lo24 is not None) else None
    except Exception:
        range_mid = None

    session_h = max(highs[-6:]) if highs else None
    session_l = min(lows[-6:])  if lows else None
    session_hl = [session_h, session_l] if (session_h is not None and session_l is not None) else None

    # вклеиваем в snapshot
    snapshot["calc"].update({
        "rsi_1h": rsi_1h,
        "rsi_4h": rsi_4h,
        "ema_50_1h": ema50,
        "ema_200_1h": ema200,
        "ema_cross": ema_x
    })
    snapshot["derivs"].update({
        "oi_change_24h_pct": oi_change_24h_pct,
        "taker_buy_sell_ratio": taker_ratio
    })
    snapshot["volume_analysis"] = {
        "spot_volume_24h": eth.get("turnover24h") if isinstance(eth, dict) else None,
        "futures_volume_24h": fut_turnover_24h,
        "cumulative_delta_1h": None,     # можно реализовать отдельно
        "liquidations_24h_usd": liq_24h_usd
    }
    if range_mid is not None:
        snapshot["levels"]["range_mid"] = range_mid
    if session_hl:
        snapshot["levels"]["session_high_low"] = session_hl

    # ---- сохраняем локально (на всякий случай) ----
    fname = f"{today}_{MODE}.json"
    local_path = f"/tmp/{fname}"
    with open(local_path, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, separators=(",",":"))
    print("Snapshot saved locally:", local_path, flush=True)

    # ---- загружаем в GitHub ----
    if not PAT:
        print("WARNING: GITHUB_TOKEN is empty — upload skipped", file=sys.stderr)
        return

    repo_path = f"{SNPATH}/{fname}"
    github_put_json(REPO, BRANCH, repo_path, snapshot, PAT)
    print("Uploaded to GitHub:", f"{REPO}/{repo_path}", flush=True)
    print("Done", today, MODE, flush=True)


if __name__ == "__main__":
    try:
        main()
    except error.HTTPError as e:
        print("HTTPError:", e.code, e.reason, file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print("Error:", repr(e), file=sys.stderr)
        sys.exit(1)
