"""活动/日程合理度（仅时长）：审查活动需要多少天/小时，挡住不可能的塞程。

范围（ONLY）：
  - typical_days / min_stay_days / typical_days_min|max（停留是否够长/过短）
  - min_transit_days_from / 交通日（跨地跳转日历间隔是否够）
  - 可选小时级：slot 跨度 vs reasonableness.typical_hours（若有）

明确不做：氛围/质量/人设契合/好不好玩/评分 vibe。活动池 `rating` 若存在也不参与本模块打分。

闸门：duration check (reasonableness) → life_schedule → scene_refs_queue

日程仅走本地确定性校验，不启动独立 review Agent、不自动改写日程。
REASONABLENESS_AGENT 仅可用于 --pool-duration 补充活动池时长。
"""
from __future__ import annotations

import datetime as dt
import json
import os
import pathlib
import re
import shutil
import subprocess
import tempfile
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[1]
POOL_FILE = ROOT / "data" / "activity_pool.json"
REVIEW_LOG = ROOT / "data" / "collect_runs" / "reasonableness_reviews.jsonl"

# 地名 → activity_id 粗匹配用
_LOC_KEYS = (
    ("禾木", "travel_hemu"),
    ("喀纳斯", "travel_hemu"),
    ("白哈巴", "travel_hemu"),
    ("阿勒泰", "travel_hemu"),
    ("崇礼", "ski_chongli"),
    ("太舞", "ski_chongli"),
    ("富龙", "ski_chongli"),
    ("云顶", "ski_chongli"),
    ("滑雪", "ski_chongli"),
    ("三亚", "travel_sanya"),
    ("海棠湾", "travel_sanya"),
    ("亚龙湾", "travel_sanya"),
    ("蜈支洲", "travel_sanya"),
    ("东京", "travel_tokyo_winter"),
    ("日本", "travel_tokyo_winter"),
    ("涩谷", "travel_tokyo_winter"),
    ("首尔", "travel_seoul_winter"),
    ("韩国", "travel_seoul_winter"),
    ("明洞", "travel_seoul_winter"),
    ("清迈", "travel_chiangmai"),
    ("巴厘", "travel_bali"),
    ("北海道", "travel_hokkaido_ski"),
    ("札幌", "travel_hokkaido_ski"),
    ("新加坡", "travel_singapore"),
    ("大阪", "travel_osaka_winter"),
    ("京都", "travel_osaka_winter"),
    ("香港", "travel_hk_macau"),
    ("澳门", "travel_hk_macau"),
    ("台北", "travel_taipei"),
    ("台湾", "travel_taipei"),
    ("转机", "transit_day"),
    ("机场", "transit_day"),
    ("高铁", "transit_day"),
)


def load_pool() -> dict:
    if not POOL_FILE.exists():
        return {"version": 0, "activities": [], "gate": {}}
    return json.loads(POOL_FILE.read_text())


def save_pool(doc: dict) -> None:
    doc["updated"] = dt.date.today().isoformat()
    POOL_FILE.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n")


def activities_by_id() -> dict[str, dict]:
    return {a["id"]: a for a in (load_pool().get("activities") or [])}


def match_activity(text: str) -> dict | None:
    """从活动文案粗匹配池条目（最长别名优先）。"""
    if not text:
        return None
    by = activities_by_id()
    # 先按池内 aliases 长度降序
    cands: list[tuple[int, dict]] = []
    for a in by.values():
        names = [a.get("name") or ""] + list(a.get("aliases") or [])
        for n in names:
            if n and n in text:
                cands.append((len(n), a))
    if cands:
        cands.sort(key=lambda x: -x[0])
        return cands[0][1]
    for key, aid in _LOC_KEYS:
        if key in text and aid in by:
            return by[aid]
    return None


def activity_rating(act: dict | None) -> float:
    if not act:
        return 0.0
    r = act.get("rating") or {}
    if isinstance(r, (int, float)):
        return float(r)
    return float(r.get("base") or r.get("score") or 0.0)


def passes_rating(act: dict | None, min_rating: float | None = None) -> bool:
    """活动库可选元数据门槛；不参与 reasonableness 时长分。"""
    gate = (load_pool().get("gate") or {})
    thr = min_rating if min_rating is not None else float(gate.get("min_rating") or 0.6)
    return activity_rating(act) >= thr


