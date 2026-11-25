import copy, json, os, pathlib
from collections.abc import MutableMapping
from contextlib import contextmanager
from contextvars import ContextVar
ROOT = pathlib.Path(__file__).resolve().parent.parent
_SNAPSHOT = ContextVar("persona_config_snapshot", default=None)

def _read():
    if os.environ.get("COMPANION_QQ_ROLE"):
        from companion_qq import runtime_config
        return runtime_config()
    if os.environ.get("ROLE_REGISTRY_DIR"):
        from role_runtime import selected_config
        base = selected_config()
        from personalization_runtime import overlay
        return overlay(base)
    path = pathlib.Path(os.environ.get("XIAOTIAN_CONFIG_FILE") or ROOT / "config.json")
    base = json.loads(path.read_text())
    from identity import scoped_load
    from personalization_runtime import overlay
    return overlay(scoped_load(base, root=ROOT))

def load():
    current = _SNAPSHOT.get()
    return current if current is not None else _read()

@contextmanager
def snapshot(cfg=None):
    """Every downstream load in a task sees exactly the same persona version."""
    if _SNAPSHOT.get() is not None:
        yield _SNAPSHOT.get()
        return
    value = copy.deepcopy(cfg if cfg is not None else _read())
    token = _SNAPSHOT.set(value)
    try:
        yield value
    finally:
        _SNAPSHOT.reset(token)

class ConfigProxy(MutableMapping):
    """For legacy modules that held import-time CFG; supports test overrides."""
    def __init__(self):
        self.overrides = {}
    def _value(self):
        return {**load(), **self.overrides}
    def __getitem__(self, key):
        return self._value()[key]
    def __iter__(self):
        return iter(self._value())
    def __len__(self):
        return len(self._value())
    def __setitem__(self, key, value):
        self.overrides[key] = value
    def __delitem__(self, key):
        self.overrides.pop(key, None)
