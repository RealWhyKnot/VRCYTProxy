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
import hashlib

if platform.system() == 'Windows':
    os.system('color')

try:
    from _version import __version__ as CURRENT_VERSION
except ImportError:
    CURRENT_VERSION = "v2025.12.28.10"

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

EMPTY_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
STARTUP_SCAN_DEPTH = 300 * 1024 * 1024

WRAPPER_EXE_NAME = 'yt-dlp-wrapper.exe'
ORIGINAL_EXE_NAME = 'yt-dlp-og.exe'
SECURE_BACKUP_NAME = 'yt-dlp-og-secure.exe'
TARGET_EXE_NAME = 'yt-dlp.exe'
WRAPPER_SOURCE_DIR_NAME = 'wrapper_files'

_console_handler_ref = None

def calculate_sha256(filepath):
    if not os.path.exists(filepath):
        return None
    sha256_hash = hashlib.sha256()
    try:
        with open(filepath, "rb") as f:
            for byte_block in iter(lambda: f.read(4096), b""):
                sha256_hash.update(byte_block)
        return sha256_hash.hexdigest()
    except Exception:
        return None

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
SECURE_BACKUP_PATH = os.path.join(VRCHAT_TOOLS_DIR, SECURE_BACKUP_NAME)
REDIRECTOR_LOG_PATH = os.path.join(VRCHAT_TOOLS_DIR, REDIRECTOR_LOG_NAME)
WRAPPER_STATE_PATH = os.path.join(VRCHAT_TOOLS_DIR, 'wrapper_state.json')

def update_wrapper_state(is_broken=False):
    try:
        state = {}
        if os.path.exists(WRAPPER_STATE_PATH):
            try:
                with open(WRAPPER_STATE_PATH, 'r') as f:
                    state = json.load(f)
            except Exception: pass
        
        if is_broken:
            state['force_fallback'] = True
            state['fallback_until'] = time.time() + 300  # Disable for 5 minutes
            logger.warning(f"Marking proxy as unstable for 5 minutes (until {time.ctime(state['fallback_until'])})")
        
        with open(WRAPPER_STATE_PATH, 'w') as f:
            json.dump(state, f)
    except Exception as e:
        logger.error(f"Failed to update wrapper state: {e}")

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

def is_game_running():
    try:
        import subprocess
        # Use tasklist for primary detection as it's the most reliable on Windows
        output = subprocess.check_output('tasklist /FI "IMAGENAME eq VRChat.exe" /NH', shell=True, stderr=subprocess.DEVNULL).decode()
        
        if "VRChat.exe" in output or "vrchat.exe" in output:
            # Extract PID - tasklist /NH output format: VRChat.exe  12345  Console ...
            parts = output.split()
            pid = None
            for i, part in enumerate(parts):
                if part.lower() == "vrchat.exe" and i + 1 < len(parts):
                    pid = parts[i+1]
                    break
            
            if pid:
                # Try to get CreationDate for session locking, but don't fail if wmic is unavailable
                try:
                    # format:list is easier to parse than CSV
                    cmd = f'wmic process where ProcessId={pid} get CreationDate /format:list'
                    wmic_out = subprocess.check_output(cmd, shell=True, stderr=subprocess.DEVNULL).decode()
                    for line in wmic_out.splitlines():
                        if 'CreationDate=' in line:
                            return pid, line.split('=')[1].strip()
                except Exception:
                    pass
                
                return pid, "active-session"
        
        return None, None
    except Exception as e:
        # Fallback to very basic check if even tasklist fails
        return None, None

def check_wrapper_health(wrapper_file_list):
    try:
        missing_files = []
        for filename in wrapper_file_list:
            if filename.lower() == WRAPPER_EXE_NAME.lower():
                continue
            
            file_path = os.path.join(VRCHAT_TOOLS_DIR, filename)
            if not os.path.exists(file_path):
                missing_files.append(filename)
        
        if missing_files:
            logger.info(f"Health Check: Restoring missing components: {', '.join(missing_files)}")
            shutil.copytree(SOURCE_WRAPPER_DIR, VRCHAT_TOOLS_DIR, dirs_exist_ok=True)
            return True
    except Exception as e:
        logger.debug(f"Health check failed: {e}")
    return False

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
    target_hash = calculate_sha256(TARGET_YTDLP_PATH)
    source_wrapper_path = os.path.join(SOURCE_WRAPPER_DIR, WRAPPER_EXE_NAME)
    wrapper_hash = calculate_sha256(source_wrapper_path)
    
    backup_hash = calculate_sha256(ORIGINAL_YTDLP_BACKUP_PATH)
    backup_exists = backup_hash is not None and backup_hash != wrapper_hash and backup_hash != EMPTY_SHA256
    
    if target_hash and wrapper_hash and target_hash == wrapper_hash:
        if backup_exists: return PatchState.ENABLED
        else: return PatchState.BROKEN
    
    if target_hash and target_hash != wrapper_hash and target_hash != EMPTY_SHA256:
        return PatchState.DISABLED
        
    if not target_hash or target_hash == EMPTY_SHA256:
        if backup_exists: return PatchState.BROKEN
        else: return PatchState.DISABLED
        
    return PatchState.BROKEN


