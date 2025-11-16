import os
import sys
import shutil
import time
import logging
import glob
import re
import threading
from logging.handlers import RotatingFileHandler

# --- Constants ---
VRCHAT_LOG_DIR = os.path.join(os.path.expanduser('~'), 'AppData', 'LocalLow', 'VRChat', 'VRChat')
VRCHAT_TOOLS_DIR = os.path.join(VRCHAT_LOG_DIR, 'Tools')
WRAPPER_SOURCE_DIR_NAME = 'wrapper_files'
WRAPPER_EXE_NAME = 'main.exe'
ORIGINAL_EXE_NAME = 'yt-dlp-og.exe'
TARGET_EXE_NAME = 'yt-dlp.exe'
POLL_INTERVAL = 2
LOG_FILE_NAME = 'patcher.log'
REDIRECTOR_LOG_NAME = 'wrapper_debug.log'

# --- Path Setup ---
def get_application_path():
    """Gets the base path for the application, accommodating both frozen and source-code execution."""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

APP_BASE_PATH = get_application_path()
LOG_FILE_PATH = os.path.join(APP_BASE_PATH, LOG_FILE_NAME)
SOURCE_DIR_PATH = os.path.join(APP_BASE_PATH, 'resources', WRAPPER_SOURCE_DIR_NAME)
WRAPPER_SOURCE_EXE = os.path.join(SOURCE_DIR_PATH, WRAPPER_EXE_NAME)

TARGET_YTDLP_PATH = os.path.join(VRCHAT_TOOLS_DIR, TARGET_EXE_NAME)
ORIGINAL_YTDLP_PATH = os.path.join(VRCHAT_TOOLS_DIR, ORIGINAL_EXE_NAME)
REDIRECTOR_LOG_PATH = os.path.join(VRCHAT_TOOLS_DIR, REDIRECTOR_LOG_NAME)

# --- Logger Setup ---
def setup_logging():
    """Configures a rotating file logger and a console logger."""
    logger = logging.getLogger('Patcher')
    logger.setLevel(logging.INFO)
    
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    
    # Console Handler
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(formatter)
    logger.addHandler(ch)
    
    # File Handler
    try:
        fh = RotatingFileHandler(LOG_FILE_PATH, maxBytes=2*1024*1024, backupCount=3, mode='w')
        fh.setFormatter(formatter)
        logger.addHandler(fh)
    except PermissionError:
        logger.warning(f"Permission denied for log file: {LOG_FILE_PATH}. Logging to console only.")
        
    return logger

logger = setup_logging()

def tail_log_file(log_path, stop_event):
    """
    Monitors a log file and prints new lines to the console.
    Runs in a separate thread.
    """
    logger.info(f"Starting to monitor redirector log: {log_path}")
    last_pos = 0
    
    # On first run, fast-forward to the end of the file
    try:
        if os.path.exists(log_path):
            last_pos = os.path.getsize(log_path)
    except OSError:
        pass # File might not exist yet, which is fine

    while not stop_event.is_set():
        try:
            if os.path.exists(log_path):
                # Reset position if file has shrunk (e.g., been overwritten)
                current_size = os.path.getsize(log_path)
                if last_pos > current_size:
                    last_pos = 0
                
                with open(log_path, 'r', encoding='utf-8', errors='replace') as f:
                    f.seek(last_pos)
                    new_lines = f.readlines()
                    if new_lines:
                        for line in new_lines:
                            # Print a formatted version to the main console
                            print(f"[Redirector] {line.strip()}")
                        last_pos = f.tell()
        except Exception:
            # Don't spam logs if there's a read error, just try again
            pass
        time.sleep(0.5) # Check every 500ms
    logger.info("Stopping redirector log monitor.")


