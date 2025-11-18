import sys
import os
import time
import logging
import re
import glob
import shutil
import threading
from logging.handlers import RotatingFileHandler
import platform
import json
from enum import Enum, auto

if platform.system() != 'Windows':
    print("FATAL: This patcher is designed to run on Windows only.", file=sys.stderr)
    sys.exit(1)

class PatchState(Enum):
    UNKNOWN = auto()
    ENABLED = auto()
    DISABLED = auto()
    BROKEN = auto()

POLL_INTERVAL = 2  # seconds
LOG_FILE_NAME = 'patcher.log'
REDIRECTOR_LOG_NAME = 'wrapper_debug.log'
CONFIG_FILE_NAME = 'patcher_config.json'

WRAPPER_EXE_NAME = 'yt-dlp-wrapper.exe'
ORIGINAL_EXE_NAME = 'yt-dlp-og.exe'
TARGET_EXE_NAME = 'yt-dlp.exe'

def get_application_path():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

APP_BASE_PATH = get_application_path()
LOG_FILE_PATH = os.path.join(APP_BASE_PATH, LOG_FILE_NAME)

def setup_logging():
    logger = logging.getLogger('Patcher')
    logger.setLevel(logging.INFO)
    
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(formatter)
    logger.addHandler(ch)
    
    try:
        fh = RotatingFileHandler(LOG_FILE_PATH, maxBytes=10*1024*1024, backupCount=3, encoding='utf-8')
        fh.setLevel(logging.INFO)
        fh.setFormatter(formatter)
        logger.addHandler(fh)
    except Exception:
        logger.error(f"Failed to set up file logging at {LOG_FILE_PATH}", exc_info=True)

    return logger

logger = setup_logging()

def load_config(config_path):
    if os.path.exists(config_path):
        try:
            with open(config_path, 'r') as f:
                return json.load(f)
        except Exception:
            logger.error(f"Failed to load config from {config_path}", exc_info=True)
    return {}

def save_config(config_path, config_data):
    try:
        with open(config_path, 'w') as f:
            json.dump(config_data, f, indent=2)
    except Exception:
        logger.error(f"Failed to save config to {config_path}", exc_info=True)

def get_platform_default_paths():
    return [os.path.join(os.path.expanduser('~'), 'AppData', 'LocalLow', 'VRChat', 'VRChat')]

def prompt_for_log_dir(config_path):
    logger.warning("Could not automatically find VRChat log directory.")
    print("--- VRChat Path Setup ---")
    print("Could not find the VRChat log directory. Please provide it manually.")
    print(r"Example: C:\Users\YourUser\AppData\LocalLow\VRChat\VRChat")
    
    while True:
        user_path = input("Enter VRChat log path: ").strip()
        
        if os.path.exists(user_path) and os.path.isdir(user_path):
            logger.info(f"User provided valid path: {user_path}")
            save_config(config_path, {'vrchat_log_dir': user_path})
            return user_path
        else:
            logger.error(f"Invalid path provided: {user_path}")
            print("Invalid path. Please try again.")

def get_vrchat_log_dir(base_path):
    config_path = os.path.join(base_path, CONFIG_FILE_NAME)
    config = load_config(config_path)
    
    log_dir = config.get('vrchat_log_dir')
    if log_dir and os.path.exists(log_dir) and os.path.isdir(log_dir):
        logger.info(f"Loaded VRChat log directory from config: {log_dir}")
        return log_dir
        
    logger.info("Checking default VRChat log paths...")
    for path in get_platform_default_paths():
        if os.path.exists(path) and os.path.isdir(path):
            logger.info(f"Found VRChat log directory at: {path}")
            save_config(config_path, {'vrchat_log_dir': path})
            return path
            
    return prompt_for_log_dir(config_path)

VRCHAT_LOG_DIR = get_vrchat_log_dir(APP_BASE_PATH)
VRCHAT_TOOLS_DIR = os.path.join(VRCHAT_LOG_DIR, 'Tools')
SOURCE_WRAPPER_EXE = os.path.join(APP_BASE_PATH, 'resources', 'wrapper_files', WRAPPER_EXE_NAME)

TARGET_YTDLP_PATH = os.path.join(VRCHAT_TOOLS_DIR, TARGET_EXE_NAME)
ORIGINAL_YTDLP_BACKUP_PATH = os.path.join(VRCHAT_TOOLS_DIR, ORIGINAL_EXE_NAME)
REDIRECTOR_LOG_PATH = os.path.join(VRCHAT_TOOLS_DIR, REDIRECTOR_LOG_NAME)


