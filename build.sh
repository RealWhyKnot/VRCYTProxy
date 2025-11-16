#!/bin/bash
set -e

LOG_FILE="build_log.txt"

# Use a function to group all commands, then pipe the output of the function to tee
main() {
    echo "================================================="
    echo "     WKYoutubeProxy Unified Build Script         "
    echo "================================================="
    echo

    PYTHON_EXE="python3"
    BUILD_DIR="build"
    DIST_DIR="dist"
    REDIRECTOR_BUILD_DIR="$BUILD_DIR/redirector_build"
    PATCHER_BUILD_DIR="$BUILD_DIR/patcher_build"

    # --- Safety Checks ---
    if [ "$BUILD_DIR" != "build" ] || [ "$DIST_DIR" != "dist" ]; then
        echo "SAFETY FAIL: Build directories are not set to default values. Aborting."
        exit 1
    fi

    # --- Step 1: Environment Setup ---
    echo "[1/5] Setting up Python environment..."
    if [ ! -d ".venv" ]; then
        $PYTHON_EXE -m venv .venv
    fi
    source .venv/bin/activate
    
    VENV_PYTHON=".venv/bin/python"

    echo
    echo "[Step 2/5] Installing dependencies (with custom bootloader)..."
    echo "NOTE: This step compiles the PyInstaller bootloader and requires a C compiler (e.g., GCC)."
    echo
    export PYINSTALLER_COMPILE_BOOTLOADER=1
    $VENV_PYTHON -m pip install --upgrade pip
    pip install --force-reinstall --no-cache-dir pyinstaller
    echo "Environment ready."
    echo

    # --- Step 3: Clean Directories ---
    echo "[3/5] Cleaning previous build and dist directories..."
    rm -rf "$BUILD_DIR" "$DIST_DIR"
    mkdir -p "$BUILD_DIR" "$DIST_DIR"
    echo "Directories cleaned."
    echo

    # --- Step 4: Build Components ---
    echo "[4/5] Building executables (folder mode)..."
    echo "  -> Building Redirector..."
    pyinstaller \
        --noconfirm \
        --noupx \
        --distpath "$REDIRECTOR_BUILD_DIR" \
        --workpath "$BUILD_DIR/redirector_work" \
        --specpath "$BUILD_DIR" \
        --name "main" \
        src/yt_dlp_redirect/main.py
    echo "  -> Redirector build complete."

    echo "  -> Building Patcher..."
    pyinstaller \
        --noconfirm \
        --noupx \
        --distpath "$PATCHER_BUILD_DIR" \
        --workpath "$BUILD_DIR/patcher_work" \
        --specpath "$BUILD_DIR" \
        --name "patcher" \
        src/patcher/main.py
    echo "  -> Patcher build complete."
    echo

    # --- Step 5: Assemble Final Application ---
    echo "[5/5] Assembling final application in '$DIST_DIR'..."
    # Copy contents of the patcher build into the root of dist
    cp -r "$PATCHER_BUILD_DIR"/patcher/* "$DIST_DIR/"
    # Create the resources structure and copy the redirector build into it
    mkdir -p "$DIST_DIR/resources"
    cp -r "$REDIRECTOR_BUILD_DIR"/main "$DIST_DIR"/resources/wrapper_files
    echo "Assembly complete."
    echo

    # Final cleanup is handled outside the logged function

    # --- Success ---
    echo "================================================="
    echo "     BUILD SUCCEEDED!                            "
    echo "     Final application is in: $DIST_DIR          "
    echo "================================================="
}

# Execute the main function and pipe its output to tee, which logs and prints.
main | tee "$LOG_FILE"

# Final cleanup
echo "Cleaning up intermediate and anomalous files..."
rm -rf "build"
rm -f Building patcher redirector
echo "Cleanup complete."
