# proxy.py — GitHub snapshot proxy (FastAPI)
# ENV:
#   GITHUB_REPO="anton-baton-sem/bybit-tg-bot"
#   GITHUB_BRANCH="main"            (опц., по умолчанию main)
#   GITHUB_PATH="snapshots"         (опц., по умолчанию snapshots)
#   PROXY_TOKEN="<секрет>"          (опц., если хочешь защиту)
#
# Deploy как Web Service на Render: Command = `uvicorn proxy:app --host 0.0.0.0 --port 10000`

import os, base64, json, urllib.request, urllib.parse
from fastapi import FastAPI, HTTPException, Response, Query
from fastapi.middleware.cors import CORSMiddleware

REPO   = os.getenv("GITHUB_REPO", "anton-baton-sem/bybit-tg-bot")
BRANCH = os.getenv("GITHUB_BRANCH", "main")
SNPATH = os.getenv("GITHUB_PATH", "snapshots")
PTOKEN = os.getenv("PROXY_TOKEN")   # если задан — запросы должны передавать token=<...>

RAW_BASE = "https://raw.githubusercontent.com"
API_BASE = "https://api.github.com"

app = FastAPI(title="GitHub Snapshot Proxy")

# Разрешим CORS на всякий случай
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_methods=["GET"], allow_headers=["*"],
)

def http_get(url, headers=None, timeout=12):
    req = urllib.request.Request(url, headers=headers or {"User-Agent":"Proxy/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(), r.getcode(), r.headers

def fetch_snapshot(date_str: str, snap_type: str):
    # 1) raw
    rel = f"{SNPATH}/{date_str}_{snap_type}.json"
    raw_url = f"{RAW_BASE}/{REPO}/{BRANCH}/{rel}"
    try:
        body, code, _ = http_get(raw_url)
        if code == 200:
            return json.loads(body.decode("utf-8"))
    except Exception:
        pass
    # 2) API fallback
    api_url = f"{API_BASE}/repos/{REPO}/contents/{rel}?ref={BRANCH}"
    try:
        body, code, _ = http_get(api_url, headers={"User-Agent":"Proxy/1.0","Accept":"application/vnd.github+json"})
        if code == 200:
            obj = json.loads(body.decode("utf-8"))
            content = obj.get("content", "")
            data = base64.b64decode(content).decode("utf-8")
            return json.loads(data)
    except Exception:
        pass
    raise HTTPException(status_code=404, detail="snapshot not found")

@app.get("/snapshot")
def snapshot(
    date: str = Query(..., description="YYYY-MM-DD"),
    type: str = Query(..., pattern="^(forecast|review)$"),
    token: str | None = None
):
    # опц. защита токеном
    if PTOKEN and token != PTOKEN:
        raise HTTPException(status_code=401, detail="unauthorized")

    try:
        data = fetch_snapshot(date, type)
    except HTTPException as e:
        raise e
    except Exception:
        raise HTTPException(status_code=500, detail="internal error")

    # без кэша и как чистый JSON
    return Response(
        content=json.dumps(data, ensure_ascii=False),
        media_type="application/json",
        headers={"Cache-Control":"no-store"}
    )
    
    # ----------------------------------------------------
# Новый блок: быстрый доступ к "сегодняшнему" снапшоту
# ----------------------------------------------------
from zoneinfo import ZoneInfo
from datetime import datetime

TZ = ZoneInfo("Europe/Podgorica")

@app.get("/today")
def today_snapshot(type: str):
    """
    Возвращает актуальный снапшот за сегодняшний день
    по часовому поясу Europe/Podgorica.
    Пример: /today?type=forecast
    """
    if type not in ("forecast", "review"):
        raise HTTPException(400, "type must be forecast|review")
    date = datetime.now(TZ).date().isoformat()
    data = fetch_snapshot(date, type)
    return Response(
        content=json.dumps(data, ensure_ascii=False),
        media_type="application/json",
        headers={"Cache-Control": "no-store"}
    )

@app.get("/healthz")
def health_check():
    """Проверка состояния прокси."""
    return {"ok": True, "time": datetime.now(TZ).isoformat()}
