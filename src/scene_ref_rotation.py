"""Rotate only the caller's eligible candidates; never widen scene/approval filters."""
import fcntl
import hashlib
import json
import os
import tempfile


def select(candidates, k, cfg, key):
    """Least recently selected first, persisted separately for each persona.

    Called only for actual generation, not probes or dry previews. The caller
    revalidates approvals before every selection. Failed generations also move
    on so a bad reference cannot pin retries to the same set.
    """
    if not candidates or k <= 0:
        return []
    from imagegen_scope import paths
    root = paths(cfg)[1].parent
    root.mkdir(parents=True, exist_ok=True)
    path = root / "scene_ref_rotation.json"
    bucket = hashlib.sha256(str(key).encode()).hexdigest()
    with (root / "scene_ref_rotation.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            state = json.loads(path.read_text())
            if not isinstance(state, dict):
                state = {}
        except (OSError, ValueError):
            state = {}
        previous = state.get(bucket, [])
        if not isinstance(previous, list):
            previous = []
        rank = {name: n for n, name in enumerate(previous)}
        def name(item):
            p = item[0] if isinstance(item, tuple) else item
            return str(p.resolve())
        # Stable sorting retains the caller's quality/day order for unseen refs.
        selected = sorted(candidates, key=lambda p: rank.get(name(p), -1))[:k]
        chosen = [name(p) for p in selected]
        eligible = {name(p) for p in candidates}
        state.pop(bucket, None)
        state[bucket] = [p for p in previous if p in eligible and p not in chosen] + chosen
        state = dict(list(state.items())[-128:])
        fd, tmp = tempfile.mkstemp(dir=root, prefix=".scene-rotation-")
        try:
            with os.fdopen(fd, "w") as out:
                json.dump(state, out)
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
        return selected
