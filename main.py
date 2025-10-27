# main.py — Bybit Snapshot (forecast & review)
# версия 2025-10-27

import os, json, math, time, base64, urllib.request, urllib.parse
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

# --------- Константы ---------
BYBIT = "https://api.bybit.com"
REPO = os.environ.get("GITHUB_REPO", "anton-baton-sem/bybit-tg-bot")
BRANCH = os.environ.get("GITHUB_BRANCH", "main")
MODE = os.environ.get("MODE", "forecast").strip().lower()  # forecast | review

# --------- Время / Дата ---------
LOCAL_TZ = ZoneInfo("Europe/Podgorica")

def now_local():
    return datetime.now(LOCAL_TZ)

def today_local_str():
    return now_local().date().isoformat()

def local_midnight_ms(d: datetime | None = None) -> int:
    """Метка миллисекунд локальной полуночи (UTC)."""
    d = d or now_local()
    local_midnight = datetime(d.year, d.month, d.day, 0, 0, 0, tzinfo=LOCAL_TZ)
    return int(local_midnight.astimezone(timezone.utc).timestamp() * 1000)

def local_time_to_utc_ms(dt_local: datetime) -> int:
    return int(dt_local.astimezone(timezone.utc).timestamp() * 1000)

# --------- HTTP утилиты ---------
def http_get_json(url, params=None, timeout=10, retries=(1,3,7)):
    q = dict(params or {})
    q["nocache"] = "1"
    q["ts"] = str(int(time.time()))
    full = f"{url}?{urllib.parse.urlencode(q)}"
    headers = {
        "User-Agent": "RenderBot/1.0 (+https://render.com)",
        "Accept": "application/json",
        "Cache-Control": "no-store",
        "Pragma": "no-cache",
    }
    last_err = None
    for backoff in (0,)+tuple(retries):
        if backoff:
            time.sleep(backoff)
        try:
            req = urllib.request.Request(full, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode())
        except Exception as e:
            last_err = e
    raise last_err

def github_put_file(path_rel: str, content_bytes: bytes, message: str):
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        print("⚠️ No GITHUB_TOKEN — skip upload")
        return
    url = f"https://api.github.com/repos/{REPO}/contents/{path_rel}"
    body = json.dumps({
        "message": message,
        "content": base64.b64encode(content_bytes).decode(),
        "branch": BRANCH,
    }).encode()
    req = urllib.request.Request(url, data=body, method="PUT", headers={
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "RenderBot/1.0"
    })
    with urllib.request.urlopen(req) as r:
        print(f"GitHub upload: HTTP {r.status} {path_rel}")

