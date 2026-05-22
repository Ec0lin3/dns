"""Builds chart-ready annotations for one ticker.

Given the current config, produces candles plus every feature the bot
detects (moving averages, FVG zones, liquidity swings, range levels,
gaps, support/resistance) so the dashboard can draw them on a
TradingView Lightweight Chart with an explaining legend.
"""
import pandas as pd

from . import indicators as ind
from .data import get_data

MA_COLORS = ["#f59e0b", "#06b6d4", "#a855f7", "#eab308", "#ec4899"]
GREEN = "#22c55e"
RED = "#ef4444"
BLUE = "#3b82f6"
AMBER = "#f59e0b"
INTRADAY = ("15m", "30m", "60m", "1h")


def _fmt(ts, timeframe):
    ts = pd.Timestamp(ts)
    if timeframe in INTRADAY:
        return int(ts.timestamp())
    return ts.strftime("%Y-%m-%d")


def _merge_levels(levels, tol_pct=0.6, keep=7):
    """Collapse S/R levels that sit within tol_pct of each other."""
    ordered = sorted(levels, key=lambda x: x["price"])
    merged = []
    for lvl in ordered:
        if merged and abs(lvl["price"] - merged[-1]["price"]) / merged[-1]["price"] * 100 <= tol_pct:
            continue
        merged.append(lvl)
    if len(merged) > keep:  # keep the levels nearest the latest price
        merged = merged[-keep:]
    return merged


def _empty(ticker, timeframe, error):
    return {"ticker": ticker, "timeframe": timeframe, "error": error,
            "candles": [], "lines": [], "priceLines": [], "boxes": [],
            "markers": [], "legend": []}


