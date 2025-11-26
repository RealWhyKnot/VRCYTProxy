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
import urllib.request
import urllib.error
import atexit
import signal
import ctypes

if platform.system() == 'Windows':
    os.system('color')

try:
    from _version import __version__ as CURRENT_VERSION
except ImportError:
    CURRENT_VERSION = "v11.22.25-dev"

GITHUB_REPO_OWNER = "RealWhyKnot"
GITHUB_REPO_NAME = "VRCYTProxy"

if platform.system() != 'Windows':
    print("FATAL: This patcher is designed to run on Windows only.", file=sys.stderr)
    sys.exit(1)

class PatchState(Enum):
    UNKNOWN = auto()
    ENABLED = auto()
    DISABLED = auto()
    BROKEN = auto()

POLL_INTERVAL = 3.0 
LOG_FILE_NAME = 'patcher.log'
REDIRECTOR_LOG_NAME = 'wrapper_debug.log'
CONFIG_FILE_NAME = 'patcher_config.json'
WRAPPER_FILE_LIST_NAME = 'wrapper_filelist.json'

VRC_YTDLP_MIN_SIZE_BYTES = 10 * 1024 * 1024
STARTUP_SCAN_DEPTH = 300 * 1024 * 1024

WRAPPER_EXE_NAME = 'yt-dlp-wrapper.exe'
ORIGINAL_EXE_NAME = 'yt-dlp-og.exe'
TARGET_EXE_NAME = 'yt-dlp.exe'
WRAPPER_SOURCE_DIR_NAME = 'wrapper_files'

_console_handler_ref = None

def install_exit_handler():
    global _console_handler_ref
    if platform.system() == 'Windows':
        HandlerRoutine = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_ulong)
        
        def console_handler(ctrl_type):
            if ctrl_type in (0, 2):
                print(f"\n[System] Console close detected (Type: {ctrl_type}). Cleaning up...")
                cleanup_on_exit()
                return True
            return False

        _console_handler_ref = HandlerRoutine(console_handler)
        ctypes.windll.kernel32.SetConsoleCtrlHandler(_console_handler_ref, True)

class Colors:
    RESET = "\033[0m"
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    MAGENTA = "\033[95m"
    CYAN = "\033[96m"
    GREY = "\033[90m"
    BOLD = "\033[1m"
    BG_RED = "\033[41m"

class ColoredFormatter(logging.Formatter):
    def format(self, record):
        color = Colors.RESET
        if record.levelno >= logging.CRITICAL:
            color = Colors.BG_RED + Colors.BOLD + "\033[97m"
        elif record.levelno >= logging.ERROR:
            color = Colors.RED
        elif record.levelno >= logging.WARNING:
            color = Colors.YELLOW
        
        msg_str = str(record.msg)
        if "[Redirector]" in msg_str:
            color = Colors.CYAN
        elif "Switching to ENABLED" in msg_str or "Patch enabled" in msg_str:
            color = Colors.GREEN
        elif "Switching to DISABLED" in msg_str or "Patch disabled" in msg_str:
            color = Colors.GREY
        elif "Startup State Found" in msg_str:
            color = Colors.MAGENTA
        elif "Applying initial state" in msg_str:
            color = Colors.BLUE + Colors.BOLD

        timestamp = self.formatTime(record, self.datefmt)
        if record.exc_info:
            return f"{color}{timestamp} - {record.levelname} - {super().format(record)}{Colors.RESET}"
        return f"{Colors.GREY}{timestamp}{Colors.RESET} - {color}{record.getMessage()}{Colors.RESET}"

def get_application_path():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

APP_BASE_PATH = get_application_path()
LOG_FILE_PATH = os.path.join(APP_BASE_PATH, LOG_FILE_NAME)
WRAPPER_FILE_LIST_PATH = os.path.join(APP_BASE_PATH, WRAPPER_FILE_LIST_NAME)
SOURCE_WRAPPER_DIR = os.path.join(APP_BASE_PATH, 'resources', WRAPPER_SOURCE_DIR_NAME)

def setup_logging():
    logger = logging.getLogger('Patcher')
    logger.setLevel(logging.DEBUG)

    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.DEBUG) 
    ch.setFormatter(ColoredFormatter('%(asctime)s - %(levelname)s - %(message)s'))
    logger.addHandler(ch)

    try:
        file_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
        fh = RotatingFileHandler(LOG_FILE_PATH, maxBytes=10*1024*1024, backupCount=3, encoding='utf-8')
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(file_formatter)
        logger.addHandler(fh)
    except Exception:
        logger.error(f"Failed to set up file logging at {LOG_FILE_PATH}", exc_info=True)
    return logger

