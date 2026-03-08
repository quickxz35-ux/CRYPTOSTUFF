# Crypto Screening Playbook

Updated: 2026-03-07 (America/Chicago)

## Objective
Build a working system to:
1. Screen coins.
2. Monitor for entry conditions.
3. Maintain a chart/technical-analysis roadmap per coin.
4. Execute entries in Bookmap.

## Active Metrics (Glassnode MCP)
- `/v1/metrics/derivatives/futures_open_interest_sum`
- `/v1/metrics/derivatives/futures_funding_rate_perpetual`
- `/v1/metrics/derivatives/futures_volume_daily_perpetual_sum`

## Inactive Metrics (for now)
- `/v1/metrics/derivatives/options_implied_volatility_term_structure`

## Confirmed Constraints
- Options term structure is BTC/ETH only.
- User does not want paid Coinglass dependency.

## Build Direction
1. Screener phase:
- Rank candidates by OI change, funding regime, and perp volume expansion.

2. Monitoring phase:
- Track shortlisted symbols on selected timeframe(s).
- Alert on shifts in OI/funding/perp volume that align with setup direction.

3. Technical roadmap phase:
- For each monitored coin, keep:
  - Trend bias
  - Support/resistance and breakout levels
  - Invalidations
  - Entry trigger checklist

4. Execution phase:
- User executes in Bookmap using liquidity/flow confirmation.

## Next Implementation Block
- Build a single command that:
1. Accepts one or many symbols.
2. Pulls active 3 derivatives metrics.
3. Outputs a ranked table + simple long/short watch tags.


## Long/Short Pressure Model (Active)
Data inputs (Glassnode):
- `/v1/metrics/derivatives/futures_volume_buy_daily_perpetual_sum`
- `/v1/metrics/derivatives/futures_volume_sell_daily_perpetual_sum`
- `/v1/metrics/derivatives/futures_cvd_perpetual`

Timeframes:
- `10m`, `1h`, `24h`

Computed values:
1. `buy_sell_ratio = buy_volume / max(sell_volume, tiny)`
2. `buy_sell_imbalance = (buy_volume - sell_volume) / max((buy_volume + sell_volume), tiny)`
3. `cvd_slope = cvd_now - cvd_prev`

Pressure score:
- `score = 0.6 * buy_sell_imbalance + 0.4 * sign(cvd_slope)`

Regime tags:
- `bullish` when `buy_sell_ratio >= 1.15` and `cvd_slope > 0`
- `bearish` when `buy_sell_ratio <= 0.87` and `cvd_slope < 0`
- otherwise `neutral`

Notes:
- Use `1h` as default decision timeframe.
- Use `10m` only for trigger confirmation.
- Keep `24h` as trend context filter.

## Execution Mode (Locked)
- MCP-only.
- No direct Glassnode REST/API-key scripts.
- Pressure scans are run through Glassnode MCP tools in-session.

## MCP-Only Pressure Scan Flow
1. For each symbol/timeframe, fetch via MCP:
- `/v1/metrics/derivatives/futures_volume_buy_daily_perpetual_sum`
- `/v1/metrics/derivatives/futures_volume_sell_daily_perpetual_sum`
- `/v1/metrics/derivatives/futures_cvd_perpetual`
2. Compute model values (ratio, imbalance, cvd slope, score).
3. Assign regime tag (`bullish|bearish|neutral`).
4. Rank symbols by score and review with Bookmap for trigger.

## Signal Architecture (Module Split)

### Module A: Derivatives Pressure
Purpose:
- Measure directional pressure from derivatives positioning/activity.

Inputs:
- `/v1/metrics/derivatives/futures_open_interest_sum`
- `/v1/metrics/derivatives/futures_funding_rate_perpetual`
- `/v1/metrics/derivatives/futures_volume_daily_perpetual_sum`
- `/v1/metrics/derivatives/futures_volume_buy_daily_perpetual_sum`
- `/v1/metrics/derivatives/futures_volume_sell_daily_perpetual_sum`
- `/v1/metrics/derivatives/futures_cvd_perpetual`

Standard output:
- `pressure_regime`: `bullish|bearish|neutral`
- `pressure_score`: numeric
- `pressure_reason`: short text

### Module B: Exchange/Whale Flow
Purpose:
- Measure spot flow and large-holder distribution/accumulation risk.

