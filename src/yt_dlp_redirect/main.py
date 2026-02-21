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
WRAPPER_VERSION = "v2026.02.21.11 .dev" 
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

def detect_legacy(incoming_args, custom_ua):
    # Clue 0: Check Patcher's state for the current session
    try:
        if os.path.exists(WRAPPER_STATE_PATH):
            with open(WRAPPER_STATE_PATH, 'r') as f:
                state = json.load(f)
                if state.get('active_player') == 'unity':
                    logger.debug("Legacy player detected via Patcher state.")
                    return True
                if state.get('active_player') == 'avpro':
                    return False
    except: pass

    # Clue 1: Check for User-Agent in incoming args
    ua_in_args = None
    for i, arg in enumerate(incoming_args):
        if arg == "--user-agent" and i + 1 < len(incoming_args):
            ua_in_args = incoming_args[i+1]
            break
    effective_ua = ua_in_args or custom_ua
    if any(x in (effective_ua or "") for x in ["UnityPlayer", "NSPlayer", "WMFSDK"]):
        return True

    # Clue 2: Check for VRChat's specific legacy format pattern [protocol^=http]
    # Unity player explicitly requests non-streaming protocols.
    for arg in incoming_args:
        if "protocol^=http" in arg or "protocol!*=m3u8" in arg:
            logger.debug("Legacy player detected via format protocol restrictions.")
            return True
            
    return False

def update_wrapper_success(target_url=None, resolved_url=None):
    try:
        if os.path.exists(WRAPPER_STATE_PATH):
            with open(WRAPPER_STATE_PATH, 'r') as f:
                state = json.load(f)
            
            state['consecutive_errors'] = 0
            state['force_fallback'] = False
            
            if target_url and resolved_url:
                # Single-entry cache: Store only the last resolution
                state['cache'] = {
                    target_url: {
                        'url': resolved_url,
                        'expiry': time.time() + 900
                    }
                }
                logger.debug(f"Cached last resolution for: {target_url[:50]}...")

            with open(WRAPPER_STATE_PATH, 'w') as f:
                json.dump(state, f)
    except: pass

