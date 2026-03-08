#!/usr/bin/env python3
"""Standalone structure module with LTF/MTF/HTF profiles."""

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
    "ltf": {"timeframe": "15m", "lookback": 96, "breakout_lookback": 20},
    "mtf": {"timeframe": "1h", "lookback": 120, "breakout_lookback": 30},
    "htf": {"timeframe": "4h", "lookback": 120, "breakout_lookback": 40},
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


def resolve_profile(profile: str, timeframe: str, lookback: int, breakout_lookback: int) -> Tuple[str, int, int, str]:
    if profile in PROFILE_MAP:
        cfg = PROFILE_MAP[profile]
        return str(cfg["timeframe"]), int(cfg["lookback"]), int(cfg["breakout_lookback"]), profile
    return timeframe, lookback, breakout_lookback, "custom"


def fetch_spot_klines(symbol: str, interval: str, limit: int) -> List[List[Any]]:
    query = urllib.parse.urlencode({"symbol": f"{symbol}USDT", "interval": interval, "limit": limit})
    url = f"https://api.binance.com/api/v3/klines?{query}"
    data = safe_http_get_json(url)
    return data if isinstance(data, list) else []


def sma(values: List[float], length: int) -> float:
    if len(values) < length:
        return 0.0
    return statistics.mean(values[-length:])


def trend_state(closes: List[float]) -> Tuple[str, str]:
    if len(closes) < 55:
        return "neutral", "insufficient trend history"
    sma20 = sma(closes, 20)
    sma50 = sma(closes, 50)
    slope = closes[-1] - closes[-10]
    if sma20 > sma50 and slope > 0:
        return "bullish", "SMA20>SMA50 and positive slope"
    if sma20 < sma50 and slope < 0:
        return "bearish", "SMA20<SMA50 and negative slope"
    return "neutral", "mixed moving-average/slope context"


def range_position(close: float, highs: List[float], lows: List[float], lookback: int) -> float:
    h = max(highs[-lookback:])
    l = min(lows[-lookback:])
    if h == l:
        return 0.5
    return clamp((close - l) / (h - l), 0.0, 1.0)


def break_state(close: float, highs: List[float], lows: List[float], lookback: int) -> str:
    prior_high = max(highs[-(lookback + 1):-1])
    prior_low = min(lows[-(lookback + 1):-1])
    if close > prior_high:
        return "broke_prior_high"
    if close < prior_low:
        return "broke_prior_low"
    return "no_break"


def true_ranges(highs: List[float], lows: List[float], closes: List[float]) -> List[float]:
    out: List[float] = []
    for i in range(1, len(closes)):
        h = highs[i]
        l = lows[i]
        pc = closes[i - 1]
        out.append(max(h - l, abs(h - pc), abs(l - pc)))
    return out


def volatility_state(highs: List[float], lows: List[float], closes: List[float]) -> Tuple[str, float]:
    trs = true_ranges(highs, lows, closes)
    if len(trs) < 20:
        return "normal", 1.0
    atr = statistics.mean(trs[-14:])
    base = statistics.mean(trs[-50:-1]) if len(trs) > 50 else statistics.mean(trs[:-1])
    ratio = (atr / base) if base else 1.0
    if ratio >= 1.2:
        return "expanding", ratio
    if ratio <= 0.8:
        return "contracting", ratio
    return "normal", ratio


def trend_quality(closes: List[float]) -> str:
    if len(closes) < 30:
        return "weak"
    rets = [closes[i] - closes[i - 1] for i in range(len(closes) - 20, len(closes))]
    pos = sum(1 for r in rets if r > 0)
    neg = sum(1 for r in rets if r < 0)
    dominant = max(pos, neg)
    if dominant >= 15:
        return "clean"
    if dominant <= 11:
        return "choppy"
    return "moderate"


def structure_state_from_parts(t_state: str, b_state: str, r_pos: float, v_state: str) -> str:
    if b_state == "broke_prior_high" and t_state == "bullish":
        return "Breakout"
    if b_state == "broke_prior_low" and t_state == "bearish":
        return "Breakdown"
    if 0.35 <= r_pos <= 0.65 and v_state in {"contracting", "normal"}:
        return "Range"
    if t_state == "bullish" and r_pos < 0.50:
        return "Pullback"
    if t_state == "bearish" and r_pos > 0.50:
        return "Pullback"
    return "Range"


