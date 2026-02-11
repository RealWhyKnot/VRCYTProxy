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
from logging import FileHandler

if platform.system() != 'Windows':
    print("FATAL: This wrapper is designed to run on Windows only.", file=sys.stderr)
    sys.exit(1)

REMOTE_SERVER_BASE = "https://whyknot.dev"

LATEST_YTDLP_FILENAME = "yt-dlp-latest.exe"
DENO_FILENAME = "deno.exe"

ORIGINAL_YTDLP_FILENAME = "yt-dlp-og.exe"

LOG_FILE_NAME = 'wrapper_debug.log'

def get_application_path():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

APP_BASE_PATH = get_application_path()
LOG_FILE_PATH = os.path.join(APP_BASE_PATH, LOG_FILE_NAME)
WRAPPER_STATE_PATH = os.path.join(APP_BASE_PATH, 'wrapper_state.json')
WRAPPER_CONFIG_PATH = os.path.join(APP_BASE_PATH, 'wrapper_config.json')

LATEST_YTDLP_PATH = os.path.join(APP_BASE_PATH, LATEST_YTDLP_FILENAME)
ORIGINAL_YTDLP_PATH = os.path.join(APP_BASE_PATH, ORIGINAL_YTDLP_FILENAME)
DENO_PATH = os.path.join(APP_BASE_PATH, DENO_FILENAME)

IS_DEV_BUILD = not getattr(sys, 'frozen', False)

DEFAULT_CONFIG = {
    "remote_server": "https://whyknot.dev",
    "proxy_all": False,
    "proxy_domains": ["youtube.com", "youtu.be", "twitch.tv", "vrcdn.live", "vrcdn.video"],
    "video_error_patterns": [
        "[Video Player] Failed to load",
        "VideoError",
        "[AVProVideo] Error",
        "[VideoTXL] Error",
        "Loading failed"
    ],
    "instance_patterns": {
        "invite": "~private",
        "friends+": "~hidden",
        "friends": "~friends",
        "group_public": "groupAccessType(public)",
        "group_plus": "groupAccessType(plus)",
        "group": "~group"
    },
    "proxy_domain": "whyknot.dev",
    "vrchat_log_dir": os.path.join(os.environ.get('USERPROFILE', ''), 'AppData', 'LocalLow', 'VRChat', 'VRChat'),
    "debug_mode": IS_DEV_BUILD,
    "failure_retry_window": 15,
    "resolution_timeout": 5.0,
    "preferred_max_height": 1080,
    "enable_tier1_proxy": True,
    "enable_tier2_local": True,
    "enable_tier3_native": True,
    "custom_user_agent": None,
    "force_patch_in_public": False
}

def load_config():
    needs_save = False
    config = DEFAULT_CONFIG.copy()
    
    if os.path.exists(WRAPPER_CONFIG_PATH):
        try:
            with open(WRAPPER_CONFIG_PATH, 'r') as f:
                user_config = json.load(f)
                
                if not isinstance(user_config, dict):
                    raise ValueError("Config must be a JSON object")
                    
                # Merge and detect if we need to save missing keys
                for k, v in DEFAULT_CONFIG.items():
                    if k not in user_config:
                        user_config[k] = v
                        needs_save = True
                config = user_config
        except (json.JSONDecodeError, ValueError) as e:
            sys.stderr.write(f"Config file is invalid or corrupted: {e}. Regenerating defaults...\n")
            needs_save = True
        except Exception as e:
            sys.stderr.write(f"Unexpected error loading config: {e}. Using defaults.\n")
    else:
        needs_save = True

    if needs_save:
        try:
            with open(WRAPPER_CONFIG_PATH, 'w') as f:
                json.dump(config, f, indent=4)
        except Exception as e:
            sys.stderr.write(f"Failed to save config: {e}\n")
            
    return config

CONFIG = load_config()
REMOTE_SERVER_BASE = CONFIG.get("remote_server", "https://whyknot.dev")

def setup_logging():
    logger = logging.getLogger('RedirectWrapper')
    
    # Logic: If debug_mode is True, log everything (DEBUG). 
    # If False, log only ERROR and above.
    is_debug = CONFIG.get("debug_mode", False)
    level = logging.DEBUG if is_debug else logging.ERROR
    logger.setLevel(level)
    
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    
    try:
        # Append mode for logs
        handler = FileHandler(LOG_FILE_PATH, mode='a', encoding='utf-8')
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    except Exception as e:
        sys.stderr.write(f"FATAL: Could not set up file logging: {e}\n")
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        
    return logger

logger = setup_logging()

def safe_print(text):
    try:
        print(text, flush=True)
    except OSError as e:
        logger.error(f"Failed to print to stdout (VRChat pipe likely closed): {e}")

