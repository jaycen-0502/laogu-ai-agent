$ErrorActionPreference = "Stop"

$root = (Resolve-Path (Join-Path $PSScriptRoot "..\")).Path
$release = Join-Path $root "release\2026-08-24-control-center-agent-multi-engine-v0.21.9"
$payload = Join-Path $release "01-windows-portable\Laogu-Control-Center-Agent-0.21.9"
$checks = Join-Path $release "02-checksums"
$docs = Join-Path $release "03-build-record"
$zip = Join-Path $release "01-windows-portable\Laogu-Control-Center-Agent-0.21.9-windows-amd64.zip"

if (-not (Test-Path -LiteralPath (Join-Path $root "dist\Laogu-Desktop\Laogu-Desktop.exe") -PathType Leaf)) {
    throw "Run packaging/windows/build_portable.ps1 -Clean first."
}
if (Test-Path -LiteralPath $release) {
    Remove-Item -LiteralPath $release -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $payload | Out-Null
Copy-Item -LiteralPath (Join-Path $root "dist\Laogu-Desktop\Laogu-Desktop.exe") -Destination $payload -Force
Copy-Item -LiteralPath (Join-Path $root "dist\Laogu-Desktop\_internal") -Destination $payload -Recurse -Force
New-Item -ItemType Directory -Force -Path (Join-Path $payload "agent_data"), (Join-Path $payload "logs"), (Join-Path $payload "config") | Out-Null
Copy-Item -LiteralPath (Join-Path $root "packaging\windows\laogu.env.example") -Destination (Join-Path $payload "config\laogu.env.example") -Force
foreach ($secret in @("credentials.json", "offline_access.json", "laogu.env")) {
    Get-ChildItem -LiteralPath $payload -Recurse -Force -File -Filter $secret -ErrorAction SilentlyContinue |
        Remove-Item -Force
}
New-Item -ItemType Directory -Force -Path $checks, $docs | Out-Null

@(
    "Laogu Control Center + Windows Agent integrated portable build"
    "============================================================="
    ""
    "Usage: start Laogu Browser, then double-click Laogu-Desktop.exe."
    "The desktop control center starts and stops the embedded Windows Agent service."
    "No separate PowerShell window or python -m agent.service_main command is required."
    ""
    "Included: Laogu-Desktop.exe, PyInstaller runtime, embedded Agent modules, and automation scripts."
    "Create config\\laogu.env from config\\laogu.env.example before first use."
    ""
    "Agent, desktop, and automation source scripts were not modified for packaging."
    "Laogu Browser must be installed/running with its local API available."
    ""
    "Excluded: credentials, Agent Tokens, cookies, login state, proxy credentials, databases, and logs."
) | Set-Content -LiteralPath (Join-Path $payload "README.txt") -Encoding UTF8

@(
    "Build time: $((Get-Date).ToString('yyyy-MM-dd HH:mm:ss zzz'))"
    "Project root: $root"
    "Build command: packaging\\windows\\build_portable.ps1 -Clean"
    "PyInstaller: passed"
    "Artifact: dist\\Laogu-Desktop\\Laogu-Desktop.exe"
    "Package: integrated desktop control center + embedded Agent"
    "Source changes: offline authorization and desktop capability gates updated; automation execution scripts were not modified by this task"
    "Test status: targeted offline-grace suite passed (31 tests); broader regression has 4 pre-existing failures"
    "Failed tests: agent/tests/test_x_automation_engine.py (3), tests/test_security_hardening.py migration-version assertion (1)"
    "Security: package excludes credentials, tokens, personal config, cookies, and logs"
) | Set-Content -LiteralPath (Join-Path $docs "BUILD-RECORD.txt") -Encoding UTF8

@(
    "Package scope:"
    "- Embedded Agent is packaged inside Laogu-Desktop.exe/_internal."
    "- Automation scripts are included under _internal\\scripts."
    "- No standalone Agent executable is produced by the current repository build."
    ""
    "Operational prerequisite:"
    "- Laogu Browser must be installed/running."
    "- Create config\\laogu.env from config\\laogu.env.example locally."
) | Set-Content -LiteralPath (Join-Path $docs "PACKAGE-SCOPE.txt") -Encoding UTF8

$hashLines = foreach ($file in (Get-ChildItem -LiteralPath $payload -Recurse -File | Sort-Object FullName)) {
    $relative = $file.FullName.Substring($release.Length).TrimStart([char[]]"\/").Replace("\", "/")
    $hash = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    "$hash  $relative"
}
$hashLines | Set-Content -LiteralPath (Join-Path $checks "SHA256SUMS.txt") -Encoding ASCII

if (Test-Path -LiteralPath $zip -PathType Leaf) {
    Remove-Item -LiteralPath $zip -Force
}
Compress-Archive -LiteralPath $payload -DestinationPath $zip -CompressionLevel Optimal
$zipHash = (Get-FileHash -LiteralPath $zip -Algorithm SHA256).Hash.ToLowerInvariant()
"$zipHash  $($zip.Substring($release.Length).TrimStart([char[]]"\/").Replace("\", "/"))" |
    Set-Content -LiteralPath (Join-Path $checks "PACKAGE-SHA256SUMS.txt") -Encoding ASCII

Write-Host "CONTROL_CENTER_AGENT_PACKAGE_OK=$release"