Inputs:
- `/v1/metrics/transactions/transfers_volume_to_exchanges_sum`
- `/v1/metrics/transactions/transfers_volume_from_exchanges_sum`
- `/v1/metrics/transactions/transfers_volume_exchanges_net`
- `/v1/metrics/distribution/balance_exchanges`
- `/v1/metrics/transactions/transfers_volume_whales_to_exchanges_sum`

Standard output:
- `flow_regime`: `accumulation|distribution_risk|neutral`
- `flow_score`: numeric
- `flow_reason`: short text

### Module C (Later): Correlation Layer
Purpose:
- Combine A + B only after both modules are stable.

Planned logic (initial):
- Long bias watch: `pressure_regime=bullish` AND `flow_regime=accumulation`
- Short/avoid bias: `pressure_regime=bearish` AND `flow_regime=distribution_risk`
- Mixed: wait for Bookmap confirmation

Rule:
- Keep A and B independently runnable and independently debuggable.

## Module B Scoring Rules (MCP-Only)
Definitions (per symbol, timeframe):
- `to_exch`: latest `/transactions/transfers_volume_to_exchanges_sum`
- `from_exch`: latest `/transactions/transfers_volume_from_exchanges_sum`
- `net_exch`: latest `/transactions/transfers_volume_exchanges_net`
- `net_exch_prev`: previous net value
- `balance`: latest `/distribution/balance_exchanges`
- `balance_prev`: previous balance value
- `whale_btc`: latest BTC `/transactions/transfers_volume_whales_to_exchanges_sum` (global risk context)
- `whale_btc_avg24`: 24-point average of same BTC series

Derived:
- `net_delta = net_exch - net_exch_prev`
- `balance_delta = balance - balance_prev`
- `flow_ratio = net_exch / max(abs(to_exch) + abs(from_exch), tiny)`
- `whale_risk_ratio = whale_btc / max(whale_btc_avg24, tiny)`

Scoring components:
- `flow_component = +1` if `flow_ratio <= -0.15`; `-1` if `flow_ratio >= 0.15`; else `0`
- `balance_component = +1` if `balance_delta < 0`; `-1` if `balance_delta > 0`; else `0`
- `whale_component = +1` if `whale_risk_ratio <= 0.8`; `-1` if `whale_risk_ratio >= 1.5`; else `0`

Final score:
- `flow_score = 0.5*flow_component + 0.3*balance_component + 0.2*whale_component`

Regime mapping:
- `accumulation` if `flow_score >= 0.5`
- `distribution_risk` if `flow_score <= -0.5`
- `neutral` otherwise

Output fields:
- `flow_regime`
- `flow_score`
- `flow_reason`

## Coverage Pre-Check (Required Before Scoring)
Run this before Module A/Module B calculations.

Input:
- user symbol list
- selected timeframe (`10m|1h|24h`)

Endpoint checks:
- Module A required:
  - `/v1/metrics/derivatives/futures_open_interest_sum`
  - `/v1/metrics/derivatives/futures_funding_rate_perpetual`
  - `/v1/metrics/derivatives/futures_volume_daily_perpetual_sum`
  - `/v1/metrics/derivatives/futures_volume_buy_daily_perpetual_sum`
  - `/v1/metrics/derivatives/futures_volume_sell_daily_perpetual_sum`
  - `/v1/metrics/derivatives/futures_cvd_perpetual`
- Module B required:
  - `/v1/metrics/transactions/transfers_volume_to_exchanges_sum`
  - `/v1/metrics/transactions/transfers_volume_from_exchanges_sum`
  - `/v1/metrics/transactions/transfers_volume_exchanges_net`
  - `/v1/metrics/distribution/balance_exchanges`
- Module B context:
  - `/v1/metrics/transactions/transfers_volume_whales_to_exchanges_sum` (BTC context)

Coverage labels per symbol:
- `FULL`: all required endpoints for both modules available.
- `PARTIAL_A`: Module A available, Module B missing one or more required endpoints.
- `PARTIAL_B`: Module B available, Module A missing one or more required endpoints.
- `NONE`: neither module has enough required coverage.

Execution rule:
- Score only modules with sufficient coverage.
- Always output missing endpoints list for partial symbols.
- Never drop symbols silently.

## Module D: Liquidation Positioning Engine (Locked)
Purpose:
- Track long/short liquidation level positioning and active liquidation events.
- This module does NOT use exchange inflow/outflow.