def _day_primary_activity(day: dict) -> str:
    # Prefer explicit activity_id binding (seeded travel arcs)
    aid = day.get("activity_id")
    if aid:
        act = activities_by_id().get(aid)
        if act:
            return act.get("name") or aid
        return aid
    for s in day.get("slots") or []:
        if s.get("bucket") in ("sleep",):
            continue
        act = s.get("activity") or ""
        if any(k in act for k in ("睡觉", "赖床", "吃晚饭", "回宿舍", "刚起床", "食堂", "早午饭", "夜宵", "准备睡")):
            continue
        if s.get("bucket") in ("travel", "outdoor_outing", "city_fun") or match_activity(act):
            return act
    # fallback: first non-sleep
    for s in day.get("slots") or []:
        if s.get("bucket") != "sleep":
            return s.get("activity") or ""
    return ""


def _region_of(act: dict | None, text: str = "") -> str:
    if act:
        loc = act.get("location") or {}
        return loc.get("city") or loc.get("region") or act.get("id") or ""
    for key, _ in _LOC_KEYS:
        if key in (text or ""):
            return key
    return "北京" if any(k in (text or "") for k in ("上课", "排练", "图书馆", "探店", "citywalk", "剧本杀")) else ""


def _day_active_hours(day: dict) -> float:
    """非 sleep slot 的小时跨度之和（仅时长）。"""
    total = 0.0
    for s in day.get("slots") or []:
        if s.get("bucket") == "sleep":
            continue
        try:
            a, b = s.get("start") or "00:00", s.get("end") or "00:00"
            ah, am = map(int, a.split(":")[:2])
            bh, bm = map(int, b.split(":")[:2])
            end_m = (24 * 60 if b == "24:00" else bh * 60 + bm)
            total += max(0, end_m - (ah * 60 + am)) / 60.0
        except Exception:
            continue
    return total


def _check_hours_issues(days: list[dict], by: dict) -> list[dict]:
    """若活动声明 min_hours/typical_hours，检查单日是否严重不足（只审时长）。"""
    issues = []
    for d in days:
        aid = d.get("activity_id")
        act = by.get(aid) if aid else None
        if not act:
            text = _day_primary_activity(d)
            act = match_activity(text)
        if not act:
            continue
        rz = act.get("reasonableness") or {}
        min_h = rz.get("min_hours")
        typ_h = rz.get("typical_hours")
        if not min_h and not typ_h:
            continue
        hrs = _day_active_hours(d)
        need = float(min_h or typ_h)
        if hrs + 0.01 < need * 0.5:  # 少于声明时长的一半才报错（避免误杀）
            issues.append({
                "code": "hours_too_short",
                "severity": "error",
                "segment": act.get("name") or aid,
                "msg": f"{d.get('date')} {act.get('name')} 当日活动约 {hrs:.1f}h，声明至少 ~{need}h",
                "hours": hrs, "min_hours": need,
            })
    return issues


def segment_trips(days: list[dict]) -> list[dict]:
    """把连续日程压成目的地段：{activity_id, name, start, end, n_days, region}。"""
    segs: list[dict] = []
    cur: dict | None = None
    by = activities_by_id()
    for d in sorted(days, key=lambda x: x.get("date") or ""):
        text = _day_primary_activity(d)
        if d.get("activity_id") and d["activity_id"] in by:
            act = by[d["activity_id"]]
            aid = d["activity_id"]
        else:
            act = match_activity(text)
            aid = (act or {}).get("id") or ("local_" + (d.get("kind") or "day"))
        # transit 单独段
        region = _region_of(act, text)
        if cur and cur["activity_id"] == aid and aid != "transit_day":
            cur["end"] = d["date"]
            cur["n_days"] += 1
            cur["texts"].append(text)
        else:
            if cur:
                segs.append(cur)
            cur = {
                "activity_id": aid,
                "name": (act or {}).get("name") or text or aid,
                "start": d["date"],
                "end": d["date"],
                "n_days": 1,
                "region": region,
                "texts": [text],
                "pool": act,
            }
    if cur:
        segs.append(cur)
    return segs



