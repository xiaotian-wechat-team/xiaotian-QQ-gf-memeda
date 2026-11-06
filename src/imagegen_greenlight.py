"""本机生图 greenlight / 预检。

在跑 Codex 之前：Magpie :3425 是否在听，挂了就 open 拉起；再确认 codex/out_dir。
红灯立刻失败（走 relay 应急），避免空等 TimeoutExpired。Magpie 可能被人为退出清内存——只需启动，不必做激进清理。

用法：
  tools/imagegen_greenlight.py           # CLI，绿=0 / 红=1
  tools/imagegen_greenlight.py --json    # 机器可读
  from imagegen_greenlight import greenlight; greenlight(cfg)  # 红灯抛 LocalBackendError
"""
from __future__ import annotations
import argparse, json, os, pathlib, shutil, subprocess, sys, time, urllib.request

DEFAULT_MAGPIE_URL = "http://127.0.0.1:3425/v1/models"
DEFAULT_MAGPIE_APP = "~/Applications/magpie.app"
DEFAULT_WAIT = 45
MIN_FREE_MB = 512
MEM_WARN_FREE_GB = 1.0  # 低于此仅警告，不红灯


def _cfg_ig(cfg=None):
    if cfg is None:
        try:
            sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
            from config import load
            cfg = load()
        except Exception:
            cfg = {}
    return cfg, (cfg.get("imagegen") or {})


def magpie_ok(url=DEFAULT_MAGPIE_URL, timeout=2.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=float(timeout)) as r:
            return 200 <= getattr(r, "status", 200) < 500
    except Exception:
        return False


def start_magpie(app: str) -> None:
    app = os.path.expanduser(app)
    try:
        subprocess.run(["open", app], check=False, capture_output=True, timeout=15)
    except Exception:
        pass


def check_magpie(ig: dict, *, auto_start=True) -> dict:
    url = ig.get("magpie_url") or DEFAULT_MAGPIE_URL
    app = ig.get("magpie_app") or DEFAULT_MAGPIE_APP
    wait = float(ig.get("magpie_wait_sec") or DEFAULT_WAIT)
    if magpie_ok(url):
        return {"ok": True, "name": "magpie", "detail": f"up {url}"}
    if not auto_start:
        return {
            "ok": False, "name": "magpie",
            "detail": f"down {url}",
            "fix": f"启动 Magpie：open {os.path.expanduser(app)} 或 tools/xiaotian_ctl.sh start magpie；确认 curl -sS {url}",
        }
    start_magpie(app)
    deadline = time.time() + wait
    while time.time() < deadline:
        if magpie_ok(url):
            return {"ok": True, "name": "magpie", "detail": f"started → up {url}"}
        time.sleep(1.0)
    return {
        "ok": False, "name": "magpie",
        "detail": f"still down after {wait:.0f}s ({url})",
        "fix": (
            f"1) open {os.path.expanduser(app)}  2) tools/xiaotian_ctl.sh install magpie && "
            f"tools/xiaotian_ctl.sh start magpie  3) curl -sS {url} 应 200。"
            "Magpie 挂掉时 codex 会空等到 TimeoutExpired，不要靠加长 timeout。"
        ),
    }


def check_codex(ig: dict) -> dict:
    cmd = (ig.get("cmd") or os.environ.get("IMAGEGEN_CODEX") or "codex").strip()
    path = cmd if os.path.isabs(cmd) and os.path.isfile(cmd) else shutil.which(cmd)
    if not path:
        return {
            "ok": False, "name": "codex",
            "detail": f"not found: {cmd}",
            "fix": "装 Codex CLI 并保证在 PATH（launchd 用 ~/.local/bin）。"
                     "或把 imagegen.cmd 设成绝对路径如 /Users/…/.local/bin/codex。"
                     "曾出现 FileNotFoundError: codex（daemon PATH 缺 pi-node）。",
        }
    try:
        r = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=10)
        ver = (r.stdout or r.stderr or "").strip().splitlines()[:1]
        ver_s = ver[0] if ver else "ok"
    except Exception as e:
        return {"ok": False, "name": "codex", "detail": str(e)[:200],
                "fix": f"无法执行 {path} --version；检查安装与权限。"}
    # login status（非致命：失败只警告）
    login = None
    try:
        lr = subprocess.run([path, "login", "status"], capture_output=True, text=True, timeout=15)
        login = (lr.stdout or lr.stderr or "").strip().splitlines()[:1]
        login = login[0] if login else None
    except Exception:
        pass
    warn = None
    if login and "logged in" not in login.lower() and "chatgpt" not in login.lower():
        warn = f"login status 异常：{login}（未登录时不要在 mini 上发设备码）"
    return {"ok": True, "name": "codex", "detail": f"{path} ({ver_s})", "warn": warn}


