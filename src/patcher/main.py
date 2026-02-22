import sys
import os

def get_application_path():
    if getattr(sys, 'frozen', False): return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

APP_BASE_PATH = get_application_path()
if APP_BASE_PATH not in sys.path:
    sys.path.insert(0, APP_BASE_PATH)

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
import msvcrt
import subprocess

try:
    from jobs import job_manager
    from state import update_wrapper_state
    from health import check_wrapper_health
except ImportError:
    from .jobs import job_manager
    from .state import update_wrapper_state
    from .health import check_wrapper_health


if platform.system() == 'Windows':
    os.system('color')

try:
    from _version import __version__ as CURRENT_VERSION
    from _version import __build_type__ as BUILD_TYPE
except ImportError:
    CURRENT_VERSION = "vDEV"
    BUILD_TYPE = "DEV"

GITHUB_REPO_OWNER = "RealWhyKnot"
GITHUB_REPO_NAME = "VRCYTProxy"

class PatchState(Enum):
    UNKNOWN = auto()
    ENABLED = auto()
    DISABLED = auto()
    BROKEN = auto()

POLL_INTERVAL = 1.0 
LOG_FILE_NAME = 'patcher.log'
REDIRECTOR_LOG_NAME = 'wrapper.log'
CONFIG_FILE_NAME = 'patcher_config.json'
WRAPPER_FILE_LIST_NAME = 'wrapper_filelist.json'
STARTUP_SCAN_DEPTH = 300 * 1024 * 1024
WRAPPER_EXE_NAME = 'yt-dlp-wrapper.exe'
ORIGINAL_EXE_NAME = 'yt-dlp-og.exe'
SECURE_BACKUP_NAME = 'yt-dlp-og-secure.exe'
TARGET_EXE_NAME = 'yt-dlp.exe'
WRAPPER_SOURCE_DIR_NAME = 'wrapper_files'

def calculate_sha256(filepath):
    if not os.path.exists(filepath): return None
    sha256_hash = hashlib.sha256()
    try:
        with open(filepath, "rb") as f:
            for byte_block in iter(lambda: f.read(4096), b""):
                sha256_hash.update(byte_block)
        return sha256_hash.hexdigest()
    except Exception: return None

class Colors:
    RESET = "\033[0m"; RED = "\033[91m"; GREEN = "\033[92m"; YELLOW = "\033[93m"
    BLUE = "\033[94m"; MAGENTA = "\033[95m"; CYAN = "\033[96m"; GREY = "\033[90m"
    BOLD = "\033[1m"; BG_RED = "\033[41m"

class ColoredFormatter(logging.Formatter):
    def format(self, record):
        color = Colors.RESET
        # Priority 1: Levels (Errors/Warnings)
        if record.levelno >= logging.ERROR: color = Colors.RED
        elif record.levelno >= logging.WARNING: color = Colors.YELLOW
        else:
            # Priority 2: Semantic highlights for INFO/DEBUG
            msg = str(record.msg)
            if "ENABLED" in msg or "enabled" in msg: color = Colors.GREEN
            elif "DISABLED" in msg or "disabled" in msg: color = Colors.GREY
            elif "[Redirector]" in msg: color = Colors.CYAN
            
        ts = self.formatTime(record, self.datefmt)
        return f"{Colors.GREY}{ts}{Colors.RESET} - {color}{record.getMessage()}{Colors.RESET}"

def get_application_path():
    if getattr(sys, 'frozen', False): return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

APP_BASE_PATH = get_application_path()
LOG_FILE_PATH = os.path.join(APP_BASE_PATH, LOG_FILE_NAME)
WRAPPER_FILE_LIST_PATH = os.path.join(APP_BASE_PATH, WRAPPER_FILE_LIST_NAME)
SOURCE_WRAPPER_DIR = os.path.join(APP_BASE_PATH, 'resources', WRAPPER_SOURCE_DIR_NAME)