Inputs:
- `/v1/metrics/derivatives/liquidation_entry_price_heatmap_long`
- `/v1/metrics/derivatives/liquidation_entry_price_heatmap_short`
- `/v1/metrics/derivatives/liquidation_entry_price_heatmap_net`
- `/v1/metrics/derivatives/liquidation_heatmap`
- `/v1/metrics/derivatives/futures_liquidated_total_volume_sum`
- optional: `/v1/metrics/derivatives/futures_liquidated_volume_long_sum`
- optional: `/v1/metrics/derivatives/futures_liquidated_volume_short_sum`

Timeframe contract:
- `heatmap_tf`: fixed `1h` (provider constraint)
- `event_tf`: customizable `10m | 1h | 24h`

Coverage:
- Heatmap family supported coins only: `BNB,BTC,DOGE,ETH,SOL,TON,XRP`

Required outputs:
- `liq_level_bias`: `long_side_loaded | short_side_loaded | balanced`
- `liq_event_state`: `flush_down | squeeze_up | calm`
- `liq_composite_state`: `bullish_watch | bearish_watch | high_vol_chop | neutral_watch`
- `liq_reason`: short plain-language explanation

Interpretation guide:
- `flush_down`: dominant long liquidations / downside sweep behavior
- `squeeze_up`: dominant short liquidations / upside sweep behavior
- `long_side_loaded`: more long-side liquidation levels clustered nearby
- `short_side_loaded`: more short-side liquidation levels clustered nearby

## Session Checkpoint (Saved)
Date:
- 2026-03-07 (America/Chicago)

Locked decisions:
- MCP-only workflow.
- No CME metrics.
- Options term structure inactive.
- Module architecture retained (A/B/C) and Module D added.
- Module D uses liquidation positioning only (no exchange flow).
- `heatmap_tf` fixed to `1h`; `event_tf` customizable to `10m|1h|24h`.

What Module D will show per supported coin:
- Long/short/net liquidation level areas.
- Active liquidation event state (`flush_down|squeeze_up|calm`).
- Composite watch state (`bullish_watch|bearish_watch|high_vol_chop|neutral_watch`).
- Short reason text.

## Momentum Part (Standalone) - Finalized

Script: `C:\Users\gssjr\OneDrive\Documents\New project\momentum_module.py`

### Output Contract
- `trend_direction` (bullish|bearish|neutral)
- `momentum_state` (bullish|bearish|neutral)
- `momentum_strength` (weak|medium|strong)
- `momentum_score` (-1..+1)
- `momentum_bias` (Continue|Wait Pullback|Mean Revert|Avoid)
- `momentum_reason` (one-line explanation)

### Profile Presets
- `ltf` -> timeframe `15m`, baseline `48`, impulse threshold `0.25%`
- `mtf` -> timeframe `1h`, baseline `72`, impulse threshold `0.50%`
- `htf` -> timeframe `4h`, baseline `90`, impulse threshold `1.00%`
- `custom` -> manual `--timeframe`, `--baseline`, `--impulse-threshold-pct`

### Current Scoring Terms
- price impulse term
- spot volume expansion term
- perp volume expansion term
- OI confirmation matrix
- score clamped to `[-1, +1]`

### Robustness Rule
- Unsupported symbols return `ERROR insufficient_kline_data` and do not stop the full module run.

### Standard Commands
```powershell
python "C:\Users\gssjr\OneDrive\Documents\New project\momentum_module.py" --symbols XRP,LINK,BARD --profile ltf --format table
python "C:\Users\gssjr\OneDrive\Documents\New project\momentum_module.py" --symbols XRP,LINK,BARD --profile mtf --format table
python "C:\Users\gssjr\OneDrive\Documents\New project\momentum_module.py" --symbols XRP,LINK,BARD --profile htf --format table
```

## Structure Part (Standalone) - Finalized

Script: `C:\Users\gssjr\OneDrive\Documents\New project\structure_module.py`

### Output Contract
- `trend_state` (bullish|bearish|neutral)
- `structure_state` (Breakout|Breakdown|Pullback|Range)
- `state_line` (Expansion|Compression|Chop|Normal)
- `bias` (Continue|Wait Pullback|Mean Revert|Avoid)
- extra diagnostics: `break_state`, `range_position`, `volatility_state`, `volatility_ratio`, `trend_quality`, `structure_reason`

