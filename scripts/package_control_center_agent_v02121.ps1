$ErrorActionPreference = "Stop"

$root = (Resolve-Path (Join-Path $PSScriptRoot "..\")).Path
$version = "0.21.21"
$release = Join-Path $root "release\Laogu-0.21.21-ready"
$payload = Join-Path $release "Laogu"
$checks = Join-Path $release "02-checksums"
$docs = Join-Path $release "03-build-record"
$zip = Join-Path $release "Laogu-0.21.21-ready.zip"
$dist = Join-Path $root "dist\Laogu-Desktop"
$previousLocation = Get-Location
Set-Location -LiteralPath $root
$gitCommit = (git rev-parse --short HEAD).Trim()
$sourceState = if (git status --porcelain) { "modified working tree" } else { "clean working tree" }
Set-Location -LiteralPath $previousLocation
if ($LASTEXITCODE -ne 0 -or -not $gitCommit) {
    throw "Unable to read the current Git commit."
}

$releaseAbsolute = [System.IO.Path]::GetFullPath($release)
$releaseRoot = [System.IO.Path]::GetFullPath((Join-Path $root "release"))
if (-not $releaseAbsolute.StartsWith($releaseRoot.TrimEnd('\') + '\', [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing to package outside the release directory: $releaseAbsolute"
}
if (-not (Test-Path -LiteralPath (Join-Path $dist "Laogu-Desktop.exe") -PathType Leaf)) {
    throw "Portable build not found. Run packaging/windows/build_portable.ps1 -Clean first."
}
if (-not (Test-Path -LiteralPath (Join-Path $dist "_internal") -PathType Container)) {
    throw "PyInstaller runtime directory is missing."
}

if (Test-Path -LiteralPath $release) {
    Remove-Item -LiteralPath $release -Recurse -Force
}
New-Item -ItemType Directory -Force -Path @(
    $payload,
    $checks,
    $docs,
    (Join-Path $payload "agent_data"),
    (Join-Path $payload "logs"),
    (Join-Path $payload "config")
) | Out-Null

Copy-Item -LiteralPath (Join-Path $dist "Laogu-Desktop.exe") -Destination $payload -Force
Copy-Item -LiteralPath (Join-Path $dist "_internal") -Destination $payload -Recurse -Force
Copy-Item -LiteralPath (Join-Path $root "packaging\windows\laogu.env.example") -Destination (Join-Path $payload "config\laogu.env.example") -Force

foreach ($secret in @("credentials.json", "offline_access.json", "laogu.env")) {
    Get-ChildItem -LiteralPath $payload -Recurse -Force -File -Filter $secret -ErrorAction SilentlyContinue |
        Remove-Item -Force
}

@(
    "Laogu Control Center + Windows Agent - v$version"
    "================================================"
    ""
    "Start Laogu Browser, then double-click Laogu-Desktop.exe."
    "The control center manages the embedded Windows Agent automatically."
    "No separate PowerShell window or agent.service_main command is required."
    ""
    "Run modes: smart time windows, immediate execution, one-time schedule and daily schedule."
    "Scheduled tasks start the browser only when due and use Asia/Shanghai time."
    "Profile startup is serialized; ready Profiles can run concurrently."
    ""
    "Create config\laogu.env from config\laogu.env.example on the target PC."
    "Keep the extracted application path reasonably short; the packaged folder is named Laogu for this reason."
    "Excluded: credentials, Agent Tokens, cookies, login state, proxy credentials, databases and logs."
) | Set-Content -LiteralPath (Join-Path $payload "README.txt") -Encoding UTF8

@(
    "Build time: $((Get-Date).ToString('yyyy-MM-dd HH:mm:ss zzz'))"
    "Version: $version"
    "Git commit: $gitCommit ($sourceState)"
    "Artifact: dist\Laogu-Desktop\Laogu-Desktop.exe"
    "Package: desktop control center + embedded Agent + bundled automation engine"
    "Changes: smart/immediate/scheduled modes; persistent restore; live scheduler logs; isolated Qt DLL loading; incompatible bundled ICU removed"
    "Startup: serialized Profile creation and CDP readiness; different ready Profiles remain concurrent"
    "Verification: 63 scheduling/startup/controller regression tests passed; py_compile and diff checks passed"
    "Security: credentials, tokens, personal config, cookies, databases and logs excluded"
) | Set-Content -LiteralPath (Join-Path $docs "BUILD-RECORD.txt") -Encoding UTF8

@(
    "Package scope:"
    "- Laogu-Desktop.exe and PyInstaller runtime."
    "- Embedded Agent and bundled automation modules."
    "- No standalone Agent executable is produced."
    ""
    "Upgrade:"
    "- Replace Laogu-Desktop.exe and _internal."
    "- Preserve config, agent_data and logs."
    ""
    "Prerequisite:"
    "- Laogu Browser local API must be available."
) | Set-Content -LiteralPath (Join-Path $docs "PACKAGE-SCOPE.txt") -Encoding UTF8

$hashLines = foreach ($file in (Get-ChildItem -LiteralPath $payload -Recurse -File | Sort-Object FullName)) {
    $relative = $file.FullName.Substring($release.Length).TrimStart([char[]]"/\").Replace("\", "/")
    $hash = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    "$hash  $relative"
}
$hashLines | Set-Content -LiteralPath (Join-Path $checks "SHA256SUMS.txt") -Encoding ASCII

Compress-Archive -LiteralPath $payload -DestinationPath $zip -CompressionLevel Optimal
$zipHash = (Get-FileHash -LiteralPath $zip -Algorithm SHA256).Hash.ToLowerInvariant()
"$zipHash  $($zip.Substring($release.Length).TrimStart([char[]]"/\").Replace("\", "/"))" |
    Set-Content -LiteralPath (Join-Path $checks "PACKAGE-SHA256SUMS.txt") -Encoding ASCII

Write-Host "CONTROL_CENTER_AGENT_PACKAGE_OK=$release"
Write-Host "ZIP=$zip"
Write-Host "SHA256=$zipHash"
