import os
import logging
import urllib.request
import urllib.error
import subprocess
from urllib.parse import urljoin
try:
    from jobs import job_manager
except ImportError:
    from .jobs import job_manager

logger = logging.getLogger("Verifier")

def verify_stream(url, timeout=3.0, depth=0):
    """Deep verification of a stream. Recursively checks bitrate manifests."""
    if not url or depth > 2: return False
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "*/*",
        "Accept-Encoding": "identity",
        "Accept-Language": "en-US,en;q=0.9",
        "Connection": "keep-alive"
    }
    
    try:
        # 1. Initial HEAD check
        req = urllib.request.Request(url, method='HEAD', headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status >= 400: 
                logger.debug(f"HEAD check failed: HTTP {resp.status} for {url[:50]}...")
                return False
            
            content_type = resp.headers.get('Content-Type', '').lower()
            if any(x in content_type for x in ['video/', 'audio/', 'application/octet-stream']):
                return True
        
        # 2. Manifest Deep Check
        is_manifest = any(x in url.lower() for x in ['.m3u8', '.mpd', 'manifest'])
        if is_manifest:
            req_get = urllib.request.Request(url, method='GET', headers=headers)
            with urllib.request.urlopen(req_get, timeout=timeout) as resp:
                content = resp.read().decode('utf-8', errors='ignore').strip()
                if content.startswith('<!DOCTYPE') or '<html' in content.lower(): 
                    logger.debug(f"Manifest failed: HTML returned instead of manifest for {url[:50]}...")
                    return False
                if not any(x in content for x in ['#EXTM3U', 'MPD', 'Playlist']): 
                    logger.debug(f"Manifest failed: Invalid format/missing signature for {url[:50]}...")
                    return False

                lines = [line.strip() for line in content.split('\n') if line.strip() and not line.startswith('#')]
                if lines:
                    target_uri = lines[0]
                    if not target_uri.startswith('http'):
                        target_uri = urljoin(url, target_uri)
                    
                    if any(x in target_uri.lower() for x in ['.m3u8', '.mpd']):
                        return verify_stream(target_uri, timeout, depth + 1)
                    
                    seg_req = urllib.request.Request(target_uri, method='HEAD', headers=headers)
                    with urllib.request.urlopen(seg_req, timeout=timeout) as seg_resp:
                        if seg_resp.status >= 400:
                            logger.debug(f"Segment check failed: HTTP {seg_resp.status} for {target_uri[:50]}...")
                        return seg_resp.status < 400
        else:
            logger.debug(f"Stream check failed: Unrecognized Content-Type '{content_type}' for {url[:50]}...")
        return True 
    except Exception as e: 
        logger.debug(f"Verification Exception for {url[:50]}...: {e}")
        return False

def verify_stream_with_ytdlp(ytdlp_path, url, timeout=5.0):
    """Uses the actual yt-dlp binary to verify if a URL is playable."""
    if not os.path.exists(ytdlp_path): return False
    try:
        cmd = [ytdlp_path, "--check-formats", "--no-warnings", "--ignore-errors", url]
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW
        )
        job_manager.assign(proc)
        try:
            proc.communicate(timeout=timeout)
            return proc.returncode == 0
        except subprocess.TimeoutExpired:
            proc.kill()
            return False
    except Exception: return False
