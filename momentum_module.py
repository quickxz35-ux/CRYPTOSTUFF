#!/usr/bin/env python3
"""Standalone momentum module with LTF/MTF/HTF profiles."""

from __future__ import annotations

import argparse
import json
import statistics
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Tuple

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json",
}

PROFILE_MAP: Dict[str, Dict[str, Any]] = {
    "ltf": {"timeframe": "15m", "baseline": 48, "impulse_threshold_pct": 0.25},
    "mtf": {"timeframe": "1h", "baseline": 72, "impulse_threshold_pct": 0.50},
    "htf": {"timeframe": "4h", "baseline": 90, "impulse_threshold_pct": 1.00},
}


def http_get_json(url: str) -> Any:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=20) as resp:
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


def resolve_profile(profile: str, timeframe: str, baseline: int, impulse_threshold_pct: float) -> Tuple[str, int, float, str]:
    if profile in PROFILE_MAP:
        cfg = PROFILE_MAP[profile]
        return str(cfg["timeframe"]), int(cfg["baseline"]), float(cfg["impulse_threshold_pct"]), profile
    return timeframe, baseline, impulse_threshold_pct, "custom"


def fetch_spot_klines(symbol: str, interval: str, limit: int) -> List[List[Any]]:
    query = urllib.parse.urlencode({"symbol": f"{symbol}USDT", "interval": interval, "limit": limit})
    url = f"https://api.binance.com/api/v3/klines?{query}"
    data = safe_http_get_json(url)
    return data if isinstance(data, list) else []


def fetch_perp_klines(symbol: str, interval: str, limit: int) -> List[List[Any]]:
    query = urllib.parse.urlencode({"symbol": f"{symbol}USDT", "interval": interval, "limit": limit})
    url = f"https://fapi.binance.com/fapi/v1/klines?{query}"
    data = safe_http_get_json(url)
    return data if isinstance(data, list) else []


def fetch_oi_change_usd(symbol: str, interval: str) -> float:
    query = urllib.parse.urlencode({"symbol": f"{symbol}USDT", "period": interval, "limit": 2})
    url = f"https://fapi.binance.com/futures/data/openInterestHist?{query}"
    data = safe_http_get_json(url)
    if not isinstance(data, list) or not data:
        return 0.0
    latest = data[-1]
    prev = data[-2] if len(data) > 1 else latest
    return to_float(latest.get("sumOpenInterestValue")) - to_float(prev.get("sumOpenInterestValue"))


def trend_direction_from_closes(closes: List[float]) -> str:
    if len(closes) < 50:
        return "neutral"
    sma20 = statistics.mean(closes[-20:])
    sma50 = statistics.mean(closes[-50:])
    if sma20 > sma50:
        return "bullish"
    if sma20 < sma50:
        return "bearish"
    return "neutral"


def score_momentum(
    price_impulse_pct: float,
    spot_vol_ratio: float,
    perp_vol_ratio: float,
    oi_change_usd: float,
    impulse_threshold_pct: float,
) -> Tuple[float, List[str]]:
    reasons: List[str] = []
    sign = 1.0 if price_impulse_pct > 0 else -1.0 if price_impulse_pct < 0 else 0.0

    score = 0.0

    # Price impulse term.
    if abs(price_impulse_pct) >= impulse_threshold_pct and sign != 0:
        score += 0.30 * sign
        reasons.append(f"impulse {price_impulse_pct:.2f}%")
    else:
        reasons.append(f"impulse weak {price_impulse_pct:.2f}%")

    # Spot volume expansion.
    if spot_vol_ratio >= 2.0:
        score += 0.25 * sign
        reasons.append(f"spot vol strong x{spot_vol_ratio:.2f}")
    elif spot_vol_ratio >= 1.5:
        score += 0.15 * sign
        reasons.append(f"spot vol expand x{spot_vol_ratio:.2f}")

    # Perp volume expansion.
    if perp_vol_ratio >= 2.0:
        score += 0.20 * sign
        reasons.append(f"perp vol strong x{perp_vol_ratio:.2f}")
    elif perp_vol_ratio >= 1.5:
        score += 0.10 * sign
        reasons.append(f"perp vol expand x{perp_vol_ratio:.2f}")

    # OI confirmation matrix.
    if price_impulse_pct > 0 and oi_change_usd > 0:
        score += 0.15
        reasons.append("price up + OI up")
    elif price_impulse_pct > 0 and oi_change_usd < 0:
        score -= 0.10
        reasons.append("price up + OI down")
    elif price_impulse_pct < 0 and oi_change_usd > 0:
        score -= 0.15
        reasons.append("price down + OI up")
    elif price_impulse_pct < 0 and oi_change_usd < 0:
        score += 0.05
        reasons.append("price down + OI down")

    return clamp(score, -1.0, 1.0), reasons


def state_from_score(score: float) -> str:
    if score >= 0.35:
        return "bullish"
    if score <= -0.35:
        return "bearish"
    return "neutral"


def strength_from_score(score: float) -> str:
    a = abs(score)
    if a >= 0.70:
        return "strong"
    if a >= 0.45:
        return "medium"
    return "weak"


