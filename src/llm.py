"""LLM backends: cursor-agent (long/hard) + DeepSeek chat (short/casual).

cursor-agent: --mode ask, sandbox; day-persistent session in data/llm_ws.
DeepSeek: https://api.deepseek.com chat completions; key from env / config/deepseek.env.
Router: pick_backend() → short_backend (deepseek-chat) vs long_backend (cursor_agent).
Persona static rules live in project skill xiaotian-chat (softlinked to ~/.cursor/skills)."""
import subprocess, tempfile, json, re
from config import load
import llm_session
from preview_mode import is_preview

# 长/难：走 cursor-agent；短闲聊：DeepSeek
_HARD = re.compile(
    r"(帮我分析|帮我计划|帮你分析|帮你计划|写代码|改代码|改一下代码|debug|重构|"
    r"详细分析|仔细分析|制定计划|实现一下|怎么实现|架构|方案设计|"
    r"用\s*cursor|用\s*agent|深度思考|认真想想|列个计划)"
)
_EXPLICIT_LONG = re.compile(r"(长回复|详细说说|展开讲|慢慢想|别用flash|用推理)")
_EXPLICIT_SHORT = re.compile(r"(简短回|短回|随便聊聊|用\s*flash|用\s*deepseek)")


def _base_cmd(llm, workspace=None, model=None):
    cmd = [llm["cmd"], "-p", "--mode", "ask", "--sandbox", "enabled", "--trust", "--output-format", "text"]
    if workspace is not None:
        cmd += ["--workspace", str(workspace)]
    m = model or llm.get("model")
    if m:
        cmd += ["--model", m]
    return cmd


def complete(prompt, timeout=None, reuse_session=None, model=None):
    """reuse_session: True=按天复用 chat 会话；False/None 时看 config；显式 False 则每次冷启动（供 complete_json）。

    model: optional override (e.g. grok-4.7-low-fast for weather tool loop).
    """
    llm = load()["llm"]
    use = llm.get("reuse_session", True) if reuse_session is None else reuse_session
    if is_preview():
        use = False  # Never append a preview to the real daily conversation.
    timeout = timeout or llm.get("timeout", 120)
    if not use:
        cmd = _base_cmd(llm, model=model)
        cmd.append(prompt)
        with tempfile.TemporaryDirectory(prefix="xiaotian-llm-") as empty:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=empty)
        if r.returncode != 0:
            raise RuntimeError(r.stderr[-500:])
        return r.stdout.strip()

    ws = llm_session.workspace(llm)
    sid = llm_session.ensure(llm, llm["cmd"])
    cmd = _base_cmd(llm, ws, model=model) + ["--resume", sid, prompt]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=str(ws))
    if r.returncode != 0 and llm_session.resume_broken(r.stderr or ""):
        llm_session.forget(llm)
        sid = llm_session.ensure(llm, llm["cmd"])
        cmd = _base_cmd(llm, ws, model=model) + ["--resume", sid, prompt]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=str(ws))
    elif r.returncode == 0:
        llm_session.save(llm, sid)  # bump count
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout or "llm failed")[-500:])
    return r.stdout.strip()


def complete_json(prompt, timeout=None):
    """Ask for JSON; tolerate ```json fences / surrounding text. Never reuse chat session."""
    t = complete(prompt + "\n\n只输出 JSON，不要任何解释。", timeout, reuse_session=False)
    m = re.search(r"(\[.*\]|\{.*\})", t, re.S)
    return json.loads(m.group(1)) if m else None


def _multi_question(text: str) -> bool:
    n = text.count("？") + text.count("?")
    if n >= 2:
        return True
    # 一句里多个「吗/呢」问句也偏难
    return len(re.findall(r"[吗呢]\s*[，,。！!？?]?", text)) >= 3


def _image_needs_reasoning(text: str) -> bool:
    if "[图片]" not in text and "[表情包]" not in text:
        return False
    return bool(re.search(r"(分析|什么意思|帮我看|解读|这是啥|怎么看|为什么)", text))


