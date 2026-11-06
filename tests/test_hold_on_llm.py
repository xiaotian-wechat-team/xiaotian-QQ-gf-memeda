"""hold_on：场景 hint → 短 LLM；禁止固定句库抽签。本地 dry，不发 QQ/微信。"""
import os
os.environ["XIAOTIAN_TEST"] = "1"
import random
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
import photo_scene
import chat_media


def test_llm_path_uses_scene_hint_not_lottery():
    calls = []

    def fake(messages):
        calls.append(messages)
        return "手伸好 我拍特写～"

    out = photo_scene.hold_on_line(
        rng=random.Random(0),
        shot_type="hand_closeup",
        user_text="看看手",
        complete_fn=fake,
    )
    assert out == "手伸好 我拍特写～"
    assert len(calls) == 1
    blob = calls[0][1]["content"]
    assert "手部特写" in blob
    assert "看看手" in blob
    assert "勿照抄" in blob
    # 不得退回把整库当输出
    assert out not in photo_scene.HOLD_ON_HINTS or out == "手伸好 我拍特写～"


def test_fallback_on_llm_failure_not_full_lottery():
    def boom(_messages):
        raise RuntimeError("no network")

    outs = {
        photo_scene.hold_on_line(rng=random.Random(i), complete_fn=boom)
        for i in range(20)
    }
    # 只能落在少量兜底，不能铺满旧 10 句库
    assert outs <= set(photo_scene.HOLD_ON_FALLBACKS)
    assert len(outs) >= 1
    assert "网络有点慢 你先别走" not in outs


def test_reject_tech_meta_then_fallback():
    def tech(_messages):
        return "网络有点慢 你先别走"

    out = photo_scene.hold_on_line(
        rng=random.Random(3), complete_fn=tech, user_text="来张自拍",
    )
    assert "网络" not in out
    assert out in photo_scene.HOLD_ON_FALLBACKS


def test_reject_english_clunky():
    def eng(_messages):
        return "hold on loading please wait"

    out = photo_scene.hold_on_line(rng=random.Random(4), complete_fn=eng)
    assert out in photo_scene.HOLD_ON_FALLBACKS


def test_recent_outbound_dedupe():
    n = {"i": 0}

    def always(_messages):
        n["i"] += 1
        # 第一次与最近重复；第二次换句
        return "等会儿啊 我拍一下" if n["i"] == 1 else "别急 对个光"

    out = photo_scene.hold_on_line(
        rng=random.Random(5),
        complete_fn=always,
        recent_outbound=["之前", "等会儿啊 我拍一下"],
        user_text="发张照片",
    )
    assert out == "别急 对个光"
    assert n["i"] == 2


def test_shot_from_user_text_when_type_missing():
    seen = []

    def fake(messages):
        seen.append(messages[1]["content"])
        return "对镜找角度 等下"

    out = photo_scene.hold_on_line(
        rng=random.Random(6),
        user_text="来张对镜自拍",
        complete_fn=fake,
    )
    assert out == "对镜找角度 等下"
    assert any("对镜" in s for s in seen)


def test_chat_media_wrapper_forwards_kwargs():
    def fake(_messages):
        return "马上～"

    out = chat_media.hold_on_line(
        complete_fn=fake, user_text="全身呢", shot_type="full_body",
    )
    assert out == "马上～"


def test_sanitize_strips_prefix_and_multiline():
    assert photo_scene._sanitize_hold_on('小甜：等我一下') == "等我一下"
    assert photo_scene._sanitize_hold_on("第一行\n第二行") == "第一行"
    assert photo_scene._sanitize_hold_on("网络卡了") is None


if __name__ == "__main__":
    test_llm_path_uses_scene_hint_not_lottery()
    test_fallback_on_llm_failure_not_full_lottery()
    test_reject_tech_meta_then_fallback()
    test_reject_english_clunky()
    test_recent_outbound_dedupe()
    test_shot_from_user_text_when_type_missing()
    test_chat_media_wrapper_forwards_kwargs()
    test_sanitize_strips_prefix_and_multiline()
    print("test_hold_on_llm OK")
