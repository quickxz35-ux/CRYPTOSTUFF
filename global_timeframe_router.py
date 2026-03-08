#!/usr/bin/env python3
"""Global timeframe router for module-first crypto parts.

Purpose:
- One switch (`--profile ltf|mtf|htf`) to keep all parts aligned.
- Emit per-part settings and ready-to-run command templates.
"""

from __future__ import annotations

import argparse
import json
import os
from typing import Dict, List


PART_SCRIPTS = {
    "part_a_pressure": "oi_combined_view.py",
    "part_b_liquidity": "liquidity_module.py",
    "part_c_derivatives": "part_c_derivatives.py",
    "part_d_liquidation": "part_d_liquidation.py",
    "part_e_liq_context": "part_e_liq_context.py",
    "structure": "structure_module.py",
    "momentum": "momentum_module.py",
}

DEFAULT_PROFILE_MAP: Dict[str, Dict[str, str]] = {
    k: {
        "part_a_profile": k,
        "part_b_profile": k,
        "part_c_profile": k,
        "part_d_profile": k,
        "part_e_profile": k,
        "structure_profile": k,
        "momentum_profile": k,
    }
    for k in ("ltf", "mtf", "htf")
}


def parse_csv(raw: str) -> str:
    return ",".join([x.strip().upper() for x in raw.split(",") if x.strip()])


def load_profile_map(profiles_file: str) -> Dict[str, Dict[str, str]]:
    try:
        with open(profiles_file, "r", encoding="utf-8") as f:
            obj = json.load(f)
    except Exception:
        return DEFAULT_PROFILE_MAP
    if not isinstance(obj, dict):
        return DEFAULT_PROFILE_MAP

    out: Dict[str, Dict[str, str]] = {}
    for prof in ("ltf", "mtf", "htf"):
        section = obj.get(prof, {})
        pp = section.get("part_profiles", {}) if isinstance(section, dict) else {}
        if not isinstance(pp, dict):
            pp = {}
        out[prof] = {
            "part_a_profile": str(pp.get("part_a", prof)),
            "part_b_profile": str(pp.get("part_b", prof)),
            "part_c_profile": str(pp.get("part_c", prof)),
            "part_d_profile": str(pp.get("part_d", prof)),
            "part_e_profile": str(pp.get("part_e", prof)),
            "structure_profile": str(pp.get("structure", prof)),
            "momentum_profile": str(pp.get("momentum", prof)),
        }
    return out


def cmd(py: str, script: str, args: List[str]) -> str:
    q = lambda s: f"\"{s}\""
    return " ".join([q(py), q(script)] + args)


def build_commands(root: str, py: str, profile_cfg: Dict[str, str], symbols: str, assets: str, part_e_mcp_file: str) -> Dict[str, str]:
    a = cmd(py, os.path.join(root, PART_SCRIPTS["part_a_pressure"]), ["--symbols", symbols, "--profile", profile_cfg["part_a_profile"]])
    b = cmd(py, os.path.join(root, PART_SCRIPTS["part_b_liquidity"]), ["--profile", profile_cfg["part_b_profile"], "--assets", assets, "--source", "manual"])
    c = cmd(py, os.path.join(root, PART_SCRIPTS["part_c_derivatives"]), ["--profile", profile_cfg["part_c_profile"], "--symbols", symbols, "--source", "manual"])
    d = cmd(py, os.path.join(root, PART_SCRIPTS["part_d_liquidation"]), ["--profile", profile_cfg["part_d_profile"], "--symbols", symbols, "--source", "manual"])
    e = cmd(
        py,
        os.path.join(root, PART_SCRIPTS["part_e_liq_context"]),
        [
            "--profile",
            profile_cfg["part_e_profile"],
            "--symbols",
            symbols,
            "--source",
            "mcp",
            "--provider",
            "auto",
            "--fallback-provider",
            "manual",
            "--mcp-input-file",
            part_e_mcp_file,
        ],
    )
    s = cmd(py, os.path.join(root, PART_SCRIPTS["structure"]), ["--profile", profile_cfg["structure_profile"], "--symbols", symbols])
    m = cmd(py, os.path.join(root, PART_SCRIPTS["momentum"]), ["--profile", profile_cfg["momentum_profile"], "--symbols", symbols])
    return {
        "part_a_pressure": a,
        "part_b_liquidity": b,
        "part_c_derivatives": c,
        "part_d_liquidation": d,
        "part_e_liq_context": e,
        "structure": s,
        "momentum": m,
    }


def main() -> None:
    p = argparse.ArgumentParser(description="Global timeframe router for all parts/modules")
    p.add_argument("--profile", choices=["ltf", "mtf", "htf"], required=True)
    p.add_argument("--symbols", required=True, help="Comma-separated symbols, e.g. BTC,ETH,SOL")
    p.add_argument("--assets", default="USDT,USDC", help="Comma-separated assets for Part B")
    p.add_argument("--python", default="python", help="Python executable")
    p.add_argument("--part-e-mcp-input-file", default="part_e_payload.json")
    p.add_argument("--profiles-file", default="timeframe_profiles.json", help="JSON profile map file")
    p.add_argument("--output-file", default="", help="Optional file path to save emitted JSON")
    p.add_argument("--format", choices=["json", "table"], default="json")
    args = p.parse_args()

    root = os.path.dirname(os.path.abspath(__file__))
    symbols = parse_csv(args.symbols)
    assets = parse_csv(args.assets)
    profile_map = load_profile_map(os.path.join(root, args.profiles_file))
    cfg = profile_map.get(args.profile, DEFAULT_PROFILE_MAP[args.profile])
    commands = build_commands(root, args.python, cfg, symbols, assets, args.part_e_mcp_input_file)

    payload = {
        "global_profile": args.profile,
        "symbols": symbols.split(","),
        "assets": assets.split(","),
        "part_profiles": {
            "part_a": cfg["part_a_profile"],
            "part_b": cfg["part_b_profile"],
            "part_c": cfg["part_c_profile"],
            "part_d": cfg["part_d_profile"],
            "part_e": cfg["part_e_profile"],
            "structure": cfg["structure_profile"],
            "momentum": cfg["momentum_profile"],
        },
        "commands": commands,
    }

    if args.output_file:
        with open(args.output_file, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)

    if args.format == "json":
        print(json.dumps(payload, indent=2))
    else:
        print(f"Global timeframe profile: {args.profile}")
        print(f"Symbols: {symbols}")
        print(f"Assets: {assets}")
        print("")
        print("Part Profiles")
        for k, v in payload["part_profiles"].items():
            print(f"- {k}: {v}")
        print("")
        print("Commands")
        for k, v in commands.items():
            print(f"- {k}: {v}")


if __name__ == "__main__":
    main()
