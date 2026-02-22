import os
import json
import time
import logging

logger = logging.getLogger("State")

def update_wrapper_state(state_path, active_player=None):
    """
    Updates the shared state between Patcher and Redirector.
    We now only track active player and history. Fallback logic is removed.
    """
    try:
        state = {'active_player': 'unknown', 'history': []}
        if os.path.exists(state_path):
            try:
                with open(state_path, 'r') as f:
                    state = json.load(f)
            except Exception: pass
        
        if 'history' not in state: state['history'] = []
        
        if active_player:
            state['active_player'] = active_player
            if active_player == 'unknown':
                # Instance changed, we could clear history but usually users want to keep it
                # across worlds for media toggling. We'll just log it.
                logger.debug("Instance changed: Monitoring new session.")

        # Prune expired history (older than 1 hour)
        now = time.time()
        state['history'] = [h for h in state.get('history', []) if (now - h[3] < 3600)]

        with open(state_path, 'w') as f:
            json.dump(state, f)
    except Exception as e:
        logger.error(f"Failed to update wrapper state: {e}")
