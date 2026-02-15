import msvcrt
import sys
import os
import logging
import subprocess
import re
import threading
import platform
import json
import time
import urllib.request
import urllib.error
from urllib.parse import quote_plus

# --- Constants ---
WRAPPER_NAME = "yt-dlp-wrapper"
WRAPPER_VERSION = "v2026.02.15.dev-main-aa72022" # Updated by build script
BUILD_TYPE = "" # Updated by build script
LOG_FILE_NAME = "wrapper.log"
CONFIG_FILE_NAME = "patcher_config.json"
WRAPPER_STATE_NAME = "wrapper_state.json"
ORIGINAL_YTDLP_FILENAME = "yt-dlp-og.exe"
LATEST_YTDLP_FILENAME = "yt-dlp-latest.exe"

# Robust path detection for onedir builds
if getattr(sys, 'frozen', False):
    # If frozen, sys.executable is the full path to the exe
    APP_BASE_PATH = os.path.abspath(os.path.dirname(sys.executable))
else:
    APP_BASE_PATH = os.path.dirname(os.path.abspath(__file__))

ORIGINAL_YTDLP_PATH = os.path.join(APP_BASE_PATH, ORIGINAL_YTDLP_FILENAME)
LATEST_YTDLP_PATH = os.path.join(APP_BASE_PATH, LATEST_YTDLP_FILENAME)
CONFIG_PATH = os.path.join(APP_BASE_PATH, CONFIG_FILE_NAME)
WRAPPER_STATE_PATH = os.path.join(APP_BASE_PATH, WRAPPER_STATE_NAME)

IS_DEV_BUILD = not getattr(sys, 'frozen', False)

