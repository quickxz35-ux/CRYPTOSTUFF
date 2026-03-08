#!/usr/bin/env python3
"""Standalone liquidity module (Part B): exchange flow, balances, whale-to-exchange."""

from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Tuple

HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}

PROFILE_MAP: Dict[str, Dict[str, str]] = {
    "ltf": {"event_i": "10m", "context_i": "1h", "balance_i": "24h"},
    "mtf": {"event_i": "1h", "context_i": "24h", "balance_i": "24h"},
    "htf": {"event_i": "24h", "context_i": "24h", "balance_i": "24h"},
}

PROFILE_WEIGHT_MAP: Dict[str, Dict[str, float]] = {
    "ltf": {"w_netflow": 0.32, "w_ratio": 0.18, "w_balance": 0.20, "w_whale": 0.30},
    "mtf": {"w_netflow": 0.40, "w_ratio": 0.25, "w_balance": 0.25, "w_whale": 0.10},
    "htf": {"w_netflow": 0.45, "w_ratio": 0.20, "w_balance": 0.30, "w_whale": 0.05},
    "custom": {"w_netflow": 0.40, "w_ratio": 0.25, "w_balance": 0.20, "w_whale": 0.15},
}

BALANCE_REGIME_TO_SCORE: Dict[str, float] = {
    "STRONG_BULLISH": 1.00,
    "MEDIUM_BULLISH": 0.66,
    "MILD_BULLISH": 0.33,
    "NEUTRAL": 0.00,
    "MILD_BEARISH": -0.33,
    "MEDIUM_BEARISH": -0.66,
    "STRONG_BEARISH": -1.00,
}


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


def parse_assets(raw: str) -> List[str]:
    return [x.strip().upper() for x in raw.split(",") if x.strip()]


def resolve_profile(profile: str, event_i: str, context_i: str, balance_i: str) -> Tuple[str, str, str, str]:
    if profile in PROFILE_MAP:
        cfg = PROFILE_MAP[profile]
        return cfg["event_i"], cfg["context_i"], cfg["balance_i"], profile
    return event_i, context_i, balance_i, "custom"


def gn_series(endpoint: str, asset: str, interval: str, key: str, limit: int = 3) -> List[Dict[str, Any]]:
    q = urllib.parse.urlencode({"a": asset, "i": interval, "api_key": key})
    url = f"https://api.glassnode.com{endpoint}?{q}"
    data = safe_http_get_json(url)
    if isinstance(data, list):
        return data[-limit:]
    return []


def gn_last_value(endpoint: str, asset: str, interval: str, key: str) -> float:
    rows = gn_series(endpoint, asset, interval, key, limit=1)
    if not rows:
        return 0.0
    row = rows[-1]
    return to_float(row.get("v")) if isinstance(row, dict) else 0.0


def classify_balance_level(latest: float, hist: List[float]) -> str:
    if not hist:
        return "MID"
    lo, hi = min(hist), max(hist)
    if hi <= lo:
        return "MID"
    pos = (latest - lo) / (hi - lo)
    if pos <= 0.20:
        return "VERY_LOW"
    if pos <= 0.40:
        return "LOW"
    if pos <= 0.60:
        return "MID"
    if pos <= 0.80:
        return "HIGH"
    return "VERY_HIGH"


def classify_balance_trend(delta_pct: float) -> str:
    if delta_pct <= -1.00:
        return "FALLING_FAST"
    if delta_pct <= -0.20:
        return "FALLING"
    if delta_pct < 0.20:
        return "FLAT"
    if delta_pct < 1.00:
        return "RISING"
    return "RISING_FAST"


def map_level_trend_to_regime(level: str, trend: str) -> str:
    # Explicit semantic mapping (trading lens):
    # rising exchange inventories are bearish, falling inventories are bullish.
    # High/very-high levels temper bullishness; low/very-low levels temper bearishness.

    if trend == "FALLING_FAST":
        if level == "VERY_LOW":
            return "MILD_BULLISH"
        if level == "LOW":
            return "MEDIUM_BULLISH"
        if level == "MID":
            return "STRONG_BULLISH"
        if level == "HIGH":
            return "MEDIUM_BULLISH"
        if level == "VERY_HIGH":
            return "MILD_BULLISH"

    if trend == "FALLING":
        if level == "VERY_LOW":
            return "MILD_BULLISH"
        if level == "LOW":
            return "MEDIUM_BULLISH"
        if level == "MID":
            return "MEDIUM_BULLISH"
        if level == "HIGH":
            return "MILD_BULLISH"  # requested behavior
        if level == "VERY_HIGH":
            return "NEUTRAL"

    if trend == "FLAT":
        if level in {"VERY_LOW", "LOW"}:
            return "MILD_BULLISH"
        if level == "MID":
            return "NEUTRAL"
        if level in {"HIGH", "VERY_HIGH"}:
            return "MILD_BEARISH"

    if trend == "RISING":
        if level == "VERY_LOW":
            return "NEUTRAL"
        if level == "LOW":
            return "MILD_BEARISH"
        if level == "MID":
            return "MILD_BEARISH"
        if level == "HIGH":
            return "MEDIUM_BEARISH"
        if level == "VERY_HIGH":
            return "STRONG_BEARISH"

    if trend == "RISING_FAST":
        if level in {"VERY_LOW", "LOW"}:
            return "MILD_BEARISH"
        if level == "MID":
            return "MEDIUM_BEARISH"
        if level == "HIGH":
            return "STRONG_BEARISH"
        if level == "VERY_HIGH":
            return "STRONG_BEARISH"

    return "NEUTRAL"


