import os
import logging
import subprocess
import platform
import json
import urllib.request
from urllib.parse import quote_plus
try:
    from jobs import job_manager
    from verifier import verify_stream, verify_stream_with_ytdlp
except ImportError:
    from .jobs import job_manager
    from .verifier import verify_stream, verify_stream_with_ytdlp

logger = logging.getLogger("Resolver")

def attempt_executable(path, executable_name, args, app_base_path, timeout=10.0):
    if not os.path.exists(path): return None, 1
    try:
        env = os.environ.copy()
        temp_dir = os.path.join(app_base_path, "_tmp")
        if not os.path.exists(temp_dir): os.makedirs(temp_dir)
        env['TMP'] = temp_dir
        env['TEMP'] = temp_dir
        
        cmd = [path] + args
        logger.debug(f"Launching {executable_name}: {' '.join(cmd)}")
        
        process = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env,
            creationflags=subprocess.CREATE_NO_WINDOW if platform.system() == 'Windows' else 0
        )
        job_manager.assign(process)
        
        try:
            stdout, stderr = process.communicate(timeout=timeout)
            if process.returncode == 0: return stdout.strip(), 0
            return None, process.returncode
        except subprocess.TimeoutExpired:
            process.kill()
            return None, -1
    except Exception: return None, 1

def resolve_via_proxy(target_url, incoming_args, res_timeout, custom_ua, remote_server_base, player_hint):
    try:
        video_type = "va"
        for i, arg in enumerate(incoming_args):
            if arg == "--format" and i + 1 < len(incoming_args):
                if "bestaudio" in incoming_args[i+1]: video_type = "a"
                break
        
        resolve_url = f"{remote_server_base}/api/stream/resolve?url={quote_plus(target_url)}&video_type={video_type}&player={player_hint}"
        req = urllib.request.Request(resolve_url, method='GET')
        if custom_ua: req.add_header("User-Agent", custom_ua)
        
        with urllib.request.urlopen(req, timeout=res_timeout) as response:
            if response.status == 200:
                data = json.loads(response.read().decode())
                return data.get("stream_url") or data.get("url")
    except Exception: return None

def resolve_tier_1_proxy(target_url, incoming_args, res_timeout, custom_ua, remote_base, player_hint):
    """Tier 1: Proxy."""
    url = resolve_via_proxy(target_url, incoming_args, res_timeout, custom_ua, remote_base, player_hint)
    if url and verify_stream(url):
        logger.info(f"Tier 1 SUCCESS (Proxy): {url[:100]}...")
        return {"tier": 1, "url": url}
    elif url:
        logger.warning("WINNER: Tier 1 (Proxy) FAILED Deep Verification.")
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
        args.extend(["-f", f"bestvideo[height<={max_height}]+bestaudio/best[height<={max_height}]"])

    res, code = attempt_executable(latest_path, latest_filename, args, app_base_path, timeout=res_timeout)
    if code == 0 and res and verify_stream_with_ytdlp(latest_path, res):
        logger.info(f"Tier 2 SUCCESS (Modern): {res[:100]}...")
        return {"tier": 2, "url": res}
    return None

def resolve_tier_3_native(incoming_args, res_timeout, app_base_path, native_path, native_filename):
    """Tier 3: Native yt-dlp."""
    res, code = attempt_executable(native_path, native_filename, incoming_args, app_base_path, timeout=res_timeout)
    if code == 0 and res and verify_stream_with_ytdlp(native_path, res):
        logger.info("Tier 3 SUCCESS (Native).")
        return {"tier": 3, "url": res}
    return None
