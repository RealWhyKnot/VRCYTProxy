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
import atexit
import signal
import ctypes
import hashlib
import subprocess
import msvcrt
import art
import rich._unicode_data
from rich.align import Align
from rich.columns import Columns
from rich.console import Console, Group
from rich.live import Live
from rich.table import Table
from rich.panel import Panel
from rich.layout import Layout
from rich.logging import RichHandler
from rich.text import Text
from rich.spinner import Spinner

try:
    from jobs import job_manager
    from state import update_wrapper_state
    from health import check_wrapper_health
except ImportError:
    from .jobs import job_manager
    from .state import update_wrapper_state
    from .health import check_wrapper_health

# --- Constants ---
POLL_INTERVAL = 1.0 
LOG_FILE_NAME = 'patcher.log'
REDIRECTOR_LOG_NAME = 'wrapper.log'
CONFIG_FILE_NAME = 'patcher_config.json'
WRAPPER_FILE_LIST_NAME = 'wrapper_filelist.json' # Moving to resources
STARTUP_SCAN_DEPTH = 300 * 1024 * 1024
WRAPPER_EXE_NAME = 'yt-dlp-wrapper.exe'
ORIGINAL_EXE_NAME = 'yt-dlp-og.exe'
TARGET_EXE_NAME = 'yt-dlp.exe'
WRAPPER_SOURCE_DIR_NAME = 'wrapper_files'

# --- Derived Paths ---
LOG_DIR = os.path.join(APP_BASE_PATH, 'logs')
if not os.path.exists(LOG_DIR):
    try: os.makedirs(LOG_DIR, exist_ok=True)
    except: LOG_DIR = APP_BASE_PATH # Fallback to root if folder creation fails

LOG_FILE_PATH = os.path.join(LOG_DIR, LOG_FILE_NAME)
SOURCE_WRAPPER_DIR = os.path.join(APP_BASE_PATH, 'resources', WRAPPER_SOURCE_DIR_NAME)
# Internal manifest now lives in resources
WRAPPER_FILE_LIST_PATH = os.path.join(APP_BASE_PATH, 'resources', WRAPPER_FILE_LIST_NAME)

if platform.system() == 'Windows':
    os.system('color')

try:
    from _version import __version__ as CURRENT_VERSION
    from _version import __build_type__ as BUILD_TYPE
except ImportError:
    CURRENT_VERSION = "vDEV"
    BUILD_TYPE = "DEV"

class PatchState(Enum):
    UNKNOWN = auto()
    ENABLED = auto()
    DISABLED = auto()
    BROKEN = auto()

def calculate_sha256(filepath):
    if not os.path.exists(filepath): return None
    sha256_hash = hashlib.sha256()
    try:
        with open(filepath, "rb") as f:
            for byte_block in iter(lambda: f.read(4096), b""):
                sha256_hash.update(byte_block)
        return sha256_hash.hexdigest()
    except Exception: return None

class UIState:
    def __init__(self):
        self.status = "Initializing..."
        self.world = "Unknown"
        self.engine = "Idle"
        self.recent_activities = []
        self.scroll_offset = 0
        self.start_time = time.time()
        self.last_uptime = 0
        self.stats = {"total": 0, "t1": 0, "t2": 0, "t3": 0, "t4": 0, "fail": 0}
        self.lock = threading.Lock()
        self._dirty = True

    def mark_dirty(self):
        self._dirty = True

    def add_activity(self, msg, level="info"):
        with self.lock:
            self.recent_activities.insert(0, (msg, level, time.time()))
            if len(self.recent_activities) > 200:
                self.recent_activities.pop()
            if self.scroll_offset > 0: self.scroll_offset += 1
            self._dirty = True

    def update_stats(self, tier=None, failed=False):
        with self.lock:
            self.stats["total"] += 1
            if failed: self.stats["fail"] += 1
            elif tier: self.stats[f"t{tier}"] += 1
            self._dirty = True

    def scroll(self, delta):
        with self.lock:
            try:
                term_height = shutil.get_terminal_size().lines
                visible_count = max(5, term_height - 15)
            except: visible_count = 12
            max_scroll = max(0, len(self.recent_activities) - visible_count)
            old_offset = self.scroll_offset
            self.scroll_offset = max(0, min(max_scroll, self.scroll_offset + delta))
            if old_offset != self.scroll_offset: self._dirty = True

    def check_and_reset_dirty(self) -> bool:
        # Also dirty if uptime second changed
        curr_uptime = int(time.time() - self.start_time)
        if curr_uptime != self.last_uptime:
            self.last_uptime = curr_uptime
            return True
            
        if self._dirty:
            self._dirty = False
            return True
        return False

