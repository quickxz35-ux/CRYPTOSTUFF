param(
  [string]$Python = "python"
)

$ErrorActionPreference = "Stop"
$root = "C:\Users\gssjr\OneDrive\Documents\New project"

function Run-And-CheckJson {
  param(
    [string]$Name,
    [string]$Command,
    [string[]]$RequiredTop,
    [string[]]$RequiredResult
  )

  Write-Host "[RUN] $Name" -ForegroundColor Cyan
  $jsonText = Invoke-Expression $Command | Out-String
  $obj = $jsonText | ConvertFrom-Json

  foreach ($k in $RequiredTop) {
    if (-not ($obj.PSObject.Properties.Name -contains $k)) {
      throw "$Name missing top-level key: $k"
    }
  }

  if ($RequiredResult.Count -gt 0) {
    if (-not $obj.results -or $obj.results.Count -lt 1) {
      throw "$Name missing results[]"
    }
    $first = $obj.results[0]
    foreach ($k in $RequiredResult) {
      if (-not ($first.PSObject.Properties.Name -contains $k)) {
        throw "$Name missing results[0] key: $k"
      }
    }
  }

  Write-Host "[OK ] $Name" -ForegroundColor Green
}

# Part B
$cmdB = "$Python `"$root\liquidity_module.py`" --profile mtf --source manual --assets USDT,USDC,BTC --manual-inflow-usd 46465028.44 --manual-outflow-usd 38456160.82 --manual-netflow-usd 8008867.62 --manual-netflow-context-abs-avg 10329706.52 --manual-exchange-balance-regime MILD_BULLISH --manual-whale-to-exchange-usd 5.2482 --format json"
Run-And-CheckJson -Name "Part B Liquidity" -Command $cmdB -RequiredTop @("part","profile","metrics","liquidity_score","liquidity_state") -RequiredResult @()

# Part C
$cmdC = "$Python `"$root\part_c_derivatives.py`" --symbols BTC --profile mtf --source manual --manual-oi-change-pct 6 --manual-perp-volume-change-pct 14 --manual-funding-now 0.00008 --manual-funding-avg 0.00005 --format json"
Run-And-CheckJson -Name "Part C Derivatives" -Command $cmdC -RequiredTop @("part","profile","results") -RequiredResult @("symbol","derivatives_score_base","derivatives_state_base","derivatives_score","derivatives_state","long_short","overheat_gate_triggered")

# Part D
$cmdD = "$Python `"$root\part_d_liquidation.py`" --symbols BTC --profile mtf --source manual --manual-heatmap-net 0.12 --manual-liq-total-now 90 --manual-liq-total-avg 35 --manual-liq-long-now 62 --manual-liq-short-now 28 --format json"
Run-And-CheckJson -Name "Part D Liquidation" -Command $cmdD -RequiredTop @("part","profile","results") -RequiredResult @("symbol","liq_level_bias","liq_event_state","liq_composite_state","liq_reason")

Write-Host "All smoke tests passed." -ForegroundColor Yellow
