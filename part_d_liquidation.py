#!/usr/bin/env python3
"""Standalone liquidation positioning module (Part D)."""

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

PROFILE_EVENT_MAP: Dict[str, str] = {
    "ltf": "10m",
    "mtf": "1h",
    "htf": "24h",
}

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


def resolve_profile(profile: str, event_tf: str) -> Tuple[str, str]:
    if profile in PROFILE_EVENT_MAP:
        return PROFILE_EVENT_MAP[profile], profile
    return event_tf, "custom"


def gn_series(endpoint: str, symbol: str, interval: str, key: str, limit: int = 5) -> List[Dict[str, Any]]:
    q = urllib.parse.urlencode({"a": symbol, "i": interval, "api_key": key})
    url = f"https://api.glassnode.com{endpoint}?{q}"
    data = safe_http_get_json(url)
    if isinstance(data, list):
        return data[-limit:]
    return []


def gn_last_value(endpoint: str, symbol: str, interval: str, key: str) -> float:
    rows = gn_series(endpoint, symbol, interval, key, limit=1)
    if not rows:
        return 0.0
    row = rows[-1]
    return to_float(row.get("v")) if isinstance(row, dict) else 0.0


def fetch_api_metrics(symbol: str, heatmap_tf: str, event_tf: str, key: str) -> Dict[str, Any]:
    # Net entry heatmap proxy for level loading
    net_now = gn_last_value(
        "/v1/metrics/derivatives/liquidation_entry_price_heatmap_net",
        symbol,
        heatmap_tf,
        key,
    )

    # Liquidation event flow
    liq_rows = gn_series(
        "/v1/metrics/derivatives/futures_liquidated_total_volume_sum",
        symbol,
        event_tf,
        key,
        limit=12,
    )
    liq_vals = [to_float(r.get("v")) for r in liq_rows if isinstance(r, dict)]
    liq_now = liq_vals[-1] if liq_vals else 0.0
    liq_prev = liq_vals[-2] if len(liq_vals) > 1 else liq_now
    liq_avg = statistics.mean(liq_vals[:-1]) if len(liq_vals) > 1 else liq_now

    # Optional long/short liquidation split
    long_now = gn_last_value(
        "/v1/metrics/derivatives/futures_liquidated_volume_long_sum",
        symbol,
        event_tf,
        key,
    )
    short_now = gn_last_value(
        "/v1/metrics/derivatives/futures_liquidated_volume_short_sum",
        symbol,
        event_tf,
        key,
    )

    return {
        "liq_heatmap_net": net_now,
        "liq_total_now": liq_now,
        "liq_total_prev": liq_prev,
        "liq_total_avg": liq_avg,
        "liq_long_now": long_now,
        "liq_short_now": short_now,
    }


def classify_level_bias(liq_heatmap_net: float, net_threshold: float) -> str:
    if liq_heatmap_net >= net_threshold:
        return "short_side_loaded"
    if liq_heatmap_net <= -net_threshold:
        return "long_side_loaded"
    return "balanced"


def classify_event_state(
    liq_total_now: float,
    liq_total_avg: float,
    liq_long_now: float,
    liq_short_now: float,
    spike_mult: float,
) -> Tuple[str, float]:
    base = liq_total_avg if liq_total_avg > 0 else max(liq_total_now, 1.0)
    spike_ratio = liq_total_now / base if base > 0 else 0.0

    if spike_ratio >= spike_mult:
        if liq_long_now > liq_short_now:
            return "flush_down", spike_ratio
        if liq_short_now > liq_long_now:
            return "squeeze_up", spike_ratio
        return "high_vol_chop", spike_ratio

    return "calm", spike_ratio


