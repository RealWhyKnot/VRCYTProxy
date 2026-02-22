import os
import logging
import urllib.request
import urllib.error
import subprocess
import ssl
from urllib.parse import urljoin

try:
    from jobs import job_manager
except ImportError:
    from .jobs import job_manager

logger = logging.getLogger("Verifier")

# Global context for SSL to avoid some certificate issues
ssl_context = ssl.create_default_context()
ssl_context.check_hostname = False
ssl_context.verify_mode = ssl.CERT_NONE

def verify_stream(url, timeout=5.0, depth=0, user_agent=None):
    """
    Verifies if a URL is actually a playable stream (not HTML/404).
    Uses a stealthy HEAD request followed by a recursive check for manifests.
    """
    if not url or depth > 3: return False
    
    ua = user_agent or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    headers = {
        "User-Agent": ua,
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Connection": "keep-alive"
    }
    
    try:
        # 1. Initial HEAD check (Stealthy)
        req = urllib.request.Request(url, method='HEAD', headers=headers)
        with urllib.request.urlopen(req, timeout=timeout, context=ssl_context) as resp:
            status = resp.getcode()
            if status >= 400: 
                logger.debug(f"HEAD failed: HTTP {status}")
                return False
            
            content_type = resp.headers.get('Content-Type', '').lower()
            
            # If it's a direct video/audio type, we're likely good
            if any(x in content_type for x in ['video/', 'audio/', 'application/octet-stream']):
                return True
                
            # If it's HTML, it's almost certainly a fail (login page, 404 page, etc.)
            if 'text/html' in content_type:
                logger.debug("HEAD result was HTML (Fail).")
                return False

        # 2. Manifest Deep Check (GET)
        # If it looks like a manifest URL or the content type is mpegurl/xml
        is_manifest_url = any(x in url.lower() for x in ['.m3u8', '.mpd', 'manifest'])
        
        req_get = urllib.request.Request(url, method='GET', headers=headers)
        # We only need the first 4KB to identify the format
        req_get.add_header('Range', 'bytes=0-4096')
        
        with urllib.request.urlopen(req_get, timeout=timeout, context=ssl_context) as resp:
            content = resp.read(4096).decode('utf-8', errors='ignore').strip()
            content_upper = content.upper()
            
            # Signature checks
            if content_upper.startswith('#EXTM3U') or '<MPD' in content_upper or '<?XML' in content_upper:
                # It's a manifest! 
                # For HLS, check if it points to another manifest (master playlist)
                if '#EXT-X-STREAM-INF' in content_upper:
                    # Try to find the first variant and verify it
                    lines = [line.strip() for line in content.split('\n') if line.strip() and not line.startswith('#')]
                    if lines:
                        next_url = urljoin(url, lines[0])
                        return verify_stream(next_url, timeout, depth + 1, ua)
                return True # It's a valid manifest
                
            # If it's not a manifest but we got data and it's not HTML, consider it verified
            if '<HTML' not in content_upper and '<!DOCTYPE' not in content_upper:
                return True

    except Exception as e: 
        logger.debug(f"Verification error: {e}")
    
    return False

def verify_stream_with_ytdlp(ytdlp_path, url, timeout=10.0):
    """Uses the actual yt-dlp binary to verify if a URL is playable."""
    if not os.path.exists(ytdlp_path): return False
    try:
        # --print is faster than --check-formats for simple verification
        cmd = [ytdlp_path, "--no-warnings", "--ignore-errors", "--get-url", url]
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW
        )
        job_manager.assign(proc)
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
            return proc.returncode == 0 and bool(stdout.strip())
        except subprocess.TimeoutExpired:
            proc.kill()
            return False
    except Exception: return False
