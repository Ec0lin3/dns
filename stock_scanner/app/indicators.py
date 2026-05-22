"""Technical / ICT-style indicator checks.

Every check returns a tuple: (passed: bool, detail: str).
The `detail` string is shown in the dashboard so the user can see *why*
a stock did or did not pass.
"""
import pandas as pd


# --------------------------------------------------------------------------
# Moving averages
# --------------------------------------------------------------------------
def moving_average(series, ma_type, period):
    if str(ma_type).upper() == "EMA":
        return series.ewm(span=period, adjust=False).mean()
    return series.rolling(period).mean()


def check_price_vs_ma(df, ma_type, period, condition):
    """Price above / below a moving average."""
    if len(df) < period:
        return False, "not enough data"
    ma = moving_average(df["Close"], ma_type, period)
    price = float(df["Close"].iloc[-1])
    ma_val = ma.iloc[-1]
    if pd.isna(ma_val):
        return False, "MA n/a"
    ma_val = float(ma_val)
    above = price > ma_val
    ok = above if condition == "above" else not above
    sign = ">" if above else "<="
    return ok, f"price {price:.2f} {sign} {ma_type}{period} {ma_val:.2f}"


def check_ma_cross(df, ma_type, fast_period, slow_period, direction, within_bars):
    """Golden / death cross within the last `within_bars` bars."""
    if len(df) < slow_period + within_bars + 2:
        return False, "not enough data"
    fast = moving_average(df["Close"], ma_type, fast_period)
    slow = moving_average(df["Close"], ma_type, slow_period)
    diff = fast - slow
    prev = diff.shift(1)
    cross_up = (prev <= 0) & (diff > 0)
    cross_down = (prev >= 0) & (diff < 0)
    window = max(1, within_bars)
    if direction == "golden":
        ok = bool(cross_up.iloc[-window:].any())
        return ok, "golden cross" if ok else "no golden cross"
    ok = bool(cross_down.iloc[-window:].any())
    return ok, "death cross" if ok else "no death cross"


def check_price_near_ma(df, ma_type, period, tolerance_pct):
    """Price within `tolerance_pct` of a moving average (dynamic S/R touch)."""
    if len(df) < period:
        return False, "not enough data"
    ma = moving_average(df["Close"], ma_type, period)
    price = float(df["Close"].iloc[-1])
    ma_val = ma.iloc[-1]
    if pd.isna(ma_val) or float(ma_val) == 0:
        return False, "MA n/a"
    dist = abs(price - float(ma_val)) / float(ma_val) * 100
    ok = dist <= tolerance_pct
    return ok, f"{dist:.2f}% from {ma_type}{period}"


# --------------------------------------------------------------------------
# Fair Value Gaps (FVG)
# --------------------------------------------------------------------------
def find_fvgs(df, direction):
    """Three-candle Fair Value Gaps for the given direction.

    Bullish FVG: gap between candle[i-2].high and candle[i].low.
    Bearish FVG: gap between candle[i].high and candle[i-2].low.
    A gap is 'filled' once later price trades fully back through it.
    """
    highs = df["High"].values
    lows = df["Low"].values
    n = len(df)
    fvgs = []
    for i in range(2, n):
        if direction == "bullish" and highs[i - 2] < lows[i]:
            bottom, top = float(highs[i - 2]), float(lows[i])
            filled = bool((lows[i + 1:] <= bottom).any()) if i + 1 < n else False
            fvgs.append({"idx": i, "bottom": bottom, "top": top, "filled": filled})
        elif direction == "bearish" and lows[i - 2] > highs[i]:
            bottom, top = float(highs[i]), float(lows[i - 2])
            filled = bool((highs[i + 1:] >= top).any()) if i + 1 < n else False
            fvgs.append({"idx": i, "bottom": bottom, "top": top, "filled": filled})
    return fvgs


def check_fvg(df, direction, condition, lookback):
    if len(df) < 3:
        return False, "not enough data"
    sub = df.tail(max(lookback, 3))
    open_fvgs = [f for f in find_fvgs(sub, direction) if not f["filled"]]
    if condition == "exists":
        ok = len(open_fvgs) > 0
        return ok, f"{len(open_fvgs)} open {direction} FVG"
    # condition == "price_inside"
    price = float(df["Close"].iloc[-1])
    inside = [f for f in open_fvgs if f["bottom"] <= price <= f["top"]]
    if inside:
        return True, f"price inside {direction} FVG"
    return False, f"price outside FVG ({len(open_fvgs)} open)"


