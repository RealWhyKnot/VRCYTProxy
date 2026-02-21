import os
import shutil
import logging
import subprocess
try:
    from jobs import job_manager
except ImportError:
    from .jobs import job_manager

logger = logging.getLogger("Health")

def check_wrapper_health(wrapper_file_list, tools_dir, source_dir, wrapper_exe_name):
    try:
        missing_files = []
        corrupted_files = []
        
        for filename in wrapper_file_list:
            if filename.lower() == wrapper_exe_name.lower(): continue
            file_path = os.path.join(tools_dir, filename)
            if not os.path.exists(file_path):
                missing_files.append(filename)
                continue
            
            if filename in ["deno.exe", "yt-dlp-latest.exe"]:
                try:
                    proc = subprocess.Popen(
                        [file_path, "--version"], 
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                        creationflags=subprocess.CREATE_NO_WINDOW
                    )
                    job_manager.assign(proc)
                    try:
                        out, err = proc.communicate(timeout=5.0)
                        if not out.strip() and not err.strip() and proc.returncode != 0:
                            corrupted_files.append(filename)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        corrupted_files.append(f"{filename} (timeout)")
                except Exception:
                    corrupted_files.append(filename)
        
        if missing_files or corrupted_files:
            reason = []
            if missing_files: reason.append(f"missing: {', '.join(missing_files)}")
            if corrupted_files: reason.append(f"non-functional: {', '.join(corrupted_files)}")
            logger.info(f"Health Check failed ({' and '.join(reason)}). Restoring components...")
            shutil.copytree(source_dir, tools_dir, dirs_exist_ok=True)
            return True
    except Exception as e:
        logger.debug(f"Health check failed: {e}")
    return False
