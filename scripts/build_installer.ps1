<#
.SYNOPSIS
    Builds the Windows app and its installer from this repository.

.DESCRIPTION
    1. Installs the project's dependencies with uv, exactly as uv.lock pins them.
    2. Packages the app with PyInstaller (inwebstigator.spec) into dist\inwebstigator.
    3. Builds the installer with Inno Setup (inwebstigator_installer_script.iss) as
       "dist\Inwebstigator Installer.exe".

    The .env file at the top of the repository is put in the installer, next to inwebstigator.exe, which is where
    the installed app reads its email settings from. It holds the email account's password, so only send the
    installer to people who should have it.

    Needs uv (https://docs.astral.sh/uv/) and Inno Setup 6.5 or later (https://jrsoftware.org/isdl.php).

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\build_installer.ps1
#>

$ErrorActionPreference = "Stop"

# Run from the top of the repository, wherever the script was started from
Set-Location (Split-Path -Parent $PSScriptRoot)

function Invoke-BuildStep {
    <#
    .SYNOPSIS
        Runs one step of the build, stopping the build if the program it runs fails.
    #>
    param(
        [Parameter(Mandatory)] [string] $Description,
        [Parameter(Mandatory)] [scriptblock] $Command
    )
    Write-Host "==> $Description"
    # Programs such as PyInstaller report their progress on stderr, which some PowerShell hosts would treat as a
    # failure, so only the program's exit code decides whether the step worked
    $previousErrorAction = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $Command
    }
    finally {
        $ErrorActionPreference = $previousErrorAction
    }
    if ($LASTEXITCODE -ne 0) {
        throw "$Description failed (exit code $LASTEXITCODE)."
    }
}

function Find-InnoSetupCompiler {
    <#
    .SYNOPSIS
        Finds Inno Setup's compiler (ISCC.exe), whether it is on the PATH or installed in one of its usual folders.
    #>
    $onPath = Get-Command "ISCC.exe" -ErrorAction SilentlyContinue
    if ($onPath) {
        return $onPath.Source
    }
    $usualFolders = @(
        "${env:ProgramFiles(x86)}\Inno Setup 6",
        "$env:ProgramFiles\Inno Setup 6",
        "$env:LOCALAPPDATA\Programs\Inno Setup 6"
    )
    foreach ($folder in $usualFolders) {
        $compiler = Join-Path $folder "ISCC.exe"
        if (Test-Path $compiler) {
            return $compiler
        }
    }
    throw "Inno Setup was not found. Install Inno Setup 6.5 or later from https://jrsoftware.org/isdl.php."
}

if (-not (Test-Path ".env")) {
    throw "There is no .env file at the top of the repository. Copy .env.example to .env and fill in the email " +
        "settings, as the installed app reads them from it."
}
if (-not (Get-Command "uv" -ErrorAction SilentlyContinue)) {
    throw "uv was not found. Install it from https://docs.astral.sh/uv/."
}
$innoSetupCompiler = Find-InnoSetupCompiler

Invoke-BuildStep "Installing the dependencies" { uv sync --locked }
Invoke-BuildStep "Packaging the app with PyInstaller" { uv run pyinstaller --noconfirm --clean inwebstigator.spec }
Invoke-BuildStep "Building the installer with Inno Setup" { & $innoSetupCompiler "inwebstigator_installer_script.iss" }

Write-Host "Built dist\Inwebstigator Installer.exe"
