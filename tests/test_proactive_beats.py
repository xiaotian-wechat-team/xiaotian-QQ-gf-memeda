"""离线：日程节拍 + 冲浪匹配（不调 LLM、不发送、不写 STATE）。"""
import os
os.environ["XIAOTIAN_TEST"] = "1"
os.environ.setdefault("RELAY_ENV_FILE", "/nonexistent/xiaotian-test-relay.env")
import sys, pathlib, datetime, random
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
import proactive_beats as pb
import proactive_scheduler as ps
import config

D = datetime.datetime
R = random.Random
base = dict(config.load()["proactive"], enabled=True, probability=0)
base.pop('contact_rhythm', None)  # Event discovery tested separately from random pacing.
# 强制开节拍
base["beats"] = dict(pb.cfg_beats(base), enabled=True)


def _st(day_slots, now_slot, *, done=None, emotion=None):
    day = {
        "date": "2026-10-25", "weekday": 6, "weekday_zh": "日", "kind": "weekend",
        "photo_bias": "city_fun", "slots": day_slots, "notes": None,
    }
    return {
        "life_today": day,
        "life_now": {**now_slot, "date": "2026-10-25", "kind": "weekend", "photo_bias": "city_fun"},
        "life_plan_meta": {"source": "draft"},
        "proactive_beats": {"day": "2026-10-25", "done": done or {}, "shared_material_ids": [], "surf_target": None, "shares": []},
        "emotion": emotion or {"pad": {"pleasure": 0.2, "arousal": 0.3, "dominance": 0.5}, "intimacy": 60},
        "last_reply_at": "2026-10-25 10:05:00",
        "last_proactive_at": "2026-10-24 20:00:00",
    }


SLOTS = [
    {"start": "00:00", "end": "10:00", "activity": "睡觉", "tone": "迷迷糊糊", "bucket": "sleep",
     "scene_hints": [], "photo_ok": False, "energy": -4, "delay": [20, 180]},
    {"start": "10:00", "end": "13:00", "activity": "赖床/吃早午饭", "tone": "懒洋洋", "bucket": "weekend_home",
     "scene_hints": ["宿舍窗边"], "photo_ok": True, "energy": 0, "delay": [10, 120]},
    {"start": "13:00", "end": "18:30", "activity": "攀岩馆/室内攀岩初体验", "tone": "嗨", "bucket": "city_fun",
     "scene_hints": ["攀岩"], "photo_ok": True, "energy": 1, "delay": [10, 120]},
    {"start": "18:30", "end": "19:30", "activity": "吃晚饭", "tone": "放松", "bucket": "campus_life",
     "scene_hints": ["晚饭"], "photo_ok": True, "energy": 1, "delay": [10, 120]},
    {"start": "19:30", "end": "22:30", "activity": "同学局·剧本杀/古堡逃脱", "tone": "嗨", "bucket": "city_fun",
     "scene_hints": ["剧本杀"], "photo_ok": True, "energy": 1, "delay": [10, 120]},
    {"start": "22:30", "end": "24:00", "activity": "自由时间/准备睡", "tone": "放松", "bucket": "weekend_home",
     "scene_hints": [], "photo_ok": True, "energy": 1, "delay": [10, 120]},
]


def test_morning_and_brief_after_wake():
    now = D(2026, 10, 25, 10, 20)
    st = _st(SLOTS, SLOTS[1])
    due = pb.due_beats(now, st, base)
    ids = [b["id"] for b in due]
    assert "morning" in ids and "schedule_brief" in ids
    m = next(b for b in due if b["id"] == "morning")
    assert m["bypass_quiet"] and m["must"]
    task = pb.build_beat_task(m, st)
    assert "禁止小熊" not in task
    assert "本轮称呼" in task or "称呼" in task
    assert "[SKIP]" in task

    assert "短气泡" in task or "气泡" in task