DEFAULT_CONFIG = {
    "use_test_version": False,
    "video_error_patterns": [
        "[Video Player] Failed to load",
        "VideoError",
        "[AVProVideo] Error",
        "[VideoTXL] Error",
        "Loading failed",
        "PlayerError"
    ],
    "preferred_max_height": 1080,
    "resolution_timeout": 5.0,
    "failure_retry_window": 15,
    "custom_user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

# --- Global State ---
logger = None
CONFIG = None

# --- Logging Setup ---
def setup_logging():
    # Use APP_BASE_PATH to ensure it's in the same folder as the executable (Tools)
    log_file = os.path.join(APP_BASE_PATH, LOG_FILE_NAME)
    
    # Check config for debug mode early
    is_debug = IS_DEV_BUILD
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                cfg = json.load(f)
                is_debug = cfg.get("debug_mode", IS_DEV_BUILD)
        except: pass

    # Rotate log if it's too big (1MB)
    if os.path.exists(log_file) and os.path.getsize(log_file) > 1024 * 1024:
        try: os.remove(log_file)
        except: pass

    level = logging.DEBUG if is_debug else logging.INFO
    
    # Configure root logger
    logging.basicConfig(
        level=level,
        format='%(asctime)s [%(levelname)s] %(message)s',
        handlers=[
            logging.FileHandler(log_file, mode='w', encoding='utf-8'),
        ]
    )
    
    l = logging.getLogger(WRAPPER_NAME)
    l.setLevel(level)
    return l

# --- Config Handling ---
def load_config():
    global CONFIG
    logger.debug(f"Loading config from: {CONFIG_PATH}")
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                user_config = json.load(f)
                needs_save = False
                logger.debug("Successfully parsed config JSON.")

                if not isinstance(user_config, dict):
                    raise ValueError("Config must be a JSON object")

                # MIGRATION: Rename proxy_all to always_proxy if found
                if "proxy_all" in user_config:
                    user_config["always_proxy"] = user_config.pop("proxy_all")
                    needs_save = True
                    logger.debug("Migrated 'proxy_all' to 'always_proxy'.")

                # Merge and detect if we need to save missing keys
                for k, v in DEFAULT_CONFIG.items():
                    if k not in user_config:
                        user_config[k] = v
                        needs_save = True
                        logger.debug(f"Added missing config key: {k}")

                config = user_config
                if needs_save:
                    logger.debug("Saving updated config back to file...")
                    try:
                        with open(CONFIG_PATH, 'w', encoding='utf-8') as wf:
                            json.dump(config, wf, indent=2)
                    except Exception as e:
                        logger.debug(f"Failed to save config: {e}")
                return config
        except (json.JSONDecodeError, ValueError) as e:
            logger.error(f"Config file is invalid or corrupted: {e}. Using defaults...")
            try:
                with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
                    json.dump(DEFAULT_CONFIG, f, indent=2)
            except Exception: pass
        except Exception as e:
            logger.error(f"Unexpected error loading config: {e}")
    else:
        logger.debug("Config file not found, using defaults.")
    return DEFAULT_CONFIG

# --- Utilities ---
def safe_print(msg):
    """Ensures output is written to stdout cleanly."""
    try:
        sys.stdout.write(msg + '\n')
        sys.stdout.flush()
    except Exception:
        pass

def find_url_in_args(args):
    """Simple regex based URL finder in arguments."""
    url_pattern = re.compile(r'https?://[^\s<>"+]+|www\.[^\s<>"+]+')
    for arg in args:
        match = url_pattern.search(arg)
        if match:
            return match.group(0)
    return None

def check_proxy_online():
    """Fast check to see if our remote server is up."""
    try:
        domain = "test.whyknot.dev" if CONFIG.get("use_test_version", False) else "whyknot.dev"
        REMOTE_SERVER_BASE = f"https://{domain}"
        # The endpoint is actually /api/status/ping
        req = urllib.request.Request(f"{REMOTE_SERVER_BASE}/api/status/ping", method='GET')
        with urllib.request.urlopen(req, timeout=2.0) as response:
            return response.status == 200
    except:
        return False

def update_wrapper_success():
    """Clears global fallback if it was active."""
    try:
        if os.path.exists(WRAPPER_STATE_PATH):
            with open(WRAPPER_STATE_PATH, 'r') as f:
                state = json.load(f)
            state['consecutive_errors'] = 0
            state['force_fallback'] = False
            with open(WRAPPER_STATE_PATH, 'w') as f:
                json.dump(state, f)
    except:
        pass

def attempt_executable(path, executable_name, args, use_custom_temp_dir=False, log_level=logging.INFO):
    """Launches a subprocess and captures its output."""
    if not os.path.exists(path):
        logger.error(f"Executable not found: {path}")
        return None, 1

    try:
        env = os.environ.copy()
        if use_custom_temp_dir:
            temp_dir = os.path.join(APP_BASE_PATH, "_tmp")
            if not os.path.exists(temp_dir): os.makedirs(temp_dir)
            env['TMP'] = temp_dir
            env['TEMP'] = temp_dir

        cmd = [path] + args
        logger.debug(f"Executing: {' '.join(cmd)}")
        
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
            creationflags=subprocess.CREATE_NO_WINDOW if platform.system() == 'Windows' else 0
        )
        stdout, stderr = process.communicate()

        if stderr and log_level == logging.DEBUG:
            logger.debug(f"{executable_name} stderr: {stderr.strip()}")

        if process.returncode == 0:
            return stdout.strip(), 0
        else:
            return None, process.returncode
    except Exception as e:
        import traceback
        logger.error(f"Execution of {executable_name} failed: {e}")
        logger.error(traceback.format_exc())
        return None, 1

