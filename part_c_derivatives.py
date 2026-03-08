#!/usr/bin/env python3
"""Standalone derivatives module (Part C): OI, funding, and perp volume."""

from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import time
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Tuple

HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}

PROFILE_MAP: Dict[str, Dict[str, str]] = {
    "ltf": {"event_i": "15m", "context_i": "1h"},
    "mtf": {"event_i": "1h", "context_i": "4h"},
    "htf": {"event_i": "4h", "context_i": "1d"},
}

PROFILE_WEIGHT_MAP: Dict[str, Dict[str, float]] = {
    "ltf": {"w_oi": 0.30, "w_volume": 0.50, "w_funding": 0.20},
    "mtf": {"w_oi": 0.40, "w_volume": 0.35, "w_funding": 0.25},
    "htf": {"w_oi": 0.50, "w_volume": 0.20, "w_funding": 0.30},
    "custom": {"w_oi": 0.40, "w_volume": 0.35, "w_funding": 0.25},
}

LS_ALLOWED_TIMEFRAMES = {"5m", "15m", "30m", "1h", "4h", "1d"}
DEFAULT_LS_TIMEFRAMES = ["5m", "15m", "30m", "1h", "4h", "1d"]

OKX_BAR_MAP = {
    "15m": "15m",
    "1h": "1H",
    "4h": "4H",
    "1d": "1D",
}

OKX_OI_PERIOD_MAP = {
    "15m": "5m",
    "1h": "1H",
    "4h": "1H",
    "1d": "1D",
}

BINANCE_OI_PERIODS = {"5m", "15m", "30m", "1h", "2h", "4h", "6h", "12h", "1d"}
BINANCE_KLINE_INTERVALS = {
    "1m",
    "3m",
    "5m",
    "15m",
    "30m",
    "1h",
    "2h",
    "4h",
    "6h",
    "8h",
    "12h",
    "1d",
    "3d",
    "1w",
    "1M",
}

COINALYZE_INTERVAL_MAP = {
    "15m": "15min",
    "30m": "30min",
    "1h": "1hour",
    "4h": "4hour",
    "1d": "daily",
}

COINALYZE_DEFAULT_EXCHANGE = "A"  # Binance
_COINALYZE_SYMBOL_CACHE: Dict[str, str] = {}


def http_get_json(url: str) -> Any:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=25) as resp:
        return json.loads(resp.read().decode("utf-8"))


def safe_http_get_json(url: str) -> Any:
    try:
        return http_get_json(url)
    except Exception:
        return None


