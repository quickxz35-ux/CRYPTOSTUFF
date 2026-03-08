#!/usr/bin/env python3
"""Infer likely stablecoin deployment destinations (best-effort, no key)."""

from __future__ import annotations

import argparse
import json
import os
import statistics
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json",
}

BINANCE_OI_PERIODS = {"5m", "15m", "30m", "1h", "2h", "4h", "6h", "12h", "1d"}
GLASSNODE_INTERVALS = {"1h", "24h"}
PROFILE_MAP = {
    "ltf": {"timeframe": "15m", "ls_timeframes": ["5m", "15m", "1h"]},
    "mtf": {"timeframe": "1h", "ls_timeframes": ["15m", "1h", "4h"]},
    "htf": {"timeframe": "4h", "ls_timeframes": ["1h", "4h", "1d"]},
}


def http_get_json(url: str) -> Any:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def safe_http_get_json(url: str) -> Optional[Any]:
    try:
        return http_get_json(url)
    except Exception:
        return None


def to_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def zscore(value: float, values: List[float]) -> float:
    if not values:
        return 0.0
    mu = statistics.mean(values)
    sigma = statistics.pstdev(values)
    if sigma == 0:
        return 0.0
    return (value - mu) / sigma


def fetch_spot_24h() -> List[Dict[str, Any]]:
    rows = http_get_json("https://api.binance.com/api/v3/ticker/24hr")
    if not isinstance(rows, list):
        return []
    out = []
    for r in rows:
        sym = str(r.get("symbol", "")).upper()
        if sym.endswith("USDT") or sym.endswith("USDC"):
            out.append(r)
    return out


def fetch_futures_24h() -> Dict[str, Dict[str, Any]]:
    rows = http_get_json("https://fapi.binance.com/fapi/v1/ticker/24hr")
    if not isinstance(rows, list):
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        sym = str(r.get("symbol", "")).upper()
        if sym.endswith("USDT"):
            out[sym] = r
    return out


def fetch_oi_change(symbol_usdt: str, timeframe: str) -> float:
    period = timeframe if timeframe in BINANCE_OI_PERIODS else "1h"
    base = "https://fapi.binance.com/futures/data/openInterestHist"
    query = urllib.parse.urlencode({"symbol": symbol_usdt, "period": period, "limit": 2})
    rows = http_get_json(f"{base}?{query}")
    if not isinstance(rows, list) or not rows:
        return 0.0
    latest = rows[-1]
    prev = rows[-2] if len(rows) > 1 else rows[-1]
    return to_float(latest.get("sumOpenInterestValue")) - to_float(prev.get("sumOpenInterestValue"))


def fetch_glassnode_netflow_sum(interval: str) -> Optional[float]:
    api_key = os.environ.get("GLASSNODE_API_KEY", "").strip()
    if not api_key:
        return None
    if interval not in GLASSNODE_INTERVALS:
        interval = "1h"

    def one(asset: str) -> float:
        query = urllib.parse.urlencode(
            {
                "a": asset,
                "i": interval,
                "api_key": api_key,
            }
        )
        url = f"https://api.glassnode.com/v1/metrics/transactions/transfers_volume_exchanges_net?{query}"
        rows = http_get_json(url)
        if not isinstance(rows, list) or not rows:
            return 0.0
        return to_float(rows[-1].get("v"))

    return one("USDT") + one("USDC")


def resolve_bias(requested_bias: str, auto_bias: bool, bias_timeframe: str) -> Dict[str, Any]:
    if not auto_bias:
        return {
            "selected_bias": requested_bias,
            "bias_source": "manual",
            "stablecoin_netflow_usd": None,
        }

    total = fetch_glassnode_netflow_sum(bias_timeframe)
    if total is None:
        return {
            "selected_bias": requested_bias,
            "bias_source": "manual_fallback_no_glassnode_key",
            "stablecoin_netflow_usd": None,
        }

    if total > 0:
        selected = "inflow"
    elif total < 0:
        selected = "outflow"
    else:
        selected = "neutral"
    return {
        "selected_bias": selected,
        "bias_source": f"glassnode_{bias_timeframe}",
        "stablecoin_netflow_usd": total,
    }


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
        out[tf] = ((longs - shorts) / denom) if denom > 0 else 0.0
    return out


def fetch_cm_orderbook_pressure(symbol: str, exchange: str, cm_key: str) -> Optional[float]:
    if not cm_key:
        return None
    q = urllib.parse.urlencode(
        {
            "e": exchange,
            "symbol": symbol,
            "api_key": cm_key,
        }
    )
    url = f"https://api.cryptometer.io/merged-orderbook/?{q}"
    payload = safe_http_get_json(url)
    if not isinstance(payload, dict):
        return None
    data = payload.get("data", [])
    if not isinstance(data, list) or not data:
        return None
    row = data[0]
    bids = to_float(row.get("bids"))
    asks = to_float(row.get("asks"))
    denom = bids + asks
    if denom <= 0:
        return 0.0
    return (bids - asks) / denom


