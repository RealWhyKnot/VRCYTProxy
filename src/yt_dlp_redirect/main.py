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

LATEST_YTDLP_PATH = os.path.join(APP_BASE_PATH, LATEST_YTDLP_FILENAME)
ORIGINAL_YTDLP_PATH = os.path.join(APP_BASE_PATH, ORIGINAL_YTDLP_FILENAME)
DENO_PATH = os.path.join(APP_BASE_PATH, DENO_FILENAME)

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

def check_proxy_online():
    try:
        logger.info(f"Checking if proxy is online: {REMOTE_SERVER_BASE}")
        req = urllib.request.Request(REMOTE_SERVER_BASE, method='HEAD')
        with urllib.request.urlopen(req, timeout=2.0) as response:
            return response.status == 200
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

    command = [executable_path] + incoming_args
    logger.info(f"Executing command: {subprocess.list2cmdline(command)}")
    
    process_env = os.environ.copy()
    
    if use_custom_temp_dir:
        process_env['TEMP'] = APP_BASE_PATH
        process_env['TMP'] = APP_BASE_PATH
        logger.info(f"Setting TEMP/TMP (temp dir) to: {APP_BASE_PATH}")
    
    process = subprocess.Popen(
        command, 
        stdout=subprocess.PIPE, 
        stderr=subprocess.PIPE, 
        text=True, 
        encoding='utf-8', 
        errors='replace',
        env=process_env
    )

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
    
    # Check for fallback state
    if os.path.exists(WRAPPER_STATE_PATH):
        try:
            with open(WRAPPER_STATE_PATH, 'r') as f:
                state = json.load(f)
                if state.get('force_fallback', False):
                    fallback_until = state.get('fallback_until', 0)
                    if time.time() < fallback_until:
                        logger.warning(f"Fallback mode active (Expires: {time.ctime(fallback_until)}). Disabling Tier 1 (Proxy).")
                        proxy_disabled = True
        except Exception as e:
            logger.error(f"Failed to read wrapper state: {e}")

    # Health check the proxy if not already disabled
    if not proxy_disabled:
        if not check_proxy_online():
            logger.warning("Proxy is offline or unreachable. Disabling Tier 1 (Proxy).")
            proxy_disabled = True

    target_url = find_url_in_args(incoming_args)
    logger.info(f"URL found in arguments: {target_url}")

    if not target_url:
        logger.warning("No URL found in arguments. Passing to VRChat's yt-dlp as a fallback.")
        final_output, return_code = attempt_executable(ORIGINAL_YTDLP_PATH, ORIGINAL_YTDLP_FILENAME, incoming_args)
        if final_output:
            print(final_output, flush=True)
        return return_code

    is_already_proxied = target_url and target_url.startswith(REMOTE_SERVER_BASE)
    is_youtube_url = target_url and not is_already_proxied and re.search(r'youtube\.com|youtu\.be', target_url)
    
    logger.info(f"Analysis: Is YouTube? {bool(is_youtube_url)}. Is already proxied? {is_already_proxied}. Proxy Disabled? {proxy_disabled}")

    if is_youtube_url and not proxy_disabled:
        logger.info("Tier 1: YouTube URL detected. Returning proxied URL directly.")
        encoded_youtube_url = quote_plus(target_url)
        new_url = f"{REMOTE_SERVER_BASE}/stream?url={encoded_youtube_url}"
        
        logger.info(f"Rewriting URL to: {new_url}")
        print(new_url, flush=True)
        logger.info(f"Successfully sent final URL to VRChat: {new_url}")
        return 0 

    if is_already_proxied and not proxy_disabled:
        logger.info("Tier 1: URL is already proxied. Passing through directly.")
        print(target_url, flush=True) 
        logger.info(f"Successfully sent final URL to VRChat: {target_url}")
        return 0
    
    logger.info("Tier 2: Non-YouTube URL (or Proxy Disabled). Attempting to resolve with yt-dlp-latest.exe...")
    
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
    logger.info(f"Adding JS runtime argument: --js-runtimes {js_runtime_arg}")

    resolved_url, return_code = attempt_executable(
        LATEST_YTDLP_PATH, 
        LATEST_YTDLP_FILENAME, 
        tier_2_args, 
        use_custom_temp_dir=True 
    )
    
    if return_code == 0 and resolved_url and resolved_url.startswith('http'):
        logger.info(f"Tier 2 success. Returning URL: {resolved_url}")
        print(resolved_url, flush=True)
        return 0
    else:
        logger.warning(f"Tier 2 failed (Code: {return_code}) or returned invalid URL. Output: {resolved_url}")

    logger.info("Tier 3: Falling back to VRChat's yt-dlp-og.exe...")
    final_output, return_code = attempt_executable(
        ORIGINAL_YTDLP_PATH, 
        ORIGINAL_YTDLP_FILENAME, 
        incoming_args 
    )
    
    if final_output:
        logger.info(f"Tier 3 finished. Returning output to VRChat: {final_output}")
        print(final_output, flush=True)
    else:
        logger.error(f"Tier 3 finished (Code: {return_code}) but produced no output.")
        sys.stderr.write(f"Wrapper Error: Tier 3 failed. Check {LOG_FILE_NAME}\n")

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