import sys
import os

# Ensure local modules are findable when frozen or run directly
if getattr(sys, 'frozen', False):
    APP_BASE_PATH = os.path.abspath(os.path.dirname(sys.executable))
else:
    APP_BASE_PATH = os.path.dirname(os.path.abspath(__file__)) 

if APP_BASE_PATH not in sys.path:
    sys.path.insert(0, APP_BASE_PATH)

import logging
import re
import threading
import json
import time
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    from jobs import job_manager
    from verifier import verify_stream, verify_stream_with_ytdlp
    from resolver import resolve_tier_1_proxy, resolve_tier_2_modern, resolve_tier_3_native, attempt_executable
except ImportError:
    from .jobs import job_manager
    from .verifier import verify_stream, verify_stream_with_ytdlp
    from .resolver import resolve_tier_1_proxy, resolve_tier_2_modern, resolve_tier_3_native, attempt_executable

try:
    from _version import __version__ as WRAPPER_VERSION
    from _version import __build_type__ as BUILD_TYPE
except ImportError:
    WRAPPER_VERSION = "vDEV"
    BUILD_TYPE = "DEV"

# --- Constants ---
WRAPPER_NAME = "yt-dlp-wrapper"
LOG_FILE_NAME = "wrapper.log"
CONFIG_FILE_NAME = "patcher_config.json"
WRAPPER_STATE_NAME = "wrapper_state.json"
ORIGINAL_YTDLP_FILENAME = "yt-dlp-og.exe"
LATEST_YTDLP_FILENAME = "yt-dlp-latest.exe"

ORIGINAL_YTDLP_PATH = os.path.join(APP_BASE_PATH, ORIGINAL_YTDLP_FILENAME)
LATEST_YTDLP_PATH = os.path.join(APP_BASE_PATH, LATEST_YTDLP_FILENAME)
CONFIG_PATH = os.path.join(APP_BASE_PATH, CONFIG_FILE_NAME)
WRAPPER_STATE_PATH = os.path.join(APP_BASE_PATH, WRAPPER_STATE_NAME)

DEFAULT_CONFIG = {
    "use_test_version": False,
    "preferred_max_height": 1080,
    "failure_retry_window": 60,
    "custom_user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "enable_tier1_proxy": True,
    "enable_tier2_modern": True,
    "enable_tier3_native": True,
    "debug_mode": BUILD_TYPE == "DEV"
}

# --- Global State ---
logger = None
CONFIG = None

def setup_logging(debug_mode):
    log_file = os.path.join(APP_BASE_PATH, LOG_FILE_NAME)
    if os.path.exists(log_file) and os.path.getsize(log_file) > 10 * 1024 * 1024:
        try: os.remove(log_file)
        except: pass
    
    level = logging.DEBUG if debug_mode else logging.INFO
    
    # Use 'a' (append) because redirector is called many times per session.
    # The Patcher wipes this file once at startup.
    logging.basicConfig(
        level=level,
        format='%(asctime)s [%(levelname)s] [%(name)s] %(message)s',
        handlers=[logging.FileHandler(log_file, mode='a', encoding='utf-8')]
    )
    
    l = logging.getLogger(WRAPPER_NAME)
    l.setLevel(level)
    return l

def load_config():
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                user_config = json.load(f)
                mapping = {"enable_tier1_modern": "enable_tier2_modern", "enable_tier2_proxy": "enable_tier1_proxy"}
                for old, new in mapping.items():
                    if old in user_config and new not in user_config: user_config[new] = user_config[old]
                for k, v in DEFAULT_CONFIG.items():
                    if k not in user_config: user_config[k] = v
                return user_config
        except: pass
    return DEFAULT_CONFIG

def safe_print(msg):
    try:
        sys.stdout.write(msg + '\n')
        sys.stdout.flush()
    except: pass

def find_url_in_args(args):
    url_pattern = re.compile(r'https?://[^\s<>"+]+|www\.[^\s<>"+]+')
    for arg in args:
        match = url_pattern.search(arg)
        if match: return match.group(0)
    return None

def detect_legacy(incoming_args, custom_ua):
    try:
        if os.path.exists(WRAPPER_STATE_PATH):
            with open(WRAPPER_STATE_PATH, 'r') as f:
                state = json.load(f)
                if state.get('active_player') == 'unity': return True
                if state.get('active_player') == 'avpro': return False
    except: pass
    ua_in_args = next((incoming_args[i+1] for i, a in enumerate(incoming_args) if a == "--user-agent" and i+1 < len(incoming_args)), None)
    eff_ua = ua_in_args or custom_ua
    if any(x in (eff_ua or "") for x in ["UnityPlayer", "NSPlayer", "WMFSDK"]): return True
    if any("protocol^=http" in a or "protocol!*=m3u8" in a for a in incoming_args): return True
    return False

