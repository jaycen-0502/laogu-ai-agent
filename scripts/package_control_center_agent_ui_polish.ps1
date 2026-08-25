$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..\")).Path
$release = Join-Path $root "release\2026-08-24-control-center-agent-ui-polish-v0.21.9"
$payload = Join-Path $release "01-windows-portable\Laogu-Control-Center-Agent-0.21.9-ui-polish"
$checks = Join-Path $release "02-checksums"
$docs = Join-Path $release "03-build-record"
$zip = Join-Path $release "01-windows-portable\Laogu-Control-Center-Agent-0.21.9-ui-polish-windows-amd64.zip"
$dist = Join-Path $root "dist\Laogu-Desktop"
if (-not (Test-Path -LiteralPath (Join-Path $dist "Laogu-Desktop.exe") -PathType Leaf)) { throw "Portable build not found." }
if (Test-Path -LiteralPath $release) { Remove-Item -LiteralPath $release -Recurse -Force }
New-Item -ItemType Directory -Force -Path $payload, $checks, $docs, (Join-Path $payload "agent_data"), (Join-Path $payload "logs"), (Join-Path $payload "config") | Out-Null
Copy-Item -LiteralPath (Join-Path $dist "Laogu-Desktop.exe") -Destination $payload -Force
Copy-Item -LiteralPath (Join-Path $dist "_internal") -Destination $payload -Recurse -Force
Copy-Item -LiteralPath (Join-Path $root "packaging\windows\laogu.env.example") -Destination (Join-Path $payload "config\laogu.env.example") -Force
foreach ($secret in @("credentials.json", "offline_access.json", "laogu.env")) { Get-ChildItem -LiteralPath $payload -Recurse -File -Filter $secret -ErrorAction SilentlyContinue | Remove-Item -Force }
@("Laogu Control Center + Windows Agent — UI polish build", "", "Start Laogu-Desktop.exe after Laogu Browser is running.", "Create config\\laogu.env from config\\laogu.env.example before first use.", "", "Automation scripts, Agent service interfaces, and layout structure are unchanged.", "Excluded: credentials, tokens, cookies, personal config, databases, and logs.") | Set-Content -LiteralPath (Join-Path $payload "README.txt") -Encoding UTF8
@("Build time: $((Get-Date).ToString('yyyy-MM-dd HH:mm:ss zzz'))", "Artifact: dist\\Laogu-Desktop\\Laogu-Desktop.exe", "UI: asynchronous configuration loading, engine metadata cache, refined scrollbar/dropdown/focus/button styling", "Layout: unchanged", "Automation scripts: unchanged", "Agent service interfaces: unchanged", "Verification: py_compile passed; targeted tests passed (22 tests)") | Set-Content -LiteralPath (Join-Path $docs "BUILD-RECORD.txt") -Encoding UTF8
@("Package scope:", "- Integrated Laogu-Desktop.exe with embedded Agent runtime.", "- Automation modules remain under _internal.", "", "Prerequisite: Laogu Browser local API must be available.") | Set-Content -LiteralPath (Join-Path $docs "PACKAGE-SCOPE.txt") -Encoding UTF8
$hashLines = foreach ($file in (Get-ChildItem -LiteralPath $payload -Recurse -File | Sort-Object FullName)) { $relative = $file.FullName.Substring($release.Length).TrimStart([char[]]"/\").Replace("\", "/"); "$((Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant())  $relative" }
$hashLines | Set-Content -LiteralPath (Join-Path $checks "SHA256SUMS.txt") -Encoding ASCII
Compress-Archive -LiteralPath $payload -DestinationPath $zip -CompressionLevel Optimal
$zipHash = (Get-FileHash -LiteralPath $zip -Algorithm SHA256).Hash.ToLowerInvariant()
"$zipHash  $($zip.Substring($release.Length).TrimStart([char[]]"/\").Replace("\", "/"))" | Set-Content -LiteralPath (Join-Path $checks "PACKAGE-SHA256SUMS.txt") -Encoding ASCII
Write-Host "UI_POLISH_PACKAGE_OK=$release"
Write-Host "ZIP=$zip"
Write-Host "SHA256=$zipHash"