def update_wrapper_success():
    try:
        if os.path.exists(WRAPPER_STATE_PATH):
            with open(WRAPPER_STATE_PATH, 'r') as f:
                state = json.load(f)
            if state.get('consecutive_errors', 0) > 0 or state.get('force_fallback', False):
                state['consecutive_errors'] = 0
                state['force_fallback'] = False
                with open(WRAPPER_STATE_PATH, 'w') as f:
                    json.dump(state, f)
                logger.info("Successfully reset proxy error state.")
    except Exception: pass

def check_proxy_online():
    try:
        logger.info(f"Checking if proxy is online: {REMOTE_SERVER_BASE}")
        req = urllib.request.Request(REMOTE_SERVER_BASE, method='HEAD')
        with urllib.request.urlopen(req, timeout=2.0) as response:
            return response.status == 200
    except urllib.error.HTTPError as e:
        if e.code >= 500:
            logger.error(f"Proxy Server Error (5xx): {e.code}. This will trigger immediate fallback.")
        else:
            logger.warning(f"Proxy returned HTTP Error: {e.code}")
        return False
    except Exception as e:
        logger.warning(f"Proxy health check failed: {e}")
        return False

def find_url_in_args(args_list):
    for arg in args_list:
        if arg.startswith('http'):
            return arg
    return None

def attempt_executable(executable_path, executable_name, incoming_args, use_custom_temp_dir=False):
    
    if not os.path.exists(executable_path):
        logger.error(f"Executable '{executable_name}' not found at '{executable_path}'.")
        return None, -1

    sanitized_args = [str(arg).replace('\0', '') for arg in incoming_args]
    command = [executable_path] + sanitized_args
    logger.info(f"Executing command: {subprocess.list2cmdline(command)}")
    
    process_env = os.environ.copy()
    
    if use_custom_temp_dir:
        process_env['TEMP'] = APP_BASE_PATH
        process_env['TMP'] = APP_BASE_PATH
        logger.info(f"Setting TEMP/TMP (temp dir) to: {APP_BASE_PATH}")
    
    try:
        creation_flags = 0
        if platform.system() == 'Windows':
            creation_flags = 0x08000000 # CREATE_NO_WINDOW

        process = subprocess.Popen(
            command, 
            stdout=subprocess.PIPE, 
            stderr=subprocess.PIPE, 
            text=True, 
            encoding='utf-8', 
            errors='replace',
            env=process_env,
            creationflags=creation_flags
        )
    except OSError as e:
        logger.error(f"Failed to launch executable '{executable_name}': {e}")
        return None, -1

    stdout_lines = []
    stderr_lines = []

    def log_stream(stream, log_level, log_prefix, output_list):
        try:
            for line in iter(stream.readline, ''):
                stripped_line = line.strip()
                logger.log(log_level, f"[{log_prefix}] {stripped_line}")
                output_list.append(stripped_line)
        except Exception as e:
            logger.error(f"Error reading stream from {log_prefix}: {e}")
        finally:
            stream.close()

    stdout_thread = threading.Thread(target=log_stream, args=(process.stdout, logging.INFO, f"{executable_name}-stdout", stdout_lines))
    stderr_thread = threading.Thread(target=log_stream, args=(process.stderr, logging.ERROR, f"{executable_name}-stderr", stderr_lines))
    
    stdout_thread.start()
    stderr_thread.start()
    
    return_code = process.wait()
    stdout_thread.join()
    stderr_thread.join()
    
    logger.info(f"Executable '{executable_name}' finished with exit code {return_code}.")
    
    final_url_output = ""
    for line in reversed(stdout_lines):
        if line:
            final_url_output = line
            break
            
    return final_url_output, return_code

