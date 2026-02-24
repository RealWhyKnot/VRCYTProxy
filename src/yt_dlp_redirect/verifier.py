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
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=ssl_context) as resp:
                status = resp.getcode()
                content_type = resp.headers.get('Content-Type', '').lower()
                
                logger.debug(f"[Verifier] HEAD {status} - Type: {content_type}")

                # If it's a direct video/audio type, we're likely good
                if any(x in content_type for x in ['video/', 'audio/', 'application/octet-stream', 'mpegurl', 'application/dash+xml']):
                    return True
                    
                # If it's HTML, it's almost certainly a fail (login page, 404 page, etc.)
                if 'text/html' in content_type:
                    logger.debug(f"Stream verification rejected: Content-Type is HTML.")
                    return False
        except urllib.error.HTTPError as e:
            # Some CDNs return 403 or 405 for HEAD. We fallback to GET.
            if e.code not in [403, 405]:
                logger.debug(f"HEAD check failed: HTTP {e.code}")
                return False
            logger.debug(f"[Verifier] HEAD returned {e.code}, using GET Range fallback.")

        # 2. Manifest/Stream Deep Check (GET)
        req_get = urllib.request.Request(url, method='GET', headers=headers)
        req_get.add_header('Range', 'bytes=0-8192')
        
        with urllib.request.urlopen(req_get, timeout=timeout, context=ssl_context) as resp:
            content = resp.read(8192).decode('utf-8', errors='ignore').strip()
            content_upper = content.upper()
            
            # Signature checks
            if content_upper.startswith('#EXTM3U') or '<MPD' in content_upper or '<?XML' in content_upper:
                if '#EXT-X-STREAM-INF' in content_upper:
                    # Master playlist - check first variant
                    lines = [line.strip() for line in content.split('\n') if line.strip() and not line.startswith('#')]
                    if lines:
                        for line in lines:
                            if not line.startswith('#'):
                                next_url = urljoin(url, line)
                                return verify_stream(next_url, timeout, depth + 1, ua)
                return True
                
            # If it's not a manifest but we got data and it's not HTML, consider it verified
            if len(content) > 0 and '<HTML' not in content_upper and '<!DOCTYPE' not in content_upper:
                return True

    except Exception as e: 
        logger.debug(f"Verification Exception: {e}")
    
    return False

def verify_stream_with_ytdlp(ytdlp_path, url, timeout=15.0):
    """
    Uses the actual yt-dlp binary to verify if a URL is playable.
    Returns: True (Success), False (Failed), None (Binary doesn't support validation flags)
    """
    if not os.path.exists(ytdlp_path): return False
    try:
        # Use --get-url as it implies --simulate and is widely supported
        cmd = [ytdlp_path, "--get-url", url]
        
        # Modern yt-dlp supports more quiet/safe flags
        if "latest" in ytdlp_path.lower():
            cmd = [ytdlp_path, "--no-warnings", "--ignore-errors", "--get-url", url]

        logger.debug(f"Running binary verification: {' '.join(cmd)}")
        process = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW
        )
        job_manager.assign(process)
        try:
            stdout, stderr = process.communicate(timeout=timeout)
            err_text = stderr.decode(errors='ignore').lower()
            
            if process.returncode == 0 and stdout.strip():
                return True
            
            # If binary doesn't support --get-url (very unlikely), fallback
            if "no such option" in err_text and "--get-url" in err_text:
                logger.debug(f"[Verifier] Binary {os.path.basename(ytdlp_path)} doesn't support --get-url.")
                return None
                
            logger.debug(f"[Verifier] Binary check failed ({process.returncode}): {err_text.strip()}")
            return False
        except subprocess.TimeoutExpired:
            process.kill()
            return False
    except Exception as e: 
        logger.debug(f"Binary check exception: {e}")
        return False