### Profile Presets
- `ltf` -> timeframe `15m`, lookback `96`, breakout lookback `20`
- `mtf` -> timeframe `1h`, lookback `120`, breakout lookback `30`
- `htf` -> timeframe `4h`, lookback `120`, breakout lookback `40`
- `custom` -> manual `--timeframe`, `--lookback`, `--breakout-lookback`

### Robustness Rule
- Unsupported symbols return `ERROR insufficient_kline_data` and do not stop the full module run.

### Standard Commands
```powershell
python "C:\Users\gssjr\OneDrive\Documents\New project\structure_module.py" --symbols XRP,LINK,BARD --profile ltf --format table
python "C:\Users\gssjr\OneDrive\Documents\New project\structure_module.py" --symbols XRP,LINK,BARD --profile mtf --format table
python "C:\Users\gssjr\OneDrive\Documents\New project\structure_module.py" --symbols XRP,LINK,BARD --profile htf --format table
```

## Module B (Part B) Tuning - Locked Defaults (2026-03-07)

Script:
- C:\Users\gssjr\OneDrive\Documents\New project\liquidity_module.py

Profile defaults:
- ltf: w_netflow=0.32, w_ratio=0.18, w_balance=0.20, w_whale=0.30
- mtf: w_netflow=0.40, w_ratio=0.25, w_balance=0.25, w_whale=0.10
- htf: w_netflow=0.45, w_ratio=0.20, w_balance=0.30, w_whale=0.05
- custom fallback: .40 / 0.25 / 0.20 / 0.15

Behavior intent:
- ltf emphasizes whale shocks (faster tape).
- mtf is balanced between flow and balance regime.
- htf emphasizes netflow + balance regime, de-emphasizes whale noise.

Override rule:
- Any explicit weight flag (--w-netflow, --w-ratio, --w-balance, --w-whale) overrides profile defaults.


## Build Rule (Locked)
- Build/tune modules separately in order: A -> B -> C -> D -> E.
- Do **not** combine into one master score until each module is stable by timeframe (ltf/mtf/htf).
- After standalone tuning is complete, evaluate cross-module weighting.

## Weighting Method (How numbers are chosen)
1. Normalize each input metric to a comparable [-1, +1] scale.
2. Set initial weights by timeframe intent:
- ltf: more weight to fast/reactive signals.
- mtf: balanced mix.
- htf: more weight to slower/structural signals.
3. Run scenario tests (bull, bear, mixed, conflict) and check output states.
4. Adjust weights so outputs match intended behavior:
- clear bull scenario => bullish
- clear bear scenario => bearish
- conflicting scenario => neutral or weak bias
5. Lock profile defaults only after repeated stable behavior.

## Practical Rule For Choosing Weights
- Bigger weight = that signal has more control over final score.
- If one metric is noisy on a timeframe, reduce its weight there.
- If one metric is consistently predictive on a timeframe, increase its weight there.
- Keep weights summing near 1.0 for interpretability.

## Part C: Derivatives Standalone (Built)

Script:
- C:\Users\gssjr\OneDrive\Documents\New project\part_c_derivatives.py

Inputs:
- Open Interest change %
- Perp quote-volume change %
- Funding rate vs funding average

Outputs:
- derivatives_score
- derivatives_state (bullish|bearish|neutral)
- derivatives_reason

Profiles:
- ltf: event 15m, context 1h, weights w_oi=0.30 w_volume=0.50 w_funding=0.20
- mtf: event 1h, context 4h, weights w_oi=0.40 w_volume=0.35 w_funding=0.25
- htf: event 4h, context 1d, weights w_oi=0.50 w_volume=0.20 w_funding=0.30

Manual mode (for tuning):
- --source manual --manual-oi-change-pct ... --manual-perp-volume-change-pct ... --manual-funding-now ... --manual-funding-avg ...

API mode:
- Tries Binance futures first, then OKX fallback.
- API mode verified with full-access session (Binance live pulls working).
- If a future session is sandboxed, switch to full access or use manual mode for tuning.


## Part C Weight Tuning Grid (2026-03-07)

Result:
- Kept current profile defaults (set A) as tuned baseline:
  - ltf: w_oi=0.30, w_volume=0.50, w_funding=0.20
  - mtf: w_oi=0.40, w_volume=0.35, w_funding=0.25
  - htf: w_oi=0.50, w_volume=0.20, w_funding=0.30

