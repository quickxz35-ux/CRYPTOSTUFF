#!/usr/bin/env python3
"""Standalone liquidation context mapper (Part E).

Primary objective: map actionable POI levels around current price for chart entries.
Priority weighting (default):
1) Location POIs (liquidation levels) -> 0.55
2) Liquidation event flow -> 0.25
3) Entry-position context -> 0.20
"""

from __future__ import annotations

import argparse
import json
import math
import os
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
HEATMAP_SUPPORTED = {"BNB", "BTC", "DOGE", "ETH", "SOL", "TON", "XRP"}


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


def parse_levels(raw: str) -> List[Tuple[float, float]]:
    """Parse 'price:strength,price:strength'."""
    out: List[Tuple[float, float]] = []
    if not raw.strip():
        return out
    for token in raw.split(","):
        token = token.strip()
        if not token or ":" not in token:
            continue
        p_raw, s_raw = token.split(":", 1)
        p = to_float(p_raw.strip())
        s = to_float(s_raw.strip())
        if p > 0 and s > 0:
            out.append((p, s))
    return out


def normalize_weights(w_loc: float, w_evt: float, w_ent: float) -> Tuple[float, float, float]:
    s = w_loc + w_evt + w_ent
    if s <= 0:
        return 0.55, 0.25, 0.20
    return w_loc / s, w_evt / s, w_ent / s


def fetch_price(symbol: str) -> float:
    q = urllib.parse.urlencode({"symbol": f"{symbol}USDT"})
    url = f"https://api.binance.com/api/v3/ticker/price?{q}"
    payload = safe_http_get_json(url)
    if not isinstance(payload, dict):
        return 0.0
    return to_float(payload.get("price"))


def gn_series(endpoint: str, symbol: str, interval: str, key: str, limit: int = 8) -> List[Dict[str, Any]]:
    q = urllib.parse.urlencode({"a": symbol, "i": interval, "api_key": key})
    url = f"https://api.glassnode.com{endpoint}?{q}"
    payload = safe_http_get_json(url)
    if isinstance(payload, list):
        return payload[-limit:]
    return []


def gn_last_value(endpoint: str, symbol: str, interval: str, key: str) -> float:
    rows = gn_series(endpoint, symbol, interval, key, limit=1)
    if not rows:
        return 0.0
    return to_float(rows[-1].get("v")) if isinstance(rows[-1], dict) else 0.0


def collect_price_value_pairs(obj: Any, out: List[Tuple[float, float]]) -> None:
    """Best-effort parser for heatmap-like payloads with price/intensity pairs."""
    if isinstance(obj, dict):
        keys = {k.lower(): k for k in obj.keys()}
        p_key = None
        v_key = None

        for k in ("price", "p", "level", "y"):
            if k in keys:
                p_key = keys[k]
                break
        for k in ("value", "v", "intensity", "size", "w", "z"):
            if k in keys:
                v_key = keys[k]
                break

        if p_key and v_key:
            p = to_float(obj.get(p_key))
            v = to_float(obj.get(v_key))
            if p > 0 and v > 0:
                out.append((p, v))

        for val in obj.values():
            collect_price_value_pairs(val, out)
        return

    if isinstance(obj, list):
        # Also support compact array pair [price, value]
        if len(obj) >= 2 and all(not isinstance(x, (dict, list)) for x in obj[:2]):
            p = to_float(obj[0])
            v = to_float(obj[1])
            if p > 0 and v > 0:
                out.append((p, v))
        for item in obj:
            collect_price_value_pairs(item, out)


def split_levels_around_price(
    pairs: List[Tuple[float, float]], price_now: float, max_levels: int
) -> Tuple[List[Tuple[float, float]], List[Tuple[float, float]]]:
    up = [(p, s) for p, s in pairs if p > price_now]
    down = [(p, s) for p, s in pairs if p < price_now]

    up.sort(key=lambda x: abs(x[0] - price_now))
    down.sort(key=lambda x: abs(x[0] - price_now))
    return up[:max_levels], down[:max_levels]


def level_obj(price_now: float, price: float, strength: float, typ: str) -> Dict[str, Any]:
    dist = abs(price - price_now) / max(price_now, 1e-9) * 100.0
    return {
        "price": price,
        "strength": strength,
        "type": typ,
        "distance_pct": dist,
    }


