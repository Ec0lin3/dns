"""Scan orchestration: download data, evaluate every ticker, score, notify."""
import threading
from datetime import datetime

from . import indicators as ind
from .config import load_config
from .data import get_data
from .notifier import send_telegram
from .universe import resolve_universe

# Hebrew labels used in Telegram messages and (as a fallback) the UI.
CRITERION_LABELS = {
    "moving_average": "ממוצעים נעים",
    "fvg": "FVG",
    "liquidity": "נזילות",
    "range_equilibrium": "Range/EQ",
    "gaps": "פערים",
    "support_resistance": "תמיכה/התנגדות",
}


# --------------------------------------------------------------------------
# Shared scan state (read by the dashboard via /api/scan/status)
# --------------------------------------------------------------------------
class ScanState:
    def __init__(self):
        self.lock = threading.Lock()
        self.running = False
        self.phase = "idle"          # idle | downloading | evaluating | done | error
        self.progress = 0
        self.total = 0
        self.results = []
        self.last_run = None
        self.error = None
        self.errors_count = 0
        self.universe_size = 0

    def snapshot(self):
        with self.lock:
            return {
                "running": self.running,
                "phase": self.phase,
                "progress": self.progress,
                "total": self.total,
                "results": self.results,
                "last_run": self.last_run,
                "error": self.error,
                "errors_count": self.errors_count,
                "universe_size": self.universe_size,
            }


STATE = ScanState()


# --------------------------------------------------------------------------
# Per-criterion evaluation
# --------------------------------------------------------------------------
def _df(data, timeframe, ticker):
    return data.get(timeframe, {}).get(ticker)


def _multi(checks, match, evaluate_one):
    """Combine a list of sub-checks with all/any logic."""
    if not checks:
        return False, "no checks configured"
    results = [evaluate_one(chk) for chk in checks]
    oks = [r[0] for r in results]
    detail = " | ".join(r[1] for r in results)
    passed = all(oks) if match == "all" else any(oks)
    return passed, detail


def eval_moving_average(cfg, data, ticker):
    def one(chk):
        df = _df(data, chk.get("timeframe", "1d"), ticker)
        if df is None or df.empty:
            return False, f"{chk.get('timeframe', '1d')}: no data"
        kind = chk.get("check")
        if kind == "price_vs_ma":
            return ind.check_price_vs_ma(df, chk["ma_type"], int(chk["period"]),
                                         chk["condition"])
        if kind == "ma_cross":
            return ind.check_ma_cross(df, chk["ma_type"], int(chk["fast_period"]),
                                      int(chk["slow_period"]), chk["direction"],
                                      int(chk.get("within_bars", 10)))
        if kind == "price_near_ma":
            return ind.check_price_near_ma(df, chk["ma_type"], int(chk["period"]),
                                           float(chk["tolerance_pct"]))
        return False, "unknown MA check"

    return _multi(cfg.get("checks", []), cfg.get("match", "all"), one)


def eval_fvg(cfg, data, ticker):
    def one(chk):
        df = _df(data, chk.get("timeframe", "1d"), ticker)
        if df is None or df.empty:
            return False, f"{chk.get('timeframe', '1d')}: no data"
        return ind.check_fvg(df, chk["direction"], chk["condition"],
                             int(chk.get("lookback", 60)))

    return _multi(cfg.get("checks", []), cfg.get("match", "all"), one)


def eval_liquidity(cfg, data, ticker):
    def one(chk):
        df = _df(data, chk.get("timeframe", "1d"), ticker)
        if df is None or df.empty:
            return False, f"{chk.get('timeframe', '1d')}: no data"
        return ind.check_liquidity(df, int(chk.get("strength", 5)),
                                   int(chk.get("lookback", 120)), chk["condition"],
                                   int(chk.get("recency", 5)))

    return _multi(cfg.get("checks", []), cfg.get("match", "all"), one)


def eval_range(cfg, data, ticker):
    df = _df(data, cfg.get("timeframe", "1d"), ticker)
    if df is None or df.empty:
        return False, "no data"
    return ind.check_range(df, int(cfg.get("lookback", 60)),
                           cfg.get("zone", "discount"),
                           float(cfg.get("eq_band_pct", 10)))


def eval_gaps(cfg, data, ticker):
    df = _df(data, cfg.get("timeframe", "1d"), ticker)
    if df is None or df.empty:
        return False, "no data"
    return ind.check_gap(df, cfg.get("direction", "up"),
                         float(cfg.get("min_gap_pct", 1.0)),
                         cfg.get("condition", "unfilled"),
                         int(cfg.get("lookback", 30)))


def eval_support_resistance(cfg, data, ticker):
    df = _df(data, cfg.get("timeframe", "1d"), ticker)
    if df is None or df.empty:
        return False, "no data"
    return ind.check_support_resistance(
        df, int(cfg.get("lookback", 250)),
        cfg.get("line_types", ["horizontal"]),
        int(cfg.get("swing_strength", 5)),
        float(cfg.get("tolerance_pct", 1.0)),
        cfg.get("trendline_anchor", "lows"))


EVALUATORS = {
    "moving_average": eval_moving_average,
    "fvg": eval_fvg,
    "liquidity": eval_liquidity,
    "range_equilibrium": eval_range,
    "gaps": eval_gaps,
    "support_resistance": eval_support_resistance,
}


