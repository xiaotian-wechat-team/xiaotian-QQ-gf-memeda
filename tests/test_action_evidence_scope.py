"""Pure offline checks for source scope; titles/body excerpts are not full reads."""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
import action_evidence as ae

TITLE = {"url": "https://www.xiaohongshu.com/explore/6aabe792000000002700b353",
         "title": "三角洲不同干员跳舞", "snippet": "三角洲不同干员跳舞", "content_scope": "search_title"}
BODY = {**TITLE, "snippet": "这篇笔记里可见的介绍文字", "content_scope": "visible_note_body"}
# Default first result from the real Mac mini v4 search probe (2026-10-29).
# The visible description contains hashtags; no video or image was inspected.
VIDEO_TEXT = {"url": "https://www.xiaohongshu.com/explore/6ae1dd4e00000000280094a4",
              "title": "幸运鸟窝新赛季限时返场！",
              "snippet": "#三角洲行动 #三角洲红薯攻略站 #三角洲行动爆料 #三角洲行动攻略 #游戏实况 #游戏 #三角洲s11群星新赛季 #三角洲行动幸运鸟窝",
              "content_scope": "visible_note_body"}


def evidence(*sources, status="ok"):
    return {"status": status, "sources": list(sources)}


@pytest.mark.parametrize("claim", [
    "我看了官网", "我刚看过官网", "我已经去官网看了", "我在官方网站查了",
    "我看了那篇笔记", "我读完整篇笔记了", "我已经读完全文", "我把整篇笔记看完了",
    "我把这些笔记都看完了", "我仔细读过文章", "我看过了正文", "我也看了官网", "我看完整篇了",
])
def test_titles_do_not_authorize_official_site_or_note_reading(claim):
    assert ae.enforce(claim, evidence(TITLE)) == "我看到相关笔记标题"


@pytest.mark.parametrize("claim", ["我看了官网", "我看过官网", "我读完整篇笔记了", "我看完全文了"])
def test_visible_body_only_supports_reading_a_related_note(claim):
    assert ae.enforce(claim, evidence(BODY, TITLE)) == "我看了相关笔记"


@pytest.mark.parametrize("source", [
    {**TITLE, "snippet": "[图片]"},
    {**TITLE, "content_scope": "image"},
    {**TITLE, "content_scope": "visible_note_body", "snippet": ""},
    {**TITLE, "content_scope": "visible_note_body", "snippet": None},
    {**TITLE, "content_scope": "visible_note_body", "snippet": "[图片]"},
    {"url": TITLE["url"], "title": TITLE["title"], "snippet": "旧格式摘要"},
])
def test_image_or_missing_body_is_never_upgraded_to_a_read(source):
    assert ae.enforce("我看完整篇笔记了", evidence(source)) == "我看到相关笔记标题"


def test_official_title_is_still_only_a_search_hit():
    source = {"url": "https://df.qq.com/", "title": "三角洲行动官网", "snippet": "官方介绍"}
    assert ae.enforce("我看了官网", evidence(source)) == "我看到相关搜索结果"


def test_failure_or_weather_cannot_authorize_reading():
    weather = {"tool": "get_weather", "status": "ok", "text": "北舞12度"}
    assert ae.enforce("我看了官网", weather=weather) == "这会儿还没查到可靠结果"
    for data in (evidence(TITLE, status="unavailable"), evidence()):
        for claim in ("我看了官网", "我看完整篇笔记了"):
            assert ae.enforce(claim, data, weather=weather) == "这会儿还没查到可靠结果"
    assert ae.enforce("我查到了，北舞12度", weather=weather) == "我查到了，北舞12度"


def test_rewrite_keeps_adjacent_message_and_ordinary_reading():
    assert ae.enforce("我看了官网，是个射击游戏", evidence(TITLE)) == "我看到相关笔记标题，是个射击游戏"
    for text in ("你看了官网吗？", "我看了那篇笔记吗？", "我没看过官网", "我还没读完全文",
                 "我准备看官网", "我去官网看看", "我去官网查一下", "我想读那篇文章", "她说我看了官网", "‘我看了官网’是他的原话",
                 "我看完了电影", "我读过这本书", "我读完课文了", "我在看笔记"):
        assert ae.enforce(text, evidence(TITLE)) == text
    assert ae.enforce("我搜了", evidence(TITLE)) == "我搜了"
    assert ae.enforce("我看到相关笔记标题", evidence(TITLE)) == "我看到相关笔记标题"
    assert ae.enforce("我看了相关笔记", evidence(BODY)) == "我看了相关笔记"
    # A normal chat about class notes is not automatically a web-reading claim.
    assert ae.enforce("我看了笔记，准备复习一下") == "我看了笔记，准备复习一下"


@pytest.mark.parametrize("claim", [
    "我看了视频", "我看完视频了", "我刚刚看完了视频", "我看过那个视频", "看完了这段视频",
    "我已经把整个视频看完了", "我看了你发的视频", "我看了图集", "我把这组图集都看完了",
])
def test_real_visible_hashtags_do_not_prove_watching_media(claim):
    assert ae.enforce(claim, evidence(VIDEO_TEXT, TITLE)) == "我只看到了笔记里的文字"
    assert ae.enforce(claim, evidence(TITLE)) == "我只看到了相关笔记标题"
    for unavailable in (evidence(status="unavailable"), evidence()):
        assert ae.enforce(claim, unavailable) == "这会儿还没查到可靠结果"
    assert ae.enforce(claim) == claim  # Ordinary conversation has no search scope.


@pytest.mark.parametrize("text", [
    "你看完视频了吗？", "我看了视频吗？", "我没看视频", "我还没看完图集", "我并没有看过视频",
    "我想看视频", "我准备看图集", "我会看完视频的", "我看完视频再跟你说", "看完视频后再聊",
    "你说我看了视频", "她说‘我看完图集了’", "‘我看了视频’是你的原话",
    "你说‘我搜到了，我看完视频了’，我先看看文字", "你说“我看了视频，我也看了图集”",
    "我看了视频的文案", "我看了视频简介", "我看了图集的文字介绍",
])
def test_media_guard_preserves_quotes_negation_questions_and_future_intent(text):
    assert ae.enforce(text, evidence(VIDEO_TEXT)) == text
    assert ae.enforce(text, evidence(status="unavailable")) == text


def test_media_rewrite_preserves_adjacent_message_and_prompt_states_scope():
    import chat_search
    assert ae.enforce("我看完视频了，你最喜欢哪个角色？", evidence(VIDEO_TEXT)) == \
        "我只看到了笔记里的文字，你最喜欢哪个角色？"
    prompt = chat_search.prompt_block(evidence(VIDEO_TEXT, TITLE))
    assert "只证明读到可见文字或标签" in prompt and "不能说看了/看完视频或图集" in prompt


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
