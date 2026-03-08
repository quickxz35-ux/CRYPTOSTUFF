#!/usr/bin/env python3
"""Build Part E MCP payload from raw MCP-exported metric snapshots.

Input (raw) expected shape:
{
  "symbols": {
    "BTC": {
      "price_now": 100000,
      "up_levels": [[100250, 1.2]],
      "down_levels": [[99600, 0.6]],
      "heatmap_pairs": [[100250, 1.2], [99600, 0.6]],
      "entry_net": 0.45,
      "liq_long_now": 20,
      "liq_short_now": 52,
      "metrics": {
        "liquidation_entry_price_heatmap_net": [{"v": 0.45}],
        "futures_liquidated_volume_long_sum": [{"v": 20}],
        "futures_liquidated_volume_short_sum": [{"v": 52}]
      }
    }
  }
}

Output shape is the exact Part E `--source mcp` contract:
{
  "symbols": {
    "BTC": {
      "price_now": ...,
      "up_levels": [[price, strength], ...],
      "down_levels": [[price, strength], ...],
      "entry_net": ...,
      "liq_long_now": ...,
      "liq_short_now": ...
    }
  }
}
"""

from __future__ import annotations

import argparse
import json
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Tuple

HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}


def to_float(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def parse_symbols(raw: str) -> List[str]:
    return [x.strip().upper() for x in raw.split(",") if x.strip()]


def http_get_json(url: str) -> Any:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def safe_http_get_json(url: str) -> Any:
    try:
        return http_get_json(url)
    except Exception:
        return None


def fetch_binance_price(symbol: str) -> float:
    q = urllib.parse.urlencode({"symbol": f"{symbol}USDT"})
    url = f"https://api.binance.com/api/v3/ticker/price?{q}"
    payload = safe_http_get_json(url)
    if isinstance(payload, dict):
        return to_float(payload.get("price"))
    return 0.0


def parse_level_pairs(obj: Any) -> List[Tuple[float, float]]:
    out: List[Tuple[float, float]] = []
    if not isinstance(obj, list):
        return out
    for item in obj:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            p = to_float(item[0])
            s = to_float(item[1])
            if p > 0 and s > 0:
                out.append((p, s))
        elif isinstance(item, dict):
            p = to_float(item.get("price"))
            s = to_float(item.get("strength", item.get("value", 0.0)))
            if p > 0 and s > 0:
                out.append((p, s))
    return out


def split_levels_by_price(pairs: List[Tuple[float, float]], price_now: float, max_levels: int) -> Tuple[List[List[float]], List[List[float]]]:
    up = [[p, s] for p, s in pairs if p > price_now]
    down = [[p, s] for p, s in pairs if p < price_now]
    up.sort(key=lambda x: abs(x[0] - price_now))
    down.sort(key=lambda x: abs(x[0] - price_now))
    return up[:max_levels], down[:max_levels]


def last_metric_value(metrics_obj: Any, key: str) -> float:
    if not isinstance(metrics_obj, dict):
        return 0.0
    rows = metrics_obj.get(key)
    if not isinstance(rows, list) or not rows:
        return 0.0
    last = rows[-1]
    if isinstance(last, dict):
        return to_float(last.get("v"))
    if isinstance(last, (list, tuple)) and last:
        return to_float(last[-1])
    return 0.0


def build_symbol_row(symbol: str, row: Dict[str, Any], max_levels: int, price_source: str) -> Dict[str, Any]:
    price_now = to_float(row.get("price_now"))
    if price_now <= 0 and price_source == "binance":
        price_now = fetch_binance_price(symbol)

    up_pairs = parse_level_pairs(row.get("up_levels"))
    down_pairs = parse_level_pairs(row.get("down_levels"))
    if not up_pairs and not down_pairs:
        pairs = parse_level_pairs(row.get("heatmap_pairs"))
        if pairs and price_now > 0:
            up, down = split_levels_by_price(pairs, price_now, max_levels=max_levels)
            up_pairs = [(to_float(x[0]), to_float(x[1])) for x in up]
            down_pairs = [(to_float(x[0]), to_float(x[1])) for x in down]

    metrics_obj = row.get("metrics", {})
    entry_net = to_float(row.get("entry_net"))
    liq_long_now = to_float(row.get("liq_long_now"))
    liq_short_now = to_float(row.get("liq_short_now"))

    if entry_net == 0.0:
        entry_net = last_metric_value(metrics_obj, "liquidation_entry_price_heatmap_net")
    if liq_long_now == 0.0:
        liq_long_now = last_metric_value(metrics_obj, "futures_liquidated_volume_long_sum")
    if liq_short_now == 0.0:
        liq_short_now = last_metric_value(metrics_obj, "futures_liquidated_volume_short_sum")

    return {
        "price_now": price_now,
        "up_levels": [[p, s] for p, s in up_pairs[:max_levels]],
        "down_levels": [[p, s] for p, s in down_pairs[:max_levels]],
        "entry_net": entry_net,
        "liq_long_now": liq_long_now,
        "liq_short_now": liq_short_now,
    }


def main() -> None:
    p = argparse.ArgumentParser(description="Build Part E MCP payload from raw snapshot JSON")
    p.add_argument("--symbols", required=True, help="Comma-separated symbols, e.g. BTC,ETH,SOL")
    p.add_argument("--input-file", required=True, help="Raw MCP snapshot JSON")
    p.add_argument("--output-file", required=True, help="Output payload file for part_e_liq_context.py --source mcp")
    p.add_argument("--price-source", choices=["input", "binance"], default="binance")
    p.add_argument("--max-levels", type=int, default=8)
    args = p.parse_args()

    try:
        with open(args.input_file, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception:
        print(json.dumps({"error": "failed_to_read_input_file"}, indent=2))
        return

    symbols_map = raw.get("symbols", {}) if isinstance(raw, dict) else {}
    if not isinstance(symbols_map, dict):
        print(json.dumps({"error": "invalid_input_symbols_map"}, indent=2))
        return

    out: Dict[str, Any] = {"symbols": {}}
    for sym in parse_symbols(args.symbols):
        row = symbols_map.get(sym, {})
        if not isinstance(row, dict):
            out["symbols"][sym] = {"price_now": 0.0, "up_levels": [], "down_levels": [], "entry_net": 0.0, "liq_long_now": 0.0, "liq_short_now": 0.0}
            continue
        out["symbols"][sym] = build_symbol_row(sym, row, max_levels=args.max_levels, price_source=args.price_source)

    with open(args.output_file, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)

    print(json.dumps({"status": "ok", "output_file": args.output_file, "symbols": list(out["symbols"].keys())}, indent=2))


if __name__ == "__main__":
    main()

