# Project State

Updated: 2026-03-08 (America/Chicago)

## Objective
Build modular crypto decision parts (A/B/C/D/E), tune each independently, then combine later.

## Active Data Stack
- Glassnode (when `GLASSNODE_API_KEY` set)
- Cryptometer (when `CRYPTOMETER_API_KEY` set)

## Disabled but Remembered
- Messari
- Santiment
- Mobula

## Module Status
- Part A Pressure: Built
- Part B Liquidity: Built + tuned
- Part C Derivatives: Built + tuned + overheat label + LS block
- Part D Liquidation Positioning: Built (API requires GLASSNODE key)
- Part E: Built standalone (`part_e_liq_context.py`)

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

## Current Next Step
- Tune Part D and Part E thresholds with live data after setting `GLASSNODE_API_KEY`.
- Keep parts standalone; do not integrate full machine yet.