def tail_log_file(log_path, stop_event):
    logger.info(f"Starting to monitor redirector log: {log_path}")
    last_pos = 0
    
    try:
        if os.path.exists(log_path):
            last_pos = os.path.getsize(log_path)
    except OSError:
        pass # File might not exist yet, which is fine

    while not stop_event.is_set():
        try:
            if os.path.exists(log_path):
                current_size = os.path.getsize(log_path)
                if last_pos > current_size:
                    last_pos = 0 # Log was rotated or truncated
                
                with open(log_path, 'r', encoding='utf-8', errors='replace') as f:
                    f.seek(last_pos)
                    new_lines = f.readlines()
                    if new_lines:
                        for line in new_lines:
                            print(f"[Redirector] {line.strip()}")
                        last_pos = f.tell()
        except Exception:
            pass
        time.sleep(0.5) # Check every 500ms
    logger.info("Stopping redirector log monitor.")


def get_patch_state():
    backup_exists = os.path.exists(ORIGINAL_YTDLP_BACKUP_PATH)
    target_exists = os.path.exists(TARGET_YTDLP_PATH)

    if not target_exists and not backup_exists:
        return PatchState.DISABLED # Clean state

    if backup_exists:
        if target_exists:
            return PatchState.ENABLED
        else:
            return PatchState.BROKEN

    if target_exists and not backup_exists:
        return PatchState.DISABLED

    return PatchState.BROKEN

def enable_patch():
    logger.info("Enabling patch...")
    try:
        if not os.path.exists(VRCHAT_TOOLS_DIR):
            os.makedirs(VRCHAT_TOOLS_DIR)
            logger.info(f"Created Tools directory at: {VRCHAT_TOOLS_DIR}")

        if os.path.exists(TARGET_YTDLP_PATH) and not os.path.exists(ORIGINAL_YTDLP_BACKUP_PATH):
            logger.info(f"Backing up original '{TARGET_EXE_NAME}' to '{ORIGINAL_EXE_NAME}'")
            shutil.move(TARGET_YTDLP_PATH, ORIGINAL_YTDLP_BACKUP_PATH)

        logger.info(f"Copying wrapper to '{TARGET_YTDLP_PATH}'")
        shutil.copyfile(SOURCE_WRAPPER_EXE, TARGET_YTDLP_PATH)
        
        logger.info("Patch enabled successfully.")
        return True
    except PermissionError:
        logger.error("Permission denied. Is VRChat running? Failed to enable patch.")
        return False
    except Exception:
        logger.exception("An unexpected error occurred while enabling the patch.")
        return False

def disable_patch():
    logger.info("Disabling patch...")
    try:
        if os.path.exists(TARGET_YTDLP_PATH):
            if os.path.exists(ORIGINAL_YTDLP_BACKUP_PATH):
                logger.info(f"Removing wrapper file '{TARGET_YTDLP_PATH}'")
                os.remove(TARGET_YTDLP_PATH)

        if os.path.exists(ORIGINAL_YTDLP_BACKUP_PATH):
            logger.info(f"Restoring original '{ORIGINAL_EXE_NAME}'")
            shutil.move(ORIGINAL_YTDLP_BACKUP_PATH, TARGET_YTDLP_PATH)
            logger.info("Patch disabled successfully.")
        else:
            logger.info("No backup found to restore. Patch is considered disabled.")
            
        return True
    except PermissionError:
        logger.error("Permission denied. Is VRChat running? Failed to disable patch.")
        return False
    except Exception:
        logger.exception("An unexpected error occurred while disabling the patch.")
        return False

def ensure_patch_state(desired_state, current_state):
    if current_state == desired_state:
        logger.info(f"Patch is already in the desired state ({desired_state.name}). No action needed.")
        return current_state

    logger.warning(f"State mismatch: current is {current_state.name}, desired is {desired_state.name}. Taking action.")

    if desired_state == PatchState.ENABLED:
        if enable_patch():
            return PatchState.ENABLED
    elif desired_state == PatchState.DISABLED:
        if disable_patch():
            return PatchState.DISABLED
    
    return get_patch_state()

def find_latest_log_file():
    try:
        list_of_files = glob.glob(os.path.join(VRCHAT_LOG_DIR, 'output_log_*.txt'))
        return max(list_of_files, key=os.path.getmtime) if list_of_files else None
    except Exception:
        logger.exception("Error finding latest VRChat log file.")
        return None

def parse_instance_type_from_line(line):
    if '[Behaviour] Destination set:' in line or '[Behaviour] Joining' in line:
        match = re.search(r'~(private|public|friends|friends\+|hidden|invite|group|group\+)\(', line)
        if match:
            instance_type = match.group(1).lower()
            return 'group' if 'group' in instance_type else instance_type
            
    if '[API] Creating world instance {' in line:
        match = re.search(r'type:\s*([a-zA-Z0-9_]+),', line)
        if match:
            instance_type = match.group(1).lower()
            return 'group' if 'group' in instance_type else instance_type
            
    return None

