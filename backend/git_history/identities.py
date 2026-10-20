"""Identity helpers for Module 7."""

import hashlib
import json
from typing import Any


def compute_change_id(
    commit_id: str, change_type: str, old_path: Any, new_path: Any
) -> str:
    payload = {
        "commit_id": commit_id,
        "change_type": change_type,
        "old_path": old_path,
        "new_path": new_path,
    }
    canonical = json.dumps(
        payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )
    data = ("git-history:change:v1:" + canonical).encode("utf-8")
    return hashlib.sha256(data).hexdigest()
