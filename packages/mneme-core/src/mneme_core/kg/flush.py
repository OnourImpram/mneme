"""Stop-hook KG flush: request a community-detection refresh.

``mark_community_refresh`` writes a small flag file. The next worker
drain reads the flag and, after the queue has been added to Graphiti,
calls ``graphiti.build_communities`` once before clearing the flag.

The community pass is deliberately not run in-process from the Stop
hook: ``build_communities`` is an LLM call and the Zero-LLM-Stop
constraint forbids that.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from .episode_stage import KgConfig, is_active


def mark_community_refresh(config: KgConfig, *, reason: str = "stop_hook") -> bool:
    """Write the community-refresh flag. Returns True on write, False otherwise.

    Never raises. Safe to call from the Stop hook even when the KG
    layer is inactive (returns False without writing).
    """
    try:
        if not is_active(config):
            return False
        config.community_refresh_flag.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "requested_at": datetime.now(timezone.utc).isoformat(),
            "reason": reason,
        }
        config.community_refresh_flag.write_text(
            json.dumps(payload, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return True
    except Exception:
        return False