def check_persona_realism(days: list[dict]) -> list[dict]:
    """拟真密度复查（非时长）：平日娱乐过密 / 缺自习或实习可被审出。

    依据 docs/life-schedule-design.md。severity 多为 warn；极端过密用 error。
    不替代 duration_fit_only 主分，但并入 issues 供 Agent/本地看见。
    """
    import datetime as _dt
    FUN = {"city_fun", "outdoor_outing"}  # campus_fun 轻社团不计娱乐过密
    STUDY = {"school_class", "school_library", "school_rehearsal"}
    issues: list[dict] = []
    # sliding window of workdays
    workdays = []
    for d in sorted(days, key=lambda x: x.get("date") or ""):
        try:
            date = _dt.date.fromisoformat(str(d.get("date"))[:10])
        except Exception:
            continue
        kind = d.get("kind") or ""
        if kind in ("weekend", "holiday", "winter_break", "summer_break") or date.weekday() >= 5:
            continue
        if kind in ("exam_week", "thesis_defense", "finals", "exam_review"):
            continue
        buckets = [(s.get("bucket") or "") for s in (d.get("slots") or []) if s.get("bucket") != "sleep"]
        fun_n = sum(1 for b in buckets if b in FUN)
        study_n = sum(1 for b in buckets if b in STUDY)
        intern_n = sum(1 for b in buckets if b == "work_internship")
        quiet_n = sum(1 for b in buckets if b in ("weekend_home", "campus_life") and True)
        workdays.append({
            "date": date.isoformat(), "fun_n": fun_n, "study_n": study_n,
            "intern_n": intern_n, "buckets": buckets,
        })
        if fun_n >= 2:
            issues.append({
                "code": "weekday_fun_too_dense_day",
                "severity": "warn",
                "segment": date.isoformat(),
                "msg": f"{date} 工作日娱乐槽≥2（拟真：平日晚娱乐应稀疏）",
            })
    if not workdays:
        return issues
    # week-ish: any 5 consecutive workdays with fun every day
    fun_flags = [1 if w["fun_n"] >= 1 else 0 for w in workdays]
    for i in range(0, max(0, len(fun_flags) - 4)):
        window = fun_flags[i:i+5]
        if sum(window) >= 5:
            issues.append({
                "code": "weekday_fun_too_dense_week",
                "severity": "error",
                "segment": workdays[i]["date"],
                "msg": f"自 {workdays[i]['date']} 起连续5个工作日每天都有娱乐槽（拟真失败）",
            })
            break
        if sum(window) >= 4:
            issues.append({
                "code": "weekday_fun_dense_warn",
                "severity": "warn",
                "segment": workdays[i]["date"],
                "msg": f"自 {workdays[i]['date']} 起5个工作日中有{sum(window)}天娱乐（偏密）",
            })
    # month-ish shortage of study / internship
    n = len(workdays)
    study_days = sum(1 for w in workdays if w["study_n"] >= 1)
    intern_days = sum(1 for w in workdays if w["intern_n"] >= 1)
    fun_days = sum(1 for w in workdays if w["fun_n"] >= 1)
    if n >= 10 and study_days / n < 0.7:
        issues.append({
            "code": "weekday_missing_study",
            "severity": "warn",
            "segment": "workdays",
            "msg": f"工作日含课/自习/排练的比例 {study_days}/{n} < 70%（拟真：学业骨架不足）",
        })
    if n >= 15 and intern_days / n < 0.35:
        issues.append({
            "code": "weekday_missing_internship",
            "severity": "warn",
            "segment": "workdays",
            "msg": f"{n} 个工作日仅 {intern_days} 天含电影公司实习（目标约每周 2～4 天，占比≳35%）",
        })
    if n >= 10 and fun_days / n > 0.55:
        issues.append({
            "code": "weekday_fun_ratio_high",
            "severity": "error" if fun_days / n > 0.75 else "warn",
            "segment": "workdays",
            "msg": f"工作日娱乐日占比 {fun_days}/{n}={fun_days/n:.0%}（目标约≤35%）",
        })
    return issues