ui_state = UIState()

def get_header_renderable() -> Panel:
    try: width, height = shutil.get_terminal_size()
    except: width, height = 80, 24

    if height > 15 and width > 65:
        banner_text = art.text2art("VRCYTProxy", font="slant").strip()
        content = Group(
            Align.center(Text(banner_text, style="bold cyan", no_wrap=True)),
            Align.center(Text(f"High-Performance VRChat Video Redirector", style="italic grey70")),
            Align.center(Text(f"{CURRENT_VERSION} • {BUILD_TYPE}", style="bold grey37"))
        )
    else:
        content = Align.center(Text(f"VRCYTProxy • {CURRENT_VERSION}", style="bold cyan"))
    return Panel(content, border_style="cyan")

def get_activity_renderable() -> Panel:
    try:
        width, height = shutil.get_terminal_size()
        if height <= 0: height = 24
        header_size = 10 if (height > 15 and width > 65) else 3
        visible_count = max(3, height - header_size - 3 - 4)
    except:
        visible_count = 10
        width = 80

    table = Table.grid(expand=True, padding=(0, 1))
    table.add_column(style="grey37", width=10, no_wrap=True) # Timestamp
    table.add_column(width=6, justify="center", no_wrap=True) # Tag
    table.add_column(ratio=1) # Message
    
    with ui_state.lock:
        max_scroll = max(0, len(ui_state.recent_activities) - visible_count)
        ui_state.scroll_offset = min(ui_state.scroll_offset, max_scroll)
        
        start = ui_state.scroll_offset
        end = start + visible_count
        visible_items = ui_state.recent_activities[start:end]
        total_items = len(ui_state.recent_activities)

        if not visible_items:
            table.add_row("", "", "[grey37]Waiting for activity...[/]")
        else:
            for msg, level, ts in visible_items:
                t_str = time.strftime("%H:%M:%S", time.localtime(ts))
                tag = "•"; tag_style = "grey37"; display_msg = msg
                
                if "[Redirector]" in msg:
                    display_msg = msg.replace("[Redirector]", "").strip()
                    tag = "REDIR"; tag_style = "bold cyan"
                    display_msg = display_msg.replace("VALIDATED", "[bold green]VALIDATED[/]")
                    display_msg = display_msg.replace("SUCCESS", "[bold spring_green3]SUCCESS[/]")
                    display_msg = display_msg.replace("failed", "[bold red]failed[/]")
                    display_msg = display_msg.replace("FAILED", "[bold dark_orange]FAILED[/]")
                    display_msg = display_msg.replace("Cache Hit", "[bold magenta]Cache Hit[/]")
                    if "Tier" in display_msg:
                        display_msg = re.sub(r'(Tier \d)', r'[bold sky_blue1]\1[/]', display_msg)
                elif "[System]" in msg:
                    display_msg = msg.replace("[System]", "").strip()
                    tag = "SYSTEM"; tag_style = "bold white"
                    if "ENABLED" in display_msg: display_msg = f"[bold spring_green3]{display_msg}[/]"
                    if "DISABLED" in display_msg: display_msg = f"[bold grey50]{display_msg}[/]"

                if level == "error": tag_style = "bold red"
                elif level == "warning": tag_style = "bold yellow"
                elif level == "debug": tag_style = "bold grey37"
                table.add_row(t_str, Text(tag, style=tag_style), display_msg)
    
    title = "[bold white] Activity Timeline [/]"
    if width > 60:
        scroll_hint = f"[grey37](Scroll: {start+1}-{min(end, total_items)} of {total_items} | ↑/↓ Arrows)[/]"
        title = f"[bold white] Activity Timeline {scroll_hint} [/]"
        
    return Panel(table, title=title, border_style="grey23", padding=(1, 2))

from rich.columns import Columns

