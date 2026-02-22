import os
import json
import time
import logging
from urllib.parse import urlparse

logger = logging.getLogger("State")

def update_wrapper_state(state_path, is_broken=False, duration=None, failed_url=None, active_player=None, failed_tier=None):
    try:
        state = {'consecutive_errors': 0, 'failed_urls': {}, 'active_player': 'unknown', 'domain_blacklist': {}, 'cache': {}}
        if os.path.exists(state_path):
            try:
                with open(state_path, 'r') as f:
                    state = json.load(f)
            except Exception: pass
        
        if 'failed_urls' not in state: state['failed_urls'] = {}
        if 'domain_blacklist' not in state: state['domain_blacklist'] = {}
        
        if active_player:
            state['active_player'] = active_player
            if active_player == 'unknown':
                state['domain_blacklist'] = {}
                state['cache'] = {}
                logger.debug("Instance changed: Cleared transient state.")

        if is_broken:
            count = state.get('consecutive_errors', 0) + 1
            state['consecutive_errors'] = count
            
            if failed_url:
                try:
                    domain = urlparse(failed_url).netloc.lower()
                    if domain:
                        if domain not in state['domain_blacklist']:
                            state['domain_blacklist'][domain] = {'failed_tiers': [], 'expiry': 0}
                        if failed_tier and failed_tier not in state['domain_blacklist'][domain]['failed_tiers']:
                            state['domain_blacklist'][domain]['failed_tiers'].append(failed_tier)
                        state['domain_blacklist'][domain]['expiry'] = time.time() + 900
                        logger.warning(f"Domain '{domain}' blacklisted for Tier {failed_tier} (15m recovery).")
                except Exception: pass

                existing = state['failed_urls'].get(failed_url, {})
                
                # If we have a failed_tier (last winner), increment from that.
                # If we have no failed_tier (total failure), increment from current.
                current_recorded = existing.get('tier', 0)
                
                # If Tier 1 failed, next is Tier 2.
                # If we don't know what failed, increment from what we last tried.
                new_tier = min((failed_tier or current_recorded) + 1, 4)
                
                state['failed_urls'][failed_url] = {
                    'expiry': time.time() + 300,
                    'tier': new_tier,
                    'last_request_time': time.time()
                }
                if 'cache' in state and failed_url in state['cache']: del state['cache'][failed_url]
                logger.warning(f"URL Failed (Tier {failed_tier or '?'}). Next Attempt: Tier {new_tier}.")
            else:
                state['force_fallback'] = True
                wait_time = duration or (60 if count <= 1 else 300 if count == 2 else 900 if count == 3 else 3600)
                state['fallback_until'] = time.time() + wait_time
                logger.warning(f"Proxy Error #{count}. Falling back for {wait_time}s.")
        else:
            state['consecutive_errors'] = 0
            state['force_fallback'] = False
        
        now = time.time()
        state['failed_urls'] = {u: d for u, d in state.get('failed_urls', {}).items() if d.get('expiry', 0) > now}

        with open(state_path, 'w') as f:
            json.dump(state, f)
    except Exception as e:
        logger.error(f"Failed to update wrapper state: {e}")