def fetch_api_metrics(assets: List[str], event_i: str, context_i: str, balance_i: str, key: str) -> Dict[str, Any]:
    inflow_event = 0.0
    outflow_event = 0.0
    net_event = 0.0
    net_context_values: List[float] = []
    whale_event = 0.0

    balance_histories: List[List[float]] = []

    for a in assets:
        inflow_event += gn_last_value("/v1/metrics/transactions/transfers_volume_to_exchanges_sum", a, event_i, key)
        outflow_event += gn_last_value("/v1/metrics/transactions/transfers_volume_from_exchanges_sum", a, event_i, key)
        net_event += gn_last_value("/v1/metrics/transactions/transfers_volume_exchanges_net", a, event_i, key)

        ctx_rows = gn_series("/v1/metrics/transactions/transfers_volume_exchanges_net", a, context_i, key, limit=12)
        ctx_vals = [to_float(r.get("v")) for r in ctx_rows if isinstance(r, dict) and "v" in r]
        if ctx_vals:
            net_context_values.extend(ctx_vals)

        bal_rows = gn_series("/v1/metrics/distribution/balance_exchanges", a, balance_i, key, limit=20)
        bal_vals = [to_float(r.get("v")) for r in bal_rows if isinstance(r, dict) and "v" in r]
        if bal_vals:
            balance_histories.append(bal_vals)

        if a == "BTC":
            whale_event += gn_last_value("/v1/metrics/transactions/transfers_volume_whales_to_exchanges_sum", a, event_i, key)

    net_context_abs_avg = statistics.mean([abs(v) for v in net_context_values]) if net_context_values else 0.0

    if balance_histories:
        min_len = min(len(h) for h in balance_histories)
        agg_hist = [sum(h[-min_len + i] for h in balance_histories) for i in range(min_len)]
    else:
        agg_hist = []

    latest_balance = agg_hist[-1] if agg_hist else 0.0
    prev_balance = agg_hist[-2] if len(agg_hist) > 1 else latest_balance
    balance_delta_pct = ((latest_balance - prev_balance) / abs(prev_balance) * 100.0) if prev_balance else 0.0

    level = classify_balance_level(latest_balance, agg_hist)
    trend = classify_balance_trend(balance_delta_pct)
    regime = map_level_trend_to_regime(level, trend)
    regime_score = BALANCE_REGIME_TO_SCORE.get(regime, 0.0)

    return {
        "inflow_usd": inflow_event,
        "outflow_usd": outflow_event,
        "netflow_usd": net_event,
        "netflow_context_abs_avg": net_context_abs_avg,
        "exchange_balance_delta_pct": balance_delta_pct,
        "exchange_balance_level": level,
        "exchange_balance_trend": trend,
        "exchange_balance_regime": regime,
        "exchange_balance_regime_score": regime_score,
        "whale_to_exchange_usd": whale_event,
    }


def compute_liquidity_score(
    inflow_usd: float,
    outflow_usd: float,
    netflow_usd: float,
    netflow_context_abs_avg: float,
    exchange_balance_regime_score_val: float,
    whale_to_exchange_usd: float,
    w_netflow: float,
    w_ratio: float,
    w_balance: float,
    w_whale: float,
) -> Tuple[float, str, str]:
    norm = netflow_context_abs_avg if netflow_context_abs_avg > 1 else 1.0
    net_term = -math.tanh(netflow_usd / norm)

    ratio = (outflow_usd / inflow_usd) if inflow_usd > 0 else 0.0
    ratio_term = clamp((ratio - 1.0), -1.0, 1.0)

    balance_term = clamp(exchange_balance_regime_score_val, -1.0, 1.0)

    whale_scale = max(abs(netflow_usd), norm, 1.0)
    whale_term = -clamp(whale_to_exchange_usd / whale_scale, -1.0, 1.0)

    score = (
        (w_netflow * net_term)
        + (w_ratio * ratio_term)
        + (w_balance * balance_term)
        + (w_whale * whale_term)
    )
    score = clamp(score, -1.0, 1.0)

    if score >= 0.25:
        state = "bullish"
    elif score <= -0.25:
        state = "bearish"
    else:
        state = "neutral"

    reason = f"net={netflow_usd:.0f} out/in={ratio:.2f} bal_regime={exchange_balance_regime_score_val:.2f} whale={whale_to_exchange_usd:.0f}"
    return score, state, reason