def bias_from_alignment(trend_direction: str, momentum_state: str, momentum_strength: str) -> str:
    if momentum_state == "neutral":
        return "Mean Revert"
    if trend_direction == momentum_state:
        return "Continue" if momentum_strength in {"medium", "strong"} else "Wait Pullback"
    if trend_direction == "neutral":
        return "Wait Pullback"
    return "Avoid"


def run_symbol(symbol: str, timeframe: str, baseline: int, impulse_threshold_pct: float) -> Dict[str, Any]:
    limit = max(baseline + 2, 60)
    spot = fetch_spot_klines(symbol, timeframe, limit)
    perp = fetch_perp_klines(symbol, timeframe, limit)

    if len(spot) < baseline + 2 or len(perp) < baseline + 2:
        return {
            "symbol": symbol,
            "error": "insufficient_kline_data",
        }

    spot_closes = [to_float(k[4]) for k in spot]
    spot_quote_vols = [to_float(k[7]) for k in spot]
    perp_quote_vols = [to_float(k[7]) for k in perp]

    prev_close = spot_closes[-2]
    last_close = spot_closes[-1]
    price_impulse_pct = ((last_close - prev_close) / prev_close * 100.0) if prev_close else 0.0

    spot_base = statistics.mean(spot_quote_vols[-(baseline + 1):-1])
    perp_base = statistics.mean(perp_quote_vols[-(baseline + 1):-1])
    spot_vol_ratio = (spot_quote_vols[-1] / spot_base) if spot_base else 0.0
    perp_vol_ratio = (perp_quote_vols[-1] / perp_base) if perp_base else 0.0

    oi_change_usd = fetch_oi_change_usd(symbol, timeframe)
    trend_direction = trend_direction_from_closes(spot_closes)

    momentum_score, reasons = score_momentum(
        price_impulse_pct,
        spot_vol_ratio,
        perp_vol_ratio,
        oi_change_usd,
        impulse_threshold_pct,
    )
    momentum_state = state_from_score(momentum_score)
    momentum_strength = strength_from_score(momentum_score)
    momentum_bias = bias_from_alignment(trend_direction, momentum_state, momentum_strength)

    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "trend_direction": trend_direction,
        "momentum_state": momentum_state,
        "momentum_strength": momentum_strength,
        "momentum_score": momentum_score,
        "momentum_bias": momentum_bias,
        "price_impulse_pct": price_impulse_pct,
        "spot_vol_ratio": spot_vol_ratio,
        "perp_vol_ratio": perp_vol_ratio,
        "oi_change_usd": oi_change_usd,
        "momentum_reason": "; ".join(reasons),
    }


def as_table(rows: List[Dict[str, Any]], profile: str, timeframe: str, baseline: int, impulse_threshold_pct: float) -> str:
    lines = [
        f"Momentum Module (profile={profile}, timeframe={timeframe}, baseline={baseline}, impulse_threshold={impulse_threshold_pct}%)",
        "",
    ]
    for r in rows:
        if r.get("error"):
            lines.append(f"{r['symbol']}: ERROR {r['error']}")
            continue
        lines.append(
            f"{r['symbol']}: state={r['momentum_state']} strength={r['momentum_strength']} score={r['momentum_score']:.3f} trend={r['trend_direction']} bias={r['momentum_bias']}"
        )
        lines.append(
            f"  impulse={r['price_impulse_pct']:.2f}% spot_vol_ratio={r['spot_vol_ratio']:.2f} perp_vol_ratio={r['perp_vol_ratio']:.2f} oi_change=${r['oi_change_usd']:.0f}"
        )
        lines.append(f"  reason: {r['momentum_reason']}")
    return "\n".join(lines)


def main() -> None:
    p = argparse.ArgumentParser(description="Standalone momentum module")
    p.add_argument("--symbols", required=True, help="Comma-separated symbols, e.g. BTC,ETH,SOL")
    p.add_argument("--profile", choices=["custom", "ltf", "mtf", "htf"], default="mtf")
    p.add_argument("--timeframe", default="1h", help="Used in custom profile")
    p.add_argument("--baseline", type=int, default=72, help="Used in custom profile")
    p.add_argument("--impulse-threshold-pct", type=float, default=0.50, help="Used in custom profile")
    p.add_argument("--format", choices=["table", "json"], default="table")
    args = p.parse_args()

    timeframe, baseline, impulse_threshold_pct, effective_profile = resolve_profile(
        args.profile,
        args.timeframe,
        args.baseline,
        args.impulse_threshold_pct,
    )

    symbols = parse_symbols(args.symbols)
    rows = [run_symbol(s, timeframe, baseline, impulse_threshold_pct) for s in symbols]

    if args.format == "json":
        print(
            json.dumps(
                {
                    "profile": effective_profile,
                    "timeframe": timeframe,
                    "baseline": baseline,
                    "impulse_threshold_pct": impulse_threshold_pct,
                    "results": rows,
                },
                indent=2,
            )
        )
    else:
        print(as_table(rows, effective_profile, timeframe, baseline, impulse_threshold_pct))


if __name__ == "__main__":
    main()