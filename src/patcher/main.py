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
    ENABLED = auto()    # Our wrapper is yt-dlp.exe, VRC's file is in backup
    DISABLED = auto()   # VRC's file is yt-dlp.exe
    BROKEN = auto()     # Any other state (missing files, etc.)

POLL_INTERVAL = 2  # seconds
LOG_FILE_NAME = 'patcher.log'
REDIRECTOR_LOG_NAME = 'wrapper_debug.log'
CONFIG_FILE_NAME = 'patcher_config.json'

WRAPPER_EXE_NAME = 'yt-dlp-wrapper.exe' # This is the name in resources/wrapper_files
ORIGINAL_EXE_NAME = 'yt-dlp-og.exe'   # This is the backup name for VRC's file
TARGET_EXE_NAME = 'yt-dlp.exe'        # This is the file VRChat calls
WRAPPER_SOURCE_DIR_NAME = 'wrapper_files' # Name of the subfolder in /resources

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

SOURCE_WRAPPER_DIR = os.path.join(APP_BASE_PATH, 'resources', WRAPPER_SOURCE_DIR_NAME)
SOURCE_WRAPPER_FILE = os.path.join(SOURCE_WRAPPER_DIR, WRAPPER_EXE_NAME)

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
        pass 

    while not stop_event.is_set():
        try:
            if os.path.exists(log_path):
                current_size = os.path.getsize(log_path)
                if last_pos > current_size:
                    last_pos = 0
                
                with open(log_path, 'r', encoding='utf-8', errors='replace') as f:
                    f.seek(last_pos)
                    new_lines = f.readlines()
                    if new_lines:
                        for line in new_lines:
                            print(f"[Redirector] {line.strip()}")
                        last_pos = f.tell()
        except Exception:
            pass
        time.sleep(0.5)
    logger.info("Stopping redirector log monitor.")



def safe_get_size(filepath):
    try:
        return os.path.getsize(filepath)
    except FileNotFoundError:
        return 0

def get_patch_state(wrapper_file_size):
    if wrapper_file_size == 0:
        logger.critical("Wrapper file size is 0. Cannot determine state.")
        return PatchState.BROKEN

    target_size = safe_get_size(TARGET_YTDLP_PATH)
    backup_exists = os.path.exists(ORIGINAL_YTDLP_BACKUP_PATH)
    is_target_our_wrapper = (target_size == wrapper_file_size)

    if is_target_our_wrapper:
        if backup_exists:
            return PatchState.ENABLED # Correct state: (target=Wrapper, backup=VRC)
        else:
            return PatchState.BROKEN # Wrapper is active, but VRC backup is gone.
    
    if not is_target_our_wrapper and target_size > 0:
        return PatchState.DISABLED
    
    if target_size == 0:
        if backup_exists:
            return PatchState.BROKEN # Files are missing, but backup is weirdly there
        else:
            return PatchState.DISABLED # Clean slate. VRChat will regenerate.
    
    return PatchState.BROKEN # Catch-all