def main() -> None:
    p = argparse.ArgumentParser(description="Standalone liquidity module (Part B)")
    p.add_argument("--profile", choices=["custom", "ltf", "mtf", "htf"], default="mtf")
    p.add_argument("--assets", default="USDT,USDC", help="Comma-separated assets (e.g., USDT,USDC or BTC)")
    p.add_argument("--source", choices=["api", "manual"], default="api")

    p.add_argument("--event-interval", default="1h")
    p.add_argument("--context-interval", default="24h")
    p.add_argument("--balance-interval", default="24h")

    p.add_argument("--manual-inflow-usd", type=float, default=0.0)
    p.add_argument("--manual-outflow-usd", type=float, default=0.0)
    p.add_argument("--manual-netflow-usd", type=float, default=0.0)
    p.add_argument("--manual-netflow-context-abs-avg", type=float, default=1.0)
    p.add_argument("--manual-exchange-balance-regime", choices=list(BALANCE_REGIME_TO_SCORE.keys()), default="NEUTRAL")
    p.add_argument("--manual-whale-to-exchange-usd", type=float, default=0.0)

    p.add_argument("--w-netflow", type=float, default=None)
    p.add_argument("--w-ratio", type=float, default=None)
    p.add_argument("--w-balance", type=float, default=None)
    p.add_argument("--w-whale", type=float, default=None)

    p.add_argument("--format", choices=["table", "json"], default="table")
    args = p.parse_args()

    event_i, context_i, balance_i, effective_profile = resolve_profile(
        args.profile,
        args.event_interval,
        args.context_interval,
        args.balance_interval,
    )
    profile_weights = PROFILE_WEIGHT_MAP.get(effective_profile, PROFILE_WEIGHT_MAP["custom"])
    w_netflow = args.w_netflow if args.w_netflow is not None else profile_weights["w_netflow"]
    w_ratio = args.w_ratio if args.w_ratio is not None else profile_weights["w_ratio"]
    w_balance = args.w_balance if args.w_balance is not None else profile_weights["w_balance"]
    w_whale = args.w_whale if args.w_whale is not None else profile_weights["w_whale"]

    assets = parse_assets(args.assets)

    if args.source == "api":
        key = os.environ.get("GLASSNODE_API_KEY", "").strip()
        if not key:
            payload = {
                "error": "missing_glassnode_api_key",
                "hint": "Set GLASSNODE_API_KEY or run with --source manual",
            }
            print(json.dumps(payload, indent=2))
            return
        metrics = fetch_api_metrics(assets, event_i, context_i, balance_i, key)
    else:
        reg = args.manual_exchange_balance_regime
        metrics = {
            "inflow_usd": args.manual_inflow_usd,
            "outflow_usd": args.manual_outflow_usd,
            "netflow_usd": args.manual_netflow_usd,
            "netflow_context_abs_avg": max(args.manual_netflow_context_abs_avg, 1.0),
            "exchange_balance_delta_pct": 0.0,
            "exchange_balance_level": "MANUAL",
            "exchange_balance_trend": "MANUAL",
            "exchange_balance_regime": reg,
            "exchange_balance_regime_score": BALANCE_REGIME_TO_SCORE.get(reg, 0.0),
            "whale_to_exchange_usd": args.manual_whale_to_exchange_usd,
        }

    score, state, reason = compute_liquidity_score(
        metrics["inflow_usd"],
        metrics["outflow_usd"],
        metrics["netflow_usd"],
        metrics["netflow_context_abs_avg"],
        metrics["exchange_balance_regime_score"],
        metrics["whale_to_exchange_usd"],
        w_netflow,
        w_ratio,
        w_balance,
        w_whale,
    )

    out = {
        "part": "B_liquidity",
        "profile": effective_profile,
        "assets": assets,
        "intervals": {"event": event_i, "context": context_i, "balance": balance_i},
        "weights": {
            "w_netflow": w_netflow,
            "w_ratio": w_ratio,
            "w_balance": w_balance,
            "w_whale": w_whale,
        },
        "metrics": metrics,
        "liquidity_score": score,
        "liquidity_state": state,
        "liquidity_reason": reason,
    }

    if args.format == "json":
        print(json.dumps(out, indent=2))
    else:
        print(f"Liquidity Module (Part B) profile={effective_profile} event={event_i} context={context_i} balance={balance_i}")
        print(f"State={state} Score={score:.3f}")
        print(f"Reason: {reason}")
        print(
            f"Metrics: inflow=${metrics['inflow_usd']:.0f} outflow=${metrics['outflow_usd']:.0f} net=${metrics['netflow_usd']:.0f} "
            f"ctx_abs_avg=${metrics['netflow_context_abs_avg']:.0f} bal_regime={metrics['exchange_balance_regime']}({metrics['exchange_balance_regime_score']:.2f}) "
            f"bal_level={metrics['exchange_balance_level']} bal_trend={metrics['exchange_balance_trend']} bal_delta={metrics['exchange_balance_delta_pct']:.2f}% "
            f"whale=${metrics['whale_to_exchange_usd']:.0f}"
        )


if __name__ == "__main__":
    main()