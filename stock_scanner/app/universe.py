"""Resolves the universe of tickers to scan.

S&P 500 and Nasdaq-100 constituents are fetched from Wikipedia and cached
locally for a week. If the fetch fails, a bundled fallback list is used.
"""
import json
import os
import time

CACHE_DIR = os.path.join(os.path.dirname(__file__), "_cache")
CACHE_TTL = 7 * 24 * 3600  # one week

SP500_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
NASDAQ100_URL = "https://en.wikipedia.org/wiki/Nasdaq-100"

# Bundled fallbacks (large, liquid names) used only when Wikipedia is unreachable.
FALLBACK_SP500 = [
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "GOOG", "META", "BRK-B", "LLY",
    "AVGO", "JPM", "TSLA", "V", "XOM", "UNH", "MA", "JNJ", "PG", "HD", "COST",
    "ABBV", "MRK", "CVX", "CRM", "AMD", "WMT", "BAC", "KO", "PEP", "NFLX",
    "TMO", "ADBE", "LIN", "MCD", "CSCO", "ACN", "ABT", "ORCL", "DHR", "WFC",
    "TXN", "DIS", "INTC", "VZ", "INTU", "PM", "CMCSA", "NKE", "QCOM", "IBM",
    "AMGN", "NEE", "GE", "CAT", "HON", "UNP", "LOW", "SPGI", "BA", "AXP",
    "PFE", "GS", "BLK", "RTX", "PLD", "ELV", "SBUX", "BKNG", "MDT", "DE",
    "LMT", "ADP", "TJX", "GILD", "MMC", "CVS", "MDLZ", "AMT", "C", "ISRG",
    "SYK", "REGN", "VRTX", "NOW", "ZTS", "CB", "BSX", "SO", "CI", "MO",
    "PGR", "DUK", "BDX", "SLB", "EOG", "ITW", "AON", "CME", "PNC", "USB",
    "MU", "APD", "CL", "MMM", "EW", "GD", "NSC", "FCX", "HUM", "MAR", "ICE",
]

FALLBACK_NASDAQ100 = [
    "AAPL", "MSFT", "NVDA", "AMZN", "AVGO", "META", "GOOGL", "GOOG", "TSLA",
    "COST", "NFLX", "AMD", "PEP", "ADBE", "CSCO", "TMUS", "INTC", "CMCSA",
    "QCOM", "INTU", "AMGN", "TXN", "HON", "AMAT", "BKNG", "SBUX", "ISRG",
    "VRTX", "GILD", "ADI", "MDLZ", "REGN", "ADP", "LRCX", "PANW", "MU",
    "SNPS", "CDNS", "MELI", "KLAC", "PYPL", "MAR", "ABNB", "ORLY", "CTAS",
    "NXPI", "ASML", "CRWD", "WDAY", "MNST", "CSX", "FTNT", "PCAR", "PAYX",
    "ROP", "MRVL", "ADSK", "DXCM", "AEP", "KDP", "CHTR", "KHC", "EXC",
    "IDXX", "FAST", "CPRT", "EA", "BIIB", "CTSH", "GEHC", "ON", "DDOG",
    "TTD", "TEAM", "ZS", "ANSS", "DLTR", "WBD", "XEL", "CCEP", "TTWO",
    "ROST", "VRSK", "CSGP", "BKR", "FANG", "MCHP", "AZN", "PDD", "ARM",
    "LULU", "MDB", "CDW",
]


def _read_cache(name):
    path = os.path.join(CACHE_DIR, name + ".json")
    if os.path.exists(path) and (time.time() - os.path.getmtime(path)) < CACHE_TTL:
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, list) and data:
                return data
        except (json.JSONDecodeError, OSError):
            pass
    return None


def _write_cache(name, tickers):
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(os.path.join(CACHE_DIR, name + ".json"), "w", encoding="utf-8") as fh:
            json.dump(tickers, fh)
    except OSError:
        pass


def _normalize(symbols):
    """Yahoo Finance uses '-' where exchanges use '.' (e.g. BRK-B)."""
    out = []
    for sym in symbols:
        sym = str(sym).strip().upper().replace(".", "-")
        if sym and sym != "NAN":
            out.append(sym)
    return out


def get_sp500():
    cached = _read_cache("sp500")
    if cached:
        return cached
    try:
        import pandas as pd

        tables = pd.read_html(SP500_URL)
        tickers = _normalize(tables[0]["Symbol"].tolist())
        if tickers:
            _write_cache("sp500", tickers)
            return tickers
    except Exception:
        pass
    return list(FALLBACK_SP500)


def get_nasdaq100():
    cached = _read_cache("nasdaq100")
    if cached:
        return cached
    try:
        import pandas as pd

        tables = pd.read_html(NASDAQ100_URL)
        for table in tables:
            ticker_cols = [c for c in table.columns if "Ticker" in str(c)]
            if ticker_cols:
                tickers = _normalize(table[ticker_cols[0]].tolist())
                if len(tickers) > 50:
                    _write_cache("nasdaq100", tickers)
                    return tickers
    except Exception:
        pass
    return list(FALLBACK_NASDAQ100)


def resolve_universe(universe_cfg):
    """Return the list of tickers for the configured universe."""
    kind = (universe_cfg or {}).get("type", "sp500")
    if kind == "custom":
        raw = universe_cfg.get("custom_tickers", [])
        return _normalize(raw)
    if kind == "nasdaq100":
        return get_nasdaq100()
    if kind == "both":
        return sorted(set(get_sp500()) | set(get_nasdaq100()))
    return get_sp500()