def enable_patch(wrapper_file_size, wrapper_file_list, is_waiting_flag):
    try:
        if not os.path.exists(VRCHAT_TOOLS_DIR):
            os.makedirs(VRCHAT_TOOLS_DIR)
            logger.info(f"Created Tools directory at: {VRCHAT_TOOLS_DIR}")

        target_size = safe_get_size(TARGET_YTDLP_PATH)
        is_target_our_wrapper = (target_size == wrapper_file_size)

        if not is_target_our_wrapper and target_size > 0:
            logger.info("Enabling patch...")
            logger.info(f"Backing up current VRChat file '{TARGET_EXE_NAME}' to '{ORIGINAL_EXE_NAME}'")
            os.replace(TARGET_YTDLP_PATH, ORIGINAL_YTDLP_BACKUP_PATH)
        elif is_target_our_wrapper:
            if not os.path.exists(ORIGINAL_YTDLP_BACKUP_PATH):
                logger.error("Our wrapper is active but backup is missing! This is a broken state.")
                return False, is_waiting_flag 
        elif target_size == 0:
            if not is_waiting_flag:
                logger.warning(f"'{TARGET_EXE_NAME}' not found. Cannot create backup. Waiting for VRChat to create it.")
            return False, True # Return True for "is_waiting"

        logger.info(f"Copying wrapper files from '{SOURCE_WRAPPER_DIR}' to '{VRCHAT_TOOLS_DIR}'")
        _remove_wrapper_files(wrapper_file_list) # Clean first
        shutil.copytree(SOURCE_WRAPPER_DIR, VRCHAT_TOOLS_DIR, dirs_exist_ok=True)
        
        copied_wrapper_path = os.path.join(VRCHAT_TOOLS_DIR, WRAPPER_EXE_NAME)
        if os.path.exists(copied_wrapper_path):
            logger.info(f"Renaming '{WRAPPER_EXE_NAME}' to '{TARGET_EXE_NAME}'")
            os.replace(copied_wrapper_path, TARGET_YTDLP_PATH)
            logger.info("Patch enabled successfully.")
            return True, False # Return False for "is_waiting"
        else:
            logger.error(f"Copy failed, wrapper exe not found at {copied_wrapper_path} after copy.")
            return False, is_waiting_flag 

    except PermissionError:
        logger.error("Permission denied. Is VRChat running? Failed to enable patch.")
        return False, is_waiting_flag
    except Exception:
        logger.exception("An unexpected error occurred while enabling the patch.")
        return False, is_waiting_flag

def _remove_wrapper_files(wrapper_file_list):
    logger.info("Cleaning old wrapper files...")
    for filename in wrapper_file_list:
        paths_to_check = [
            os.path.join(VRCHAT_TOOLS_DIR, filename),
            os.path.join(VRCHAT_TOOLS_DIR, TARGET_EXE_NAME) # Check for renamed exe
        ]
        
        for file_path in set(paths_to_check): # Use set to avoid double-checking
            if os.path.exists(file_path):
                try:
                    if os.path.isfile(file_path):
                        os.remove(file_path)
                    elif os.path.isdir(file_path):
                        shutil.rmtree(file_path)
                except Exception as e:
                    logger.warning(f"Could not remove old file {file_path}: {e}")

def disable_patch(wrapper_file_list):
    logger.info("Disabling patch...")
    try:
        _remove_wrapper_files(wrapper_file_list)

        if os.path.exists(ORIGINAL_YTDLP_BACKUP_PATH):
            logger.info(f"Restoring original '{ORIGINAL_EXE_NAME}'")
            os.replace(ORIGINAL_YTDLP_BACKUP_PATH, TARGET_YTDLP_PATH)
            logger.info("Patch disabled successfully.")
        else:
            logger.warning("No backup found to restore. VRChat will need to regenerate the file.")
            
        return True
    except PermissionError:
        logger.error("Permission denied. Is VRChat running? Failed to disable patch.")
        return False
    except Exception:
        logger.exception("An unexpected error occurred while disabling the patch.")
        return False

def repair_patch(wrapper_file_list):
    logger.warning("Patch state is BROKEN. Attempting to repair by cleaning files.")
    logger.info("This will allow VRChat to regenerate its own yt-dlp.exe.")
    try:
        _remove_wrapper_files(wrapper_file_list)

        if os.path.exists(ORIGINAL_YTDLP_BACKUP_PATH):
            os.remove(ORIGINAL_YTDLP_BACKUP_PATH)
        
        logger.info("Clean-up successful. New state is DISABLED.")
        return PatchState.DISABLED
        
    except PermissionError:
        logger.error("Permission denied during repair. Is VRChat running?")
        return PatchState.BROKEN
    except Exception:
        logger.exception("Error during repair.")
        return PatchState.BROKEN


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
        match = re.search(r'type:\s*([a-zA-Z0_]+),', line)
        if match:
            instance_type = match.group(1).lower()
            return 'group' if 'group' in instance_type else instance_type
            
    return None