def enable_patch():
    """
    Enables the redirector patch.
    This involves backing up the original yt-dlp.exe if it's not already our patch,
    then copying the wrapper files and renaming the wrapper executable to yt-dlp.exe.
    """
    logger.info("Attempting to enable patch...")
    try:
        # First, determine if a backup of VRChat's original file is needed.
        is_vrchat_original = False
        if os.path.exists(TARGET_YTDLP_PATH):
            try:
                # If file sizes are different, we assume it's the VRChat original.
                if os.path.getsize(TARGET_YTDLP_PATH) != os.path.getsize(WRAPPER_SOURCE_EXE):
                    is_vrchat_original = True
            except OSError:
                # File might have disappeared, assume it's not the original.
                is_vrchat_original = False
        
        # If the target file exists and we've determined it's the VRChat one, back it up.
        # This overwrites the old backup, ensuring we always have the latest version.
        if is_vrchat_original:
            logger.info(f"VRChat's '{TARGET_EXE_NAME}' detected. Backing up to '{ORIGINAL_EXE_NAME}'...")
            os.replace(TARGET_YTDLP_PATH, ORIGINAL_YTDLP_PATH)

        # Now, copy the wrapper files and apply the patch.
        logger.info(f"Copying wrapper files from '{SOURCE_DIR_PATH}' to '{VRCHAT_TOOLS_DIR}'")
        shutil.copytree(SOURCE_DIR_PATH, VRCHAT_TOOLS_DIR, dirs_exist_ok=True)
        
        copied_wrapper_path = os.path.join(VRCHAT_TOOLS_DIR, WRAPPER_EXE_NAME)
        if os.path.exists(copied_wrapper_path):
            logger.info(f"Renaming '{WRAPPER_EXE_NAME}' to '{TARGET_EXE_NAME}'")
            os.replace(copied_wrapper_path, TARGET_YTDLP_PATH)
            logger.info("Patch enabled successfully.")
            return True
        else:
            logger.error(f"Wrapper executable not found at '{copied_wrapper_path}' after copy. Swap failed.")
            return False
    
    except PermissionError:
        logger.error("Permission denied during patch enable. Ensure the script has rights to modify VRChat files.")
        return False
    except Exception:
        logger.exception("An unexpected error occurred while enabling the patch.")
        return False

def disable_patch():
    """
    Disables the redirector patch.
    This involves removing the wrapper and restoring the original yt-dlp.exe from backup.
    """
    logger.info("Attempting to disable patch...")
    try:
        if os.path.exists(ORIGINAL_YTDLP_PATH):
            if os.path.exists(TARGET_YTDLP_PATH):
                logger.info(f"Removing current wrapper file '{TARGET_YTDLP_PATH}'")
                os.remove(TARGET_YTDLP_PATH)
            
            logger.info(f"Restoring original '{ORIGINAL_EXE_NAME}' to '{TARGET_EXE_NAME}'")
            os.replace(ORIGINAL_YTDLP_PATH, TARGET_YTDLP_PATH)
            logger.info("Patch disabled successfully.")
            return True
        else:
            logger.warning(f"No backup ('{ORIGINAL_EXE_NAME}') found. Cannot restore.")
            # Clean up orphan wrapper if it exists
            if os.path.exists(TARGET_YTDLP_PATH):
                try:
                    wrapper_size = os.path.getsize(WRAPPER_SOURCE_EXE)
                    target_size = os.path.getsize(TARGET_YTDLP_PATH)
                    if target_size == wrapper_size:
                        logger.info(f"Removing orphan wrapper file '{TARGET_YTDLP_PATH}'")
                        os.remove(TARGET_YTDLP_PATH)
                except Exception:
                    pass
            return True

    except PermissionError:
        logger.error("Permission denied during patch disable. Ensure the script has rights to modify VRChat files.")
        return False
    except Exception:
        logger.exception("An unexpected error occurred while disabling the patch.")
        return False

def find_latest_log_file():
    """Finds the most recently modified VRChat log file."""
    try:
        list_of_files = glob.glob(os.path.join(VRCHAT_LOG_DIR, 'output_log_*.txt'))
        if not list_of_files:
            return None
        return max(list_of_files, key=os.path.getmtime)
    except Exception:
        logger.exception("Error finding latest VRChat log file.")
        return None

def parse_instance_type_from_line(line):
    """Parses a log line to find the VRChat instance type (e.g., public, private)."""
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