def process_and_execute(incoming_args):
    """3-Tier fallback logic (Modern -> Proxy -> Native)."""
    try:
        logger.info(f"--- {WRAPPER_NAME} {WRAPPER_VERSION} ---")
        
        current_time = time.time()
        proxy_disabled = False
        t1_modern_enabled = CONFIG.get("enable_tier1_modern", True) # NEW KEY
        t2_proxy_enabled = CONFIG.get("enable_tier2_proxy", True) # SWAPPED TIER
        t3_native_enabled = CONFIG.get("enable_tier3_native", True)
        
        forced_tier = 0 # 0 = Normal, 1 = Skip Tier 1, 2 = Skip Tier 1&2

        target_url = find_url_in_args(incoming_args)
        logger.debug(f"Targeting URL: {target_url}")

        # Configuration Checks
        retry_window = CONFIG.get("failure_retry_window", 15)
        
        if target_url:
            try:
                if os.path.exists(WRAPPER_STATE_PATH):
                    with open(WRAPPER_STATE_PATH, 'r') as f:
                        state = json.load(f)
                    
                    # Check for Global Fallback mode
                    if state.get('force_fallback', False):
                        fallback_until = state.get('fallback_until', 0)
                        if current_time < fallback_until:
                            logger.warning(f"GLOBAL FALLBACK ACTIVE. Disabling Tier 1 & 2.")
                            forced_tier = 2 # Start at Tier 3

                    # Per-URL fallback/escalation and back-to-back detection
                    failed_urls = state.get('failed_urls', {})
                    if target_url in failed_urls:
                        failed_info = failed_urls[target_url]
                        last_req = failed_info.get('last_request_time', 0)
                        current_tier = failed_info.get('tier', 0)

                        # DETECTION LOGIC: If called again within the window, assume playback failure        
                        if current_time - last_req < retry_window:
                            forced_tier = min(current_tier + 1, 3) # Cap at Tier 3
                            logger.warning(f"RAPID RETRY DETECTED (\u0394{current_time - last_req:.1f}s). Escalating: Tier {current_tier} -> {forced_tier}")
                        else:
                            if current_time < failed_info.get('expiry', 0):
                                forced_tier = failed_info.get('tier', 0)
                                if forced_tier > 0:
                                    logger.warning(f"PREVIOUS FAILURE REMEMBERED. Starting at Tier {forced_tier}.")

                    # Update state PRE-EXECUTION to track this attempt
                    failed_info = failed_urls.get(target_url, {'tier': 0, 'last_request_time': 0})
                    failed_info['last_request_time'] = current_time
                    failed_info['tier'] = forced_tier
                    failed_info['expiry'] = current_time + 300
                    state['failed_urls'][target_url] = failed_info
                    with open(WRAPPER_STATE_PATH, 'w') as f:
                        json.dump(state, f)

            except Exception as e:
                logger.error(f"State Update Error: {e}")

        # Health check the proxy if we might use it
        if forced_tier <= 1 and t2_proxy_enabled:
            if not check_proxy_online():
                logger.warning("PROXY OFFLINE. Disabling Tier 2.")
                t2_proxy_enabled = False

        if not target_url:
            logger.debug("No URL found. Defaulting to Native.")
            final_output, return_code = attempt_executable(ORIGINAL_YTDLP_PATH, ORIGINAL_YTDLP_FILENAME, incoming_args)
            if final_output: safe_print(final_output)
            return return_code

        # Determine remote server base based on test flag
        domain = "test.whyknot.dev" if CONFIG.get("use_test_version", False) else "whyknot.dev"
        REMOTE_SERVER_BASE = f"https://{domain}"
        
        res_timeout = CONFIG.get("resolution_timeout", 5.0)
        custom_ua = CONFIG.get("custom_user_agent")

        # --- TIER 1: MODERN (yt-dlp-latest + deno) ---
        if forced_tier <= 0 and t1_modern_enabled:
            logger.debug("Tier 1 [MODERN]: Resolving...")
            
            tier_1_args = []
            skip_next = False
            max_height = CONFIG.get("preferred_max_height", 1080)
            
            for arg in incoming_args:
                if skip_next:
                    skip_next = False
                    continue
                if arg in ("--exp-allow", "--wild-allow"):
                    skip_next = True
                    continue
                tier_1_args.append(arg)

            tier_1_args.extend(["--remote-components", "ejs:github"])
            if "-f" not in tier_1_args and "--format" not in tier_1_args:
                fmt = f"bestvideo[height<={max_height}]+bestaudio/best[height<={max_height}]"
                tier_1_args.extend(["-f", fmt])

            resolved_url, return_code = attempt_executable(
                LATEST_YTDLP_PATH, LATEST_YTDLP_FILENAME, tier_1_args,
                use_custom_temp_dir=True, log_level=logging.DEBUG
            )

            if return_code == 0 and resolved_url and resolved_url.startswith('http'):
                logger.debug(f"TIER 1 SUCCESS: {resolved_url}")
                update_wrapper_success()
                safe_print(resolved_url)
                return 0
            else:
                logger.warning(f"TIER 1 FAILED: Code {return_code}. Moving to Tier 2.")
                forced_tier = 1

        # --- TIER 2: PROXY (WhyKnot.dev) ---
        if forced_tier <= 1 and t2_proxy_enabled:
            logger.debug(f"Tier 2 [PROXY]: Resolving {target_url}...")
            try:
                video_type = "va"
                for i, arg in enumerate(incoming_args):
                    if arg == "--format" and i + 1 < len(incoming_args):
                        if "bestaudio" in incoming_args[i+1]: video_type = "a"
                        break

                resolve_url = f"{REMOTE_SERVER_BASE}/api/stream/resolve?url={quote_plus(target_url)}&video_type={video_type}"
                if custom_ua: resolve_url += f"&ua={quote_plus(custom_ua)}"

                logger.debug(f"API Request: {resolve_url}")
                req = urllib.request.Request(resolve_url, method='GET')
                with urllib.request.urlopen(req, timeout=res_timeout) as response:
                    if response.status == 200:
                        data = json.loads(response.read().decode())
                        new_url = data.get("stream_url")
                        status = data.get("status", "ready")

                        if status == "failed":
                            logger.error("TIER 2 FAILED: Server reported status 'failed'.")
                            forced_tier = 2
                        elif new_url:
                            logger.debug(f"TIER 2 SUCCESS: {new_url} (Status: {status})")
                            update_wrapper_success()
                            safe_print(new_url)
                            return 0
                    else:
                        logger.error(f"TIER 2 FAILED: HTTP {response.status}")
                        forced_tier = 2
            except Exception as e:
                logger.error(f"TIER 2 FAILED: {e}")
                forced_tier = 2

        # --- TIER 3: NATIVE (VRChat Original) ---
        # If Tier 1 was disabled (FORCE mode), we don't fall back to native.
        if t3_native_enabled:
            if not t1_modern_enabled:
                logger.warning("Tier 3 [NATIVE] SKIPPED: Force Tier 2 mode active.")
                return 1
            
            logger.debug("Tier 3 [NATIVE]: Resolving...")
            final_output, return_code = attempt_executable(
                ORIGINAL_YTDLP_PATH, ORIGINAL_YTDLP_FILENAME, incoming_args,
                log_level=logging.DEBUG
            )

            if return_code == 0 and final_output:
                logger.debug(f"TIER 3 SUCCESS")
                safe_print(final_output)
                return 0
            else:
                logger.error(f"ALL TIERS FAILED: {target_url}")
                sys.stderr.write(f"Wrapper Error: All tiers failed.\n")

        return return_code
    except Exception as e:
        import traceback
        logger.error(f"UNHANDLED ERROR in process_and_execute: {e}")
        logger.error(traceback.format_exc())
        return 1

def main():
    try:
        global logger
        logger = setup_logging()
        global CONFIG
        CONFIG = load_config()
        logger.debug(f"Wrapper launched with args: {sys.argv}")
        return_code = process_and_execute(sys.argv[1:])
        sys.exit(return_code)
    except Exception as e:
        import traceback
        error_msg = f"FATAL WRAPPER ERROR: {e}\n{traceback.format_exc()}"
        try: logger.critical(error_msg)
        except: pass
        try:
            with open(os.path.join(APP_BASE_PATH, "FATAL_ERROR.txt"), "w") as f: f.write(error_msg)
        except: pass
        sys.stderr.write(error_msg + "\n")
        sys.exit(1)

if __name__ == '__main__':
    main()
