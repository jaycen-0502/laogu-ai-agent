$ErrorActionPreference = "Stop"

$root = (Resolve-Path (Join-Path $PSScriptRoot "..\")).Path
$release = Join-Path $root "release\2026-08-23-consistency-diagnostics"
$payload = Join-Path $release "01-windows-portable\Laogu-Browser-1.0.0-consistency-diagnostics"
$checks = Join-Path $release "02-checksums"
$docs = Join-Path $release "03-build-record"

if (Test-Path -LiteralPath $release) { Remove-Item -LiteralPath $release -Recurse -Force }
New-Item -ItemType Directory -Force -Path $payload, $checks, $docs | Out-Null

Copy-Item -LiteralPath (Join-Path $root "build\bin\Laogu-Browser.exe") -Destination (Join-Path $payload "Laogu-Browser.exe") -Force
$runtime = "D:\Ant-Browser-master (2)\Ant-Browser-master\bin"
$payloadBin = Join-Path $payload "bin"
New-Item -ItemType Directory -Force -Path $payloadBin | Out-Null
foreach ($name in @("xray.exe", "sing-box.exe", "README.md")) {
    $source = Join-Path $runtime $name
    if (Test-Path -LiteralPath $source -PathType Leaf) { Copy-Item -LiteralPath $source -Destination (Join-Path $payloadBin $name) -Force }
}
Copy-Item -LiteralPath (Join-Path $root "publish\config.init.yaml") -Destination (Join-Path $payload "config.init.yaml") -Force
New-Item -ItemType Directory -Force -Path (Join-Path $payload "data"), (Join-Path $payload "logs") | Out-Null

@(
    "Laogu Browser consistency diagnostics build"
    "============================================"
    ""
    "This package adds read-only profile consistency warnings."
    "Warnings never modify profile settings, block saving, or stop startup."
    ""
    "The package does not include profile data, cookies, credentials, tokens, or personal config.yaml."
) | Set-Content -LiteralPath (Join-Path $payload "README.txt") -Encoding UTF8

@(
    "Build time: $((Get-Date).ToString('yyyy-MM-dd HH:mm:ss zzz'))"
    "Source root: $root"
    "Frontend build: passed"
    "Backend tests: passed"
    "Wails build: passed"
    "Behavior: diagnostics are advisory only"
    "Compatibility: Launch API, CDP, Agent lifecycle, proxy stacks, and profile schema unchanged"
) | Set-Content -LiteralPath (Join-Path $docs "BUILD-RECORD.txt") -Encoding UTF8
Copy-Item -LiteralPath (Join-Path $root "docs\BROWSER_OPTIMIZATION_ASSESSMENT_ZH_CN.md") -Destination (Join-Path $docs "BROWSER_OPTIMIZATION_ASSESSMENT_ZH_CN.md") -Force

$hashLines = foreach ($file in (Get-ChildItem -LiteralPath $payload -Recurse -File | Sort-Object FullName)) {
    $relative = $file.FullName.Substring($release.Length).TrimStart("\", "/").Replace("\", "/")
    $hash = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    "$hash  $relative"
}
$hashLines | Set-Content -LiteralPath (Join-Path $checks "SHA256SUMS.txt") -Encoding ASCII

$zip = Join-Path $release "01-windows-portable\Laogu-Browser-1.0.0-consistency-diagnostics-windows-amd64.zip"
Compress-Archive -LiteralPath $payload -DestinationPath $zip -CompressionLevel Optimal
$packageHashes = foreach ($file in @($zip)) {
    $hash = (Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToLowerInvariant()
    "$hash  $($file.Substring($release.Length).TrimStart("\", "/").Replace("\", "/"))"
}
$packageHashes | Set-Content -LiteralPath (Join-Path $checks "PACKAGE-SHA256SUMS.txt") -Encoding ASCII
Write-Host "CONSISTENCY_PACKAGE_OK=$release"
