param(
    [switch]$Clean
)

$ErrorActionPreference = "Stop"
$Project = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$Spec = Join-Path $PSScriptRoot "laogu-desktop.spec"
$Dist = Join-Path $Project "dist\Laogu-Desktop"
$Work = Join-Path $Project "build\laogu-desktop"
$Preserve = Join-Path $Project ("build\portable-state-" + [guid]::NewGuid().ToString("N"))
$PortableState = @("config", "logs", "agent_data")

foreach ($Path in @($Dist, $Work)) {
    $AbsolutePath = [System.IO.Path]::GetFullPath($Path)
    $ProjectPrefix = $Project.TrimEnd('\') + '\'
    if (-not $AbsolutePath.StartsWith($ProjectPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to operate outside the project directory: $AbsolutePath"
    }
}

Set-Location $Project

function Save-PortableState {
    if (-not (Test-Path -LiteralPath $Dist)) { return }
    New-Item -ItemType Directory -Force -Path $Preserve | Out-Null
    foreach ($Name in $PortableState) {
        $Source = Join-Path $Dist $Name
        if (Test-Path -LiteralPath $Source) {
            Copy-Item -LiteralPath $Source -Destination $Preserve -Recurse -Force
        }
    }
}

function Restore-PortableState {
    foreach ($Name in $PortableState) {
        $Saved = Join-Path $Preserve $Name
        $Target = Join-Path $Dist $Name
        New-Item -ItemType Directory -Force -Path $Target | Out-Null
        if (Test-Path -LiteralPath $Saved) {
            Get-ChildItem -LiteralPath $Saved -Force | Copy-Item -Destination $Target -Recurse -Force
        }
    }
}

Save-PortableState
try {
    if ($Clean) {
        Remove-Item -LiteralPath $Work -Recurse -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $Dist -Recurse -Force -ErrorAction SilentlyContinue
    }

    python -m PyInstaller --noconfirm --distpath (Join-Path $Project "dist") --workpath $Work $Spec
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed" }

    # Qt6Core on Windows uses the operating system ICU. PyInstaller can pick
    # up an incompatible icuuc.dll from an unrelated PATH entry (for example
    # Poppler), which makes QtWidgets fail with WinError 127 at startup.
    $InternalRuntime = Join-Path $Dist "_internal"
    Get-ChildItem -LiteralPath $InternalRuntime -File -Filter "icu*.dll" -ErrorAction SilentlyContinue |
        Remove-Item -Force
    Get-ChildItem -LiteralPath $InternalRuntime -File -Filter "api-ms-win-*.dll" -ErrorAction SilentlyContinue |
        Remove-Item -Force
    Remove-Item -LiteralPath (Join-Path $InternalRuntime "ucrtbase.dll") -Force -ErrorAction SilentlyContinue

    New-Item -ItemType Directory -Force -Path (Join-Path $Dist "config"), (Join-Path $Dist "logs"), (Join-Path $Dist "agent_data") | Out-Null
    Copy-Item -LiteralPath (Join-Path $Project "packaging\windows\laogu.env.example") -Destination (Join-Path $Dist "config\laogu.env.example") -Force
    Copy-Item -LiteralPath (Join-Path $Project "packaging\windows\README.txt") -Destination (Join-Path $Dist "README.txt") -Force

    $Config = Join-Path $Dist "config\laogu.env"
    if (-not (Test-Path -LiteralPath $Config)) {
        Copy-Item -LiteralPath (Join-Path $Dist "config\laogu.env.example") -Destination $Config
    }

    Restore-PortableState

    Write-Host "BUILD_OK=$Dist"
    Write-Host "EXE=$(Join-Path $Dist 'Laogu-Desktop.exe')"
}
finally {
    # If a build fails after cleaning dist, restore credentials and local state
    # so the failure cannot erase the existing portable installation data.
    if (Test-Path -LiteralPath $Preserve) {
        Restore-PortableState
        $ResolvedPreserve = [System.IO.Path]::GetFullPath($Preserve)
        $ProjectPrefix = $Project.TrimEnd('\') + '\'
        if ($ResolvedPreserve.StartsWith($ProjectPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
            Remove-Item -LiteralPath $ResolvedPreserve -Recurse -Force
        }
    }
}
