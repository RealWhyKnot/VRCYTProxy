param(
    [string]$Version,
    [switch]$Force
)

$PSScriptRoot = $PSCommandPath | Split-Path
$LogFilePath = Join-Path $PSScriptRoot "build_log.ps1.txt"

Start-Transcript -Path $LogFilePath -Force

# Terminate any running instances to prevent file locking
Write-Host "Checking for running instances..." -ForegroundColor Cyan
Get-Process "patcher", "yt-dlp-wrapper" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 1 # Give time for file handles to release

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

    # 1. Syntax Check
    $SyntaxCheckCode = @"
import sys
import py_compile
import ast
from pathlib import Path

def check_syntax(directory):
    print(f"Deep checking syntax in {directory}...")
    success = True
    for path in Path(directory).rglob("*.py"):
        if any(x in str(path) for x in [".old", ".venv", "node_modules", "__pycache__", "build", "dist"]):
            continue
            
        # 1. Bytecode compilation check
        try:
            py_compile.compile(str(path), doraise=True)
        except py_compile.PyCompileError as e:
            print(f"\n[FAIL] Bytecode Compilation Error in {path}:")
            print(e)
            success = False
            continue
            
        # 2. AST Parsing check (Extremely strict on indentation and structure)
        try:
            with open(path, "r", encoding="utf-8") as f:
                ast.parse(f.read())
        except SyntaxError as e:
            print(f"\n[FAIL] AST Syntax Error in {path}:")
            print(f"  Line {e.lineno}: {e.msg}")
            if e.text:
                print(f"  Code: {e.text.strip()}")
            success = False
        except Exception as e:
            print(f"\n[FAIL] Unexpected parsing error in {path}: {e}")
            success = False
            
    return success

if __name__ == "__main__":
    import os
    src_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(os.getcwd())
    if not check_syntax(src_dir):
        sys.exit(1)
    sys.exit(0)
"@
    $SyntaxCheckCode | Out-File -FilePath (Join-Path $BuildDir "syntax_check.py") -Encoding utf8
    
    # 2. Name Check (Undefined Variables)
    $NameCheckCode = @"
import ast
import sys
import os
from pathlib import Path

