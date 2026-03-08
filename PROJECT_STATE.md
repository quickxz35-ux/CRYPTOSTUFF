# Project State

Updated: 2026-03-08 (America/Chicago)

## Objective
Build modular crypto decision parts (A/B/C/D/E), tune each independently, then combine later.

## Active Data Stack
- Glassnode MCP (MCP-only policy)
- Cryptometer (when `CRYPTOMETER_API_KEY` set)
- Coinalyze (when `COINALYZE_API_KEY` set)

## Disabled but Remembered
- Messari
- Santiment
- Mobula

## Module Status
- Part A Pressure: Built
- Part B Liquidity: Built + tuned
- Part C Derivatives: Built + tuned + overheat label + LS block
- Part D Liquidation Positioning: Built (run via manual now; MCP-feed policy)
- Part E: Built standalone (`part_e_liq_context.py`)
- Part E MCP feed builder: Built (`part_e_mcp_feed.py`)
- Global timeframe router: Built (`global_timeframe_router.py`)

## Key Decisions Locked
- Build/tune each part independently first.
- Overheat gate is state-label only (`bullish_crowded`), no score cap.
- LS block supports single or multi-timeframe.
- LS timeframe aggregation = equal average across selected timeframes.
- Naming: `MTF Consensus Toggle` (`mtf_consensus`).

## Important Files
- `CRYPTO_SCREENING_PLAYBOOK.md`
- `liquidity_module.py`
- `part_c_derivatives.py`
- `part_d_liquidation.py`
- `momentum_module.py`
- `structure_module.py`
- `timeframe_profiles.json`
- `run_all_parts.ps1`
- `pretrade_snapshot.ps1`

## Current Next Step
- Tune Part D and Part E thresholds using MCP-fed live payloads.
- Validate provider fallback behavior (`--provider auto`) on missing/partial inputs.
- Use global timeframe router for consistent profile alignment across all parts.
- Keep parts standalone; do not integrate full machine yet.
- Continue extending unified source switching across all parts (`auto|provider|manual`).
- Shared routing helper added: `source_routing.py`.
