param(
    [string]$Version, 
    [switch]$Force
)

$PSScriptRoot = $PSCommandPath | Split-Path
$LogFilePath = Join-Path $PSScriptRoot "build_log.ps1.txt"

Start-Transcript -Path $LogFilePath -Force
$ErrorActionPreference = "Stop"

Write-Host "================================================="
Write-Host "   VRCYTProxy PowerShell Build Script"
Write-Host "================================================="
Write-Host ""

if (-not $Version) {
    Write-Host "Version argument not provided. Python script will use fallback." -ForegroundColor Yellow
} else {
    Write-Host "Target Version: $Version" -ForegroundColor Green
}

$PythonExe = "python" 
$VendorDir = Join-Path $PSScriptRoot "vendor"
$BuildDir = Join-Path $PSScriptRoot "build"
$DistDir = Join-Path $PSScriptRoot "dist"
$VenvDir = Join-Path $PSScriptRoot ".venv"
$SrcPatcherDir = Join-Path $PSScriptRoot "src\patcher"
$ResourcesDir = Join-Path $PSScriptRoot "src\resources"

$RedirectorBuildDir = Join-Path $BuildDir "redirector_build"
$PatcherBuildDir = Join-Path $BuildDir "patcher_build"
$RedirectorWorkDir = Join-Path $BuildDir "redirector_work"
$PatcherWorkDir = Join-Path $BuildDir "patcher_work"

$DenoPath = Join-Path $VendorDir "deno.exe"
$YtdlpPath = Join-Path $VendorDir "yt-dlp-latest.exe"
$VersionFilePath = Join-Path $PSScriptRoot "vendor_versions.json"
$WrapperFileListJson = Join-Path $BuildDir "wrapper_filelist.json"

$IconPath = Join-Path $ResourcesDir "app.ico"
$IconArg = ""

if (Test-Path $IconPath) {
    Write-Host "Icon found at: $IconPath" -ForegroundColor Cyan
    $IconArg = "--icon=""$IconPath"""
} else {
    Write-Host "No icon found at '$IconPath'. Building with default icon." -ForegroundColor Yellow
}

$BuildSucceeded = $false

