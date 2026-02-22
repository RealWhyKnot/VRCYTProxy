param(
    [string]$Version,
    [switch]$Force
)

$PSScriptRoot = $PSCommandPath | Split-Path
$LogFilePath = Join-Path $PSScriptRoot "build_log.ps1.txt"

Start-Transcript -Path $LogFilePath -Force

# Check for running instances and wait for user
Write-Host "Checking for running instances..." -ForegroundColor Cyan
while ($true) {
    $Running = Get-Process "patcher", "yt-dlp-wrapper" -ErrorAction SilentlyContinue
    if (-not $Running) {
        break
    }
    $Names = $Running.ProcessName -join ", "
    Write-Host "Processes still running: $Names" -ForegroundColor Yellow
    Write-Host "Please close these processes to continue the build..."
    Start-Sleep -Seconds 2
}
Write-Host "Environment clear." -ForegroundColor Green

$ErrorActionPreference = "Stop"

Write-Host "================================================="
Write-Host "   VRCYTProxy PowerShell Build Script"
Write-Host "================================================="
Write-Host ""

if (-not $Version) {
    Write-Host "Version argument not provided. Generating dynamic version..." -ForegroundColor Yellow
    
    # Get the latest commit message which contains the version (e.g. v2026.02.20.20)
    $RawCommit = & git log -1 --pretty=%B 2>$null
    $LatestCommit = [string]::Join(" ", $RawCommit).Trim()
    
    if ($LatestCommit -match "(v\d{4}\.\d{2}\.\d{2}\.\d+)") {
        # Use only the matched version part and ensure no spaces
        $Version = "$($Matches[1]).dev"
        Write-Host "Detected version from git: $Version" -ForegroundColor Cyan
    } else {
        # Fallback if commit message doesn't match format
        $DateStr = Get-Date -Format "yyyy.MM.dd"
        $GitHash = & git rev-parse --short HEAD 2>$null
        $Version = "v$DateStr.dev-$GitHash"
        Write-Host "Fallback version generated: $Version" -ForegroundColor Yellow
    }
}

    $IsRelease = $Version -notmatch "dev"
    $FullVersionString = if ($IsRelease) { "$Version (RELEASE)" } else { "$Version (DEV)" }
    $BuildType = if ($IsRelease) { "RELEASE" } else { "DEV" }

    Write-Host "-------------------------------------------------"
    Write-Host "   Target Version: $FullVersionString"
    Write-Host "   Force Rebuild:  $Force"
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
    $IconArg = "--icon=$IconPath"
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
    
    $PinnedVersions = @{ deno = ""; ytdlp_hash = "" }
    if (Test-Path $VersionFilePath) { 
        try { 
            $PinnedVersions = Get-Content $VersionFilePath | ConvertFrom-Json 
        } catch { } 
    }

    Write-Host "Fetching latest metadata from GitHub..."
    $Headers = @{ "Accept" = "application/vnd.github.v3+json" }
    if ($env:GITHUB_TOKEN) {
        Write-Host "Using GITHUB_TOKEN for authenticated API calls."
        $Headers["Authorization"] = "Bearer $($env:GITHUB_TOKEN)"
    }
    
    # 1. Check Deno (Release Tag)
    $LatestDenoVer = (Invoke-RestMethod "https://api.github.com/repos/denoland/deno/releases/latest" -Headers $Headers).tag_name
    
    # 2. Check yt-dlp (Master Hash)
    $LatestYtdlpHash = (Invoke-RestMethod "https://api.github.com/repos/yt-dlp/yt-dlp/branches/master" -Headers $Headers).commit.sha

    $NeedsDeno = $Force -or -not (Test-Path $DenoPath) -or ($PinnedVersions.deno -ne $LatestDenoVer)
    $NeedsYtdlp = $Force -or -not (Test-Path $YtdlpPath) -or ($PinnedVersions.ytdlp_hash -ne $LatestYtdlpHash)

    if ($NeedsDeno) {
        Write-Host "Deno update needed (Current: '$($PinnedVersions.deno)', Latest: '$LatestDenoVer')..." -ForegroundColor Cyan
        Invoke-WebRequest "https://github.com/denoland/deno/releases/download/$LatestDenoVer/deno-x86_64-pc-windows-msvc.zip" -OutFile (Join-Path $VendorDir "deno.zip")
        Expand-Archive (Join-Path $VendorDir "deno.zip") -DestinationPath $VendorDir -Force
        Remove-Item (Join-Path $VendorDir "deno.zip")
        Write-Host "Deno updated to $LatestDenoVer."
    } else {
        Write-Host "Deno is up to date ($LatestDenoVer)." -ForegroundColor Gray
    }

    if ($NeedsYtdlp) {
        Write-Host "yt-dlp update needed (Current: '$($PinnedVersions.ytdlp_hash)', Latest: '$LatestYtdlpHash')..." -ForegroundColor Cyan
    } else {
        Write-Host "yt-dlp is up to date ($($LatestYtdlpHash.Substring(0,7)))." -ForegroundColor Gray
    }

    Write-Host "[1/6] Setting up fresh Conda environment..." -ForegroundColor Green
    $CondaEnvName = "VRCYTProxy_Build"
    $IsCI = $env:GITHUB_ACTIONS -eq "true"

    # Use absolute path for conda in CI to ensure recognition
    $CondaCmd = if ($IsCI) { Join-Path $env:CONDA "Scripts\conda.exe" } else { "conda" }
    Write-Host "Using Conda command: $CondaCmd"

    $EnvList = & $CondaCmd env list
    $EnvExists = $EnvList -match "\b$CondaEnvName\b"

    if ($Force -and $EnvExists) {
        Write-Host "Force rebuild requested. Wiping existing environment '$CondaEnvName'..."
        & $CondaCmd remove -n $CondaEnvName --all -y
        $EnvExists = $false
    }

    if (-not $EnvExists) {
        Write-Host "Creating fresh Conda environment '$CondaEnvName'..."
        & $CondaCmd create -n $CondaEnvName python=3.13 -y
    } else {
        Write-Host "Using existing Conda environment '$CondaEnvName'."
    }

    # Get paths from conda (works in both local and CI)
    $EnvInfo = & $CondaCmd run -n $CondaEnvName python -c "import sys, os; print(sys.executable); print(os.path.join(os.path.dirname(sys.executable), 'Scripts'))"
    $EnvInfoLines = $EnvInfo -split "`r`n"
    $VenvPython = $EnvInfoLines[0].Trim()
    $VenvScripts = $EnvInfoLines[1].Trim()
    
    $VenvPip = Join-Path $VenvScripts "pip.exe"
    $VenvPyInstaller = Join-Path $VenvScripts "pyinstaller.exe"
    
    Write-Host "Conda Python: $VenvPython"

    Write-Host "[1.5/6] Preparing workspace and Validating Python Source..." -ForegroundColor Green
    
    if (Test-Path $DistDir) { 
        Remove-Item -Recurse -Force -Path $DistDir -ErrorAction SilentlyContinue 
    }
    if (Test-Path $BuildDir) { 
        Remove-Item -Recurse -Force -Path $BuildDir 
    }
    New-Item -ItemType Directory -Path $DistDir -Force | Out-Null
    New-Item -ItemType Directory -Path $BuildDir -Force | Out-Null

    $SrcRoot = Join-Path $PSScriptRoot "src"

    # 1. Syntax Check
    Write-Host "   -> Syntax Check..." -NoNewline
    $SyntaxScript = Join-Path $PSScriptRoot "dev_tools\syntax_check.py"
    & $VenvPython "$SyntaxScript" "$SrcRoot"
    if ($LASTEXITCODE -ne 0) { Write-Host " FAILED" -ForegroundColor Red; exit 1 }
    Write-Host " PASSED" -ForegroundColor Gray

    # 2. Name Check
    Write-Host "   -> Name Check..." -NoNewline
    $NameScript = Join-Path $PSScriptRoot "dev_tools\name_check.py"
    & $VenvPython "$NameScript" "$SrcRoot"
    if ($LASTEXITCODE -ne 0) { Write-Host " FAILED" -ForegroundColor Red; exit 1 }
    Write-Host " PASSED" -ForegroundColor Gray

    # 3. Import Check
    Write-Host "   -> Import/Symbol Check..." -NoNewline
    $ImportScript = Join-Path $PSScriptRoot "dev_tools\import_check.py"
    & $VenvPython "$ImportScript" "$SrcRoot"
    if ($LASTEXITCODE -ne 0) { Write-Host " FAILED" -ForegroundColor Red; exit 1 }
    Write-Host " PASSED" -ForegroundColor Gray

    # Automatically update the hardcoded fallback version in patcher main.py
    if ($Version -and $Version -match "^v") {
        $VersionFile = Join-Path $SrcPatcherDir "_version.py"
        
        Write-Host "   -> Generating version file: $Version ($BuildType)"
        $VersionContent = "__version__ = '$Version'`n__build_type__ = '$BuildType'"
        [System.IO.File]::WriteAllText($VersionFile, $VersionContent)
        Write-Host "      File created at: $VersionFile"
    }

    Write-Host "[2/6] Installing/Updating dependencies in Conda..." -ForegroundColor Green
    $env:PYINSTALLER_COMPILE_BOOTLOADER = "1"
    
    Write-Host "Updating pip..."
    & $VenvPython -m pip install --upgrade pip --quiet
    
    if ($NeedsYtdlp) {
        Write-Host "Installing yt-dlp from master source for build..." -ForegroundColor Cyan
        & $VenvPython -m pip install https://github.com/yt-dlp/yt-dlp/archive/master.tar.gz --quiet
    }

    Write-Host "Installing PyInstaller from source (this may take a few minutes)..." -ForegroundColor Yellow
    # Using --progress-bar on and direct execution to avoid conda run hangs
    & $VenvPip install --force-reinstall --no-binary pyinstaller pyinstaller --progress-bar on
    
    Write-Host "Dependencies installed (PyInstaller bootloader recompiled)."

    Write-Host "[3/6] Starting build process..." -ForegroundColor Green
    
    Write-Host "[4/6] Building executables..." -ForegroundColor Green

    if ($NeedsYtdlp) {
        Write-Host "   -> Building standalone yt-dlp (Latest Master: $($LatestYtdlpHash.Substring(0,7)))..." -ForegroundColor Cyan
        
        # We need to ensure we build from the source we just installed via pip
        # yt-dlp master branch build requirements
        $YtDlpBuildArgs = @(
            "--noconfirm",
            "--onefile",
            "--name", "yt-dlp-latest",
            "--distpath", $VendorDir,
            "--collect-all", "yt_dlp"
        )

        if ($IconArg) { $YtDlpBuildArgs += $IconArg }

        # Find the actual path to yt_dlp source from pip
        $YtPkgInfo = & $VenvPython -m pip show yt-dlp
        $YtLocation = ($YtPkgInfo | Select-String "Location:").ToString().Split(" ")[1].Trim()
        $YtMain = Join-Path $YtLocation "yt_dlp\__main__.py"
        
        Write-Host "   -> Entry Point: $YtMain"
        & $CondaCmd run -n $CondaEnvName python -m PyInstaller @YtDlpBuildArgs $YtMain
        Write-Host "   -> yt-dlp build complete."
    } else {
        Write-Host "   -> yt-dlp is up to date. Using existing binary." -ForegroundColor Gray
    }

    # Always update versions file after successful metadata fetch/build
    @{ deno = $LatestDenoVer; ytdlp_hash = $LatestYtdlpHash } | ConvertTo-Json | Out-File $VersionFilePath

    Write-Host "   -> Building Redirector..." -ForegroundColor Cyan
    
    $RedirectorSrcDir = Join-Path $PSScriptRoot "src\yt_dlp_redirect"
    $RedirectorArgs = @(
        "--noconfirm",
        "--noupx",
        "--distpath", $RedirectorBuildDir,
        "--workpath", $RedirectorWorkDir,
        "--specpath", $BuildDir,
        "--name", "yt-dlp-wrapper",
        "--paths", $RedirectorSrcDir
    )
    if ($IconArg) { $RedirectorArgs += $IconArg }
    $RedirectorArgs += (Join-Path $RedirectorSrcDir "main.py")

    & $CondaCmd run -n $CondaEnvName python -m PyInstaller @RedirectorArgs

    $WrapperBuildPath = Join-Path $RedirectorBuildDir "yt-dlp-wrapper"
    $WrapperFiles = (Get-ChildItem -Path $WrapperBuildPath | Select-Object -ExpandProperty Name) + "deno.exe" + "yt-dlp-latest.exe"
    
    # Explicitly ensure _internal is in the list if it exists as a directory
    if (Test-Path (Join-Path $WrapperBuildPath "_internal")) {
        if ("_internal" -notin $WrapperFiles) {
            $WrapperFiles += "_internal"
        }
    }
    
    $WrapperFiles | ConvertTo-Json -Compress | Out-File -FilePath $WrapperFileListJson -Encoding ascii
    Write-Host "   -> Wrapper file list generated ($($WrapperFiles.Count) files)."

    $VersionFile = Join-Path $SrcPatcherDir "_version.py"
    
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
        "--name", "patcher",
        "--paths", $SrcPatcherDir
    )
    if ($IconArg) { $PatcherArgs += $IconArg }
    $PatcherArgs += (Join-Path $SrcPatcherDir "main.py")

    & $CondaCmd run -n $CondaEnvName python -m PyInstaller @PatcherArgs
        
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
    exit 1
}
finally {
    Stop-Transcript
    $VersionFile = Join-Path $SrcPatcherDir "_version.py"
    if (Test-Path $VersionFile) { Remove-Item $VersionFile }
}
