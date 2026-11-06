"""小甜日程更新管线：freeze / 用户 override 后门 / 学业优先拒出门。

接线目标（见 docs/schedule-update-pipeline.md）:
  1) XHS/Douyin 调研 → activity_pool
  2) duration Agent 填池时长
  3) schedule-fill → life_schedule
  4) Mac mini Cursor (REASONABLENESS_AGENT / grok-4.7-high) 复审
  5) 每周更新/审查下月一次 → FREEZE → sync_to_memory 喂给小甜
  6) 用户 override：用户说「下周陪我出门」等 → 所有 review Agent 必须 yield；
     小甜微信问清时间/路线；确认用户真实回复后只改该槽，其余不变
  7) 学业优先：毕业答辩/考试周等可拒陪出门

本模块只提供钩子与纯函数；不启动 wechat daemon。
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import re
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCHEDULE = ROOT / "data" / "life_schedule_2026q4.json"
OVERRIDE_PENDING = ROOT / "data" / "schedule_override_pending.json"

# 用户要约出门 / 改行程（小甜侧应确认具体时间路线；review Agent 不得代决）
OVERRIDE_PATTERNS = [
    r"下周.*(?:出门|出去|见一面|见面|约会|陪我|来找我|一起)",
    r"(?:出门|出去|见面|约会|陪我|来找我).*(?:下周|这周末|明天|后天|这周)",
    r"陪我(?:出门|出去|玩|逛街|吃饭)",
    r"(?:下周|这周|周末).*(?:出去玩|出门玩|一起玩)",
    r"出去玩",
    r"(?:约|改|空出|留).*?(?:时间|行程|日程)",
    r"下周去.{0,12}(?:吧|啊|呀|哦)?$",
]

# 学业硬优先：这些窗口小甜可以拒绝陪出门（除非用户已确认 override 且非答辩当日）
STUDY_BLOCK_KINDS = frozenset({"exam_week", "thesis_defense", "finals"})
STUDY_TEXT_RE = re.compile(
    r"毕业答辩|开题答辩|期末考试|考试周|考研|论文答辩|答辩周|期末周|闭关复习"
)


def load_schedule(path: pathlib.Path | None = None) -> dict:
    p = path or SCHEDULE
    if not p.exists():
        return {"version": 1, "days": [], "frozen": None, "overrides": []}
    return json.loads(p.read_text())


def save_schedule(doc: dict, path: pathlib.Path | None = None) -> pathlib.Path:
    p = path or SCHEDULE
    p.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n")
    return p


def is_frozen(doc: dict | None = None, *, on: dt.date | None = None) -> bool:
    """整月/整窗冻结后，自动月更与 reasonableness 改写须 yield（用户 override 除外）。"""
    doc = doc if doc is not None else load_schedule()
    fr = doc.get("frozen")
    if not fr or not fr.get("active"):
        return False
    on = on or dt.date.today()
    start = fr.get("from")
    end = fr.get("to")
    if start and on < dt.date.fromisoformat(start):
        return False
    if end and on > dt.date.fromisoformat(end):
        return False
    return True


def freeze_month(
    year: int,
    month: int,
    *,
    doc: dict | None = None,
    note: str = "weekly next-month update",
    path: pathlib.Path | None = None,
) -> dict:
    """冻结指定自然月；写入后供 sync_to_memory 喂小甜。"""
    doc = doc if doc is not None else load_schedule(path)
    start = dt.date(year, month, 1)
    if month == 12:
        end = dt.date(year, 12, 31)
    else:
        end = dt.date(year, month + 1, 1) - dt.timedelta(days=1)
    doc["frozen"] = {
        "active": True,
        "from": start.isoformat(),
        "to": end.isoformat(),
        "frozen_at": dt.datetime.now().isoformat(timespec="seconds"),
        "note": note,
        "feed": "life_calendar.sync_to_memory",
    }
    save_schedule(doc, path)
    return doc["frozen"]


def unfreeze(*, doc: dict | None = None, path: pathlib.Path | None = None) -> dict:
    doc = doc if doc is not None else load_schedule(path)
    fr = doc.get("frozen") or {}
    fr["active"] = False
    fr["unfrozen_at"] = dt.datetime.now().isoformat(timespec="seconds")
    doc["frozen"] = fr
    save_schedule(doc, path)
    return fr


def review_agents_must_yield(doc: dict | None = None) -> dict:
    """所有时长/行程 review Agent 在下列情况必须 yield，不得改写日程。"""
    doc = doc if doc is not None else load_schedule()
    pending = load_override_pending()
    reasons = []
    if pending and pending.get("status") in ("awaiting_user_confirm", "confirmed_pending_apply", "study_refused"):
        reasons.append("user_override_pending")
    if is_frozen(doc) and not (pending and pending.get("status") == "confirmed_pending_apply"):
        # freeze 挡住自动月更/Agent 改写；已确认的用户 override 仍可落单槽
        reasons.append("schedule_frozen")
    return {
        "yield": bool(reasons),
        "reasons": reasons,
        "rule": "user override > freeze > review agents; agents never invent time/route",
    }


def detect_user_override_intent(text: str) -> dict | None:
    """从用户微信原文检测「要约出门/改行程」。不解析具体时间——必须由小甜追问确认。"""
    if not text or not str(text).strip():
        return None
    t = str(text).strip()
    for pat in OVERRIDE_PATTERNS:
        if re.search(pat, t):
            return {
                "intent": "user_schedule_override",
                "raw": t,
                "must_ask": ["exact_time", "route_or_place"],
                "agents_yield": True,
                "note": "小甜微信确认用户真实回复后，仅改该槽；review Agent 不代决",
            }
    return None


def load_override_pending() -> dict | None:
    if not OVERRIDE_PENDING.exists():
        return None
    try:
        return json.loads(OVERRIDE_PENDING.read_text())
    except Exception:
        return None


def save_override_pending(doc: dict | None) -> None:
    if doc is None:
        if OVERRIDE_PENDING.exists():
            OVERRIDE_PENDING.unlink()
        return
    OVERRIDE_PENDING.parent.mkdir(parents=True, exist_ok=True)
    OVERRIDE_PENDING.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n")


def _outing_window_dates(user_text: str, today: dt.date | None = None) -> list[dt.date]:
    """Heuristic dates the user might mean (next week / weekend / tomorrow)."""
    today = today or dt.date.today()
    t = user_text or ""
    out: list[dt.date] = []
    if "明天" in t:
        out.append(today + dt.timedelta(days=1))
    if "后天" in t:
        out.append(today + dt.timedelta(days=2))
    # 下周：接下来的周一~周日
    if "下周" in t or "这周" in t or "周末" in t or not out:
        # next 14 days sample
        for i in range(1, 15):
            out.append(today + dt.timedelta(days=i))
    # unique preserve order
    seen = set()
    uniq = []
    for d in out:
        if d not in seen:
            seen.add(d)
            uniq.append(d)
    return uniq


def study_blocks_override_for_text(user_text: str, today: dt.date | None = None) -> dict | None:
    """If any date in the outing window is exam/defense, return that block (override blocked)."""
    for d in _outing_window_dates(user_text, today):
        blk = study_priority_block(d, user_ask_outing=True)
        if blk.get("blocks_override") or (blk.get("block") and not blk.get("user_override_still_wins")):
            return {**blk, "date": d.isoformat()}
    return None


def begin_override_confirm(user_text: str, *, cid: str | None = None) -> dict:
    """标记后门进行中：review Agents yield；小甜应追问时间/路线。

    若窗口落在考试周/毕业答辩：不进入确认，直接 study_refused（小甜可拒）。
    """
    intent = detect_user_override_intent(user_text) or {
        "intent": "user_schedule_override",
        "raw": user_text,
        "must_ask": ["exact_time", "route_or_place"],
        "agents_yield": True,
    }
    blocked = study_blocks_override_for_text(user_text)
    if blocked:
        pending = {
            "status": "study_refused",
            "created_at": dt.datetime.now().isoformat(timespec="seconds"),
            "cid": cid,
            "user_raw": user_text,
            "intent": intent,
            "study_block": blocked,
            "wechat_ask": blocked.get("refuse_line")
                or "这阵子学业为重，去不了……换考完/答辩后再约好不好？",
            "agents_yield": True,
        }
        save_override_pending(pending)
        return pending
    pending = {
        "status": "awaiting_user_confirm",
        "created_at": dt.datetime.now().isoformat(timespec="seconds"),
        "cid": cid,
        "user_raw": user_text,
        "intent": intent,
        "wechat_ask": "好呀——具体哪天几点、去哪条路线/地点？你定了我再改日程（别的安排不动）。",
        "agents_yield": True,
    }
    save_override_pending(pending)
    return pending


def confirm_override_from_user_reply(
    reply_text: str,
    *,
    date: str,
    start: str,
    end: str,
    activity: str,
    route: str | None = None,
) -> dict:
    """仅在小甜确认「这是用户真实回复」后调用；写入 pending 待 apply_override_slot。"""
    blk = study_priority_block(date, user_ask_outing=True)
    if blk.get("blocks_override") or (blk.get("block") and not blk.get("user_override_still_wins")):
        pending = load_override_pending() or {}
        pending.update({
            "status": "study_refused",
            "confirmed_at": dt.datetime.now().isoformat(timespec="seconds"),
            "user_reply": reply_text,
            "study_block": {**blk, "date": date},
            "wechat_ask": blk.get("refuse_line"),
            "agents_yield": True,
        })
        save_override_pending(pending)
        return pending
    pending = load_override_pending() or {}
    pending.update({
        "status": "confirmed_pending_apply",
        "confirmed_at": dt.datetime.now().isoformat(timespec="seconds"),
        "user_reply": reply_text,
        "slot": {
            "date": date,
            "start": start,
            "end": end,
            "activity": activity,
            "route": route,
            "bucket": "city_fun",
            "tone": "和他出门、开心",
            "photo_ok": True,
            "energy": 1,
            "source": "user_override",
        },
    })
    save_override_pending(pending)
    return pending


def apply_override_slot(
    *,
    doc: dict | None = None,
    path: pathlib.Path | None = None,
    clear_pending: bool = True,
) -> dict:
    """只改确认槽所在那天的对应时段；其余日程不变。绕过 freeze 与 review Agent。"""
    pending = load_override_pending()
    if not pending or pending.get("status") != "confirmed_pending_apply":
        raise RuntimeError("no confirmed override pending")
    slot = pending["slot"]
    blk = study_priority_block(slot.get("date"), user_ask_outing=True)
    if blk.get("blocks_override") or (blk.get("block") and not blk.get("user_override_still_wins")):
        pending["status"] = "study_refused"
        pending["study_block"] = {**blk, "date": slot.get("date")}
        save_override_pending(pending)
        return {
            "ok": False,
            "refused": "study_priority",
            "study_block": pending["study_block"],
            "refuse_line": blk.get("refuse_line"),
        }
    doc = doc if doc is not None else load_schedule(path)
    days = doc.setdefault("days", [])
    by = {d["date"]: d for d in days}
    day = by.get(slot["date"])
    if not day:
        # 窗外：先生成再改
        import life_calendar as lc
        day = lc.generate_day(dt.date.fromisoformat(slot["date"]))
        days.append(day)
        days.sort(key=lambda d: d["date"])
        by = {d["date"]: d for d in days}
        day = by[slot["date"]]

    new_slots = []
    replaced = False
    for s in day.get("slots") or []:
        # 覆盖与目标时段重叠的非 sleep 槽；sleep 保留
        if s.get("bucket") == "sleep":
            new_slots.append(s)
            continue
        if s.get("start") == slot["start"] or (
            s.get("start", "") <= slot["start"] < s.get("end", "24:00")
        ):
            if not replaced:
                new_slots.append({
                    "start": slot["start"],
                    "end": slot["end"],
                    "activity": slot["activity"],
                    "tone": slot.get("tone") or "和他出门、开心",
                    "bucket": slot.get("bucket") or "city_fun",
                    "scene_hints": [slot["route"]] if slot.get("route") else [],
                    "photo_ok": True,
                    "energy": slot.get("energy", 1),
                    "delay": [5, 40],
                    "source": "user_override",
                })
                replaced = True
            # drop overlapping original
            continue
        new_slots.append(s)
    if not replaced:
        new_slots.append({
            "start": slot["start"],
            "end": slot["end"],
            "activity": slot["activity"],
            "tone": slot.get("tone") or "和他出门、开心",
            "bucket": slot.get("bucket") or "city_fun",
            "scene_hints": [slot["route"]] if slot.get("route") else [],
            "photo_ok": True,
            "energy": 1,
            "delay": [5, 40],
            "source": "user_override",
        })
        new_slots.sort(key=lambda s: s.get("start") or "")
    day["slots"] = new_slots
    day["notes"] = ((day.get("notes") or "") + " | user_override").strip(" |")
    ov = doc.setdefault("overrides", [])
    ov.append({
        "applied_at": dt.datetime.now().isoformat(timespec="seconds"),
        "date": slot["date"],
        "slot": slot,
        "user_reply": pending.get("user_reply"),
    })
    save_schedule(doc, path)
    if clear_pending:
        save_override_pending(None)
    return {"ok": True, "date": slot["date"], "activity": slot["activity"], "others_unchanged": True}


def _load_study_blocks_doc() -> dict:
    pth = ROOT / "data" / "study_blocks.json"
    if not pth.exists():
        return {}
    try:
        return json.loads(pth.read_text())
    except Exception:
        return {}


def _study_block_entry(when: dt.date) -> dict | None:
    """Per-day study block with refuse_strength (0..1)."""
    doc = _load_study_blocks_doc()
    blocks = doc.get("blocks") or {}
    item = blocks.get(when.isoformat())
    if not item:
        return None
    if isinstance(item, str):
        item = {"kind": item, "label": item}
    out = dict(item)
    # derive strength from days_to_exam if missing
    exam_date = out.get("exam_date")
    if exam_date and "refuse_strength" not in out:
        try:
            ed = dt.date.fromisoformat(str(exam_date))
            days = (ed - when).days
            if days <= 0:
                out["refuse_strength"] = 1.0
            elif days <= 3:
                out["refuse_strength"] = 0.95
            elif days <= 7:
                out["refuse_strength"] = 0.85
            elif days <= 14:
                out["refuse_strength"] = 0.65
            elif days <= 21:
                out["refuse_strength"] = 0.45
            else:
                out["refuse_strength"] = 0.28
        except Exception:
            out["refuse_strength"] = 0.7
    out.setdefault("refuse_strength", 1.0 if out.get("kind") in STUDY_BLOCK_KINDS else 0.5)
    return out



def study_priority_block(
    when: dt.date | str | None = None,
    *,
    day: dict | None = None,
    user_ask_outing: bool = False,
) -> dict:
    """学业优先：考试周/答辩/复习窗可拒陪出门。

    study_blocks.json 带 refuse_strength：越靠近考试越硬拒；
    复习窗远端（2–4 周前段）边界模糊，更容易向用户出门让步。
    返回 {block, reason, kind, refuse_line, blocks_override, refuse_strength, ...}。
    """
    if isinstance(when, str):
        when = dt.date.fromisoformat(when)
    when = when or dt.date.today()
    kind = None
    text_hit = None
    entry = _study_block_entry(when)
    if day:
        kind = day.get("kind")
        blob = " ".join(
            [day.get("notes") or ""]
            + [s.get("activity") or "" for s in (day.get("slots") or [])]
        )
        m = STUDY_TEXT_RE.search(blob)
        if m:
            text_hit = m.group(0)
    if kind is None and day is None:
        try:
            import life_calendar as lc
            d = lc.get_day(when)
            if d:
                return study_priority_block(when, day=d, user_ask_outing=user_ask_outing)
            kind = lc._winter_kind(when)
        except Exception:
            kind = None
    if entry and not kind:
        kind = entry.get("kind")

    # 毕业答辩 / 开题答辩 — 硬拒，后门也让步给学业
    if text_hit in ("毕业答辩", "开题答辩", "论文答辩") or kind == "thesis_defense" or (
        entry and entry.get("kind") == "thesis_defense"
    ):
        return {
            "block": True,
            "reason": "thesis_defense",
            "kind": "thesis_defense",
            "label": (entry or {}).get("label"),
            "refuse_line": (entry or {}).get("refuse_line")
                or "那天我毕业答辩/答辩相关，真去不了……换一天行不行嘛",
            "user_override_still_wins": False,
            "blocks_override": True,
            "refuse_strength": 1.0,
        }

    # study_blocks 显式条目（含复习窗模糊强度）
    if entry and entry.get("kind") in ("finals", "exam_week", "exam_review", "thesis_defense"):
        strength = float(entry.get("refuse_strength") or 0.7)
        hard = strength >= 0.7 or entry.get("kind") in ("finals", "exam_week", "thesis_defense")
        # 远端复习：仍是拒绝理由，但边界模糊 → 用户 override 可赢
        if entry.get("kind") == "exam_review" and strength < 0.7:
            hard = False
        course = entry.get("course") or entry.get("label") or "考试"
        if strength >= 0.9:
            line = entry.get("refuse_line") or f"那天有{course}，去不了——学习为重，考完再约好不好？"
        elif strength >= 0.7:
            line = entry.get("refuse_line") or f"这阵子在复习{course}，怕陪不了出门……临考了呢"
        else:
            line = entry.get("refuse_line") or f"我这边有点想复习{course}，不过也还早……你要是定了时间我再看看？"
        return {
            "block": True,
            "reason": "exam_or_study",
            "kind": entry.get("kind"),
            "label": entry.get("label"),
            "course": entry.get("course"),
            "exam_date": entry.get("exam_date"),
            "refuse_line": line,
            "user_override_still_wins": (not hard),
            "blocks_override": hard,
            "refuse_strength": strength,
        }

    # 考试周 / 期末 — 硬拒 override（学习为重）
    if kind in STUDY_BLOCK_KINDS or text_hit in ("期末考试", "考试周", "答辩周", "期末周", "闭关复习", "考研"):
        return {
            "block": True,
            "reason": "exam_or_study",
            "kind": kind or "exam_week",
            "refuse_line": "这周考试周/复习呢，去不了——学习为重，等考完再约好不好？",
            "user_override_still_wins": False,
            "blocks_override": True,
            "refuse_strength": 1.0,
        }
    if text_hit and user_ask_outing:
        return {
            "block": True,
            "reason": "study_text",
            "kind": kind,
            "refuse_line": "我这边在忙学业相关的事，怕陪不了出门……",
            "user_override_still_wins": False,
            "blocks_override": True,
            "refuse_strength": 0.8,
        }
    return {
        "block": False,
        "reason": None,
        "kind": kind,
        "refuse_line": None,
        "blocks_override": False,
        "refuse_strength": 0.0,
    }


def month_window(year: int, month: int) -> tuple[dt.date, dt.date]:
    start = dt.date(year, month, 1)
    if month == 12:
        end = dt.date(year, 12, 31)
    else:
        end = dt.date(year, month + 1, 1) - dt.timedelta(days=1)
    return start, end


def ensure_month_days(
    year: int,
    month: int,
    *,
    doc: dict | None = None,
    path: pathlib.Path | None = None,
    write: bool = False,
    inject_research: bool = False,
    max_inject: int = 6,
) -> dict:
    """用 life_calendar.generate_day 补齐下月缺失日（不启 daemon）。冻结时拒绝写入。"""
    import life_calendar as lc

    doc = doc if doc is not None else load_schedule(path)
    start, end = month_window(year, month)
    if is_frozen(doc, on=start) and write:
        return {
            "ok": False,
            "refused": "schedule_frozen",
            "frozen": doc.get("frozen"),
            "hint": "unfreeze first, or use user override backdoor",
        }
    by = {d["date"]: d for d in doc.get("days") or []}
    added = []
    d = start
    while d <= end:
        key = d.isoformat()
        if key not in by:
            day = lc.generate_day(d)
            by[key] = day
            added.append(key)
        d += dt.timedelta(days=1)
    doc["days"] = [by[k] for k in sorted(by)]
    # 扩展 range
    rng = doc.setdefault("range", {})
    if not rng.get("start") or start.isoformat() < rng["start"]:
        rng["start"] = start.isoformat()
    if not rng.get("end") or end.isoformat() > rng["end"]:
        rng["end"] = end.isoformat()
    if rng.get("start") and rng.get("end"):
        s = dt.date.fromisoformat(rng["start"])
        e = dt.date.fromisoformat(rng["end"])
        rng["days"] = (e - s).days + 1
    inject_res = None
    if inject_research:
        inject_res = inject_research_into_month(
            year, month, doc=doc, path=path, max_inject=max_inject, write=False
        )
        doc = inject_res.get("doc") or doc
    enr = doc.setdefault("enrichment", {})
    enr["last_month_fill"] = {
        "month": f"{year:04d}-{month:02d}",
        "added": added,
        "injected": (inject_res or {}).get("injected"),
        "at": dt.datetime.now().isoformat(timespec="seconds"),
    }
    import reasonableness
    itinerary_review = reasonableness.review(doc['days'])
    if not itinerary_review['ok']:
        return {'ok': False, 'refused': 'schedule_local_checks', 'review': itinerary_review}
    if write:
        save_schedule(doc, path)
    return {
        "ok": True,
        "month": f"{year:04d}-{month:02d}",
        "added": added,
        "injected": (inject_res or {}).get("injected"),
        "n_days": len(doc["days"]),
        "doc": doc,
    }



def note_inbound_user_message(text: str, *, cid: str | None = None) -> dict:
    """Daemon/brain receive hook: 用户微信原文 → override 后门或学业拒绝。

    不启 daemon。返回 {triggered, pending|None, refused}。
    """
    if not text or not str(text).strip():
        return {"triggered": False, "pending": None, "refused": False}
    # skip if already awaiting confirm on same raw
    cur = load_override_pending()
    if cur and cur.get("status") == "awaiting_user_confirm" and cur.get("user_raw") == str(text).strip():
        return {"triggered": True, "pending": cur, "refused": False, "dedup": True}
    intent = detect_user_override_intent(text)
    if not intent:
        return {"triggered": False, "pending": None, "refused": False}
    pending = begin_override_confirm(text, cid=cid)
    refused = pending.get("status") == "study_refused"
    return {
        "triggered": True,
        "pending": pending,
        "refused": refused,
        "wechat_ask": pending.get("wechat_ask"),
        "agents_yield": True,
    }


def load_research_idea_titles(limit: int = 24) -> list[dict]:
    p = ROOT / "data" / "collect_runs" / "life_enrich_research.json"
    if not p.exists():
        return []
    try:
        ideas = json.loads(p.read_text()).get("ideas") or []
    except Exception:
        return []
    out = []
    for it in ideas:
        title = (it.get("title") or "").strip()
        if title:
            out.append({"title": title, "platform": it.get("platform"), "topic": it.get("topic")})
        if len(out) >= limit:
            break
    return out


def inject_research_into_month(
    year: int,
    month: int,
    *,
    doc: dict | None = None,
    path: pathlib.Path | None = None,
    max_inject: int = 6,
    write: bool = False,
) -> dict:
    """只给已排好的本地外出添加灵感，不再用素材标题覆盖真实活动/吃饭/洗漱。

    旅游素材留在活动池，由日程补充 Agent 连同多日停留与往返整体安排。
    """
    import reasonableness as rz
    import activity_pool_upsert as apu
    import evening_plan as ep
    doc = doc if doc is not None else load_schedule(path)
    start, end = month_window(year, month)
    if write and is_frozen(doc, on=start):
        return {"ok": False, "refused": "schedule_frozen"}
    by = rz.activities_by_id()
    ideas = load_research_idea_titles()
    local_ideas = []
    for idea in ideas:
        aid = apu.match_existing_id(idea["title"], by)
        act = by.get(aid) or {}
        hours = (act.get("reasonableness") or {}).get("min_hours")
        if ((act.get("location") or {}).get("city") == "北京"
                and act.get("category") == "city_fun" and hours):
            local_ideas.append((idea, aid, float(hours)))
    injected = []
    for day in doc.get("days") or []:
        ds = day.get("date", "")
        if not start.isoformat() <= ds <= end.isoformat() or ep.protected(day):
            continue
        if day.get("kind") in STUDY_BLOCK_KINDS or study_priority_block(ds, day=day).get("block"):
            continue
        for s in day.get("slots") or []:
            if len(injected) >= max_inject:
                break
            # 只给已有独立往返的活动段附灵感；不改变地点、活动名、时间或来源。
            if s.get("leg") != "activity" or s.get("city") != "北京" or s.get("research_inspiration"):
                continue
            available = (ep.minutes(s["end"]) - ep.minutes(s["start"])) / 60
            candidate = next((x for x in local_ideas if x[2] <= available), None)
            if not candidate:
                continue
            idea, aid, _ = candidate
            local_ideas.remove(candidate)
            s["research_inspiration"] = {"title": idea["title"], "activity_id": aid,
                                         "usage": "待补充 Agent 核实地点与交通后规划；不是已发生经历"}
            injected.append({"date": ds, "inspiration": idea["title"], "activity_unchanged": True})
    doc.setdefault("enrichment", {})["last_research_inject"] = {
        "month": f"{year:04d}-{month:02d}", "n": len(injected), "mode": "inspiration_only",
        "at": dt.datetime.now().isoformat(timespec="seconds"),
    }
    if write:
        save_schedule(doc, path)
    return {"ok": True, "injected": len(injected), "items": injected, "doc": doc}


def fill_pool_duration_stub(ideas: list[dict] | None = None) -> dict:
    """Step2 钩子：把调研想法标成待填时长；真正时长由 annotate + Cursor Agent 写入池。

    不调用 live agent；返回待办列表供 REASONABLENESS_AGENT 批处理。
    """
    import reasonableness as rz

    pool = rz.load_pool()
    missing = []
    for a in pool.get("activities") or []:
        rz_m = a.get("reasonableness") or {}
        if not rz_m.get("typical_days"):
            missing.append(a.get("id"))
    todo = {
        "scope": "duration_only",
        "missing_typical_days": missing,
        "ideas_sample": (ideas or [])[:8],
        "invoke": "REASONABLENESS_AGENT=1 .venv/bin/python src/reasonableness.py --pool-duration --agent",
        "pool_annotate": "PYTHONPATH=src .venv/bin/python -c \"import reasonableness as r; print(r.annotate_pool_defaults())\"",
        "note": "Agent 只许改 activity_pool.reasonableness 时长字段，不评 vibe",
    }
    out = ROOT / "data" / "collect_runs" / "pool_duration_todo.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(todo, ensure_ascii=False, indent=2) + "\n")
    return todo