def state_line_from_parts(v_state: str, q_state: str) -> str:
    if v_state == "expanding":
        return "Expansion"
    if v_state == "contracting":
        return "Compression"
    return "Chop" if q_state == "choppy" else "Normal"


def bias_from_snapshot(t_state: str, s_state: str, line: str) -> str:
    if t_state == "bullish" and s_state == "Breakout" and line == "Expansion":
        return "Continue"
    if t_state == "bullish" and s_state == "Pullback":
        return "Wait Pullback"
    if t_state == "bearish" and s_state == "Breakdown" and line == "Expansion":
        return "Continue"
    if s_state == "Range" or line == "Chop":
        return "Mean Revert"
    if t_state == "neutral":
        return "Mean Revert"
    return "Avoid"


def run_symbol(symbol: str, timeframe: str, lookback: int, breakout_lookback: int) -> Dict[str, Any]:
    required = max(lookback + 5, breakout_lookback + 5, 80)
    rows = fetch_spot_klines(symbol, timeframe, required)
    if len(rows) < required:
        return {"symbol": symbol, "error": "insufficient_kline_data"}

    highs = [to_float(r[2]) for r in rows]
    lows = [to_float(r[3]) for r in rows]
    closes = [to_float(r[4]) for r in rows]

    close = closes[-1]
    t_state, t_reason = trend_state(closes)
    r_pos = range_position(close, highs, lows, breakout_lookback)
    b_state = break_state(close, highs, lows, breakout_lookback)
    v_state, v_ratio = volatility_state(highs, lows, closes)
    q_state = trend_quality(closes)

    s_state = structure_state_from_parts(t_state, b_state, r_pos, v_state)
    line = state_line_from_parts(v_state, q_state)
    bias = bias_from_snapshot(t_state, s_state, line)

    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "trend_state": t_state,
        "structure_state": s_state,
        "state_line": line,
        "bias": bias,
        "range_position": r_pos,
        "break_state": b_state,
        "volatility_state": v_state,
        "volatility_ratio": v_ratio,
        "trend_quality": q_state,
        "structure_reason": f"{t_reason}; break={b_state}; range_pos={r_pos:.2f}; vol={v_state}",
    }


def as_table(rows: List[Dict[str, Any]], profile: str, timeframe: str, lookback: int, breakout_lookback: int) -> str:
    lines = [
        f"Structure Module (profile={profile}, timeframe={timeframe}, lookback={lookback}, breakout_lookback={breakout_lookback})",
        "",
    ]
    for r in rows:
        if r.get("error"):
            lines.append(f"{r['symbol']}: ERROR {r['error']}")
            continue
        lines.append(
            f"{r['symbol']}: Trend={r['trend_state']} | Structure={r['structure_state']} | State={r['state_line']} | Bias={r['bias']}"
        )
        lines.append(
            f"  break={r['break_state']} range_pos={r['range_position']:.2f} vol={r['volatility_state']}({r['volatility_ratio']:.2f}) quality={r['trend_quality']}"
        )
        lines.append(f"  reason: {r['structure_reason']}")
    return "\n".join(lines)


def main() -> None:
    p = argparse.ArgumentParser(description="Standalone structure module")
    p.add_argument("--symbols", required=True, help="Comma-separated symbols, e.g. BTC,ETH,SOL")
    p.add_argument("--profile", choices=["custom", "ltf", "mtf", "htf"], default="mtf")
    p.add_argument("--timeframe", default="1h", help="Used in custom profile")
    p.add_argument("--lookback", type=int, default=120, help="Used in custom profile")
    p.add_argument("--breakout-lookback", type=int, default=30, help="Used in custom profile")
    p.add_argument("--format", choices=["table", "json"], default="table")
    args = p.parse_args()

    timeframe, lookback, breakout_lookback, effective_profile = resolve_profile(
        args.profile,
        args.timeframe,
        args.lookback,
        args.breakout_lookback,
    )

    symbols = parse_symbols(args.symbols)
    rows = [run_symbol(s, timeframe, lookback, breakout_lookback) for s in symbols]

    if args.format == "json":
        print(
            json.dumps(
                {
                    "profile": effective_profile,
                    "timeframe": timeframe,
                    "lookback": lookback,
                    "breakout_lookback": breakout_lookback,
                    "results": rows,
                },
                indent=2,
            )
        )
    else:
        print(as_table(rows, effective_profile, timeframe, lookback, breakout_lookback))


if __name__ == "__main__":
    main()