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
from concurrent.futures import ThreadPoolExecutor, as_completed

# --- Constants ---
WRAPPER_NAME = "yt-dlp-wrapper"
WRAPPER_VERSION = "v2026.02.20.dev-production-f187016" 
BUILD_TYPE = "DEV" 
LOG_FILE_NAME = "wrapper.log"
CONFIG_FILE_NAME = "patcher_config.json"
WRAPPER_STATE_NAME = "wrapper_state.json"
ORIGINAL_YTDLP_FILENAME = "yt-dlp-og.exe"
LATEST_YTDLP_FILENAME = "yt-dlp-latest.exe"

# Robust path detection for onedir builds
if getattr(sys, 'frozen', False):
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
    "resolution_timeout": 10.0,
    "failure_retry_window": 15,
    "custom_user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "enable_tier1_modern": True,
    "enable_tier2_proxy": True,
    "enable_tier3_native": True
}

# --- Global State ---
logger = None
CONFIG = None

# --- Logging Setup ---
def setup_logging():
    log_file = os.path.join(APP_BASE_PATH, LOG_FILE_NAME)
    is_debug = True # Force debug during testing
    
    if os.path.exists(log_file) and os.path.getsize(log_file) > 5 * 1024 * 1024:
        try: os.remove(log_file)
        except: pass

    level = logging.DEBUG
    logging.basicConfig(
        level=level,
        format='%(asctime)s [%(levelname)s] %(message)s',
        handlers=[logging.FileHandler(log_file, mode='a', encoding='utf-8')]
    )
    l = logging.getLogger(WRAPPER_NAME)
    l.setLevel(level)
    return l

# --- Config Handling ---
def load_config():
    global CONFIG
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                user_config = json.load(f)
                needs_save = False
                if not isinstance(user_config, dict): raise ValueError("Config must be a JSON object")
                for k, v in DEFAULT_CONFIG.items():
                    if k not in user_config:
                        user_config[k] = v
                        needs_save = True
                if needs_save:
                    try:
                        with open(CONFIG_PATH, 'w', encoding='utf-8') as wf:
                            json.dump(user_config, wf, indent=2)
                    except: pass
                return user_config
        except:
            pass
    return DEFAULT_CONFIG

# --- Utilities ---
def safe_print(msg):
    try:
        sys.stdout.write(msg + '\n')
        sys.stdout.flush()
    except: pass

def kill_process_tree(pid):
    """Forcefully kills a process and all its children."""
    try:
        if platform.system() == 'Windows':
            subprocess.run(['taskkill', '/F', '/T', '/PID', str(pid)], capture_output=True, check=False)
        else:
            import signal
            os.kill(pid, signal.SIGKILL)
    except: pass

def find_url_in_args(args):
    url_pattern = re.compile(r'https?://[^\s<>"+]+|www\.[^\s<>"+]+')
    for arg in args:
        match = url_pattern.search(arg)
        if match: return match.group(0)
    return None

def check_proxy_online():
    try:
        domain = "test.whyknot.dev" if CONFIG.get("use_test_version", False) else "whyknot.dev"
        req = urllib.request.Request(f"https://{domain}/api/status/ping", method='GET')
        with urllib.request.urlopen(req, timeout=3.0) as response:
            return response.status == 200
    except Exception as e:
        logger.debug(f"Proxy Online Check FAILED: {e}")
        return False

def update_wrapper_success():
    try:
        if os.path.exists(WRAPPER_STATE_PATH):
            with open(WRAPPER_STATE_PATH, 'r') as f:
                state = json.load(f)
            state['consecutive_errors'] = 0
            state['force_fallback'] = False
            with open(WRAPPER_STATE_PATH, 'w') as f:
                json.dump(state, f)
    except: pass

