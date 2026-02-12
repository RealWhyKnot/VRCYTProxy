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
WRAPPER_VERSION = "v2026.02.12.dev-main-9fe7379" # Updated by build script
LOG_FILE_NAME = "wrapper.log"
CONFIG_FILE_NAME = "patcher_config.json"
WRAPPER_STATE_NAME = "wrapper_state.json"
ORIGINAL_YTDLP_FILENAME = "yt-dlp-og.exe"
LATEST_YTDLP_FILENAME = "yt-dlp-latest.exe"

APP_BASE_PATH = os.path.dirname(os.path.abspath(sys.argv[0]))
ORIGINAL_YTDLP_PATH = os.path.join(APP_BASE_PATH, ORIGINAL_YTDLP_FILENAME)
LATEST_YTDLP_PATH = os.path.join(APP_BASE_PATH, LATEST_YTDLP_FILENAME)
CONFIG_PATH = os.path.join(APP_BASE_PATH, CONFIG_FILE_NAME)
WRAPPER_STATE_PATH = os.path.join(APP_BASE_PATH, WRAPPER_STATE_NAME)

IS_DEV_BUILD = not getattr(sys, 'frozen', False)

DEFAULT_CONFIG = {
    "remote_server": "https://whyknot.dev",
    "always_proxy": False,
    "proxy_domains": ["youtube.com", "youtu.be"],
    "video_error_patterns": [
        "[Video Player] Failed to load",
        "VideoError",
        "Error loading video",
        "Failed to resolve"
    ],
    "preferred_max_height": 1080,
    "resolution_timeout": 5.0,
    "failure_retry_window": 15,
    "custom_user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

# --- Global State ---
CONFIG = DEFAULT_CONFIG

# --- Logging Setup ---
def setup_logging():
    log_file = os.path.join(APP_BASE_PATH, LOG_FILE_NAME)
    
    # Rotate log if it's too big (1MB)
    if os.path.exists(log_file) and os.path.getsize(log_file) > 1024 * 1024:
        try: os.remove(log_file)
        except: pass

    logging.basicConfig(
        level=logging.DEBUG if IS_DEV_BUILD else logging.INFO,
        format='%(asctime)s [%(levelname)s] %(message)s',
        handlers=[
            logging.FileHandler(log_file, encoding='utf-8'),
        ]
    )
    return logging.getLogger(WRAPPER_NAME)

logger = setup_logging()

# --- Config Handling ---
def load_config():
    global CONFIG
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                user_config = json.load(f)
                needs_save = False

                if not isinstance(user_config, dict):
                    raise ValueError("Config must be a JSON object")

                # MIGRATION: Rename proxy_all to always_proxy if found
                if "proxy_all" in user_config:
                    user_config["always_proxy"] = user_config.pop("proxy_all")
                    needs_save = True

                # Merge and detect if we need to save missing keys
                for k, v in DEFAULT_CONFIG.items():
                    if k not in user_config:
                        user_config[k] = v
                        needs_save = True

                config = user_config
                if needs_save:
                    with open(CONFIG_PATH, 'w', encoding='utf-8') as wf:
                        json.dump(config, wf, indent=2)
                return config
        except (json.JSONDecodeError, ValueError) as e:
            sys.stderr.write(f"Config file is invalid or corrupted: {e}. Regenerating defaults...\n")    
            with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
                json.dump(DEFAULT_CONFIG, f, indent=2)
        except Exception as e:
            sys.stderr.write(f"Unexpected error loading config: {e}\n")
    return DEFAULT_CONFIG

CONFIG = load_config()

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
        url = CONFIG.get("remote_server", "https://whyknot.dev") + "/api/status/ping"
        with urllib.request.urlopen(url, timeout=2.0) as response:
            return response.status == 200
    except:
        return False

def update_wrapper_success():
    """Clears persistent failure flags for this URL in state."""
    # Logic moved to patcher for robustness, but here for local state if needed
    pass

# --- Core Logic ---
def attempt_executable(executable_path, executable_name, incoming_args, use_custom_temp_dir=False, log_level=logging.INFO, log_prefix="YT-DLP"):
    """
    Executes a subprocess and captures/logs output in real-time.
    """
    if not os.path.exists(executable_path):
        logger.error(f"Executable not found: {executable_path}")
        return None, 1

    sanitized_args = [str(arg).replace('\0', '') for arg in incoming_args]
    command = [executable_path] + sanitized_args
    
    # Enhanced Debug Logging: Command + Environment
    logger.debug(f"Launching Executable: {executable_name}")
    logger.debug(f"Full Command: {subprocess.list2cmdline(command)}")

    process_env = os.environ.copy()

    if use_custom_temp_dir:
        process_env['TEMP'] = APP_BASE_PATH
        process_env['TMP'] = APP_BASE_PATH
        logger.debug(f"Env: TEMP/TMP set to: {APP_BASE_PATH}")

    try:
        creation_flags = 0
        if platform.system() == 'Windows':
            creation_flags = subprocess.CREATE_NO_WINDOW

        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=process_env,
            universal_newlines=True,
            encoding='utf-8',
            errors='replace',
            creationflags=creation_flags
        )
        
        stdout_lines = []
        stderr_lines = []

        def reader(stream, output_list):
            try:
                for line in iter(stream.readline, ''):
                    stripped_line = line.strip()
                    # Pass through the output to the logger
                    logger.log(log_level, f"[{log_prefix}] {stripped_line}")
                    output_list.append(stripped_line)
            except Exception as e:
                logger.debug(f"Reader error: {e}")

        stdout_thread = threading.Thread(target=reader, args=(process.stdout, stdout_lines))
        stderr_thread = threading.Thread(target=reader, args=(process.stderr, stderr_lines))
        
        stdout_thread.start()
        stderr_thread.start()

        return_code = process.wait()
        
        stdout_thread.join()
        stderr_thread.join()

        logger.debug(f"Process '{executable_name}' exited with code {return_code}.")

        final_url_output = ""
        for line in reversed(stdout_lines):
            if line.startswith('http'):
                final_url_output = line
                break
        
        return final_url_output, return_code

    except Exception as e:
        logger.error(f"Execution failed: {e}", exc_info=True)
        return None, 1