def load_config(config_path):
    defaults = {"video_error_patterns": ["[Video Player] Failed to load", "VideoError", "[AVProVideo] Error", "PlayerError"], "instance_patterns": {"invite": "~private", "friends+": "~hidden", "friends": "~friends", "group": "~group"}, "debug_mode": BUILD_TYPE == "DEV", "force_patch_in_public": False, "auto_update_check": True, "first_run": True, "enable_tier1_modern": True, "enable_tier2_proxy": True, "enable_tier3_native": True}
    if os.path.exists(config_path):
        try:
            with open(config_path, 'r', encoding='utf-8-sig') as f:
                user_config = json.load(f)
                for k, v in defaults.items():
                    if k not in user_config: user_config[k] = v
                return user_config
        except: pass
    return defaults

def setup_logging():
    logger = logging.getLogger('Patcher')
    config_path = os.path.join(APP_BASE_PATH, CONFIG_FILE_NAME)
    cfg = load_config(config_path)
    level = logging.DEBUG if cfg.get("debug_mode") else logging.INFO
    logger.setLevel(level)
    ch = logging.StreamHandler(sys.stdout); ch.setFormatter(ColoredFormatter('%(asctime)s - %(message)s'))
    logger.addHandler(ch)
    try:
        fh = RotatingFileHandler(LOG_FILE_PATH, mode='w', maxBytes=10*1024*1024, backupCount=3, encoding='utf-8')
        fh.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
        logger.addHandler(fh)
    except: pass
    return logger

logger = setup_logging()

def get_vrchat_log_dir():
    config_path = os.path.join(APP_BASE_PATH, CONFIG_FILE_NAME)
    cfg = load_config(config_path)
    log_dir = cfg.get('vrchat_log_dir')
    if log_dir and os.path.exists(log_dir): return log_dir
    default = os.path.join(os.path.expanduser('~'), 'AppData', 'LocalLow', 'VRChat', 'VRChat')
    if os.path.exists(default): return default
    return input("Enter VRChat log path: ").strip()

# --- Global Logic Variables ---
CONFIG = {}
VRCHAT_LOG_DIR = None; VRCHAT_TOOLS_DIR = None; TARGET_YTDLP_PATH = None
ORIGINAL_YTDLP_BACKUP_PATH = None; SECURE_BACKUP_PATH = None
REDIRECTOR_LOG_PATH = None; WRAPPER_STATE_PATH = None

class LogMonitor:
    def __init__(self):
        self.current_log = None; self.last_pos = 0; self.last_instance_type = "private"
        self.proxy_domain = "whyknot.dev"; self.error_patterns = CONFIG.get("video_error_patterns", [])
        self.last_attempted_url = None; self.last_winner_tier = None; self.is_initial_scan = False
        self.resolved_to_source = {}; self.last_error_time = 0

    def update_log_file(self, path):
        if path != self.current_log:
            self.current_log = path; self.last_pos = 0; self.is_initial_scan = True
            self.last_error_time = 0 # Reset on log switch
            logger.info(f"Monitoring Log: {os.path.basename(path)}")
            try: self.last_pos = max(0, os.path.getsize(path) - STARTUP_SCAN_DEPTH)
            except: pass

    def tick(self):
        if not self.current_log or not os.path.exists(self.current_log): return
        try:
            curr_size = os.path.getsize(self.current_log)
            if self.last_pos > curr_size: self.last_pos = 0
            if curr_size > self.last_pos:
                with open(self.current_log, 'r', encoding='utf-8', errors='replace') as f:
                    f.seek(self.last_pos)
                    lines = f.readlines()
                    if lines:
                        self.last_pos = f.tell()
                        now = time.time()
                        for line in lines:
                            if "[Video Playback] URL '" in line:
                                parts = line.split("'")
                                if len(parts) >= 4: self.resolved_to_source[parts[3]] = parts[1]
                            if "[AVProVideo] Opening" in line:
                                m = re.search(r'Opening\s+(https?://[^\s\)]+)', line)
                                self.last_attempted_url = m.group(1) if m else None
                                if not self.is_initial_scan: update_wrapper_state(WRAPPER_STATE_PATH, active_player='avpro')
                            if "[VideoPlayer] Loading" in line or "[VideoPlayer] Opening" in line:
                                m = re.search(r'(?:Loading|Opening)\s+(https?://[^\s\)]+)', line)
                                self.last_attempted_url = m.group(1) if m else None
                                if not self.is_initial_scan: update_wrapper_state(WRAPPER_STATE_PATH, active_player='unity')
                            
                            # Error Detection with Debounce (3s)
                            if any(x in line for x in self.error_patterns) and not self.is_initial_scan:
                                if self.last_attempted_url and (now - self.last_error_time > 3.0):
                                    self.last_error_time = now
                                    orig = self.resolved_to_source.get(self.last_attempted_url, self.last_attempted_url)
                                    update_wrapper_state(WRAPPER_STATE_PATH, is_broken=True, failed_url=orig, failed_tier=self.last_winner_tier)
                            
                            # Instance Detection
                            if '[Behaviour] Destination set:' in line or '[Behaviour] Joining wrld_' in line:
                                it = 'public'
                                if '~private' in line: it = 'invite'
                                elif '~hidden' in line: it = 'friends+'
                                elif '~friends' in line: it = 'friends'
                                elif '~group' in line: it = 'group'
                                
                                if it != self.last_instance_type:
                                    if not self.is_initial_scan:
                                        logger.info(f"Instance changed: {self.last_instance_type} -> {it}")
                                    self.last_instance_type = it
                                    update_wrapper_state(WRAPPER_STATE_PATH, active_player='unknown')
                        
                        if self.is_initial_scan:
                            self.is_initial_scan = False
                            logger.info("Log catch-up complete. Live monitoring active.")
        except: pass

