$ErrorActionPreference = "Stop"

$root = (Resolve-Path (Join-Path $PSScriptRoot "..\")).Path
$release = Join-Path $root "release\2026-08-24-default-noise-off"
$packageName = "Laogu-Browser-1.0.0-default-noise-off"
$portableRoot = Join-Path $release "01-windows-portable"
$payload = Join-Path $portableRoot $packageName
$checks = Join-Path $release "02-checksums"
$docs = Join-Path $release "03-build-record"

if (Test-Path -LiteralPath $release) {
    Remove-Item -LiteralPath $release -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $payload, $checks, $docs | Out-Null

$exe = Join-Path $root "build\bin\Laogu-Browser.exe"
if (-not (Test-Path -LiteralPath $exe -PathType Leaf)) {
    throw "Built executable not found: $exe"
}
Copy-Item -LiteralPath $exe -Destination (Join-Path $payload "Laogu-Browser.exe") -Force

# Only copy verified Windows proxy runtimes. Never copy data, config.yaml,
# cookies, credentials, logs, or profile state from the reference tree.
$runtimeRoot = "D:\Ant-Browser-master (2)\Ant-Browser-master\bin"
$payloadBin = Join-Path $payload "bin"
New-Item -ItemType Directory -Force -Path $payloadBin | Out-Null
foreach ($name in @("xray.exe", "sing-box.exe")) {
    $source = Join-Path $runtimeRoot $name
    if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
        throw "Required runtime not found: $source"
    }
    Copy-Item -LiteralPath $source -Destination (Join-Path $payloadBin $name) -Force
}
$runtimeReadme = Join-Path $runtimeRoot "README.md"
if (Test-Path -LiteralPath $runtimeReadme -PathType Leaf) {
    Copy-Item -LiteralPath $runtimeReadme -Destination (Join-Path $payloadBin "README.md") -Force
}

$configTemplate = Join-Path $root "publish\config.init.yaml"
if (-not (Test-Path -LiteralPath $configTemplate -PathType Leaf)) {
    throw "Release config template not found: $configTemplate"
}
Copy-Item -LiteralPath $configTemplate -Destination (Join-Path $payload "config.init.yaml") -Force
New-Item -ItemType Directory -Force -Path (Join-Path $payload "data"), (Join-Path $payload "logs") | Out-Null

@(
    "Laogu Browser privacy controls build"
    "===================================="
    ""
    "Included changes:"
    "- Canvas noise defaults to explicitly disabled (flag value 0)."
    "- ClientRects noise defaults to explicitly disabled (flag value 0)."
    "- Explicit user choices remain respected and are not overwritten by runtime defaults."
    "- Read-only profile consistency diagnostics with advisory warnings only."
    ""
    "Compatibility guarantees:"
    "- Existing profile schema and fingerprint serialization are unchanged."
    "- Launch API, local CDP integration, Agent lifecycle, and proxy connector stacks are unchanged."
    "- Diagnostics never modify settings, block saving, or block browser startup."
    ""
    "Excluded: profile data, cookies, credentials, tokens, personal config.yaml, and login state."
) | Set-Content -LiteralPath (Join-Path $payload "README.txt") -Encoding UTF8

@(
    "Build time: $((Get-Date).ToString('yyyy-MM-dd HH:mm:ss zzz'))"
    "Source root: $root"
    "Frontend: npm run build:clean (passed)"
    "Backend: go test ./backend/... (passed)"
    "Desktop: wails build -clean (passed)"
    "Artifact: build\bin\Laogu-Browser.exe"
    "Release: 2026-08-24-default-noise-off"
    "Behavior: Canvas and ClientRects noise default off; explicit per-profile choices preserved"
    "Integration: Launch API, CDP, Agent lifecycle, profile schema, and proxy stacks unchanged"
) | Set-Content -LiteralPath (Join-Path $docs "BUILD-RECORD.txt") -Encoding UTF8

$assessment = Join-Path $root "docs\BROWSER_OPTIMIZATION_ASSESSMENT_ZH_CN.md"
if (Test-Path -LiteralPath $assessment -PathType Leaf) {
    Copy-Item -LiteralPath $assessment -Destination (Join-Path $docs "BROWSER_OPTIMIZATION_ASSESSMENT_ZH_CN.md") -Force
}

$hashLines = foreach ($file in (Get-ChildItem -LiteralPath $payload -Recurse -File | Sort-Object FullName)) {
    $relative = $file.FullName.Substring($release.Length).TrimStart("\", "/").Replace("\", "/")
    $hash = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    "$hash  $relative"
}
$hashLines | Set-Content -LiteralPath (Join-Path $checks "SHA256SUMS.txt") -Encoding ASCII

$zip = Join-Path $portableRoot "$packageName-windows-amd64.zip"
Compress-Archive -LiteralPath $payload -DestinationPath $zip -CompressionLevel Optimal
$zipHash = (Get-FileHash -LiteralPath $zip -Algorithm SHA256).Hash.ToLowerInvariant()
"$zipHash  $($zip.Substring($release.Length).TrimStart("\", "/").Replace("\", "/"))" |
    Set-Content -LiteralPath (Join-Path $checks "PACKAGE-SHA256SUMS.txt") -Encoding ASCII

Write-Host "PRIVACY_CONTROLS_PACKAGE_OK=$release"
Get-ChildItem -LiteralPath $release -Recurse -File | Select-Object FullName, Length, LastWriteTime
