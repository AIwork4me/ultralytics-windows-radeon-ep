# Experiment B4 - read-only AMD display driver package metadata (strings only).
$ErrorActionPreference = 'Continue'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$root = Join-Path (Get-Location) 'reports\experiment_b\_raw'

$infPath = 'C:\Windows\INF\oem21.inf'
$inf = [ordered]@{
  path = 'C:/Windows/INF/oem21.inf'
  exists = (Test-Path $infPath)
  driver_ver_line = $null
  banner_comments = @()
  lines_read = 0
}
if ($inf.exists) {
  $lines = Get-Content $infPath -TotalCount 25
  $inf.lines_read = @($lines).Count
  foreach ($ln in $lines) {
    $s = [string]$ln
    if ($s.StartsWith('DriverVer=')) { $inf.driver_ver_line = $s }
    if ($s.StartsWith(';----------')) { $inf.banner_comments += $s }
  }
}

$o = [string](pnputil /enum-drivers 2>&1 | Out-String)
$records = @()
$cur = $null
foreach ($raw in ($o -split "\r?\n")) {
  $t = ([string]$raw).Trim()
  if ($t -eq "") {
    if ($cur -ne $null -and $cur.Count -gt 0) { $records += ,$cur }
    $cur = [ordered]@{}
    continue
  }
  if ($t -match "^([A-Za-z ]+?):\s+(.+)$") {
    if ($cur -eq $null) { $cur = [ordered]@{} }
    $cur[$matches[1].Trim()] = $matches[2].Trim()
  }
}
if ($cur -ne $null -and $cur.Count -gt 0) { $records += ,$cur }
$amdRecords = @()
foreach ($r in $records) {
  if (([string]$r["Provider Name"]) -match "Advanced Micro Devices" -or ([string]$r["Driver Version"]) -match "32\.0\.22018\.5") {
    $amdRecords += $r
  }
}

$vc = @()
foreach ($v in (Get-CimInstance Win32_VideoController | Where-Object { ([string]$_.PNPDeviceID) -match 'VEN_1002' })) {
  $vc += [ordered]@{
    Name = [string]$v.Name
    DriverVersion = [string]$v.DriverVersion
    DriverDate = [string]$v.DriverDate
    InfFilename = [string]$v.InfFilename
    PNPDeviceID = ([string]$v.PNPDeviceID) -replace '\\[0-9A-Fa-f&]+$', ''
    Status = [string]$v.Status
    AdapterRAM = [string]$v.AdapterRAM
  }
}

$out = [ordered]@{
  captured = (Get-Date).ToString('o')
  engine = $PSVersionTable.PSVersion.ToString()
  inf = $inf
  pnputil_amd_records = $amdRecords
  pnputil_record_count = $records.Count
  video_controller = $vc
  driver_store_packages = @(Get-ChildItem 'C:\Windows\System32\DriverStore\FileRepository' -Directory -ErrorAction SilentlyContinue |
      Where-Object { ([string]$_.Name).StartsWith('u0') } | Select-Object -First 10 | ForEach-Object { [string]$_.Name })
  os = [ordered]@{
    caption = [string](Get-CimInstance Win32_OperatingSystem).Caption
    version = [string](Get-CimInstance Win32_OperatingSystem).Version
    build = [string](Get-CimInstance Win32_OperatingSystem).BuildNumber
  }
}
$json = $out | ConvertTo-Json -Depth 4
Set-Content -Encoding UTF8 -Path (Join-Path $root 'b4_driver_inf.json') -Value $json
"driver raw written; bytes=" + (Get-Item (Join-Path $root 'b4_driver_inf.json')).Length
