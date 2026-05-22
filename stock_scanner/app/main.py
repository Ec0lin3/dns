"""FastAPI application: serves the dashboard and the JSON API."""
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .analysis import build_chart
from .config import load_config, save_config
from .data import clear_cache
from .notifier import send_telegram
from .scanner import STATE, analyze_ticker, start_scan
from .scheduler import init_scheduler, reschedule, shutdown_scheduler

BASE_DIR = os.path.dirname(__file__)
STATIC_DIR = os.path.join(BASE_DIR, "static")


@asynccontextmanager
async def lifespan(_app):
    init_scheduler()
    yield
    shutdown_scheduler()


app = FastAPI(title="Stock Scanner Bot", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC_DIR, "dashboard.html"))


@app.get("/api/config")
def get_config():
    return load_config()


@app.post("/api/config")
async def post_config(request: Request):
    cfg = await request.json()
    save_config(cfg)
    reschedule(cfg)
    return {"status": "saved"}


@app.post("/api/scan")
def post_scan():
    started = start_scan()
    return {"status": "started" if started else "already_running"}


@app.get("/api/scan/status")
def get_scan_status():
    return STATE.snapshot()


@app.get("/api/chart/{ticker}")
def get_chart(ticker: str, timeframe: str | None = None):
    config = load_config()
    if timeframe:
        config.setdefault("chart", {})["timeframe"] = timeframe
    return build_chart(config, ticker.upper())


@app.post("/api/test")
async def post_test(request: Request):
    """Single-stock test: evaluate + chart for the selected criteria only."""
    body = await request.json()
    ticker = (body.get("ticker") or "").strip().upper()
    if not ticker:
        return {"error": "missing ticker"}
    timeframe = body.get("timeframe", "1d")
    selected = set(body.get("criteria", []))

    config = load_config()
    config.setdefault("chart", {})["timeframe"] = timeframe
    for name, ccfg in config.get("criteria", {}).items():
        if name in selected:
            if ccfg.get("mode", "off") == "off":
                ccfg["mode"] = "bonus"
        else:
            ccfg["mode"] = "off"

    return {
        "ticker": ticker,
        "evaluation": analyze_ticker(config, ticker),
        "chart": build_chart(config, ticker),
    }


@app.post("/api/cache/clear")
def post_clear_cache():
    clear_cache()
    return {"status": "cleared"}


@app.post("/api/telegram/test")
def post_telegram_test():
    telegram = load_config().get("telegram", {})
    ok, detail = send_telegram(
        telegram.get("bot_token"),
        telegram.get("chat_id"),
        "✅ Stock Scanner Bot - test message",
    )
    return {"ok": ok, "message": detail}