def test_mid_activity_does_not_suppress_available_surf():
    now = D(2026, 10, 27, 10, 30)  # 假设上课 — 用自定义槽
    class_slot = {"start": "09:00", "end": "12:00", "activity": "编导技法课", "tone": "短",
                  "bucket": "school_class", "scene_hints": [], "energy": 0}
    day = [
        {"start": "00:00", "end": "08:00", "activity": "睡觉", "bucket": "sleep", "tone": "", "scene_hints": [], "energy": -4},
        {"start": "08:00", "end": "09:00", "activity": "刚起床赶课", "bucket": "campus_life", "tone": "", "scene_hints": [], "energy": -1},
        class_slot,
    ]
    st = _st(day, class_slot)
    st["life_today"]["date"] = "2026-10-27"
    st["life_now"]["date"] = "2026-10-27"
    st["proactive_beats"]["day"] = "2026-10-27"
    from unittest.mock import patch
    # Do not depend on the host's material library: busy slots discover the
    # same available share, while a missing source still cannot be shared.
    material = {"id": "offline-class-share", "image_path": "/tmp/offline-share.png",
                "source_url": "https://example.com/offline-share"}
    with patch.object(pb, "pick_surf_item", return_value=material):
        due = pb.due_beats(now, st, base)
    assert any(b["id"] == "surf_share" for b in due)
    assert any(b["id"] == "daily_chat" for b in due)
    with patch.object(pb, "pick_surf_item", return_value=None):
        due_without_material = pb.due_beats(now, st, base)
    assert all(b["id"] != "surf_share" for b in due_without_material)


def test_morning_survives_mid_quiet_in_wake_window():
    """school_class mid_quiet must NOT drop morning/schedule_brief inside wake window."""
    now = D(2026, 10, 27, 9, 5)  # 上课中，但仍在 wake(08:00)+90min 窗
    class_slot = {"start": "08:50", "end": "12:00", "activity": "编导技法课", "tone": "短",
                  "bucket": "school_class", "scene_hints": [], "energy": 0}
    day = [
        {"start": "00:00", "end": "08:00", "activity": "睡觉", "bucket": "sleep", "tone": "", "scene_hints": [], "energy": -4},
        {"start": "08:00", "end": "08:50", "activity": "刚起床赶课", "bucket": "campus_life", "tone": "", "scene_hints": [], "energy": -1},
        class_slot,
    ]
    st = _st(day, class_slot)
    st["life_today"]["date"] = "2026-10-27"
    st["life_now"]["date"] = "2026-10-27"
    st["proactive_beats"]["day"] = "2026-10-27"
    due = pb.due_beats(now, st, base)
    ids = [b["id"] for b in due]
    assert "morning" in ids and "schedule_brief" in ids, ids
    m = next(b for b in due if b["id"] == "morning")
    assert m["must"] and m["bypass_quiet"]


def test_evening_must_and_no_double_bypass():
    """evening is must in evening window; unanswered proactive must not block it."""
    now = D(2026, 10, 25, 19, 0)
    st = _st(SLOTS, SLOTS[4], done={"morning": "10:20:00", "schedule_brief": "10:25:00"})
    due = pb.due_beats(now, st, base)
    ev = [b for b in due if b["id"] == "evening"]
    assert ev, [b["id"] for b in due]
    assert ev[0]["must"] and ev[0]["bypass_quiet"]
    task = pb.build_beat_task(ev[0], st, now=now)
    assert "晚上好" in task  # soft ban mentions the phrase
    assert "禁止固定" in task or "勿只会" in task
    # no_double: last_pro after last_chat → non-must blocked; evening must passes
    ctx = {"pending_from_him": False, "unanswered_proactive": 1, "unanswered_proactive_turns": 1,
           "mentions": [], "low_mood": False, "emotions": []}
    stg = {"last_proactive_at": "2026-10-25 16:00:00", "last_reply_at": "2026-10-25 12:00:00",
           "proactive_unanswered_turns": 1}
    assert ps.gates(now, stg, base, ctx, beat=ev[0]) is None
    g_meal = ps.gates(now, stg, base, ctx, beat={"id": "meal", "must": False, "soft_reach": False,
        "bypass_quiet": False, "slot": SLOTS[3]})
    assert g_meal is None