def score_schedule(days: list[dict], *, min_rating: float | None = None) -> dict:
    """本地时长审查。返回 {ok, score 0-1 duration_fit_only, issues[], segments[]}。

    min_rating 保留兼容，不参与时长分数（勿用于 vibe）。
    """
    issues: list[dict] = []
    segs = segment_trips(days)
    by = activities_by_id()
    _ = min_rating  # API compat; intentionally unused for duration-only scoring

    # 1) 每段停留天数 vs typical / min_stay（只审时长，不审 vibe/rating）
    for seg in segs:
        act = seg.get("pool") or by.get(seg["activity_id"])
        if not act:
            continue
        rz = act.get("reasonableness") or {}
        if act.get("category") in ("travel_winter", "travel_abroad", "travel"):
            n = seg["n_days"]
            mn = int(rz.get("min_stay_days") or rz.get("typical_days_min") or 1)
            typ = int(rz.get("typical_days") or mn)
            mx = int(rz.get("typical_days_max") or typ)
            if n < mn:
                issues.append({
                    "code": "stay_too_short",
                    "severity": "error",
                    "segment": seg["name"],
                    "msg": f"{seg['name']} 仅 {n} 天，最少建议 {mn} 天（典型 ~{typ}）",
                    "n_days": n, "min_stay_days": mn, "typical_days": typ,
                })
            elif n < typ - 1:
                issues.append({
                    "code": "stay_short_warn",
                    "severity": "warn",
                    "segment": seg["name"],
                    "msg": f"{seg['name']} {n} 天偏短（典型 ~{typ}，范围 {rz.get('typical_days_min')}-{mx}）",
                })
            elif n > mx + 2:
                issues.append({
                    "code": "stay_too_long",
                    "severity": "warn",
                    "segment": seg["name"],
                    "msg": f"{seg['name']} {n} 天偏长（典型上限 ~{mx}）",
                })

    # 2) 段间跳转：地理不可能 / 缺转机日
    def _parse(d: str) -> dt.date:
        return dt.date.fromisoformat(d)

    for i in range(len(segs) - 1):
        a, b = segs[i], segs[i + 1]
        if a["activity_id"] == b["activity_id"]:
            continue
        if a["activity_id"] == "transit_day" or b["activity_id"] == "transit_day":
            continue
        act_b = b.get("pool") or by.get(b["activity_id"])
        act_a = a.get("pool") or by.get(a["activity_id"])
        if not act_b or not act_a:
            continue
        if act_a.get("category") not in ("travel_winter", "travel_abroad", "travel") and \
           act_b.get("category") not in ("travel_winter", "travel_abroad", "travel"):
            continue
        gap = (_parse(b["start"]) - _parse(a["end"])).days  # 0 = 次日衔接
        # 需要的最少转机天数（按出发地方键匹配）
        need_map = (act_b.get("reasonableness") or {}).get("min_transit_days_from") or {}
        keys_a = [
            (act_a.get("location") or {}).get("city") or "",
            (act_a.get("location") or {}).get("region") or "",
            act_a.get("name") or "",
            *(act_a.get("aliases") or []),
        ]
        need = 0
        for k, v in need_map.items():
            if any(k and k in x for x in keys_a):
                need = max(need, int(v))
        # 跨远距离且同日/次日无缓冲
        far = need >= 1 or (
            (act_a.get("location") or {}).get("region")
            != (act_b.get("location") or {}).get("region")
            and act_a.get("category") in ("travel_winter", "travel_abroad")
            and act_b.get("category") in ("travel_winter", "travel_abroad")
        )
        # gap=0 同日；gap=1 次日抵达。需要 need 天交通时，要求 gap >= need+1
        # （中间至少插入 need 个日历日，通常由 transit_day 段承担）
        need_gap = (need + 1) if need >= 1 else 0
        if far and need_gap and gap < need_gap:
            issues.append({
                "code": "impossible_jump",
                "severity": "error",
                "segment": f"{a['name']} → {b['name']}",
                "msg": (
                    f"{a['end']} 仍在{a['name']}，{b['start']} 就到{b['name']}："
                    f"跨地至少需要 {need} 天交通/缓冲"
                    f"（日历间隔 gap={gap}，要求 ≥{need_gap}）"
                ),
                "from": a["activity_id"], "to": b["activity_id"], "gap_days": gap,
            })

    issues.extend(check_slot_safety(days, by))
    import schedule_travel_review
    issues.extend(schedule_travel_review.review(days)["issues"])
    issues.extend(_check_hours_issues(days, by))
    issues.extend(check_persona_realism(days))

    errors = [x for x in issues if x.get("severity") == "error"]
    warns = [x for x in issues if x.get("severity") == "warn"]
    # duration score only: 1.0 无时长问题；error/warn 扣分。ok = 无 error
    score = max(0.0, 1.0 - 0.25 * len(errors) - 0.08 * len(warns))
    ok = len(errors) == 0
    return {
        "ok": ok,
        "score": round(score, 3),
        "score_meaning": "duration_fit_only",  # not vibe/quality/persona
        "issues": issues,
        "segments": [
            {k: v for k, v in s.items() if k != "pool"}
            for s in segs
        ],
        "engine": "local",
    }


