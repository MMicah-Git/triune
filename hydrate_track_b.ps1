<#
hydrate_track_b.ps1 — download (hydrate) OneDrive cloud-only marked Takeoff PDFs
so bluebeam_to_yolo can mine them for Track B training labels (the miss classes).

PREREQUISITE: OneDrive must be RUNNING (the cloud file provider). If it isn't:
    Start-Process "C:\Program Files\Microsoft OneDrive\OneDrive.exe"
    (or open the OneDrive app and sign in), then wait ~30s.

Mechanism: pin each file (attrib +P -U = "Always keep on this device") -> OneDrive
downloads it. Downloads are ASYNC; re-run with -Status to watch progress.

Usage:
    powershell -ExecutionPolicy Bypass -File hydrate_track_b.ps1                 # dry-run: count + size
    powershell -ExecutionPolicy Bypass -File hydrate_track_b.ps1 -Sample 150 -Execute
    powershell -ExecutionPolicy Bypass -File hydrate_track_b.ps1 -All -Execute   # ~22 GB
    powershell -ExecutionPolicy Bypass -File hydrate_track_b.ps1 -Status
#>
param(
  [int]$Sample = 150,
  [switch]$All,
  [switch]$Execute,
  [switch]$Status,
  [string]$Root = "C:\Users\TriuneTakeoff\OneDrive - Triune Solutions LLC"
)

if (-not (Get-Process OneDrive -ErrorAction SilentlyContinue)) {
  Write-Host "WARNING: OneDrive is not running - hydration will fail." -ForegroundColor Yellow
  Write-Host '  Start it:  Start-Process "C:\Program Files\Microsoft OneDrive\OneDrive.exe"' -ForegroundColor Yellow
}

$pdfs    = Get-ChildItem -Path $Root -Recurse -Filter "Takeoff_*.pdf" -File -ErrorAction SilentlyContinue
$offline = @($pdfs | Where-Object { ($_.Attributes -band [IO.FileAttributes]::Offline) -ne 0 })
$local   = @($pdfs | Where-Object { ($_.Attributes -band [IO.FileAttributes]::Offline) -eq 0 })

if ($Status) {
  $lg = (($local | Measure-Object Length -Sum).Sum) / 1GB
  Write-Host ("marked PDFs: {0} | local/hydrated: {1} | cloud-only: {2}" -f $pdfs.Count, $local.Count, $offline.Count)
  Write-Host ("local size: {0:N1} GB" -f $lg)
  return
}

if ($All) {
  $targets = $offline
} else {
  $step = [Math]::Max(1, [int]($offline.Count / $Sample))
  $targets = @(for ($k = 0; $k -lt $offline.Count -and @($targets).Count -lt $Sample; $k += $step) { $offline[$k] })
}
$sz = (($targets | Measure-Object Length -Sum).Sum) / 1GB
Write-Host ("Target: {0} PDFs, ~{1:N1} GB to download." -f @($targets).Count, $sz)

if (-not $Execute) {
  Write-Host "DRY RUN - re-run with -Execute to pin/download. (-All for everything.)"
  return
}

$i = 0
foreach ($f in $targets) {
  & attrib.exe +P -U "$($f.FullName)" 2>$null
  $i++
  if ($i % 25 -eq 0) { Write-Host ("  pinned {0}/{1}..." -f $i, @($targets).Count) }
}
Write-Host ("Pinned {0} files for download. OneDrive is downloading in the background." -f $i)
Write-Host "Re-run with -Status to watch progress; mine once local (python build_track_b.py)."