# --------------------------------------------------------------------------
# Range / Equilibrium  (50% of lowest-low .. highest-high)
# --------------------------------------------------------------------------
def check_range(df, lookback, zone, eq_band_pct):
    if len(df) < 2:
        return False, "not enough data"
    sub = df.tail(lookback)
    low_low = float(sub["Low"].min())
    high_high = float(sub["High"].max())
    if high_high <= low_low:
        return False, "flat range"
    eq = (low_low + high_high) / 2
    price = float(df["Close"].iloc[-1])
    pos_pct = (price - low_low) / (high_high - low_low) * 100
    if zone == "discount":
        ok = price < eq
    elif zone == "premium":
        ok = price > eq
    else:  # equilibrium
        ok = abs(pos_pct - 50) <= eq_band_pct
    return ok, f"range pos {pos_pct:.0f}% (EQ {eq:.2f})"


# --------------------------------------------------------------------------
# Liquidity (ICT internal / external)
# --------------------------------------------------------------------------
def _swing_points(df, strength):
    """Boolean masks for swing highs and swing lows.

    A larger `strength` yields fewer, more significant swings (external
    liquidity); a smaller one yields minor swings (internal liquidity).
    """
    window = 2 * strength + 1
    roll_high = df["High"].rolling(window, center=True).max()
    roll_low = df["Low"].rolling(window, center=True).min()
    is_high = df["High"] == roll_high
    is_low = df["Low"] == roll_low
    return is_high, is_low


def check_liquidity(df, strength, lookback, condition, recency=5):
    if len(df) < 2 * strength + 2:
        return False, "not enough data"
    sub = df.tail(lookback).reset_index(drop=True)
    is_high, is_low = _swing_points(sub, strength)
    highs = sub["High"]
    lows = sub["Low"]
    price = float(sub["Close"].iloc[-1])
    n = len(sub)

    untapped_above = untapped_below = swept_above = swept_below = False

    for pos in highs.index[is_high]:
        val = float(highs.iloc[pos])
        later = highs.iloc[pos + 1:]
        exceeded = later[later > val]
        if val > price and exceeded.empty:
            untapped_above = True
        if not exceeded.empty and exceeded.index[0] >= n - recency:
            swept_above = True

    for pos in lows.index[is_low]:
        val = float(lows.iloc[pos])
        later = lows.iloc[pos + 1:]
        breached = later[later < val]
        if val < price and breached.empty:
            untapped_below = True
        if not breached.empty and breached.index[0] >= n - recency:
            swept_below = True

    flags = {
        "untapped_above": untapped_above,
        "untapped_below": untapped_below,
        "swept_above": swept_above,
        "swept_below": swept_below,
    }
    ok = flags.get(condition, False)
    return ok, f"{condition} = {ok}"


# --------------------------------------------------------------------------
# Gaps
# --------------------------------------------------------------------------
def find_gaps(df, direction, min_gap_pct):
    """All gaps in `df` matching the direction. idx is positional within df.

    Each gap carries the zone it left untraded: prev_close .. gap-day open.
    """
    opens = df["Open"].values
    closes = df["Close"].values
    gaps = []
    for i in range(1, len(df)):
        prev_close = closes[i - 1]
        if prev_close <= 0:
            continue
        gap_pct = (opens[i] - prev_close) / prev_close * 100
        if (direction == "up" and gap_pct >= min_gap_pct) or \
           (direction == "down" and gap_pct <= -min_gap_pct):
            gaps.append({"idx": i, "gap_pct": gap_pct,
                         "prev_close": float(prev_close),
                         "open": float(opens[i])})
    return gaps


