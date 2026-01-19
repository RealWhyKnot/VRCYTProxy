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
    Write-Host "Version argument not provided. Generating dynamic version..." -ForegroundColor Yellow
    $DateStr = Get-Date -Format "yyyy.MM.dd"
    $GitHash = & git rev-parse --short HEAD 2>$null
    $GitBranch = & git rev-parse --abbrev-ref HEAD 2>$null
    
    if ($GitHash -and $GitBranch) {
        $Version = "v$DateStr.dev-$GitBranch-$GitHash"
    } else {
        $Version = "v$DateStr.dev-local"
    }
}

$IsRelease = $Version -notmatch "dev"
$FullVersionString = if ($IsRelease) { "$Version (RELEASE)" } else { "$Version (DEV)" }

Write-Host "-------------------------------------------------"
Write-Host "   Target Version: $FullVersionString"
Write-Host "-------------------------------------------------" -ForegroundColor Cyan

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
        
        # Always fetch latest from GitHub unless Force is NOT set AND we have local files
        $DenoVer = (Invoke-RestMethod "https://api.github.com/repos/denoland/deno/releases/latest" -Headers $Headers).tag_name
        $YtdlpVer = (Invoke-RestMethod "https://api.github.com/repos/yt-dlp/yt-dlp/releases/latest" -Headers $Headers).tag_name
        
        if ($Force -or -not (Test-Path $DenoPath) -or ($PinnedVersions.deno -ne $DenoVer)) {
            Write-Host "Downloading Deno $DenoVer..." -ForegroundColor Cyan
            Invoke-WebRequest "https://github.com/denoland/deno/releases/download/$DenoVer/deno-x86_64-pc-windows-msvc.zip" -OutFile (Join-Path $VendorDir "deno.zip")
            Expand-Archive (Join-Path $VendorDir "deno.zip") -DestinationPath $VendorDir -Force
            Remove-Item (Join-Path $VendorDir "deno.zip")
            Write-Host "Deno downloaded."
        }
        
        if ($Force -or -not (Test-Path $YtdlpPath) -or ($PinnedVersions.ytdlp -ne $YtdlpVer)) {
            Write-Host "Downloading yt-dlp $YtdlpVer..." -ForegroundColor Cyan
            Invoke-WebRequest "https://github.com/yt-dlp/yt-dlp/releases/download/$YtdlpVer/yt-dlp.exe" -OutFile $YtdlpPath
            Write-Host "yt-dlp downloaded."
        }
        
        @{ deno = $DenoVer; ytdlp = $YtdlpVer } | ConvertTo-Json | Out-File $VersionFilePath
        Write-Host "Updated vendor_versions.json"
    }

    Write-Host "[1/6] Setting up fresh Conda environment..." -ForegroundColor Green
    $CondaEnvName = "VRCYTProxy_Build"
    
    # Force wipe existing environment
    Write-Host "Wiping existing Conda environment '$CondaEnvName'..."
    & conda remove -n $CondaEnvName --all -y 2>$null
    
    Write-Host "Creating fresh Conda environment '$CondaEnvName'..."
    & conda create -n $CondaEnvName python=3.13 -y

    # Get paths from conda
    $EnvInfo = & conda run -n $CondaEnvName python -c "import sys, os; print(sys.executable); print(os.path.join(os.path.dirname(sys.executable), 'Scripts'))"
    $EnvInfoLines = $EnvInfo -split "`r`n"
    $VenvPython = $EnvInfoLines[0].Trim()
    $VenvScripts = $EnvInfoLines[1].Trim()
    
    $VenvPip = Join-Path $VenvScripts "pip.exe"
    $VenvPyInstaller = Join-Path $VenvScripts "pyinstaller.exe"
    
    Write-Host "Conda Python: $VenvPython"

    # Automatically update the hardcoded fallback version in main.py
    if ($Version -and $Version -match "^v") {
        $MainPyPath = Join-Path $SrcPatcherDir "main.py"
        if (Test-Path $MainPyPath) {
            Write-Host "   -> Updating hardcoded fallback version in main.py to $Version..." -ForegroundColor Cyan
            $MainPyContent = Get-Content $MainPyPath -Raw
            # Match CURRENT_VERSION = "v..."
            $MainPyContent = $MainPyContent -replace 'CURRENT_VERSION = "v[^"]+"', "CURRENT_VERSION = `"$Version`""
            [System.IO.File]::WriteAllText($MainPyPath, $MainPyContent)
        }
    }

    Write-Host "[2/6] Installing/Updating dependencies in Conda..." -ForegroundColor Green
    $env:PYINSTALLER_COMPILE_BOOTLOADER = "1"
    
    Write-Host "Updating pip..."
    & $VenvPython -m pip install --upgrade pip --quiet
    
    Write-Host "Installing PyInstaller from source (this may take a few minutes)..." -ForegroundColor Yellow
    # Using --progress-bar on and direct execution to avoid conda run hangs
    & $VenvPip install --force-reinstall --no-binary pyinstaller pyinstaller --progress-bar on
    
    Write-Host "Dependencies installed (PyInstaller bootloader recompiled)."

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

    & conda run -n $CondaEnvName pyinstaller @RedirectorArgs

    $WrapperBuildPath = Join-Path $RedirectorBuildDir "yt-dlp-wrapper"
    $WrapperFiles = (Get-ChildItem -Path $WrapperBuildPath | Select-Object -ExpandProperty Name) + "deno.exe" + "yt-dlp-latest.exe"
    $WrapperFiles | ConvertTo-Json -Compress | Out-File -FilePath $WrapperFileListJson -Encoding ascii
    Write-Host "   -> Wrapper file list generated ($($WrapperFiles.Count) files)."

    $VersionFile = Join-Path $SrcPatcherDir "_version.py"
    $BuildType = if ($IsRelease) { "RELEASE" } else { "DEV" }
    
    Write-Host "   -> Generating version file: $Version ($BuildType)"
    "__version__ = '$Version'`n__build_type__ = '$BuildType'" | Out-File -FilePath $VersionFile -Encoding UTF8
    Write-Host "      File created at: $VersionFile"

    $DisplayVersion = "$Version ($BuildType)"
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

    & conda run -n $CondaEnvName pyinstaller @PatcherArgs
        
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