def base_asset(pair_symbol: str) -> str:
    if pair_symbol.endswith("USDT"):
        return pair_symbol[:-4]
    if pair_symbol.endswith("USDC"):
        return pair_symbol[:-4]
    return pair_symbol


def build_candidates(
    symbols_filter: Optional[List[str]],
    timeframe: str,
    limit: int,
    netflow_bias: str,
    cm_key: str,
    cm_ls_exchange: str,
    cm_ls_timeframes: List[str],
    cm_ob_exchange: str,
    pressure_weight_ls: float,
    pressure_weight_ob: float,
    pressure_enabled: bool,
) -> List[Dict[str, Any]]:
    spot = fetch_spot_24h()
    fut = fetch_futures_24h()

    by_base: Dict[str, Dict[str, Any]] = {}
    for r in spot:
        pair = str(r.get("symbol", "")).upper()
        base = base_asset(pair)
        if not base:
            continue
        if symbols_filter and base not in symbols_filter:
            continue
        row = by_base.setdefault(
            base,
            {
                "symbol": base,
                "spot_quote_volume_usd": 0.0,
                "spot_trade_count": 0.0,
                "spot_price_change_pct_avg": 0.0,
                "pairs": [],
            },
        )
        row["spot_quote_volume_usd"] += to_float(r.get("quoteVolume"))
        row["spot_trade_count"] += to_float(r.get("count"))
        row["spot_price_change_pct_avg"] += to_float(r.get("priceChangePercent"))
        row["pairs"].append(pair)

    out: List[Dict[str, Any]] = []
    for base, row in by_base.items():
        pair_count = max(len(row["pairs"]), 1)
        row["spot_price_change_pct_avg"] = row["spot_price_change_pct_avg"] / pair_count
        fut_row = fut.get(f"{base}USDT", {})
        row["perp_quote_volume_usd_24h"] = to_float(fut_row.get("quoteVolume"))
        row["perp_price_change_pct_24h"] = to_float(fut_row.get("priceChangePercent"))
        row["oi_change_usd"] = fetch_oi_change(f"{base}USDT", timeframe) if fut_row else 0.0

        ls_map = fetch_cm_long_short_pressure(base, cm_ls_exchange, cm_ls_timeframes, cm_key)
        row["cm_long_short_pressure"] = ls_map
        row["cm_long_short_pressure_avg"] = (
            sum(ls_map.values()) / len(ls_map) if ls_map else 0.0
        )

        ob = fetch_cm_orderbook_pressure(base, cm_ob_exchange, cm_key)
        row["cm_orderbook_pressure"] = ob if ob is not None else 0.0
        row["cm_orderbook_available"] = ob is not None
        out.append(row)

    if not out:
        return []

    spot_vals = [r["spot_quote_volume_usd"] for r in out]
    perp_vals = [r["perp_quote_volume_usd_24h"] for r in out]
    oi_vals = [r["oi_change_usd"] for r in out]

    bias_sign = 1.0 if netflow_bias == "inflow" else -1.0 if netflow_bias == "outflow" else 0.0
    for r in out:
        z_spot = zscore(r["spot_quote_volume_usd"], spot_vals)
        z_perp = zscore(r["perp_quote_volume_usd_24h"], perp_vals)
        z_oi = zscore(r["oi_change_usd"], oi_vals)

        core = (0.40 * z_spot) + (0.25 * z_perp) + (0.20 * z_oi * (1.0 + bias_sign))
        pressure_score = (
            (pressure_weight_ls * r["cm_long_short_pressure_avg"])
            + (pressure_weight_ob * r["cm_orderbook_pressure"])
        ) if pressure_enabled else 0.0

        r["core_score"] = core
        r["pressure_score"] = pressure_score
        r["deployment_score"] = core + pressure_score
        r["bias"] = netflow_bias

    out.sort(key=lambda x: x["deployment_score"], reverse=True)
    return out[:limit]


def parse_symbols(raw: str) -> List[str]:
    return [x.strip().upper() for x in raw.split(",") if x.strip()]


def parse_timeframes(raw: str) -> List[str]:
    return [x.strip() for x in raw.split(",") if x.strip()]


def resolve_profile(profile: str, timeframe: str, cm_ls_timeframes: str) -> Tuple[str, List[str], str]:
    if profile in PROFILE_MAP:
        cfg = PROFILE_MAP[profile]
        return str(cfg["timeframe"]), list(cfg["ls_timeframes"]), profile
    return timeframe, parse_timeframes(cm_ls_timeframes), "custom"