def verify_stream(url, timeout=3.0, depth=0):
    """Deep verification of a stream. Recursively checks bitrate manifests."""
    if not url or depth > 2: return False
    
    # Use headers that mimic VRChat (MediaFoundation/AVPro)
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "*/*",
        "Accept-Encoding": "identity",
        "Accept-Language": "en-US,en;q=0.9",
        "Connection": "keep-alive"
    }
    
    try:
        # 1. Initial HEAD check
        req = urllib.request.Request(url, method='HEAD', headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status >= 400: return False
            
            content_type = resp.headers.get('Content-Type', '').lower()
            if any(x in content_type for x in ['video/', 'audio/', 'application/octet-stream']):
                return True
        
        # 2. Manifest Deep Check
        is_manifest = any(x in url.lower() for x in ['.m3u8', '.mpd', 'manifest'])
        if is_manifest:
            req_get = urllib.request.Request(url, method='GET', headers=headers)
            with urllib.request.urlopen(req_get, timeout=timeout) as resp:
                content = resp.read().decode('utf-8', errors='ignore').strip()
                
                if content.startswith('<!DOCTYPE') or '<html' in content.lower():
                    return False
                
                if not any(x in content for x in ['#EXTM3U', 'MPD', 'Playlist']):
                    return False

                # Extract first URI from manifest
                lines = [line.strip() for line in content.split('\n') if line.strip() and not line.startswith('#')]
                if lines:
                    target_uri = lines[0]
                    if not target_uri.startswith('http'):
                        from urllib.parse import urljoin
                        target_uri = urljoin(url, target_uri)
                    
                    # Recursive check if it's another manifest (bitrate list)
                    if any(x in target_uri.lower() for x in ['.m3u8', '.mpd']):
                        return verify_stream(target_uri, timeout, depth + 1)
                    
                    # Otherwise verify it as a segment
                    seg_req = urllib.request.Request(target_uri, method='HEAD', headers=headers)
                    with urllib.request.urlopen(seg_req, timeout=timeout) as seg_resp:
                        return seg_resp.status < 400
        
        return True 
    except Exception as e:
        logger.debug(f"Stream Verification FAILED (Depth {depth}): {e}")
        return False

def verify_stream_with_ytdlp(ytdlp_path, url, timeout=5.0):
    """Uses the actual yt-dlp binary to verify if a URL is playable."""
    if not os.path.exists(ytdlp_path): return False
    try:
        # --check-formats is a lightweight way to verify the link works
        cmd = [ytdlp_path, "--check-formats", "--no-warnings", "--ignore-errors", url]
        process = subprocess.run(
            cmd, capture_output=True, timeout=timeout, creationflags=subprocess.CREATE_NO_WINDOW
        )
        return process.returncode == 0
    except Exception as e:
        logger.debug(f"yt-dlp Verification failed for {ytdlp_path}: {e}")
        return False

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
        
        is_legacy = detect_legacy(incoming_args, custom_ua)
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
                    return res
    except Exception as e: 
        logger.debug(f"Proxy API Error: {e}")
    return None

def resolve_tier_1_proxy(target_url, incoming_args, res_timeout=10.0, custom_ua=None, remote_server_base=None):
    """Tier 1: Remote Proxy (WhyKnot.dev). Verified via Deep Manifest Check."""
    try:
        logger.debug(f"Tier 1 (Proxy) started. Timeout: {res_timeout}s")
        url = resolve_via_proxy(target_url, incoming_args, res_timeout, custom_ua, remote_server_base)
        if url:
            if verify_stream(url):
                logger.info(f"Tier 1 SUCCESS (Proxy): {url[:100]}...")
                return {"tier": 1, "url": url}
            else:
                logger.warning("WINNER: Tier 1 (Proxy) FAILED Deep Verification.")
    except Exception as e: 
        logger.debug(f"Tier 1 (Proxy) Crash: {e}")
    return None

def resolve_tier_2_modern(incoming_args, res_timeout=10.0, custom_ua=None):
    """Tier 2: Latest yt-dlp. Verified via binary check."""
    try:
        max_height = CONFIG.get("preferred_max_height", 1080)
        is_legacy = detect_legacy(incoming_args, custom_ua)
        
        tier_2_args = []
        skip_next = False
        format_specified = False
        for arg in incoming_args:
            if skip_next:
                skip_next = False
                continue
            if arg in ("--exp-allow", "--wild-allow"):
                skip_next = True
                continue
            if arg in ("-f", "--format"):
                format_specified = True
            tier_2_args.append(arg)
            
        DENO_PATH = os.path.join(APP_BASE_PATH, "deno.exe")
        tier_2_args.extend(["--remote-components", "ejs:github"])
        if os.path.exists(DENO_PATH):
            tier_2_args.extend(["--extractor-args", f"ejs:deno_path={DENO_PATH}"])

        if not format_specified:
            if is_legacy:
                tier_2_args.extend(["-f", f"best[height<={max_height}][ext=mp4][vcodec^=avc1][acodec^=mp4a][protocol^=http][protocol!*=m3u8][protocol!*=dash]/best[height<={max_height}]/best"])
            else:
                tier_2_args.extend(["-f", f"bestvideo[height<={max_height}]+bestaudio/best[height<={max_height}]"])
        
        logger.debug(f"Tier 2 (Modern) started. Timeout: {res_timeout}s")
        resolved_url, return_code = attempt_executable(LATEST_YTDLP_PATH, LATEST_YTDLP_FILENAME, tier_2_args, use_custom_temp_dir=True, timeout=res_timeout)
        
        if return_code == 0 and resolved_url:
            if verify_stream_with_ytdlp(LATEST_YTDLP_PATH, resolved_url):
                logger.info(f"Tier 2 SUCCESS (Modern): {resolved_url[:100]}...")
                return {"tier": 2, "url": resolved_url}
            else:
                logger.warning("Tier 2 (Modern) result failed self-verification.")
    except Exception as e: 
        logger.debug(f"Tier 2 (Modern) Crash: {e}")
    return None

def resolve_tier_3_native(incoming_args, res_timeout=15.0):
    """Tier 3: Original VRChat yt-dlp. Verified via binary check."""
    try:
        logger.debug(f"Tier 3 (Native) started. Timeout: {res_timeout}s")
        final_output, return_code = attempt_executable(
            ORIGINAL_YTDLP_PATH, ORIGINAL_YTDLP_FILENAME, incoming_args, 
            timeout=res_timeout
        )
        if return_code == 0 and final_output:
            if verify_stream_with_ytdlp(ORIGINAL_YTDLP_PATH, final_output):
                logger.info("Tier 3 SUCCESS (Native).")
                return {"tier": 3, "url": final_output}
            else:
                logger.warning("Tier 3 (Native) result failed self-verification.")
    except Exception as e:
        logger.debug(f"Tier 3 (Native) Crash: {e}")
    return None

def process_and_execute(incoming_args):
    try:
        logger.info(f"--- RESOLVER START ({WRAPPER_VERSION}) ---")
        
        current_time = time.time()
        target_url = find_url_in_args(incoming_args)
        
        if not target_url:
            final_output, return_code = attempt_executable(ORIGINAL_YTDLP_PATH, ORIGINAL_YTDLP_FILENAME, incoming_args)
            if final_output: safe_print(final_output)
            return return_code

        # --- STEP 1: STATE, TIER & BLACKLIST CHECK ---
        t1_enabled = CONFIG.get("enable_tier2_proxy", True) 
        t2_enabled = CONFIG.get("enable_tier1_modern", True) 
        t3_enabled = CONFIG.get("enable_tier3_native", True)
        forced_tier = 0
        
        try:
            if os.path.exists(WRAPPER_STATE_PATH):
                with open(WRAPPER_STATE_PATH, 'r') as f:
                    state = json.load(f)
                
                # Domain Blacklist Check with recovery timer
                from urllib.parse import urlparse
                target_domain = urlparse(target_url).netloc.lower()
                blacklist_entry = state.get('domain_blacklist', {}).get(target_domain, {})
                
                if blacklist_entry:
                    if current_time < blacklist_entry.get('expiry', 0):
                        failed_tiers = blacklist_entry.get('failed_tiers', [])
                        if 1 in failed_tiers:
                            logger.warning(f"Tier 1 blacklisted for '{target_domain}'. Recovery in {int(blacklist_entry['expiry'] - current_time)}s.")
                            t1_enabled = False
                        if 2 in failed_tiers and 1 in failed_tiers:
                            logger.error(f"Domain '{target_domain}' unreliable. Locking to Tier 2 safety valve.")
                            t1_enabled = False
                            t2_enabled = True 
                            forced_tier = 2
                    else:
                        logger.info(f"Domain '{target_domain}' blacklist expired. Retrying all tiers.")

                if state.get('active_player') == 'unity':
                    logger.debug("PATCHER STATE: Unity Player Forced.")
                
                if state.get('force_fallback', False) and current_time < state.get('fallback_until', 0):
                    forced_tier = 2 
                
                failed_urls = state.get('failed_urls', {})
                if target_url in failed_urls:
                    failed_info = failed_urls[target_url]
                    if current_time - failed_info.get('last_request_time', 0) < CONFIG.get("failure_retry_window", 15):
                        forced_tier = failed_info.get('tier', 1) + 1
        except Exception: pass

        # --- STEP 2: CACHE CHECK ---
        if forced_tier < 2:
            try:
                if os.path.exists(WRAPPER_STATE_PATH):
                    with open(WRAPPER_STATE_PATH, 'r') as f:
                        state = json.load(f)
                    
                    cache = state.get('cache', {})
                    if target_url in cache:
                        entry = cache[target_url]
                        if current_time < entry.get('expiry', 0):
                            cached_url = entry.get('url')
                            logger.info(f"CACHE HIT: {target_url[:50]}...")
                            if verify_stream(cached_url, timeout=2.0):
                                safe_print(cached_url)
                                return 0
                            else:
                                if 'cache' in state and target_url in state['cache']:
                                    del state['cache'][target_url]
                                    with open(WRAPPER_STATE_PATH, 'w') as wf: json.dump(state, wf)
            except Exception: pass

        # --- STEP 3: PARALLEL RESOLUTION ---
        domain = "test.whyknot.dev" if CONFIG.get("use_test_version", False) else "whyknot.dev"
        REMOTE_BASE = f"https://{domain}"
        GLOBAL_TIMEOUT = 8.0 
        custom_ua = CONFIG.get("custom_user_agent")

        if forced_tier < 3:
            with ThreadPoolExecutor(max_workers=2) as executor:
                t1_future = None
                t2_future = None
                
                if t1_enabled and forced_tier < 1:
                    t1_future = executor.submit(resolve_tier_1_proxy, target_url, incoming_args, GLOBAL_TIMEOUT, custom_ua, REMOTE_BASE)
                
                if t2_enabled and forced_tier < 2:
                    t2_future = executor.submit(resolve_tier_2_modern, incoming_args, 30.0, custom_ua)

                if t1_future:
                    try:
                        t1_res = t1_future.result()
                        if t1_res:
                            update_wrapper_success(target_url, t1_res['url'])
                            safe_print(t1_res['url'])
                            return 0
                    except Exception: pass

                tasks = {}
                if t1_future and not t1_future.done(): tasks[t1_future] = 1
                if t2_future and not t2_future.done(): tasks[t2_future] = 2
                
                if tasks:
                    try:
                        for future in as_completed(tasks, timeout=GLOBAL_TIMEOUT):
                            res = future.result()
                            if res:
                                update_wrapper_success(target_url, res['url'])
                                safe_print(res['url'])
                                return 0
                    except Exception: pass

                if t3_enabled:
                    logger.info("Tiers 1 & 2 failed. Attempting Tier 3 (Native)...")
                    t3_res = resolve_tier_3_native(incoming_args, timeout=GLOBAL_TIMEOUT)
                    if t3_res:
                        update_wrapper_success(target_url, t3_res['url'])
                        safe_print(t3_res['url'])
                        return 0
        else:
            if t3_enabled:
                t3_res = resolve_tier_3_native(incoming_args)
                if t3_res:
                    safe_print(t3_res['url'])
                    return 0

        logger.error(f"ALL TIERS FAILED for: {target_url}")
        return 1
    except Exception as e:
        import traceback
        logger.error(f"FATAL: {e}\n{traceback.format_exc()}")
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
