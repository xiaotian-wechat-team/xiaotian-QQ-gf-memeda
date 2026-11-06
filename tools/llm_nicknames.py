"""用 cursor-agent 分类别生成候选昵称并按词义打 5 维分 → data/nick_candidates/*.json
用法: python3.13 tools/llm_nicknames.py gen   |  review  |  build"""
import json, subprocess, sys, re, pathlib, concurrent.futures as cf
ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "nick_candidates"; OUT.mkdir(parents=True, exist_ok=True)
CA = str(pathlib.Path.home() / ".local/bin/cursor-agent")
DIMS = ["sweet", "tease", "intimacy", "serious", "playful"]
CATS = {
 "sweet": "甜腻撒娇类（宝贝、宝宝、心肝、甜心、乖乖…及其自然叠词/后缀变体）",
 "tease": "损友式亲昵调侃（笨蛋、小傻瓜、猪头、憨憨、呆瓜、小祖宗…嘴损但不伤人）",
 "intimacy": "亲密伴侣身份类（老婆、老公、媳妇儿、亲爱的、我家那位、另一半、心上人…）",
 "serious": "认真/安慰/郑重场合会叫的称呼（亲爱的、宝贝、全名式昵称、我的人…温柔不调侃）",
 "playful": "活泼卖萌与网络常用语（宝子、集美、小可爱、崽崽、宝儿、酱、鸭…网络流行叫法）",
 "food_animal": "可爱的动物/食物系昵称（小猪、猪猪、小猫咪、兔兔、团子、小笼包、奶糖…）",
 "dialect": "方言味的亲昵称呼（东北/粤语/川渝/台湾/上海等：媳妇儿、老妹儿、傻妞、BB、宝儿、靓仔、囡囡…）",
 "daily": "日常随口叫的称呼（亲、宝、喂、这位、大宝、小宝、某人、那谁、懒虫…）",
 "pet_suffix": "带『宝/宝宝/家的/仔/崽/儿』等后缀的自然组合（臭宝、猪宝、乖宝宝、我家的、笨崽…）",
 "bear": "小熊/熊/维尼相关的亲昵称呼（对方小名是小熊：维尼、小维尼、小熊、熊熊、小熊、熊宝、笨熊、胖熊、蜜罐熊、熊崽…只要自然的，约 40 个）",
 "english_mix": "中英混合或外来语（honey、baby、达令、亲爱哒、宝贝儿~、sweetie…真实微信常用）",
}
def ask(prompt):
    r = subprocess.run([CA, "-p", "--trust", "--output-format", "text", prompt], capture_output=True, text=True,
                       timeout=600, cwd=str(ROOT / "data"))
    m = re.search(r"\[.*\]", r.stdout, re.S)
    if not m: raise RuntimeError(r.stdout[-300:] + r.stderr[-300:])
    return json.loads(m.group(0))
SCORE = ("每条打 5 维分，0~1 两位小数，按词义：sweet 甜度, tease 调侃程度, intimacy 亲密度, "
         "serious 正式/认真（安慰或郑重时能否用）, playful 活泼度。")
def gen(cat, desc):
    p = (f"你是熟悉中国年轻人微信聊天习惯的语言学者。列出 150 个真实中文情侣在微信里确实会叫出口的称呼，类别：{desc}。"
         "要求：真实常用、口语自然，不要生硬拼凑（如『傻星星』『笨鹿儿』这类没人这么叫的）；无侮辱/不雅/色情词；"
         "不要用『爸爸』『妈妈』等亲属称谓。" + SCORE +
         '只输出 JSON 数组，不要别的文字，不要调用工具、不要写文件。格式：[{"name":"臭宝","sweet":0.7,"tease":0.4,"intimacy":0.85,"serious":0.1,"playful":0.7}]')
    data = ask(p); (OUT / f"gen_{cat}.json").write_text(json.dumps(data, ensure_ascii=False)); return cat, len(data)
def review(i, names):
    p = ("你是严格的审核员。下面是候选的中文情侣微信昵称。逐条判断：这是不是真实中国情侣在微信里最常用、自然、会叫出口的称呼？宁缺毋滥，只保留常用的（大约保留三分之一到一半）。小熊/熊相关的自然称呼请保留（对方小名是小熊）。关系：嘴损的情侣，甜中带损优先，性别中性的优先；带性别的（老公/老婆/公主等）可保留；剔除爸爸/主人类。"
         "剔除生硬拼凑、没人用的、侮辱/不雅/色情、亲属称谓（爸爸妈妈等）的词。保留的请重新按词义打分。" + SCORE +
         '只输出保留项的 JSON 数组，格式 [{"name":..,"sweet":..,"tease":..,"intimacy":..,"serious":..,"playful":..}]，不要调用工具或写文件。\n候选：'
         + "、".join(names))
    data = ask(p); (OUT / f"rev_{i:02d}.json").write_text(json.dumps(data, ensure_ascii=False)); return i, len(data)