def github_get_raw_snapshot(fname: str) -> dict | None:
    """Пробуем RAW, затем API (base64). Возвращаем dict или None."""
    raw_url = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}/snapshots/{fname}"
    try:
        req = urllib.request.Request(raw_url, headers={"User-Agent":"RenderBot/1.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode())
    except Exception:
        pass
    # API fallback
    api_url = f"https://api.github.com/repos/{REPO}/contents/snapshots/{fname}?ref={BRANCH}"
    try:
        req = urllib.request.Request(api_url, headers={"User-Agent":"RenderBot/1.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            j = json.loads(r.read().decode())
            if "content" in j:
                return json.loads(base64.b64decode(j["content"]).decode())
    except Exception:
        pass
    return None

# --------- Bybit helpers ---------
def get_spot_last(symbol: str):
    j = http_get_json(f"{BYBIT}/v5/market/tickers", {"category":"spot","symbol":symbol})
    it = j.get("result", {}).get("list", [{}])[0]
    last = float(it.get("lastPrice", "nan"))
    ts_ms = int(time.time()*1000)
    return last, ts_ms

def get_kline_last_close(symbol: str):
    j = http_get_json(f"{BYBIT}/v5/market/kline",
                      {"category":"spot","symbol":symbol,"interval":"1","limit":"1"})
    it = j.get("result", {}).get("list", [[]])
    if it and it[0]:
        close = float(it[0][4])
        start_ms = int(it[0][0])
        return close, start_ms
    return float("nan"), 0

def get_klines_range(symbol: str, interval: str, start_ms: int, end_ms: int) -> list:
    """Возвращает список свечей (как в Bybit v5). Для суток достаточно interval=5."""
    j = http_get_json(f"{BYBIT}/v5/market/kline",
                      {"category":"spot","symbol":symbol,"interval":interval,
                       "start":str(start_ms),"end":str(end_ms),"limit":"1000"})
    return j.get("result", {}).get("list", [])

def within(x, ref, tol):
    return math.isfinite(x) and math.isfinite(ref) and abs(x-ref) <= tol

# --------- Safe spot fetch (ETH/BTC) ---------
def fetch_spot_safe(snapshot, symbol="ETHUSDT", key="eth_spot"):
    last, last_ts = get_spot_last(symbol)
    ref_close, ref_ts = get_kline_last_close(symbol)
    atr = float(snapshot.get("calc", {}).get("atr_1d", "nan"))
    vwap = float(snapshot.get("calc", {}).get("vwap_today", "nan"))

    tol_pct = 0.008
    tol_abs = max(0.0, 2.0*atr if math.isfinite(atr) else 0.0)
    tol = max(tol_abs, last*tol_pct if math.isfinite(last) else 0.0)

    ok_close = within(last, ref_close, tol)
    ok_vwap  = True if not math.isfinite(vwap) else within(last, vwap, max(tol, last*0.01))

    invalid = False
    if not (ok_close and ok_vwap):
        last2, last2_ts = get_spot_last(symbol)
        ref_close2, ref_ts2 = get_kline_last_close(symbol)
        if not (within(last2, ref_close2, tol) or (math.isfinite(vwap) and within(last2, vwap, max(tol, last2*0.01)))):
            invalid = True
        last, last_ts = last2, last2_ts
        ref_close, ref_ts = ref_close2, ref_ts2

    snapshot[key] = round(float(last), 2)
    snapshot.setdefault("meta", {}).update({
        f"{key}_source": "bybit_spot_lastPrice",
        f"{key}_time_ms": int(last_ts),
        f"{key}_ref_close": round(float(ref_close), 2) if math.isfinite(ref_close) else None,
        f"{key}_ref_time_ms": int(ref_ts) if ref_ts else None,
        "tz_local": "Europe/Podgorica",
        "snapshot_date_local": today_local_str(),
    })
    if invalid:
        snapshot["meta"]["invalid"] = True
    return snapshot

# --------- Заглушки вычислений (подставь свои реальные расчёты) ---------
def compute_calc(snapshot):
    snapshot["calc"] = snapshot.get("calc", {}) | {
        "atr_1d": snapshot.get("calc", {}).get("atr_1d", 16.5),
        "vwap_today": snapshot.get("calc", {}).get("vwap_today", 4135.2),
        "orderbook_imbalance_pct": snapshot.get("calc", {}).get("orderbook_imbalance_pct", -1.8),
        "rsi_1h": snapshot.get("calc", {}).get("rsi_1h", 52.4),
        "rsi_4h": snapshot.get("calc", {}).get("rsi_4h", 58.1),
        "ema_20_1h": snapshot.get("calc", {}).get("ema_20_1h", 4120.5),
        "ema_50_1h": snapshot.get("calc", {}).get("ema_50_1h", 4102.3),
        "ema_200_1h": snapshot.get("calc", {}).get("ema_200_1h", 4045.0),
        "ema_cross": snapshot.get("calc", {}).get("ema_cross", "bullish"),
        "macd_hist_1h": snapshot.get("calc", {}).get("macd_hist_1h", -0.12),
    }
    return snapshot

def compute_derivs(snapshot):
    snapshot["derivs"] = snapshot.get("derivs", {}) | {
        "funding_eth_pct": 0.009,
        "funding_btc_pct": 0.011,
        "oi_eth": 3.42,
        "oi_btc": 5.12,
        "oi_change_24h_pct": 0.8,
        "taker_buy_sell_ratio": 1.04,
        "liquidations_buy_24h_usd": 3.1e6,
        "liquidations_sell_24h_usd": 2.8e6,
    }
    return snapshot

def compute_volumes(snapshot):
    snapshot["volume_analysis"] = snapshot.get("volume_analysis", {}) | {
        "spot_volume_24h": 531_000_000,
        "futures_volume_24h": 1_240_000_000,
        "cumulative_delta_1h": -3_800_000,
        "liquidations_24h_usd": 5.9e6,
    }
    return snapshot

def compute_levels(snapshot):
    snapshot["levels"] = snapshot.get("levels", {}) | {
        "support": [4050, 3970],
        "resistance": [4250, 4320],
        "range_mid": 4140,
        "session_high_low": [4046, 4253],
    }
    return snapshot

# --------- BUILD: forecast ---------
def build_forecast_snapshot():
    snap = {
        "timestamp_utc": datetime.utcnow().isoformat(),
        "timestamp_local": now_local().isoformat(),
        "mode": "forecast",
    }
    snap = compute_calc(snap)
    snap = fetch_spot_safe(snap, "ETHUSDT", "eth_spot")
    snap = fetch_spot_safe(snap, "BTCUSDT", "btc_spot")
    snap = compute_derivs(snap)
    snap = compute_volumes(snap)
    snap = compute_levels(snap)
    return snap

# --------- BUILD: review ---------
def build_review_snapshot():
    date_str = today_local_str()
    forecast_name = f"{date_str}_forecast.json"
    forecast = github_get_raw_snapshot(forecast_name)
    if not forecast:
        # fallback: попробуем частичную сборку без прогноза (не критично)
        print("⚠️ Forecast snapshot not found — review will be partial.")
        forecast = {}

    # диапазон сессии: от локальной полуночи до текущего момента (вечером — до 21:10 локального)
    start_ms = local_midnight_ms()
    end_local = now_local()
    end_ms = local_time_to_utc_ms(end_local)

    # берём 5-минутки за день
    kl = get_klines_range("ETHUSDT", interval="5", start_ms=start_ms, end_ms=end_ms)

    hi = -float("inf")
    lo = float("inf")
    close = float("nan")
    vol_sum_base = 0.0
    vol_sum_quote = 0.0

    for k in kl:
        # формат: [start, open, high, low, close, volume(base), turnover(quote)]
        h = float(k[2]); l = float(k[3]); c = float(k[4])
        v_base = float(k[5]); v_quote = float(k[6])
        hi = max(hi, h)
        lo = min(lo, l)
        close = c
        vol_sum_base += v_base
        vol_sum_quote += v_quote

    vwap_approx = (vol_sum_quote / vol_sum_base) if vol_sum_base > 0 else None

    # сравнение с прогнозными уровнями
    levels = forecast.get("levels", {}) if isinstance(forecast, dict) else {}
    supp = levels.get("support", []) or []
    ress = levels.get("resistance", []) or []
    range_mid = levels.get("range_mid")

    touched_support = any(math.isfinite(lo) and lo <= float(s) + 1e-8 for s in supp)
    touched_resist  = any(math.isfinite(hi) and hi >= float(r) - 1e-8 for r in ress)
    inside_range = (
        math.isfinite(close) and
        (min([*supp, *ress]) if (supp or ress) else -float("inf")) <= close <=
        (max([*supp, *ress]) if (supp or ress) else float("inf"))
    )

    bias = "range"
    if touched_resist and (not touched_support):
        bias = "bullish"
    elif touched_support and (not touched_resist):
        bias = "bearish"
    # иначе range

    review = {
        "timestamp_utc": datetime.utcnow().isoformat(),
        "timestamp_local": now_local().isoformat(),
        "mode": "review",
        "session": {
            "start_local_iso": datetime.fromtimestamp(start_ms/1000, LOCAL_TZ).isoformat(),
            "end_local_iso": end_local.isoformat(),
        },
        "actual": {
            "high": round(hi, 2) if math.isfinite(hi) else None,
            "low": round(lo, 2) if math.isfinite(lo) else None,
            "close": round(close, 2) if math.isfinite(close) else None,
            "vwap_approx": round(vwap_approx, 2) if vwap_approx else None,
            "volume_base_sum": vol_sum_base,
            "turnover_quote_sum": vol_sum_quote,
        },
        "compare": {
            "levels_forecast": levels,
            "touched_support": bool(touched_support),
            "touched_resistance": bool(touched_resist),
            "inside_range": bool(inside_range),
            "bias": bias,
        },
        "forecast_ref": {
            "eth_spot_at_forecast": forecast.get("eth_spot"),
            "btc_spot_at_forecast": forecast.get("btc_spot"),
            "calc_at_forecast": forecast.get("calc"),
        }
    }
    return review

# --------- SAVE & UPLOAD ---------
def save_and_upload(obj: dict, name: str, msg: str):
    path = f"/tmp/{name}"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    print(f"Saved: {path}")
    with open(path, "rb") as f:
        github_put_file(f"snapshots/{name}", f.read(), msg)

# --------- MAIN ---------
if __name__ == "__main__":
    d = today_local_str()
    if MODE == "forecast":
        print(f"Running forecast for {d}")
        snap = build_forecast_snapshot()
        save_and_upload(snap, f"{d}_forecast.json", f"auto snapshot forecast {d}")
        print("✅ Forecast done.")
    elif MODE == "review":
        print(f"Running review for {d}")
        rev = build_review_snapshot()
        save_and_upload(rev, f"{d}_review.json", f"auto snapshot review {d}")
        print("✅ Review done.")
    else:
        print(f"Unknown MODE={MODE}. Use 'forecast' or 'review'.")