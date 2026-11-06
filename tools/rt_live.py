"""有界前台实时回复：跑 daemon.Loop 到 --secs 截止（不经 launchd），记录检测/回复时延到 data/devtest/live.jsonl。"""
import sys, time, json, pathlib, argparse
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
import daemon, mac_wechat_io as io
OUT = pathlib.Path(__file__).resolve().parents[1] / "data" / "devtest"

class Probe:
    """包一层 io：记录指纹变化、读屏、发送时刻。"""
    def __init__(s, log): s.log, s.prev, s.changed_at = log, None, None
    def __getattr__(s, k): return getattr(io, k)
    def chat_fingerprint(s):
        fp = io.chat_fingerprint()
        if s.prev is not None and fp != s.prev and s.changed_at is None: s.changed_at = time.time(); s.log(ev="fp_change")
        s.prev = fp; return fp
    def read_chat(s):
        t = time.time(); ok, m = io.read_chat()
        s.log(ev="read", ok=ok, ms=int((time.time() - t) * 1000), tail=[x for x in m if x["from"] != "system"][-3:]); return ok, m
    def send_message(s, text):
        io.send_message(text); now = time.time()
        s.log(ev="sent", text=text, since_fp_change=round(now - s.changed_at, 1) if s.changed_at else None)
        s.changed_at = None

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--secs", type=float, default=600); a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True); f = open(OUT / "live.jsonl", "a")
    def L(**k):
        k["t"] = time.strftime("%H:%M:%S"); line = json.dumps(k, ensure_ascii=False); print(line, flush=True); f.write(line + "\n"); f.flush()
    lp = daemon.Loop(daemon.memory.db(), io=Probe(L)); end = time.time() + a.secs
    L(ev="start", secs=a.secs)
    while time.time() < end:
        try: d = lp.step()
        except Exception as e: L(ev="error", err=repr(e)); d = 4
        time.sleep(min(d, max(0, end - time.time())))
    L(ev="stop")

if __name__ == "__main__": main()
