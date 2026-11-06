"""Reply-style helpers: teasing discourse particles + soft phatic understanding.

Bug 1 — 「啧」开场偏高：
  catchphrase / skill 把「啧」排在口头禅首位，模型爱单独起「啧」。
  这里用同寄存器助词轮换 + near-use 去重（对齐 hold_on._near_dup_hold），
  并给 prompt 一条短提示；必要时轻量改写首气泡开场助词。

Bug 2 — 敷衍笑 / 「好呢」被当真笑追问：
  主路径是软理解（skill + 本轮 cue），不是硬白名单锁死行为。
  仅对极短纯语气/收尾粒子做启发式安全网，决定要不要挂 cue；
  笑明显挂在具体内容上时不挂「别追问」cue，允许轻损接。
"""
from __future__ import annotations

import random
import re

# 嘴损情侣同寄存器开场助词（与 language_pack / skill / nickname.tease 对齐；勿 OOC 新造）
TEASE_PARTICLES = (
    "切",
    "呵",
    "得了",
    "行吧",
    "嗯哼",
    "哼",
    "啧",
)

# 「啧」不特权：池内等权；近用出现过则避开
_PARTICLE_ALT = {
    "啧": ("切", "呵", "得了", "行吧", "嗯哼", "哼"),
    "切": ("呵", "得了", "行吧", "嗯哼", "哼", "啧"),
    "呵": ("切", "得了", "行吧", "嗯哼", "哼", "啧"),
    "得了": ("切", "呵", "行吧", "嗯哼", "哼", "啧"),
    "行吧": ("切", "呵", "得了", "嗯哼", "哼", "啧"),
    "嗯哼": ("切", "呵", "得了", "行吧", "哼", "啧"),
    "哼": ("切", "呵", "得了", "行吧", "嗯哼", "啧"),
}

# 开场：整泡是助词，或「助词 + 空白/逗号」起头
_OPENER_RE = re.compile(
    r"^(?P<p>" + "|".join(re.escape(p) for p in sorted(TEASE_PARTICLES, key=len, reverse=True)) + r")"
    r"(?P<rest>\s*[，,。.!！?？…~～]?|\s+|$)"
)

_PUNCT_RE = re.compile(r"[~～。.!！?？,，、…·\s\"'“”‘’「」【】（）()\[\]❤❤️🐻🙄😏😤🥺😂🤣😆😅]+")
_LAUGH_RUN_RE = re.compile(r"[啊呵嘿]*[哈嘻]{1,}|呵呵+")


def _near_dup_particle(a: str | None, b: str | None) -> bool:
    """对齐 photo_scene._near_dup_hold：同串或短包含即近重。"""
    a = (a or "").strip()
    b = (b or "").strip()
    if not a or not b:
        return False
    if a == b:
        return True
    if len(a) >= 2 and (a in b or b in a):
        return True
    return False


def extract_opener_particle(text: str | None) -> str | None:
    """首行/首泡开场助词；无则 None。"""
    if not text:
        return None
    line = str(text).splitlines()[0].strip()
    if not line:
        return None
    m = _OPENER_RE.match(line)
    return m.group("p") if m else None


def recent_opener_particles(hist, *, limit: int = 8) -> list[str]:
    """最近己方气泡开场助词（新→旧截断前先收集）。"""
    out: list[str] = []
    for h in reversed(hist or []):
        if isinstance(h, (list, tuple)) and len(h) >= 2:
            s, t = h[0], h[1]
        elif isinstance(h, dict):
            s = h.get("from") or h.get("role") or h.get("sender")
            t = h.get("text") or h.get("content") or ""
        else:
            continue
        if s not in ("me", "assistant"):
            continue
        # 一条 outbound 可能多行气泡
        for line in str(t or "").splitlines():
            p = extract_opener_particle(line)
            if p:
                out.append(p)
                if len(out) >= limit:
                    return out
    return out


def pick_particle(
    recent: list[str] | None = None,
    rng: random.Random | None = None,
    *,
    avoid: str | None = "啧",
) -> str:
    """轮换抽一个助词；避开 recent 近重。avoid 默认压「啧」连用。"""
    rng = rng or random.Random()
    recent = [str(x) for x in (recent or []) if x]
    pool = list(TEASE_PARTICLES)
    # 近用过的降权：先滤掉近重
    cand = [p for p in pool if not any(_near_dup_particle(p, r) for r in recent)]
    if not cand:
        cand = list(pool)
    # 若「啧」刚用过或池里还有别的，进一步避开 avoid
    if avoid and len(cand) > 1:
        soft = [p for p in cand if not _near_dup_particle(p, avoid)]
        if soft and any(_near_dup_particle(avoid, r) for r in recent):
            cand = soft
        elif soft and avoid in recent:
            cand = soft
    return rng.choice(cand)


