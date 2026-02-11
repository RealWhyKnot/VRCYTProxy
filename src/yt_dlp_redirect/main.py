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

DEFAULT_CONFIG = {
    "remote_server": "https://whyknot.dev",
    "proxy_all": False,
    "proxy_domains": ["youtube.com", "youtu.be", "twitch.tv", "vrcdn.live", "vrcdn.video"]
}

def load_config():
    if os.path.exists(WRAPPER_CONFIG_PATH):
        try:
            with open(WRAPPER_CONFIG_PATH, 'r') as f:
                user_config = json.load(f)
                config = DEFAULT_CONFIG.copy()
                config.update(user_config)
                return config
        except Exception as e:
            logger.error(f"Failed to load config: {e}")
    
    # Save default config if not exists
    try:
        with open(WRAPPER_CONFIG_PATH, 'w') as f:
            json.dump(DEFAULT_CONFIG, f, indent=4)
    except: pass
    return DEFAULT_CONFIG

CONFIG = load_config()
REMOTE_SERVER_BASE = CONFIG.get("remote_server", "https://whyknot.dev")

def setup_logging():
    logger = logging.getLogger('RedirectWrapper')
    logger.setLevel(logging.INFO)
    
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    
    try:
        handler = FileHandler(LOG_FILE_PATH, mode='w', encoding='utf-8')
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
    logger.info(f"URL found in arguments: {target_url}")

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
                    
                    # DETECTION LOGIC: If called again within 15s, assume playback failure
                    if current_time - last_req < 15:
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
    if not proxy_disabled:
        if not check_proxy_online():
            logger.warning("Proxy is offline or unreachable. Disabling Tier 1 (Proxy).")
            proxy_disabled = True

    if not target_url:
        logger.warning("No URL found in arguments. Passing to VRChat's yt-dlp as a fallback.")
        final_output, return_code = attempt_executable(ORIGINAL_YTDLP_PATH, ORIGINAL_YTDLP_FILENAME, incoming_args)
        if final_output:
            safe_print(final_output)
        return return_code

    is_already_proxied = target_url and target_url.startswith(REMOTE_SERVER_BASE)
    
    # Expand proxied domains based on config
    proxy_all = CONFIG.get("proxy_all", False)
    proxy_domains = CONFIG.get("proxy_domains", [])
    
    should_proxy = False
    if target_url and not is_already_proxied:
        if proxy_all:
            should_proxy = True
        else:
            domain_pattern = "|".join([re.escape(d) for d in proxy_domains])
            if domain_pattern and re.search(domain_pattern, target_url, re.IGNORECASE):
                should_proxy = True
    
    logger.info(f"Analysis: Should Proxy? {bool(should_proxy)}. Is already proxied? {is_already_proxied}. Proxy Disabled? {proxy_disabled}. Forced Tier: {forced_tier}")

    if should_proxy and not proxy_disabled and forced_tier < 1:
        logger.info(f"Tier 1: Proxyable URL detected ({target_url}). Resolving via server...")
        
        try:
            # Use the more powerful /api/stream/resolve endpoint
            # Find video type in args
            video_type = "va"
            for i, arg in enumerate(incoming_args):
                if arg == "--format" and i + 1 < len(incoming_args):
                    if "bestvideo" in incoming_args[i+1] and "bestaudio" not in incoming_args[i+1]:
                        video_type = "v"
                    elif "bestaudio" in incoming_args[i+1] and "bestvideo" not in incoming_args[i+1]:
                        video_type = "a"

            resolve_url = f"{REMOTE_SERVER_BASE}/api/stream/resolve?url={quote_plus(target_url)}&video_type={video_type}"
            req = urllib.request.Request(resolve_url, method='GET')
            
            with urllib.request.urlopen(req, timeout=5.0) as response:
                if response.status == 200:
                    data = json.loads(response.read().decode())
                    new_url = data.get("stream_url")
                    if new_url:
                        logger.info(f"Tier 1 success. Resolved to: {new_url}")
                        update_wrapper_success()
                        safe_print(new_url)
                        return 0
        except Exception as e:
            logger.error(f"Tier 1 resolution failed: {e}")
            # Fall through to Tier 2

    if is_already_proxied and not proxy_disabled and forced_tier < 1:
        logger.info("Tier 1: URL is already proxied. Passing through directly.")
        update_wrapper_success()
        safe_print(target_url) 
        return 0
    
    if forced_tier <= 1:
        logger.info("Tier 2: Attempting to resolve with yt-dlp-latest.exe...")
        
        tier_2_args = []
        skip_next = False
        for arg in incoming_args:
            if skip_next:
                skip_next = False
                continue
            
            if arg in ("--exp-allow", "--wild-allow"):
                logger.warning(f"Removing unsupported VRChat argument: {arg}")
                skip_next = True 
                continue
                
            tier_2_args.append(arg)

        js_runtime_arg = f"deno:{DENO_PATH}"
        tier_2_args.extend(["--js-runtimes", js_runtime_arg])

        resolved_url, return_code = attempt_executable(
            LATEST_YTDLP_PATH, 
            LATEST_YTDLP_FILENAME, 
            tier_2_args, 
            use_custom_temp_dir=True 
        )
        
        if return_code == 0 and resolved_url and resolved_url.startswith('http'):
            logger.info(f"Tier 2 success. Returning URL: {resolved_url}")
            safe_print(resolved_url)
            return 0
        else:
            logger.warning(f"Tier 2 failed (Code: {return_code}) or returned invalid URL. Output: {resolved_url}")
            # If we were at Tier 2 and it failed, and we have a target URL, we should tell the patcher
            if target_url:
                # We need a way to communicate Tier 2 failure back to the patcher so it can escalate to Tier 3
                # For now, if the wrapper is running, it can't easily update the patcher's internal state
                # but it CAN update wrapper_state.json
                try:
                    if os.path.exists(WRAPPER_STATE_PATH):
                        with open(WRAPPER_STATE_PATH, 'r') as f:
                            state = json.load(f)
                        if 'failed_urls' not in state: state['failed_urls'] = {}
                        state['failed_urls'][target_url] = {
                            'expiry': time.time() + 300,
                            'tier': 2 # Mark that Tier 2 failed
                        }
                        with open(WRAPPER_STATE_PATH, 'w') as f:
                            json.dump(state, f)
                except Exception: pass

    logger.info("Tier 3: Falling back to VRChat's yt-dlp-og.exe...")
    final_output, return_code = attempt_executable(
        ORIGINAL_YTDLP_PATH, 
        ORIGINAL_YTDLP_FILENAME, 
        incoming_args 
    )
    
    if return_code == 0 and final_output:
        logger.info(f"Tier 3 finished. Returning output to VRChat: {final_output}")
        safe_print(final_output)
        return 0
    else:
        logger.warning(f"Tier 3 failed (Code: {return_code}) or produced no output.")
        
        # FINAL LAST RESORT: Try Tier 1 (Proxy) even if we normally wouldn't, 
        # as long as it's not already a proxy URL and proxy isn't globally dead.
        if not is_already_proxied and not proxy_disabled and target_url:
            logger.info("CRITICAL FALLBACK: Tier 3 failed. Attempting Tier 1 (Proxy) as absolute last resort.")
            try:
                resolve_url = f"{REMOTE_SERVER_BASE}/api/stream/resolve?url={quote_plus(target_url)}"
                req = urllib.request.Request(resolve_url, method='GET')
                with urllib.request.urlopen(req, timeout=5.0) as response:
                    if response.status == 200:
                        data = json.loads(response.read().decode())
                        new_url = data.get("stream_url")
                        if new_url:
                            logger.info(f"Last resort resolution success: {new_url}")
                            safe_print(new_url)
                            return 0
            except: pass
            
            # If API fails, fall back to the old redirect style as a literal last resort
            encoded_url = quote_plus(target_url)
            new_url = f"{REMOTE_SERVER_BASE}/stream?url={encoded_url}"
            logger.info(f"Last resort direct rewrite: {new_url}")
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