logger = setup_logging()

def load_config(config_path):
    if os.path.exists(config_path):
        try:
            with open(config_path, 'r', encoding='utf-8-sig') as f:
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
TARGET_YTDLP_PATH = os.path.join(VRCHAT_TOOLS_DIR, TARGET_EXE_NAME)
ORIGINAL_YTDLP_BACKUP_PATH = os.path.join(VRCHAT_TOOLS_DIR, ORIGINAL_EXE_NAME)
REDIRECTOR_LOG_PATH = os.path.join(VRCHAT_TOOLS_DIR, REDIRECTOR_LOG_NAME)

def check_for_updates():
    if "dev" in CURRENT_VERSION:
        logger.info(f"Running Dev Build ({CURRENT_VERSION}). Skipping update check.")
        return
    logger.info(f"Checking for updates (Current: {CURRENT_VERSION})...")
    api_url = f"https://api.github.com/repos/{GITHUB_REPO_OWNER}/{GITHUB_REPO_NAME}/releases/latest"
    try:
        req = urllib.request.Request(api_url)
        req.add_header('User-Agent', 'VRCYTProxy-Patcher')
        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read().decode('utf-8'))
            latest_tag = data.get('tag_name')
            if latest_tag and latest_tag != CURRENT_VERSION:
                logger.info("-" * 50)
                logger.info(f"NEW UPDATE DETECTED! Latest: {latest_tag} (Current: {CURRENT_VERSION})")
                logger.info(f"Download: {data.get('html_url', 'https://github.com/' + GITHUB_REPO_OWNER + '/' + GITHUB_REPO_NAME)}")
                logger.info("-" * 50)
            else:
                logger.info("Patcher is up to date.")
    except Exception as e:
        logger.debug(f"Update check failed: {e}")

def safe_get_size(filepath):
    try:
        return os.path.getsize(filepath)
    except FileNotFoundError: return 0

def tail_log_file(log_path, stop_event):
    logger.info(f"Starting to monitor wrapper log: {log_path}")
    last_pos = 0
    try:
        if os.path.exists(log_path): last_pos = os.path.getsize(log_path)
    except OSError: pass
    while not stop_event.is_set():
        try:
            if os.path.exists(log_path):
                current_size = os.path.getsize(log_path)
                if last_pos > current_size: last_pos = 0
                
                if current_size > last_pos:
                    with open(log_path, 'r', encoding='utf-8', errors='replace') as f:
                        f.seek(last_pos)
                        new_lines = f.readlines()
                        if new_lines:
                            for line in new_lines:
                                logger.info(f"[Redirector] {line.strip()}")
                            last_pos = f.tell()
        except Exception: pass
        time.sleep(1.0)

def find_latest_log_file():
    try:
        list_of_files = glob.glob(os.path.join(VRCHAT_LOG_DIR, 'output_log_*.txt'))
        return max(list_of_files, key=os.path.getmtime) if list_of_files else None
    except Exception: return None

def retry_operation(func, retries=5, delay=0.5):
    for i in range(retries):
        try:
            return func()
        except (PermissionError, OSError) as e:
            if i < retries - 1:
                logger.debug(f"File locked ({e}). Retrying in {delay}s... ({i+1}/{retries})")
                time.sleep(delay)
            else:
                raise
        except Exception:
            raise

def get_patch_state():
    target_size = safe_get_size(TARGET_YTDLP_PATH)
    backup_exists = os.path.exists(ORIGINAL_YTDLP_BACKUP_PATH)
    
    is_target_our_wrapper = (target_size > 0 and target_size < VRC_YTDLP_MIN_SIZE_BYTES)
    is_target_vrchat_file = (target_size >= VRC_YTDLP_MIN_SIZE_BYTES)

    if is_target_our_wrapper:
        if backup_exists: return PatchState.ENABLED
        else: return PatchState.BROKEN
    if is_target_vrchat_file: return PatchState.DISABLED
    if target_size == 0:
        if backup_exists: return PatchState.BROKEN
        else: return PatchState.DISABLED
    return PatchState.BROKEN

