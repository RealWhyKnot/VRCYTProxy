import sys
import os
import logging
import subprocess
import re
import threading
import platform
from urllib.parse import quote_plus
from logging.handlers import RotatingFileHandler

if platform.system() != 'Windows':
    print("FATAL: This wrapper is designed to run on Windows only.", file=sys.stderr)
    sys.exit(1)

REMOTE_SERVER_BASE = "https://proxy.whyknot.dev"

ORIGINAL_YTDLP_FILENAME = "yt-dlp-og.exe"
LOG_FILE_NAME = 'wrapper_debug.log'

def get_application_path():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

APP_BASE_PATH = get_application_path()
LOG_FILE_PATH = os.path.join(APP_BASE_PATH, LOG_FILE_NAME)
ORIGINAL_YTDLP_PATH = os.path.join(APP_BASE_PATH, ORIGINAL_YTDLP_FILENAME)

def setup_logging():
    logger = logging.getLogger('RedirectWrapper')
    logger.setLevel(logging.INFO)
    
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    
    try:
        handler = RotatingFileHandler(LOG_FILE_PATH, mode='w', maxBytes=2*1024*1024, backupCount=1)
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    except Exception as e:
        sys.stderr.write(f"FATAL: Could not set up file logging: {e}\n")
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        
    return logger

logger = setup_logging()

def find_url_in_args(args_list):
    for arg in args_list:
        if arg.startswith('http'):
            return arg
    return None

def process_and_execute(incoming_args):
    target_url = find_url_in_args(incoming_args)
    logger.info(f"URL found in arguments: {target_url}")

    is_already_proxied = target_url and target_url.startswith(REMOTE_SERVER_BASE)
    is_youtube_url = target_url and not is_already_proxied and re.search(r'youtube\.com|youtu\.be', target_url)
    
    logger.info(f"Analysis: Is YouTube? {bool(is_youtube_url)}. Is already proxied? {is_already_proxied}.")

    if not os.path.exists(ORIGINAL_YTDLP_PATH):
        logger.critical(f"Original executable '{ORIGINAL_YTDLP_FILENAME}' not found at '{ORIGINAL_YTDLP_PATH}'.")
        sys.exit(1)
    
    final_args = incoming_args
    if is_youtube_url:
        logger.info("YouTube URL detected. Rewriting arguments for proxy.")
        encoded_youtube_url = quote_plus(target_url)
        new_url = f"{REMOTE_SERVER_BASE}/stream?url={encoded_youtube_url}"
        logger.info(f"Rewriting URL to: {new_url}")
        
        temp_args = []
        skip_next = False
        for arg in incoming_args:
            if skip_next:
                skip_next = False
                continue
            if arg == '-f':
                logger.info("Removing '-f' format filter to prevent conflicts.")
                skip_next = True
                continue
            if arg == target_url:
                temp_args.append(new_url)
            else:
                temp_args.append(arg)
        final_args = temp_args
        
    command = [ORIGINAL_YTDLP_PATH] + final_args
    logger.info(f"Executing command: {' '.join(command)}")

    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='replace')

    def log_stream(stream, log_level, log_prefix):
        for line in iter(stream.readline, ''):
            logger.log(log_level, f"[{log_prefix}] {line.strip()}")
        stream.close()

    stderr_thread = threading.Thread(target=log_stream, args=(process.stderr, logging.INFO, "yt-dlp-og-stderr"))
    stderr_thread.start()
    
    final_url_output = ""
    with process.stdout:
        for line in iter(process.stdout.readline, ''):
            stripped_line = line.strip()
            logger.info(f"[yt-dlp-og-stdout] {stripped_line}")
            if stripped_line:
                final_url_output = stripped_line
    
    return_code = process.wait()
    logger.info(f"Original executable finished with exit code {return_code}.")
    stderr_thread.join()

    if return_code == 0:
        print(final_url_output)
        logger.info(f"Successfully sent final URL to VRChat: {final_url_output}")
    else:
        logger.error(f"Original yt-dlp process failed. See logs for details.")
        print(f"ERROR: yt-dlp failed. See {LOG_FILE_NAME} in the VRChat Tools folder for details.")

    return return_code

def main():
    logger.info("--- VRChat yt-dlp Wrapper Initialized ---")
    logger.info(f"Arguments received: {sys.argv[1:]}")
    logger.info(f"Proxy server base: {REMOTE_SERVER_BASE}")
    logger.info(f"Expected original exe path: {ORIGINAL_YTDLP_PATH}")
    
    try:
        return_code = process_and_execute(sys.argv[1:])
        sys.exit(return_code)
    except Exception:
        logger.exception("An unhandled exception occurred in the wrapper.")
        sys.exit(1)

if __name__ == '__main__':
    main()