Scenario-grid finding:
- All candidate sets scored similarly on core bull/bear/chop cases.
- "Overheated top risk" still reads bullish in every weight set.

Interpretation:
- This is not a weight-selection issue.
- It is a model-logic issue: need a separate crowding/overheat gate (e.g., very positive funding + strong OI/volume expansion) to downgrade state to 
eutral_watch or isk_on_but_crowded.

Action:
- Keep current weights locked.
- Add overheat gate in next Part C refinement pass (optional toggle).

## Part C Overheat Gate (Added)

Purpose:
- Detect crowded-long conditions even when derivatives score is bullish.

Behavior:
- If base derivatives_state is bullish and all thresholds are met, state is downgraded to bullish_crowded.

Default thresholds:
- oi_change_pct >= 8.0
- perp_volume_change_pct >= 18.0
- funding_z >= 2.5 where funding_z = funding_now / max(abs(funding_avg), 0.0001)

CLI controls:
- --overheat-gate on|off (default on)
- --overheat-oi-gte (default 8.0)
- --overheat-volume-gte (default 18.0)
- --overheat-funding-z-gte (default 2.5)

Outputs:
- derivatives_state can now be: bullish|bearish|neutral|bullish_crowded
- overheat_gate_triggered (bool)
- overheat_gate_reason (text)

## Part C Long/Short Block (Added)

Integration:
- Added long/short as a separate sub-component and blended into Part C score.
- Final blend rule:
  - final_score = (1 - w_ls) * base_derivatives_score + w_ls * long_short_score

Defaults:
- w_ls=0.15
- ls_enabled=on
- exchange: binance_futures

Profile timeframe sets:
- ltf: 5m,15m,30m with weights 0.45,0.35,0.20
- mtf: 30m,1h,4h with weights 0.20,0.35,0.45
- htf: 1h,4h,1d with weights 0.20,0.35,0.45

CLI controls:
- --ls-enabled on|off
- --w-ls <0..1>
- --cm-ls-exchange <exchange>
- --cm-ls-timeframes 5m,15m,30m,1h,4h,1d

Output fields:
- long_short.enabled
- long_short.used
- long_short.timeframes
- long_short.values
- long_short.score
- long_short.state
- derivatives_score_base, derivatives_state_base
- derivatives_score, derivatives_state (post LS blend + overheat gate)


## Naming Lock: MTF Consensus Toggle

Decision:
- Name for multi-timeframe switch/option: MTF Consensus Toggle
- Config/CLI alias: mtf_consensus

Scope:
- Keep initial use focused on LS block.
- Reuse this same naming for future features when applying multi-timeframe agreement logic.

## Part D: Liquidation Standalone (Built)

Script:
- C:\Users\gssjr\OneDrive\Documents\New project\part_d_liquidation.py

Inputs:
- /v1/metrics/derivatives/liquidation_entry_price_heatmap_net
- /v1/metrics/derivatives/futures_liquidated_total_volume_sum
- Optional split:
  - /v1/metrics/derivatives/futures_liquidated_volume_long_sum
  - /v1/metrics/derivatives/futures_liquidated_volume_short_sum

Output contract:
- liq_level_bias: long_side_loaded | short_side_loaded | balanced
- liq_event_state: flush_down | squeeze_up | calm | high_vol_chop
- liq_composite_state: bullish_watch | bearish_watch | high_vol_chop | neutral_watch
- liq_reason

Timeframe contract:
- heatmap_tf fixed to 1h
- event_tf configurable by profile or --event-tf (10m|1h|24h)

Coverage:
- Supported symbols for heatmap family:
  - BNB,BTC,DOGE,ETH,SOL,TON,XRP
- Unsupported symbols return explicit error.

Current runtime status:
- Manual mode validated.
- Glassnode policy is MCP-only; use MCP-fed data path for live runs.

## Part E Provider Routing (Locked)

Part E runtime is MCP/manual only with source switching:
- `--provider glassnode_mcp|manual|auto`
- `--fallback-provider none|glassnode_mcp|manual`

Part E MCP feed builder:
- `part_e_mcp_feed.py`
- Purpose: convert raw MCP metric snapshots into Part E payload contract consumed by:
  - `part_e_liq_context.py --source mcp --mcp-input-file <json>`
