import os
import sys
import shutil
import time
import logging
import glob
import re
import threading
from logging.handlers import RotatingFileHandler
import platform
import json
from enum import Enum, auto

class PatchState(Enum):
    UNKNOWN = auto()
    ENABLED = auto()
    DISABLED = auto()
    BROKEN = auto()

# --- Constants ---
POLL_INTERVAL = 2  # seconds
LOG_FILE_NAME = 'patcher.log'
REDIRECTOR_LOG_NAME = 'wrapper_debug.log'
CONFIG_FILE_NAME = 'patcher_config.json'

if platform.system() == 'Windows':
    WRAPPER_EXE_NAME = 'main.exe'
    ORIGINAL_EXE_NAME = 'yt-dlp-og.exe'
    TARGET_EXE_NAME = 'yt-dlp.exe'
else:  # Linux/macOS
    WRAPPER_EXE_NAME = 'main'
    ORIGINAL_EXE_NAME = 'yt-dlp-og'
    TARGET_EXE_NAME = 'yt-dlp'

# --- Path Setup ---
def get_application_path():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

APP_BASE_PATH = get_application_path()
LOG_FILE_PATH = os.path.join(APP_BASE_PATH, LOG_FILE_NAME)

# --- Logging Setup ---
def setup_logging():
    logger = logging.getLogger('Patcher')
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(formatter)
    logger.addHandler(ch)
    
    try:
        fh = RotatingFileHandler(LOG_FILE_PATH, maxBytes=2*1024*1024, backupCount=3, mode='w')
        fh.setFormatter(formatter)
        logger.addHandler(fh)
    except PermissionError:
        logger.warning(f"Permission denied for log file: {LOG_FILE_PATH}. Logging to console only.")
        
    return logger

logger = setup_logging()

# --- Configuration Management ---
def load_config(config_path):
    if os.path.exists(config_path):
        try:
            with open(config_path, 'r') as f:
                return json.load(f)
        except Exception:
            logger.warning(f"Could not read config file at {config_path}")
    return {}

def save_config(config_path, config_data):
    try:
        with open(config_path, 'w') as f:
            json.dump(config_data, f, indent=2)
    except Exception:
        logger.error(f"Failed to save config to {config_path}", exc_info=True)

# --- VRChat Path Discovery ---
def get_platform_default_paths():
    system = platform.system()
    if system == 'Windows':
        return [os.path.join(os.path.expanduser('~'), 'AppData', 'LocalLow', 'VRChat', 'VRChat')]
    if system == 'Darwin':
        return [os.path.join(os.path.expanduser('~'), 'Library', 'Application Support', 'VRChat', 'VRChat')]
    if system == 'Linux':
        return [
            os.path.expanduser('~/.local/share/Steam/steamapps/compatdata/438100/pfx/drive_c/users/steamuser/AppData/LocalLow/VRChat/VRChat'),
            os.path.expanduser('~/.steam/steam/steamapps/compatdata/438100/pfx/drive_c/users/steamuser/AppData/LocalLow/VRChat/VRChat'),
        ]
    return []

def prompt_for_log_dir(config_path):
    logger.warning("Could not automatically find VRChat log directory.")
    print("--- VRChat Path Setup ---")
    # ... (rest of the prompt function is unchanged)
    while True:
        user_path = input("Enter VRChat log path: ").strip()
        if os.path.exists(user_path) and os.path.isdir(user_path):
            logger.info(f"User provided valid path: {user_path}")
            save_config(config_path, {'vrchat_log_dir': user_path})
            print(f"Path saved to {config_path}. Thank you.")
            return user_path
        else:
            print("Invalid path. Please try again.")

def get_vrchat_log_dir(base_path):
    config_path = os.path.join(base_path, CONFIG_FILE_NAME)
    config = load_config(config_path)
    
    saved_path = config.get('vrchat_log_dir')
    if saved_path and os.path.exists(saved_path):
        logger.info(f"Using saved VRChat log path: {saved_path}")
        return saved_path
        
    for path in get_platform_default_paths():
        if os.path.exists(path):
            logger.info(f"Found default VRChat log path: {path}")
            save_config(config_path, {'vrchat_log_dir': path})
            return path
            
    return prompt_for_log_dir(config_path)

VRCHAT_LOG_DIR = get_vrchat_log_dir(APP_BASE_PATH)
VRCHAT_TOOLS_DIR = os.path.join(VRCHAT_LOG_DIR, 'Tools')
SOURCE_WRAPPER_EXE = os.path.join(APP_BASE_PATH, 'resources', 'wrapper_files', WRAPPER_EXE_NAME)

TARGET_YTDLP_PATH = os.path.join(VRCHAT_TOOLS_DIR, TARGET_EXE_NAME)
ORIGINAL_YTDLP_BACKUP_PATH = os.path.join(VRCHAT_TOOLS_DIR, ORIGINAL_EXE_NAME)
REDIRECTOR_LOG_PATH = os.path.join(VRCHAT_TOOLS_DIR, REDIRECTOR_LOG_NAME)