def particle_prompt_tip(hist=None, rng: random.Random | None = None) -> str:
    """本轮短提示：近用「啧」偏多时挂轮换 cue；否则空（技能正文已写轮换原则）。"""
    recent = recent_opener_particles(hist)
    ze_n = sum(1 for p in recent if _near_dup_particle(p, "啧"))
    if ze_n < 1 and not (recent and recent[0] == "啧"):
        # 仍给极轻提示的条件：最近 6 条开场里啧 ≥2
        if ze_n < 2:
            return ""
    hint = pick_particle(recent, rng, avoid="啧")
    return f"开场助词轮换偶用，勿总起啧；本轮可偏「{hint}」。"


def diversify_opener(
    text: str | None,
    hist=None,
    rng: random.Random | None = None,
) -> str:
    """安全网：首泡是近用过的「啧」开场 → 换成同寄存器助词。不改其余内容。"""
    t = (text or "").strip()
    if not t:
        return t
    recent = recent_opener_particles(hist)
    if not any(_near_dup_particle(p, "啧") for p in recent):
        return t
    lines = t.splitlines()
    first = lines[0].strip()
    m = _OPENER_RE.match(first)
    if not m or m.group("p") != "啧":
        return t
    # 整泡就是「啧」或「啧 + 轻标点」
    rest = first[m.end():].lstrip()
    alt = pick_particle(recent + ["啧"], rng, avoid="啧")
    if first == "啧" or (not rest and m.group("p") == "啧"):
        lines[0] = alt
    else:
        # 「啧 脸皮挺厚」→「切 脸皮挺厚」
        lines[0] = alt + (m.group("rest") if rest else " ") + rest
        lines[0] = re.sub(r" {2,}", " ", lines[0]).strip()
    return "\n".join(lines)


def looks_phatic_soft(text: str | None) -> str | None:
    """软路由标签：'phatic' | 'content_laugh' | None。

    不是回复白名单。只决定要不要挂理解 cue。
    - phatic：更像语气粒子/敷衍收尾/空笑
    - content_laugh：笑挂在具体事上 → 可轻损，不挂「别追问」
    """
    t = (text or "").strip()
    if not t:
        return None

    has_laugh = bool(_LAUGH_RUN_RE.search(t) or re.search(r"[哈嘻]{2,}", t))
    if has_laugh:
        core = _LAUGH_RUN_RE.sub("", t)
        core = _PUNCT_RE.sub("", core)
        if len(core) >= 2 and re.search(r"[\u4e00-\u9fffA-Za-z0-9]", core):
            return "content_laugh"
        return "phatic"

    # 极短纯收尾——安全网启发式（示例形态，非锁死表）
    # 单字哦/嗯/好/行：靠 skill 软原则，不挂长 cue（避免每轮「哦」烧 token）
    bare = _PUNCT_RE.sub("", t)
    if not bare or len(bare) > 6 or len(bare) < 2:
        return None
    if re.search(r"想你|在吗|晚安|早啊|爱你|抱抱|亲亲|忙|累|难过", bare):
        return None
    if re.search(r"[吃喝睡走看说来想做学写打买去回]|照片|图|课|饭|工作|代码", bare):
        return None
    # 2～4 字语气/敷衍收尾（好呢、嗯嗯、哦吼、好的…）；勿当穷尽名单
    if re.fullmatch(
        r"[好嗯哦行哎欸唉喔噢]+[呢的啊呀吧啦嘛喔噢吼豁]*",
        bare,
    ):
        return "phatic"
    return None

def phatic_prompt_tip(text: str | None) -> str:
    """软理解 cue：默认别把空笑/语气收尾当真开心追问。"""
    kind = looks_phatic_soft(text)
    if kind == "phatic":
        return (
            "他这条更像随口语气/敷衍收尾或空笑，"
            "默认别当真追问（别问笑什么/就完了）；"
            "笑挂在具体事上才可轻损接。"
        )
    if kind == "content_laugh":
        return "笑挂在内容上，可轻损接，别盘问笑什么。"
    return ""


def style_prompt_block(hist=None, last_them: str | None = None, rng=None) -> str:
    """拼进 brain 提示词的短块（可空）。"""
    parts = []
    pt = particle_prompt_tip(hist, rng)
    if pt:
        parts.append(pt)
    ft = phatic_prompt_tip(last_them)
    if ft:
        parts.append(ft)
    if not parts:
        return ""
    return "# 语气\n" + "\n".join(parts) + "\n"