def _remove_wrapper_files(wrapper_file_list, clean_renamed_exe=True):
    if not wrapper_file_list: return
    logger.debug(f"Cleaning wrapper files (Clean Target: {clean_renamed_exe})...")
    for filename in wrapper_file_list:
        file_path = os.path.join(VRCHAT_TOOLS_DIR, filename)
        
        if filename.lower() == WRAPPER_EXE_NAME.lower() and clean_renamed_exe:
            renamed_path = os.path.join(VRCHAT_TOOLS_DIR, TARGET_EXE_NAME)
            if os.path.exists(renamed_path):
                try:
                    if safe_get_size(renamed_path) < VRC_YTDLP_MIN_SIZE_BYTES:
                        retry_operation(lambda: os.remove(renamed_path))
                        logger.info(f"Cleanup: Removed wrapper executable: {renamed_path}")
                    else:
                        logger.info(f"Cleanup: Skipping target {renamed_path} (Size > 10MB, likely Original)")
                except Exception as e:
                    logger.warning(f"Failed to remove {renamed_path}: {e}")

        if os.path.exists(file_path):
            try:
                if os.path.isfile(file_path):
                    retry_operation(lambda: os.remove(file_path))
                elif os.path.isdir(file_path):
                    retry_operation(lambda: shutil.rmtree(file_path))
            except Exception as e:
                logger.warning(f"Failed to remove {file_path}: {e}")

def enable_patch(wrapper_file_list, is_waiting_flag):
    try:
        if not os.path.exists(VRCHAT_TOOLS_DIR):
            os.makedirs(VRCHAT_TOOLS_DIR)

        target_size = safe_get_size(TARGET_YTDLP_PATH)
        is_target_vrchat_file = (target_size >= VRC_YTDLP_MIN_SIZE_BYTES)
        is_target_old_wrapper = (target_size > 0 and target_size < VRC_YTDLP_MIN_SIZE_BYTES)

        if is_target_vrchat_file:
            logger.info(f"Enabling patch. Found original VRChat file ({target_size} bytes).")
            
            if not os.path.exists(ORIGINAL_YTDLP_BACKUP_PATH):
                logger.info(f"Creating secure backup copy at '{ORIGINAL_EXE_NAME}'...")
                retry_operation(lambda: shutil.copy2(TARGET_YTDLP_PATH, ORIGINAL_YTDLP_BACKUP_PATH))
            
            backup_size = safe_get_size(ORIGINAL_YTDLP_BACKUP_PATH)
            logger.info(f"Backup Verification: Size={backup_size} bytes.")

            if not os.path.exists(ORIGINAL_YTDLP_BACKUP_PATH) or backup_size < VRC_YTDLP_MIN_SIZE_BYTES:
                logger.error("Backup failed or corrupted! Aborting enable to protect original file.")
                return False, is_waiting_flag
                
            logger.info("Backup confirmed. Removing original executable to replace with wrapper...")
            retry_operation(lambda: os.remove(TARGET_YTDLP_PATH))

        elif is_target_old_wrapper:
            logger.info("Updating existing wrapper...")
            if not os.path.exists(ORIGINAL_YTDLP_BACKUP_PATH):
                 logger.error("Backup missing! Cannot safely update. Please verify game integrity.")
                 return False, is_waiting_flag

        elif target_size == 0:
            if not os.path.exists(ORIGINAL_YTDLP_BACKUP_PATH):
                logger.info("Target missing and no backup found. Waiting for generation.")
                return False, True

        _remove_wrapper_files(wrapper_file_list, clean_renamed_exe=False)
        
        retry_operation(lambda: shutil.copytree(SOURCE_WRAPPER_DIR, VRCHAT_TOOLS_DIR, dirs_exist_ok=True))
        
        copied_wrapper_path = os.path.join(VRCHAT_TOOLS_DIR, WRAPPER_EXE_NAME)
        if os.path.exists(copied_wrapper_path):
            if os.path.exists(TARGET_YTDLP_PATH):
                if safe_get_size(TARGET_YTDLP_PATH) < VRC_YTDLP_MIN_SIZE_BYTES:
                    retry_operation(lambda: os.remove(TARGET_YTDLP_PATH))
            
            retry_operation(lambda: os.replace(copied_wrapper_path, TARGET_YTDLP_PATH))
            logger.info("Patch enabled successfully.")
            return True, False
        else:
            logger.error(f"Copy failed. '{WRAPPER_EXE_NAME}' not found after copy.")
            return False, is_waiting_flag

    except PermissionError:
        logger.warning("Permission denied. VRChat is likely loading/using the file. Retrying next cycle.")
        return False, is_waiting_flag
    except Exception:
        logger.exception("An unexpected error occurred enabling patch.")
        return False, is_waiting_flag

