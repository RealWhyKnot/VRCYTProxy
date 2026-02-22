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
import urllib.request
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    from jobs import job_manager
    from verifier import verify_stream
    from resolver import resolve_tier_1_proxy, resolve_tier_2_modern, resolve_tier_3_native, attempt_executable
except ImportError:
    from .jobs import job_manager
    from .verifier import verify_stream
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

if getattr(sys, 'frozen', False):
    APP_BASE_PATH = os.path.abspath(os.path.dirname(sys.executable))
else:
    APP_BASE_PATH = os.path.dirname(os.path.abspath(__file__))

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
    
    # We want a clean format for the log file
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
                # Map old keys to new ones if they exist to prevent breakage
                mapping = {
                    "enable_tier1_modern": "enable_tier2_modern",
                    "enable_tier2_proxy": "enable_tier1_proxy"
                }
                for old, new in mapping.items():
                    if old in user_config and new not in user_config:
                        user_config[new] = user_config[old]
                
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
                if state.get('active_player') == 'unity': 
                    logger.debug("Legacy detected via wrapper_state (Unity)")
                    return True
                if state.get('active_player') == 'avpro': return False
    except: pass
    
    ua_in_args = next((incoming_args[i+1] for i, a in enumerate(incoming_args) if a == "--user-agent" and i+1 < len(incoming_args)), None)
    eff_ua = ua_in_args or custom_ua
    if any(x in (eff_ua or "") for x in ["UnityPlayer", "NSPlayer", "WMFSDK"]): 
        logger.debug(f"Legacy detected via User-Agent: {eff_ua}")
        return True
    if any("protocol^=http" in a or "protocol!*=m3u8" in a for a in incoming_args): 
        logger.debug("Legacy detected via protocol arguments")
        return True
    return False

def update_wrapper_success(target_url=None, resolved_url=None):
    try:
        if os.path.exists(WRAPPER_STATE_PATH):
            with open(WRAPPER_STATE_PATH, 'r') as f: state = json.load(f)
            state['consecutive_errors'] = 0
            state['force_fallback'] = False
            if target_url and resolved_url:
                if 'cache' not in state: state['cache'] = {}
                state['cache'][target_url] = {'url': resolved_url, 'expiry': time.time() + 900}
            with open(WRAPPER_STATE_PATH, 'w') as f: json.dump(state, f)
            logger.debug(f"State updated: Success for {target_url[:50]}...")
    except Exception as e:
        logger.debug(f"Failed to update state on success: {e}")