def attempt_executable(path, executable_name, args, use_custom_temp_dir=False, log_level=logging.INFO, timeout=10.0):
    if not os.path.exists(path): 
        logger.error(f"Executable missing: {path}")
        return None, 1
    try:
        env = os.environ.copy()
        if use_custom_temp_dir:
            temp_dir = os.path.join(APP_BASE_PATH, "_tmp")
            if not os.path.exists(temp_dir): os.makedirs(temp_dir)
            env['TMP'] = temp_dir
            env['TEMP'] = temp_dir
        
        cmd = [path] + args
        logger.debug(f"Launching {executable_name}: {' '.join(cmd)}")
        
        process = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env,
            creationflags=subprocess.CREATE_NO_WINDOW if platform.system() == 'Windows' else 0
        )
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            logger.warning(f"{executable_name} TIMED OUT after {timeout}s. PURGING process tree {process.pid}.")
            kill_process_tree(process.pid)
            return None, -1
            
        if stderr:
            logger.debug(f"{executable_name} stderr: {stderr.strip()}")
            
        if process.returncode == 0: 
            return stdout.strip(), 0
        else:
            logger.debug(f"{executable_name} return code: {process.returncode}")
            return None, process.returncode
    except Exception as e:
        logger.error(f"Error running {executable_name}: {e}")
        return None, 1

def resolve_via_proxy(target_url, incoming_args, res_timeout, custom_ua, remote_server_base):
    try:
        video_type = "va"
        for i, arg in enumerate(incoming_args):
            if arg == "--format" and i + 1 < len(incoming_args):
                if "bestaudio" in incoming_args[i+1]: video_type = "a"
                break
        # Detect legacy players to provide the correct hint to the proxy
        is_legacy = any(x in (custom_ua or "") for x in ["UnityPlayer", "NSPlayer", "WMFSDK"])
        player_hint = "unity" if is_legacy else "avpro"
        
        resolve_url = f"{remote_server_base}/api/stream/resolve?url={quote_plus(target_url)}&video_type={video_type}&player={player_hint}"
        logger.debug(f"Proxy API Request: {resolve_url}")
        
        req = urllib.request.Request(resolve_url, method='GET')
        if custom_ua: req.add_header("User-Agent", custom_ua)
        
        with urllib.request.urlopen(req, timeout=res_timeout) as response:
            if response.status == 200:
                data = json.loads(response.read().decode())
                res = data.get("stream_url") or data.get("url")
                if res:
                    logger.debug(f"Proxy resolved: {res[:100]}...")
                    return res
            logger.debug(f"Proxy API Status: {response.status}")
    except Exception as e: 
        logger.debug(f"Proxy API Error: {e}")
    return None

def resolve_tier_1(incoming_args, res_timeout=10.0, custom_ua=None):
    try:
        max_height = CONFIG.get("preferred_max_height", 1080)
        tier_1_args = []
        skip_next = False
        for arg in incoming_args:
            if skip_next:
                skip_next = False
                continue
            if arg in ("--exp-allow", "--wild-allow"):
                skip_next = True
                continue
            tier_1_args.append(arg)
            
        tier_1_args.extend(["--remote-components", "ejs:github"])

        # Detect legacy players to force MP4/VOD if necessary
        is_legacy = any(x in (custom_ua or "") for x in ["UnityPlayer", "NSPlayer", "WMFSDK"])
        
        if "-f" not in tier_1_args and "--format" not in tier_1_args:
            if is_legacy:
                logger.info(f"Legacy player detected ({custom_ua}). Forcing MP4/VOD for Tier 1.")
                # Force combined MP4 (H.264 + AAC) or similar compatible formats
                tier_1_args.extend(["-f", f"best[height<={max_height}][ext=mp4][vcodec^=avc1][acodec^=mp4a][protocol^=http][protocol!*=m3u8][protocol!*=dash]/best[height<={max_height}]/best"])
            else:
                tier_1_args.extend(["-f", f"bestvideo[height<={max_height}]+bestaudio/best[height<={max_height}]"])
        
        logger.debug(f"Tier 1 (Local) started. Timeout: {res_timeout}s")
        resolved_url, return_code = attempt_executable(LATEST_YTDLP_PATH, LATEST_YTDLP_FILENAME, tier_1_args, use_custom_temp_dir=True, timeout=res_timeout)
        
        if return_code == 0 and resolved_url:
            logger.info(f"Tier 1 Success: {resolved_url[:100]}...")
            return {"tier": 1, "url": resolved_url}
        else:
            logger.debug(f"Tier 1 Failed. Code: {return_code}")
    except Exception as e: 
        logger.debug(f"Tier 1 Crash: {e}")
    return None