def compose_state(level_bias: str, event_state: str) -> str:
    if event_state == "flush_down":
        if level_bias == "short_side_loaded":
            return "bullish_watch"
        return "high_vol_chop"

    if event_state == "squeeze_up":
        if level_bias == "long_side_loaded":
            return "bearish_watch"
        return "high_vol_chop"

    if event_state == "high_vol_chop":
        return "high_vol_chop"

    if level_bias == "short_side_loaded":
        return "bullish_watch"
    if level_bias == "long_side_loaded":
        return "bearish_watch"
    return "neutral_watch"


def as_table(rows: List[Dict[str, Any]], profile: str, heatmap_tf: str, event_tf: str) -> str:
    lines = [f"Liquidation Module (Part D) profile={profile} heatmap_tf={heatmap_tf} event_tf={event_tf}", ""]
    for r in rows:
        if "error" in r:
            lines.append(f"{r['symbol']}: ERROR {r['error']}")
            continue
        lines.append(
            f"{r['symbol']}: {r['liq_composite_state']} | bias={r['liq_level_bias']} event={r['liq_event_state']}"
        )
        lines.append(f"  Reason: {r['liq_reason']}")
        m = r["metrics"]
        lines.append(
            "  Metrics: "
            f"net={m['liq_heatmap_net']:.4f} total_now={m['liq_total_now']:.2f} total_avg={m['liq_total_avg']:.2f} "
            f"long={m['liq_long_now']:.2f} short={m['liq_short_now']:.2f} spike={r['liq_spike_ratio']:.2f}"
        )
        lines.append("")
    return "\n".join(lines)