def process_and_execute(incoming_args):
    """3-Tier fallback logic."""
    logger.info(f"--- {WRAPPER_NAME} {WRAPPER_VERSION} ---")
    
    current_time = time.time()
    proxy_disabled = False
    t1_enabled = True
    t2_enabled = True
    t3_enabled = True
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
                        logger.warning(f"GLOBAL FALLBACK ACTIVE. Disabling Tier 1.")
                        proxy_disabled = True

                # Per-URL fallback/escalation and back-to-back detection
                failed_urls = state.get('failed_urls', {})
                if target_url in failed_urls:
                    failed_info = failed_urls[target_url]
                    last_req = failed_info.get('last_request_time', 0)
                    current_tier = failed_info.get('tier', 0)

                    # DETECTION LOGIC: If called again within the window, assume playback failure        
                    if current_time - last_req < retry_window:
                        current_tier = failed_info.get('tier', 0)
                        forced_tier = current_tier + 1
                        logger.warning(f"RAPID RETRY DETECTED (\u0394{current_time - last_req:.1f}s). Escalating: Tier {current_tier} -> {forced_tier}")
                    else:
                        if current_time < failed_info.get('expiry', 0):
                            forced_tier = failed_info.get('tier', 0)
                            if forced_tier > 0:
                                logger.warning(f"PREVIOUS FAILURE REMEMBERED. Escalating to Tier {forced_tier}.")

                    # Update state
                    failed_info['last_request_time'] = current_time
                    failed_info['tier'] = forced_tier
                    failed_info['expiry'] = current_time + 300
                    state['failed_urls'][target_url] = failed_info

                    with open(WRAPPER_STATE_PATH, 'w') as f:
                        json.dump(state, f)
                    
                    if forced_tier >= 1: proxy_disabled = True

        except Exception as e:
            logger.error(f"State Update Error: {e}")

    # Health check the proxy
    if not proxy_disabled and t1_enabled:
        if not check_proxy_online():
            logger.warning("PROXY OFFLINE. Disabling Tier 1.")
            proxy_disabled = True

    if not target_url:
        logger.debug("No URL found. Defaulting to Native.")
        final_output, return_code = attempt_executable(ORIGINAL_YTDLP_PATH, ORIGINAL_YTDLP_FILENAME, incoming_args)
        if final_output:
            safe_print(final_output)
        return return_code

    REMOTE_SERVER_BASE = CONFIG.get("remote_server", "https://whyknot.dev")
    is_already_proxied = target_url and target_url.startswith(REMOTE_SERVER_BASE)

    # Expand proxied domains based on config
    always_proxy = CONFIG.get("always_proxy", False)
    proxy_domains = CONFIG.get("proxy_domains", [])
    res_timeout = CONFIG.get("resolution_timeout", 5.0)
    custom_ua = CONFIG.get("custom_user_agent")

    should_proxy = False
    if target_url and not is_already_proxied and t1_enabled:
        if always_proxy:
            should_proxy = True
        else:
            domain_pattern = "|".join([re.escape(d) for d in proxy_domains])
            if domain_pattern and re.search(domain_pattern, target_url, re.IGNORECASE):
                should_proxy = True

    logger.debug(f"Decisions: should_proxy={should_proxy}, is_already_proxied={is_already_proxied}, forced_tier={forced_tier}")

    if should_proxy and not proxy_disabled and forced_tier < 1:
        logger.debug(f"Tier 1 [PROXY]: Resolving {target_url}...")

        try:
            video_type = "va"
            for i, arg in enumerate(incoming_args):
                if arg == "--format" and i + 1 < len(incoming_args):
                    if "bestaudio" in incoming_args[i+1]: video_type = "a"
                    break

            resolve_url = f"{REMOTE_SERVER_BASE}/api/stream/resolve?url={quote_plus(target_url)}&video_type={video_type}"
            if custom_ua:
                resolve_url += f"&ua={quote_plus(custom_ua)}"

            logger.debug(f"API Request: {resolve_url}")
            req = urllib.request.Request(resolve_url, method='GET')

            with urllib.request.urlopen(req, timeout=res_timeout) as response:
                logger.debug(f"API Response Status: {response.status}")
                if response.status == 200:
                    raw_data = response.read().decode()
                    logger.debug(f"API Response Body: {raw_data}")
                    data = json.loads(raw_data)
                    new_url = data.get("stream_url")
                    status = data.get("status", "ready")

                    if status == "failed":
                        logger.error(f"TIER 1 FAILED: Server reported status 'failed'. Falling back.")   
                    elif new_url:
                        if status == "downloading":
                            logger.info(f"TIER 1: Video is currently processing on the server. Please wait 10-20 seconds and try again.")
                            # Return 0 to stop the wrapper from falling back to Tier 2
                            return 0
                        else:
                            logger.debug(f"TIER 1 SUCCESS: {new_url} (Status: {status})")
                            update_wrapper_success()
                            safe_print(new_url)
                            return 0
        except Exception as e:
            logger.error(f"TIER 1 FAILED: {e}")

    if is_already_proxied and not proxy_disabled and forced_tier < 1:
        logger.debug("TIER 1 PASSTHROUGH: URL already proxied.")
        update_wrapper_success()
        safe_print(target_url)
        return 0

    if forced_tier <= 1 and t2_enabled:
        logger.debug("Tier 2 [LOCAL LATEST]: Resolving...")

        tier_2_args = []
        skip_next = False
        max_height = CONFIG.get("preferred_max_height", 1080)

        for arg in incoming_args:
            if skip_next:
                skip_next = False
                continue
            if arg in ("--exp-allow", "--wild-allow"):
                skip_next = True
                continue
            tier_2_args.append(arg)

        # Signature Throttling Bypass Components
        tier_2_args.extend(["--remote-components", "ejs:github"])

        if "-f" not in tier_2_args and "--format" not in tier_2_args:
            tier_2_args.extend(["-f", f"bestvideo[height<={max_height}]+bestaudio/best[height<={max_height}]"])

        resolved_url, return_code = attempt_executable(
            LATEST_YTDLP_PATH,
            LATEST_YTDLP_FILENAME,
            tier_2_args,
            use_custom_temp_dir=True,
            log_level=logging.DEBUG
        )

        if return_code == 0 and resolved_url and resolved_url.startswith('http'):
            logger.debug(f"TIER 2 SUCCESS: {resolved_url}")
            safe_print(resolved_url)
            return 0
        else:
            logger.warning(f"TIER 2 FAILED: Code {return_code}")
            if target_url:
                try:
                    if os.path.exists(WRAPPER_STATE_PATH):
                        with open(WRAPPER_STATE_PATH, 'r') as f:
                            state = json.load(f)
                        if 'failed_urls' not in state: state['failed_urls'] = {}
                        state['failed_urls'][target_url] = {'expiry': time.time() + 300, 'tier': 2}      
                        with open(WRAPPER_STATE_PATH, 'w') as f:
                            json.dump(state, f)
                except: pass

    if t3_enabled:
        logger.debug("Tier 3 [NATIVE]: Resolving...")
        final_output, return_code = attempt_executable(
            ORIGINAL_YTDLP_PATH,
            ORIGINAL_YTDLP_FILENAME,
            incoming_args,
            log_level=logging.DEBUG
        )

        if return_code == 0 and final_output:
            logger.debug(f"TIER 3 SUCCESS: {final_output}")
            safe_print(final_output)
            return 0
        else:
            logger.warning(f"TIER 3 FAILED: Code {return_code}")

            # FINAL LAST RESORT
            if not is_already_proxied and not proxy_disabled and target_url and t1_enabled:
                logger.info("CRITICAL FALLBACK: Attempting Tier 1 as last resort.")
                try:
                    video_type = "va"
                    resolve_url = f"{REMOTE_SERVER_BASE}/api/stream/resolve?url={quote_plus(target_url)}&video_type={video_type}"
                    if custom_ua: resolve_url += f"&ua={quote_plus(custom_ua)}"

                    req = urllib.request.Request(resolve_url, method='GET')
                    with urllib.request.urlopen(req, timeout=res_timeout) as response:
                        if response.status == 200:
                            data = json.loads(response.read().decode())
                            new_url = data.get("stream_url")
                            if new_url:
                                logger.info(f"Last Resort Success: {new_url}")
                                safe_print(new_url)
                                return 0
                except: pass

                encoded_url = quote_plus(target_url)
                new_url = f"{REMOTE_SERVER_BASE}/stream?url={encoded_url}"
                logger.info(f"Last Resort Direct: {new_url}")
                safe_print(new_url)
                return 0

            logger.error(f"ALL TIERS FAILED: {target_url}")
            sys.stderr.write(f"Wrapper Error: All tiers failed.\n")

    return return_code

def main():
    try:
        return_code = process_and_execute(sys.argv[1:])
        sys.exit(return_code)
    except Exception:
        logger.exception("An unhandled exception occurred in the wrapper.")
        sys.exit(1)

if __name__ == '__main__':
    main()