def get_last_instance_type(log_file):
    """
    Reads the end of a log file to find the last recorded instance join type.
    Returns only the instance type string, or None if not found.
    """
    try:
        with open(log_file, 'r', encoding='utf-8', errors='replace') as f:
            # Seek to a reasonable buffer size from the end of the file (~16KB)
            # This is a balance between performance and finding a recent log entry.
            file_size = os.path.getsize(log_file)
            f.seek(max(0, file_size - 16384))
            lines = f.readlines()
            # Iterate backwards through the lines to find the most recent match first
            for line in reversed(lines):
                instance_type = parse_instance_type_from_line(line)
                if instance_type:
                    logger.info(f"Found last instance type '{instance_type}' in log startup scan.")
                    return instance_type
    except Exception:
        logger.exception(f"Could not read or parse last instance type from '{log_file}'.")
    
    logger.info("No previous instance join found in log startup scan.")
    return None

def main():
    """Main loop to monitor VRChat logs and dynamically apply/remove the patch."""
    logger.info("Patcher starting up...")
    logger.info(f"VRChat Tools Directory: {VRCHAT_TOOLS_DIR}")
    logger.info(f"Wrapper Source Directory: {SOURCE_DIR_PATH}")
    logger.info(f"Patcher Log File: {LOG_FILE_PATH}")
    
    if not os.path.exists(WRAPPER_SOURCE_EXE):
        logger.critical(f"Wrapper source file not found: '{WRAPPER_SOURCE_EXE}'!")
        input("Press Enter to exit...")
        sys.exit(1)

    # --- Start Redirector Log Tailing Thread ---
    stop_event = threading.Event()
    log_tail_thread = threading.Thread(
        target=tail_log_file,
        args=(REDIRECTOR_LOG_PATH, stop_event),
        daemon=True
    )
    log_tail_thread.start()

    current_log_file = None
    last_pos = 0
    
    # --- Initial State Setup ---
    logger.info("Performing startup scan to set initial patch state...")
    latest_log_file = find_latest_log_file()
    if latest_log_file:
        current_log_file = latest_log_file
        last_instance_type = get_last_instance_type(latest_log_file)
        
        if last_instance_type:
            if last_instance_type in ['public', 'group']:
                logger.info("Startup: Last detected instance was public/group. Ensuring patch is disabled.")
                disable_patch()
            else:
                logger.info("Startup: Last detected instance was private. Applying patch.")
                enable_patch()
        else:
            logger.info("Startup: No recent instance join found. Enabling patch by default.")
            enable_patch()
        
        try:
            last_pos = os.path.getsize(latest_log_file)
        except OSError:
            last_pos = 0
    else:
        logger.info("Startup: No VRChat logs found. Enabling patch by default.")
        enable_patch()

    # --- Monitoring Loop ---
    logger.info("Startup complete. Now monitoring for new instance joins...")
    try:
        while True:
            latest_log_file = find_latest_log_file()
            
            if not latest_log_file:
                time.sleep(POLL_INTERVAL * 2)
                continue

            if latest_log_file != current_log_file:
                logger.info(f"New log file detected: {os.path.basename(latest_log_file)}")
                current_log_file = latest_log_file
                last_pos = 0
            
            try:
                with open(current_log_file, 'r', encoding='utf-8', errors='replace') as f:
                    f.seek(last_pos)
                    new_lines = f.readlines()
                    if new_lines:
                        last_pos = f.tell()
                    
                for line in new_lines:
                    instance_type = parse_instance_type_from_line(line)
                    if instance_type:
                        logger.info(f"Detected new instance join: type '{instance_type}'")
                        
                        if instance_type in ['public', 'group']:
                            logger.warning("Public/Group instance -> Ensuring patch is disabled for safety.")
                            disable_patch()
                        else: # private, friends, etc.
                            logger.info("Private/Friends instance -> Applying patch.")
                            enable_patch()
            
            except FileNotFoundError:
                logger.warning(f"Log file '{current_log_file}' disappeared. Searching for new one.")
                current_log_file = None
                last_pos = 0
            except Exception:
                logger.exception("Error reading VRChat log file.")
                last_pos = 0
            
            time.sleep(POLL_INTERVAL)
    
    except KeyboardInterrupt:
        logger.info("Shutdown signal received. Stopping threads and restoring files...")
    
    finally:
        stop_event.set()
        disable_patch()
        log_tail_thread.join(timeout=2)
        logger.info("Patcher shut down.")


if __name__ == '__main__':
    main()