# --- State Management ---
def get_patch_state():
    """Determines the current patch state by inspecting files in the Tools directory."""
    backup_exists = os.path.exists(ORIGINAL_YTDLP_BACKUP_PATH)
    target_exists = os.path.exists(TARGET_YTDLP_PATH)

    if not target_exists and not backup_exists:
        return PatchState.DISABLED # Clean state

    if backup_exists:
        # If backup exists, target should be our wrapper.
        if target_exists:
            # Simple check: if backup exists, we assume patch is enabled.
            # A more robust check could compare file hashes/sizes if needed.
            return PatchState.ENABLED
        else:
            # Backup exists but target is gone. VRChat might have cleared it.
            return PatchState.BROKEN

    if target_exists and not backup_exists:
        # Target exists but no backup. Assume it's the original yt-dlp.
        return PatchState.DISABLED

    return PatchState.BROKEN

def enable_patch():
    logger.info("Enabling patch...")
    try:
        if not os.path.exists(VRCHAT_TOOLS_DIR):
            os.makedirs(VRCHAT_TOOLS_DIR)
            logger.info(f"Created Tools directory at: {VRCHAT_TOOLS_DIR}")

        # If yt-dlp.exe exists and isn't a backup, back it up.
        if os.path.exists(TARGET_YTDLP_PATH) and not os.path.exists(ORIGINAL_YTDLP_BACKUP_PATH):
            logger.info(f"Backing up original '{TARGET_EXE_NAME}' to '{ORIGINAL_EXE_NAME}'")
            shutil.move(TARGET_YTDLP_PATH, ORIGINAL_YTDLP_BACKUP_PATH)

        # Copy our wrapper executable
        logger.info(f"Copying wrapper to '{TARGET_YTDLP_PATH}'")
        shutil.copyfile(SOURCE_WRAPPER_EXE, TARGET_YTDLP_PATH)
        
        logger.info("Patch enabled successfully.")
        return True
    except Exception:
        logger.exception("An unexpected error occurred while enabling the patch.")
        return False

def disable_patch():
    logger.info("Disabling patch...")
    try:
        # Remove the wrapper
        if os.path.exists(TARGET_YTDLP_PATH):
            # A simple check to see if the target is our wrapper.
            # This assumes if a backup exists, the target must be the wrapper.
            if os.path.exists(ORIGINAL_YTDLP_BACKUP_PATH):
                 logger.info(f"Removing wrapper file '{TARGET_YTDLP_PATH}'")
                 os.remove(TARGET_YTDLP_PATH)

        # Restore the original from backup
        if os.path.exists(ORIGINAL_YTDLP_BACKUP_PATH):
            logger.info(f"Restoring original '{ORIGINAL_EXE_NAME}'")
            shutil.move(ORIGINAL_YTDLP_BACKUP_PATH, TARGET_YTDLP_PATH)
            logger.info("Patch disabled successfully.")
        else:
            logger.info("No backup found to restore. Patch is considered disabled.")
            
        return True
    except Exception:
        logger.exception("An unexpected error occurred while disabling the patch.")
        return False

def ensure_patch_state(desired_state, current_state):
    """Ensures the patch is in the desired state, taking action if it's not."""
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
    
    # If action fails, the state is likely broken
    return get_patch_state()

# --- Log Monitoring ---
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
    return None

# --- Main Application ---
def main():
    logger.info("Patcher starting up...")
    logger.info(f"VRChat Tools Directory: {VRCHAT_TOOLS_DIR}")
    
    if not os.path.exists(SOURCE_WRAPPER_EXE):
        logger.critical(f"Wrapper source file not found: '{SOURCE_WRAPPER_EXE}'!")
        input("Press Enter to exit..."); sys.exit(1)

    current_log_file = None
    last_pos = 0
    last_instance_type = None
    last_known_patch_state = PatchState.UNKNOWN
    
    # Startup State Check
    logger.info("Performing startup scan...")
    last_known_patch_state = get_patch_state()
    logger.info(f"Initial patch state detected: {last_known_patch_state.name}")

    # Set initial desired state based on a quick log scan
    latest_log = find_latest_log_file()
    if latest_log:
        current_log_file = latest_log
        try:
            with open(latest_log, 'r', encoding='utf-8', errors='replace') as f:
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
            # Self-healing check
            current_state = get_patch_state()
            if current_state != last_known_patch_state:
                logger.warning(f"Detected patch state change on disk! Old: {last_known_patch_state.name}, New: {current_state.name}")
                desired_state = PatchState.DISABLED if last_instance_type in ['public', 'group'] else PatchState.ENABLED
                last_known_patch_state = ensure_patch_state(desired_state, current_state)

            # Log monitoring
            latest_log = find_latest_log_file()
            if not latest_log:
                time.sleep(POLL_INTERVAL); continue

            if latest_log != current_log_file:
                logger.info(f"New log file detected: {os.path.basename(latest_log)}")
                current_log_file, last_pos = latest_log, 0
            
            try:
                with open(current_log_file, 'r', encoding='utf-8', errors='replace') as f:
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
        logger.info("Performing final cleanup. Disabling patch for safety.")
        disable_patch()
        logger.info("Patcher shut down.")

if __name__ == '__main__':
    main()