def get_footer_renderable() -> Panel:
    try: width, _ = shutil.get_terminal_size()
    except: width = 80

    uptime = int(time.time() - ui_state.start_time)
    h, m = divmod(uptime // 60, 60); s = uptime % 60
    uptime_str = f"{h:02d}:{m:02d}:{s:02d}"

    # Determine dynamic icon and style based on status
    status_lower = ui_state.status.lower()
    if "active" in status_lower:
        icon = Spinner("dots", style="bold cyan")
    elif "patching" in status_lower or "applying" in status_lower or "removing" in status_lower:
        icon = Spinner("aesthetic", style="bold yellow")
    else:
        icon = "ℹ"

    # Status Group
    status_group = Group(
        Text.assemble(
            (f" {ui_state.status}", "bold white"),
            (f"  Up: {uptime_str}", "grey37")
        )
    )

    grid = Table.grid(expand=True)
    grid.add_column(ratio=1)
    if width > 60: grid.add_column(justify="right")
    
    # Left side: Icon + Status
    left_side = Columns([icon, status_group])
    
    if width > 60:
        s = ui_state.stats
        stats_line = Text.assemble(
            ("Resolutions: ", "grey70"),
            (f"{s['t1']}", "bold magenta"), ("/", "grey37"),
            (f"{s['t2']}", "bold blue"), ("/", "grey37"),
            (f"{s['t3']}", "bold cyan"), ("/", "grey37"),
            (f"{s['fail']}", "bold red"),
            ("  │  World: ", "grey70"), (ui_state.world.upper(), "bold green" if ui_state.world != "public" else "bold yellow"),
            (" │ Engine: ", "grey70"), (ui_state.engine.upper(), "bold magenta")
        )
        grid.add_row(left_side, stats_line)
    else:
        grid.add_row(left_side)
        
    return Panel(grid, border_style="grey23")

def build_full_ui() -> Layout:
    layout = Layout()
    try:
        width, height = shutil.get_terminal_size()
    except:
        width, height = 80, 24

    header_size = 10 if (height > 15 and width > 65) else 3
    
    if height > 12:
        layout.split_column(
            Layout(get_header_renderable(), name="header", size=header_size),
            Layout(get_activity_renderable(), name="body"),
            Layout(get_footer_renderable(), name="footer", size=3),
        )
    elif height > 6:
        layout.split_column(
            Layout(get_header_renderable(), name="header", size=3),
            Layout(get_activity_renderable(), name="body"),
            Layout(get_footer_renderable(), name="footer", size=3)
        )
    else:
        layout.split_column(Layout(get_activity_renderable(), name="body"))
    
    return layout

# --- Global UI & Logging ---
console = Console()

def setup_logging():
    # Ensure log directory exists
    if not os.path.exists(LOG_DIR):
        try: os.makedirs(LOG_DIR, exist_ok=True)
        except: pass

    config_path = os.path.join(APP_BASE_PATH, CONFIG_FILE_NAME)
    defaults = {"debug_mode": BUILD_TYPE == "DEV"}
    if os.path.exists(config_path):
        try:
            with open(config_path, 'r') as f: defaults.update(json.load(f))
        except: pass
    
    level = logging.DEBUG if defaults.get("debug_mode") else logging.INFO
    
    # Standard logger for file
    logger = logging.getLogger('Patcher')
    logger.setLevel(level)
    
    try:
        fh = RotatingFileHandler(LOG_FILE_PATH, mode='w', maxBytes=10*1024*1024, backupCount=3, encoding='utf-8')
        fh.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
        logger.addHandler(fh)
    except: pass

    # Custom Handler to feed UI
    class UIHandler(logging.Handler):
        def emit(self, record):
            try:
                msg = self.format(record)
                lvl = "info"
                if record.levelno >= logging.ERROR: lvl = "error"
                elif record.levelno >= logging.WARNING: lvl = "warning"
                elif record.levelno >= logging.DEBUG: lvl = "debug"
                ui_state.add_activity(msg, lvl)
            except: pass

    uh = UIHandler()
    uh.setFormatter(logging.Formatter('%(message)s'))
    logger.addHandler(uh)
    
    return logger

logger = setup_logging()

# --- Shared Logic ---

def load_config(config_path):
    defaults = {"video_error_patterns": [], "instance_patterns": {}, "debug_mode": BUILD_TYPE == "DEV", "force_patch_in_public": False}
    if os.path.exists(config_path):
        try:
            with open(config_path, 'r', encoding='utf-8-sig') as f:
                user_config = json.load(f)
                for k, v in defaults.items():
                    if k not in user_config: user_config[k] = v
                return user_config
        except: pass
    return defaults

def get_vrchat_log_dir():
    config_path = os.path.join(APP_BASE_PATH, CONFIG_FILE_NAME)
    cfg = load_config(config_path)
    log_dir = cfg.get('vrchat_log_dir')
    if log_dir and os.path.exists(log_dir): return log_dir
    default = os.path.join(os.path.expanduser('~'), 'AppData', 'LocalLow', 'VRChat', 'VRChat')
    return default if os.path.exists(default) else None

# --- Global Logic Variables ---
CONFIG = {}
VRCHAT_LOG_DIR = None; VRCHAT_TOOLS_DIR = None; TARGET_YTDLP_PATH = None
ORIGINAL_YTDLP_BACKUP_PATH = None; REDIRECTOR_LOG_PATH = None; WRAPPER_STATE_PATH = None
system_mutex = None

class LogMonitor:
    def __init__(self):
        self.current_log = None; self.last_pos = 0; self.last_instance_type = "public"
        self.last_attempted_url = None; self.last_winner_tier = None; self.is_initial_scan = False

    def update_log_file(self, path):
        if path != self.current_log:
            self.current_log = path; self.last_pos = 0; self.is_initial_scan = True
            logger.info(f"[System] Monitoring Log: {os.path.basename(path)}")
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
                        for line in lines:
                            if "[AVProVideo] Opening" in line:
                                m = re.search(r'Opening\s+(https?://[^\s\)]+)', line)
                                if not self.is_initial_scan: update_wrapper_state(WRAPPER_STATE_PATH, active_player='avpro')
                            if "[VideoPlayer] Loading" in line or "[VideoPlayer] Opening" in line:
                                m = re.search(r'(?:Loading|Opening)\s+(https?://[^\s\)]+)', line)
                                if not self.is_initial_scan: update_wrapper_state(WRAPPER_STATE_PATH, active_player='unity')
                            
                            if '[Behaviour] Destination set:' in line or '[Behaviour] Joining wrld_' in line or '[Behaviour] Entering Room:' in line:
                                it = 'public'
                                if '~private' in line or '~canRequestInvite' in line: it = 'invite'
                                elif '~hidden' in line: it = 'friends+'
                                elif '~friends' in line: it = 'friends'
                                elif '~group' in line: it = 'group'
                                
                                if it != self.last_instance_type:
                                    if not self.is_initial_scan:
                                        logger.info(f"[System] Instance changed -> {it.upper()}")
                                    self.last_instance_type = it
                                    update_wrapper_state(WRAPPER_STATE_PATH, active_player='unknown')
                        self.last_pos = f.tell()
                        if self.is_initial_scan:
                            self.is_initial_scan = False
                            logger.info("[System] Log catch-up complete.")
        except: pass

def tail_log_file(log_path, stop_event):
    last_pos = 0
    while not stop_event.is_set():
        if os.path.exists(log_path):
            try:
                curr_size = os.path.getsize(log_path)
                if last_pos > curr_size: last_pos = 0
                if curr_size > last_pos:
                    with open(log_path, 'r', encoding='utf-8', errors='replace') as f:
                        f.seek(last_pos)
                        lines = f.readlines()
                        for line in lines:
                            line = line.strip()
                            if not line: continue
                            
                            # Extremely robust cleaning: Strip leading timestamps and bracketed metadata
                            # Matches '2026-02-23 19:15:09,842 [INFO] [yt-dlp-wrapper] '
                            clean_msg = re.sub(r'^\d{4}-\d{2}-\d{2}\s\d{2}:\d{2}:\d{2},\d{3}\s\[.*?\]\s\[.*?\]\s', '', line)
                            
                            # Secondary fallback: If the regex didn't change anything, try splitting by the last ']'
                            if clean_msg == line and ']' in line:
                                parts = line.split('] ')
                                if len(parts) >= 2: clean_msg = parts[-1]

                            full_msg = f"[Redirector] {clean_msg}"
                            
                            # Detect level from the raw line
                            # Update stats based on results
                            if "VALIDATED" in line or "SUCCESS" in line:
                                tier_match = re.search(r'Tier (\d)', line)
                                if tier_match: ui_state.update_stats(tier=int(tier_match.group(1)))
                            elif "failed" in line or "FAILED" in line:
                                if "[Redirector]" in full_msg: ui_state.update_stats(failed=True)

                            if "[ERROR]" in line: logger.error(full_msg)
                            elif "[WARNING]" in line: logger.warning(full_msg)
                            elif "[DEBUG]" in line: logger.debug(full_msg)
                            else: logger.info(full_msg)
                        last_pos = f.tell()
            except: pass
        time.sleep(0.5)

def get_patch_state():
    target_hash = calculate_sha256(TARGET_YTDLP_PATH)
    source_wrapper_path = os.path.join(SOURCE_WRAPPER_DIR, WRAPPER_EXE_NAME)
    wrapper_hash = calculate_sha256(source_wrapper_path)
    if not wrapper_hash: return PatchState.BROKEN
    if target_hash and target_hash == wrapper_hash: return PatchState.ENABLED
    return PatchState.DISABLED

def enable_patch(file_list):
    for attempt in range(3):
        try:
            if not os.path.exists(VRCHAT_TOOLS_DIR): os.makedirs(VRCHAT_TOOLS_DIR)
            shutil.copytree(SOURCE_WRAPPER_DIR, VRCHAT_TOOLS_DIR, dirs_exist_ok=True)
            source_wrapper_path = os.path.join(SOURCE_WRAPPER_DIR, WRAPPER_EXE_NAME)
            wh = calculate_sha256(source_wrapper_path)
            if os.path.exists(TARGET_YTDLP_PATH):
                th = calculate_sha256(TARGET_YTDLP_PATH)
                if th and wh and th != wh:
                    logger.info("[System] Backing up original yt-dlp.exe")
                    shutil.copy2(TARGET_YTDLP_PATH, ORIGINAL_YTDLP_BACKUP_PATH)
            shutil.copy2(os.path.join(VRCHAT_TOOLS_DIR, WRAPPER_EXE_NAME), TARGET_YTDLP_PATH)
            if calculate_sha256(TARGET_YTDLP_PATH) == wh:
                logger.info("[System] Patch ENABLED and verified.")
                return True
        except: time.sleep(1.0)
    return False

def disable_patch(file_list):
    try:
        if os.path.exists(ORIGINAL_YTDLP_BACKUP_PATH):
            shutil.copy2(ORIGINAL_YTDLP_BACKUP_PATH, TARGET_YTDLP_PATH)
        for filename in file_list:
            if filename.lower() in [TARGET_EXE_NAME.lower(), ORIGINAL_EXE_NAME.lower()]: continue
            path = os.path.join(VRCHAT_TOOLS_DIR, filename)
            if os.path.exists(path):
                try:
                    if os.path.isdir(path): shutil.rmtree(path, ignore_errors=True)
                    else: os.remove(path)
                except: pass
        cleanup_targets = [WRAPPER_STATE_PATH, REDIRECTOR_LOG_PATH, ORIGINAL_YTDLP_BACKUP_PATH]
        for target in cleanup_targets:
            if target and os.path.exists(target):
                try: os.remove(target)
                except: pass
        logger.info("[System] Patch DISABLED (Original state restored).")
        return True
    except: return False

def main():
    global CONFIG, VRCHAT_LOG_DIR, VRCHAT_TOOLS_DIR, TARGET_YTDLP_PATH, ORIGINAL_YTDLP_BACKUP_PATH, REDIRECTOR_LOG_PATH, WRAPPER_STATE_PATH, system_mutex
    
    if platform.system() == 'Windows':
        mutex_name = r"Global\VRCYTProxy_Patcher_Mutex"
        kernel32 = ctypes.windll.kernel32
        system_mutex = kernel32.CreateMutexW(None, False, mutex_name)
        if kernel32.GetLastError() == 183:
            print("\n[CRITICAL] VRCYTProxy is already running.\n")
            time.sleep(3); sys.exit(1)

    CONFIG = load_config(os.path.join(APP_BASE_PATH, CONFIG_FILE_NAME))
    VRCHAT_LOG_DIR = get_vrchat_log_dir()
    if not VRCHAT_LOG_DIR:
        print("VRChat log directory not found!")
        time.sleep(5); sys.exit(1)
        
    VRCHAT_TOOLS_DIR = os.path.join(VRCHAT_LOG_DIR, 'Tools')
    TARGET_YTDLP_PATH = os.path.join(VRCHAT_TOOLS_DIR, TARGET_EXE_NAME)
    ORIGINAL_YTDLP_BACKUP_PATH = os.path.join(VRCHAT_TOOLS_DIR, ORIGINAL_EXE_NAME)
    REDIRECTOR_LOG_PATH = os.path.join(VRCHAT_TOOLS_DIR, REDIRECTOR_LOG_NAME)
    WRAPPER_STATE_PATH = os.path.join(VRCHAT_TOOLS_DIR, 'wrapper_state.json')

    if os.path.exists(REDIRECTOR_LOG_PATH):
        try: os.remove(REDIRECTOR_LOG_PATH)
        except: pass
    update_wrapper_state(WRAPPER_STATE_PATH, active_player='unknown')

    with open(WRAPPER_FILE_LIST_PATH, 'r') as f: file_list = json.load(f)
    stop_event = threading.Event(); monitor = LogMonitor()
    
    threading.Thread(target=tail_log_file, args=(REDIRECTOR_LOG_PATH, stop_event), daemon=True).start()
    
    def vrc_monitor_loop():
        while not stop_event.is_set():
            logs = glob.glob(os.path.join(VRCHAT_LOG_DIR, 'output_log_*.txt'))
            if logs: monitor.update_log_file(max(logs, key=os.path.getmtime))
            monitor.tick()
            
            if ui_state.world != monitor.last_instance_type:
                ui_state.world = monitor.last_instance_type
                ui_state.mark_dirty()

            try:
                if os.path.exists(WRAPPER_STATE_PATH):
                    with open(WRAPPER_STATE_PATH, 'r') as f:
                        s = json.load(f)
                        new_engine = s.get('active_player', 'unknown')
                        if ui_state.engine != new_engine:
                            ui_state.engine = new_engine
                            ui_state.mark_dirty()
            except: pass
            time.sleep(0.5)
    threading.Thread(target=vrc_monitor_loop, daemon=True).start()

    atexit.register(lambda: [job_manager.close(), disable_patch(file_list)])
    def signal_handler(sig, frame):
        disable_patch(file_list); job_manager.close(); sys.exit(0)
    signal.signal(signal.SIGINT, signal_handler); signal.signal(signal.SIGTERM, signal_handler)

    with Live(screen=True, refresh_per_second=10) as live:
        while True:
            # 1. Regenerate UI only if dirty (Performance Optimization)
            if ui_state.check_and_reset_dirty():
                live.update(build_full_ui())

            # 2. Handle Input (Non-blocking)
            if msvcrt.kbhit():
                key = msvcrt.getch()
                if key == b'\x00' or key == b'\xe0':
                    key = msvcrt.getch()
                    if key == b'H': ui_state.scroll(-1)
                    elif key == b'P': ui_state.scroll(1)
                    elif key == b'I': ui_state.scroll(-10)
                    elif key == b'G': ui_state.scroll(10)

            # 3. Logic & States
            should_be_enabled = CONFIG.get("force_patch_in_public", False) or (monitor.last_instance_type not in ['public', 'group_public'])
            current_state = get_patch_state()
            
            if should_be_enabled:
                new_status = "System Active"
                if current_state != PatchState.ENABLED:
                    new_status = "Applying Patch..."
                    enable_patch(file_list)
                
                if ui_state.status != new_status:
                    ui_state.status = new_status
                    ui_state.mark_dirty()
            else:
                new_status = "Idle (Public)"
                if current_state == PatchState.ENABLED:
                    new_status = "Removing Patch..."
                    disable_patch(file_list)
                
                if ui_state.status != new_status:
                    ui_state.status = new_status
                    ui_state.mark_dirty()
            
            if current_state == PatchState.ENABLED:
                check_wrapper_health(file_list, VRCHAT_TOOLS_DIR, SOURCE_WRAPPER_DIR, WRAPPER_EXE_NAME)
            
            time.sleep(0.05)

if __name__ == '__main__':
    main()