def tail_log_file(log_path, stop_event, monitor):
    last_pos = 0
    # Wait for file to exist
    while not os.path.exists(log_path) and not stop_event.is_set():
        time.sleep(1.0)
    
    if os.path.exists(log_path): last_pos = os.path.getsize(log_path)
    
    while not stop_event.is_set():
        try:
            if os.path.exists(log_path):
                curr_size = os.path.getsize(log_path)
                if last_pos > curr_size: last_pos = 0
                if curr_size > last_pos:
                    with open(log_path, 'r', encoding='utf-8', errors='replace') as f:
                        f.seek(last_pos)
                        lines = f.readlines()
                        for line in lines:
                            line = line.strip()
                            msg = f"[Redirector] {line}"
                            # Parse level from [LEVEL] in line
                            if "[ERROR]" in line: logger.error(msg)
                            elif "[WARNING]" in line: logger.warning(msg)
                            elif "[DEBUG]" in line: logger.debug(msg)
                            else: logger.info(msg)

                            if "WINNER: Tier" in line:
                                m = re.search(r'Tier (\d+)', line)
                                if m: monitor.last_winner_tier = int(m.group(1))
                        last_pos = f.tell()
        except: pass
        time.sleep(1.0)

def get_patch_state():
    target_hash = calculate_sha256(TARGET_YTDLP_PATH)
    source_wrapper_path = os.path.join(SOURCE_WRAPPER_DIR, WRAPPER_EXE_NAME)
    wrapper_hash = calculate_sha256(source_wrapper_path)
    
    if not wrapper_hash:
        return PatchState.BROKEN

    if target_hash and target_hash == wrapper_hash:
        return PatchState.ENABLED
    
    return PatchState.DISABLED

def get_process_using_file(filepath):
    """Attempts to find which process is using a file. Primarily checks for VRChat or yt-dlp."""
    try:
        filename = os.path.basename(filepath)
        # Check for processes with the same name first
        proc = subprocess.Popen(['tasklist', '/FI', f"IMAGENAME eq {filename}", '/FO', 'CSV', '/NH'], 
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, creationflags=subprocess.CREATE_NO_WINDOW)
        out, _ = proc.communicate()
        if filename.lower() in out.lower():
            return f"Found active process: {filename}"
        
        # Check for VRChat
        proc = subprocess.Popen(['tasklist', '/FI', "IMAGENAME eq VRChat.exe", '/FO', 'CSV', '/NH'], 
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, creationflags=subprocess.CREATE_NO_WINDOW)
        out, _ = proc.communicate()
        if "VRChat.exe" in out:
            return "VRChat.exe is running (likely holding a handle)"
            
    except Exception: pass
    return "Unknown process (check Task Manager)"