def check_gap(df, direction, min_gap_pct, condition, lookback):
    if len(df) < 2:
        return False, "not enough data"
    sub = df.tail(lookback).reset_index(drop=True)
    highs = sub["High"].values
    lows = sub["Low"].values

    gaps = find_gaps(sub, direction, min_gap_pct)
    if not gaps:
        return False, f"no {direction} gap >= {min_gap_pct}%"

    last = gaps[-1]
    i, gap_pct, prev_close = last["idx"], last["gap_pct"], last["prev_close"]
    zone_lo, zone_hi = sorted([prev_close, last["open"]])
    if direction == "up":
        filled = bool(lows[i:].min() <= prev_close)
    else:
        filled = bool(highs[i:].max() >= prev_close)

    if condition == "price_inside":
        price = float(sub["Close"].iloc[-1])
        ok = zone_lo <= price <= zone_hi
        where = "inside" if ok else "outside"
        return ok, f"{direction} gap zone {zone_lo:.2f}-{zone_hi:.2f}, price {where}"
    if condition == "any":
        ok = True
    elif condition == "filled":
        ok = filled
    else:  # unfilled
        ok = not filled
    state = "filled" if filled else "open"
    return ok, f"{direction} gap {gap_pct:+.1f}% ({state})"


# --------------------------------------------------------------------------
# Support / Resistance
# --------------------------------------------------------------------------
def _touches(low, high, level, tolerance_pct):
    """True if the candle range [low, high] reaches `level` within tolerance."""
    if level <= 0:
        return False
    if low <= level <= high:
        return True
    dist = min(abs(low - level), abs(high - level)) / level * 100
    return dist <= tolerance_pct


def support_resistance_levels(df, lookback, swing_strength):
    """Horizontal S/R levels: swing highs = resistance, swing lows = support."""
    sub = df.tail(lookback)
    is_high, is_low = _swing_points(sub, swing_strength)
    levels = []
    for ts, price in sub["High"][is_high].items():
        levels.append({"price": float(price), "kind": "resistance", "time": ts})
    for ts, price in sub["Low"][is_low].items():
        levels.append({"price": float(price), "kind": "support", "time": ts})
    return levels


def support_trendline(df, lookback, swing_strength, anchor):
    """Diagonal trendline anchored on a major swing extreme.

    anchor='lows'  -> rising support: from the lowest swing low to the most
                      recent swing low that formed after it.
    anchor='highs' -> falling resistance: from the highest swing high to the
                      most recent swing high after it.

    Returns the two anchor points plus the line value extrapolated to the
    most recent bar, or None if fewer than two swing points exist.
    """
    sub = df.tail(lookback)
    is_high, is_low = _swing_points(sub, swing_strength)
    if anchor == "highs":
        mask, col = is_high, "High"
    else:
        mask, col = is_low, "Low"
    pts = [(pos, float(sub[col].iloc[pos]))
           for pos in range(len(sub)) if bool(mask.iloc[pos])]
    if len(pts) < 2:
        return None

    # anchor 1 = the major extreme (lowest low / highest high)
    if anchor == "highs":
        major = max(range(len(pts)), key=lambda k: pts[k][1])
    else:
        major = min(range(len(pts)), key=lambda k: pts[k][1])
    later = [p for p in pts if p[0] > pts[major][0]]
    if later:
        (x1, y1), (x2, y2) = pts[major], later[-1]
    else:
        # the major extreme is the most recent swing -> fall back to last two
        (x1, y1), (x2, y2) = pts[-2], pts[-1]
    if x2 == x1:
        return None

    slope = (y2 - y1) / (x2 - x1)
    last = len(sub) - 1
    return {
        "anchor": anchor,
        "t1": sub.index[x1], "y1": y1,
        "t2": sub.index[x2], "y2": y2,
        "t_last": sub.index[last],
        "price_now": y1 + slope * (last - x1),
    }


def check_support_resistance(df, lookback, line_types, swing_strength,
                             tolerance_pct, trendline_anchor):
    """Pass when the current candle touches a horizontal level or trendline."""
    if len(df) < 2 * swing_strength + 2:
        return False, "not enough data"
    candle = df.iloc[-1]
    low, high = float(candle["Low"]), float(candle["High"])
    touched = []
    if "horizontal" in line_types:
        for lvl in support_resistance_levels(df, lookback, swing_strength):
            if _touches(low, high, lvl["price"], tolerance_pct):
                touched.append(f"{lvl['kind']} {lvl['price']:.2f}")
    if "trendline" in line_types:
        tl = support_trendline(df, lookback, swing_strength, trendline_anchor)
        if tl is not None and _touches(low, high, tl["price_now"], tolerance_pct):
            touched.append(f"trendline {tl['price_now']:.2f}")
    if touched:
        return True, "touched " + ", ".join(touched[:3])
    return False, "no S/R touch"
