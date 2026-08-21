param(
    [switch]$Clean
)

$ErrorActionPreference = "Stop"
$Project = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$Spec = Join-Path $PSScriptRoot "laogu-desktop.spec"
$Dist = Join-Path $Project "dist\Laogu-Desktop"
$Work = Join-Path $Project "build\laogu-desktop"

foreach ($Path in @($Dist, $Work)) {
    $AbsolutePath = [System.IO.Path]::GetFullPath($Path)
    $ProjectPrefix = $Project.TrimEnd('\') + '\'
    if (-not $AbsolutePath.StartsWith($ProjectPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to operate outside the project directory: $AbsolutePath"
    }
}

Set-Location $Project
if ($Clean) {
    Remove-Item -LiteralPath $Work -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $Dist -Recurse -Force -ErrorAction SilentlyContinue
}

python -m PyInstaller --noconfirm --distpath (Join-Path $Project "dist") --workpath $Work $Spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed" }

New-Item -ItemType Directory -Force -Path (Join-Path $Dist "config"), (Join-Path $Dist "logs"), (Join-Path $Dist "agent_data") | Out-Null
Copy-Item -LiteralPath (Join-Path $Project "packaging\windows\laogu.env.example") -Destination (Join-Path $Dist "config\laogu.env.example") -Force
Copy-Item -LiteralPath (Join-Path $Project "packaging\windows\README.txt") -Destination (Join-Path $Dist "README.txt") -Force

$Config = Join-Path $Dist "config\laogu.env"
if (-not (Test-Path -LiteralPath $Config)) {
    Copy-Item -LiteralPath (Join-Path $Dist "config\laogu.env.example") -Destination $Config
}

Write-Host "BUILD_OK=$Dist"
Write-Host "EXE=$(Join-Path $Dist 'Laogu-Desktop.exe')"
