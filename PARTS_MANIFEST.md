# Crypto Tool Parts Manifest

## Purpose
This file is the pinned session resume map for module-first development.

## Part Status

| Part | Script | Status | Core Inputs | Core Outputs | Mode |
|---|---|---|---|---|---|
| A Pressure | `oi_combined_view.py` | Built | long/short pressure, orderbook pressure | `pressure_score`, `pressure_state`, `pressure_reason` | Manual/API |
| B Liquidity | `liquidity_module.py` | Built | exchange inflow/outflow/net, exchange balance delta, whale-to-exchange | `liquidity_score`, `liquidity_state`, `liquidity_reason` | Manual/API |
| C Derivatives | `part_c_derivatives.py` | Built | OI change, perp volume change, funding, optional LS block | `derivatives_score`, `derivatives_state`, `derivatives_reason` | Manual/API |
| D Liquidation | `part_d_liquidation.py` | Built | liquidation heatmap + liquidation event signals | `liq_level_bias`, `liq_event_state`, `liq_composite_state`, `liq_reason` | Manual/API |
| E Liquidation Context Layer | `part_e_liq_context.py` | Built (standalone) | liquidation level POIs + liquidation events + entry heatmap net | chart-focused POI/magnet/entry context output | Manual/MCP-fed |

## Supporting Modules

| Module | Script | Purpose |
|---|---|---|
| Structure | `structure_module.py` | Bias-style structure read (`Continue`, `Wait Pullback`, `Mean Revert`, `Avoid`) |
| Momentum | `momentum_module.py` | Timeframe-aware momentum read |
| Global Timeframe Router | `global_timeframe_router.py` | One profile switch (`ltf/mtf/htf`) that emits aligned per-part settings and command templates |
| Stablecoin Utility | `stablecoin_deployment_candidates.py` | Stablecoin-related helper logic/candidate handling |
| Part E MCP Feed Builder | `part_e_mcp_feed.py` | Converts raw MCP snapshots into Part E `--source mcp` payload format |
| Smoke Tests | `smoke_test_parts.ps1` | Contract checks for core parts |

## Tunables By Part

### Part A Pressure
- Long/short weight
- Orderbook weight
- State thresholds (bullish/neutral/bearish cutoffs)
- Timeframe/profile selector
- Data source selector (`exchange_api|coinalyze|auto`)

### Part B Liquidity
- Inflow/outflow/netflow weighting
- Exchange balance regime thresholds
- Whale transfer impact weighting
- Balance-regime sensitivity (mild/medium/strong)
- Timeframe/profile selector

### Part C Derivatives
- OI change weight
- Perp volume change weight
- Funding pressure weight
- LS block blend (`w_ls`)
- LS timeframe mode:
  - single timeframe, or
  - `MTF Consensus Toggle` (`mtf_consensus`)
- Overheat/crowding gate label behavior
- State thresholds (bullish/neutral/bearish cutoffs)
- Timeframe/profile selector
- Data source selector (`exchange_api|coinalyze|auto|manual`)

### Part D Liquidation
- Heatmap interpretation thresholds
- Event timeframe selector (`10m`, `1h`, `24h`)
- Composite state thresholds
- Heatmap timeframe handling (currently fixed `1h`)
- Symbol support enforcement list

### Part E Liquidation Context Layer (Planned)
- Composition weights across:
  - liquidation volumes
  - liquidation heatmaps
  - liquidation entry-price heatmaps
- Context state thresholds
- Reason text contract

### Part E Liquidation Context Layer
- Primary weighting lock:
  - location POIs (`w_location`) default `0.55`
  - liquidation events (`w_event`) default `0.25`
  - entry context (`w_entry`) default `0.20`
- POI controls:
  - `max_levels`
  - `near_poi_pct`
- Risk/entry controls:
  - `chop_ratio`
  - `bias_deadband`
  - `invalidation_pad_pct`
  - `entry_scale`
  - `min_pressure_floor`
  - `neutral_composite_band`
- Source contract:
  - `--source manual` (local tuning values)
  - `--source mcp --mcp-input-file <json>` (MCP-fed payload)
  - No direct Glassnode REST/api-key mode in Part E runtime path.
  - Provider routing:
    - `--provider glassnode_mcp|manual|auto`
    - `--fallback-provider none|glassnode_mcp|manual`

## Data Source Policy (Current)
- Active build/test sources: current project sources only.
- Parked/disabled by decision (remembered, not active): Messari, Santiment, Mobula, Dune.
- Source switching policy:
  - Prefer explicit `--source` per module.
  - `auto` means try primary source first, then fallback source if configured/available.
  - New APIs/MCPs should be added as selectable providers, not hard-wired defaults.

## Build Order Policy
1. Build each part standalone.
2. Tune each part standalone.
3. Freeze output contracts.
4. Integrate parts later into a unified machine.

## Global Timeframe Component
- Use:
  - `python global_timeframe_router.py --profile mtf --symbols BTC,ETH,SOL --assets USDT,USDC --format table`
- Output:
  - aligned part profiles
  - ready-to-run command templates for Part A/B/C/D/E + Structure + Momentum

## Resume Prompt (Session Starter)
Use this at session start:

"Load `PARTS_MANIFEST.md` and continue module-first tuning. Do not integrate all parts yet unless explicitly requested."