def build_chart(config, ticker):
    chart_cfg = config.get("chart", {})
    timeframe = chart_cfg.get("timeframe", "1d")
    n = int(chart_cfg.get("candles", 250))

    df = get_data([ticker], timeframe).get(ticker)
    if df is None or df.empty:
        return _empty(ticker, timeframe, "no data for this ticker")
    df = df.tail(n)
    times = [_fmt(t, timeframe) for t in df.index]
    last_time = times[-1]

    out = _empty(ticker, timeframe, None)
    out["candles"] = [
        {"time": times[i],
         "open": round(float(df["Open"].iloc[i]), 4),
         "high": round(float(df["High"].iloc[i]), 4),
         "low": round(float(df["Low"].iloc[i]), 4),
         "close": round(float(df["Close"].iloc[i]), 4)}
        for i in range(len(df))
    ]
    crit = config.get("criteria", {})

    def legend(color, label, desc):
        out["legend"].append({"color": color, "label": label, "desc": desc})

    # --- moving averages -------------------------------------------------
    ma_cfg = crit.get("moving_average", {})
    if ma_cfg.get("mode", "off") != "off":
        seen, ci = set(), 0
        for chk in ma_cfg.get("checks", []):
            if chk.get("check") == "ma_cross":
                specs = [(chk["ma_type"], int(chk["fast_period"])),
                         (chk["ma_type"], int(chk["slow_period"]))]
            else:
                specs = [(chk["ma_type"], int(chk["period"]))]
            for ma_type, period in specs:
                if (ma_type, period) in seen:
                    continue
                seen.add((ma_type, period))
                series = ind.moving_average(df["Close"], ma_type, period)
                pts = [{"time": times[i], "value": round(float(series.iloc[i]), 4)}
                       for i in range(len(df)) if pd.notna(series.iloc[i])]
                color = MA_COLORS[ci % len(MA_COLORS)]
                ci += 1
                out["lines"].append({"label": f"{ma_type} {period}",
                                     "color": color, "points": pts})
                legend(color, f"{ma_type} {period}", "ממוצע נע")

    # --- FVG -------------------------------------------------------------
    fvg_cfg = crit.get("fvg", {})
    if fvg_cfg.get("mode", "off") != "off":
        directions = {chk.get("direction", "bullish")
                      for chk in fvg_cfg.get("checks", [])}
        for direction in directions:
            color = ("rgba(34,197,94,0.18)" if direction == "bullish"
                     else "rgba(239,68,68,0.18)")
            open_fvgs = [f for f in ind.find_fvgs(df, direction)
                         if not f["filled"]]
            for f in open_fvgs:
                out["boxes"].append({
                    "time1": times[f["idx"]], "time2": last_time,
                    "price1": round(f["bottom"], 4), "price2": round(f["top"], 4),
                    "color": color, "label": f"FVG {direction}",
                })
            if open_fvgs:
                legend(color.replace("0.18", "0.9"),
                       f"FVG {direction}", "פער הוגן שלא מולא")

    # --- liquidity (swing points) ---------------------------------------
    liq_cfg = crit.get("liquidity", {})
    if liq_cfg.get("mode", "off") != "off":
        strengths = {int(chk.get("strength", 5))
                     for chk in liq_cfg.get("checks", [])}
        strength = min(strengths) if strengths else 5
        is_high, is_low = ind._swing_points(df, strength)
        hi_idx = [i for i in range(len(df)) if bool(is_high.iloc[i])][-12:]
        lo_idx = [i for i in range(len(df)) if bool(is_low.iloc[i])][-12:]
        for i in hi_idx:
            out["markers"].append({"time": times[i], "position": "aboveBar",
                                   "color": RED, "shape": "circle",
                                   "text": "נזילות"})
        for i in lo_idx:
            out["markers"].append({"time": times[i], "position": "belowBar",
                                   "color": GREEN, "shape": "circle",
                                   "text": "נזילות"})
        if hi_idx or lo_idx:
            legend(RED, "נזילות", "נקודות סווינג שמעליהן/מתחתן יושבת נזילות")

    # --- range / equilibrium --------------------------------------------
    rng_cfg = crit.get("range_equilibrium", {})
    if rng_cfg.get("mode", "off") != "off":
        sub = df.tail(int(rng_cfg.get("lookback", 60)))
        low_low = float(sub["Low"].min())
        high_high = float(sub["High"].max())
        eq = (low_low + high_high) / 2
        out["priceLines"] += [
            {"price": round(high_high, 2), "color": RED,
             "label": f"Range High {high_high:.2f}"},
            {"price": round(eq, 2), "color": BLUE,
             "label": f"EQ 50% {eq:.2f}"},
            {"price": round(low_low, 2), "color": GREEN,
             "label": f"Range Low {low_low:.2f}"},
        ]
        legend(BLUE, "Equilibrium 50%", "אמצע הטווח (נמוך-נמוך עד גבוה-גבוה)")

    # --- gaps ------------------------------------------------------------
    gap_cfg = crit.get("gaps", {})
    if gap_cfg.get("mode", "off") != "off":
        direction = gap_cfg.get("direction", "up")
        gaps = ind.find_gaps(df, direction, float(gap_cfg.get("min_gap_pct", 1.0)))
        for g in gaps[-8:]:
            i = g["idx"]
            out["markers"].append({
                "time": times[i],
                "position": "belowBar" if direction == "up" else "aboveBar",
                "color": AMBER,
                "shape": "arrowUp" if direction == "up" else "arrowDown",
                "text": f"פער {g['gap_pct']:+.1f}%",
            })
        if gaps:
            legend(AMBER, "פער (Gap)", "פער מחיר בין נר לנר")

    # --- support / resistance -------------------------------------------
    sr_cfg = crit.get("support_resistance", {})
    if sr_cfg.get("mode", "off") != "off":
        lookback = int(sr_cfg.get("lookback", 250))
        strength = int(sr_cfg.get("swing_strength", 5))
        line_types = sr_cfg.get("line_types", ["horizontal"])
        if "horizontal" in line_types:
            levels = _merge_levels(
                ind.support_resistance_levels(df, lookback, strength))
            for lvl in levels:
                support = lvl["kind"] == "support"
                out["priceLines"].append({
                    "price": round(lvl["price"], 2),
                    "color": GREEN if support else RED,
                    "label": ("תמיכה " if support else "התנגדות ")
                             + f"{lvl['price']:.2f}",
                })
            if levels:
                legend(GREEN, "תמיכה / התנגדות", "קווים אופקיים מנקודות קיצון")
        if "trendline" in line_types:
            tl = ind.support_trendline(df, lookback, strength,
                                       sr_cfg.get("trendline_anchor", "lows"))
            if tl is not None:
                out["lines"].append({
                    "label": "S/R trendline", "color": GREEN, "dashed": True,
                    "points": [
                        {"time": _fmt(tl["t1"], timeframe),
                         "value": round(tl["y1"], 4)},
                        {"time": last_time,
                         "value": round(tl["price_now"], 4)},
                    ],
                })
                legend(GREEN, "קו מגמה", "קו מגמה אלכסוני על 2 נקודות קיצון")

    # markers must be unique per time and sorted for Lightweight Charts
    seen_times = set()
    unique = []
    for m in sorted(out["markers"], key=lambda x: str(x["time"])):
        if m["time"] in seen_times:
            continue
        seen_times.add(m["time"])
        unique.append(m)
    out["markers"] = unique
    return out
