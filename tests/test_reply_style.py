"""reply_style: 啧轮换去重 + 软 phatic cue（非硬白名单）。"""
import sys
import pathlib
import random

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
import reply_style as rs
import brain
import emotion
import llm_deepseek


def test_particle_rotate_avoids_recent_ze():
    rng = random.Random(1)
    recent = ["啧", "啧"]
    picks = {rs.pick_particle(recent, rng) for _ in range(40)}
    assert "啧" not in picks or picks - {"啧"}
    # with recent 啧, should strongly prefer non-啧
    non = [rs.pick_particle(recent, random.Random(i), avoid="啧") for i in range(30)]
    assert sum(1 for p in non if p != "啧") >= 25


def test_diversify_opener_when_ze_recent():
    hist = [("them", "想爱我"), ("me", "啧"), ("them", "又来")]
    out = rs.diversify_opener("啧\n脸皮挺厚", hist, random.Random(2))
    assert out.splitlines()[0] != "啧"
    assert "脸皮挺厚" in out
    # no recent 啧 → unchanged
    assert rs.diversify_opener("啧\n脸皮挺厚", [("me", "切")], random.Random(0)) == "啧\n脸皮挺厚"


def test_phatic_soft_not_hard_whitelist():
    assert rs.looks_phatic_soft("啊哈哈哈") == "phatic"
    assert rs.looks_phatic_soft("好呢") == "phatic"
    assert rs.looks_phatic_soft("嗯嗯") == "phatic"
    assert rs.looks_phatic_soft("哦吼") == "phatic"
    # content-attached laugh → OK to tease, not don't-chase
    assert rs.looks_phatic_soft("想摸摸行吗哈哈哈") == "content_laugh"
    assert rs.looks_phatic_soft("我也不会哈哈哈，你想学单板还是双板") == "content_laugh"
    assert rs.looks_phatic_soft("在干嘛") is None
    assert rs.looks_phatic_soft("想你了") is None
    tip = rs.phatic_prompt_tip("好呢")
    assert "别当真" in tip or "别追" in tip
    assert "轻损" in rs.phatic_prompt_tip("想摸摸行吗哈哈哈")


def test_prompt_injects_soft_cues():
    llm_deepseek._SKILL_CACHE = None
    import datetime as dt

    class H(tuple):
        def __new__(cls, s, t):
            return tuple.__new__(cls, (s, t))

    now = dt.datetime(2026, 10, 26, 21, 40)
    st = {"emotion": emotion.default()}
    hist = [H("me", "啧"), H("them", "啊哈哈哈")]
    p = brain.build_prompt("demo_contact", hist, nick=None, st=st, now=now)
    assert "语气" in p
    assert "别当真" in p or "空笑" in p
    assert "勿总起啧" in p or "轮换" in p
    msgs = brain.build_deepseek_messages("demo_contact", hist, nick=None, st=st, now=now)
    blob = msgs[0]["content"] + msgs[1]["content"]
    assert "别当真" in blob or "空笑" in blob or "别追问" in blob


def test_appraise_phatic_not_happy():
    emo, lab = emotion.appraise(emotion.default(), "啊哈哈哈")
    assert lab == "neutral"
    emo2, lab2 = emotion.appraise(emotion.default(), "今天太开心了哈哈")
    assert lab2 == "happy"


def test_skill_mentions_rotate_and_phatic():
    root = pathlib.Path(__file__).resolve().parents[1]
    ds = (root / ".cursor/skills/xiaotian-chat/SKILL.deepseek.md").read_text(encoding="utf-8")
    assert "勿总起啧" in ds
    assert "空笑" in ds or "语气收尾" in ds
    skill = (root / ".cursor/skills/xiaotian-chat/SKILL.md").read_text(encoding="utf-8")
    body = skill.split("---", 2)[-1].strip()
    assert 500 <= len(body) <= 1500


if __name__ == "__main__":
    test_particle_rotate_avoids_recent_ze()
    test_diversify_opener_when_ze_recent()
    test_phatic_soft_not_hard_whitelist()
    test_prompt_injects_soft_cues()
    test_appraise_phatic_not_happy()
    test_skill_mentions_rotate_and_phatic()
    print("test_reply_style OK")