def enable_patch(file_list):
    for attempt in range(3):
        try:
            if not os.path.exists(SOURCE_WRAPPER_DIR):
                logger.error(f"Source folder missing: {SOURCE_WRAPPER_DIR}")
                return False

            if not os.path.exists(VRCHAT_TOOLS_DIR): os.makedirs(VRCHAT_TOOLS_DIR)
            
            # 1. Sync wrapper files
            shutil.copytree(SOURCE_WRAPPER_DIR, VRCHAT_TOOLS_DIR, dirs_exist_ok=True)
            
            # 2. Backup if target is original
            source_wrapper_path = os.path.join(SOURCE_WRAPPER_DIR, WRAPPER_EXE_NAME)
            wh = calculate_sha256(source_wrapper_path)
            
            if os.path.exists(TARGET_YTDLP_PATH):
                th = calculate_sha256(TARGET_YTDLP_PATH)
                if th and wh and th != wh:
                    logger.info(f"Backing up original yt-dlp.exe (Hash: {th[:8]}...)")
                    shutil.copy2(TARGET_YTDLP_PATH, ORIGINAL_YTDLP_BACKUP_PATH)
            
            # 3. Swap in wrapper
            shutil.copy2(os.path.join(VRCHAT_TOOLS_DIR, WRAPPER_EXE_NAME), TARGET_YTDLP_PATH)
            
            # Final Verify
            final_hash = calculate_sha256(TARGET_YTDLP_PATH)
            if final_hash == wh:
                logger.info("Patch ENABLED and verified.")
                return True
            else:
                logger.error("Patch verification failed after copy!")
                return False
        except PermissionError as e:
            if e.winerror == 32:
                culprit = get_process_using_file(TARGET_YTDLP_PATH)
                logger.warning(f"File locked (Attempt {attempt+1}/3): {culprit}")
                if attempt < 2: 
                    time.sleep(1.0)
                    continue
            logger.error(f"Enable failed: {e}")
        except Exception as e: 
            logger.error(f"Enable failed: {e}")
            break
    return False

def disable_patch(file_list):
    for attempt in range(3):
        try:
            # 1. Restore original yt-dlp.exe
            if os.path.exists(ORIGINAL_YTDLP_BACKUP_PATH):
                logger.info("Restoring original yt-dlp.exe...")
                shutil.copy2(ORIGINAL_YTDLP_BACKUP_PATH, TARGET_YTDLP_PATH)
            
            # 2. Remove wrapper-specific files
            logger.info("Cleaning up proxy files from Tools folder...")
            for filename in file_list:
                # Never delete the target yt-dlp.exe (we just restored it) 
                # or the original backup (we might delete it later)
                if filename.lower() in [TARGET_EXE_NAME.lower(), ORIGINAL_EXE_NAME.lower()]:
                    continue
                    
                path = os.path.join(VRCHAT_TOOLS_DIR, filename)
                if os.path.exists(path):
                    try:
                        if os.path.isdir(path):
                            shutil.rmtree(path, ignore_errors=True)
                        else:
                            os.remove(path)
                    except Exception as e:
                        logger.debug(f"Failed to remove {filename}: {e}")

            # 3. Clean up the backup file if we restored successfully
            if os.path.exists(ORIGINAL_YTDLP_BACKUP_PATH):
                try: os.remove(ORIGINAL_YTDLP_BACKUP_PATH)
                except: pass
            
            # 4. Clean up transient state
            if os.path.exists(WRAPPER_STATE_PATH):
                try: os.remove(WRAPPER_STATE_PATH)
                except: pass

            logger.info("Patch DISABLED (Original state restored).")
            return True
        except PermissionError as e:
            if e.winerror == 32:
                culprit = get_process_using_file(TARGET_YTDLP_PATH)
                logger.warning(f"File locked during cleanup (Attempt {attempt+1}/3): {culprit}")
                if attempt < 2:
                    time.sleep(0.7)
                    continue
            logger.error(f"Disable failed: {e}")
        except Exception as e: 
            logger.error(f"Disable failed: {e}")
            break
    return False