# --------------------------------------------------------------------------
# Ticker scoring
# --------------------------------------------------------------------------
def collect_timeframes(config):
    """Every timeframe referenced by an active criterion."""
    timeframes = set()
    for name, cfg in config.get("criteria", {}).items():
        if cfg.get("mode", "off") == "off":
            continue
        if name in ("moving_average", "fvg", "liquidity"):
            for chk in cfg.get("checks", []):
                timeframes.add(chk.get("timeframe", "1d"))
        else:
            timeframes.add(cfg.get("timeframe", "1d"))
    return sorted(timeframes) or ["1d"]


def _last_price(data, ticker):
    for timeframe in ("1d", "1h", "60m", "1wk", "1mo", "30m", "15m"):
        df = _df(data, timeframe, ticker)
        if df is not None and not df.empty:
            return round(float(df["Close"].iloc[-1]), 2)
    for tf_map in data.values():
        df = tf_map.get(ticker)
        if df is not None and not df.empty:
            return round(float(df["Close"].iloc[-1]), 2)
    return None


def evaluate_ticker(config, data, ticker):
    breakdown = []
    score = 0
    max_bonus = 0
    mandatory_ok = True

    for name, cfg in config.get("criteria", {}).items():
        mode = cfg.get("mode", "off")
        if mode == "off" or name not in EVALUATORS:
            continue
        passed, detail = EVALUATORS[name](cfg, data, ticker)
        weight = int(cfg.get("weight", 1))
        if mode == "bonus":
            max_bonus += weight
            if passed:
                score += weight
        elif mode == "mandatory" and not passed:
            mandatory_ok = False
        breakdown.append({
            "criterion": name,
            "label": CRITERION_LABELS.get(name, name),
            "mode": mode,
            "passed": passed,
            "detail": detail,
            "weight": weight,
        })

    threshold = min(int(config.get("min_score", 0)), max_bonus)
    included = mandatory_ok and score >= threshold
    return {
        "ticker": ticker,
        "price": _last_price(data, ticker),
        "score": score,
        "max_bonus": max_bonus,
        "mandatory_ok": mandatory_ok,
        "included": included,
        "breakdown": breakdown,
    }


# --------------------------------------------------------------------------
# Scan runner
# --------------------------------------------------------------------------
def run_scan_sync():
    """Run a full scan. Safe to call from a thread or the scheduler."""
    with STATE.lock:
        if STATE.running:
            return
        STATE.running = True
        STATE.phase = "starting"
        STATE.error = None
        STATE.progress = 0
        STATE.total = 0

    try:
        config = load_config()
        tickers = resolve_universe(config.get("universe", {}))
        with STATE.lock:
            STATE.universe_size = len(tickers)
        if not tickers:
            raise ValueError("universe is empty - add tickers in the dashboard")

        timeframes = collect_timeframes(config)
        with STATE.lock:
            STATE.phase = "downloading"
            STATE.total = len(timeframes)

        data = {}
        for idx, timeframe in enumerate(timeframes):
            data[timeframe] = get_data(tickers, timeframe)
            with STATE.lock:
                STATE.progress = idx + 1

        with STATE.lock:
            STATE.phase = "evaluating"
            STATE.total = len(tickers)
            STATE.progress = 0

        results = []
        errors = 0
        for idx, ticker in enumerate(tickers):
            try:
                outcome = evaluate_ticker(config, data, ticker)
                if outcome["included"]:
                    results.append(outcome)
            except Exception:  # noqa: BLE001 - one bad ticker must not abort the scan
                errors += 1
            with STATE.lock:
                STATE.progress = idx + 1

        results.sort(key=lambda r: (r["score"], r["ticker"]), reverse=True)
        with STATE.lock:
            STATE.results = results
            STATE.errors_count = errors
            STATE.last_run = datetime.now().isoformat(timespec="seconds")
            STATE.phase = "done"

        _notify(config, results)
    except Exception as exc:  # noqa: BLE001
        with STATE.lock:
            STATE.error = str(exc)
            STATE.phase = "error"
    finally:
        with STATE.lock:
            STATE.running = False


def start_scan():
    """Start a scan in a background thread (no-op if one is already running)."""
    with STATE.lock:
        if STATE.running:
            return False
    threading.Thread(target=run_scan_sync, daemon=True).start()
    return True


# --------------------------------------------------------------------------
# Telegram notification
# --------------------------------------------------------------------------
def _chunks(text, size=3900):
    lines = text.split("\n")
    buf = ""
    for line in lines:
        if len(buf) + len(line) + 1 > size:
            if buf:
                yield buf
            buf = line
        else:
            buf = f"{buf}\n{line}" if buf else line
    if buf:
        yield buf


def _notify(config, results):
    telegram = config.get("telegram", {})
    if not telegram.get("enabled"):
        return
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    if not results:
        message = f"\U0001F50D Stock Scanner ({stamp})\nNo stocks matched the filter."
    else:
        lines = [f"\U0001F50D <b>Stock Scanner</b> - {len(results)} matches ({stamp})", ""]
        for r in results[:50]:
            price = f"${r['price']:.2f}" if r["price"] is not None else "-"
            lines.append(f"<b>{r['ticker']}</b> {price}  score {r['score']}/{r['max_bonus']}")
        message = "\n".join(lines)
    for chunk in _chunks(message):
        send_telegram(telegram.get("bot_token"), telegram.get("chat_id"), chunk)