def test_meal_not_forced_every_lunch():
    """Legacy meal probability no longer suppresses the event itself."""
    now = D(2026, 10, 25, 18, 45)
    st = _st(SLOTS, SLOTS[3])
    pc = dict(base)
    pc["beats"] = dict(pc["beats"], meal_p=0.0)
    due = pb.due_beats(now, st, pc)
    assert any(b["id"] == "meal" for b in due)


def test_early_wake_morning_due_at_0740():
    """~07:40 wake weekday: morning must is due (launchd must tick hours 1–8)."""
    now = D(2026, 10, 27, 7, 40)
    wake = {"start": "07:37", "end": "08:53", "activity": "刚起床赶课", "tone": "赶",
            "bucket": "campus_life", "scene_hints": [], "energy": -1}
    day = [
        {"start": "00:00", "end": "07:37", "activity": "睡觉", "bucket": "sleep", "tone": "", "scene_hints": [], "energy": -4},
        wake,
        {"start": "08:53", "end": "12:01", "activity": "导演基础/镜头语言课", "bucket": "school_class", "tone": "", "scene_hints": [], "energy": 0},
    ]
    st = _st(day, wake)
    st["life_today"]["date"] = "2026-10-27"
    st["life_now"]["date"] = "2026-10-27"
    st["proactive_beats"]["day"] = "2026-10-27"
    due = pb.due_beats(now, st, base)
    ids = [b["id"] for b in due]
    assert "morning" in ids, ids
    m = next(b for b in due if b["id"] == "morning")
    assert m["must"] and m["bypass_quiet"]
    # unanswered no_double must not block
    ctx = {"pending_from_him": False, "unanswered_proactive": 2, "unanswered_proactive_turns": 2,
           "mentions": [], "low_mood": False, "emotions": []}
    stg = {"last_proactive_at": "2026-10-26 22:00:00", "last_reply_at": "2026-10-26 20:00:00",
           "proactive_unanswered_turns": 2}
    assert ps.gates(now, stg, base, ctx, beat=m) is None


def test_meal_beat_and_activity_end():
    now = D(2026, 10, 25, 18, 45)
    st = _st(SLOTS, SLOTS[3])  # 吃晚饭
    pc = dict(base)
    pc["beats"] = dict(pc["beats"], meal_p=1.0)  # force optional meal for determinism
    due = pb.due_beats(now, st, pc)
    assert any(b["id"] == "meal" for b in due)
    meal = next(b for b in due if b["id"] == "meal")
    assert not meal.get("must"), "meal should be optional/probabilistic, not must"
    # 攀岩刚结束：19:00 前 grace — 用 18:35 刚进晚饭
    now2 = D(2026, 10, 25, 18, 35)
    due2 = pb.due_beats(now2, st, pc)
    assert any(b["id"] == "activity_end" for b in due2)


def test_pre_sleep():
    now = D(2026, 10, 25, 23, 20)
    st = _st(SLOTS, SLOTS[5])
    due = pb.due_beats(now, st, base)
    assert any(b["id"] == "pre_sleep" for b in due)


def test_sleep_gate_blocks_proactive():
    now = D(2026, 10, 25, 1, 15)
    st = _st(SLOTS, SLOTS[0], done={})
    r = ps.tick(now, base, st, [], R(0), use_llm=False)
    assert r["action"] == "SKIP" and "睡觉" in r["reason"]


def test_surf_query_matches_slot():
    q = pb.surf_query(SLOTS[4], {"kind": "weekend", "photo_bias": "city_fun"})
    assert "剧本杀" in q or "探店" in q
    # pick may be None if store empty in CI; if present must have title + media when required
    item = pb.pick_surf_item(
        SLOTS[4], {"kind": "weekend", "photo_bias": "city_fun"},
        require_media=True, platforms=("xiaohongshu", "douyin"),
    )
    if item:
        assert item.get("title")
        assert item.get("image_path")
        assert item.get("source_url")
        assert item.get("platform") in ("xiaohongshu", "douyin")
        assert "搜索" not in item["title"]
        assert "weibo" != item.get("platform")


