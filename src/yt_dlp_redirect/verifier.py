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
                
                logger.debug(f"[Verifier] HEAD {status} - Type: {content_type} - URL: {url[:60]}...")

                # If it's a direct video/audio type, we're likely good
                if any(x in content_type for x in ['video/', 'audio/', 'application/octet-stream', 'mpegurl', 'application/dash+xml']):
                    return True
                    
                # If it's HTML, it's almost certainly a fail (login page, 404 page, etc.)
                if 'text/html' in content_type:
                    logger.debug(f"[Verifier] Rejected: Result is HTML.")
                    return False
        except urllib.error.HTTPError as e:
            # Some CDNs return 403 or 405 for HEAD. We fallback to GET.
            if e.code not in [403, 405]:
                logger.debug(f"[Verifier] HEAD failed with HTTP {e.code}")
                return False
            logger.debug(f"[Verifier] HEAD returned {e.code}, falling back to GET Range.")

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
                                logger.debug(f"[Verifier] HLS Master found, checking variant: {next_url[:60]}...")
                                return verify_stream(next_url, timeout, depth + 1, ua)
                return True
                
            # If it's not a manifest but we got data and it's not HTML, consider it verified
            if len(content) > 0 and '<HTML' not in content_upper and '<!DOCTYPE' not in content_upper:
                logger.debug(f"[Verifier] GET Success: Binary data found ({len(content)} bytes).")
                return True
            else:
                logger.debug(f"[Verifier] GET Failed: Data contains HTML or is empty.")

    except Exception as e: 
        logger.debug(f"[Verifier] Exception: {e}")
    
    return False

def verify_stream_with_ytdlp(ytdlp_path, url, timeout=15.0):
    """
    Uses the actual yt-dlp binary to verify if a URL is playable.
    """
    if not os.path.exists(ytdlp_path): 
        logger.debug(f"[Verifier] Binary missing: {ytdlp_path}")
        return False
    try:
        # --simulate --check-formats is the most accurate check yt-dlp has for actual playability
        cmd = [ytdlp_path, "--no-warnings", "--ignore-errors", "--simulate", "--check-formats", url]
        logger.debug(f"[Verifier] Launching binary check: {' '.join(cmd)}")
        process = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW
        )
        job_manager.assign(process)
        try:
            stdout, stderr = process.communicate(timeout=timeout)
            if process.returncode == 0:
                logger.debug("[Verifier] Binary check PASSED.")
                return True
            else:
                logger.debug(f"[Verifier] Binary check FAILED (Code {process.returncode}). Stderr: {stderr.decode(errors='ignore').strip()}")
                return False
        except subprocess.TimeoutExpired:
            process.kill()
            logger.debug("[Verifier] Binary check TIMED OUT.")
            return False
    except Exception as e: 
        logger.debug(f"[Verifier] Binary check EXCEPTION: {e}")
        return False
