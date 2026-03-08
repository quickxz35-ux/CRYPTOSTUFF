param(
  [Parameter(Mandatory = $true)][string]$Symbols,
  [ValidateSet("ltf","mtf","htf")][string]$Profile = "mtf",
  [string]$Python = "python",
  [string]$OutputRoot = ".\\runs",
  [string]$ProfilesFile = ".\\timeframe_profiles.json",
  [string]$PartEMcpInputFile = ""
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$runner = Join-Path $root "run_all_parts.ps1"
$runJsonText = if ($PartEMcpInputFile) {
  & $runner -Symbols $Symbols -Profile $Profile -Python $Python -OutputRoot $OutputRoot -ProfilesFile $ProfilesFile -PartEMcpInputFile $PartEMcpInputFile | Out-String
} else {
  & $runner -Symbols $Symbols -Profile $Profile -Python $Python -OutputRoot $OutputRoot -ProfilesFile $ProfilesFile | Out-String
}
$run = $runJsonText | ConvertFrom-Json
$symbolsList = @($run.symbols)

Write-Host ""
Write-Host "Pre-Trade Snapshot ($Profile)" -ForegroundColor Yellow
Write-Host "Run Dir: $($run.run_dir)"
Write-Host ""
Write-Host ("{0,-10} {1,-10} {2,-12} {3,-14} {4,-14} {5,-12}" -f "SYMBOL","PART C","PART D","PART E","STRUCTURE","MOMENTUM")

foreach ($s in $symbolsList) {
  $c = $run.part_c.$s
  $d = $run.part_d.$s
  $e = $run.part_e.$s
  $st = $run.structure.$s
  $m = $run.momentum.$s

  $cState = if ($c) { [string]$c.state } else { "n/a" }
  $dState = if ($d) { [string]$d.state } else { "n/a" }
  $eMode = if ($e) { [string]$e.mode } else { "n/a" }
  $sBias = if ($st) { [string]$st.bias } else { "n/a" }
  $mBias = if ($m) { [string]$m.bias } else { "n/a" }

  Write-Host ("{0,-10} {1,-10} {2,-12} {3,-14} {4,-14} {5,-12}" -f $s, $cState, $dState, $eMode, $sBias, $mBias)
}

Write-Host ""
Write-Host ("Part B (Liquidity): {0} score={1}" -f $run.part_b.state, [math]::Round([double]$run.part_b.score, 3))
Write-Host "Summary JSON saved inside run directory."
