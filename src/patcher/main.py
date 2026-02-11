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
    from _version import __build_type__ as BUILD_TYPE
except ImportError:
    CURRENT_VERSION = "v2026.01.19.dev-main-fb2d4aa"
    BUILD_TYPE = "DEV"

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

def load_config(config_path):
    defaults = {
        "video_error_patterns": [
            "[Video Player] Failed to load", 
            "VideoError", 
            "[AVProVideo] Error", 
            "[VideoTXL] Error", 
            "Loading failed"
        ],
        "instance_patterns": {
            "invite": "~private",
            "friends+": "~hidden",
            "friends": "~friends",
            "group_public": "groupAccessType(public)",
            "group_plus": "groupAccessType(plus)",
            "group": "~group"
        },
        "proxy_domain": "whyknot.dev",
        "debug_mode": BUILD_TYPE == "DEV",
        "force_patch_in_public": False,
        "auto_update_check": True
    }
    config = defaults.copy()
    if os.path.exists(config_path):
        try:
            with open(config_path, 'r', encoding='utf-8-sig') as f:
                user_config = json.load(f)
                if not isinstance(user_config, dict):
                    raise ValueError("Config must be a JSON object")
                
                needs_save = False
                for k, v in defaults.items():
                    if k not in user_config:
                        user_config[k] = v
                        needs_save = True
                config = user_config
                if needs_save: save_config(config_path, config)
        except (json.JSONDecodeError, ValueError) as e:
            logger.error(f"Failed to load config from {config_path}: {e}. Regenerating defaults...")
            save_config(config_path, defaults)
        except Exception:
            logger.error(f"Unexpected error loading config from {config_path}", exc_info=True)
    else:
        save_config(config_path, defaults)
    return config

def setup_logging():
    logger = logging.getLogger('Patcher')
    
    # Check config for debug mode early
    config_path = os.path.join(APP_BASE_PATH, CONFIG_FILE_NAME)
    temp_cfg = load_config(config_path)
    is_debug = temp_cfg.get("debug_mode", BUILD_TYPE == "DEV")
    
    level = logging.DEBUG if is_debug else logging.ERROR
    logger.setLevel(level)

    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(level) 
    ch.setFormatter(ColoredFormatter('%(asctime)s - %(levelname)s - %(message)s'))
    logger.addHandler(ch)

    try:
        file_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
        fh = RotatingFileHandler(LOG_FILE_PATH, maxBytes=10*1024*1024, backupCount=3, encoding='utf-8')
        fh.setLevel(level)
        fh.setFormatter(file_formatter)
        logger.addHandler(fh)
    except Exception:
        pass
    return logger

logger = setup_logging()

def save_config(config_path, config_data):
    try:
        with open(config_path, 'w', encoding='utf-8') as f:
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
            cfg = load_config(config_path)
            cfg['vrchat_log_dir'] = user_path
            save_config(config_path, cfg)
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
            config['vrchat_log_dir'] = path
            save_config(config_path, config)
            return path
    return prompt_for_log_dir(config_path)

CONFIG = load_config(os.path.join(APP_BASE_PATH, CONFIG_FILE_NAME))
VRCHAT_LOG_DIR = get_vrchat_log_dir(APP_BASE_PATH)
VRCHAT_TOOLS_DIR = os.path.join(VRCHAT_LOG_DIR, 'Tools')
TARGET_YTDLP_PATH = os.path.join(VRCHAT_TOOLS_DIR, TARGET_EXE_NAME)
ORIGINAL_YTDLP_BACKUP_PATH = os.path.join(VRCHAT_TOOLS_DIR, ORIGINAL_EXE_NAME)
SECURE_BACKUP_PATH = os.path.join(VRCHAT_TOOLS_DIR, SECURE_BACKUP_NAME)
REDIRECTOR_LOG_PATH = os.path.join(VRCHAT_TOOLS_DIR, REDIRECTOR_LOG_NAME)
WRAPPER_STATE_PATH = os.path.join(VRCHAT_TOOLS_DIR, 'wrapper_state.json')

