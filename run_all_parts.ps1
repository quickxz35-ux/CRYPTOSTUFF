param(
  [Parameter(Mandatory = $true)][string]$Symbols,
  [ValidateSet("ltf","mtf","htf")][string]$Profile = "mtf",
  [string]$Assets = "USDT,USDC",
  [string]$Python = "python",
  [string]$OutputRoot = ".\\runs",
  [string]$ProfilesFile = ".\\timeframe_profiles.json",
  [string]$SourceA = "",
  [string]$SourceB = "",
  [string]$SourceC = "",
  [string]$SourceD = "",
  [string]$SourceE = "",
  [string]$PartEMcpInputFile = ""
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

function Invoke-JsonCommand {
  param(
    [string]$Name,
    [string]$Command,
    [string]$OutFile
  )
  Write-Host "[RUN] $Name" -ForegroundColor Cyan
  $text = Invoke-Expression $Command | Out-String
  $text | Set-Content -Encoding UTF8 $OutFile
  try {
    return $text | ConvertFrom-Json
  } catch {
    throw "$Name returned non-JSON output. Check $OutFile"
  }
}

function Get-NowStamp {
  return (Get-Date).ToString("yyyyMMdd_HHmmss")
}

function Get-BinancePrice {
  param([string]$Symbol)
  try {
    $uri = "https://api.binance.com/api/v3/ticker/price?symbol=$($Symbol)USDT"
    $obj = Invoke-RestMethod -Uri $uri -Method Get -TimeoutSec 20
    return [double]$obj.price
  } catch {
    return 0.0
  }
}

# Load timeframe/source defaults.
if (-not (Test-Path $ProfilesFile)) {
  throw "Profiles file not found: $ProfilesFile"
}
$profiles = Get-Content $ProfilesFile | ConvertFrom-Json
$profCfg = $profiles.$Profile
if (-not $profCfg) { throw "Profile not found in ${ProfilesFile}: $Profile" }

$pp = $profCfg.part_profiles
$sd = $profCfg.source_defaults

$partAProfile = [string]$pp.part_a
$partBProfile = [string]$pp.part_b
$partCProfile = [string]$pp.part_c
$partDProfile = [string]$pp.part_d
$partEProfile = [string]$pp.part_e
$structureProfile = [string]$pp.structure
$momentumProfile = [string]$pp.momentum

$srcA = if ($SourceA) { $SourceA } else { [string]$sd.part_a }
$srcB = if ($SourceB) { $SourceB } else { [string]$sd.part_b }
$srcC = if ($SourceC) { $SourceC } else { [string]$sd.part_c }
$srcD = if ($SourceD) { $SourceD } else { [string]$sd.part_d }
$srcE = if ($SourceE) { $SourceE } else { [string]$sd.part_e }

$stamp = Get-NowStamp
$runDir = Join-Path $OutputRoot "${stamp}_${Profile}"
New-Item -ItemType Directory -Force -Path $runDir | Out-Null

$symbolsUpper = ($Symbols -split "," | ForEach-Object { $_.Trim().ToUpper() } | Where-Object { $_ }) -join ","
$firstSymbol = ($symbolsUpper -split ",")[0]
$manualPrice = Get-BinancePrice -Symbol $firstSymbol
if ($manualPrice -le 0) { $manualPrice = 100.0 }

$up1 = [math]::Round($manualPrice * 1.01, 6)
$up2 = [math]::Round($manualPrice * 1.02, 6)
$dn1 = [math]::Round($manualPrice * 0.99, 6)
$dn2 = [math]::Round($manualPrice * 0.98, 6)
$manualUpLevels = ("{0}:1.00,{1}:0.80" -f $up1, $up2)
$manualDownLevels = ("{0}:0.95,{1}:0.75" -f $dn1, $dn2)

$cmdA = "$Python `"$root\oi_combined_view.py`" --symbols $symbolsUpper --profile $partAProfile --source $srcA --format json"
if ($srcA -eq "mcp" -and $PartEMcpInputFile) { $cmdA += " --mcp-input-file `"$PartEMcpInputFile`"" }
if ($srcA -eq "manual") { $cmdA += " --manual-oi-notional-usd 1000000 --manual-oi-change-usd 25000" }
$aObj = Invoke-JsonCommand -Name "Part A" -Command $cmdA -OutFile (Join-Path $runDir "part_a.json")

$cmdB = "$Python `"$root\liquidity_module.py`" --profile $partBProfile --assets $Assets --source $srcB --format json"
if ($srcB -eq "mcp" -and $PartEMcpInputFile) { $cmdB += " --mcp-input-file `"$PartEMcpInputFile`"" }
if ($srcB -eq "manual") {
  $cmdB += " --manual-inflow-usd 46470000 --manual-outflow-usd 38460000 --manual-netflow-usd 8010000 --manual-netflow-context-abs-avg 12000000 --manual-exchange-balance-regime MILD_BULLISH --manual-whale-to-exchange-usd 2100000"
}
$bObj = Invoke-JsonCommand -Name "Part B" -Command $cmdB -OutFile (Join-Path $runDir "part_b.json")

$cmdC = "$Python `"$root\part_c_derivatives.py`" --symbols $symbolsUpper --profile $partCProfile --source $srcC --format json"
if ($srcC -eq "manual") { $cmdC += " --manual-oi-change-pct 3 --manual-perp-volume-change-pct 5 --manual-funding-now 0.0001 --manual-funding-avg 0.00005" }
$cObj = Invoke-JsonCommand -Name "Part C" -Command $cmdC -OutFile (Join-Path $runDir "part_c.json")

$cmdD = "$Python `"$root\part_d_liquidation.py`" --symbols $symbolsUpper --profile $partDProfile --source $srcD --format json"
if ($srcD -eq "mcp" -and $PartEMcpInputFile) { $cmdD += " --mcp-input-file `"$PartEMcpInputFile`"" }
if ($srcD -eq "manual") { $cmdD += " --manual-heatmap-net 0.12 --manual-liq-total-now 90 --manual-liq-total-avg 35 --manual-liq-long-now 62 --manual-liq-short-now 28" }
$dObj = Invoke-JsonCommand -Name "Part D" -Command $cmdD -OutFile (Join-Path $runDir "part_d.json")

$cmdE = "$Python `"$root\part_e_liq_context.py`" --symbols $symbolsUpper --profile $partEProfile --source $srcE --fallback-source none --format json"
if ($srcE -in @("mcp","glassnode_mcp","auto")) {
  if ($PartEMcpInputFile) { $cmdE += " --mcp-input-file `"$PartEMcpInputFile`"" }
}
if ($srcE -eq "manual") {
  $cmdE += " --manual-price-now $manualPrice --manual-up-levels `"$manualUpLevels`" --manual-down-levels `"$manualDownLevels`" --manual-entry-net 0.05 --manual-liq-long-now 8000000 --manual-liq-short-now 5000000"
}
$eObj = Invoke-JsonCommand -Name "Part E" -Command $cmdE -OutFile (Join-Path $runDir "part_e.json")

$cmdS = "$Python `"$root\structure_module.py`" --symbols $symbolsUpper --profile $structureProfile --format json"
$sObj = Invoke-JsonCommand -Name "Structure" -Command $cmdS -OutFile (Join-Path $runDir "structure.json")

$cmdM = "$Python `"$root\momentum_module.py`" --symbols $symbolsUpper --profile $momentumProfile --format json"
$mObj = Invoke-JsonCommand -Name "Momentum" -Command $cmdM -OutFile (Join-Path $runDir "momentum.json")

$summary = [ordered]@{
  run_dir = $runDir
  profile = $Profile
  symbols = $symbolsUpper -split ","
  sources = @{
    part_a = $srcA
    part_b = $srcB
    part_c = $srcC
    part_d = $srcD
    part_e = $srcE
  }
  part_b = @{
    state = $bObj.liquidity_state
    score = $bObj.liquidity_score
    reason = $bObj.liquidity_reason
  }
  part_c = @{}
  part_d = @{}
  part_e = @{}
  structure = @{}
  momentum = @{}
}

foreach ($r in $cObj.results) { $summary.part_c[$r.symbol] = @{state=$r.derivatives_state; score=$r.derivatives_score; reason=$r.derivatives_reason} }
foreach ($r in $dObj.results) {
  if ($r.error) { $summary.part_d[$r.symbol] = @{state="error"; reason=$r.error} }
  else { $summary.part_d[$r.symbol] = @{state=$r.liq_composite_state; reason=$r.liq_reason} }
}
foreach ($r in $eObj.results) {
  if ($r.error) { $summary.part_e[$r.symbol] = @{mode="error"; sweep="n/a"; reason=$r.error} }
  else { $summary.part_e[$r.symbol] = @{mode=$r.entry_mode; sweep=$r.sweep_risk_state; reason=$r.part_e_reason} }
}
foreach ($r in $sObj.results) {
  if ($r.error) { $summary.structure[$r.symbol] = @{bias="error"; reason=$r.error} }
  else { $summary.structure[$r.symbol] = @{bias=$r.bias; state=$r.structure_state; reason=$r.structure_reason} }
}
foreach ($r in $mObj.results) {
  if ($r.error) { $summary.momentum[$r.symbol] = @{bias="error"; reason=$r.error} }
  else { $summary.momentum[$r.symbol] = @{bias=$r.momentum_bias; state=$r.momentum_state; score=$r.momentum_score; reason=$r.momentum_reason} }
}

$summaryPath = Join-Path $runDir "summary.json"
$summary | ConvertTo-Json -Depth 8 | Set-Content -Encoding UTF8 $summaryPath

Write-Host ""
Write-Host "Run complete: $runDir" -ForegroundColor Green
Write-Host "Summary: $summaryPath" -ForegroundColor Green
Write-Output ($summary | ConvertTo-Json -Depth 8)