def main():
    logger.info("Patcher starting up...")
    logger.info(f"VRChat Tools Directory: {VRCHAT_TOOLS_DIR}")
    logger.info(f"Patcher Log File: {LOG_FILE_PATH}")
    
    if not os.path.exists(SOURCE_WRAPPER_EXE):
        logger.critical(f"Wrapper source file not found: '{SOURCE_WRAPPER_EXE}'!")
        input("Press Enter to exit..."); sys.exit(1)

    stop_event = threading.Event()
    log_tail_thread = threading.Thread(
        target=tail_log_file,
        args=(REDIRECTOR_LOG_PATH, stop_event),
        daemon=True
    )
    log_tail_thread.start()

    current_log_file = None
    last_pos = 0
    last_instance_type = None
    last_known_patch_state = PatchState.UNKNOWN
    
    logger.info("Performing startup scan...")
    last_known_patch_state = get_patch_state()
    logger.info(f"Initial patch state detected: {last_known_patch_state.name}")

    latest_log = find_latest_log_file()
    if latest_log:
        current_log_file = latest_log
        try:
            with open(latest_log, 'r', encoding='utf-8', errors='replace') as f:
                file_size = os.path.getsize(latest_log)
                f.seek(max(0, file_size - 16384)) # Read last 16KB
                lines = f.readlines()
                for line in reversed(lines):
                    instance_type = parse_instance_type_from_line(line)
                    if instance_type:
                        last_instance_type = instance_type
                        break
        except Exception:
            logger.exception("Could not read initial instance type.")
        
        try:
            last_pos = os.path.getsize(latest_log)
        except OSError:
            last_pos = 0

    if last_instance_type:
        logger.info(f"Last known instance type from logs: '{last_instance_type}'")
        desired_state = PatchState.DISABLED if last_instance_type in ['public', 'group'] else PatchState.ENABLED
    else:
        logger.info("No recent instance join found. Defaulting to ENABLED state.")
        desired_state = PatchState.ENABLED
        
    last_known_patch_state = ensure_patch_state(desired_state, last_known_patch_state)

    logger.info("Startup complete. Now monitoring for changes...")
    try:
        while True:
            current_state_on_disk = get_patch_state()
            if current_state_on_disk != last_known_patch_state:
                logger.warning(f"Detected patch state change on disk! Old: {last_known_patch_state.name}, New: {current_state_on_disk.name}")
                desired_state = PatchState.DISABLED if last_instance_type in ['public', 'group'] else PatchState.ENABLED
                logger.warning(f"Re-enforcing desired state: {desired_state.name}")
                last_known_patch_state = ensure_patch_state(desired_state, current_state_on_disk)

            latest_log = find_latest_log_file()
            if not latest_log:
                time.sleep(POLL_INTERVAL); continue

            if latest_log != current_log_file:
                logger.info(f"New log file detected: {os.path.basename(latest_log)}")
                current_log_file, last_pos = latest_log, 0
            
            try:
                with open(current_log_file, 'r', encoding='utf-8', errors='replace') as f:
                    current_size = os.path.getsize(current_log_file)
                    if last_pos > current_size:
                        last_pos = 0
                        
                    f.seek(last_pos)
                    new_lines = f.readlines()
                    if new_lines:
                        last_pos = f.tell()
                        for line in new_lines:
                            instance_type = parse_instance_type_from_line(line)
                            if instance_type and instance_type != last_instance_type:
                                logger.info(f"Detected new instance join: type '{instance_type}'")
                                last_instance_type = instance_type
                                
                                desired_state = PatchState.DISABLED if instance_type in ['public', 'group'] else PatchState.ENABLED
                                current_state = get_patch_state()
                                last_known_patch_state = ensure_patch_state(desired_state, current_state)
            
            except FileNotFoundError:
                logger.warning(f"Log file '{current_log_file}' disappeared. Searching for new one.")
                current_log_file, last_pos = None, 0
            except Exception:
                logger.exception("Error reading VRChat log file.")
                time.sleep(POLL_INTERVAL) # Avoid spamming errors
            
            time.sleep(POLL_INTERVAL)
    
    except KeyboardInterrupt:
        logger.info("Shutdown signal received...")
    
    finally:
        logger.info("Stopping log monitor thread...")
        stop_event.set() # Tell the thread to stop
        logger.info("Performing final cleanup. Disabling patch for safety.")
        disable_patch()
        log_tail_thread.join(timeout=2) # Wait for thread to finish
        logger.info("Patcher shut down.")

if __name__ == '__main__':
    main()