def update_wrapper_state(is_broken=False, duration=None, failed_url=None):
    try:
        state = {'consecutive_errors': 0, 'failed_urls': {}}
        if os.path.exists(WRAPPER_STATE_PATH):
            try:
                with open(WRAPPER_STATE_PATH, 'r') as f:
                    state = json.load(f)
            except Exception: pass
        
        if 'failed_urls' not in state: state['failed_urls'] = {}

        if is_broken:
            count = state.get('consecutive_errors', 0) + 1
            state['consecutive_errors'] = count
            
            if failed_url:
                # Store the URL and when it failed. We block it for a while.
                state['failed_urls'][failed_url] = {
                    'expiry': time.time() + 300, # Block this specific URL for 5 mins
                    'tier': 1 # We'll implement tier escalation in wrapper
                }
                logger.warning(f"URL Failed: {failed_url}. Force bypassing proxy for this URL for 5m.")
            else:
                state['force_fallback'] = True
                if duration:
                    wait_time = duration
                else:
                    if count <= 1: wait_time = 60 # Transient
                    elif count == 2: wait_time = 300 # Standard
                    elif count == 3: wait_time = 900 # Extended
                    else: wait_time = 3600 # Max
                state['fallback_until'] = time.time() + wait_time
                logger.warning(f"Proxy Error #{count}. Falling back for {wait_time}s (until {time.ctime(state['fallback_until'])})")
        else:
            state['consecutive_errors'] = 0
            state['force_fallback'] = False
            # We don't necessarily clear failed_urls here as they are per-URL
        
        # Cleanup expired failed_urls
        now = time.time()
        state['failed_urls'] = {u: d for u, d in state.get('failed_urls', {}).items() if d.get('expiry', 0) > now}

        with open(WRAPPER_STATE_PATH, 'w') as f:
            json.dump(state, f)
    except Exception as e:
        logger.error(f"Failed to update wrapper state: {e}")

def check_for_updates():
    if not CONFIG.get("auto_update_check", True):
        return
    if BUILD_TYPE == "DEV":
        logger.debug(f"Running Dev Build ({CURRENT_VERSION}). Skipping update check.")
        return
    logger.debug(f"Checking for updates (Current: {CURRENT_VERSION})...")
    api_url = f"https://api.github.com/repos/{GITHUB_REPO_OWNER}/{GITHUB_REPO_NAME}/releases/latest"
    try:
        req = urllib.request.Request(api_url)
        req.add_header('User-Agent', 'VRCYTProxy-Patcher')
        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read().decode('utf-8'))
            latest_tag = data.get('tag_name')
            if latest_tag and latest_tag != CURRENT_VERSION:
                # Force visibility for update detections regardless of debug_mode
                print("-" * 50)
                print(f"{Colors.BOLD}{Colors.MAGENTA}NEW UPDATE DETECTED!{Colors.RESET} Latest: {latest_tag} (Current: {CURRENT_VERSION})")
                print(f"Download: {data.get('html_url', 'https://github.com/' + GITHUB_REPO_OWNER + '/' + GITHUB_REPO_NAME)}")
                print("-" * 50)
            else:
                logger.debug("Patcher is up to date.")
    except Exception as e:
        logger.debug(f"Update check failed: {e}")

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
        return max(list_of_files) if list_of_files else None
    except Exception: return None

uac_requested = False

def is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except:
        return False

def remove_readonly(path):
    """Recursively remove Read-Only attribute from a file or directory."""
    if not os.path.exists(path): return

    def make_writable(p):
        try:
            import stat
            current_mode = os.stat(p).st_mode
            if not (current_mode & stat.S_IWRITE):
                # logger.debug(f"Removing Read-Only attribute: {p}")
                os.chmod(p, current_mode | stat.S_IWRITE)
        except Exception: pass

    if os.path.isfile(path):
        make_writable(path)
    else:
        make_writable(path) # The folder itself
        for root, dirs, files in os.walk(path):
            for name in dirs:
                make_writable(os.path.join(root, name))
            for name in files:
                make_writable(os.path.join(root, name))