def process_and_execute(incoming_args):
    proxy_disabled = False
    forced_tier = 0 # 0 = Normal, 1 = Skip Tier 1, 2 = Skip Tier 1&2
    
    target_url = find_url_in_args(incoming_args)
    logger.debug(f"URL found in arguments: {target_url}")

    # Configuration Checks
    retry_window = CONFIG.get("failure_retry_window", 15)
    t1_enabled = CONFIG.get("enable_tier1_proxy", True)
    t2_enabled = CONFIG.get("enable_tier2_local", True)
    t3_enabled = CONFIG.get("enable_tier3_native", True)

    # Check for fallback state
    current_time = time.time()
    if os.path.exists(WRAPPER_STATE_PATH):
        try:
            with open(WRAPPER_STATE_PATH, 'r') as f:
                state = json.load(f)
                
                # Global fallback
                if state.get('force_fallback', False):
                    fallback_until = state.get('fallback_until', 0)
                    if current_time < fallback_until:
                        logger.warning(f"Global Fallback mode active. Disabling Tier 1 (Proxy).")
                        proxy_disabled = True
                
                # Per-URL fallback/escalation and back-to-back detection
                if target_url:
                    if 'failed_urls' not in state: state['failed_urls'] = {}
                    
                    failed_info = state['failed_urls'].get(target_url, {})
                    last_req = failed_info.get('last_request_time', 0)
                    
                    # DETECTION LOGIC: If called again within the window, assume playback failure
                    if current_time - last_req < retry_window:
                        # Increment tier based on previous failure or just start escalating
                        current_tier = failed_info.get('tier', 0)
                        forced_tier = current_tier + 1
                        logger.warning(f"Back-to-back request detected for {target_url} (Interval: {current_time - last_req:.2f}s). Escalating to Tier {forced_tier}.")
                    else:
                        # Normal request, but might still have a persistent failure tier
                        if current_time < failed_info.get('expiry', 0):
                            forced_tier = failed_info.get('tier', 0)
                            if forced_tier > 0:
                                logger.warning(f"URL previously failed at Tier {forced_tier-1}. Escalating to Tier {forced_tier}.")

                    # Update state with current request info
                    failed_info['last_request_time'] = current_time
                    failed_info['tier'] = forced_tier
                    # Keep the failure/escalation "remembered" for 5 minutes
                    failed_info['expiry'] = current_time + 300
                    state['failed_urls'][target_url] = failed_info
                    
                    with open(WRAPPER_STATE_PATH, 'w') as f:
                        json.dump(state, f)
                    
                    if forced_tier >= 1: proxy_disabled = True

        except Exception as e:
            logger.error(f"Failed to read/update wrapper state: {e}")

    # Health check the proxy if not already disabled
    if not proxy_disabled and t1_enabled:
        if not check_proxy_online():
            logger.warning("Proxy is offline or unreachable. Disabling Tier 1 (Proxy).")
            proxy_disabled = True

    if not target_url:
        logger.debug("No URL found in arguments. Passing to VRChat's yt-dlp as a fallback.")
        final_output, return_code = attempt_executable(ORIGINAL_YTDLP_PATH, ORIGINAL_YTDLP_FILENAME, incoming_args)
        if final_output:
            safe_print(final_output)
        return return_code

    is_already_proxied = target_url and target_url.startswith(REMOTE_SERVER_BASE)
    
    # Expand proxied domains based on config
    proxy_all = CONFIG.get("proxy_all", False)
    proxy_domains = CONFIG.get("proxy_domains", [])
    res_timeout = CONFIG.get("resolution_timeout", 5.0)
    custom_ua = CONFIG.get("custom_user_agent")
    
    should_proxy = False
    if target_url and not is_already_proxied and t1_enabled:
        if proxy_all:
            should_proxy = True
        else:
            domain_pattern = "|".join([re.escape(d) for d in proxy_domains])
            if domain_pattern and re.search(domain_pattern, target_url, re.IGNORECASE):
                should_proxy = True
    
    logger.debug(f"Analysis: Should Proxy? {bool(should_proxy)}. Is already proxied? {is_already_proxied}. Proxy Disabled? {proxy_disabled}. Forced Tier: {forced_tier}")

    if should_proxy and not proxy_disabled and forced_tier < 1:
        logger.debug(f"Tier 1: Proxyable URL detected ({target_url}). Resolving via server...")
        
        try:
            # Find video type in args
            video_type = "va"
            for i, arg in enumerate(incoming_args):
                if arg == "--format" and i + 1 < len(incoming_args):
                    fmt_val = incoming_args[i+1]
                    if "bestvideo" in fmt_val and "bestaudio" not in fmt_val:
                        video_type = "v"
                    elif "bestaudio" in fmt_val and "bestvideo" not in fmt_val:
                        video_type = "a"

            resolve_url = f"{REMOTE_SERVER_BASE}/api/stream/resolve?url={quote_plus(target_url)}&video_type={video_type}"
            if custom_ua:
                resolve_url += f"&ua={quote_plus(custom_ua)}"

            req = urllib.request.Request(resolve_url, method='GET')
            
            with urllib.request.urlopen(req, timeout=res_timeout) as response:
                if response.status == 200:
                    data = json.loads(response.read().decode())
                    new_url = data.get("stream_url")
                    if new_url:
                        logger.debug(f"Tier 1 success. Resolved to: {new_url}")
                        update_wrapper_success()
                        safe_print(new_url)
                        return 0
        except Exception as e:
            logger.error(f"Tier 1 resolution failed: {e}")
            # Fall through to Tier 2

    if is_already_proxied and not proxy_disabled and forced_tier < 1:
        logger.debug("Tier 1: URL is already proxied. Passing through directly.")
        update_wrapper_success()
        safe_print(target_url) 
        return 0
    
    if forced_tier <= 1 and t2_enabled:
        logger.debug("Tier 2: Attempting to resolve with yt-dlp-latest.exe...")
        
        tier_2_args = []
        skip_next = False
        max_height = CONFIG.get("preferred_max_height", 1080)

        for arg in incoming_args:
            if skip_next:
                skip_next = False
                continue
            
            if arg in ("--exp-allow", "--wild-allow"):
                logger.warning(f"Removing unsupported VRChat argument: {arg}")
                skip_next = True 
                continue
            
            tier_2_args.append(arg)

        # Inject Max Height if not explicitly complex
        if "-f" not in tier_2_args and "--format" not in tier_2_args:
            tier_2_args.extend(["-f", f"bestvideo[height<={max_height}]+bestaudio/best[height<={max_height}]"])

        if custom_ua:
            tier_2_args.extend(["--user-agent", custom_ua])

        js_runtime_arg = f"deno:{DENO_PATH}"
        tier_2_args.extend(["--js-runtimes", js_runtime_arg])

        resolved_url, return_code = attempt_executable(
            LATEST_YTDLP_PATH, 
            LATEST_YTDLP_FILENAME, 
            tier_2_args, 
            use_custom_temp_dir=True 
        )
        
        if return_code == 0 and resolved_url and resolved_url.startswith('http'):
            logger.debug(f"Tier 2 success. Returning URL: {resolved_url}")
            safe_print(resolved_url)
            return 0
        else:
            logger.warning(f"Tier 2 failed (Code: {return_code}) or returned invalid URL. Output: {resolved_url}")
            # Communicate failure
            if target_url:
                try:
                    if os.path.exists(WRAPPER_STATE_PATH):
                        with open(WRAPPER_STATE_PATH, 'r') as f:
                            state = json.load(f)
                        if 'failed_urls' not in state: state['failed_urls'] = {}
                        state['failed_urls'][target_url] = {
                            'expiry': time.time() + 300,
                            'tier': 2
                        }
                        with open(WRAPPER_STATE_PATH, 'w') as f:
                            json.dump(state, f)
                except Exception: pass

    if t3_enabled:
        logger.debug("Tier 3: Falling back to VRChat's yt-dlp-og.exe...")
        final_output, return_code = attempt_executable(
            ORIGINAL_YTDLP_PATH, 
            ORIGINAL_YTDLP_FILENAME, 
            incoming_args 
        )
        
        if return_code == 0 and final_output:
            logger.debug(f"Tier 3 finished. Returning output to VRChat: {final_output}")
            safe_print(final_output)
            return 0
        else:
            logger.warning(f"Tier 3 failed (Code: {return_code}) or produced no output.")
            
            # FINAL LAST RESORT: Try Tier 1 (Proxy) even if we normally wouldn't
            if not is_already_proxied and not proxy_disabled and target_url and t1_enabled:
                logger.info("CRITICAL FALLBACK: Tier 3 failed. Attempting Tier 1 (Proxy) as absolute last resort.")
                try:
                    video_type = "va" # Default for last resort
                    resolve_url = f"{REMOTE_SERVER_BASE}/api/stream/resolve?url={quote_plus(target_url)}&video_type={video_type}"
                    if custom_ua:
                        resolve_url += f"&ua={quote_plus(custom_ua)}"
                        
                    req = urllib.request.Request(resolve_url, method='GET')
                    with urllib.request.urlopen(req, timeout=res_timeout) as response:
                        if response.status == 200:
                            data = json.loads(response.read().decode())
                            new_url = data.get("stream_url")
                            if new_url:
                                logger.info(f"Last resort resolution success: {new_url}")
                                safe_print(new_url)
                                return 0
                except: pass
                
                # Old redirect style as literal last resort
                encoded_url = quote_plus(target_url)
                new_url = f"{REMOTE_SERVER_BASE}/stream?url={encoded_url}"
                logger.info(f"Last resort direct redirect: {new_url}")
                safe_print(new_url)
                return 0

            logger.error(f"All tiers failed for URL: {target_url}")
            sys.stderr.write(f"Wrapper Error: All tiers failed. Check {LOG_FILE_NAME}\n")

    return return_code

def main():
    logger.info("--- VRChat yt-dlp Wrapper Initialized ---")
    logger.info(f"Arguments received: {sys.argv[1:]}")
    logger.info(f"Tier 1 (Proxy): {REMOTE_SERVER_BASE}")
    logger.info(f"Tier 2 (Latest): {LATEST_YTDLP_PATH}")
    logger.info(f"Tier 2 (Deno): {DENO_PATH}")
    logger.info(f"Tier 3 (VRChat): {ORIGINAL_YTDLP_PATH}")
    
    try:
        return_code = process_and_execute(sys.argv[1:])
        sys.exit(return_code)
    except Exception:
        logger.exception("An unhandled exception occurred in the wrapper.")
        sys.exit(1)

if __name__ == '__main__':
    main()