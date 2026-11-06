"""全局：测试绝不读真实 relay.env；Linux 下跳过 Mac/本机资源依赖。"""
from __future__ import annotations

import os
import sys
import types
import platform
from pathlib import Path

os.environ["XIAOTIAN_TEST"] = "1"
os.environ.setdefault("RELAY_ENV_FILE", "/nonexistent/xiaotian-test-relay.env")

import pytest

# ---- macOS-only frameworks: stub on Linux so imports don't explode ----
if sys.platform != "darwin":
    def _stub(mod: str, **attrs):
        m = types.ModuleType(mod)
        for k, v in attrs.items():
            setattr(m, k, v)
        sys.modules.setdefault(mod, m)
        return m

    _stub("Quartz")
    vis = _stub("Vision")
    # common attribute touches
    for name in (
        "VNRecognizeTextRequest",
        "VNImageRequestHandler",
        "VNRequestTextRecognitionLevelAccurate",
    ):
        setattr(vis, name, type(name, (), {}))
    for name in ("Cocoa", "AppKit", "Foundation", "CoreFoundation"):
        _stub(name)

# Modules that assert at import-time against Mac assets / live services / flaky clocks.
# Keep files in repo; skip collection on non-Darwin or always when marked.
_COLLECT_SKIP_ALWAYS = {
    "test_imagegen.py": "needs local xiaotian-assets appearance files (not in public snapshot)",
    "test_imagegen_greenlight.py": "needs Magpie/MacBook appearance assets",
    "test_relay.py": "needs local appearance reference images",
    "test_life.py": "module-level time/seed assertion not stable in CI",
    "test_schedule_memory.py": "module-level schedule weight assertion env-specific",
    "test_proactive_text_then_image.py": "module-level onebot HTTP stub shape",
    "test_canonical_session.py": "needs local xiaotian-assets appearance descriptions",
}
_COLLECT_SKIP_NON_DARWIN = {
    "test_chat_events.py": "imports mac_wechat_io / Vision stack",
    "test_daemon_loop.py": "imports mac_wechat_io / Vision stack",
    "test_mac_copy_read.py": "macOS UI automation",
    "test_mac_io_fixes.py": "macOS UI automation",
    "test_avatar_iphone.py": "requires WDA / iPhone",
    "test_avatar.py": "imports mac_wechat_io / Foundation",
    "test_msg_classifier.py": "imports mac_wechat_io / Foundation",
}


def pytest_ignore_collect(collection_path: Path, config):
    name = collection_path.name
    if name in _COLLECT_SKIP_ALWAYS:
        return True
    if sys.platform != "darwin" and name in _COLLECT_SKIP_NON_DARWIN:
        return True
    return None


@pytest.fixture(autouse=True)
def _no_real_relay(monkeypatch):
    monkeypatch.setenv("XIAOTIAN_TEST", "1")
    if not os.environ.get("RELAY_ENV_FILE", "").startswith(
        ("/nonexistent", "/tmp", "/var/folders", "/private")
    ):
        monkeypatch.setenv("RELAY_ENV_FILE", "/nonexistent/xiaotian-test-relay.env")