def check_slot_safety(days: list[dict], by: dict | None = None) -> list[dict]:
    """逐槽防止长途素材短槽化，并校验本地往返闭环；不调用模型。"""
    import evening_plan as ep
    by = by if by is not None else activities_by_id()
    issues = []
    for day in days:
        def error(code, msg):
            issues.append(dict(code=code, severity="error", date=day.get("date"), msg=msg))
        outings = {}
        previous_end = 0
        slots = day.get("slots") or []
        for s in slots:
            try:
                a, b = ep.minutes(s["start"]), ep.minutes(s["end"])
                if not 0 <= a < b <= 1440:
                    raise ValueError("invalid interval")
            except (KeyError, ValueError, TypeError):
                error("invalid_slot_time", f"无效时间段：{s.get('activity')}")
                continue
            if day.get("evening_plan") and a != previous_end:
                error("timeline_gap_or_overlap", f"{s['start']} 前存在时间缺口或重叠")
            previous_end = b
            act = by.get(s.get("activity_id")) or match_activity(s.get("activity", ""))
            if act and act.get("category") in ("travel", "travel_winter", "travel_abroad"):
                if not (day.get("activity_id") in (act.get("id"), "transit_day")
                        or s.get("trip_id") or day.get("trip_id")):
                    error("unplanned_long_distance_slot", f"{s['activity']} 缺完整多日行程，不能直接塞入本地空档")
            if s.get("outing_id"):
                outings.setdefault(s["outing_id"], []).append(s)
        if day.get("evening_plan") and previous_end != 1440:
            error("timeline_incomplete", "日程未覆盖到午夜")
        for outing_id, legs in outings.items():
            if ([s.get("leg") for s in legs] != ["outbound", "activity", "return"]
                    or any(s.get("city") != "北京" for s in legs)
                    or any(a["end"] != b["start"] for a, b in zip(legs, legs[1:]))):
                error("local_outing_incomplete", f"{outing_id} 缺连贯的本地去程、活动、返程")
    return issues


DEFAULT_AGENT_MODEL = "grok-4.7-high"
# Agent helpers below are only for activity-pool duration enrichment.


def _truthy(name: str, default: str = "0") -> bool:
    return os.environ.get(name, default) in ("1", "true", "TRUE", "YES", "yes")


def _agent_bin() -> str | None:
    """Prefer Mac mini local Cursor CLI; fall back to ~/.grok agent."""
    override = os.environ.get("REASONABLENESS_AGENT_BIN", "").strip()
    if override and pathlib.Path(override).exists():
        return override
    home = pathlib.Path.home()
    candidates: list[pathlib.Path] = []
    for name in ("cursor-agent", "agent", "grok"):
        which = shutil.which(name)
        if which:
            candidates.append(pathlib.Path(which))
    candidates.extend([
        home / ".local" / "bin" / "cursor-agent",
        home / ".cursor" / "bin" / "cursor-agent",
        home / ".grok" / "bin" / "agent",
        home / ".grok" / "bin" / "grok",
    ])
    # Prefer cursor-agent over grok `agent` when both exist
    cursor_first = [p for p in candidates if "cursor-agent" in p.name]
    rest = [p for p in candidates if "cursor-agent" not in p.name]
    for p in cursor_first + rest:
        if p.exists():
            return str(p.resolve() if p.is_symlink() else p)
    return None