def check_out_dir(ig: dict) -> dict:
    out = os.path.expanduser(ig.get("out_dir") or "~/xiaotian-assets/generated")
    p = pathlib.Path(out)
    try:
        p.mkdir(parents=True, exist_ok=True)
        t = p / ".greenlight_write_test"
        t.write_text("ok"); t.unlink()
    except Exception as e:
        return {"ok": False, "name": "out_dir", "detail": str(e)[:200],
                "fix": f"确保可写目录：{out}（imagegen.out_dir）"}
    try:
        usage = shutil.disk_usage(str(p))
        free_mb = usage.free / (1024 * 1024)
    except Exception:
        free_mb = None
    if free_mb is not None and free_mb < MIN_FREE_MB:
        return {"ok": False, "name": "disk", "detail": f"{free_mb:.0f}MB free under {p}",
                "fix": f"清理磁盘（至少预留 {MIN_FREE_MB}MB）：~/xiaotian-assets/generated 旧目录、~/.codex/sessions。"}
    detail = f"{p} writable" + (f", {free_mb:.0f}MB free" if free_mb is not None else "")
    return {"ok": True, "name": "out_dir", "detail": detail}


def check_mem() -> dict:
    """macOS vm_stat 粗估；仅警告不红灯。"""
    try:
        out = subprocess.run(["vm_stat"], capture_output=True, text=True, timeout=5).stdout
        page = 16384
        for line in out.splitlines():
            if "page size of" in line:
                try: page = int(line.split("page size of")[1].split()[0])
                except Exception: pass
        def pages(key):
            for line in out.splitlines():
                if key in line:
                    return int(line.split(":")[1].strip().rstrip(".").replace(",", ""))
            return 0
        free = (pages("Pages free") + pages("Pages speculative")) * page / 1e9
        warn = None
        if free < MEM_WARN_FREE_GB:
            warn = (f"可用内存偏低 ≈{free:.2f}GB free+spec；可关 Chrome 重标签或 "
                    f"kill 残留 codex。不阻断生图。")
        return {"ok": True, "name": "memory", "detail": f"≈{free:.2f}GB free+spec", "warn": warn}
    except Exception as e:
        return {"ok": True, "name": "memory", "detail": f"skip ({e})", "warn": None}


def run_checks(cfg=None, *, auto_start_magpie=True) -> list[dict]:
    if os.environ.get("XIAOTIAN_TEST") == "1":
        return [{"ok": True, "name": "test_skip", "detail": "XIAOTIAN_TEST=1"}]
    if os.environ.get("IMAGEGEN_SKIP_GREENLIGHT") in ("1", "true", "yes"):
        return [{"ok": True, "name": "skip", "detail": "IMAGEGEN_SKIP_GREENLIGHT"}]
    _, ig = _cfg_ig(cfg)
    return [
        check_magpie(ig, auto_start=auto_start_magpie),
        check_codex(ig),
        check_out_dir(ig),
        check_mem(),
    ]


def greenlight(cfg=None, *, auto_start_magpie=True, raise_on_red=True) -> list[dict]:
    """供 imagegen 调用。红灯且 raise_on_red 时抛 LocalBackendError（走 MacBook/relay 应急）。"""
    checks = run_checks(cfg, auto_start_magpie=auto_start_magpie)
    reds = [c for c in checks if not c.get("ok")]
    for c in checks:
        if c.get("warn"):
            print(f"[greenlight] WARN {c['name']}: {c['warn']}", file=sys.stderr)
        mark = "OK" if c.get("ok") else "RED"
        print(f"[greenlight] {mark} {c['name']}: {c.get('detail','')}", file=sys.stderr)
    if reds and raise_on_red:
        lines = []
        for c in reds:
            lines.append(f"{c['name']}: {c.get('detail')}")
            if c.get("fix"):
                lines.append(f"  修复 → {c['fix']}")
        msg = "本机生图预检未通过：\n" + "\n".join(lines)
        # 延迟导入避免环
        try:
            from imagegen import LocalBackendError
        except Exception:
            LocalBackendError = RuntimeError  # type: ignore
        raise LocalBackendError(msg)
    return checks


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="本机 imagegen greenlight 预检")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-start", action="store_true", help="Magpie 挂了不自动 open")
    args = ap.parse_args(argv)
    checks = run_checks(auto_start_magpie=not args.no_start)
    reds = [c for c in checks if not c.get("ok")]
    if args.json:
        print(json.dumps({"ok": not reds, "checks": checks}, ensure_ascii=False, indent=2))
    else:
        for c in checks:
            mark = "OK " if c.get("ok") else "RED"
            print(f"{mark}  {c['name']}: {c.get('detail','')}")
            if c.get("warn"):
                print(f"      warn: {c['warn']}")
            if not c.get("ok") and c.get("fix"):
                print(f"      fix: {c['fix']}")
        print("GREEN" if not reds else "RED — 勿直接 generate，先修上述项（否则会空等超时）")
    return 1 if reds else 0


if __name__ == "__main__":
    raise SystemExit(main())