class NameChecker(ast.NodeVisitor):
    def __init__(self, filename, content):
        self.filename = filename
        self.content_lines = content.splitlines()
        self.errors = []
        # Stack of scopes. Each scope is a set of defined names.
        self.scopes = [set()]
        
        # Add common builtins
        import builtins
        self.scopes[0].update(dir(builtins))
        self.scopes[0].update(['__file__', '__name__', 'True', 'False', 'None', 'classmethod', 'staticmethod', 'property', 'id', 'next', 'iter', 'len', 'range', 'enumerate', 'any', 'all', 'sum', 'min', 'max', 'sorted', 'round', 'float', 'int', 'str', 'dict', 'list', 'set', 'bool', 'Exception', 'ValueError', 'TypeError', 'StopIteration', 'ImportError', 'FileNotFoundError'])

    def define_globals(self, tree):
        """First pass: find all globally defined names."""
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                self.define(node.name)
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    self._handle_assignment_target(target)
            elif isinstance(node, ast.AnnAssign):
                self._handle_assignment_target(node.target)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    self.define(alias.asname or alias.name.split('.')[0])

    def error(self, node, msg):
        line = node.lineno
        col = node.col_offset
        text = self.content_lines[line-1] if line <= len(self.content_lines) else ""
        self.errors.append(f"{self.filename}:{line}:{col}: {msg}\n  -> {text.strip()}")

    def define(self, name):
        self.scopes[-1].add(name)

    def is_defined(self, name):
        for scope in reversed(self.scopes):
            if name in scope:
                return True
        return False

    def visit_Import(self, node):
        for alias in node.names:
            self.define(alias.asname or alias.name.split('.')[0])
        self.generic_visit(node)

    def visit_ImportFrom(self, node):
        for alias in node.names:
            self.define(alias.asname or alias.name)
        self.generic_visit(node)

    def visit_ClassDef(self, node):
        self.define(node.name)
        # Class body is a new scope
        self.scopes.append(set())
        self.generic_visit(node)
        self.scopes.pop()

    def visit_FunctionDef(self, node):
        self.define(node.name)
        # Function arguments define names in the new scope
        new_scope = set()
        for arg in node.args.args:
            new_scope.add(arg.arg)
        if node.args.vararg: new_scope.add(node.args.vararg.arg)
        if node.args.kwarg: new_scope.add(node.args.kwarg.arg)
        
        self.scopes.append(new_scope)
        self.generic_visit(node)
        self.scopes.pop()

    def visit_AsyncFunctionDef(self, node):
        self.visit_FunctionDef(node)

    def visit_Lambda(self, node):
        # Lambda is a new scope
        self.scopes.append(set())
        for arg in node.args.args:
            self.define(arg.arg)
        if node.args.vararg: self.define(node.args.vararg.arg)
        if node.args.kwarg: self.define(node.args.kwarg.arg)
        self.generic_visit(node)
        self.scopes.pop()

    def visit_ListComp(self, node):
        self._visit_comp(node)

    def visit_SetComp(self, node):
        self._visit_comp(node)

    def visit_DictComp(self, node):
        self._visit_comp(node)

    def visit_GeneratorExp(self, node):
        self._visit_comp(node)

    def _visit_comp(self, node):
        # Comprehensions are their own scope in Python 3
        self.scopes.append(set())
        # First visit the generators to define iteration variables
        for gen in node.generators:
            self._handle_assignment_target(gen.target)
            self.visit(gen.iter)
            for if_clause in gen.ifs:
                self.visit(if_clause)
        
        # Then visit the body (elt or key/value)
        if hasattr(node, 'elt'):
            self.visit(node.elt)
        if hasattr(node, 'key'):
            self.visit(node.key)
        if hasattr(node, 'value'):
            self.visit(node.value)

        self.scopes.pop()

    def visit_Assign(self, node):
        for target in node.targets:
            self._handle_assignment_target(target)
        self.generic_visit(node)

    def visit_AnnAssign(self, node):
        self._handle_assignment_target(node.target)
        self.generic_visit(node)

    def visit_For(self, node):
        self._handle_assignment_target(node.target)
        self.generic_visit(node)

    def visit_AsyncFor(self, node):
        self.visit_For(node)

    def visit_With(self, node):
        for item in node.items:
            if item.optional_vars:
                self._handle_assignment_target(item.optional_vars)
        self.generic_visit(node)

    def visit_AsyncWith(self, node):
        self.visit_With(node)

    def _handle_assignment_target(self, target):
        if isinstance(target, ast.Name):
            self.define(target.id)
        elif isinstance(target, (ast.Tuple, ast.List)):
            for elt in target.elts:
                self._handle_assignment_target(elt)

    def visit_Name(self, node):
        # Only check names being loaded (used), not ones being stored (defined)
        if isinstance(node.ctx, ast.Load):
            if not self.is_defined(node.id):
                self.error(node, f"Undefined name '{node.id}'")
        self.generic_visit(node)

    # Specific handling for try-except blocks
    def visit_ExceptHandler(self, node):
        if node.name:
            self.define(node.name)
        self.generic_visit(node)