def _agent_flavor(bin_path: str) -> str:
    name = pathlib.Path(bin_path).name
    if "cursor-agent" in name or "cursor-agent" in bin_path:
        return "cursor-agent"
    return "grok-agent"


def _agent_model(flavor: str) -> str:
    env = os.environ.get("REASONABLENESS_MODEL", "").strip()
    if env:
        return env
    if flavor == "cursor-agent":
        return DEFAULT_AGENT_MODEL  # grok-4.7-high
    return "grok-4.7"


def _build_agent_cmd(bin_path: str, prompt: str, *, allow_write: bool) -> list[str]:
    flavor = _agent_flavor(bin_path)
    model = _agent_model(flavor)
    if flavor == "cursor-agent":
        # Used only to supplement duration fields in activity_pool.json.
        cmd = [
            bin_path,
            "-p",
            "--trust",
            "--output-format", "text",
            "--model", model,
            "--workspace", str(ROOT),
        ]
        if allow_write:
            cmd.append("--force")
        cmd.append(prompt)
        return cmd
    # ~/.grok/bin/agent (Grok Build TUI CLI)
    cmd = [
        bin_path,
        "--single", prompt,
        "--model", model,
        "--reasoning-effort", "high",
        "--cwd", str(ROOT),
        "--output-format", "plain",
    ]
    if allow_write:
        cmd.append("--always-approve")
    return cmd


def review(days: list[dict], *, use_agent: bool | None = None) -> dict:
    """本地确定性校验；兼容旧 use_agent 参数，但永不启动日程 review Agent。"""
    local = score_schedule(days)
    local["agent_hook"] = {"invoked": False, "removed": True, "owner": "local-rules"}
    return local


def may_enter_schedule(
    days: list[dict] | dict,
    *,
    use_agent: bool | None = None,
    log: bool = True,
) -> tuple[bool, dict]:
    """闸门：仅当时长审查 ok 时允许写入 life_schedule（不验 vibe/rating）。

    days 可以是 day 列表，或单日 dict。
    """
    if isinstance(days, dict) and "slots" in days:
        days = [days]
    # 单日：至少匹配到的旅行活动要通过 rating
    res = review(list(days), use_agent=use_agent)
    if log:
        try:
            REVIEW_LOG.parent.mkdir(parents=True, exist_ok=True)
            with REVIEW_LOG.open("a") as f:
                f.write(json.dumps({
                    "ts": dt.datetime.now().isoformat(timespec="seconds"),
                    "ok": res.get("ok"),
                    "score": res.get("score"),
                    "issues": res.get("issues"),
                    "engine": res.get("engine"),
                    "n_days": len(days),
                }, ensure_ascii=False) + "\n")
        except Exception:
            pass
    return bool(res.get("ok")), res


def annotate_pool_defaults() -> dict:
    """确保池内每条都有 reasonableness + rating；返回统计。"""
    doc = load_pool()
    n = 0
    for a in doc.get("activities") or []:
        rz = a.setdefault("reasonableness", {})
        rz.setdefault("typical_days", 1)
        rz.setdefault("typical_days_min", 1)
        rz.setdefault("typical_days_max", max(1, int(rz["typical_days"])))
        rz.setdefault("min_stay_days", int(rz.get("typical_days_min") or 1))
        a.setdefault("rating", {"base": 0.7, "source": "default"})
        n += 1
    save_pool(doc)
    return {"activities": n, "path": str(POOL_FILE)}



