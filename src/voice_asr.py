"""Inbound voice/record → short Chinese transcript before brain.

Prefer NapCat/QQ native STT (`fetch_ptt_text` → translatePtt2Text) when a
OneBot message_id is available, so mlx-whisper need not load. Fallback:
local mlx-whisper (SILK via weixin_ilink.voice / pilk; other formats via
ffmpeg). Magpie transcriptions/chat have no audio path. Soft-fail → bare
[语音]. Cache by file sha256 (mlx path). Do NOT call api.kimi.com.
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import re
import shutil
import subprocess
import tempfile
import threading
import time
from typing import Any, Optional

from config import ROOT, load

DEFAULT_MODEL = "mlx-community/whisper-small-mlx"
_TAG_RE = re.compile(r"\[语音(?:\s*\d+秒)?\]")
_FFMPEG = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"
_INLINE_KEYS = ("text", "transcript", "translate", "ptt_text", "asr_text")


def _cfg() -> dict:
    c = load()
    vc = dict(c.get("voice_asr") or {})
    vc.setdefault("enabled", True)
    vc.setdefault("prefer_qq_stt", True)
    vc.setdefault("qq_stt_action", "fetch_ptt_text")
    vc.setdefault("qq_stt_timeout", 12)
    vc.setdefault("backend", "mlx_whisper")
    vc.setdefault("model", DEFAULT_MODEL)
    vc.setdefault("language", "zh")
    vc.setdefault("timeout", 90)
    vc.setdefault("max_chars", 200)
    vc.setdefault("cache_dir", "data/asr_cache")
    vc.setdefault("ffmpeg", _FFMPEG)
    # OneBot WS for QQ STT (persona.channels.qq)
    qq = ((c.get("persona") or {}).get("channels") or {}).get("qq") or {}
    vc.setdefault("qq_ws_uri", qq.get("ws_uri") or "ws://127.0.0.1:3001")
    vc.setdefault("qq_ws_token", qq.get("ws_token") or "")
    return vc


def _cache_dir(cfg: dict | None = None) -> pathlib.Path:
    cfg = cfg or _cfg()
    d = pathlib.Path(cfg.get("cache_dir") or "data/asr_cache")
    if not d.is_absolute():
        d = ROOT / d
    d.mkdir(parents=True, exist_ok=True)
    return d


def file_hash(path: str | pathlib.Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _cache_path(sha: str, cfg: dict | None = None) -> pathlib.Path:
    return _cache_dir(cfg) / f"{sha}.json"


def cache_get(sha: str, cfg: dict | None = None) -> Optional[str]:
    p = _cache_path(sha, cfg)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    t = (data.get("text") or "").strip()
    return t or None


def cache_put(sha: str, text: str, *, meta: dict | None = None, cfg: dict | None = None) -> None:
    cfg = cfg or _cfg()
    payload = {
        "text": (text or "").strip(),
        "model": cfg.get("model") or DEFAULT_MODEL,
        "backend": cfg.get("backend") or "mlx_whisper",
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        **(meta or {}),
    }
    _cache_path(sha, cfg).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _looks_like_silk(path: pathlib.Path) -> bool:
    if path.suffix.lower() in (".silk", ".slk"):
        return True
    try:
        head = path.read_bytes()[:10]
    except OSError:
        return False
    return head.startswith(b"\x02#!SILK_V3") or head.startswith(b"#!SILK_V3")


def to_wav(path: str | pathlib.Path, *, cfg: dict | None = None) -> Optional[pathlib.Path]:
    """Best-effort convert local audio → 16 kHz mono wav. Returns wav path (may be temp)."""
    cfg = cfg or _cfg()
    p = pathlib.Path(path).expanduser()
    if not p.is_file():
        return None
    suf = p.suffix.lower()
    if suf == ".wav":
        return p
    tmp_dir = pathlib.Path(tempfile.mkdtemp(prefix="xt_asr_"))
    out = tmp_dir / (p.stem + ".wav")
    try:
        if _looks_like_silk(p) or suf in (".silk", ".slk"):
            from weixin_ilink.voice import silk_to_wav

            # pilk expects 24 kHz for WeChat silk; whisper is fine with that rate
            silk_to_wav(p, out, sample_rate=24000)
            return out if out.is_file() else None
        ff = cfg.get("ffmpeg") or _FFMPEG
        if not ff or not pathlib.Path(ff).is_file():
            return None
        subprocess.run(
            [ff, "-y", "-i", str(p), "-ac", "1", "-ar", "16000", str(out)],
            check=True,
            capture_output=True,
            timeout=float(cfg.get("timeout") or 90),
        )
        return out if out.is_file() else None
    except Exception:
        try:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        except Exception:
            pass
        return None


def _clip(text: str, cfg: dict) -> str:
    text = re.sub(r"\s+", " ", (text or "")).strip()
    text = text.strip("「」\"'“”")
    max_c = int(cfg.get("max_chars") or 200)
    if len(text) > max_c:
        text = text[: max_c - 1].rstrip() + "…"
    return text


def extract_inline_qq_transcript(raw: dict | None) -> Optional[str]:
    """Pull already-present QQ/NapCat transcript from OneBot raw / media meta."""
    if not isinstance(raw, dict):
        return None
    media = raw.get("_xiaotian_media") or {}
    if isinstance(media, dict):
        t = (media.get("voice_transcript") or media.get("qq_stt_text") or "").strip()
        if t:
            return t
    message = raw.get("message")
    if isinstance(message, list):
        for seg in message:
            if not isinstance(seg, dict):
                continue
            if seg.get("type") not in ("record", "voice"):
                continue
            data = seg.get("data") or {}
            if not isinstance(data, dict):
                continue
            for k in _INLINE_KEYS:
                t = (data.get(k) or "").strip() if isinstance(data.get(k), str) else ""
                if t:
                    return t
    return None


def qq_stt_fetch(
    message_id: str | int,
    *,
    cfg: dict | None = None,
    ws_uri: str | None = None,
    token: str | None = None,
    timeout: float | None = None,
    action: str | None = None,
) -> Optional[str]:
    """Call NapCat `fetch_ptt_text` (QQ native STT) over a short-lived OneBot WS.

    Returns transcript text or None on soft-fail. Does not load mlx-whisper.
    """
    cfg = cfg or _cfg()
    if not cfg.get("prefer_qq_stt", True):
        return None
    mid = message_id if message_id is not None else ""
    if isinstance(mid, str):
        mid = mid.strip()
    if mid == "" or mid is None:
        return None
    try:
        import websocket  # websocket-client
    except ImportError:
        return None
    uri = (ws_uri or cfg.get("qq_ws_uri") or "ws://127.0.0.1:3001").strip()
    tok = token if token is not None else (cfg.get("qq_ws_token") or "")
    if tok and "access_token=" not in uri:
        sep = "&" if "?" in uri else "?"
        uri = f"{uri}{sep}access_token={tok}"
    act = (action or cfg.get("qq_stt_action") or "fetch_ptt_text").strip() or "fetch_ptt_text"
    to = float(timeout if timeout is not None else cfg.get("qq_stt_timeout") or 12)
    echo = f"xt_ptt_{int(time.time() * 1000) % 10_000_000}"
    done: dict[str, Any] = {"raw": None}

    def on_open(ws):
        try:
            ws.send(
                json.dumps(
                    {"action": act, "params": {"message_id": mid}, "echo": echo},
                    ensure_ascii=False,
                )
            )
        except Exception as e:
            done["raw"] = {"error": repr(e)}
            try:
                ws.close()
            except Exception:
                pass

    def on_message(ws, message: str):
        try:
            data = json.loads(message)
        except Exception:
            return
        if data.get("echo") != echo:
            return
        if "retcode" not in data and data.get("status") not in ("ok", "failed"):
            return
        done["raw"] = data
        try:
            ws.close()
        except Exception:
            pass

    def on_error(ws, err):
        if done["raw"] is None:
            done["raw"] = {"error": repr(err)}
        try:
            ws.close()
        except Exception:
            pass

    headers = [f"Authorization: Bearer {tok}"] if tok else None
    try:
        ws = websocket.WebSocketApp(
            uri,
            header=headers,
            on_open=on_open,
            on_message=on_message,
            on_error=on_error,
        )
    except TypeError:
        ws = websocket.WebSocketApp(
            uri,
            on_open=on_open,
            on_message=on_message,
            on_error=on_error,
        )
    th = threading.Thread(target=lambda: ws.run_forever(ping_interval=None), daemon=True)
    th.start()
    t0 = time.time()
    while time.time() - t0 < to and done["raw"] is None and th.is_alive():
        time.sleep(0.05)
    try:
        ws.close()
    except Exception:
        pass
    raw = done["raw"]
    if not isinstance(raw, dict):
        return None
    if raw.get("status") == "ok" or raw.get("retcode") == 0:
        data = raw.get("data") or {}
        if isinstance(data, dict):
            t = (data.get("text") or "").strip()
            return t or None
        if isinstance(data, str) and data.strip():
            return data.strip()
    return None


def transcribe_file(path: str | pathlib.Path, *, cfg: dict | None = None, use_cache: bool = True) -> Optional[str]:
    """Transcribe one local audio file via mlx-whisper. Soft-fail → None."""
    cfg = cfg or _cfg()
    if not cfg.get("enabled", True):
        return None
    if (cfg.get("backend") or "") == "none":
        return None
    p = pathlib.Path(path).expanduser()
    if not p.is_file():
        return None
    # Refuse accidental Kimi coding API
    base = str(cfg.get("magpie_base") or "")
    if "api.kimi.com" in base:
        return None
    try:
        sha = file_hash(p)
    except OSError:
        return None
    if use_cache:
        hit = cache_get(sha, cfg)
        if hit:
            return hit
    wav = to_wav(p, cfg=cfg)
    if not wav:
        return None
    t0 = time.monotonic()
    text: Optional[str] = None
    try:
        backend = (cfg.get("backend") or "mlx_whisper").strip()
        if backend == "mlx_whisper":
            import mlx_whisper

            result = mlx_whisper.transcribe(
                str(wav),
                path_or_hf_repo=cfg.get("model") or DEFAULT_MODEL,
                language=cfg.get("language") or "zh",
                verbose=False,
            )
            text = (result.get("text") or "").strip()
        else:
            return None
    except Exception:
        return None
    finally:
        # clean temp wav dir if we created one
        if wav is not None and wav.parent.name.startswith("xt_asr_"):
            shutil.rmtree(wav.parent, ignore_errors=True)
    text = _clip(text or "", cfg)
    if not text:
        return None
    if use_cache:
        cache_put(
            sha,
            text,
            meta={
                "path": str(p),
                "latency_ms": int((time.monotonic() - t0) * 1000),
            },
            cfg=cfg,
        )
    return text


def format_tag_with_transcript(tag: str, transcript: str) -> str:
    """[语音] / [语音 3秒] + text → [语音：…] / [语音 3秒：…]."""
    inner = tag.strip()
    if inner.startswith("[") and inner.endswith("]"):
        inner = inner[1:-1]
    t = (transcript or "").strip()
    if not t:
        return f"[{inner}]"
    return f"[{inner}：{t}]"


def enrich_text(
    text: str,
    voice_paths: list[str] | None,
    *,
    cfg: dict | None = None,
    qq_transcript: str | None = None,
    qq_message_id: str | int | None = None,
    raw: dict | None = None,
) -> tuple[str, dict]:
    """Replace first bare [语音…] with bracket transcript. Soft-fail keeps original.

    Preference: inline/QQ STT → mlx-whisper on local voice_paths.
    """
    cfg = cfg or _cfg()
    info: dict[str, Any] = {
        "ok": False,
        "text": None,
        "path": None,
        "hash": None,
        "cached": False,
        "source": None,
    }
    text = text or ""
    if not cfg.get("enabled", True):
        info["error"] = "disabled"
        return text, info
    if re.search(r"\[语音(?:\s*\d+秒)?：", text):
        info["error"] = "already_enriched"
        info["ok"] = True
        info["source"] = "pre_enriched"
        return text, info
    m = _TAG_RE.search(text)
    if not m:
        info["error"] = "no_tag"
        return text, info

    # 1) Inline QQ/NapCat transcript (segment text or media meta)
    inline = (qq_transcript or "").strip() or extract_inline_qq_transcript(raw)
    if inline:
        tr = _clip(inline, cfg)
        if tr:
            info["ok"] = True
            info["text"] = tr
            info["source"] = "qq_inline"
            enriched = format_tag_with_transcript(m.group(0), tr)
            return text[: m.start()] + enriched + text[m.end() :], info

    # 2) NapCat fetch_ptt_text (QQ native translatePtt2Text)
    mid = qq_message_id
    if mid in (None, "") and isinstance(raw, dict):
        mid = raw.get("message_id") or (raw.get("_xiaotian_media") or {}).get("message_id")
    if cfg.get("prefer_qq_stt", True) and mid not in (None, ""):
        t0 = time.monotonic()
        tr = qq_stt_fetch(mid, cfg=cfg)
        if tr:
            tr = _clip(tr, cfg)
        if tr:
            info["ok"] = True
            info["text"] = tr
            info["source"] = "qq_fetch_ptt_text"
            info["message_id"] = mid
            info["latency_ms"] = int((time.monotonic() - t0) * 1000)
            enriched = format_tag_with_transcript(m.group(0), tr)
            return text[: m.start()] + enriched + text[m.end() :], info
        info["qq_stt_error"] = "fetch_failed_or_empty"

    # 3) Local mlx-whisper fallback
    paths = [p for p in (voice_paths or []) if p and pathlib.Path(p).expanduser().is_file()]
    if not paths:
        info["error"] = "no_local_voice" if not info.get("qq_stt_error") else "qq_stt_and_no_local_voice"
        return text, info
    path = paths[0]
    info["path"] = path
    try:
        sha = file_hash(path)
        info["hash"] = sha
        if cache_get(sha, cfg):
            info["cached"] = True
    except OSError as e:
        info["error"] = f"hash:{type(e).__name__}"
        return text, info
    tr = transcribe_file(path, cfg=cfg, use_cache=True)
    if not tr:
        info["error"] = "asr_failed"
        return text, info
    info["ok"] = True
    info["text"] = tr
    info["source"] = "mlx_whisper"
    enriched = format_tag_with_transcript(m.group(0), tr)
    new_text = text[: m.start()] + enriched + text[m.end() :]
    return new_text, info


def enrich_inbound(msg, *, cfg: dict | None = None):
    """Mutate Inbound.text when voice paths / QQ STT exist under raw."""
    cfg = cfg or _cfg()
    media = ((getattr(msg, "raw", None) or {}).get("_xiaotian_media") or {})
    paths = list(media.get("voice_paths") or [])
    if not paths and isinstance(getattr(msg, "raw", None), dict):
        paths = list((msg.raw.get("voice_paths") or []))
    qq_tr = None
    if isinstance(media, dict):
        qq_tr = media.get("voice_transcript") or media.get("qq_stt_text")
    mid = getattr(msg, "msg_id", None) or None
    if not mid and isinstance(getattr(msg, "raw", None), dict):
        mid = msg.raw.get("message_id") or (media.get("message_id") if isinstance(media, dict) else None)
    new_text, info = enrich_text(
        getattr(msg, "text", "") or "",
        paths,
        cfg=cfg,
        qq_transcript=qq_tr,
        qq_message_id=mid,
        raw=getattr(msg, "raw", None) if isinstance(getattr(msg, "raw", None), dict) else None,
    )
    if info.get("ok") and new_text != (msg.text or ""):
        msg.text = new_text
    if isinstance(msg.raw, dict):
        media2 = dict((msg.raw.get("_xiaotian_media") or {}))
        if info.get("ok") and info.get("text") and info.get("source", "").startswith("qq"):
            media2["voice_transcript"] = info["text"]
            media2["qq_stt_source"] = info.get("source")
        msg.raw = {**msg.raw, "_xiaotian_media": media2, "_xiaotian_asr": info}
    return msg, info