def check_file(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        tree = ast.parse(content)
        checker = NameChecker(path.name, content)
        checker.define_globals(tree)
        checker.visit(tree)
        return checker.errors
    except Exception as e:
        return [f"ERROR PARSING {path.name}: {e}"]

def run_name_checks(directory):
    print(f"Deep checking names and variables in {directory}...")
    root = Path(directory)
    all_errors = {}

    for path in root.rglob("*.py"):
        if any(x in str(path) for x in [".old", ".venv", "node_modules", "__pycache__", "build", "dist"]):
            continue

        errors = check_file(path)
        if errors:
            all_errors[str(path)] = errors

    if all_errors:
        print("\n[CRITICAL] Undefined names found!")
        for path, errors in all_errors.items():
            print(f"\nFile: {path}")
            for err in errors:
                print(f"  - {err}")
        return False
    
    print("[SUCCESS] No undefined names found.")
    return True

if __name__ == "__main__":
    import os
    src_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(os.getcwd())
    if not run_name_checks(src_dir):
        sys.exit(1)
    sys.exit(0)
"@
    $NameCheckCode | Out-File -FilePath (Join-Path $BuildDir "name_check.py") -Encoding utf8

    # 3. Import and Symbol Check
    $ImportCheckCode = @"
import sys
import os
import ast
from pathlib import Path

def get_defined_names(tree):
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
                elif isinstance(target, (ast.Tuple, ast.List)):
                    for elt in target.elts:
                        if isinstance(elt, ast.Name):
                            names.add(elt.id)
    return names

def check_file_static_symbols(file_path, project_root):
    with open(file_path, "r", encoding="utf-8") as f:
        try:
            tree = ast.parse(f.read())
        except Exception as e:
            return [f"AST Parse Error: {e}"]

    errors = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if not node.module: continue
            
            # Resolve the module path
            module_path = None
            if node.level > 0:
                # Relative
                path = Path(file_path).parent
                for _ in range(node.level - 1):
                    path = path.parent
                if node.module:
                    path = path.joinpath(*node.module.split("."))
                module_path = path
            else:
                # Absolute - check if it's in src
                path = project_root.joinpath(*node.module.split("."))
                if path.with_suffix(".py").exists() or (path.is_dir() and path.joinpath("__init__.py").exists()):
                    module_path = path

            if not module_path: continue

            target_file = None
            if module_path.with_suffix(".py").exists():
                target_file = module_path.with_suffix(".py")
            elif module_path.is_dir() and module_path.joinpath("__init__.py").exists():
                target_file = module_path.joinpath("__init__.py")

            if target_file:
                with open(target_file, "r", encoding="utf-8") as f_target:
                    try:
                        target_tree = ast.parse(f_target.read())
                        existing_names = get_defined_names(target_tree)
                        
                        for alias in node.names:
                            if alias.name == "*": continue
                            if alias.name not in existing_names:
                                errors.append(f"Symbol '{alias.name}' not found in '{node.module}'")
                    except: pass
    return errors

if __name__ == "__main__":
    src_dir = Path(sys.argv[1])
    project_root = src_dir.parent
    success = True
    for path in src_dir.rglob("*.py"):
        if any(x in str(path) for x in [".old", ".venv", "build", "dist"]): continue
        errs = check_file_static_symbols(path, src_dir)
        if errs:
            print(f"\n[FAIL] {path}")
            for e in errs: print(f"  - {e}")
            success = False
    if not success: sys.exit(1)
    sys.exit(0)
"@
    $ImportCheckCode | Out-File -FilePath (Join-Path $BuildDir "import_check.py") -Encoding utf8

    Write-Host "   -> Syntax Check..." -NoNewline
    $SyntaxScript = (Join-Path $BuildDir "syntax_check.py")
    $SrcRoot = $SrcPatcherDir.Parent.FullName
    & $VenvPython "$SyntaxScript" "$SrcRoot"
    if ($LASTEXITCODE -ne 0) { Write-Host " FAILED" -ForegroundColor Red; exit 1 }
    Write-Host " PASSED" -ForegroundColor Gray

    Write-Host "   -> Name Check..." -NoNewline
    $NameScript = (Join-Path $BuildDir "name_check.py")
    & $VenvPython "$NameScript" "$SrcRoot"
    if ($LASTEXITCODE -ne 0) { Write-Host " FAILED" -ForegroundColor Red; exit 1 }
    Write-Host " PASSED" -ForegroundColor Gray

    Write-Host "   -> Import/Symbol Check..." -NoNewline
    $ImportScript = (Join-Path $BuildDir "import_check.py")
    & $VenvPython "$ImportScript" "$SrcRoot"
    if ($LASTEXITCODE -ne 0) { Write-Host " FAILED" -ForegroundColor Red; exit 1 }
    Write-Host " PASSED" -ForegroundColor Gray

    # Automatically update the hardcoded fallback version in patcher main.py
    if ($Version -and $Version -match "^v") {
        $MainPyPath = Join-Path $SrcPatcherDir "main.py"
        if (Test-Path $MainPyPath) {
            Write-Host "   -> Updating hardcoded fallback version in patcher main.py to $Version..." -ForegroundColor Cyan
            $MainPyContent = Get-Content $MainPyPath -Raw
            $MainPyContent = $MainPyContent -replace 'CURRENT_VERSION = "v[^" ]+"', "CURRENT_VERSION = `"$Version`""
            [System.IO.File]::WriteAllText($MainPyPath, $MainPyContent)
        }
        
        $RedirectorMainPy = Join-Path $PSScriptRoot "src\yt_dlp_redirect\main.py"
        if (Test-Path $RedirectorMainPy) {
            Write-Host "   -> Updating hardcoded fallback version in redirector main.py to $Version..." -ForegroundColor Cyan
            $RedirContent = Get-Content $RedirectorMainPy -Raw
            $RedirContent = $RedirContent -replace 'WRAPPER_VERSION = "v[^" ]+"', "WRAPPER_VERSION = `"$Version`""
            $RedirContent = $RedirContent -replace 'BUILD_TYPE = "[^" ]*"', "BUILD_TYPE = `"$BuildType`""
            [System.IO.File]::WriteAllText($RedirectorMainPy, $RedirContent)
        }
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
    
    $RedirectorArgs = @(
        "--noconfirm",
        "--noupx",
        "--distpath", $RedirectorBuildDir,
        "--workpath", $RedirectorWorkDir,
        "--specpath", $BuildDir,
        "--name", "yt-dlp-wrapper"
    )
    if ($IconArg) { $RedirectorArgs += $IconArg }
    $RedirectorArgs += (Join-Path $PSScriptRoot "src\yt_dlp_redirect\main.py")

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
        "--name", "patcher"
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
