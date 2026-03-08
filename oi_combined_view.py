#!/usr/bin/env python3
"""Combined Open Interest monitor (Binance + OKX, no paid API)."""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json",
}

BINANCE_PERIODS = {"5m", "15m", "30m", "1h", "2h", "4h", "6h", "12h", "1d"}
OKX_PERIOD_MAP = {"5m": "5m", "1h": "1H", "1d": "1D"}
PROFILE_TIMEFRAME_MAP = {"ltf": "5m", "mtf": "1h", "htf": "4h"}
COINALYZE_INTERVAL_MAP = {"5m": "5min", "15m": "15min", "30m": "30min", "1h": "1hour", "2h": "2hour", "4h": "4hour", "6h": "6hour", "12h": "12hour", "1d": "daily"}
_COINALYZE_SYMBOL_CACHE: Dict[str, str] = {}


def http_get_json(url: str) -> Any:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def to_float(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def fetch_binance_oi(symbol: str, period: str) -> Dict[str, Any]:
    market_symbol = f"{symbol}USDT"
    period = period if period in BINANCE_PERIODS else "1h"

    base = "https://fapi.binance.com/futures/data/openInterestHist"
    q = urllib.parse.urlencode({"symbol": market_symbol, "period": period, "limit": 2})
    rows = http_get_json(f"{base}?{q}")

    if not isinstance(rows, list) or not rows:
        raise RuntimeError("No Binance OI history data")

    latest = rows[-1]
    prev = rows[-2] if len(rows) > 1 else rows[-1]

    latest_notional = to_float(latest.get("sumOpenInterestValue"))
    prev_notional = to_float(prev.get("sumOpenInterestValue"))

    return {
        "exchange": "BINANCE",
        "symbol": market_symbol,
        "timeframe": period,
        "oi_notional_usd": latest_notional,
        "oi_notional_change_usd": latest_notional - prev_notional,
        "timestamp": latest.get("timestamp"),
    }


def fetch_okx_oi(symbol: str, period: str) -> Dict[str, Any]:
    okx_period = OKX_PERIOD_MAP.get(period)
    if not okx_period:
        raise RuntimeError("OKX supports only 5m, 1h, 1d in this script")

    inst_id = f"{symbol}-USDT-SWAP"
    url = (
        "https://www.okx.com/api/v5/rubik/stat/contracts/open-interest-history?"
        + urllib.parse.urlencode({"instId": inst_id, "period": okx_period})
    )
    payload = http_get_json(url)
    data = payload.get("data", []) if isinstance(payload, dict) else []
    if not isinstance(data, list) or not data:
        raise RuntimeError("No OKX OI history data")

    latest = data[0]
    prev = data[1] if len(data) > 1 else data[0]

    # OKX row shape: [ts, oi_contracts, oi_ccy, oi_notional]
    latest_notional = to_float(latest[3]) if len(latest) > 3 else 0.0
    prev_notional = to_float(prev[3]) if len(prev) > 3 else 0.0

    return {
        "exchange": "OKX",
        "symbol": inst_id,
        "timeframe": okx_period,
        "oi_notional_usd": latest_notional,
        "oi_notional_change_usd": latest_notional - prev_notional,
        "timestamp": latest[0] if latest else None,
    }


def fetch_symbol(symbol: str, timeframe: str) -> Dict[str, Any]:
    out: Dict[str, Any] = {"symbol": symbol.upper(), "venues": [], "errors": []}
    for fn in (fetch_binance_oi, fetch_okx_oi):
        try:
            out["venues"].append(fn(symbol.upper(), timeframe))
        except Exception as exc:
            name = "BINANCE" if fn is fetch_binance_oi else "OKX"
            out["errors"].append(f"{name}: {exc}")

    out["combined_oi_notional_usd"] = sum(v["oi_notional_usd"] for v in out["venues"])
    out["combined_oi_change_usd"] = sum(v["oi_notional_change_usd"] for v in out["venues"])
    return out


def fetch_coinalyze_market_symbol(symbol: str, api_key: str, exchange_code: str = "A") -> str:
    key = f"{symbol}:{exchange_code}"
    if key in _COINALYZE_SYMBOL_CACHE:
        return _COINALYZE_SYMBOL_CACHE[key]
    url = "https://api.coinalyze.net/v1/future-markets?" + urllib.parse.urlencode({"api_key": api_key})
    rows = http_get_json(url)
    if not isinstance(rows, list):
        return ""
    picks: List[str] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        if str(r.get("base_asset", "")).upper() != symbol.upper():
            continue
        if str(r.get("exchange", "")).upper() != exchange_code:
            continue
        s = str(r.get("symbol", "")).strip()
        if s:
            picks.append(s)
    if not picks:
        return ""
    picks.sort(key=lambda x: (0 if "USDT" in x else 1, 0 if "PERP" in x else 1, len(x)))
    chosen = picks[0]
    _COINALYZE_SYMBOL_CACHE[key] = chosen
    return chosen


def fetch_symbol_coinalyze(symbol: str, timeframe: str, api_key: str) -> Dict[str, Any]:
    out: Dict[str, Any] = {"symbol": symbol.upper(), "venues": [], "errors": []}
    market_symbol = fetch_coinalyze_market_symbol(symbol, api_key, "A")
    if not market_symbol:
        out["errors"].append("COINALYZE: symbol_not_found")
        out["combined_oi_notional_usd"] = 0.0
        out["combined_oi_change_usd"] = 0.0
        return out

    interval = COINALYZE_INTERVAL_MAP.get(timeframe, "1hour")
    now_ts = int(time.time())
    from_ts = now_ts - (3 * 24 * 3600)
    q = urllib.parse.urlencode(
        {"symbols": market_symbol, "interval": interval, "from": from_ts, "to": now_ts, "api_key": api_key}
    )
    url = f"https://api.coinalyze.net/v1/open-interest-history?{q}"
    payload = http_get_json(url)
    if not isinstance(payload, list) or not payload:
        out["errors"].append("COINALYZE: empty_open_interest_history")
        out["combined_oi_notional_usd"] = 0.0
        out["combined_oi_change_usd"] = 0.0
        return out

    first = payload[0] if isinstance(payload[0], dict) else {}
    hist = first.get("history", []) if isinstance(first, dict) else []
    if not isinstance(hist, list) or not hist:
        out["errors"].append("COINALYZE: empty_history")
        out["combined_oi_notional_usd"] = 0.0
        out["combined_oi_change_usd"] = 0.0
        return out

    latest = hist[-1]
    prev = hist[-2] if len(hist) > 1 else hist[-1]
    oi_now = to_float(latest.get("c")) if isinstance(latest, dict) else 0.0
    oi_prev = to_float(prev.get("c")) if isinstance(prev, dict) else 0.0
    out["venues"].append(
        {
            "exchange": "COINALYZE_BINANCE",
            "symbol": market_symbol,
            "timeframe": interval,
            "oi_notional_usd": oi_now,
            "oi_notional_change_usd": oi_now - oi_prev,
            "timestamp": latest.get("t") if isinstance(latest, dict) else None,
        }
    )
    out["combined_oi_notional_usd"] = oi_now
    out["combined_oi_change_usd"] = oi_now - oi_prev
    return out


def parse_symbols(raw: str) -> List[str]:
    return [s.strip().upper() for s in raw.split(",") if s.strip()]


def resolve_timeframe(profile: str, timeframe: str) -> Tuple[str, str]:
    if profile in PROFILE_TIMEFRAME_MAP:
        return PROFILE_TIMEFRAME_MAP[profile], profile
    return timeframe, "custom"


def as_table(results: List[Dict[str, Any]], timeframe: str) -> str:
    lines = [f"Combined OI View ({timeframe})", ""]
    for row in results:
        lines.append(f"{row['symbol']}")
        lines.append(f"  Combined OI Notional: ${row['combined_oi_notional_usd']:.2f}")
        lines.append(f"  Combined OI Change:   ${row['combined_oi_change_usd']:.2f}")
        for v in row["venues"]:
            lines.append(
                f"  - {v['exchange']}: ${v['oi_notional_usd']:.2f} (chg ${v['oi_notional_change_usd']:.2f})"
            )
        if row["errors"]:
            lines.append(f"  Errors: {' | '.join(row['errors'])}")
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    p = argparse.ArgumentParser(description="Combined Binance+OKX open interest view")
    p.add_argument("--symbols", required=True, help="Comma-separated symbols, e.g. BTC,ETH,XRP")
    p.add_argument("--profile", choices=["custom", "ltf", "mtf", "htf"], default="mtf")
    p.add_argument("--source", choices=["auto", "exchange_api", "coinalyze"], default="auto")
    p.add_argument("--timeframe", default="1h", help="5m|15m|30m|1h|2h|4h|6h|12h|1d")
    p.add_argument("--format", choices=["table", "json"], default="table")
    args = p.parse_args()

    effective_timeframe, effective_profile = resolve_timeframe(args.profile, args.timeframe)
    symbols = parse_symbols(args.symbols)
    coinalyze_key = os.environ.get("COINALYZE_API_KEY", "").strip()
    results: List[Dict[str, Any]] = []
    for s in symbols:
        if args.source == "exchange_api":
            results.append(fetch_symbol(s, effective_timeframe))
        elif args.source == "coinalyze":
            if not coinalyze_key:
                results.append({"symbol": s, "venues": [], "errors": ["COINALYZE: missing_api_key"], "combined_oi_notional_usd": 0.0, "combined_oi_change_usd": 0.0})
            else:
                results.append(fetch_symbol_coinalyze(s, effective_timeframe, coinalyze_key))
        else:
            row = fetch_symbol(s, effective_timeframe)
            if row["venues"]:
                results.append(row)
            elif coinalyze_key:
                results.append(fetch_symbol_coinalyze(s, effective_timeframe, coinalyze_key))
            else:
                results.append(row)

    if args.format == "json":
        print(
            json.dumps(
                {"profile": effective_profile, "timeframe": effective_timeframe, "results": results},
                indent=2,
            )
        )
    else:
        print(as_table(results, effective_timeframe))


if __name__ == "__main__":
    main()