def fix_permissions(path):
    global uac_requested
    
    if uac_requested:
        return

    if platform.system() != 'Windows': return
    
    if os.path.isfile(path):
        target_dir = os.path.dirname(path)
    else:
        target_dir = path
        
    logger.info(f"Requesting Admin privileges to fix permissions (Recursive) on: {target_dir}")
    
    # 1. Take Ownership recursively
    # 2. Grant Full Control to the current user recursively
    cmd = f'/c takeown /f "{target_dir}" /r /d y && icacls "{target_dir}" /grant "%USERNAME%":F /T /C'
    
    try:
        # 1 = SW_SHOWNORMAL
        ctypes.windll.shell32.ShellExecuteW(None, "runas", "cmd.exe", cmd, None, 1)
        uac_requested = True
    except Exception as e:
        logger.error(f"Failed to request admin permissions: {e}")

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

    except PermissionError as e:
        logger.warning(f"Permission denied: {e}")
        
        # Try to fix read-only issues regardless of error type
        remove_readonly(TARGET_YTDLP_PATH)
        remove_readonly(VRCHAT_TOOLS_DIR)

        if getattr(e, 'winerror', None) == 32:
            logger.warning("The file is currently in use by another program.")
        elif getattr(e, 'winerror', None) == 5:
            if not is_admin():
                if not uac_requested:
                    logger.warning("Access is denied. Attempting to fix permissions via UAC...")
                    fix_permissions(VRCHAT_TOOLS_DIR)
                else:
                    logger.warning("Access is denied. UAC was already requested this session. Please check folder permissions manually.")
            else:
                 logger.error("Access denied even with Admin privileges! Check if the file is locked by another program or if it's marked as Read-Only.")
        else:
             logger.warning("The file or folder is currently inaccessible.")

        logger.warning("Retrying next cycle.")
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
    except PermissionError as e:
        logger.warning(f"Permission denied while disabling patch: {e}")
        
        remove_readonly(TARGET_YTDLP_PATH)
        remove_readonly(VRCHAT_TOOLS_DIR)

        if getattr(e, 'winerror', None) == 32:
            logger.warning("The file is currently in use by another program.")
        elif getattr(e, 'winerror', None) == 5:
            if not is_admin():
                if not uac_requested:
                    logger.warning("Access is denied. Attempting to fix permissions via UAC...")
                    fix_permissions(VRCHAT_TOOLS_DIR)
                else:
                    logger.warning("Access is denied. UAC was already requested this session.")
            else:
                 logger.error("Access denied even with Admin privileges!")
        return False
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
        if not wrld_match: return None
        world_str = line[wrld_match.start():]
        patterns = CONFIG.get("instance_patterns", {})
        
        if ':' in world_str:
            if patterns.get("invite") in world_str: return 'invite'
            if patterns.get("friends+") in world_str: return 'friends+'
            if patterns.get("friends") in world_str: return 'friends'
            
            if patterns.get("group") in world_str:
                if patterns.get("group_public") in world_str: return 'group_public'
                if patterns.get("group_plus") in world_str: return 'group_plus'
                return 'group'
            
            return 'public'
        else:
            return 'public'
    return None

class LogMonitor:
    def __init__(self):
        self.current_log = None
        self.last_pos = 0
        self.last_instance_type = "private"
        self.proxy_domain = CONFIG.get("proxy_domain", "whyknot.dev")
        self.error_patterns = CONFIG.get("video_error_patterns", [])
        self.last_attempted_url = None
        self.is_initial_scan = False

    def update_log_file(self, path):
        if path != self.current_log:
            self.current_log = path
            self.last_pos = 0
            self.is_initial_scan = True
            logger.info(f"Monitoring Log: {os.path.basename(path)} (Initial Catch-up)")
            try:
                file_size = os.path.getsize(path)
                self.last_pos = max(0, file_size - STARTUP_SCAN_DEPTH)
            except Exception: pass

    def tick(self):
        if not self.current_log or not os.path.exists(self.current_log): return
        
        try:
            current_size = os.path.getsize(self.current_log)
            if self.last_pos > current_size: self.last_pos = 0
            
            if current_size > self.last_pos:
                with open(self.current_log, 'r', encoding='utf-8', errors='replace') as f:
                    f.seek(self.last_pos)
                    new_lines = f.readlines()
                    if new_lines:
                        # If we just read a bunch of lines, and we haven't reached the current 
                        # 'end' that existed when tick started, we are still catching up.
                        # However, f.tell() will likely be close to current_size.
                        
                        self.last_pos = f.tell()
                        for line in new_lines:
                            # 1. Catch URL Loading attempts to track what might fail
                            if "[AVProVideo] Opening" in line:
                                url_match = re.search(r'Opening\s+(https?://[^\s\)]+)', line)
                                if url_match:
                                    self.last_attempted_url = url_match.group(1).strip()
                                    if not self.is_initial_scan:
                                        logger.debug(f"Detected video load attempt: {self.last_attempted_url}")

                            # 2. Catch Errors (ONLY if not in initial scan)
                            if any(x in line for x in self.error_patterns):
                                if not self.is_initial_scan:
                                    if self.last_attempted_url and self.proxy_domain in self.last_attempted_url:
                                        logger.warning(f"Detected Proxy Video Error: {line.strip()}")
                                        original_url = None
                                        if "url=" in self.last_attempted_url:
                                            try:
                                                import urllib.parse
                                                parsed = urllib.parse.urlparse(self.last_attempted_url)
                                                qs = urllib.parse.parse_qs(parsed.query)
                                                if 'url' in qs:
                                                    original_url = qs['url'][0]
                                            except Exception: pass
                                        
                                        target_to_block = original_url if original_url else self.last_attempted_url
                                        update_wrapper_state(is_broken=True, failed_url=target_to_block)
                                    else:
                                        logger.info(f"Detected Non-Proxy Video Error: {line.strip()}")
                                else:
                                    # Just log internally that we found an old error but are skipping it
                                    pass

                            it = parse_instance_type_from_line(line)
                            if it and it != self.last_instance_type:
                                logger.info(f"Instance changed: {self.last_instance_type} -> {it}")
                                self.last_instance_type = it

                        # After the first batch of lines is processed, we are no longer in initial scan
                        if self.is_initial_scan:
                            self.is_initial_scan = False
                            logger.info(f"Log catch-up complete. Live monitoring enabled.")

        except Exception: pass