def _remove_wrapper_files(wrapper_file_list, clean_renamed_exe=True):
    if not wrapper_file_list: return
    logger.debug(f"Cleaning wrapper files (Clean Target: {clean_renamed_exe})...")
    
    source_wrapper_path = os.path.join(SOURCE_WRAPPER_DIR, WRAPPER_EXE_NAME)
    wrapper_hash = calculate_sha256(source_wrapper_path)

    for filename in wrapper_file_list:
        file_path = os.path.join(VRCHAT_TOOLS_DIR, filename)
        
        if filename.lower() == WRAPPER_EXE_NAME.lower() and clean_renamed_exe:
            renamed_path = os.path.join(VRCHAT_TOOLS_DIR, TARGET_EXE_NAME)
            if os.path.exists(renamed_path):
                try:
                    current_hash = calculate_sha256(renamed_path)
                    if current_hash == wrapper_hash or current_hash == EMPTY_SHA256:
                        retry_operation(lambda: os.remove(renamed_path))
                        logger.info(f"Cleanup: Removed wrapper executable: {renamed_path}")
                    else:
                        logger.info(f"Cleanup: Skipping target {renamed_path} (Likely Original)")
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

        source_wrapper_path = os.path.join(SOURCE_WRAPPER_DIR, WRAPPER_EXE_NAME)
        wrapper_hash = calculate_sha256(source_wrapper_path)
        target_hash = calculate_sha256(TARGET_YTDLP_PATH)

        is_target_vrchat_file = (target_hash and target_hash != wrapper_hash and target_hash != EMPTY_SHA256)
        is_target_old_wrapper = (target_hash == wrapper_hash)

        if is_target_vrchat_file:
            logger.info(f"Enabling patch. Found original VRChat file (SHA256: {target_hash[:12]}...).")
            
            # Secure Backup Logic - Verify we aren't backing up a wrapper
            if not os.path.exists(ORIGINAL_YTDLP_BACKUP_PATH) or calculate_sha256(ORIGINAL_YTDLP_BACKUP_PATH) == wrapper_hash:
                logger.info(f"Creating primary backup copy at '{ORIGINAL_EXE_NAME}'...")
                retry_operation(lambda: shutil.copy2(TARGET_YTDLP_PATH, ORIGINAL_YTDLP_BACKUP_PATH))
            
            if not os.path.exists(SECURE_BACKUP_PATH) or calculate_sha256(SECURE_BACKUP_PATH) == wrapper_hash:
                 logger.info(f"Creating secondary secure backup at '{SECURE_BACKUP_NAME}'...")
                 retry_operation(lambda: shutil.copy2(TARGET_YTDLP_PATH, SECURE_BACKUP_PATH))
            
            backup_hash = calculate_sha256(ORIGINAL_YTDLP_BACKUP_PATH)
            secure_backup_hash = calculate_sha256(SECURE_BACKUP_PATH)
            logger.info(f"Backup Verification: Primary={backup_hash[:12]}..., Secure={secure_backup_hash[:12]}...")

            if (not backup_hash or backup_hash == EMPTY_SHA256) and (not secure_backup_hash or secure_backup_hash == EMPTY_SHA256):
                logger.error("Both backups failed or are corrupted! Aborting enable to protect original file.")
                return False, is_waiting_flag
            
            # Ensure we have at least one valid backup
            if calculate_sha256(ORIGINAL_YTDLP_BACKUP_PATH) == wrapper_hash and secure_backup_hash and secure_backup_hash != wrapper_hash and secure_backup_hash != EMPTY_SHA256:
                 logger.warning("Primary backup is a wrapper, restoring from secure backup...")
                 retry_operation(lambda: shutil.copy2(SECURE_BACKUP_PATH, ORIGINAL_YTDLP_BACKUP_PATH))

            logger.info("Backup confirmed. Removing original executable to replace with wrapper...")
            retry_operation(lambda: os.remove(TARGET_YTDLP_PATH))

        elif is_target_old_wrapper:
            logger.info("Updating existing wrapper...")
            
            bh = calculate_sha256(ORIGINAL_YTDLP_BACKUP_PATH)
            sh = calculate_sha256(SECURE_BACKUP_PATH)
            backup_valid = bh and bh != wrapper_hash and bh != EMPTY_SHA256
            secure_valid = sh and sh != wrapper_hash and sh != EMPTY_SHA256

            if not backup_valid and not secure_valid:
                 logger.error("All backups missing or invalid! Cannot safely update. Please verify game integrity.")
                 return False, is_waiting_flag
            
            if backup_valid and not secure_valid:
                logger.info("Restoring missing secure backup from primary...")
                retry_operation(lambda: shutil.copy2(ORIGINAL_YTDLP_BACKUP_PATH, SECURE_BACKUP_PATH))
            elif secure_valid and not backup_valid:
                logger.info("Restoring missing primary backup from secure...")
                retry_operation(lambda: shutil.copy2(SECURE_BACKUP_PATH, ORIGINAL_YTDLP_BACKUP_PATH))

        elif not target_hash or target_hash == EMPTY_SHA256:
            bh = calculate_sha256(ORIGINAL_YTDLP_BACKUP_PATH)
            sh = calculate_sha256(SECURE_BACKUP_PATH)
            if not (bh and bh != wrapper_hash and bh != EMPTY_SHA256):
                 if sh and sh != wrapper_hash and sh != EMPTY_SHA256:
                     logger.info("Primary backup missing, but secure backup found. Restoring primary...")
                     retry_operation(lambda: shutil.copy2(SECURE_BACKUP_PATH, ORIGINAL_YTDLP_BACKUP_PATH))
                 else:
                    logger.info("Target missing and no valid backups found. Waiting for generation.")
                    return False, True

        _remove_wrapper_files(wrapper_file_list, clean_renamed_exe=False)
        
        retry_operation(lambda: shutil.copytree(SOURCE_WRAPPER_DIR, VRCHAT_TOOLS_DIR, dirs_exist_ok=True))
        
        copied_wrapper_path = os.path.join(VRCHAT_TOOLS_DIR, WRAPPER_EXE_NAME)
        if os.path.exists(copied_wrapper_path):
            if os.path.exists(TARGET_YTDLP_PATH):
                current_target_hash = calculate_sha256(TARGET_YTDLP_PATH)
                if current_target_hash == wrapper_hash or current_target_hash == EMPTY_SHA256:
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
        source_wrapper_path = os.path.join(SOURCE_WRAPPER_DIR, WRAPPER_EXE_NAME)
        wrapper_hash = calculate_sha256(source_wrapper_path)

        _remove_wrapper_files(wrapper_file_list, clean_renamed_exe=False)
        
        if os.path.exists(REDIRECTOR_LOG_PATH):
            try:
                retry_operation(lambda: os.remove(REDIRECTOR_LOG_PATH))
            except Exception: pass

        restore_source = None
        bh = calculate_sha256(ORIGINAL_YTDLP_BACKUP_PATH)
        sh = calculate_sha256(SECURE_BACKUP_PATH)
        if bh and bh != wrapper_hash and bh != EMPTY_SHA256:
            restore_source = ORIGINAL_YTDLP_BACKUP_PATH
        elif sh and sh != wrapper_hash and sh != EMPTY_SHA256:
            restore_source = SECURE_BACKUP_PATH
            logger.warning("Primary backup missing or invalid. Using secure backup.")

        if restore_source:
            if os.path.exists(TARGET_YTDLP_PATH):
                current_hash = calculate_sha256(TARGET_YTDLP_PATH)
                if current_hash == wrapper_hash or current_hash == EMPTY_SHA256:
                    logger.info(f"Removing wrapper executable...")
                    retry_operation(lambda: os.remove(TARGET_YTDLP_PATH))
                else:
                    logger.info(f"Target is already a non-wrapper file. Overwriting with backup to be safe.")
            
            logger.info(f"Restoring original file from {os.path.basename(restore_source)}...")
            retry_operation(lambda: shutil.copy2(restore_source, TARGET_YTDLP_PATH))
            logger.info("Patch disabled successfully (Original file restored).")
        else:
            if os.path.exists(TARGET_YTDLP_PATH):
                current_hash = calculate_sha256(TARGET_YTDLP_PATH)
                if current_hash == wrapper_hash or current_hash == EMPTY_SHA256:
                    logger.warning(f"No backup found and target is wrapper. Deleting to force regeneration.")
                    retry_operation(lambda: os.remove(TARGET_YTDLP_PATH))
                else:
                    logger.info(f"Patch disabled (No backup, but target seems to be original file).")
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
    is_paused = False
    current_game_pid = None
    current_game_time = None
    
    last_health_check_time = 0
    last_file_watch_time = 0
    
    logger.info("Scanning for active VRChat session...")
    
    try:
        while True:
            # 1. Process Check (Every poll cycle)
            game_pid, game_time = is_game_running()
            
            if not game_pid:
                if not is_paused:
                    logger.info("VRChat not detected. Pausing operations until game starts...")
                    is_paused = True
                    current_game_pid = None
                    current_game_time = None
                time.sleep(POLL_INTERVAL)
                continue
            
            # Check for session change (restart or first detection)
            if game_pid != current_game_pid or game_time != current_game_time:
                if current_game_pid:
                    logger.info(f"VRChat session change detected (PID: {game_pid}). Resetting log monitor.")
                else:
                    logger.info(f"VRChat detected (PID: {game_pid}). Resuming operations...")
                
                is_paused = False
                current_game_pid = game_pid
                current_game_time = game_time
                current_log_file = None
                last_pos = 0

            # 2. Log Discovery (If needed or session changed)
            if not current_log_file:
                latest_log = find_latest_log_file()
                if latest_log:
                    current_log_file = latest_log
                    logger.info(f"Monitoring Log: {os.path.basename(current_log_file)}")
                    try:
                        file_size = os.path.getsize(latest_log)
                        start_pos = max(0, file_size - STARTUP_SCAN_DEPTH)
                        with open(latest_log, 'r', encoding='utf-8', errors='replace') as f:
                            f.seek(start_pos)
                            lines = f.readlines()
                            for line in lines:
                                instance_type = parse_instance_type_from_line(line)
                                if instance_type:
                                    last_instance_type = instance_type
                            last_pos = f.tell()
                        
                        if not last_instance_type: last_instance_type = "private"
                        logger.info(f"Initial Instance Type: {last_instance_type}")
                    except Exception as e:
                        logger.error(f"Error scanning log: {e}")
                        last_instance_type = "private"

            now = time.time()
            
            # 3. Proactive File Watch (Higher frequency check for yt-dlp regeneration)
            if now - last_file_watch_time > 1.0:
                should_disable = last_instance_type in ['public', 'group_public']
                desired_state = PatchState.DISABLED if should_disable else PatchState.ENABLED
                current_state = get_patch_state()
                
                if desired_state == PatchState.ENABLED and current_state == PatchState.DISABLED:
                    logger.info("VRChat regenerated original yt-dlp.exe. Re-applying wrapper...")
                    enable_patch(WRAPPER_FILE_LIST, False)
                elif desired_state == PatchState.DISABLED and current_state == PatchState.ENABLED:
                    logger.info("World changed to PUBLIC. Restoring original yt-dlp.exe...")
                    disable_patch(WRAPPER_FILE_LIST)
                
                last_file_watch_time = now

            # 4. Health Check (Every 10s while enabled)
            if now - last_health_check_time > 10.0:
                if get_patch_state() == PatchState.ENABLED:
                    check_wrapper_health(WRAPPER_FILE_LIST)
                last_health_check_time = now

            # 5. Log Monitoring
            if current_log_file and os.path.exists(current_log_file):
                try:
                    current_size = os.path.getsize(current_log_file)
                    if last_pos > current_size: last_pos = 0 
                    
                    if current_size > last_pos:
                        with open(current_log_file, 'r', encoding='utf-8', errors='replace') as f:
                            f.seek(last_pos)
                            new_lines = f.readlines()
                            if new_lines:
                                last_pos = f.tell()
                                for line in new_lines:
                                    if any(x in line for x in ["[Video Player] Failed to load", "VideoError", "[AVProVideo] Error"]):
                                        if "whyknot.dev" in line:
                                            logger.warning(f"Detected Proxy Video Error: {line.strip()}")
                                            update_wrapper_state(is_broken=True)
                                        else:
                                            logger.info(f"Detected Non-Proxy Video Error (Ignoring fallback): {line.strip()}")

                                    instance_type = parse_instance_type_from_line(line)
                                    if instance_type and instance_type != last_instance_type:
                                        logger.info(f"Instance changed: {last_instance_type} -> {instance_type}")
                                        last_instance_type = instance_type
                except Exception: pass
            
            time.sleep(POLL_INTERVAL)
    
    except KeyboardInterrupt:
        logger.info("Shutdown signal received...")
    finally:
        stop_event.set()
        disable_patch(WRAPPER_FILE_LIST)
        log_tail_thread.join(timeout=2)

if __name__ == '__main__':
    main()