def update_wrapper_success(target_url, resolved_url, tier):
    try:
        if os.path.exists(WRAPPER_STATE_PATH):
            with open(WRAPPER_STATE_PATH, 'r') as f: state = json.load(f)
            if 'history' not in state: state['history'] = []
            state['history'] = [h for h in state['history'] if h[0] != target_url]
            state['history'].insert(0, [target_url, resolved_url, tier, time.time()])
            state['history'] = state['history'][:3]
            # Prune legacy fields
            for key in ['consecutive_errors', 'force_fallback', 'failed_urls', 'domain_blacklist', 'cache']:
                if key in state: del state[key]
            with open(WRAPPER_STATE_PATH, 'w') as f: json.dump(state, f)
            logger.debug(f"History updated with Tier {tier} result.")
    except Exception as e: logger.debug(f"Failed to update history: {e}")

def get_cached_result(target_url):
    try:
        if os.path.exists(WRAPPER_STATE_PATH):
            with open(WRAPPER_STATE_PATH, 'r') as f: state = json.load(f)
            history = state.get('history', [])
            for target, resolved, tier, ts in history:
                if target == target_url and (time.time() - ts < 3600): # 1h cache
                    logger.info(f"History Hit! Using verified Tier {tier} URL.")
                    if verify_stream(resolved, timeout=4.0): return resolved
                    else:
                        logger.warning("History item invalid. Purging.")
                        state['history'] = [h for h in history if h[1] != resolved]
                        with open(WRAPPER_STATE_PATH, 'w') as wf: json.dump(state, wf)
    except: pass
    return None

def list_formats_background(ytdlp_path, name, target_url):
    """Executes -F in the background and logs the output."""
    def task():
        try:
            logger.debug(f"[{name}] Starting background format listing for: {target_url[:50]}...")
            cmd = [ytdlp_path, "--no-warnings", "--ignore-errors", "-F", target_url]
            # Use minimal flags for OG
            if "og" in ytdlp_path.lower():
                cmd = [ytdlp_path, "-F", target_url]
                
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            job_manager.assign(proc)
            stdout, _ = proc.communicate(timeout=30.0)
            if stdout:
                logger.debug(f"[{name}] Available Formats:\n{stdout}")
        except Exception as e:
            logger.debug(f"[{name}] Background format listing failed: {e}")

    threading.Thread(target=task, daemon=True).start()