def fill_pool_durations_local(ids: list[str] | None = None) -> dict:
    """Heuristic duration fill for activities missing typical_days / flagged needs_duration_review.

    Does not call Cursor. Safe offline defaults by category.
    """
    doc = load_pool()
    cat_defaults = {
        "travel_winter": (5, 3, 7),
        "travel_abroad": (6, 4, 9),
        "city_fun": (1, 1, 1),
        "study": (1, 1, 1),
        "school": (1, 1, 1),
        "transit": (1, 1, 1),
        "outdoor_outing": (1, 1, 2),
    }
    want = set(ids) if ids else None
    filled = []
    for a in doc.get("activities") or []:
        if want is not None and a.get("id") not in want:
            continue
        rz = a.setdefault("reasonableness", {})
        need = rz.get("needs_duration_review") or not rz.get("typical_days")
        if not need:
            continue
        typ, mn, mx = cat_defaults.get(a.get("category") or "", (2, 1, 4))
        rz.setdefault("typical_days", typ)
        rz.setdefault("typical_days_min", mn)
        rz.setdefault("typical_days_max", mx)
        rz.setdefault("min_stay_days", mn)
        rz["needs_duration_review"] = False
        rz["duration_filled_by"] = "local_heuristic"
        filled.append(a["id"])
    if filled:
        save_pool(doc)
    return {"ok": True, "engine": "local_heuristic", "filled": filled, "n": len(filled)}


def fill_pool_durations_with_agent(
    ids: list[str] | None = None,
    *,
    allow_write: bool | None = None,
    timeout: int | None = None,
) -> dict:
    """Duration-only Cursor pass that edits activity_pool.json reasonableness fields ONLY.

    Only this pool-enrichment entry may invoke REASONABLENESS_AGENT / cursor-agent.
    Schedule review is local only. Never invents vibe scores.
    """
    import activity_pool_upsert as apu

    doc = load_pool()
    need = ids or apu.activities_needing_duration(doc)
    bin_path = _agent_bin()
    flavor = _agent_flavor(bin_path) if bin_path else None
    model = _agent_model(flavor) if flavor else DEFAULT_AGENT_MODEL
    if allow_write is None:
        allow_write = _truthy("REASONABLENESS_REWRITE", "1")
    if timeout is None:
        timeout = int(os.environ.get("REASONABLENESS_AGENT_TIMEOUT", "300"))
    hook = {
        "available": bool(bin_path),
        "bin": bin_path,
        "flavor": flavor,
        "model": model,
        "env_flag": "REASONABLENESS_AGENT=1",
        "target": str(POOL_FILE),
        "scope": "activity_pool.reasonableness duration fields only",
        "owner": "mac-mini-local-cursor-4.7-high",
        "invoked": False,
        "ids": need,
    }
    if not need:
        return {"ok": True, "engine": "noop", "filled": [], "agent_hook": hook}
    if not bin_path or not _truthy("REASONABLENESS_AGENT", "0"):
        # offline fallback
        local = fill_pool_durations_local(need)
        local["agent_hook"] = hook
        local["note"] = "REASONABLENESS_AGENT not set; used local heuristic"
        return local

    # freeze/override yield does NOT block pool duration (pool ≠ schedule rewrite)
    slim = []
    by = {a["id"]: a for a in doc.get("activities") or []}
    for aid in need:
        a = by.get(aid) or {}
        slim.append({
            "id": aid,
            "name": a.get("name"),
            "category": a.get("category"),
            "aliases": (a.get("aliases") or [])[:6],
            "reasonableness": a.get("reasonableness") or {},
            "research_note": ((a.get("reasonableness") or {}).get("research_note") or "")[:200],
        })
    rewrite = (
        f"Only edit {POOL_FILE} activities[].reasonableness duration fields: "
        "typical_days, typical_days_min, typical_days_max, min_stay_days, "
        "typical_hours, min_hours, min_transit_days_from (if relevant). "
        "Set needs_duration_review=false after fill. "
        "Do NOT edit life_schedule, rating, vibe, name, or scene_match. "
        "Do NOT add destinations for fun. Output JSON "
        "{ok:bool, filled:[id...], summary:str, rewrote:bool}."
        if allow_write else
        "Do not write files; propose durations in JSON only."
    )
    prompt = {
        "task": "pool_duration_fill",
        "owner": "Mac mini local Cursor agent grok-4.7-high (pool only, NOT schedule rewrite)",
        "instruction": (
            "你是活动池时长 Agent。只评估/填写每个活动需要多少天或小时。"
            "禁止评价好不好玩、人设契合。参考：崇礼滑雪~4天、禾木~7天、三亚~7–14天、"
            "城市娱乐半日~1天/3–5小时、出国短途~5–8天。"
            + rewrite
        ),
        "activities": slim,
    }
    with tempfile.TemporaryDirectory() as td:
        inp = pathlib.Path(td) / "pool_in.json"
        inp.write_text(json.dumps(prompt, ensure_ascii=False, indent=2))
        user_prompt = (
            f"Read {inp} (JSON task). Follow instruction exactly. "
            f"If writing, only touch {POOL_FILE} reasonableness duration fields. "
            "Reply with ONLY the JSON object (no markdown fences)."
        )
        cmd = _build_agent_cmd(bin_path, user_prompt, allow_write=bool(allow_write))
        hook["cmd_preview"] = cmd[:8] + ["…"]
        try:
            hook["invoked"] = True
            mtime_before = POOL_FILE.stat().st_mtime if POOL_FILE.exists() else None
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout, cwd=str(ROOT)
            )
            out = (proc.stdout or "") + "\n" + (proc.stderr or "")
            m = re.search(r"\{[\s\S]*\}", out)
            agent_res = json.loads(m.group(0)) if m else {}
            rewrote = bool(agent_res.get("rewrote"))
            if mtime_before is not None and POOL_FILE.exists() and POOL_FILE.stat().st_mtime > mtime_before:
                rewrote = True
            # clear flags on touched ids if agent rewrote; else local fill remaining
            still = activities_needing_duration() if "activities_needing_duration" in dir() else []
            try:
                import activity_pool_upsert as apu
                still = apu.activities_needing_duration()
            except Exception:
                still = []
            local_fill = []
            if still and allow_write:
                local_fill = fill_pool_durations_local(still).get("filled") or []
            return {
                "ok": bool(agent_res.get("ok", True)),
                "engine": "cursor-agent-pool-duration" if flavor == "cursor-agent" else "agent-pool-duration",
                "filled": list(agent_res.get("filled") or []) + list(local_fill),
                "rewrote": rewrote,
                "summary": agent_res.get("summary"),
                "agent_hook": hook,
                "local_fallback": local_fill,
            }
        except Exception as e:
            hook["error"] = str(e)[:300]
            local = fill_pool_durations_local(need)
            local["agent_hook"] = hook
            local["engine"] = "local_after_agent_fail"
            return local


