# main.py — одноразовый прогон (Render Cron)
import os, json, urllib.request, urllib.parse
from datetime import datetime

BYBIT_URL = "https://api.bybit.com/v5/market/tickers"
TG_API    = "https://api.telegram.org"

def fetch(symbol: str, category="spot", timeout=10):
    url = f"{BYBIT_URL}?category={category}&symbol={symbol}"
    with urllib.request.urlopen(url, timeout=timeout) as r:
        j = json.loads(r.read().decode())
    if j.get("retCode") != 0:
        raise RuntimeError(f"Bybit ret={j.get('retCode')} {j.get('retMsg')}")
    it = j["result"]["list"][0]
    return {
        "last": float(it["lastPrice"]),
        "high": float(it.get("highPrice24h", "nan")),
        "low":  float(it.get("lowPrice24h", "nan")),
        "vol":  float(it.get("turnover24h", "nan")),
        "pcnt": float(it.get("price24hPcnt", "0")) * 100.0
    }

def fmt(x, d=2): 
    try: return f"{x:,.{d}f}".replace(",", " ")
    except: return str(x)

def verdict(p):
    if p >= 0.7: return "растёт (bullish)"
    if p <= -0.7: return "снижается (bearish)"
    return "консолидация (range)"

def build(eth, btc):
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    msg = [
        f"📊 Bybit spot — {now}\n",
        f"• ETH/USDT: {fmt(eth['last'])} USDT  ({'▲' if eth['pcnt']>0 else '▼' if eth['pcnt']<0 else '■'} {eth['pcnt']:+.2f}% 24ч)",
        f"  H/L(24h): {fmt(eth['high'])} / {fmt(eth['low'])} • Оборот(24h): {eth['vol']:,.0f} USDT",
        f"  Итог: {verdict(eth['pcnt'])}",
        f"\n• BTC/USDT: {fmt(btc['last'])} USDT  ({'▲' if btc['pcnt']>0 else '▼' if btc['pcnt']<0 else '■'} {btc['pcnt']:+.2f}% 24ч)",
        f"  H/L(24h): {fmt(btc['high'])} / {fmt(btc['low'])} • Оборот(24h): {btc['vol']:,.0f} USDT",
        f"  Итог: {verdict(btc['pcnt'])}",
        "\n🎯 ETH уровни: поддержка (support) 3 780 / 3 700; сопротивление (resistance) 3 950 / 4 050",
        "🔔 Алерты (alerts): 3 700 • 3 780 • 3 950 • 4 050",
        "⏰ Cron: каждые 3 минуты"
    ]
    return "\n".join(msg)

def send(token, chat_id, text, timeout=10):
    url = f"{TG_API}/bot{token}/sendMessage"
    data = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode()
    req  = urllib.request.Request(url, data=data)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        _ = r.read()

def main():
    token = os.environ["TG_BOT_TOKEN"]
    chat  = os.environ["TG_CHAT_ID"]
    eth = fetch("ETHUSDT"); btc = fetch("BTCUSDT")
    msg = build(eth, btc)
    send(token, chat, msg)
    print("OK")

if __name__ == "__main__":
    main()