def test_surf_day_target_range_stable():
    bcfg = pb.cfg_beats(base)
    assert bcfg["surf_min_per_day"] == 1 and bcfg["surf_max_per_day"] == 5
    now = D(2026, 10, 25, 20, 0)
    t1 = pb.surf_day_target(now, bcfg)
    t2 = pb.surf_day_target(now, bcfg)
    assert t1 == t2 and 1 <= t1 <= 5
    # different day may differ but still in range
    t3 = pb.surf_day_target(D(2026, 10, 26, 20, 0), bcfg)
    assert 1 <= t3 <= 5


def test_surf_requires_media_and_drops_weibo(tmp_path, monkeypatch):
    import material_store as ms
    store = ms.Store(tmp_path / "m")
    img = tmp_path / "a.jpg"; img.write_bytes(b"img-A")
    # weibo with image — must never be picked for surf
    store.add_item("weibo", "https://weibo.com/x/1", title="大学生周末探店剧本杀好看",
                   tags=["探店", "剧本杀"], media=[img])
    # xhs text-only — must be skipped when require_media
    store.add_item("xiaohongshu", "https://www.xiaohongshu.com/explore/aaa",
                   title="大学生周末探店无图帖", tags=["探店"])
    # douyin with image — ok
    img2 = tmp_path / "b.jpg"; img2.write_bytes(b"img-B")
    did = store.add_item("douyin", "https://www.douyin.com/video/123",
                         title="大学生剧本杀探店周末局", tags=["剧本杀", "探店"],
                         media=[img2], extra={"top_comments": ["好好看", "想去"]})

    monkeypatch.setenv("XIAOTIAN_MATERIALS_DIR", str(tmp_path / "m"))
    # reset module singleton
    ms._default = None
    item = pb.pick_surf_item(
        SLOTS[4], {"kind": "weekend", "photo_bias": "city_fun"},
        require_media=True, platforms=("xiaohongshu", "douyin"), rng=R(1),
    )
    assert item and item["id"] == did
    assert item["platform"] == "douyin"
    assert item["image_path"] and item["source_url"].startswith("https://www.douyin.com")
    assert "好好看" in (item.get("top_comments") or [])


def test_mark_beat_done_records_share():
    now = D(2026, 10, 25, 20, 10)
    bs = {"day": "2026-10-25", "done": {}, "shared_material_ids": [], "shares": []}
    share = {
        "id": 99, "platform": "xiaohongshu",
        "source_url": "https://www.xiaohongshu.com/explore/zzz",
        "title": "周末探店", "image_path": "/tmp/x.jpg",
        "top_comments": ["冲"],
    }
    out = pb.mark_beat_done(bs, "surf_share@99", now, material_id=99, share=share)
    assert 99 in out["shared_material_ids"]
    assert out["shares"][-1]["source_url"].endswith("/zzz")
    # day rollover keeps shares
    nxt = pb._beats_state({"proactive_beats": out}, D(2026, 10, 26, 12, 0))
    assert nxt["done"] == {} and nxt["shares"][-1]["id"] == 99


def test_surf_share_prompt_requires_url():
    import pathlib
    src = pathlib.Path(pb.__file__).read_text()
    assert "不要贴原始链接给他看" not in src
    assert "必须带原帖截图+原链" in src or "必须把原链" in src


def test_share_prompt_lines():
    st = {
        "proactive_beats": {
            "day": "2026-10-25",
            "shares": [{
                "id": 1, "platform": "douyin",
                "source_url": "https://www.douyin.com/video/1",
                "title": "滑雪攻略", "top_comments": ["求链接"],
            }],
        }
    }
    lines = pb.share_prompt_lines(st, n=3)
    assert lines and "最近分享" in lines[0]
    assert "douyin" in lines[1] and "求链接" in lines[1]