def process_and_execute(incoming_args):
    try:
        logger.info(f"--- RESOLVER START (v{WRAPPER_VERSION}) ---")
        current_time = time.time()
        target_url = find_url_in_args(incoming_args)
        
        if not target_url:
            logger.debug("No URL found in arguments, passing to native.")
            res, code = attempt_executable(ORIGINAL_YTDLP_PATH, ORIGINAL_YTDLP_FILENAME, incoming_args, APP_BASE_PATH)
            if res: safe_print(res)
            return code

        logger.info(f"Target URL: {target_url}")

        # --- STEP 1: STATE & BLACKLIST ---
        t1_enabled = CONFIG.get("enable_tier1_proxy", True)
        t2_enabled = CONFIG.get("enable_tier2_modern", True)
        t3_enabled = CONFIG.get("enable_tier3_native", True)
        start_tier = 1
        is_legacy = detect_legacy(incoming_args, CONFIG.get("custom_user_agent"))
        
        try:
            if os.path.exists(WRAPPER_STATE_PATH):
                with open(WRAPPER_STATE_PATH, 'r') as f: state = json.load(f)
                target_domain = urlparse(target_url).netloc.lower()
                
                # Check Domain Blacklist
                bl = state.get('domain_blacklist', {}).get(target_domain, {})
                if bl and current_time < bl.get('expiry', 0):
                    failed_tiers = bl.get('failed_tiers', [])
                    logger.debug(f"Domain {target_domain} has failed tiers: {failed_tiers}")
                    if 1 in failed_tiers: t1_enabled = False
                    if 2 in failed_tiers: t2_enabled = False
                
                # Check URL Specific Escalation
                failed_urls = state.get('failed_urls', {})
                if target_url in failed_urls:
                    f_info = failed_urls[target_url]
                    if current_time - f_info.get('last_request_time', 0) < CONFIG.get("failure_retry_window", 60):
                        start_tier = f_info.get('tier', 1)
                        logger.info(f"URL-specific escalation active. Starting at Tier {start_tier}.")
        except Exception as e:
            logger.debug(f"Error reading state/blacklist: {e}")

        # --- STEP 2: CACHE ---
        if start_tier == 1:
            try:
                if os.path.exists(WRAPPER_STATE_PATH):
                    with open(WRAPPER_STATE_PATH, 'r') as f: state = json.load(f)
                    cache = state.get('cache', {})
                    if target_url in cache and current_time < cache[target_url].get('expiry', 0):
                        curl = cache[target_url]['url']
                        logger.info(f"CACHE HIT: {target_url[:50]}...")
                        if verify_stream(curl, timeout=2.0):
                            logger.info("Cache verified. Returning.")
                            safe_print(curl); return 0
                        else:
                            logger.warning("Cache verification failed. Purging.")
                            if 'cache' in state: del state['cache'][target_url]
                            with open(WRAPPER_STATE_PATH, 'w') as wf: json.dump(state, wf)
            except Exception: pass

        # --- STEP 3: RESOLVE ---
        domain = "test.whyknot.dev" if CONFIG.get("use_test_version", False) else "whyknot.dev"
        REMOTE_BASE = f"https://{domain}"
        custom_ua = CONFIG.get("custom_user_agent")
        player_hint = "unity" if is_legacy else "avpro"

        # Tier 4: Last Resort Proxy
        if start_tier >= 4:
            logger.info("Tier 4: LAST RESORT Proxy.")
            res = resolve_tier_1_proxy(target_url, incoming_args, 15.0, custom_ua, REMOTE_BASE, player_hint)
            if res: 
                logger.info("WINNER: Tier 4 (Last Resort)")
                safe_print(res['url']); return 0
            logger.error("Tier 4: Last Resort Failed.")
            return 1

        # Execution Logic
        with ThreadPoolExecutor(max_workers=2) as executor:
            t1_f = None; t2_f = None
            
            # Submit T1 and T2 if they are allowed by start_tier
            if t1_enabled and start_tier <= 1:
                logger.debug(f"Submitting Tier 1 (Proxy) for: {target_url}")
                t1_f = executor.submit(resolve_tier_1_proxy, target_url, incoming_args, 8.0, custom_ua, REMOTE_BASE, player_hint)
            
            if t2_enabled and start_tier <= 2:
                logger.debug("Submitting Tier 2 (Modern yt-dlp)...")
                t2_f = executor.submit(resolve_tier_2_modern, incoming_args, 30.0, custom_ua, APP_BASE_PATH, LATEST_YTDLP_PATH, LATEST_YTDLP_FILENAME, CONFIG.get("preferred_max_height", 1080), is_legacy)

            # Wait for Tier 1 first (it's the fastest)
            if t1_f:
                try:
                    res = t1_f.result(timeout=8.5)
                    if res: 
                        logger.info("WINNER: Tier 1 (Proxy)")
                        update_wrapper_success(target_url, res['url'])
                        safe_print(res['url']); return 0
                except Exception as e:
                    logger.debug(f"Tier 1 skipped/failed: {e}")

            # If T1 failed or was skipped, wait for Tier 2 specifically if it was submitted
            if t2_f:
                try:
                    res = t2_f.result(timeout=25.0)
                    if res:
                        logger.info("WINNER: Tier 2 (Modern yt-dlp)")
                        update_wrapper_success(target_url, res['url'])
                        safe_print(res['url']); return 0
                except Exception as e:
                    logger.debug(f"Tier 2 skipped/failed: {e}")

            # If T1 or T2 were running in parallel/racing but didn't finish above, clean up
            tasks = {f: i for i, f in enumerate([t1_f, t2_f]) if f and not f.done()}
            if tasks:
                try:
                    for f in as_completed(tasks, timeout=2.0):
                        res = f.result()
                        if res:
                            logger.info(f"WINNER: Tier {res['tier']} (Late result)")
                            update_wrapper_success(target_url, res['url'])
                            safe_print(res['url']); return 0
                except Exception: pass

        # Tier 3 (Native/Legacy) is sequential - we only hit it if T1/T2 fail or are forced out
        if t3_enabled and start_tier <= 3:
            logger.info("Attempting Tier 3 (Native yt-dlp)...")
            res = resolve_tier_3_native(incoming_args, 15.0, APP_BASE_PATH, ORIGINAL_YTDLP_PATH, ORIGINAL_YTDLP_FILENAME)
            if res: 
                logger.info("WINNER: Tier 3 (Native)")
                update_wrapper_success(target_url, res['url'])
                safe_print(res['url']); return 0

        # If everything else fails, try one last proxy attempt (Tier 4)
        logger.warning("All primary tiers (1, 2, 3) failed. Attempting Tier 4 (Absolute Last Resort Proxy)...")
        res = resolve_tier_1_proxy(target_url, incoming_args, 15.0, custom_ua, REMOTE_BASE, player_hint)
        if res: 
            logger.info("WINNER: Tier 4 (Emergency Recovery)")
            safe_print(res['url']); return 0

        logger.error(f"ALL TIERS FAILED for: {target_url}")
        return 1
    except Exception as e:
        import traceback
        logger.error(f"FATAL: {e}\n{traceback.format_exc()}")
        return 1
    finally:
        job_manager.close()

def main():
    try:
        global logger, CONFIG
        CONFIG = load_config()
        logger = setup_logging(CONFIG.get("debug_mode", BUILD_TYPE == "DEV"))
        sys.exit(process_and_execute(sys.argv[1:]))
    except Exception as e:
        sys.stderr.write(f"FATAL MAIN: {e}\n")
        sys.exit(1)

if __name__ == '__main__':
    main()