def activities_needing_duration() -> list[str]:
    try:
        import activity_pool_upsert as apu
        return apu.activities_needing_duration()
    except Exception:
        doc = load_pool()
        out = []
        for a in doc.get("activities") or []:
            rz = a.get("reasonableness") or {}
            if rz.get("needs_duration_review") or not rz.get("typical_days"):
                out.append(a["id"])
        return out


if __name__ == "__main__":
    import argparse
    import sys

    ap = argparse.ArgumentParser(description="Local schedule checks; optional activity-pool duration enrichment")
    ap.add_argument("--schedule", default=str(ROOT / "data" / "life_schedule_2026q4.json"))
    ap.add_argument("--winter-from", default="2027-01-19")
    ap.add_argument("--agent", action="store_true", help="enable Cursor only with --pool-duration; schedule checks remain local")
    ap.add_argument("--no-rewrite", action="store_true", help="legacy compatibility flag; schedule checks never rewrite")
    ap.add_argument("--all-days", action="store_true", help="review full schedule, not only winter window")
    ap.add_argument("--pool-duration", action="store_true",
                    help="duration-only Cursor/local pass on activity_pool.json (not schedule)")
    ap.add_argument("--pool-ids", nargs="*", help="limit --pool-duration to these activity ids")
    args = ap.parse_args()
    if args.agent:
        os.environ["REASONABLENESS_AGENT"] = "1"
    if args.no_rewrite:
        os.environ["REASONABLENESS_REWRITE"] = "0"

    if args.pool_duration:
        r = fill_pool_durations_with_agent(args.pool_ids or None)
        print(json.dumps(r, ensure_ascii=False, indent=2))
        sys.exit(0 if r.get("ok") else 1)

    sched = pathlib.Path(args.schedule)
    doc = json.loads(sched.read_text())
    days = doc.get("days") or []
    winter = [d for d in days if d.get("date", "") >= args.winter_from]
    focus = days if args.all_days else (winter or days[-14:])
    r = review(focus)
    print(json.dumps(r, ensure_ascii=False, indent=2))
    sys.exit(0 if r.get("ok") else 1)