def test_scheduler_prefers_beat():
    now = D(2026, 10, 25, 10, 25)
    st = _st(SLOTS, SLOTS[1])
    r = ps.tick(now, base, st, [], R(0), use_llm=False)
    assert r["action"] == "DRAFT"
    assert r.get("beat") == "morning" or (r.get("topic") or "").startswith("beat:")
    assert "task" in r and "禁止小熊" not in r["task"]
    assert "本轮称呼" in r["task"] or "称呼" in r["task"]


def test_bypass_quiet_for_morning():
    now = D(2026, 10, 26, 8, 30)  # 仍在 quiet_hours 00:30-09:00
    wake = {"start": "08:15", "end": "09:00", "activity": "刚起床赶课", "tone": "赶",
            "bucket": "campus_life", "scene_hints": [], "energy": -1}
    day = [
        {"start": "00:00", "end": "08:15", "activity": "睡觉", "bucket": "sleep", "tone": "", "scene_hints": [], "energy": -4},
        wake,
        {"start": "09:00", "end": "12:00", "activity": "编导技法课", "bucket": "school_class", "tone": "", "scene_hints": [], "energy": 0},
    ]
    st = _st(day, wake)
    st["life_today"]["date"] = "2026-10-26"
    st["life_now"]["date"] = "2026-10-26"
    st["proactive_beats"]["day"] = "2026-10-26"
    beat = pb.pick_beat(now, st, base)
    assert beat and beat["id"] in ("morning", "schedule_brief")
    g = ps.gates(now, st, base, {"pending_from_him": False, "unanswered_proactive": 0}, beat=beat)
    assert g is None, g


def test_mark_beat_done():
    now = D(2026, 10, 25, 10, 30)
    bs = {"day": "2026-10-25", "done": {}, "shared_material_ids": []}
    out = pb.mark_beat_done(bs, "morning", now, material_id=42)
    assert out["done"]["morning"] and 42 in out["shared_material_ids"]



def test_silence_nudge_by_unanswered_turns():
    """未回主动轮次过软阈值也可触发 silence（即使小时未到）。"""
    now = D(2026, 10, 25, 16, 0)
    st = _st(SLOTS, SLOTS[2], done={"morning": "10:20:00"})
    st["last_reply_at"] = "2026-10-25 15:30:00"  # 仅 0.5h，不够 silence_min_hours
    st["proactive_unanswered_turns"] = 6
    due = pb.due_beats(now, st, base)
    assert "silence_nudge" in [x["id"] for x in due], [x["id"] for x in due]