def to_float(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def parse_symbols(raw: str) -> List[str]:
    return [x.strip().upper() for x in raw.split(",") if x.strip()]


def resolve_profile(profile: str, event_i: str, context_i: str) -> Tuple[str, str, str]:
    if profile in PROFILE_MAP:
        cfg = PROFILE_MAP[profile]
        return cfg["event_i"], cfg["context_i"], profile
    return event_i, context_i, "custom"


def pct_change(now_val: float, prev_val: float) -> float:
    if prev_val == 0:
        return 0.0
    return ((now_val - prev_val) / abs(prev_val)) * 100.0


def fetch_binance_oi(symbol: str, interval: str) -> Tuple[float, float]:
    period = interval if interval in BINANCE_OI_PERIODS else "1h"
    market_symbol = f"{symbol}USDT"
    q = urllib.parse.urlencode({"symbol": market_symbol, "period": period, "limit": 2})
    url = f"https://fapi.binance.com/futures/data/openInterestHist?{q}"
    rows = safe_http_get_json(url)
    if not isinstance(rows, list) or not rows:
        return 0.0, 0.0

    latest = rows[-1]
    prev = rows[-2] if len(rows) > 1 else rows[-1]
    oi_now = to_float(latest.get("sumOpenInterestValue"))
    oi_prev = to_float(prev.get("sumOpenInterestValue"))
    return oi_now, oi_prev


def fetch_binance_perp_quote_volume(symbol: str, interval: str) -> Tuple[float, float]:
    kline_interval = interval if interval in BINANCE_KLINE_INTERVALS else "1h"
    market_symbol = f"{symbol}USDT"
    q = urllib.parse.urlencode({"symbol": market_symbol, "interval": kline_interval, "limit": 2})
    url = f"https://fapi.binance.com/fapi/v1/klines?{q}"
    rows = safe_http_get_json(url)
    if not isinstance(rows, list) or not rows:
        return 0.0, 0.0

    latest = rows[-1]
    prev = rows[-2] if len(rows) > 1 else rows[-1]
    vol_now = to_float(latest[7]) if len(latest) > 7 else 0.0
    vol_prev = to_float(prev[7]) if len(prev) > 7 else 0.0
    return vol_now, vol_prev


def fetch_binance_funding(symbol: str, limit: int = 10) -> Tuple[float, float]:
    market_symbol = f"{symbol}USDT"
    q = urllib.parse.urlencode({"symbol": market_symbol, "limit": max(limit, 2)})
    url = f"https://fapi.binance.com/fapi/v1/fundingRate?{q}"
    rows = safe_http_get_json(url)
    if not isinstance(rows, list) or not rows:
        return 0.0, 0.0

    vals = [to_float(r.get("fundingRate")) for r in rows if isinstance(r, dict)]
    if not vals:
        return 0.0, 0.0
    now_val = vals[-1]
    avg_val = statistics.mean(vals)
    return now_val, avg_val


def fetch_okx_oi(symbol: str, interval: str) -> Tuple[float, float]:
    inst_id = f"{symbol}-USDT-SWAP"
    period = OKX_OI_PERIOD_MAP.get(interval, "1H")
    q = urllib.parse.urlencode({"instId": inst_id, "period": period})
    url = f"https://www.okx.com/api/v5/rubik/stat/contracts/open-interest-history?{q}"
    payload = safe_http_get_json(url)
    data = payload.get("data", []) if isinstance(payload, dict) else []
    if not isinstance(data, list) or not data:
        return 0.0, 0.0

    latest = data[0]
    prev = data[1] if len(data) > 1 else data[0]
    oi_now = to_float(latest[3]) if isinstance(latest, list) and len(latest) > 3 else 0.0
    oi_prev = to_float(prev[3]) if isinstance(prev, list) and len(prev) > 3 else 0.0
    return oi_now, oi_prev


def fetch_okx_perp_quote_volume(symbol: str, interval: str) -> Tuple[float, float]:
    inst_id = f"{symbol}-USDT-SWAP"
    bar = OKX_BAR_MAP.get(interval, "1H")
    q = urllib.parse.urlencode({"instId": inst_id, "bar": bar, "limit": 2})
    url = f"https://www.okx.com/api/v5/market/candles?{q}"
    payload = safe_http_get_json(url)
    data = payload.get("data", []) if isinstance(payload, dict) else []
    if not isinstance(data, list) or not data:
        return 0.0, 0.0

    latest = data[0]
    prev = data[1] if len(data) > 1 else data[0]
    # OKX candles: [ts,o,h,l,c,vol,volCcy,volCcyQuote,confirm]
    vol_now = to_float(latest[7]) if isinstance(latest, list) and len(latest) > 7 else 0.0
    vol_prev = to_float(prev[7]) if isinstance(prev, list) and len(prev) > 7 else 0.0
    return vol_now, vol_prev


def fetch_okx_funding(symbol: str, limit: int = 10) -> Tuple[float, float]:
    inst_id = f"{symbol}-USDT-SWAP"

    q_now = urllib.parse.urlencode({"instId": inst_id})
    url_now = f"https://www.okx.com/api/v5/public/funding-rate?{q_now}"
    payload_now = safe_http_get_json(url_now)
    now_rows = payload_now.get("data", []) if isinstance(payload_now, dict) else []
    now_val = to_float(now_rows[0].get("fundingRate")) if now_rows and isinstance(now_rows[0], dict) else 0.0

    q_hist = urllib.parse.urlencode({"instId": inst_id, "limit": max(limit, 2)})
    url_hist = f"https://www.okx.com/api/v5/public/funding-rate-history?{q_hist}"
    payload_hist = safe_http_get_json(url_hist)
    hist_rows = payload_hist.get("data", []) if isinstance(payload_hist, dict) else []
    hist_vals = [to_float(r.get("fundingRate")) for r in hist_rows if isinstance(r, dict)]
    avg_val = statistics.mean(hist_vals) if hist_vals else now_val
    return now_val, avg_val


def fetch_api_metrics(symbol: str, event_i: str, context_i: str) -> Dict[str, float]:
    oi_now, oi_prev = fetch_binance_oi(symbol, event_i)
    vol_now, vol_prev = fetch_binance_perp_quote_volume(symbol, event_i)
    funding_now, funding_avg = fetch_binance_funding(symbol, limit=10)
    source = "BINANCE"

    if oi_now == 0.0 and vol_now == 0.0:
        oi_now, oi_prev = fetch_okx_oi(symbol, event_i)
        vol_now, vol_prev = fetch_okx_perp_quote_volume(symbol, event_i)
        funding_now, funding_avg = fetch_okx_funding(symbol, limit=10)
        source = "OKX"

    return {
        "source": source,
        "oi_now_usd": oi_now,
        "oi_prev_usd": oi_prev,
        "oi_change_pct": pct_change(oi_now, oi_prev),
        "perp_volume_now_usd": vol_now,
        "perp_volume_prev_usd": vol_prev,
        "perp_volume_change_pct": pct_change(vol_now, vol_prev),
        "funding_now": funding_now,
        "funding_avg": funding_avg,
        "funding_delta": funding_now - funding_avg,
        "context_interval": context_i,
    }


def fetch_coinalyze_market_symbol(symbol: str, cm_key: str, exchange_code: str = COINALYZE_DEFAULT_EXCHANGE) -> str:
    key = f"{symbol}:{exchange_code}"
    if key in _COINALYZE_SYMBOL_CACHE:
        return _COINALYZE_SYMBOL_CACHE[key]

    q = urllib.parse.urlencode({"api_key": cm_key})
    url = f"https://api.coinalyze.net/v1/future-markets?{q}"
    rows = safe_http_get_json(url)
    if not isinstance(rows, list):
        return ""

    prefix = symbol.upper()
    picks: List[str] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        base = str(r.get("base_asset", "")).upper()
        exch = str(r.get("exchange", "")).upper()
        s = str(r.get("symbol", "")).strip()
        if base == prefix and s and (not exchange_code or exch == exchange_code):
            picks.append(s)
    if not picks:
        return ""

    # Prefer USDT perpetual symbol names when available.
    picks.sort(key=lambda x: (0 if "USDT" in x else 1, 0 if "PERP" in x else 1, len(x)))
    chosen = picks[0]
    _COINALYZE_SYMBOL_CACHE[key] = chosen
    return chosen


def fetch_coinalyze_history(endpoint: str, market_symbol: str, interval: str, cm_key: str) -> List[Dict[str, Any]]:
    c_interval = COINALYZE_INTERVAL_MAP.get(interval, "1hour")
    now_ts = int(time.time())
    from_ts = now_ts - (3 * 24 * 3600)
    q = urllib.parse.urlencode(
        {
            "symbols": market_symbol,
            "interval": c_interval,
            "from": from_ts,
            "to": now_ts,
            "api_key": cm_key,
        }
    )
    url = f"https://api.coinalyze.net/v1/{endpoint}?{q}"
    payload = safe_http_get_json(url)
    if not isinstance(payload, list) or not payload:
        return []
    first = payload[0]
    if not isinstance(first, dict):
        return []
    hist = first.get("history", [])
    if not isinstance(hist, list):
        return []
    return [h for h in hist if isinstance(h, dict)]


def fetch_coinalyze_metrics(symbol: str, event_i: str, context_i: str, cm_key: str) -> Dict[str, float]:
    market_symbol = fetch_coinalyze_market_symbol(symbol, cm_key, COINALYZE_DEFAULT_EXCHANGE)
    if not market_symbol:
        return {
            "source": "COINALYZE",
            "oi_now_usd": 0.0,
            "oi_prev_usd": 0.0,
            "oi_change_pct": 0.0,
            "perp_volume_now_usd": 0.0,
            "perp_volume_prev_usd": 0.0,
            "perp_volume_change_pct": 0.0,
            "funding_now": 0.0,
            "funding_avg": 0.0,
            "funding_delta": 0.0,
            "context_interval": context_i,
        }

    oi_hist = fetch_coinalyze_history("open-interest-history", market_symbol, event_i, cm_key)
    fr_hist = fetch_coinalyze_history("funding-rate-history", market_symbol, event_i, cm_key)
    vol_hist = fetch_coinalyze_history("ohlcv-history", market_symbol, event_i, cm_key)

    oi_now = to_float(oi_hist[-1].get("c")) if oi_hist else 0.0
    oi_prev = to_float(oi_hist[-2].get("c")) if len(oi_hist) > 1 else (to_float(oi_hist[-1].get("o")) if oi_hist else 0.0)

    funding_now = to_float(fr_hist[-1].get("c")) if fr_hist else 0.0
    funding_vals = [to_float(x.get("c")) for x in fr_hist] if fr_hist else []
    funding_avg = statistics.mean(funding_vals) if funding_vals else funding_now

    # ohlcv-history uses trade volume (notional may vary by market); we only use change%.
    vol_now = to_float(vol_hist[-1].get("v")) if vol_hist else 0.0
    vol_prev = to_float(vol_hist[-2].get("v")) if len(vol_hist) > 1 else (to_float(vol_hist[-1].get("v")) if vol_hist else 0.0)

    return {
        "source": "COINALYZE",
        "oi_now_usd": oi_now,
        "oi_prev_usd": oi_prev,
        "oi_change_pct": pct_change(oi_now, oi_prev),
        "perp_volume_now_usd": vol_now,
        "perp_volume_prev_usd": vol_prev,
        "perp_volume_change_pct": pct_change(vol_now, vol_prev),
        "funding_now": funding_now,
        "funding_avg": funding_avg,
        "funding_delta": funding_now - funding_avg,
        "context_interval": context_i,
    }


def parse_timeframes(raw: str) -> List[str]:
    out: List[str] = []
    for t in [x.strip().lower() for x in raw.split(",") if x.strip()]:
        if t in LS_ALLOWED_TIMEFRAMES:
            out.append(t)
    return out


def fetch_cm_long_short_pressure(symbol: str, exchange: str, timeframes: List[str], cm_key: str) -> Dict[str, float]:
    out: Dict[str, float] = {}
    if not cm_key or not timeframes:
        return out
    for tf in timeframes:
        q = urllib.parse.urlencode(
            {
                "e": exchange,
                "symbol": symbol,
                "timeframe": tf,
                "api_key": cm_key,
            }
        )
        url = f"https://api.cryptometer.io/long-shorts-data/?{q}"
        payload = safe_http_get_json(url)
        if not isinstance(payload, dict):
            continue
        data = payload.get("data", [])
        if not isinstance(data, list) or not data:
            continue
        row = data[0]
        longs = to_float(row.get("longs"))
        shorts = to_float(row.get("shorts"))
        denom = longs + shorts
        out[tf] = clamp((longs - shorts) / denom, -1.0, 1.0) if denom > 0 else 0.0
    return out


def aggregate_ls_score(ls_map: Dict[str, float], timeframes: List[str]) -> Tuple[float, str]:
    pairs: List[Tuple[float, str]] = []
    for tf in timeframes:
        if tf in ls_map:
            pairs.append((ls_map[tf], tf))
    if not pairs:
        return 0.0, ""
    score = sum(p[0] for p in pairs) / float(len(pairs))
    detail = ",".join(f"{p[1]}={p[0]:.3f}" for p in pairs)
    return clamp(score, -1.0, 1.0), detail


def compute_derivatives_score(
    oi_change_pct: float,
    perp_volume_change_pct: float,
    funding_now: float,
    funding_avg: float,
    w_oi: float,
    w_volume: float,
    w_funding: float,
) -> Tuple[float, str, str, Dict[str, float]]:
    oi_term = math.tanh(oi_change_pct / 12.0)
    vol_term = math.tanh(perp_volume_change_pct / 20.0)
    funding_scale = max(abs(funding_avg), 0.0001)
    funding_term = math.tanh(funding_now / (2.0 * funding_scale))

    score = (w_oi * oi_term) + (w_volume * vol_term) + (w_funding * funding_term)
    score = clamp(score, -1.0, 1.0)

    if score >= 0.25:
        state = "bullish"
    elif score <= -0.25:
        state = "bearish"
    else:
        state = "neutral"

    reason = (
        f"oi_chg={oi_change_pct:.2f}% vol_chg={perp_volume_change_pct:.2f}% "
        f"funding={funding_now:.6f} funding_avg={funding_avg:.6f}"
    )
    terms = {
        "oi_term": oi_term,
        "volume_term": vol_term,
        "funding_term": funding_term,
    }
    return score, state, reason, terms


def apply_overheat_gate(
    state: str,
    oi_change_pct: float,
    perp_volume_change_pct: float,
    funding_now: float,
    funding_avg: float,
    oi_gate_gte: float,
    volume_gate_gte: float,
    funding_z_gate_gte: float,
) -> Tuple[str, bool, str]:
    funding_scale = max(abs(funding_avg), 0.0001)
    funding_z = funding_now / funding_scale
    triggered = (
        state == "bullish"
        and oi_change_pct >= oi_gate_gte
        and perp_volume_change_pct >= volume_gate_gte
        and funding_z >= funding_z_gate_gte
    )
    if triggered:
        return (
            "bullish_crowded",
            True,
            f"overheat: oi>={oi_gate_gte:.1f}% vol>={volume_gate_gte:.1f}% funding_z={funding_z:.2f}>= {funding_z_gate_gte:.2f}",
        )
    return state, False, ""


def as_table(rows: List[Dict[str, Any]], profile: str, event_i: str, context_i: str) -> str:
    lines = [f"Derivatives Module (Part C) profile={profile} event={event_i} context={context_i}", ""]
    for r in rows:
        if "error" in r:
            lines.append(f"{r['symbol']}: ERROR {r['error']}")
            continue
        lines.append(f"{r['symbol']}: {r['derivatives_state']} score={r['derivatives_score']:.3f}")
        lines.append(f"  Reason: {r['derivatives_reason']}")
        if r.get("overheat_gate_triggered"):
            lines.append(f"  Gate: {r.get('overheat_gate_reason', '')}")
        ls = r.get("long_short", {})
        if ls.get("enabled"):
            if ls.get("used"):
                lines.append(
                    f"  LS: state={ls.get('state')} score={ls.get('score', 0.0):.3f} [{ls.get('detail', '')}]"
                )
            else:
                lines.append("  LS: enabled but unavailable (missing API key/data)")
        m = r["metrics"]
        lines.append(
            "  Metrics: "
            f"src={m['source']} "
            f"oi_now=${m['oi_now_usd']:.0f} oi_prev=${m['oi_prev_usd']:.0f} oi_chg={m['oi_change_pct']:.2f}% "
            f"perp_vol_now=${m['perp_volume_now_usd']:.0f} perp_vol_prev=${m['perp_volume_prev_usd']:.0f} vol_chg={m['perp_volume_change_pct']:.2f}% "
            f"funding={m['funding_now']:.6f} funding_avg={m['funding_avg']:.6f}"
        )
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    p = argparse.ArgumentParser(description="Standalone derivatives module (Part C)")
    p.add_argument("--symbols", required=True, help="Comma-separated symbols, e.g. BTC,ETH,XRP")
    p.add_argument("--profile", choices=["custom", "ltf", "mtf", "htf"], default="mtf")
    p.add_argument(
        "--source",
        choices=["auto", "api", "exchange_api", "coinalyze", "manual"],
        default="auto",
        help="Data source: auto/exchange_api/coinalyze/manual (api alias -> exchange_api)",
    )

    p.add_argument("--event-interval", default="1h")
    p.add_argument("--context-interval", default="4h")

    p.add_argument("--manual-oi-change-pct", type=float, default=0.0)
    p.add_argument("--manual-perp-volume-change-pct", type=float, default=0.0)
    p.add_argument("--manual-funding-now", type=float, default=0.0)
    p.add_argument("--manual-funding-avg", type=float, default=0.0)

    p.add_argument("--w-oi", type=float, default=None)
    p.add_argument("--w-volume", type=float, default=None)
    p.add_argument("--w-funding", type=float, default=None)
    p.add_argument("--w-ls", type=float, default=0.15, help="Long/short blend weight [0..1]")
    p.add_argument("--ls-enabled", choices=["on", "off"], default="on")
    p.add_argument("--cm-ls-exchange", default="binance_futures")
    p.add_argument("--cm-ls-timeframes", default="", help="Override CSV: 5m,15m,30m,1h,4h,1d")

    p.add_argument("--overheat-gate", choices=["on", "off"], default="on")
    p.add_argument("--overheat-oi-gte", type=float, default=8.0)
    p.add_argument("--overheat-volume-gte", type=float, default=18.0)
    p.add_argument("--overheat-funding-z-gte", type=float, default=2.5)

    p.add_argument("--format", choices=["table", "json"], default="table")
    args = p.parse_args()

    event_i, context_i, effective_profile = resolve_profile(args.profile, args.event_interval, args.context_interval)
    profile_weights = PROFILE_WEIGHT_MAP.get(effective_profile, PROFILE_WEIGHT_MAP["custom"])
    w_oi = args.w_oi if args.w_oi is not None else profile_weights["w_oi"]
    w_volume = args.w_volume if args.w_volume is not None else profile_weights["w_volume"]
    w_funding = args.w_funding if args.w_funding is not None else profile_weights["w_funding"]
    w_ls = clamp(args.w_ls, 0.0, 1.0)

    ls_timeframes = parse_timeframes(args.cm_ls_timeframes) if args.cm_ls_timeframes.strip() else list(DEFAULT_LS_TIMEFRAMES)
    cm_key = os.environ.get("CRYPTOMETER_API_KEY", "").strip()
    coinalyze_key = os.environ.get("COINALYZE_API_KEY", "").strip()

    symbols = parse_symbols(args.symbols)
    results: List[Dict[str, Any]] = []

    for symbol in symbols:
        source_choice = "exchange_api" if args.source == "api" else args.source
        if source_choice in ("exchange_api", "coinalyze", "auto"):
            metrics = None
            if source_choice in ("exchange_api", "auto"):
                m1 = fetch_api_metrics(symbol, event_i, context_i)
                if not (m1["oi_now_usd"] == 0.0 and m1["perp_volume_now_usd"] == 0.0):
                    metrics = m1
            if metrics is None and source_choice in ("coinalyze", "auto") and coinalyze_key:
                m2 = fetch_coinalyze_metrics(symbol, event_i, context_i, coinalyze_key)
                if not (m2["oi_now_usd"] == 0.0 and m2["perp_volume_now_usd"] == 0.0):
                    metrics = m2
            if metrics is None:
                err = "insufficient_derivatives_data"
                if source_choice in ("coinalyze", "auto") and not coinalyze_key and source_choice == "coinalyze":
                    err = "missing_coinalyze_api_key"
                results.append({"symbol": symbol, "error": err})
                continue
        else:
            metrics = {
                "source": "MANUAL",
                "oi_now_usd": 0.0,
                "oi_prev_usd": 0.0,
                "oi_change_pct": args.manual_oi_change_pct,
                "perp_volume_now_usd": 0.0,
                "perp_volume_prev_usd": 0.0,
                "perp_volume_change_pct": args.manual_perp_volume_change_pct,
                "funding_now": args.manual_funding_now,
                "funding_avg": args.manual_funding_avg,
                "funding_delta": args.manual_funding_now - args.manual_funding_avg,
                "context_interval": context_i,
            }

        score, state, reason, terms = compute_derivatives_score(
            metrics["oi_change_pct"],
            metrics["perp_volume_change_pct"],
            metrics["funding_now"],
            metrics["funding_avg"],
            w_oi,
            w_volume,
            w_funding,
        )
        base_score = score
        base_state = state

        ls_map: Dict[str, float] = {}
        ls_score = 0.0
        ls_detail = ""
        ls_used = False
        ls_state = "neutral"
        if args.ls_enabled == "on" and cm_key:
            ls_map = fetch_cm_long_short_pressure(symbol, args.cm_ls_exchange, ls_timeframes, cm_key)
            ls_score, ls_detail = aggregate_ls_score(ls_map, ls_timeframes)
            ls_used = bool(ls_map)
            if ls_score >= 0.05:
                ls_state = "bullish"
            elif ls_score <= -0.05:
                ls_state = "bearish"
            else:
                ls_state = "neutral"
        final_score = base_score
        if args.ls_enabled == "on" and ls_used:
            final_score = clamp(((1.0 - w_ls) * base_score) + (w_ls * ls_score), -1.0, 1.0)

        if final_score >= 0.25:
            state = "bullish"
        elif final_score <= -0.25:
            state = "bearish"
        else:
            state = "neutral"
        score = final_score

        gate_triggered = False
        gate_reason = ""
        final_state = state
        if args.overheat_gate == "on":
            final_state, gate_triggered, gate_reason = apply_overheat_gate(
                state,
                metrics["oi_change_pct"],
                metrics["perp_volume_change_pct"],
                metrics["funding_now"],
                metrics["funding_avg"],
                args.overheat_oi_gte,
                args.overheat_volume_gte,
                args.overheat_funding_z_gte,
            )

        results.append(
            {
                "symbol": symbol,
                "profile": effective_profile,
                "intervals": {"event": event_i, "context": context_i},
                "weights": {"w_oi": w_oi, "w_volume": w_volume, "w_funding": w_funding, "w_ls": w_ls},
                "metrics": metrics,
                "terms": terms,
                "long_short": {
                    "enabled": args.ls_enabled == "on",
                    "used": ls_used,
                    "exchange": args.cm_ls_exchange,
                    "timeframes": ls_timeframes,
                    "values": ls_map,
                    "score": ls_score,
                    "state": ls_state,
                    "detail": ls_detail,
                },
                "derivatives_score_base": base_score,
                "derivatives_state_base": base_state,
                "derivatives_score": score,
                "derivatives_state": final_state,
                "derivatives_reason": reason,
                "overheat_gate_triggered": gate_triggered,
                "overheat_gate_reason": gate_reason,
            }
        )

    payload = {
        "part": "C_derivatives",
        "profile": effective_profile,
        "intervals": {"event": event_i, "context": context_i},
        "weights": {"w_oi": w_oi, "w_volume": w_volume, "w_funding": w_funding, "w_ls": w_ls},
        "results": results,
    }

    if args.format == "json":
        print(json.dumps(payload, indent=2))
    else:
        print(as_table(results, effective_profile, event_i, context_i))


if __name__ == "__main__":
    main()

