import os
import logging
import subprocess
import platform
import json
import time
import urllib.request
from urllib.parse import quote_plus

try:
    from jobs import job_manager
    from verifier import verify_stream, verify_stream_with_ytdlp
except ImportError:
    from .jobs import job_manager
    from .verifier import verify_stream, verify_stream_with_ytdlp

logger = logging.getLogger("Resolver")

def get_speed_flags(executable_path):
    """Returns a list of flags optimized for speed based on version-safe detection."""
    is_og = "og" in executable_path.lower()
    
    if is_og:
        # VRChat's custom yt-dlp-og is extremely stripped down.
        # Only use flags verified via --help output.
        return [
            "--quiet",
            "--no-cache-dir",
            "--no-check-certificates"
        ]
    
    # Standard high-speed flags for the full 'latest' yt-dlp version
    return [
        "--no-warnings",
        "--ignore-errors",
        "--no-check-certificates",
        "--no-playlist",
        "--no-cache-dir",
        "--no-mtime",
        "--no-check-formats", 
        "--no-video-multistreams"
    ]

def attempt_executable(path, executable_name, args, app_base_path, timeout=10.0):
    if not os.path.exists(path): return None, 1
    try:
        # 1. Prepare Environment
        env = os.environ.copy()
        temp_dir = os.path.join(app_base_path, "_tmp")
        if not os.path.exists(temp_dir): os.makedirs(temp_dir)
        env['TMP'] = temp_dir
        env['TEMP'] = temp_dir
        
        # 2. Inject Speed-Up Flags
        speed_flags = get_speed_flags(path)
        final_args = list(args)
        for flag in reversed(speed_flags):
            if flag not in final_args:
                final_args.insert(0, flag)
        
        cmd = [path] + final_args
        logger.debug(f"Executing: {' '.join(cmd)}")
        
        # 3. High-Precision Launch Timing
        launch_start = time.perf_counter()
        
        process = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env,
            creationflags=subprocess.CREATE_NO_WINDOW if platform.system() == 'Windows' else 0
        )
        job_manager.assign(process)
        
        try:
            stdout, stderr = process.communicate(timeout=timeout)
            elapsed = time.perf_counter() - launch_start
            
            # Log exact launch/resolve time in debug as requested
            logger.debug(f"[{executable_name}] Resolution took {elapsed:.3f}s")
            
            if process.returncode == 0: return stdout.strip(), 0
            
            logger.debug(f"Process {executable_name} FAILED (Code {process.returncode}). Stderr: {stderr.strip()}")
            return None, process.returncode
        except subprocess.TimeoutExpired:
            process.kill()
            logger.debug(f"Process {executable_name} TIMED OUT.")
            return None, -1
    except Exception as e: 
        logger.debug(f"Error attempting executable {executable_name}: {e}")
        return None, 1

from verifier import ssl_context

def resolve_via_proxy(target_url, incoming_args, res_timeout, custom_ua, remote_server_base, player_hint):
    try:
        video_type = "va"
        for i, arg in enumerate(incoming_args):
            if arg == "--format" and i + 1 < len(incoming_args):
                if "bestaudio" in incoming_args[i+1]: video_type = "a"
                break
        
        resolve_url = f"{remote_server_base}/api/stream/resolve?url={quote_plus(target_url)}&video_type={video_type}&player={player_hint}"
        logger.debug(f"Proxy Request: {resolve_url}")
        req = urllib.request.Request(resolve_url, method='GET')
        if custom_ua: req.add_header("User-Agent", custom_ua)
        
        with urllib.request.urlopen(req, timeout=res_timeout, context=ssl_context) as response:
            if response.status == 200:
                body = response.read().decode()
                if body.strip().startswith("<!DOCTYPE") or "<html" in body.lower():
                    logger.debug("Proxy returned HTML instead of JSON (likely Smart Routing page).")
                    return None
                
                try:
                    data = json.loads(body)
                    url = data.get("stream_url") or data.get("url")
                    if url: return url
                    logger.debug("Proxy result missing URL field.")
                except json.JSONDecodeError:
                    logger.debug(f"Failed to decode proxy JSON. Body starts with: {body[:50]}")
            else:
                logger.debug(f"Proxy returned HTTP {response.status}")
    except Exception as e:
        logger.debug(f"Proxy connection failed: {e}")
    return None

def resolve_tier_1_proxy(target_url, incoming_args, res_timeout, custom_ua, remote_base, player_hint):
    """Tier 1: Proxy. Internal verification removed as main.py handles it."""
    url = resolve_via_proxy(target_url, incoming_args, res_timeout, custom_ua, remote_base, player_hint)
    if url:
        return {"tier": 1, "url": url}
    return None

def resolve_tier_2_modern(incoming_args, res_timeout, custom_ua, app_base_path, latest_path, latest_filename, max_height, is_legacy):
    """Tier 2: Modern yt-dlp."""
    args = list(incoming_args)
    # Remove old format if present
    if "-f" in args: 
        idx = args.index("-f")
        args.pop(idx); args.pop(idx)
    elif "--format" in args:
        idx = args.index("--format")
        args.pop(idx); args.pop(idx)

    deno_path = os.path.join(app_base_path, "deno.exe")
    args.extend(["--remote-components", "ejs:github"])
    if os.path.exists(deno_path):
        args.extend(["--extractor-args", f"ejs:deno_path={deno_path}"])

    if is_legacy:
        args.extend(["-f", f"best[height<={max_height}][ext=mp4][vcodec^=avc1][acodec^=mp4a][protocol^=http][protocol!*=m3u8][protocol!*=dash]/best[height<={max_height}]/best"])
    else:
        # Use more robust format selection that handles audio-only (SoundCloud) correctly
        args.extend(["-f", f"(bestvideo[height<={max_height}]+bestaudio)/best[height<={max_height}]"])

    res, code = attempt_executable(latest_path, latest_filename, args, app_base_path, timeout=res_timeout)
    if code == 0 and res:
        return {"tier": 2, "url": res}
    return None

def resolve_tier_3_native(incoming_args, res_timeout, app_base_path, native_path, native_filename):
    """Tier 3: Native yt-dlp."""
    res, code = attempt_executable(native_path, native_filename, incoming_args, app_base_path, timeout=res_timeout)
    if code == 0 and res:
        return {"tier": 3, "url": res}
    return None