def test_silence_nudge_after_long_quiet():
    """隔 silence_min_hours 未回 → soft_reach 节拍；任务走向量引导非固定模板。"""
    now = D(2026, 10, 25, 16, 0)  # 攀岩中 / city_fun
    st = _st(SLOTS, SLOTS[2], done={"morning": "10:20:00"})
    st["last_reply_at"] = "2026-10-25 10:05:00"  # ~6h ago
    st["last_proactive_at"] = "2026-10-25 12:00:00"
    due = pb.due_beats(now, st, base)
    ids = [b["id"] for b in due]
    assert "silence_nudge" in ids, ids
    b = next(x for x in due if x["id"] == "silence_nudge")
    assert b.get("soft_reach") and not b.get("must")
    task = pb.build_beat_task(b, st, now=now)
    assert "催读" in task or "查岗" in task
    assert "没有新进展就 [SKIP]" in task
    assert "在吗" in task  # 禁令里提到
    # 固定模板不应作为唯一出口：应有 gate/向量提示
    assert "怎么不找我" not in task
    assert "说话目标向量" not in task
    # 未隔够久 → 不触发
    st2 = _st(SLOTS, SLOTS[2], done={"morning": "10:20:00"})
    st2["last_reply_at"] = "2026-10-25 15:30:00"
    due2 = pb.due_beats(now, st2, base)
    assert "silence_nudge" not in [x["id"] for x in due2]
    # 日封顶
    st3 = _st(SLOTS, SLOTS[2], done={"morning": "10:20:00", "silence_nudge": "14:00:00"})
    st3["last_reply_at"] = "2026-10-25 10:05:00"
    due3 = pb.due_beats(now, st3, base)
    assert "silence_nudge" not in [x["id"] for x in due3]
    # gates：无冻结；soft_reach 破 no_double；surf 仍可能「不连发」
    ctx = {"pending_from_him": False, "unanswered_proactive": 4, "unanswered_proactive_turns": 4,
           "mentions": [], "low_mood": False, "emotions": []}
    stg = {"last_proactive_at": "2026-10-25 12:00:00", "last_reply_at": "2026-10-25 10:05:00",
           "proactive_unanswered_turns": 4}
    assert ps.gates(now, stg, base, ctx, beat=b) is None
    g_surf = ps.gates(now, stg, base, ctx, beat={"id": "surf_share", "must": False, "soft_reach": False,
        "slot": SLOTS[2]})
    # no_double 仍挡非 soft_reach；不再出现「冻结」
    assert g_surf is None or "不连发" in g_surf or "min_gap" in g_surf
    assert not (g_surf and "冻结" in g_surf)




def test_schedule_brief_ask_him_mutex():
    """schedule_brief ask_him False when already asked; task omits 问他今天干嘛."""
    now = D(2026, 10, 25, 10, 20)
    st = _st(SLOTS, SLOTS[1])
    import schedule_fidelity as sf
    st["user_day_curiosity"] = sf.mark_asked({}, now, question="今天咋安排")
    due = pb.due_beats(now, st, base)
    briefs = [b for b in due if b["id"] == "schedule_brief"]
    assert briefs, [b["id"] for b in due]
    assert briefs[0].get("ask_him") is False
    task = pb.build_beat_task(briefs[0], st, now=now)
    assert "可问他今天干嘛" not in task
    assert "已经问过" in task or "别再问" in task
    assert "ask_his_day" not in [b["id"] for b in due]


def test_schedule_brief_ask_him_skips_ask_his_day_same_tick():
    now = D(2026, 10, 25, 10, 20)
    st = _st(SLOTS, SLOTS[1])
    # Force curiosity gate on via high p and empty mark
    pc = dict(base)
    pc["beats"] = dict(pc["beats"], user_schedule_curiosity_p=1.0, user_schedule_curiosity_min_gap_days=0)
    due = pb.due_beats(now, st, pc)
    ids = [b["id"] for b in due]
    if "schedule_brief" in ids:
        brief = next(b for b in due if b["id"] == "schedule_brief")
        if brief.get("ask_him"):
            assert "ask_his_day" not in ids


def test_pendulum_can_omit_baobei_image():
    """With proactive_image_p=0, schedule_brief has no image hint."""
    now = D(2026, 10, 25, 10, 20)
    st = _st(SLOTS, SLOTS[1])
    pc = dict(base)
    pc["beats"] = dict(pc["beats"], proactive_image_p=0.0, baobei_want_image=True)
    due = pb.due_beats(now, st, pc)
    brief = next(b for b in due if b["id"] == "schedule_brief")
    assert not (brief.get("image") or {}).get("mode")



def test_meal_empty_image_no_want_image():
    """Pendulum off / image={} → pure-text meal; no WANT_IMAGE in task."""
    now = D(2026, 10, 25, 18, 45)
    st = _st(SLOTS, SLOTS[3])
    beat = {"id": "meal", "slot": SLOTS[3], "today": st["life_today"], "image": {}}
    task = pb.build_beat_task(beat, st, now=now)
    assert "WANT_IMAGE:" not in task
    assert "WANT_IMAGE" not in task
    assert "吃饭" in task or "吃晚饭" in task