def main():
    logger.info("Patcher starting up...")
    logger.info(f"VRChat Tools Directory: {VRCHAT_TOOLS_DIR}")
    logger.info(f"Patcher Log File: {LOG_FILE_PATH}")
    
    try:
        if not os.path.exists(SOURCE_WRAPPER_DIR):
             logger.critical(f"Wrapper source directory not found: '{SOURCE_WRAPPER_DIR}'!")
             input("Press Enter to exit..."); sys.exit(1)

        WRAPPER_FILE_SIZE = safe_get_size(SOURCE_WRAPPER_FILE)
        if WRAPPER_FILE_SIZE == 0:
            logger.critical(f"Wrapper source file not found or is empty: '{SOURCE_WRAPPER_FILE}'!")
            input("Press Enter to exit..."); sys.exit(1)
        
        WRAPPER_FILE_LIST = os.listdir(SOURCE_WRAPPER_DIR)
        if not WRAPPER_FILE_LIST:
            logger.critical(f"Wrapper source directory is empty: '{SOURCE_WRAPPER_DIR}'!")
            input("Press Enter to exit..."); sys.exit(1)

        logger.info(f"Wrapper exe size identified: {WRAPPER_FILE_SIZE} bytes.")
        logger.info(f"Wrapper file list loaded ({len(WRAPPER_FILE_LIST)} files).")

    except Exception as e:
        logger.critical(f"Failed to read wrapper source files: {e}", exc_info=True)
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
    
    is_waiting_for_vrchat_file = False
    
    logger.info("Performing startup scan...")
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
    else:
        logger.info("No recent instance join found. Defaulting to ENABLED.")
        last_instance_type = "private" # Default to a state that enables patch
        
    logger.info("Startup complete. Now monitoring for changes...")
    try:
        while True:
            
            desired_state = PatchState.DISABLED if last_instance_type in ['public', 'group'] else PatchState.ENABLED
            
            current_state = get_patch_state(WRAPPER_FILE_SIZE)
            
            if current_state == PatchState.BROKEN:
                logger.warning(f"Patch state is BROKEN. (Desired: {desired_state.name})")
                current_state = repair_patch(WRAPPER_FILE_LIST)
                is_waiting_for_vrchat_file = False # Reset waiting flag

            elif desired_state == PatchState.ENABLED and current_state == PatchState.DISABLED:
                if not is_waiting_for_vrchat_file:
                    logger.warning(f"State mismatch: Desired=ENABLED, Current=DISABLED. Attempting to patch...")
                
                patch_success, is_waiting = enable_patch(WRAPPER_FILE_SIZE, WRAPPER_FILE_LIST, is_waiting_for_vrchat_file)
                is_waiting_for_vrchat_file = is_waiting
                
                if patch_success:
                    current_state = PatchState.ENABLED
                
            elif desired_state == PatchState.DISABLED and current_state == PatchState.ENABLED:
                logger.warning(f"State mismatch: Desired=DISABLED, Current=ENABLED. Disabling patch...")
                if disable_patch(WRAPPER_FILE_LIST):
                    current_state = PatchState.DISABLED
                is_waiting_for_vrchat_file = False # Reset waiting flag
            
            elif desired_state == current_state:
                is_waiting_for_vrchat_file = False
            
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
                                is_waiting_for_vrchat_file = False # Reset waiting flag
                                break # Break to re-run the main state machine
            
            except FileNotFoundError:
                logger.warning(f"Log file '{current_log_file}' disappeared. Searching for new one.")
                current_log_file, last_pos = None, 0
            except Exception:
                logger.exception("Error reading VRChat log file.")
                time.sleep(POLL_INTERVAL)
            
            time.sleep(POLL_INTERVAL)
    
    except KeyboardInterrupt:
        logger.info("Shutdown signal received...")
    
    finally:
        logger.info("Stopping log monitor thread...")
        stop_event.set()
        logger.info("Performing final cleanup. Disabling patch for safety.")
        disable_patch(WRAPPER_FILE_LIST)
        log_tail_thread.join(timeout=2)
        logger.info("Patcher shut down.")

if __name__ == '__main__':
    main()