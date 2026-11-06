"""fact 抽取的相对日期换算。离线检查提示词；LLM=1 时真实调用 cursor-agent（不碰微信、不写记忆库）。"""
import sys, os, pathlib, datetime
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
import mem
d = datetime.date(2026, 10, 23)
p = mem.extract_prompt(["他: 明天面试"], today=d); assert "2026-10-23 星期五" in p and "相对时间换成具体日期" in p
if os.environ.get("LLM"):
    f = mem.extract_facts(["他: 我明天要去北京面试，下周三考六级", "他: 后天和室友去吃火锅", "小甜: 加油呀"], today=d)
    txt = " ".join(x["fact"] for x in f); print(f)
    for want in ("2026-10-24", "2026-10-28", "2026-10-25"): assert want in txt, (want, txt)
print("OK")