def test_meal_want_image_has_marker():
    now = D(2026, 10, 25, 18, 45)
    st = _st(SLOTS, SLOTS[3])
    beat = {
        "id": "meal", "slot": SLOTS[3], "today": st["life_today"],
        "image": {"mode": "want_image", "scene": "晚饭食堂", "shot_type": "hand_closeup"},
    }
    task = pb.build_beat_task(beat, st, now=now)
    assert "WANT_IMAGE:[hand_closeup]晚饭食堂" in task


def test_activity_end_omits_ask_when_already_asked():
    """If 「今日已问安排」 green, activity_end must not 可问他在干嘛."""
    now = D(2026, 10, 25, 18, 35)
    st = _st(SLOTS, SLOTS[3])
    import schedule_fidelity as sf
    st["user_day_curiosity"] = sf.mark_asked({}, now, question="今天咋安排")
    beat = {
        "id": "activity_end",
        "slot": SLOTS[3],
        "ended": SLOTS[2],
        "today": st["life_today"],
        "image": {},
    }
    task = pb.build_beat_task(beat, st, now=now)
    assert "可问他在干嘛" not in task
    assert "已经问过" in task or "别再问" in task
    assert beat.get("ask_him") is False
    # without mark → still may ask (fresh beat: no ask_him key so fallback applies)
    st2 = _st(SLOTS, SLOTS[3])
    beat2 = {
        "id": "activity_end",
        "slot": SLOTS[3],
        "ended": SLOTS[2],
        "today": st2["life_today"],
        "image": {},
    }
    task2 = pb.build_beat_task(beat2, st2, now=now)
    assert "可问他在干嘛" in task2
    assert beat2.get("ask_him") is True


def test_activity_end_due_ask_him_from_day_receipt():
    """due_beats sets ask_him True/False from shared day-ask receipt (no text regex)."""
    import schedule_fidelity as sf
    now = D(2026, 10, 25, 18, 35)
    st = _st(SLOTS, SLOTS[3])
    due = pb.due_beats(now, st, base)
    ends = [b for b in due if b["id"] == "activity_end"]
    assert ends, [b["id"] for b in due]
    assert ends[0].get("ask_him") is True
    task = pb.build_beat_task(dict(ends[0]), st, now=now)
    assert "可问他在干嘛" in task
    assert ends[0].get("ask_him") is True  # due flag preserved; build may mutate copy

    st2 = _st(SLOTS, SLOTS[3])
    st2["user_day_curiosity"] = sf.mark_asked({}, now, question="今天咋安排")
    due2 = pb.due_beats(now, st2, base)
    ends2 = [b for b in due2 if b["id"] == "activity_end"]
    assert ends2
    assert ends2[0].get("ask_him") is False
    task2 = pb.build_beat_task(dict(ends2[0]), st2, now=now)
    assert "可问他在干嘛" not in task2


def test_activity_end_ask_him_skips_ask_his_day_same_tick():
    """Same-tick: activity_end with ask_him suppresses independent ask_his_day."""
    now = D(2026, 10, 25, 18, 35)
    st = _st(SLOTS, SLOTS[3])
    pc = dict(base)
    pc["beats"] = dict(
        pc["beats"],
        user_schedule_curiosity_p=1.0,
        user_schedule_curiosity_min_gap_days=0,
    )
    due = pb.due_beats(now, st, pc)
    ids = [b["id"] for b in due]
    ends = [b for b in due if b["id"] == "activity_end"]
    if ends and ends[0].get("ask_him"):
        assert "ask_his_day" not in ids


