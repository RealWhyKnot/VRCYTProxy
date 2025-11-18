
param(
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

$PythonExe = "python" # Assumes python is in your system PATH
$VendorDir = Join-Path $PSScriptRoot "vendor"
$BuildDir = Join-Path $PSScriptRoot "build"
$DistDir = Join-Path $PSScriptRoot "dist"
$VenvDir = Join-Path $PSScriptRoot ".venv"

$RedirectorBuildDir = Join-Path $BuildDir "redirector_build"
$PatcherBuildDir = Join-Path $BuildDir "patcher_build"
$RedirectorWorkDir = Join-Path $BuildDir "redirector_work"
$PatcherWorkDir = Join-Path $BuildDir "patcher_work"

$DenoPath = Join-Path $VendorDir "deno.exe"
$YtdlpPath = Join-Path $VendorDir "yt-dlp-latest.exe"
$VersionFilePath = Join-Path $PSScriptRoot "vendor_versions.json"
$WrapperFileListJson = Join-Path $BuildDir "wrapper_filelist.json" # Path for the file list

$BuildSucceeded = $false

try {
    Write-Host "[0/6] Checking/Downloading Dependencies..." -ForegroundColor Green
    try {
        if (-not (Test-Path $VendorDir)) {
            New-Item -ItemType Directory -Path $VendorDir | Out-Null
        }
        $DenoVersion = $null
        $YtdlpVersion = $null
        $Headers = @{ "Accept" = "application/vnd.github.v3+json" }
        if (-not $Force -and (Test-Path $VersionFilePath)) {
            try {
                Write-Host "Found 'vendor_versions.json'. Loading pinned versions."
                $PinnedVersions = Get-Content $VersionFilePath | ConvertFrom-Json
                $DenoVersion = $PinnedVersions.deno
                $YtdlpVersion = $PinnedVersions.ytdlp
                if (-not $DenoVersion -or -not $YtdlpVersion) {
                    Write-Host "'vendor_versions.json' is corrupt or incomplete. Fetching latest." -ForegroundColor Yellow
                    $DenoVersion = $null
                    $YtdlpVersion = $null
                }
            } catch {
                Write-Host "Failed to read 'vendor_versions.json': $_. Fetching latest." -ForegroundColor Yellow
                $DenoVersion = $null
                $YtdlpVersion = $null
            }
        }
        if ($null -eq $DenoVersion -or $null -eq $YtdlpVersion) {
            if ($Force) {
                Write-Host "Force flag detected. Fetching latest dependency versions..." -ForegroundColor Yellow
            } else {
                Write-Host "'vendor_versions.json' not found. Fetching latest dependency versions..." -ForegroundColor Yellow
            }
            $DenoApiUrl = "https://api.github.com/repos/denoland/deno/releases/latest"
            $DenoResponse = Invoke-RestMethod -Uri $DenoApiUrl -Headers $Headers
            $DenoVersion = $DenoResponse.tag_name
            $YtdlpApiUrl = "https://api.github.com/repos/yt-dlp/yt-dlp/releases/latest"
            $YtdlpResponse = Invoke-RestMethod -Uri $YtdlpApiUrl -Headers $Headers
            $YtdlpVersion = $YtdlpResponse.tag_name
            $NewVersions = @{ deno = $DenoVersion; ytdlp = $YtdlpVersion }
            $NewVersions | ConvertTo-Json | Out-File -FilePath $VersionFilePath -Encoding utf8
            Write-Host "Saved versions to 'vendor_versions.json': Deno $DenoVersion, yt-dlp $YtdlpVersion" -ForegroundColor Cyan
        }
        $DenoUrl = "https://github.com/denoland/deno/releases/download/$DenoVersion/deno-x86_64-pc-windows-msvc.zip"
        $YtdlpUrl = "https://github.com/yt-dlp/yt-dlp/releases/download/$YtdlpVersion/yt-dlp.exe"
        Write-Host "Using Deno version: $DenoVersion"
        Write-Host "Using yt-dlp version: $YtdlpVersion"
        if ($Force -or -not (Test-Path $DenoPath)) {
            Write-Host "Downloading Deno ($DenoVersion)..." -ForegroundColor Yellow
            $DenoZipPath = Join-Path $VendorDir "deno.zip"
            Invoke-WebRequest -Uri $DenoUrl -OutFile $DenoZipPath
            Expand-Archive -Path $DenoZipPath -DestinationPath $VendorDir -Force
            Remove-Item $DenoZipPath
            Write-Host "deno.exe downloaded successfully."
        } else {
            Write-Host "deno.exe already exists. Skipping download."
        }
        if ($Force -or -not (Test-Path $YtdlpPath)) {
            Write-Host "Downloading yt-dlp ($YtdlpVersion)..." -ForegroundColor Yellow
            Invoke-WebRequest -Uri $YtdlpUrl -OutFile $YtdlpPath
            Write-Host "yt-dlp-latest.exe downloaded successfully."
        } else {
            Write-Host "yt-dlp-latest.exe already exists. Skipping download."
        }
        Write-Host "Dependencies are ready."
    } catch {
        Write-Host "FATAL ERROR: Failed to download dependencies: $_" -ForegroundColor Red
        throw "Dependency download failed."
    }

    Write-Host ""
    Write-Host "[1/6] Setting up Python environment..." -ForegroundColor Green
    if (-not (Test-Path $VenvDir)) {
        Write-Host "Creating new Python virtual environment..."
        & $PythonExe -m venv $VenvDir
    }
    $VenvPip = Join-Path $VenvDir "Scripts\pip.exe"
    $VenvPyInstaller = Join-Path $VenvDir "Scripts\pyinstaller.exe"
    Write-Host "Virtual environment paths set."

    Write-Host ""
    Write-Host "[2/6] Installing dependencies (with custom bootloader)..." -ForegroundColor Green
    Write-Host "NOTE: This step compiles the PyInstaller bootloader and requires a C compiler."
    $env:PYINSTALLER_COMPILE_BOOTLOADER = "1"
    & $VenvPip install --upgrade pip
    & $VenvPip install --force-reinstall --no-cache-dir pyinstaller
    Write-Host "Environment ready."

    Write-Host ""
    Write-Host "[3/6] Cleaning previous build and dist directories..." -ForegroundColor Green
    if (Test-Path $DistDir) {
        Remove-Item -Recurse -Force -Path $DistDir -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 1
        if (Test-Path $DistDir) {
            Write-Host ""
            Write-Host "FATAL ERROR: Failed to clean '$DistDir'." -ForegroundColor Red
            Write-Host "Is patcher.exe still running in the background?" -ForegroundColor Yellow
            Write-Host "Please close it and try again." -ForegroundColor Yellow
            throw "Failed to clean dist directory."
        }
    }
    if (Test-Path $BuildDir) {
        Remove-Item -Recurse -Force -Path $BuildDir
    }
    New-Item -ItemType Directory -Path $DistDir | Out-Null
    New-Item -ItemType Directory -Path $BuildDir | Out-Null
    Write-Host "Directories cleaned."

    Write-Host ""
    Write-Host "[4/6] Building executables (folder mode)..." -ForegroundColor Green
    
    if (-not (Test-Path $YtdlpPath)) { throw "Missing '$YtdlpPath'" }
    if (-not (Test-Path $DenoPath)) { throw "Missing '$DenoPath'" }

    Write-Host "  -> Building Redirector..."
    & $VenvPyInstaller --noconfirm --noupx --distpath $RedirectorBuildDir --workpath $RedirectorWorkDir --specpath $BuildDir --name "yt-dlp-wrapper" `
        (Join-Path $PSScriptRoot "src\yt_dlp_redirect\main.py")
    Write-Host "  -> Redirector build complete."

    Write-Host "  -> Generating wrapper file list..."
    $WrapperBuildPath = Join-Path $RedirectorBuildDir "yt-dlp-wrapper"
    
    $WrapperFiles = (Get-ChildItem -Path $WrapperBuildPath | Select-Object -ExpandProperty Name) + "deno.exe" + "yt-dlp-latest.exe"
    
    $WrapperFiles | ConvertTo-Json -Compress | Out-File -FilePath $WrapperFileListJson -Encoding ascii
    
    Write-Host "  -> Saved file list with $($WrapperFiles.Count) items."

    Write-Host "  -> Building Patcher..."
    & $VenvPyInstaller --noconfirm --noupx --distpath $PatcherBuildDir --workpath $PatcherWorkDir --specpath $BuildDir --name "patcher" `
        (Join-Path $PSScriptRoot "src\patcher\main.py")
    Write-Host "  -> Patcher build complete."

    Write-Host ""
    Write-Host "[5/6] Assembling final application in '$DistDir'..." -ForegroundColor Green
    Copy-Item -Path (Join-Path $PatcherBuildDir "patcher\*") -Destination $DistDir -Recurse -Force
    
    $FinalWrapperDir = Join-Path $DistDir "resources\wrapper_files"
    New-Item -ItemType Directory -Path $FinalWrapperDir -Force | Out-Null
    
    Copy-Item -Path (Join-Path $RedirectorBuildDir "yt-dlp-wrapper\*") -Destination $FinalWrapperDir -Recurse -Force
    
    Write-Host "  -> Copying vendor files to assembly..."
    Copy-Item -Path $DenoPath -Destination $FinalWrapperDir
    Copy-Item -Path $YtdlpPath -Destination $FinalWrapperDir
    
    Write-Host "  -> Copying file list to dist..."
    Copy-Item -Path $WrapperFileListJson -Destination $DistDir
    
    Write-Host "Assembly complete."

    Write-Host ""
    Write-Host "[6/6] Cleaning up intermediate build files..." -ForegroundColor Green
    Remove-Item -Recurse -Force -Path $BuildDir
    Remove-Item -Path (Join-Path $PSScriptRoot "*.spec") -ErrorAction SilentlyContinue
    
    Write-Host ""
    Write-Host "-------------------------"
    Write-Host "  BUILD SUCCEEDED!"
    Write-Host "-------------------------" -ForegroundColor Green
    
    $BuildSucceeded = $true

}
catch {
    Write-Host ""
    Write-Host "!!!!!!!!!!!!!!!!!!!!!!!!!" -ForegroundColor Red
    Write-Host "   BUILD FAILED" -ForegroundColor Red
    Write-Host "!!!!!!!!!!!!!!!!!!!!!!!!!" -ForegroundColor Red
    Write-Host ""
    Write-Host "ERROR: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "Check the log for details: $LogFilePath"
}
finally {
    Write-Host ""
    Write-Host "================================================="
    if ($BuildSucceeded) {
        Write-Host "Build process finished. Log saved to '$LogFilePath'"
    } else {
        Write-Host "Build failed. Log saved to '$LogFilePath'" -ForegroundColor Red
    }
    
    Stop-Transcript
    
    Write-Host "Press any key to exit..."
    $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown") | Out-Null
}