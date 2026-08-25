$ErrorActionPreference = "Stop"

$root = (Resolve-Path (Join-Path $PSScriptRoot "..\")).Path
$release = Join-Path $root "release\2026-08-23-baseline"
$exeDir = Join-Path $release "01-windows-portable\Laogu-Browser-1.0.0-windows-amd64"
$sourceDir = Join-Path $release "02-source-baseline"
$checksDir = Join-Path $release "03-checksums"
$docsDir = Join-Path $release "04-build-record"

if (Test-Path -LiteralPath $release) {
    Remove-Item -LiteralPath $release -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $exeDir, $sourceDir, $checksDir, $docsDir | Out-Null

$exe = Join-Path $root "build\bin\Laogu-Browser.exe"
if (-not (Test-Path -LiteralPath $exe -PathType Leaf)) {
    throw "基线 EXE 不存在: $exe"
}
Copy-Item -LiteralPath $exe -Destination (Join-Path $exeDir "Laogu-Browser.exe") -Force

# Runtime binaries come from the verified local Ant Browser working tree. Do not
# copy its data, config.yaml, logs, or profile state into a distributable package.
$sourceRuntime = "D:\Ant-Browser-master (2)\Ant-Browser-master\bin"
if (Test-Path -LiteralPath $sourceRuntime -PathType Container) {
    $targetRuntime = Join-Path $exeDir "bin"
    New-Item -ItemType Directory -Force -Path $targetRuntime | Out-Null
    foreach ($runtimeName in @("xray.exe", "sing-box.exe", "README.md")) {
        $runtimePath = Join-Path $sourceRuntime $runtimeName
        if (Test-Path -LiteralPath $runtimePath -PathType Leaf) {
            Copy-Item -LiteralPath $runtimePath -Destination (Join-Path $targetRuntime $runtimeName) -Force
        }
    }
}

$chromeReadme = Join-Path $root "chrome\README.md"
if (Test-Path -LiteralPath $chromeReadme -PathType Leaf) {
    Copy-Item -LiteralPath $chromeReadme -Destination (Join-Path $exeDir "chrome-README.md") -Force
}
Copy-Item -LiteralPath (Join-Path $root "publish\config.init.yaml") -Destination (Join-Path $exeDir "config.init.yaml") -Force
New-Item -ItemType Directory -Force -Path (Join-Path $exeDir "data"), (Join-Path $exeDir "logs") | Out-Null

$readme = @(
    "Laogu Browser baseline portable package"
    "======================================"
    ""
    "Built from the current browser source on 2026-08-23 for rollback and compatibility comparison."
    ""
    "Contents:"
    "- Laogu-Browser.exe: Windows build from current source"
    "- bin: verified Xray / sing-box runtimes"
    "- config.init.yaml: release configuration template"
    "- data, logs: empty runtime directories"
    "- chrome-README.md: Chromium runtime availability note"
    ""
    "Excluded: cookies, LocalStorage, login state, proxy credentials, signing keys, databases, and personal config.yaml."
    "Provide a supported Chromium runtime through the normal Browser release process; do not copy personal data directories."
)
$readme | Set-Content -LiteralPath (Join-Path $exeDir "README.txt") -Encoding UTF8

$metadata = [ordered]@{
    product = "Laogu Browser"
    version = "1.0.0"
    target = "windows-amd64"
    build_time = (Get-Date).ToString("o")
    source_root = $root
    source_reference = "current browser worktree"
    wails = "v2.13.0"
    go = "go1.26.5"
    node = "v24.18.0"
    frontend_build = "passed"
    wails_build = "passed"
    excluded = @("data", "logs", "config.yaml", "cookies", "credentials", "tokens", "proxy credentials")
}
$metadata | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $docsDir "build-metadata.json") -Encoding UTF8
$assessment = Join-Path $root "docs\BROWSER_OPTIMIZATION_ASSESSMENT_ZH_CN.md"
if (Test-Path -LiteralPath $assessment -PathType Leaf) {
    Copy-Item -LiteralPath $assessment -Destination (Join-Path $docsDir "BROWSER_OPTIMIZATION_ASSESSMENT_ZH_CN.md") -Force
}

$buildRecord = @(
    "Build time: $((Get-Date).ToString('yyyy-MM-dd HH:mm:ss zzz'))"
    "Source root: $root"
    "Frontend: npm run ensure:native; npm run build:clean (passed)"
    "Wails: wails build -clean (passed)"
    "Artifact: build\bin\Laogu-Browser.exe"
    "Note: legacy bat\publish.ps1 still hardcodes ant-chrome.exe/AntBrowser and was not used."
)
$buildRecord | Set-Content -LiteralPath (Join-Path $docsDir "BUILD-RECORD.txt") -Encoding UTF8

$hashLines = foreach ($file in (Get-ChildItem -LiteralPath $exeDir -Recurse -File | Sort-Object FullName)) {
    $relative = $file.FullName.Substring($release.Length).TrimStart("\", "/").Replace("\", "/")
    $hash = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    "$hash  $relative"
}
$hashLines | Set-Content -LiteralPath (Join-Path $checksDir "SHA256SUMS.txt") -Encoding ASCII

$zip = Join-Path $release "01-windows-portable\Laogu-Browser-1.0.0-windows-amd64-baseline.zip"
if (Test-Path -LiteralPath $zip -PathType Leaf) {
    Remove-Item -LiteralPath $zip -Force
}
Compress-Archive -LiteralPath $exeDir -DestinationPath $zip -CompressionLevel Optimal

$projectRoot = (Resolve-Path (Join-Path $root "..\")).Path
$sourceZip = Join-Path $sourceDir "Laogu-Browser-1.0.0-source-baseline.zip"
Push-Location $projectRoot
try {
    & git archive --format=zip --output=$sourceZip HEAD:browser
}
finally {
    Pop-Location
}
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $sourceZip -PathType Leaf)) {
    throw "Source baseline archive failed"
}

$packageHashes = foreach ($artifact in @($zip, $sourceZip)) {
    $hash = (Get-FileHash -LiteralPath $artifact -Algorithm SHA256).Hash.ToLowerInvariant()
    "$hash  $($artifact.Substring($release.Length).TrimStart('\', '/').Replace('\', '/'))"
}
$packageHashes | Set-Content -LiteralPath (Join-Path $checksDir "PACKAGE-SHA256SUMS.txt") -Encoding ASCII

Write-Host "BASELINE_PACKAGE_OK=$release"
Get-ChildItem -LiteralPath $release -Recurse -File | Select-Object FullName, Length, LastWriteTime
