"""imagegen 张数：先定 N；数量 prompt 独立；N>1 一次出齐语义。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import imagegen
import chat_media


def test_build_quantity_prompt_isolated():
    q1 = imagegen.build_quantity_prompt(1)
    assert "【数量指令】" in q1
    assert "本次生成 1 张" in q1
    assert "生成一张后停止" in q1
    # 不得夹带场景/妆造/构图内容词
    assert "任务核心" not in q1
    assert "外貌补充" not in q1
    assert "分张要求" not in q1
    q3 = imagegen.build_quantity_prompt(3)
    assert "本次生成 3 张" in q3
    assert "共 3 张全部生成后再停止" in q3
    assert "禁止只出一张就结束" in q3


def test_build_prompt_n1_unchanged_stop():
    p = imagegen.build_prompt("图书馆", "黑色长直发", n_images=1)
    assert "【数量指令】" in p
    assert "生成一张图片" in p or "本次生成 1 张" in p
    assert "生成一张后停止" in p
    assert "任务核心是场景与活动" in p
    # 数量块在内容块之前，且以空行分隔
    assert p.index("【数量指令】") < p.index("任务核心")


def test_build_prompt_n_multi_one_shot():
    p = imagegen.build_prompt(
        "宿舍追剧", "黑色长直发",
        n_images=3,
        shot_briefs=["对镜自拍", "第一人称手部", "全身"],
    )
    assert "本次生成 3 张" in p
    assert "共 3 张全部生成后再停止" in p
    assert "禁止只出一张就结束" in p
    assert "分张要求：" in p
    assert "第 1 张：" in p and "第 3 张：" in p
    assert "生成一张后停止" not in p
    # 数量指令不嵌在分张/场景段落里：数量块结束于内容块之前
    qty_end = p.index("\n\n")
    assert "【数量指令】" in p[:qty_end]
    assert "分张要求" not in p[:qty_end]
    assert "对镜自拍" not in p[:qty_end]


def test_build_prompt_n_clamped():
    p = imagegen.build_prompt("x", "d", n_images=99)
    assert "本次生成 5 张" in p
    assert "本次生成 6 张" not in p
    assert chat_media.MAX_CHAT_IMAGES_ABS == 5


def test_build_prompt_shared_refs_note_not_per_image():
    """N>1 只改张数文案；refs 槽位仍由 cap_refs/MAX_IMAGE_REFS 单次挂载（非每张一套）。"""
    p = imagegen.build_prompt("宿舍", "黑色长直发", n_images=3, n_canonical=2, n_scene_refs=3)
    assert "本次生成 3 张" in p
    assert "画新的 3 张" in p or "画新的" in p


def test_build_quantity_prompt_clamp_to_abs():
    q = imagegen.build_quantity_prompt(99)
    assert "本次生成 5 张" in q
    assert "本次生成 6 张" not in q


if __name__ == "__main__":
    test_build_quantity_prompt_isolated()
    test_build_prompt_n1_unchanged_stop()
    test_build_prompt_n_multi_one_shot()
    test_build_prompt_n_clamped()
    test_build_prompt_shared_refs_note_not_per_image()
    test_build_quantity_prompt_clamp_to_abs()
    print("OK")