def load_json_file(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        obj = json.load(f)
    return obj if isinstance(obj, dict) else {}


def metrics_from_mcp_payload(path: str, symbol: str) -> Dict[str, Any]:
    obj = load_json_file(path)
    # supported formats:
    # 1) top-level symbol map: {"BTC": {...metrics...}}
    # 2) keyed map: {"symbols": {"BTC": {...}}}
    # 3) part-like list: {"results":[{"symbol":"BTC","metrics":{...}}]}
    src: Dict[str, Any] = {}
    if symbol in obj and isinstance(obj[symbol], dict):
        src = obj[symbol]
    elif isinstance(obj.get("symbols"), dict) and isinstance(obj["symbols"].get(symbol), dict):
        src = obj["symbols"][symbol]
    elif isinstance(obj.get("results"), list):
        for r in obj["results"]:
            if isinstance(r, dict) and str(r.get("symbol", "")).upper() == symbol:
                m = r.get("metrics")
                src = m if isinstance(m, dict) else r
                break

    required = ["liq_heatmap_net", "liq_total_now", "liq_total_avg", "liq_long_now", "liq_short_now"]
    missing = [k for k in required if k not in src]
    if missing:
        raise ValueError(f"mcp_payload_missing_keys:{','.join(missing)}")

    liq_total_now = to_float(src.get("liq_total_now"))
    return {
        "liq_heatmap_net": to_float(src.get("liq_heatmap_net")),
        "liq_total_now": liq_total_now,
        "liq_total_prev": to_float(src.get("liq_total_prev", liq_total_now)),
        "liq_total_avg": max(to_float(src.get("liq_total_avg")), 1.0),
        "liq_long_now": to_float(src.get("liq_long_now")),
        "liq_short_now": to_float(src.get("liq_short_now")),
    }


def main() -> None:
    p = argparse.ArgumentParser(description="Standalone liquidation positioning module (Part D)")
    p.add_argument("--symbols", required=True, help="Comma-separated symbols, e.g. BTC,ETH,SOL")
    p.add_argument("--profile", choices=["custom", "ltf", "mtf", "htf"], default="mtf")
    p.add_argument(
        "--source",
        choices=["auto", "api", "glassnode_api", "mcp", "manual"],
        default="auto",
        help="Data source: auto/glassnode_api/mcp/manual (api alias -> glassnode_api)",
    )
    p.add_argument("--mcp-input-file", default="", help="JSON file for --source mcp")

    p.add_argument("--heatmap-tf", default="1h", help="Fixed by design; keep at 1h")
    p.add_argument("--event-tf", choices=["10m", "1h", "24h"], default="1h")

    p.add_argument("--net-threshold", type=float, default=0.05)
    p.add_argument("--spike-mult", type=float, default=1.8)

    p.add_argument("--manual-heatmap-net", type=float, default=0.0)
    p.add_argument("--manual-liq-total-now", type=float, default=0.0)
    p.add_argument("--manual-liq-total-avg", type=float, default=1.0)
    p.add_argument("--manual-liq-long-now", type=float, default=0.0)
    p.add_argument("--manual-liq-short-now", type=float, default=0.0)

    p.add_argument("--format", choices=["table", "json"], default="table")
    args = p.parse_args()

    event_tf, effective_profile = resolve_profile(args.profile, args.event_tf)
    heatmap_tf = "1h"

    symbols = parse_symbols(args.symbols)
    results: List[Dict[str, Any]] = []

    for symbol in symbols:
        if symbol not in HEATMAP_SUPPORTED:
            results.append({"symbol": symbol, "error": "unsupported_for_heatmap_family"})
            continue

        source_choice = "glassnode_api" if args.source == "api" else args.source
        source_used = ""
        if source_choice in ("glassnode_api", "mcp", "auto"):
            metrics = None
            if source_choice in ("mcp", "auto") and args.mcp_input_file:
                try:
                    metrics = metrics_from_mcp_payload(args.mcp_input_file, symbol)
                    source_used = "mcp"
                except Exception:
                    metrics = None
            if metrics is None and source_choice in ("glassnode_api", "auto"):
                key = os.environ.get("GLASSNODE_API_KEY", "").strip()
                if key:
                    metrics = fetch_api_metrics(symbol, heatmap_tf, event_tf, key)
                    source_used = "glassnode_api"
            if metrics is None:
                if source_choice == "mcp":
                    results.append({"symbol": symbol, "error": "invalid_or_missing_mcp_payload"})
                else:
                    results.append({"symbol": symbol, "error": "no_viable_source"})
                continue
        else:
            metrics = {
                "liq_heatmap_net": args.manual_heatmap_net,
                "liq_total_now": args.manual_liq_total_now,
                "liq_total_prev": args.manual_liq_total_now,
                "liq_total_avg": max(args.manual_liq_total_avg, 1.0),
                "liq_long_now": args.manual_liq_long_now,
                "liq_short_now": args.manual_liq_short_now,
            }
            source_used = "manual"

        level_bias = classify_level_bias(metrics["liq_heatmap_net"], args.net_threshold)
        event_state, spike_ratio = classify_event_state(
            metrics["liq_total_now"],
            metrics["liq_total_avg"],
            metrics["liq_long_now"],
            metrics["liq_short_now"],
            args.spike_mult,
        )
        composite = compose_state(level_bias, event_state)

        reason = (
            f"net={metrics['liq_heatmap_net']:.4f} spike={spike_ratio:.2f} "
            f"long={metrics['liq_long_now']:.2f} short={metrics['liq_short_now']:.2f}"
        )

        results.append(
            {
                "symbol": symbol,
                "profile": effective_profile,
                "source_used": source_used,
                "intervals": {"heatmap": heatmap_tf, "event": event_tf},
                "metrics": metrics,
                "liq_level_bias": level_bias,
                "liq_event_state": event_state,
                "liq_composite_state": composite,
                "liq_spike_ratio": spike_ratio,
                "liq_reason": reason,
            }
        )

    payload = {
        "part": "D_liquidation_positioning",
        "profile": effective_profile,
        "intervals": {"heatmap": heatmap_tf, "event": event_tf},
        "thresholds": {"net_threshold": args.net_threshold, "spike_mult": args.spike_mult},
        "results": results,
    }

    if args.format == "json":
        print(json.dumps(payload, indent=2))
    else:
        print(as_table(results, effective_profile, heatmap_tf, event_tf))


if __name__ == "__main__":
    main()