def resolve_tier_2(target_url, incoming_args, res_timeout=10.0, custom_ua=None, remote_server_base=None):
    try:
        logger.debug(f"Tier 2 (Proxy) started. Timeout: {res_timeout}s")
        url = resolve_via_proxy(target_url, incoming_args, res_timeout, custom_ua, remote_server_base)
        if url:
            logger.info(f"Tier 2 Success: {url[:100]}...")
            return {"tier": 2, "url": url}
        else:
            logger.debug("Tier 2 Failed.")
    except Exception as e: 
        logger.debug(f"Tier 2 Crash: {e}")
    return None

def process_and_execute(incoming_args):
    try:
        logger.info(f"--- PARALLEL WRAPPER START (v{WRAPPER_VERSION}) ---")
        logger.debug(f"Args: {incoming_args}")
        
        current_time = time.time()
        target_url = find_url_in_args(incoming_args)
        
        if not target_url:
            logger.debug("No URL in args. Running native.")
            final_output, return_code = attempt_executable(ORIGINAL_YTDLP_PATH, ORIGINAL_YTDLP_FILENAME, incoming_args)
            if final_output: safe_print(final_output)
            return return_code

        t1_enabled = CONFIG.get("enable_tier1_modern", True)
        t2_enabled = CONFIG.get("enable_tier2_proxy", True)
        t3_enabled = CONFIG.get("enable_tier3_native", True)
        forced_tier = 0
        
        try:
            if os.path.exists(WRAPPER_STATE_PATH):
                with open(WRAPPER_STATE_PATH, 'r') as f:
                    state = json.load(f)
                
                if state.get('force_fallback', False) and current_time < state.get('fallback_until', 0):
                    logger.warning("GLOBAL FALLBACK ACTIVE.")
                    forced_tier = 2
                
                failed_urls = state.get('failed_urls', {})
                if target_url in failed_urls:
                    failed_info = failed_urls[target_url]
                    last_time = failed_info.get('last_request_time', 0)
                    if current_time - last_time < CONFIG.get("failure_retry_window", 15):
                        logger.info(f"RAPID RETRY DETECTED ({current_time - last_time:.1f}s). Skipping to Native.")
                        forced_tier = 2
        except Exception as e:
            logger.debug(f"State Check Error: {e}")

        logger.debug(f"Status: Tier1={t1_enabled}, Tier2={t2_enabled}, ForcedTier={forced_tier}")

        domain = "test.whyknot.dev" if CONFIG.get("use_test_version", False) else "whyknot.dev"
        REMOTE_BASE = f"https://{domain}"
        GLOBAL_TIMEOUT = 10.0
        custom_ua = CONFIG.get("custom_user_agent")

        if forced_tier < 2:
            tasks = []
            with ThreadPoolExecutor(max_workers=2) as executor:
                if t1_enabled:
                    tasks.append(executor.submit(resolve_tier_1, incoming_args, GLOBAL_TIMEOUT, custom_ua))
                if t2_enabled:
                    tasks.append(executor.submit(resolve_tier_2, target_url, incoming_args, GLOBAL_TIMEOUT, custom_ua, REMOTE_BASE))
                
                logger.debug(f"Submitted {len(tasks)} parallel tasks.")
                
                for future in as_completed(tasks):
                    result = future.result()
                    if result:
                        logger.info(f"PARALLEL WINNER: Tier {result['tier']}")
                        update_wrapper_success()
                        safe_print(result['url'])
                        return 0
            logger.debug("All parallel tasks completed with no result.")

        if t3_enabled:
            logger.info("Running Tier 3 [NATIVE]...")
            final_output, return_code = attempt_executable(
                ORIGINAL_YTDLP_PATH, ORIGINAL_YTDLP_FILENAME, incoming_args, 
                timeout=GLOBAL_TIMEOUT + 5.0
            )
            if return_code == 0 and final_output:
                logger.info("Tier 3 SUCCESS.")
                safe_print(final_output)
                return 0
            logger.debug(f"Tier 3 Failed. Code: {return_code}")

        logger.error(f"ALL TIERS FAILED for: {target_url}")
        return 1
    except Exception as e:
        import traceback
        logger.error(f"FATAL process_and_execute: {e}\n{traceback.format_exc()}")
        return 1

def main():
    try:
        global logger
        logger = setup_logging()
        global CONFIG
        CONFIG = load_config()
        return_code = process_and_execute(sys.argv[1:])
        sys.exit(return_code)
    except Exception as e:
        sys.stderr.write(f"FATAL MAIN: {e}\n")
        sys.exit(1)

if __name__ == '__main__':
    main()