def disable_patch(wrapper_file_list):
    logger.info("Disabling patch...")
    try:
        _remove_wrapper_files(wrapper_file_list, clean_renamed_exe=False)
        
        if os.path.exists(REDIRECTOR_LOG_PATH):
            try:
                retry_operation(lambda: os.remove(REDIRECTOR_LOG_PATH))
            except Exception: pass

        if os.path.exists(ORIGINAL_YTDLP_BACKUP_PATH):
            if os.path.exists(TARGET_YTDLP_PATH):
                current_size = safe_get_size(TARGET_YTDLP_PATH)
                
                if current_size < VRC_YTDLP_MIN_SIZE_BYTES:
                    logger.info(f"Removing wrapper executable (Size: {current_size} bytes)...")
                    retry_operation(lambda: os.remove(TARGET_YTDLP_PATH))
                else:
                    logger.info(f"Target is already a large file ({current_size} bytes). Overwriting with backup to be safe.")
            
            logger.info("Restoring original file from backup...")
            retry_operation(lambda: shutil.copy2(ORIGINAL_YTDLP_BACKUP_PATH, TARGET_YTDLP_PATH))
            logger.info("Patch disabled successfully (Original file restored).")
        else:
            if os.path.exists(TARGET_YTDLP_PATH):
                current_size = safe_get_size(TARGET_YTDLP_PATH)
                if current_size < VRC_YTDLP_MIN_SIZE_BYTES:
                    logger.warning(f"No backup found and target is wrapper ({current_size} bytes). Deleting to force regeneration.")
                    retry_operation(lambda: os.remove(TARGET_YTDLP_PATH))
                else:
                    logger.info(f"Patch disabled (No backup, but target seems to be original file: {current_size} bytes).")
            else:
                logger.info("Patch disabled (No files found).")

        return True
    except Exception:
        logger.exception("Error disabling patch.")
        return False

def repair_patch(wrapper_file_list):
    logger.warning("Repairing patch state...")
    try:
        _remove_wrapper_files(wrapper_file_list, clean_renamed_exe=True)
        return PatchState.DISABLED
    except Exception:
        return PatchState.BROKEN

instance_info_cache = {}

def parse_instance_type_from_line(line):
    if '[Behaviour] Destination set:' in line or '[Behaviour] Joining' in line:
        wrld_match = re.search(r'(wrld_[a-f0-9\-]+)', line)
        if not wrld_match:
            return None
            
        world_str = line[wrld_match.start():]
        
        if ':' in world_str:
            if '~private' in world_str: return 'invite'
            if '~hidden' in world_str: return 'friends+'
            if '~friends' in world_str: return 'friends'
            
            if '~group' in world_str:
                if 'groupAccessType(public)' in world_str: return 'group_public'
                if 'groupAccessType(plus)' in world_str: return 'group_plus'
                return 'group'
            
            return 'public'
        else:
            return 'public'
    return None

def cleanup_on_exit():
    logger.info("Performing exit cleanup...")
    try:
        if os.path.exists(WRAPPER_FILE_LIST_PATH):
            with open(WRAPPER_FILE_LIST_PATH, 'r', encoding='utf-8-sig') as f:
                files = json.load(f)
            disable_patch(files)
    except Exception as e:
        logger.error(f"Exit cleanup failed: {e}")

atexit.register(cleanup_on_exit)
signal.signal(signal.SIGTERM, lambda signum, frame: sys.exit(0))
signal.signal(signal.SIGINT, lambda signum, frame: sys.exit(0))

