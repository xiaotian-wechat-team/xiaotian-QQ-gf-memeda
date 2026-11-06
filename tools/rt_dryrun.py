"""只读实时探针：持 ui_lock 打开/校验目标聊天，之后只盯 chat_fingerprint，变化时 read_chat 一次 OCR。
不写 chat.db / STATE，不发送。截图与日志写 data/devtest/。用法：.venv/bin/python tools/rt_dryrun.py --secs 90"""
import sys, os, time, json, shutil, argparse, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
import mac_wechat_io as io, ui_lock as ul
OUT = pathlib.Path(__file__).resolve().parents[1] / "data" / "devtest"

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--secs", type=float, default=90); ap.add_argument("--tag", default="dry")
    a = ap.parse_args(); OUT.mkdir(parents=True, exist_ok=True)
    log = open(OUT / f"{a.tag}.jsonl", "a")
    def L(**k): k["t"] = time.strftime("%H:%M:%S"); print(json.dumps(k, ensure_ascii=False)); log.write(json.dumps(k, ensure_ascii=False) + "\n"); log.flush()
    with ul.ui_lock("reply"):
        io.open_chat(); ok, msgs = io.read_chat(); shutil.copy(io.SHOT, OUT / f"{a.tag}_0.png")
        L(ev="open", title_ok=ok, title=io.title(), msgs=msgs[-8:])
        if not ok: L(ev="abort", why="title check failed"); return 2
        last = io.chat_fingerprint()
    end = time.time() + a.secs; n = 0
    while time.time() < end:
        time.sleep(1.0); t0 = time.time(); fp = io.chat_fingerprint(); dfp = time.time() - t0
        if fp == last: continue
        n += 1
        with ul.ui_lock("reply"):
            t1 = time.time(); ok, msgs = io.read_chat(); dr = time.time() - t1
            shutil.copy(io.SHOT, OUT / f"{a.tag}_{n}.png"); last = io.chat_fingerprint()
        L(ev="change", fp=fp, fp_ms=int(dfp * 1000), read_ms=int(dr * 1000), title_ok=ok, msgs=msgs[-6:])
    L(ev="done", changes=n)

if __name__ == "__main__": sys.exit(main())