def pick_backend(user_text, llm=None, *, force=None):
    """Classify short/casual → short_backend; long/hard → long_backend (cursor_agent).

    Returns backend name string. Short adapters (deepseek/kimi/grok/doubao) fall back to
    long_backend when their key/adapter is unavailable.

    Weather tool-calling (weather.needs) always uses long_backend / cursor-agent —
    never DeepSeek short path for tools.
    """
    llm = llm or load().get("llm") or {}
    short_b = llm.get("short_backend") or "deepseek"
    long_b = llm.get("long_backend") or llm.get("backend") or "cursor_agent"
    thr = int(llm.get("char_threshold", 80))

    if force in ("deepseek", "cursor_agent", "kimi", "grok", "doubao", short_b, long_b):
        chosen = force
    else:
        text = (user_text or "").strip()
        # tools: weather needs → Cursor grok tool loop (not DeepSeek)
        try:
            import weather as _wx
            if _wx.needs(text):
                return long_b
        except Exception:
            pass
        if _EXPLICIT_SHORT.search(text):
            chosen = short_b
        elif (not text) or len(text) >= thr or _EXPLICIT_LONG.search(text) or _HARD.search(text) \
                or _multi_question(text) or _image_needs_reasoning(text):
            chosen = long_b
        else:
            chosen = short_b

    try:
        from llm_adapters import is_short_adapter, get_adapter
        if is_short_adapter(chosen):
            if not get_adapter(chosen).available():
                return long_b
            return chosen
    except Exception:
        pass
    # legacy deepseek path if adapters import failed
    if chosen == "deepseek":
        try:
            import llm_deepseek
            if not llm_deepseek.api_key():
                return long_b
        except Exception:
            return long_b
        return "deepseek"
    return chosen


_CALL_TOOL_RE = re.compile(
    r"CALL_TOOL\s*:\s*get_weather\s*(\{.*?\})",
    re.I | re.S,
)


def weather_tool_model(llm=None) -> str:
    llm = llm or load().get("llm") or {}
    return (
        llm.get("weather_tool_model")
        or "grok-4.7-low-fast"
    )


def complete_with_weather_tools(prompt, user_text=None, *, timeout=None, reuse_session=None,
                                evidence_sink=None):
    """One bounded weather lookup followed by one Cursor phrasing pass.

    Place routing is already deterministic (bda|yanyuan). Fetch first instead
    of spending an extra model round trip asking it to repeat that decision.

    An optional dict receives evidence only from the actual tool result. A stale
    cache, failed tool or model-only answer never counts as a current lookup.
    """
    import weather as wx
    if evidence_sink is not None:
        evidence_sink.clear()
    llm = load().get("llm") or {}
    model = weather_tool_model(llm)
    if not wx.needs(user_text):
        return complete(prompt, timeout=timeout, reuse_session=False, model=model)
    result = wx.call_tool({"place": wx.infer_place(user_text)})
    if evidence_sink is not None and isinstance(result, dict):
        usable = result.get("ok") is True and bool(str(result.get("text") or "").strip())
        why = result.get("why")
        evidence_sink.update({
            "tool": "get_weather",
            "status": "ok" if usable and why in ("ok", "cache") else
                      "stale" if usable and why == "cache-stale" else "unavailable",
            "place": result.get("place"), "text": result.get("text"), "why": why,
        })
    follow = (
        (prompt or "")
        + "\n\n【天气工具结果】"
        + json.dumps(result, ensure_ascii=False)[:800]
        + "\n据此自然回微信气泡；不要再写 CALL_TOOL；不要编造与结果冲突的气温。"
        + "ok=false 或 why=cache-stale 时说明没拿到当前天气，旧缓存不能当现在的实况。"
        + "先回应他关心的冷暖、下雨或出行；数字只挑有用的，不照读温度/体感/风力/AQI全表。"
        + "刚说过的同一地点天气，追问时自然承接，除非他明确要数字或数据变了，不重复报数。"
        + "把冷暖感受说完整：风吹着更凉、骑车迎风可能更冷；不要沿用历史里‘更吹’这类缺词病句。"
        + "同一问题再问时接住他的关心，不用‘刚说的就是’来纠正他，也不用‘也就那样’敷衍凑句。"
        + "预测的日最低温不能说成今早实测；工具数据不能冒充亲身出门的感受。"
    )
    # The supplied history is authoritative. A daily resume may retain unsent
    # drafts/tool requests, which must not look like delivered conversation.
    r2 = complete(follow, timeout=timeout, reuse_session=False, model=model)
    return re.sub(r"(?im)^\s*CALL_TOOL\s*:.*$", "", r2 or "").strip() or (r2 or "")