def process_and_execute(incoming_args):
    try:
        start_time = time.time()
        logger.info(f"--- RESOLVER START ({WRAPPER_VERSION}) ---")
        target_url = find_url_in_args(incoming_args)
        if not target_url:
            logger.info("Direct execution (No URL).")
            res, code = attempt_executable(ORIGINAL_YTDLP_PATH, ORIGINAL_YTDLP_FILENAME, incoming_args, APP_BASE_PATH)
            if res: safe_print(res)
            return code

        domain = "test.whyknot.dev" if CONFIG.get("use_test_version", False) else "whyknot.dev"
        REMOTE_BASE = f"https://{domain}"; custom_ua = CONFIG.get("custom_user_agent")
        is_legacy = detect_legacy(incoming_args, custom_ua); player_hint = "unity" if is_legacy else "avpro"

        logger.info(f"Request: {target_url[:70]}... [{player_hint.upper()}]")
        
        # Start background format listing if in debug mode
        if CONFIG.get("debug_mode"):
            if os.path.exists(LATEST_YTDLP_PATH):
                list_formats_background(LATEST_YTDLP_PATH, "Modern", target_url)
            if os.path.exists(ORIGINAL_YTDLP_PATH):
                list_formats_background(ORIGINAL_YTDLP_PATH, "Native", target_url)

        cached = get_cached_result(target_url)
        if cached: 
            safe_print(cached)
            logger.info(f"Resolution successful via Cache in {time.time() - start_time:.2f}s.")
            return 0

        # TIER 1: PROXY (Fastest)
        if CONFIG.get("enable_tier1_proxy", True):
            t1_start = time.time()
            logger.info("Checking Tier 1 (Proxy)...")
            res = resolve_tier_1_proxy(target_url, incoming_args, 10.0, custom_ua, REMOTE_BASE, player_hint)
            if res and res.get('url'):
                if verify_stream(res['url'], timeout=5.0, user_agent=custom_ua):
                    elapsed = time.time() - start_time
                    logger.info(f"Tier 1 VALIDATED in {time.time() - t1_start:.2f}s. (Total: {elapsed:.2f}s)")
                    update_wrapper_success(target_url, res['url'], 1)
                    safe_print(res['url']); return 0
                logger.debug("Tier 1 failed verification.")

        # TIER 2: MODERN (yt-dlp latest)
        if CONFIG.get("enable_tier2_modern", True):
            t2_start = time.time()
            logger.info("Checking Tier 2 (Modern)...")
            res = resolve_tier_2_modern(incoming_args, 30.0, custom_ua, APP_BASE_PATH, LATEST_YTDLP_PATH, LATEST_YTDLP_FILENAME, CONFIG.get("preferred_max_height", 1080), is_legacy)
            if res and res.get('url'):
                v_res = verify_stream_with_ytdlp(LATEST_YTDLP_PATH, target_url, timeout=15.0)
                if v_res is True:
                    elapsed = time.time() - start_time
                    logger.info(f"Tier 2 VALIDATED in {time.time() - t2_start:.2f}s. (Total: {elapsed:.2f}s)")
                    update_wrapper_success(target_url, res['url'], 2); safe_print(res['url']); return 0
                elif v_res is None:
                    if verify_stream(res['url'], timeout=5.0, user_agent=custom_ua):
                        elapsed = time.time() - start_time
                        logger.info(f"Tier 2 VALIDATED (Network) in {time.time() - t2_start:.2f}s. (Total: {elapsed:.2f}s)")
                        update_wrapper_success(target_url, res['url'], 2); safe_print(res['url']); return 0
                logger.debug("Tier 2 failed verification.")

        # TIER 3: NATIVE (yt-dlp original)
        if CONFIG.get("enable_tier3_native", True):
            t3_start = time.time()
            logger.info("Checking Tier 3 (Native)...")
            res = resolve_tier_3_native(incoming_args, 15.0, APP_BASE_PATH, ORIGINAL_YTDLP_PATH, ORIGINAL_YTDLP_FILENAME)
            if res and res.get('url'):
                v_res = verify_stream_with_ytdlp(ORIGINAL_YTDLP_PATH, target_url, timeout=15.0)
                if v_res is True:
                    elapsed = time.time() - start_time
                    logger.info(f"Tier 3 VALIDATED in {time.time() - t3_start:.2f}s. (Total: {elapsed:.2f}s)")
                    update_wrapper_success(target_url, res['url'], 3); safe_print(res['url']); return 0
                elif v_res is None:
                    if verify_stream(res['url'], timeout=5.0, user_agent=custom_ua):
                        elapsed = time.time() - start_time
                        logger.info(f"Tier 3 VALIDATED (Network) in {time.time() - t3_start:.2f}s. (Total: {elapsed:.2f}s)")
                        update_wrapper_success(target_url, res['url'], 3); safe_print(res['url']); return 0
                logger.debug("Tier 3 failed verification.")

        # TIER 4: RECOVERY PROXY
        logger.warning("Emergency Tier 4 (Recovery)...")
        res = resolve_tier_1_proxy(target_url, incoming_args, 15.0, custom_ua, REMOTE_BASE, player_hint)
        if res and res.get('url') and verify_stream(res['url'], timeout=8.0, user_agent=custom_ua):
            elapsed = time.time() - start_time
            logger.info(f"Tier 4 SUCCESS. (Total: {elapsed:.2f}s)")
            update_wrapper_success(target_url, res['url'], 4)
            safe_print(res['url']); return 0

        logger.error(f"FATAL: All resolution tiers failed for: {target_url}")
        return 1
    except Exception as e:
        import traceback; logger.error(f"FATAL: {e}\n{traceback.format_exc()}")
        return 1
    finally: job_manager.close()

def main():
    try:
        global logger, CONFIG
        CONFIG = load_config()
        logger = setup_logging(CONFIG.get("debug_mode", BUILD_TYPE == "DEV"))
        sys.exit(process_and_execute(sys.argv[1:]))
    except Exception as e:
        sys.stderr.write(f"FATAL MAIN: {e}\n"); sys.exit(1)

if __name__ == '__main__':
    main()