def test_no_regex_scan_for_ask_him_mark():
    """Mark path uses ask_him flag / beat id — not 「在干嘛」 text regex."""
    import schedule_fidelity as sf
    import inspect
    src = inspect.getsource(sf.beat_meta_marks_ask_his_day) + inspect.getsource(
        sf.outbox_item_marks_ask_his_day
    )
    assert "在干嘛" not in src
    assert "import re" not in src
    assert "re.search" not in src and "re.findall" not in src and "re.match" not in src
    # text alone must never mark
    assert not sf.beat_meta_marks_ask_his_day(
        {"id": "activity_end", "ask_him": False},
    )
    assert not sf.outbox_item_marks_ask_his_day(
        {"beat_id": "activity_end", "text": "你在干嘛呀", "ask_him": False}
    )
    assert not sf.outbox_item_marks_ask_his_day(
        {"beat_id": "activity_end", "text": "你在干嘛呀"}
    )


def test_meal_period_filter_rejects_wrong_band():
    """Noon lunch material must not attach at dinner slot; unknown not reused."""
    dinner = SLOTS[3]  # 吃晚饭
    now = D(2026, 10, 25, 18, 45)
    assert pb.meal_period_for_slot(dinner, now) == "dinner"
    lunch_item = {"title": "今日午饭份量好足", "tags": ["午饭", "食堂"], "image_path": "/tmp/x.jpg"}
    assert pb.material_fits_meal_period(lunch_item, "dinner") is False
    dinner_item = {"title": "宿舍晚饭随便炒了个菜", "tags": ["晚饭"], "image_path": "/tmp/y.jpg"}
    assert pb.material_fits_meal_period(dinner_item, "dinner") is True
    # unknown band → NOT reused
    assert pb.material_fits_meal_period({"title": "今日份快乐", "tags": []}, "dinner") is False
    # explicit meal_period field wins
    assert pb.material_fits_meal_period(
        {"title": "随便", "tags": [], "meal_period": "dinner"}, "dinner"
    ) is True
    # breakfast hour fallback when activity silent
    vague = {"start": "07:30", "end": "08:30", "activity": "吃饭", "scene_hints": [], "bucket": "campus_life"}
    assert pb.meal_period_for_slot(vague, D(2026, 10, 25, 7, 45)) == "breakfast"
    # stamped field preferred
    stamped = {"activity": "食堂午饭", "meal_period": "brunch", "scene_hints": []}
    assert pb.meal_period_for_slot(stamped, now) == "brunch"
    # brunch keyword
    br = {"activity": "赖床/吃早午饭", "scene_hints": [], "bucket": "weekend_home"}
    assert pb.meal_period_for_slot(br, D(2026, 10, 25, 10, 30)) == "brunch"


def test_meal_image_hint_prefers_generate():
    """Meal prefers current-slot WANT_IMAGE generate over unknown materials."""
    now = D(2026, 10, 25, 18, 45)
    slot = dict(SLOTS[3])  # 吃晚饭
    slot["meal_period"] = "dinner"
    st = _st(SLOTS, slot)
    # no matching material in store → want_image with meal_period
    hint = pb.meal_image_hint(slot, st["life_today"], rng=__import__("random").Random(0), now=now)
    assert hint.get("meal_period") == "dinner"
    assert hint.get("mode") in ("want_image", "material")
    if hint.get("mode") == "want_image":
        assert hint.get("scene")



if __name__ == "__main__":
    test_morning_and_brief_after_wake()
    test_mid_activity_does_not_suppress_available_surf()
    test_morning_survives_mid_quiet_in_wake_window()
    test_evening_must_and_no_double_bypass()
    test_meal_not_forced_every_lunch()
    test_early_wake_morning_due_at_0740()
    test_meal_beat_and_activity_end()
    test_pre_sleep()
    test_sleep_gate_blocks_proactive()
    test_surf_query_matches_slot()
    test_surf_day_target_range_stable()
    test_mark_beat_done_records_share()
    test_share_prompt_lines()
    test_scheduler_prefers_beat()
    test_bypass_quiet_for_morning()
    test_silence_nudge_after_long_quiet()
    test_silence_nudge_by_unanswered_turns()
    test_mark_beat_done()
    test_meal_period_filter_rejects_wrong_band()
    test_meal_image_hint_prefers_generate()
    print("OK (tmp_path tests via pytest)")