def main():
    global CONFIG, VRCHAT_LOG_DIR, VRCHAT_TOOLS_DIR, TARGET_YTDLP_PATH, ORIGINAL_YTDLP_BACKUP_PATH, REDIRECTOR_LOG_PATH, WRAPPER_STATE_PATH
    CONFIG = load_config(os.path.join(APP_BASE_PATH, CONFIG_FILE_NAME))
    VRCHAT_LOG_DIR = get_vrchat_log_dir()
    VRCHAT_TOOLS_DIR = os.path.join(VRCHAT_LOG_DIR, 'Tools')
    TARGET_YTDLP_PATH = os.path.join(VRCHAT_TOOLS_DIR, TARGET_EXE_NAME)
    ORIGINAL_YTDLP_BACKUP_PATH = os.path.join(VRCHAT_TOOLS_DIR, ORIGINAL_EXE_NAME)
    REDIRECTOR_LOG_PATH = os.path.join(VRCHAT_TOOLS_DIR, REDIRECTOR_LOG_NAME)
    WRAPPER_STATE_PATH = os.path.join(VRCHAT_TOOLS_DIR, 'wrapper_state.json')

    # --- SESSION START: Clear transient state ---
    logger.info("Initializing fresh session state...")
    update_wrapper_state(WRAPPER_STATE_PATH, active_player='unknown')
    try:
        if os.path.exists(WRAPPER_STATE_PATH):
            with open(WRAPPER_STATE_PATH, 'r') as f: state = json.load(f)
            state['domain_blacklist'] = {}
            state['failed_urls'] = {}
            state['cache'] = {}
            state['consecutive_errors'] = 0
            state['force_fallback'] = False
            with open(WRAPPER_STATE_PATH, 'w') as f: json.dump(state, f)
    except Exception: pass

    with open(WRAPPER_FILE_LIST_PATH, 'r') as f: file_list = json.load(f)
    stop_event = threading.Event(); monitor = LogMonitor()
    
    # Start thread to monitor wrapper log
    threading.Thread(target=tail_log_file, args=(REDIRECTOR_LOG_PATH, stop_event, monitor), daemon=True).start()
    
    # Start thread to monitor VRChat log
    def vrc_monitor_loop():
        while not stop_event.is_set():
            logs = glob.glob(os.path.join(VRCHAT_LOG_DIR, 'output_log_*.txt'))
            if logs: monitor.update_log_file(max(logs, key=os.path.getmtime))
            monitor.tick()
            time.sleep(0.5)
    
    threading.Thread(target=vrc_monitor_loop, daemon=True).start()

    atexit.register(lambda: [job_manager.close(), disable_patch(file_list)])
    
    # Robust signal handling
    def signal_handler(sig, frame):
        logger.info(f"Received signal {sig}. Exiting cleanly...")
        disable_patch(file_list)
        job_manager.close()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    if platform.system() == 'Windows':
        # Use ctypes to avoid pywin32 dependency while catching console close
        PHANDLER_ROUTINE = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_uint)
        
        def console_ctrl_handler(ctrl_type):
            # 2 = CTRL_CLOSE_EVENT, 5 = CTRL_LOGOFF_EVENT, 6 = CTRL_SHUTDOWN_EVENT
            if ctrl_type in [2, 5, 6]:
                # We must be very fast here, Windows only gives a few seconds
                disable_patch(file_list)
                job_manager.close()
                return True
            return False

        # Keep a reference to the handler to prevent garbage collection
        _handler = PHANDLER_ROUTINE(console_ctrl_handler)
        if not ctypes.windll.kernel32.SetConsoleCtrlHandler(_handler, True):
            logger.debug("Failed to set Console Control Handler.")

    logger.info("Monitoring VRChat for world changes...")
    
    while True:
        # Decision Logic
        force_patch = CONFIG.get("force_patch_in_public", False)
        should_be_enabled = force_patch or (monitor.last_instance_type not in ['public', 'group_public'])
        
        current_state = get_patch_state()
        
        if should_be_enabled and current_state != PatchState.ENABLED:
            enable_patch(file_list)
        elif not should_be_enabled and current_state == PatchState.ENABLED:
            disable_patch(file_list)
            
        # Periodic Health Check
        if current_state == PatchState.ENABLED:
            check_wrapper_health(file_list, VRCHAT_TOOLS_DIR, SOURCE_WRAPPER_DIR, WRAPPER_EXE_NAME)
            
        time.sleep(POLL_INTERVAL)

if __name__ == '__main__':
    main()
