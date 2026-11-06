"""串行运行三个平台采集器：跨进程全局文件锁（fcntl）保证任何时刻只有一个采集器在跑；平台间随机间隔。
用法: python -m collectors.runner [--force] [--no-ocr] [--lcu] [xiaohongshu douyin weibo]"""
import fcntl, json, os, random, sys, time, contextlib, pathlib
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LOCK = pathlib.Path(__file__).resolve().parents[2] / "data" / "collectors.lock"

class Busy(Exception): pass

@contextlib.contextmanager
def global_lock(path=LOCK, wait_s=0):
    """独占锁；wait_s 内拿不到抛 Busy。进程退出时内核自动释放。"""
    path = pathlib.Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    f = open(path, "a+"); end = time.time() + wait_s
    while True:
        try: fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB); break
        except BlockingIOError:
            if time.time() >= end: f.close(); raise Busy(str(path))
            time.sleep(1)
    try:
        f.seek(0); f.truncate(); f.write(str(os.getpid())); f.flush(); yield
    finally:
        fcntl.flock(f, fcntl.LOCK_UN); f.close()

def collectors():
    from collectors.xiaohongshu import XHS
    from collectors.douyin import Douyin
    from collectors.weibo import Weibo
    return {"xiaohongshu": XHS, "douyin": Douyin, "weibo": Weibo}

def run_all(names=None, force=False, do_ocr=True, do_lcu=False, gap=(20, 60), sleep=time.sleep, factory=None, lock_path=LOCK):
    """持锁期间逐个平台串行执行，平台之间随机 sleep。"""
    reg = factory or collectors(); names = names or list(reg); reps = []
    with global_lock(lock_path, wait_s=0):
        for i, n in enumerate(names):
            if i: sleep(random.uniform(*gap))
            reps.append(reg[n]().run(force=force, do_ocr=do_ocr, do_lcu=do_lcu))
    return reps

if __name__ == "__main__":
    a = sys.argv[1:]; force = "--force" in a; ocr = "--no-ocr" not in a; lcu = "--lcu" in a
    names = [x for x in a if not x.startswith("--")] or None
    print(json.dumps(run_all(names, force=force, do_ocr=ocr, do_lcu=lcu), ensure_ascii=False, indent=1))
