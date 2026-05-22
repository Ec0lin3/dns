"""Market data layer: batch downloads from Yahoo Finance with a short cache."""
import time

import pandas as pd
import yfinance as yf

# timeframe -> (history period, yfinance interval)
TF_MAP = {
    "15m": ("60d", "15m"),
    "30m": ("60d", "30m"),
    "60m": ("720d", "60m"),
    "1h": ("720d", "60m"),
    "1d": ("3y", "1d"),
    "1wk": ("8y", "1wk"),
    "1mo": ("max", "1mo"),
}

_OHLCV = ["Open", "High", "Low", "Close", "Volume"]

_cache = {}            # (timeframe, tickers_key) -> (timestamp, data)
CACHE_TTL = 900        # 15 minutes


def _clean(df):
    df = df.dropna(how="all")
    df = df[[c for c in _OHLCV if c in df.columns]]
    return df.dropna()


def fetch(tickers, timeframe):
    """Download one timeframe for all tickers. Returns {ticker: DataFrame}."""
    period, interval = TF_MAP.get(timeframe, ("3y", "1d"))
    tickers = list(tickers)
    if not tickers:
        return {}
    raw = yf.download(
        tickers,
        period=period,
        interval=interval,
        group_by="ticker",
        auto_adjust=True,
        threads=True,
        progress=False,
    )
    result = {}
    if raw is None or raw.empty:
        return result
    multi = isinstance(raw.columns, pd.MultiIndex)
    for ticker in tickers:
        try:
            sub = raw[ticker] if multi else raw
        except KeyError:
            continue
        sub = _clean(sub)
        if len(sub) > 0:
            result[ticker] = sub
    return result


def get_data(tickers, timeframe, use_cache=True):
    """Cached wrapper around fetch()."""
    key = (timeframe, tuple(sorted(tickers)))
    now = time.time()
    if use_cache and key in _cache:
        stamp, data = _cache[key]
        if now - stamp < CACHE_TTL:
            return data
    data = fetch(tickers, timeframe)
    _cache[key] = (now, data)
    return data


def clear_cache():
    _cache.clear()
