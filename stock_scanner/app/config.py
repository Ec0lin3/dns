"""Configuration load/save. The whole bot is driven by config.json."""
import copy
import json
import os

CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config.json")

# Every scan criterion has a "mode": off / mandatory / bonus.
#   off       -> criterion ignored
#   mandatory -> stock rejected if the criterion fails
#   bonus     -> passing the criterion adds `weight` to the stock's score
# A stock is included when all mandatory criteria pass AND
# bonus score >= min_score.
DEFAULT_CONFIG = {
    "universe": {
        "type": "sp500",          # sp500 | nasdaq100 | both | custom
        "custom_tickers": [],
    },
    "min_score": 1,
    "criteria": {
        "moving_average": {
            "mode": "mandatory",
            "weight": 1,
            "match": "any",       # all | any  (how the checks below combine)
            "checks": [
                {"check": "price_vs_ma", "ma_type": "EMA", "period": 200,
                 "timeframe": "1d", "condition": "above"},
                {"check": "ma_cross", "ma_type": "EMA", "fast_period": 20,
                 "slow_period": 50, "timeframe": "1d", "direction": "golden",
                 "within_bars": 10},
                {"check": "price_near_ma", "ma_type": "EMA", "period": 50,
                 "timeframe": "1d", "tolerance_pct": 1.5},
            ],
        },
        "fvg": {
            "mode": "bonus",
            "weight": 1,
            "match": "any",
            "checks": [
                {"timeframe": "1wk", "direction": "bullish",
                 "condition": "exists", "lookback": 60},
                {"timeframe": "1d", "direction": "bullish",
                 "condition": "price_inside", "lookback": 60},
            ],
        },
        "liquidity": {
            "mode": "bonus",
            "weight": 1,
            "match": "any",
            "checks": [
                {"timeframe": "1d", "strength": 5, "lookback": 120,
                 "condition": "untapped_above", "recency": 5},
                {"timeframe": "1d", "strength": 2, "lookback": 60,
                 "condition": "swept_below", "recency": 5},
            ],
        },
        "range_equilibrium": {
            "mode": "mandatory",
            "weight": 1,
            "timeframe": "1d",
            "swing_strength": 10,     # how significant the swing high/low must be
            "max_lookback": 250,      # how far back to search for swings
            "zone": "discount",       # discount | premium | equilibrium
            "eq_band_pct": 10,
        },
        "gaps": {
            "mode": "bonus",
            "weight": 1,
            "timeframe": "1d",
            "direction": "up",        # up | down
            "min_gap_pct": 1.0,
            "condition": "unfilled",  # unfilled | filled | any
            "lookback": 30,
        },
        "support_resistance": {
            "mode": "bonus",
            "weight": 1,
            "timeframe": "1d",
            "lookback": 250,
            "line_types": ["horizontal", "trendline"],  # any of these
            "swing_strength": 5,
            "tolerance_pct": 1.0,
            "trendline_anchor": "lows",  # lows | highs
        },
    },
    # Settings for the per-stock chart shown when a result row is clicked.
    "chart": {
        "timeframe": "1d",
        "candles": 250,
    },
    "telegram": {
        "enabled": False,
        "bot_token": "",
        "chat_id": "",
    },
    "schedule": {
        "enabled": False,
        "time": "16:30",
        "timezone": "America/New_York",
    },
}


def _merge(base, override):
    """Deep-merge override into a copy of base. Lists are replaced wholesale."""
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _merge(base[key], value)
        else:
            base[key] = value
    return base


def load_config():
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, encoding="utf-8") as fh:
                stored = json.load(fh)
            return _merge(copy.deepcopy(DEFAULT_CONFIG), stored)
        except (json.JSONDecodeError, OSError):
            pass
    save_config(DEFAULT_CONFIG)
    return copy.deepcopy(DEFAULT_CONFIG)


def save_config(cfg):
    with open(CONFIG_PATH, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2, ensure_ascii=False)