def log_monitor_thread_func(monitor, stop_event):
    while not stop_event.is_set():
        monitor.tick()
        time.sleep(0.1) # 100ms response time

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
    
    # Force visibility for startup info
    print(f"{Colors.CYAN}{Colors.BOLD}VRCYTProxy Patcher {CURRENT_VERSION}{Colors.RESET} ({BUILD_TYPE}) starting up...")
    check_for_updates()
    
    try:
        with open(WRAPPER_FILE_LIST_PATH, 'r', encoding='utf-8-sig') as f:
            WRAPPER_FILE_LIST = json.load(f)
        logger.debug(f"Loaded {len(WRAPPER_FILE_LIST)} items from wrapper file list.")
    except Exception as e:
        logger.critical(f"Failed to read wrapper source files: {e}")
        input("Press Enter to exit..."); sys.exit(1)

    stop_event = threading.Event()
    
    # Start Redirector Log Thread
    log_tail_thread = threading.Thread(target=tail_log_file, args=(REDIRECTOR_LOG_PATH, stop_event), daemon=True)
    log_tail_thread.start()

    # Start VRChat Log Monitor Thread
    monitor = LogMonitor()
    vrc_monitor_thread = threading.Thread(target=log_monitor_thread_func, args=(monitor, stop_event), daemon=True)
    vrc_monitor_thread.start()

    last_health_check_time = 0
    last_file_watch_time = 0
    
    logger.info("Monitoring VRChat logs for activity...")
    
    try:
        while True:
            now = time.time()

            # 1. Log Discovery (Continuous check for the newest log)
            latest_log = find_latest_log_file()
            
            if latest_log:
                if latest_log != monitor.current_log:
                    # New log file detected
                    logger.info(f"New session detected via log: {os.path.basename(latest_log)}")
                    monitor.update_log_file(latest_log)
                
                # Check for log activity to see if the game is "active"
                try:
                    mtime = os.path.getmtime(latest_log)
                    if now - mtime > 60: # No writes for 60 seconds
                        if monitor.last_instance_type != "idle":
                            logger.info("Log activity ceased. Entering idle state.")
                            monitor.last_instance_type = "idle"
                except Exception: pass
            else:
                if monitor.last_instance_type != "no_logs":
                    logger.warning("No VRChat logs found.")
                    monitor.last_instance_type = "no_logs"

            # 2. Proactive File Watch
            if now - last_file_watch_time > 1.0:
                force_public = CONFIG.get("force_patch_in_public", False)
                
                if force_public:
                    desired_state = PatchState.ENABLED
                else:
                    # If we aren't in a public/group world, we enable the patch.
                    # idle/no_logs states also keep it enabled to be ready for next launch.
                    should_disable = monitor.last_instance_type in ['public', 'group_public']
                    desired_state = PatchState.DISABLED if should_disable else PatchState.ENABLED
                
                current_state = get_patch_state()
                
                if desired_state == PatchState.ENABLED and current_state == PatchState.DISABLED:
                    logger.info("Applying wrapper based on log/config state...")
                    enable_patch(WRAPPER_FILE_LIST, False)
                elif desired_state == PatchState.DISABLED and current_state == PatchState.ENABLED:
                    logger.info("World changed to PUBLIC (via log). Restoring original yt-dlp.exe...")
                    disable_patch(WRAPPER_FILE_LIST)
                
                last_file_watch_time = now

            # 3. Health Check
            if now - last_health_check_time > 10.0:
                if get_patch_state() == PatchState.ENABLED:
                    check_wrapper_health(WRAPPER_FILE_LIST)
                last_health_check_time = now
            
            time.sleep(POLL_INTERVAL)
    
    except KeyboardInterrupt:
        logger.info("Shutdown signal received...")
    finally:
        stop_event.set()
        disable_patch(WRAPPER_FILE_LIST)
        log_tail_thread.join(timeout=2)

if __name__ == '__main__':
    main()