def nearest(levels: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not levels:
        return None
    return min(levels, key=lambda x: x["distance_pct"])


def side_pressure(levels: List[Dict[str, Any]]) -> float:
    # Closer + stronger levels contribute more pressure.
    total = 0.0
    for lv in levels:
        total += lv["strength"] / max(lv["distance_pct"], 0.05)
    return total


def compute_event_bias(liq_long_now: float, liq_short_now: float) -> float:
    # Positive means shorts got liquidated more => upward squeeze pressure.
    denom = max(liq_long_now + liq_short_now, 1e-9)
    return clamp((liq_short_now - liq_long_now) / denom, -1.0, 1.0)


def compute_entry_bias(entry_net: float, entry_scale: float) -> float:
    if entry_scale <= 0:
        return 0.0
    return clamp(entry_net / entry_scale, -1.0, 1.0)


def pick_sweep_state(pull_up: float, pull_down: float, chop_ratio: float) -> str:
    if pull_up <= 0 and pull_down <= 0:
        return "two_sided_chop"
    mx = max(pull_up, pull_down, 1e-9)
    mn = min(pull_up, pull_down)
    if mn / mx >= chop_ratio:
        return "two_sided_chop"
    if pull_up > pull_down:
        return "sweep_up_risk"
    return "sweep_down_risk"


def pick_entry_mode(
    sweep_state: str,
    location_score: float,
    event_bias: float,
    entry_bias: float,
    nearest_up: Optional[Dict[str, Any]],
    nearest_down: Optional[Dict[str, Any]],
    near_poi_pct: float,
) -> str:
    if sweep_state == "two_sided_chop":
        return "avoid"

    near_up = nearest_up is not None and nearest_up["distance_pct"] <= near_poi_pct
    near_down = nearest_down is not None and nearest_down["distance_pct"] <= near_poi_pct

    if sweep_state == "sweep_up_risk":
        if near_up and event_bias > 0.20 and entry_bias > 0:
            return "breakout_follow"
        if near_up and (event_bias < -0.20 or entry_bias < 0):
            return "fade_extreme"
        return "pullback_wait"

    if sweep_state == "sweep_down_risk":
        if near_down and event_bias < -0.20 and entry_bias < 0:
            return "breakout_follow"
        if near_down and (event_bias > 0.20 or entry_bias > 0):
            return "fade_extreme"
        return "pullback_wait"

    if abs(location_score) >= 0.30:
        return "pullback_wait"
    return "avoid"


def pick_invalidation(
    entry_mode: str,
    nearest_up: Optional[Dict[str, Any]],
    nearest_down: Optional[Dict[str, Any]],
    pad_pct: float,
) -> Tuple[Optional[Dict[str, Any]], Dict[str, float]]:
    if entry_mode == "breakout_follow":
        opp = nearest_down
    elif entry_mode == "fade_extreme":
        opp = nearest_up if nearest_up and (not nearest_down or nearest_up["distance_pct"] < nearest_down["distance_pct"]) else nearest_down
    else:
        opp = nearest_down if nearest_down and (not nearest_up or nearest_down["distance_pct"] < nearest_up["distance_pct"]) else nearest_up

    if not opp:
        return None, {"low": 0.0, "high": 0.0}

    p = opp["price"]
    z = p * (pad_pct / 100.0)
    return opp, {"low": p - z, "high": p + z}


def run_symbol(args: argparse.Namespace, symbol: str) -> Dict[str, Any]:
    if symbol not in HEATMAP_SUPPORTED:
        return {"symbol": symbol, "error": "unsupported_for_heatmap_family"}

    if args.source == "api":
        key = os.environ.get("GLASSNODE_API_KEY", "").strip()
        if not key:
            return {"symbol": symbol, "error": "missing_glassnode_api_key"}
        price_now = fetch_price(symbol)
        if price_now <= 0:
            return {"symbol": symbol, "error": "missing_price_now"}

        heatmap_rows = gn_series(
            "/v1/metrics/derivatives/liquidation_heatmap",
            symbol,
            "1h",
            key,
            limit=1,
        )
        if not heatmap_rows:
            return {"symbol": symbol, "error": "missing_liquidation_heatmap_data"}

        pairs: List[Tuple[float, float]] = []
        collect_price_value_pairs(heatmap_rows[-1], pairs)
        if not pairs:
            return {"symbol": symbol, "error": "insufficient_heatmap_price_levels"}

        up_pairs, down_pairs = split_levels_around_price(pairs, price_now, args.max_levels)
        entry_net = gn_last_value(
            "/v1/metrics/derivatives/liquidation_entry_price_heatmap_net",
            symbol,
            "1h",
            key,
        )
        liq_long_now = gn_last_value(
            "/v1/metrics/derivatives/futures_liquidated_volume_long_sum",
            symbol,
            args.event_tf,
            key,
        )
        liq_short_now = gn_last_value(
            "/v1/metrics/derivatives/futures_liquidated_volume_short_sum",
            symbol,
            args.event_tf,
            key,
        )
    else:
        price_now = args.manual_price_now
        if price_now <= 0:
            return {"symbol": symbol, "error": "manual_price_now_required"}

        up_pairs = parse_levels(args.manual_up_levels)
        down_pairs = parse_levels(args.manual_down_levels)
        entry_net = args.manual_entry_net
        liq_long_now = args.manual_liq_long_now
        liq_short_now = args.manual_liq_short_now

    poi_up = [level_obj(price_now, p, s, "liq_cluster") for p, s in up_pairs]
    poi_down = [level_obj(price_now, p, s, "liq_cluster") for p, s in down_pairs]

    # Add light entry-context pseudo POIs at nearest levels to keep chart context visible.
    entry_mag = abs(entry_net)
    if entry_mag > 0 and (poi_up or poi_down):
        if entry_net > 0 and poi_up:
            poi_up[0]["type"] = "entry_cluster"
            poi_up[0]["strength"] += min(entry_mag, 1.0) * 0.25
        elif entry_net < 0 and poi_down:
            poi_down[0]["type"] = "entry_cluster"
            poi_down[0]["strength"] += min(entry_mag, 1.0) * 0.25

    nearest_up = nearest(poi_up)
    nearest_down = nearest(poi_down)

    pull_up_raw = side_pressure(poi_up)
    pull_down_raw = side_pressure(poi_down)

    denom = max(pull_up_raw + pull_down_raw, 1e-9)
    location_score = clamp((pull_up_raw - pull_down_raw) / denom, -1.0, 1.0)
    event_bias = compute_event_bias(liq_long_now, liq_short_now)
    entry_bias = compute_entry_bias(entry_net, args.entry_scale)

    w_loc, w_evt, w_ent = normalize_weights(args.w_location, args.w_event, args.w_entry)
    composite = clamp((w_loc * location_score) + (w_evt * event_bias) + (w_ent * entry_bias), -1.0, 1.0)

    pull_up_pressure = clamp((w_loc * max(location_score, 0.0)) + (w_evt * max(event_bias, 0.0)) + (w_ent * max(entry_bias, 0.0)), 0.0, 1.0)
    pull_down_pressure = clamp((w_loc * max(-location_score, 0.0)) + (w_evt * max(-event_bias, 0.0)) + (w_ent * max(-entry_bias, 0.0)), 0.0, 1.0)

    if abs(pull_up_pressure - pull_down_pressure) <= args.bias_deadband:
        trend_pull_bias = "balanced"
    elif pull_up_pressure > pull_down_pressure:
        trend_pull_bias = "up"
    else:
        trend_pull_bias = "down"

    sweep_risk_state = pick_sweep_state(pull_up_pressure, pull_down_pressure, args.chop_ratio)
    entry_mode = pick_entry_mode(
        sweep_risk_state,
        location_score,
        event_bias,
        entry_bias,
        nearest_up,
        nearest_down,
        args.near_poi_pct,
    )

    nearest_opp, invalidation_zone = pick_invalidation(entry_mode, nearest_up, nearest_down, args.invalidation_pad_pct)

    reason = (
        f"loc={location_score:.3f} evt={event_bias:.3f} ent={entry_bias:.3f} "
        f"comp={composite:.3f} pull_up={pull_up_pressure:.3f} pull_down={pull_down_pressure:.3f} "
        f"mode={entry_mode}"
    )

    return {
        "symbol": symbol,
        "price_now": price_now,
        "poi_up": poi_up,
        "poi_down": poi_down,
        "nearest_up_magnet": nearest_up or {"price": 0.0, "distance_pct": 0.0, "strength": 0.0},
        "nearest_down_magnet": nearest_down or {"price": 0.0, "distance_pct": 0.0, "strength": 0.0},
        "pull_up_pressure": pull_up_pressure,
        "pull_down_pressure": pull_down_pressure,
        "trend_pull_bias": trend_pull_bias,
        "sweep_risk_state": sweep_risk_state,
        "entry_mode": entry_mode,
        "nearest_opposing_poi": nearest_opp or {"price": 0.0, "distance_pct": 0.0, "strength": 0.0},
        "invalidation_zone": invalidation_zone,
        "part_e_reason": reason,
        "debug": {
            "weights": {"location": w_loc, "event": w_evt, "entry": w_ent},
            "scores": {
                "location_score": location_score,
                "event_bias": event_bias,
                "entry_bias": entry_bias,
                "composite_score": composite,
            },
            "event": {"liq_long_now": liq_long_now, "liq_short_now": liq_short_now},
            "entry_net": entry_net,
        },
    }


def as_table(rows: List[Dict[str, Any]], event_tf: str) -> str:
    lines = [f"Liquidation Context (Part E) event_tf={event_tf}", ""]
    for r in rows:
        if "error" in r:
            lines.append(f"{r['symbol']}: ERROR {r['error']}")
            continue
        lines.append(
            f"{r['symbol']}: mode={r['entry_mode']} bias={r['trend_pull_bias']} sweep={r['sweep_risk_state']}"
        )
        lines.append(
            f"  Magnets: up={r['nearest_up_magnet']['price']:.4f} ({r['nearest_up_magnet']['distance_pct']:.2f}%) "
            f"down={r['nearest_down_magnet']['price']:.4f} ({r['nearest_down_magnet']['distance_pct']:.2f}%)"
        )
        lines.append(
            f"  Pressure: up={r['pull_up_pressure']:.3f} down={r['pull_down_pressure']:.3f} "
            f"invalid=[{r['invalidation_zone']['low']:.4f}, {r['invalidation_zone']['high']:.4f}]"
        )
        lines.append(f"  Reason: {r['part_e_reason']}")
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    p = argparse.ArgumentParser(description="Part E liquidation context mapper")
    p.add_argument("--symbols", required=True, help="Comma-separated symbols, e.g. BTC,ETH")
    p.add_argument("--source", choices=["api", "manual"], default="api")
    p.add_argument("--event-tf", choices=["10m", "1h", "24h"], default="1h")
    p.add_argument("--format", choices=["table", "json"], default="table")

    # Priority locks: location first.
    p.add_argument("--w-location", type=float, default=0.55)
    p.add_argument("--w-event", type=float, default=0.25)
    p.add_argument("--w-entry", type=float, default=0.20)

    p.add_argument("--max-levels", type=int, default=6)
    p.add_argument("--near-poi-pct", type=float, default=0.80)
    p.add_argument("--chop-ratio", type=float, default=0.80)
    p.add_argument("--bias-deadband", type=float, default=0.08)
    p.add_argument("--entry-scale", type=float, default=1.0, help="Scaling denominator for entry_net -> [-1,+1]")
    p.add_argument("--invalidation-pad-pct", type=float, default=0.25)

    # Manual inputs.
    p.add_argument("--manual-price-now", type=float, default=0.0)
    p.add_argument("--manual-up-levels", default="", help="price:strength,price:strength")
    p.add_argument("--manual-down-levels", default="", help="price:strength,price:strength")
    p.add_argument("--manual-entry-net", type=float, default=0.0)
    p.add_argument("--manual-liq-long-now", type=float, default=0.0)
    p.add_argument("--manual-liq-short-now", type=float, default=0.0)

    args = p.parse_args()
    symbols = parse_symbols(args.symbols)

    rows = [run_symbol(args, s) for s in symbols]

    if args.format == "json":
        print(json.dumps({"results": rows}, indent=2))
    else:
        print(as_table(rows, args.event_tf))


if __name__ == "__main__":
    main()