def main():
    install_exit_handler()
    
    logger.info(f"Patcher {CURRENT_VERSION} starting up...")
    check_for_updates()
    
    try:
        with open(WRAPPER_FILE_LIST_PATH, 'r', encoding='utf-8-sig') as f:
            WRAPPER_FILE_LIST = json.load(f)
        logger.debug(f"Loaded {len(WRAPPER_FILE_LIST)} items from wrapper file list.")
    except Exception as e:
        logger.critical(f"Failed to read wrapper source files: {e}")
        input("Press Enter to exit..."); sys.exit(1)

    stop_event = threading.Event()
    log_tail_thread = threading.Thread(target=tail_log_file, args=(REDIRECTOR_LOG_PATH, stop_event), daemon=True)
    log_tail_thread.start()

    current_log_file = None
    last_pos = 0
    last_instance_type = None
    is_waiting_for_vrchat_file = False
    has_logged_waiting = False
    
    logger.info("Scanning for existing VRChat session...")
    
    latest_log = find_latest_log_file()
    if latest_log:
        current_log_file = latest_log
        logger.info(f"Monitoring VRChat Log: {os.path.basename(current_log_file)}")
        try:
            file_size = os.path.getsize(latest_log)
            start_pos = max(0, file_size - STARTUP_SCAN_DEPTH)
            logger.info(f"Scanning last {round((file_size - start_pos)/1024/1024, 2)} MB of logs for state...")

            with open(latest_log, 'r', encoding='utf-8', errors='replace') as f:
                f.seek(start_pos)
                lines = f.readlines()
                for line in lines:
                    instance_type = parse_instance_type_from_line(line)
                    if instance_type:
                        last_instance_type = instance_type
                last_pos = f.tell()
                
            if last_instance_type:
                logger.info(f"Startup State Found: World Type = {last_instance_type}")
            else:
                logger.info("No active instance found in recent logs. Defaulting to ENABLED (Private).")
                last_instance_type = "private"

            should_disable = last_instance_type in ['public', 'group_public']
            desired_state = PatchState.DISABLED if should_disable else PatchState.ENABLED
            current_state = get_patch_state()

            if desired_state == PatchState.ENABLED and current_state != PatchState.ENABLED:
                logger.info(f"Applying initial state: ENABLED (Instance: {last_instance_type})")
                enable_patch(WRAPPER_FILE_LIST, False)
            elif desired_state == PatchState.DISABLED and current_state != PatchState.DISABLED:
                logger.info(f"Applying initial state: DISABLED (Instance: {last_instance_type})")
                disable_patch(WRAPPER_FILE_LIST)
            else:
                logger.info(f"Initial state already correct: {current_state.name} (Instance: {last_instance_type})")

        except Exception as e:
            logger.error(f"Error during startup scan: {e}")
            last_instance_type = "private"

    if not last_instance_type:
        last_instance_type = "private"
        
    try:
        while True:
            should_disable = last_instance_type in ['public', 'group_public']
            desired_state = PatchState.DISABLED if should_disable else PatchState.ENABLED
            current_state = get_patch_state()
            
            if is_waiting_for_vrchat_file:
                if safe_get_size(TARGET_YTDLP_PATH) > 0:
                    logger.info("VRChat has regenerated yt-dlp.exe. Resuming patch operations...")
                    is_waiting_for_vrchat_file = False
                    has_logged_waiting = False
                else:
                    if not has_logged_waiting:
                        logger.info("Waiting for VRChat to regenerate yt-dlp.exe... (Rejoin world or restart game to force generation)")
                        has_logged_waiting = True
            else:
                has_logged_waiting = False

            if current_state == PatchState.BROKEN:
                logger.warning("State is BROKEN. Attempting repair.")
                current_state = repair_patch(WRAPPER_FILE_LIST)
                is_waiting_for_vrchat_file = False
            
            elif desired_state == PatchState.ENABLED and current_state == PatchState.DISABLED:
                if not is_waiting_for_vrchat_file:
                    logger.info(f"Switching to ENABLED (Instance: {last_instance_type})")
                    patch_success, next_is_waiting = enable_patch(WRAPPER_FILE_LIST, is_waiting_for_vrchat_file)
                    is_waiting_for_vrchat_file = next_is_waiting
            
            elif desired_state == PatchState.DISABLED and current_state == PatchState.ENABLED:
                logger.info(f"Switching to DISABLED (Instance: {last_instance_type})")
                if disable_patch(WRAPPER_FILE_LIST):
                    current_state = PatchState.DISABLED
                is_waiting_for_vrchat_file = False
            
            elif desired_state == current_state:
                is_waiting_for_vrchat_file = False
            
            latest_log = find_latest_log_file()
            if not latest_log:
                time.sleep(POLL_INTERVAL); continue

            if latest_log != current_log_file:
                logger.info("-" * 30)
                logger.info(f"New log file detected: {os.path.basename(latest_log)}")
                logger.info("-" * 30)
                current_log_file = latest_log
                last_pos = 0
                instance_info_cache.clear()
            
            try:
                if os.path.exists(current_log_file):
                    current_size = os.path.getsize(current_log_file)
                    if last_pos > current_size: last_pos = 0 
                    
                    if current_size > last_pos:
                        with open(current_log_file, 'r', encoding='utf-8', errors='replace') as f:
                            f.seek(last_pos)
                            new_lines = f.readlines()
                            if new_lines:
                                last_pos = f.tell()
                                for line in new_lines:
                                    instance_type = parse_instance_type_from_line(line)
                                    if instance_type and instance_type != last_instance_type:
                                        logger.info(f"Detected instance change: {last_instance_type} -> {instance_type}")
                                        last_instance_type = instance_type
                                        is_waiting_for_vrchat_file = False 
            except Exception:
                pass
            
            time.sleep(POLL_INTERVAL)
    
    except KeyboardInterrupt:
        logger.info("Shutdown signal received...")
    finally:
        stop_event.set()
        disable_patch(WRAPPER_FILE_LIST)
        log_tail_thread.join(timeout=2)

if __name__ == '__main__':
    main()