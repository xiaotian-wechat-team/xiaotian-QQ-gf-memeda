"""emotion.expression_cue → outfit.plan.emotion_text → imagegen.build_prompt 加权表情。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import emotion
import outfit
import imagegen


def _emo(p, a, d=0.55, intimacy=60, label="neutral"):
    return {"pad": {"pleasure": p, "arousal": a, "dominance": d},
            "intimacy": intimacy, "last_label": label, "updated": None}


def test_expression_cue_happy_energetic():
    c = emotion.expression_cue(_emo(0.9, 0.7, label="happy"))
    assert c["mood"] >= 8.0 and c["energy"] >= 7.5
    assert "嘴角自然上扬" in c["text"] and "目光清醒有神" in c["text"]
    assert "轻松愉悦" in c["text"]
    assert "忌僵硬" in c["text"]


def test_expression_cue_low_tired():
    c = emotion.expression_cue(_emo(-0.4, -0.6, label="sad"))
    assert c["mood"] < 4 and c["energy"] <= 3.5
    assert "柔软低沉" in c["text"] or "几乎不笑" in c["text"]
    assert "眼皮微沉" in c["text"] or "疲惫" in c["text"]
    assert "不勉强笑" in c["text"] or "几乎不笑" in c["text"] or "柔软低沉" in c["text"]


def test_expression_cue_coquet_baseline_default():
    c = emotion.expression_cue(None)  # default everyday PAD → calm natural face
    assert ("神情平静自然" in c["text"] or "轻微浅笑" in c["text"] or "放松柔和" in c["text"])
    assert "面无表情" in c["text"]  # anti-stiff guard always present
    c2 = emotion.expression_cue(_emo(0.5, 0.3, intimacy=85, label="coquet"))
    assert "撒娇" in c2["text"] and "亲昵" in c2["text"]


def test_outfit_plan_carries_emotion_text():
    plan = outfit.plan("图书馆看书", scene="上课/排练", emo=_emo(0.8, 0.5, label="happy"),
                       campus_life=None, chat=[], weather=None)
    assert plan.get("emotion_text") and "表情：" in plan["emotion_text"]
    assert plan["emotion"]["label"] == "happy"
    assert plan["emotion"]["mood"] >= 8.0


def test_build_prompt_includes_emotion():
    plan = outfit.plan("咖啡店", scene="约会", emo=_emo(0.85, 0.4, label="coquet"),
                       campus_life=None, chat=[], weather=None)
    p = imagegen.build_prompt("在咖啡店等他", "黑色长直发", plan["text"],
                              plan.get("realism_text", ""), emotion_text=plan["emotion_text"])
    assert "表情：" in p and "撒娇" in p
    assert "忌僵硬" in p
    assert "$imagegen" in p
    # empty emotion_text must not break
    p0 = imagegen.build_prompt("x", "d", emotion_text="")
    assert "表情：" not in p0


def test_face_sheet_identity_only_expression_follows_emotion():
    """五视图只锁长相；表情跟 expression_cue，不跟 sheet 中性脸。"""
    plan = outfit.plan("咖啡店", scene="约会", emo=_emo(0.85, 0.4, label="coquet"),
                       campus_life=None, chat=[], weather=None)
    p = imagegen.build_prompt(
        "在咖啡店等他", "黑色长直发", plan["text"], plan.get("realism_text", ""),
        n_canonical=2, body_kind="sheet", emotion_text=plan["emotion_text"],
    )
    assert "identity only" in p or "只锁长相" in p
    assert "中性" in p or "面瘫表情" in p
    assert "expression_cue" in p or "情绪单元" in p
    assert "绝不是表情参考" in p or "不是表情参考" in p
    assert "表情：" in p and "撒娇" in p
    assert plan["emotion"]["label"] == "coquet"


def test_macbook_cmd_passes_emotion_env():
    _, inner = imagegen.macbook_cmd("x", {"imagegen": {}}, "o", "", "表情：测试。")
    assert "EMOTION_TEXT=" in inner and "表情：测试。" in inner