if __name__ == "__main__":
    mode = sys.argv[1]
    if mode == "gen":
        todo = [c for c in CATS if not (OUT / f"gen_{c}.json").exists()]
        with cf.ThreadPoolExecutor(5) as ex:
            for f in cf.as_completed([ex.submit(gen, c, CATS[c]) for c in todo]):
                try: print(*f.result(), flush=True)
                except Exception as e: print("ERR", e, flush=True)
    elif mode == "review":
        seen, items = set(), []
        for c in CATS:
            for d in json.loads((OUT / f"gen_{c}.json").read_text()):
                n = str(d.get("name", "")).strip()
                if n and n not in seen: seen.add(n); items.append(n)
        (OUT / "cat_map.json").write_text(json.dumps({c: [str(d["name"]).strip() for d in json.loads((OUT / f"gen_{c}.json").read_text())] for c in CATS}, ensure_ascii=False))
        print("unique candidates", len(items))
        chunks = [items[i:i+120] for i in range(0, len(items), 120)]
        with cf.ThreadPoolExecutor(5) as ex:
            for f in cf.as_completed([ex.submit(review, i, ch) for i, ch in enumerate(chunks) if not (OUT / f"rev_{i:02d}.json").exists()]):
                try: print(*f.result(), flush=True)
                except Exception as e: print("ERR", e, flush=True)

BAN = ("爸", "妈", "主人", "爹", "娘", "屎", "死", "贱", "骚", "滚", "蠢", "丑", "狗东西", "sb")
def merged():
    cmap = json.loads((OUT / "cat_map.json").read_text())
    cat_of = {}
    for c, ns in cmap.items():
        for n in ns: cat_of.setdefault(n, c)
    keep = {}
    for f in sorted(OUT.glob("rev_*.json")):
        for d in json.loads(f.read_text()):
            n = str(d["name"]).strip()
            if n in keep or any(b in n.lower() for b in BAN): continue
            keep[n] = {"name": n, "category": cat_of.get(n, "other"),
                       "vec": {k: round(min(1, max(0, float(d[k]))), 2) for k in DIMS}}
    return keep
def gender_tag(names):
    p = ("给下面每个中文情侣昵称标注它在性别上的指向：m=只适合称呼男性（如老公、哥哥），f=只适合称呼女性（如老婆、公主、小仙女），"
         'n=中性。只输出 JSON 数组 [{"name":..,"gender":"n"}]，不要调用工具或写文件。\n' + "、".join(names))
    return {d["name"]: d["gender"] for d in ask(p)}
if __name__ == "__main__" and sys.argv[1] == "build":
    keep = merged()
    # 人工二审：去掉仅语气词/标点不同的水词变体，以及带性暗示或攻击性的
    for n in list(keep):
        b = re.sub(r"(~|呀|啊|哒)$", "", n)
        if b != n and b in keep: keep.pop(n)
    for n in ("小妖精", "杠精", "小杠精", "babe呀", "baby哒"): keep.pop(n, None)
    names = list(keep)
    gf = OUT / "gender.json"
    g = json.loads(gf.read_text()) if gf.exists() else {}
    miss = [n for n in names if n not in g]
    chunks = [miss[i:i+150] for i in range(0, len(miss), 150)]
    with cf.ThreadPoolExecutor(4) as ex:
        for r in ex.map(gender_tag, chunks): g.update(r)
    gf.write_text(json.dumps(g, ensure_ascii=False))
    items = []
    for n in names:
        keep[n]["gender"] = g.get(n, "n") if g.get(n) in ("m", "f", "n") else "n"; items.append(keep[n])
    data = {"dims": DIMS, "count": len(items),
            "method": "cursor-agent 按 11 个场景类别生成候选并按词义打分 → 分批审核（真实常用、宁缺毋滥、嘴损情侣关系）并重新打分 → 去重、禁词过滤 → 大模型标注 gender(m/f/n)",
            "nicknames": items}
    (ROOT / "data" / "nicknames.json").write_text(json.dumps(data, ensure_ascii=False, indent=1))
    print("final", len(items))
