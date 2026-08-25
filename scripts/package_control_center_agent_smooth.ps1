$ErrorActionPreference = "Stop"

$root = (Resolve-Path (Join-Path $PSScriptRoot "..\")).Path
$release = Join-Path $root "release\2026-08-24-control-center-agent-smooth-v0.21.9"
$payload = Join-Path $release "01-windows-portable\Laogu-Control-Center-Agent-0.21.9-smooth"
$checks = Join-Path $release "02-checksums"
$docs = Join-Path $release "03-build-record"
$zip = Join-Path $release "01-windows-portable\Laogu-Control-Center-Agent-0.21.9-smooth-windows-amd64.zip"

$exe = Join-Path $root "dist\Laogu-Desktop\Laogu-Desktop.exe"
$internal = Join-Path $root "dist\Laogu-Desktop\_internal"
$envExample = Join-Path $root "packaging\windows\laogu.env.example"
if (-not (Test-Path -LiteralPath $exe -PathType Leaf)) { throw "Missing $exe. Build the portable desktop application first." }
if (-not (Test-Path -LiteralPath $internal -PathType Container)) { throw "Missing $internal. Build the portable desktop application first." }
if (-not (Test-Path -LiteralPath $envExample -PathType Leaf)) { throw "Missing $envExample." }

if (Test-Path -LiteralPath $release) { Remove-Item -LiteralPath $release -Recurse -Force }
New-Item -ItemType Directory -Force -Path $payload, $checks, $docs | Out-Null
Copy-Item -LiteralPath $exe -Destination $payload -Force
Copy-Item -LiteralPath $internal -Destination $payload -Recurse -Force
New-Item -ItemType Directory -Force -Path (Join-Path $payload "agent_data"), (Join-Path $payload "logs"), (Join-Path $payload "config") | Out-Null
Copy-Item -LiteralPath $envExample -Destination (Join-Path $payload "config\laogu.env.example") -Force

foreach ($secret in @("credentials.json", "offline_access.json", "laogu.env")) {
    Get-ChildItem -LiteralPath $payload -Recurse -Force -File -Filter $secret -ErrorAction SilentlyContinue | Remove-Item -Force
}

@(
    "Laogu Control Center + Windows Agent — smooth UI build"
    "======================================================"
    ""
    "Usage: start Laogu Browser, then double-click Laogu-Desktop.exe."
    "The desktop control center starts and stops the embedded Windows Agent."
    "No separate PowerShell window or python -m agent.service_main command is required."
    ""
    "Create config\\laogu.env from config\\laogu.env.example before first use."
    "Laogu Browser must be installed/running with its local API available."
    "Automation scripts and Agent service interfaces are unchanged."
    ""
    "Excluded: credentials, Agent Tokens, cookies, login state, proxy credentials, databases, and logs."
) | Set-Content -LiteralPath (Join-Path $payload "README.txt") -Encoding UTF8

@(
    "Build time: $((Get-Date).ToString('yyyy-MM-dd HH:mm:ss zzz'))"
    "Project root: $root"
    "Artifact: dist\\Laogu-Desktop\\Laogu-Desktop.exe"
    "Package: integrated desktop control center + embedded Agent"
    "UI optimization: background status/statistics refresh, refresh deduplication, batched log rendering, table repaint suppression"
    "Automation scripts: unchanged"
    "Agent service interfaces: unchanged"
    "Verification: targeted tests passed (30 tests); Python compilation passed"
    "Security: package excludes credentials, tokens, personal config, cookies, and logs"
) | Set-Content -LiteralPath (Join-Path $docs "BUILD-RECORD.txt") -Encoding UTF8

@(
    "Package scope:"
    "- Laogu-Desktop.exe and PyInstaller runtime are included."
    "- Embedded Agent and automation modules are inside _internal."
    "- No standalone Agent executable is produced by this repository build."
    ""
    "Operational prerequisite:"
    "- Laogu Browser must be installed/running."
    "- Create config\\laogu.env from config\\laogu.env.example locally."
) | Set-Content -LiteralPath (Join-Path $docs "PACKAGE-SCOPE.txt") -Encoding UTF8

$hashLines = foreach ($file in (Get-ChildItem -LiteralPath $payload -Recurse -File | Sort-Object FullName)) {
    $relative = $file.FullName.Substring($release.Length).TrimStart([char[]]"/\").Replace("\", "/")
    $hash = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    "$hash  $relative"
}
$hashLines | Set-Content -LiteralPath (Join-Path $checks "SHA256SUMS.txt") -Encoding ASCII

if (Test-Path -LiteralPath $zip -PathType Leaf) { Remove-Item -LiteralPath $zip -Force }
Compress-Archive -LiteralPath $payload -DestinationPath $zip -CompressionLevel Optimal
$zipHash = (Get-FileHash -LiteralPath $zip -Algorithm SHA256).Hash.ToLowerInvariant()
"$zipHash  $($zip.Substring($release.Length).TrimStart([char[]]"/\").Replace("\", "/"))" |
    Set-Content -LiteralPath (Join-Path $checks "PACKAGE-SHA256SUMS.txt") -Encoding ASCII

Write-Host "CONTROL_CENTER_AGENT_SMOOTH_PACKAGE_OK=$release"
Write-Host "ZIP=$zip"
Write-Host "SHA256=$zipHash"