try {
    Write-Host "[0/6] Checking/Downloading Dependencies..." -ForegroundColor Green
    if (-not (Test-Path $VendorDir)) { 
        New-Item -ItemType Directory -Path $VendorDir | Out-Null 
        Write-Host "Created vendor directory."
    }
    
    $PinnedVersions = $null
    if (Test-Path $VersionFilePath) { 
        try { 
            $PinnedVersions = Get-Content $VersionFilePath | ConvertFrom-Json 
            Write-Host "Loaded versions from vendor_versions.json: $($PinnedVersions | ConvertTo-Json -Compress)"
        } catch {
            Write-Host "Warning: vendor_versions.json exists but could not be parsed." -ForegroundColor Yellow
        } 
    }
    
    if ($Force -or -not (Test-Path $DenoPath) -or -not (Test-Path $YtdlpPath)) {
        Write-Host "Checking for latest dependency versions..."
        $Headers = @{ "Accept" = "application/vnd.github.v3+json" }
        $DenoVer = if ($PinnedVersions.deno) { $PinnedVersions.deno } else { (Invoke-RestMethod "https://api.github.com/repos/denoland/deno/releases/latest" -Headers $Headers).tag_name }
        $YtdlpVer = if ($PinnedVersions.ytdlp) { $PinnedVersions.ytdlp } else { (Invoke-RestMethod "https://api.github.com/repos/yt-dlp/yt-dlp/releases/latest" -Headers $Headers).tag_name }
        
        if (-not (Test-Path $DenoPath)) {
            Write-Host "Downloading Deno $DenoVer..." -ForegroundColor Cyan
            Invoke-WebRequest "https://github.com/denoland/deno/releases/download/$DenoVer/deno-x86_64-pc-windows-msvc.zip" -OutFile (Join-Path $VendorDir "deno.zip")
            Expand-Archive (Join-Path $VendorDir "deno.zip") -DestinationPath $VendorDir -Force
            Remove-Item (Join-Path $VendorDir "deno.zip")
            Write-Host "Deno downloaded."
        }
        if (-not (Test-Path $YtdlpPath)) {
            Write-Host "Downloading yt-dlp $YtdlpVer..." -ForegroundColor Cyan
            Invoke-WebRequest "https://github.com/yt-dlp/yt-dlp/releases/download/$YtdlpVer/yt-dlp.exe" -OutFile $YtdlpPath
            Write-Host "yt-dlp downloaded."
        }
        @{ deno = $DenoVer; ytdlp = $YtdlpVer } | ConvertTo-Json | Out-File $VersionFilePath
        Write-Host "Updated vendor_versions.json"
    }

    Write-Host "[1/6] Setting up Python environment..." -ForegroundColor Green
    if (-not (Test-Path $VenvDir)) { 
        Write-Host "Creating virtual environment..."
        & $PythonExe -m venv $VenvDir 
    }
    $VenvPython = Join-Path $VenvDir "Scripts\python.exe"
    $VenvPip = Join-Path $VenvDir "Scripts\pip.exe"
    $VenvPyInstaller = Join-Path $VenvDir "Scripts\pyinstaller.exe"
    Write-Host "Virtual Env: $VenvDir"

    Write-Host "[2/6] Installing/Updating dependencies..." -ForegroundColor Green
    $env:PYINSTALLER_COMPILE_BOOTLOADER = "1"
    & $VenvPython -m pip install --upgrade pip
    & $VenvPip install --force-reinstall --no-cache-dir pyinstaller
    Write-Host "Dependencies installed."

    Write-Host "[3/6] Cleaning directories..." -ForegroundColor Green
    if (Test-Path $DistDir) { 
        Remove-Item -Recurse -Force -Path $DistDir -ErrorAction SilentlyContinue 
        Write-Host "Cleaned dist."
    }
    if (Test-Path $BuildDir) { 
        Remove-Item -Recurse -Force -Path $BuildDir 
        Write-Host "Cleaned build."
    }
    New-Item -ItemType Directory -Path $DistDir | Out-Null
    New-Item -ItemType Directory -Path $BuildDir | Out-Null
    
    Write-Host "[4/6] Building executables..." -ForegroundColor Green

    Write-Host "   -> Building Redirector..." -ForegroundColor Cyan
    
    $RedirectorArgs = @(
        "--noconfirm",
        "--noupx",
        "--distpath", $RedirectorBuildDir,
        "--workpath", $RedirectorWorkDir,
        "--specpath", $BuildDir,
        "--name", "yt-dlp-wrapper"
    )
    if ($IconArg) { $RedirectorArgs += "--icon", $IconPath }
    $RedirectorArgs += (Join-Path $PSScriptRoot "src\yt_dlp_redirect\main.py")

    & $VenvPyInstaller @RedirectorArgs

    $WrapperBuildPath = Join-Path $RedirectorBuildDir "yt-dlp-wrapper"
    $WrapperFiles = (Get-ChildItem -Path $WrapperBuildPath | Select-Object -ExpandProperty Name) + "deno.exe" + "yt-dlp-latest.exe"
    $WrapperFiles | ConvertTo-Json -Compress | Out-File -FilePath $WrapperFileListJson -Encoding ascii
    Write-Host "   -> Wrapper file list generated ($($WrapperFiles.Count) files)."

    $VersionFile = Join-Path $SrcPatcherDir "_version.py"
    
    if ($Version) {
        Write-Host "   -> Generating version file: $Version"
        "__version__ = '$Version'" | Out-File -FilePath $VersionFile -Encoding UTF8
        Write-Host "      File created at: $VersionFile"
    } else {
        Write-Host "   -> No version provided. Skipping _version.py generation (using python fallback)."
    }

    $DisplayVersion = if ($Version) { $Version } else { "dev-fallback" }
    Write-Host "   -> Building Patcher (Version: $DisplayVersion)..." -ForegroundColor Cyan
    
    $PatcherArgs = @(
        "--noconfirm",
        "--noupx",
        "--distpath", $PatcherBuildDir,
        "--workpath", $PatcherWorkDir,
        "--specpath", $BuildDir,
        "--name", "patcher"
    )
    if ($IconArg) { $PatcherArgs += "--icon", $IconPath }
    $PatcherArgs += (Join-Path $SrcPatcherDir "main.py")

    & $VenvPyInstaller @PatcherArgs
        
    if ($Version -and (Test-Path $VersionFile)) { 
        Remove-Item $VersionFile 
        Write-Host "   -> Cleaned up temporary version file."
    }
    Write-Host "   -> Patcher build complete."

    Write-Host "[5/6] Assembling dist..." -ForegroundColor Green
    
    Write-Host "   -> Copying Patcher binaries..."
    Copy-Item -Path (Join-Path $PatcherBuildDir "patcher\*") -Destination $DistDir -Recurse -Force
    
    Write-Host "   -> Assembling wrapper resource folder..."
    $FinalWrapperDir = Join-Path $DistDir "resources\wrapper_files"
    New-Item -ItemType Directory -Path $FinalWrapperDir -Force | Out-Null
    
    Copy-Item -Path (Join-Path $RedirectorBuildDir "yt-dlp-wrapper\*") -Destination $FinalWrapperDir -Recurse -Force
    Copy-Item -Path $DenoPath -Destination $FinalWrapperDir
    Copy-Item -Path $YtdlpPath -Destination $FinalWrapperDir
    Copy-Item -Path $WrapperFileListJson -Destination $DistDir

    Write-Host "[6/6] Final Cleanup..." -ForegroundColor Green
    Remove-Item -Recurse -Force -Path $BuildDir
    Remove-Item -Path (Join-Path $PSScriptRoot "*.spec") -ErrorAction SilentlyContinue
    Write-Host "Build directory cleaned."

    $BuildSucceeded = $true
    Write-Host "-------------------------"
    Write-Host "   BUILD SUCCEEDED! ($DisplayVersion)"
    Write-Host "-------------------------" -ForegroundColor Green
}
catch {
    Write-Host "BUILD FAILED: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "Stack Trace: $($_.ScriptStackTrace)" -ForegroundColor Red
}
finally {
    Stop-Transcript
    $VersionFile = Join-Path $SrcPatcherDir "_version.py"
    if (Test-Path $VersionFile) { Remove-Item $VersionFile }

    if (-not $BuildSucceeded) {
        Write-Host "Press any key to exit..."
        $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown") | Out-Null
    }
}