def as_table(
    rows: List[Dict[str, Any]],
    timeframe: str,
    bias: str,
    bias_source: str,
    netflow_usd: Optional[float],
    pressure_enabled: bool,
    pressure_weight_ls: float,
    pressure_weight_ob: float,
    profile: str,
    ls_timeframes: List[str],
) -> str:
    lines = [f"Stablecoin Deployment Candidates ({timeframe}, bias={bias}, source={bias_source}, profile={profile})"]
    lines.append(f"Pressure Block: enabled={pressure_enabled} ls_w={pressure_weight_ls} ob_w={pressure_weight_ob} ls_tfs={','.join(ls_timeframes)}")
    if netflow_usd is not None:
        lines.append(f"Stablecoin Netflow (USDT+USDC): ${netflow_usd:.0f}")
    lines.append("")
    for i, r in enumerate(rows, start=1):
        lines.append(f"{i}. {r['symbol']}  score={r['deployment_score']:.3f}")
        lines.append(f"   spot_quote_vol=${r['spot_quote_volume_usd']:.0f}  perp_quote_vol=${r['perp_quote_volume_usd_24h']:.0f}")
        lines.append(f"   oi_change=${r['oi_change_usd']:.0f}  spot_24h%={r['spot_price_change_pct_avg']:.2f}  perp_24h%={r['perp_price_change_pct_24h']:.2f}")
        lines.append(f"   core_score={r['core_score']:.3f} pressure_score={r['pressure_score']:.3f}")
        ls = r.get("cm_long_short_pressure", {})
        if ls:
            ls_str = ", ".join(f"{k}:{v:.3f}" for k, v in ls.items())
            lines.append(f"   cm_long_short_pressure [{ls_str}] avg={r['cm_long_short_pressure_avg']:.3f}")
        if r.get("cm_orderbook_available"):
            lines.append(f"   cm_orderbook_pressure={r['cm_orderbook_pressure']:.3f}")
    return "\n".join(lines)


def main() -> None:
    p = argparse.ArgumentParser(description="Best-effort stablecoin deployment destination screener")
    p.add_argument("--symbols", default="", help="Optional comma-separated base symbols, e.g. BTC,ETH,SOL")
    p.add_argument("--timeframe", default="1h", help="5m|15m|30m|1h|2h|4h|6h|12h|1d (for OI delta)")
    p.add_argument("--profile", choices=["custom", "ltf", "mtf", "htf"], default="custom", help="Preset timeframe profile for this part")
    p.add_argument("--limit", type=int, default=15, help="Number of coins to return")
    p.add_argument("--netflow-bias", choices=["inflow", "outflow", "neutral"], default="inflow")
    p.add_argument("--auto-bias", action="store_true", help="Auto-set bias from latest Glassnode USDT+USDC exchange netflow")
    p.add_argument("--bias-timeframe", choices=["1h", "24h"], default="1h", help="Glassnode interval used by --auto-bias")

    p.add_argument("--cm-api-key", default=os.environ.get("CRYPTOMETER_API_KEY", ""), help="Cryptometer API key (or set CRYPTOMETER_API_KEY)")
    p.add_argument("--cm-ls-exchange", default="binance_futures", help="Cryptometer exchange for long-shorts-data (e.g. binance_futures, bybit)")
    p.add_argument("--cm-ls-timeframes", default="15m,1h,4h", help="Comma-separated timeframes for long-shorts-data")
    p.add_argument("--cm-ob-exchange", default="binance", help="Cryptometer exchange for merged-orderbook")

    p.add_argument("--pressure-enabled", action="store_true", help="Enable grouped pressure block (long/short + orderbook)")
    p.add_argument("--pressure-weight-ls", type=float, default=0.15, help="Weight for long/short pressure in pressure block")
    p.add_argument("--pressure-weight-ob", type=float, default=0.15, help="Weight for orderbook pressure in pressure block")

    p.add_argument("--format", choices=["table", "json"], default="table")
    args = p.parse_args()

    effective_timeframe, effective_ls_tfs, effective_profile = resolve_profile(
        args.profile,
        args.timeframe,
        args.cm_ls_timeframes,
    )

    symbols_filter = parse_symbols(args.symbols) if args.symbols else None
    bias_info = resolve_bias(args.netflow_bias, args.auto_bias, args.bias_timeframe)
    selected_bias = str(bias_info["selected_bias"])

    rows = build_candidates(
        symbols_filter,
        effective_timeframe,
        args.limit,
        selected_bias,
        str(args.cm_api_key or "").strip(),
        args.cm_ls_exchange,
        effective_ls_tfs,
        args.cm_ob_exchange,
        args.pressure_weight_ls,
        args.pressure_weight_ob,
        args.pressure_enabled,
    )

    if args.format == "json":
        print(
            json.dumps(
                {
                    "profile": effective_profile,
                    "timeframe": effective_timeframe,
                    "ls_timeframes": effective_ls_tfs,
                    "bias": selected_bias,
                    "bias_source": bias_info["bias_source"],
                    "stablecoin_netflow_usd": bias_info["stablecoin_netflow_usd"],
                    "results": rows,
                },
                indent=2,
            )
        )
    else:
        print(
            as_table(
                rows,
                effective_timeframe,
                selected_bias,
                str(bias_info["bias_source"]),
                bias_info["stablecoin_netflow_usd"],
                args.pressure_enabled,
                args.pressure_weight_ls,
                args.pressure_weight_ob,
                effective_profile,
                effective_ls_tfs,
            )
        )


if __name__ == "__main__":
    main()