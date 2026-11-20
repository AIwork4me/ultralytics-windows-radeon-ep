# Experiment B4 - read-only MSIX / Store / deployment-event collection.
# Emits one JSON file per section under reports/experiment_b/_raw/. No writes
# outside reports/, no package install/remove, no service or policy change.
$ErrorActionPreference = 'Continue'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$root = Join-Path (Get-Location) 'reports\experiment_b\_raw'
New-Item -ItemType Directory -Force -Path $root | Out-Null

function Write-Json($obj, $name) {
  $path = Join-Path $root $name
  $obj | ConvertTo-Json -Depth 8 | Set-Content -Encoding UTF8 $path
  "wrote $name (" + (Get-Item $path).Length + " bytes)"
}

# --- 1. deployment events mentioning the EP / WinML / workload ---------------
$evFilter = 'WinML|WindowsWorkload|VitisAI|AMD\.GPU\.EP|AMDGPU|MachineLearning'
$appxEvents = @()
foreach ($log in 'Microsoft-Windows-AppXDeploymentServer/Operational', 'Microsoft-Windows-AppXDeployment/Operational') {
  try {
    $raw = Get-WinEvent -LogName $log -MaxEvents 7000 -ErrorAction Stop
    $hits = $raw | Where-Object { $_.Message -match $evFilter }
    foreach ($h in $hits) {
      $appxEvents += [pscustomobject]@{
        log = $log
        time = $h.TimeCreated.ToString('o')
        id = $h.Id
        level = $h.LevelDisplayName
        message = ($h.Message -replace "\s+", ' ').Substring(0, [Math]::Min(400, ($h.Message -replace "\s+", ' ').Length))
      }
    }
  } catch { $appxEvents += [pscustomobject]@{ log = $log; error = $_.Exception.Message } }
}
Write-Json ([pscustomobject]@{ captured = (Get-Date).ToString('o'); filter = $evFilter; total_logs_scanned = 2; events = $appxEvents }) 'b4_appx_events.json'

# --- 2. Store operational log: errors only ----------------------------------
$storeEvents = @()
try {
  $raw = Get-WinEvent -LogName 'Microsoft-Windows-Store/Operational' -MaxEvents 4000 -ErrorAction Stop
  $bad = $raw | Where-Object { $_.LevelDisplayName -match 'Error|错误|Warning|警告' -or $_.Message -match 'AMD\.GPU|WinML|WindowsWorkload' }
  foreach ($h in ($bad | Select-Object -First 120)) {
    $storeEvents += [pscustomobject]@{
      time = $h.TimeCreated.ToString('o'); id = $h.Id; level = $h.LevelDisplayName
      message = (($h.Message -replace "\s+", ' '))
    }
  }
  $storeTotal = $raw.Count
} catch { $storeEvents += [pscustomobject]@{ error = $_.Exception.Message }; $storeTotal = $null }
Write-Json ([pscustomobject]@{ captured = (Get-Date).ToString('o'); scanned = $storeTotal; kept = $storeEvents.Count; events = $storeEvents }) 'b4_store_events.json'

# --- 3. WinML shared runtime + EP framework package inventory ---------------
$want = 'WinML|WindowsWorkload|VitisAI|MachineLearning|AMD'
$pkgs = Get-AppxPackage | Where-Object { $_.Name -match $want } |
  Select-Object Name, Version, PackageFullName, PublisherId, SignatureKind, Status, IsFramework, IsResourcePackage
Write-Json ([pscustomobject]@{ captured = (Get-Date).ToString('o'); packages = @($pkgs) }) 'b4_winml_packages.json'

# --- 4. Windows Update search (no install, no download) ---------------------
$wu = [pscustomobject]@{ searched = $false }
try {
  $session = New-Object -ComObject Microsoft.Update.Session
  $searcher = $session.CreateUpdateSearcher()
  $searcher.Online = $true
  $result = $searcher.Search("IsInstalled=0 and IsHidden=0")
  $upd = @()
  foreach ($u in $result.Updates) {
    $upd += [pscustomobject]@{
      title = $u.Title
      is_driver = ($u.BrowseOnly -ne $null -and $u.Type -eq 2)
      type = $u.Type
      driver_class = $u.DriverClass
      driver_hardware_id = @($u.DriverHardwareID)
      kb = @($u.KBArticleIDs)
      is_hidden = $u.IsHidden
      reboot_required = $u.RebootRequired
    }
  }
  $wu = [pscustomobject]@{
    searched = $true
    online = $true
    result_code = $result.ResultCode
    update_count = $upd.Count
    updates = $upd
  }
} catch { $wu = [pscustomobject]@{ searched = $false; error = $_.Exception.Message } }
Write-Json ([pscustomobject]@{ captured = (Get-Date).ToString('o'); windows_update = $wu }) 'b4_wu_search.json'

# --- 5. Store / EP delivery connectivity (HEAD requests only) --------------
$targets = @(
  'https://storeedgefd.dsx.mp.microsoft.com/v9.0/',
  'https://displaycatalog.mp.microsoft.com/v7.0/products',
  'https://fe3.delivery.mp.microsoft.com/ClientWebService/client.asmx',
  'https://login.live.com/'
)
$conn = @()
foreach ($t in $targets) {
  try {
    $r = Invoke-WebRequest -Uri $t -Method Head -TimeoutSec 20 -UseBasicParsing -ErrorAction Stop
    $conn += [pscustomobject]@{ url = $t; ok = $true; status = $r.StatusCode }
  } catch {
    $code = $null
    if ($_.Exception.Response) { $code = [int]$_.Exception.Response.StatusCode }
    $conn += [pscustomobject]@{ url = $t; ok = $false; status = $code; error = ($_.Exception.Message -replace "\s+", ' ').Substring(0, [Math]::Min(200, $_.Exception.Message.Length)) }
  }
}
$proxy = Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings' -ErrorAction SilentlyContinue |
  Select-Object ProxyEnable, ProxyServer, AutoConfigURL
Write-Json ([pscustomobject]@{ captured = (Get-Date).ToString('o'); endpoints = $conn; proxy = $proxy }) 'b4_connectivity.json'

# --- 6. Servicing / servicing-stack state ----------------------------------
$svc = Get-Service AppXSvc, ClipSVC, InstallService, wuauserv, DoSvc, LicenseManager, StorSvc, StateRepository -ErrorAction SilentlyContinue |
  Select-Object Name, Status, StartType
Write-Json ([pscustomobject]@{ captured = (Get-Date).ToString('o'); services = @($svc) }) 'b4_services.json'
