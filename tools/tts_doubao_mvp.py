#!/usr/bin/env python3
"""最小 MVP：豆包音频生成 1.0 (seed-audio-1.0) → 本地 mp3/wav。

POST https://openspeech.bytedance.com/api/v3/tts/create

鉴权（二选一）：
  A) 新版：DOUBAO_VOICE_API_KEY → X-Api-Key
  B) 服务页试用：DOUBAO_VOICE_APP_ID + DOUBAO_VOICE_ACCESS_TOKEN
     → X-Api-App-Id / X-Api-App-Key + X-Api-Access-Key

可写 config/doubao_voice.env；不接入 daemon，不影响 iLink。
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import urllib.error
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "data" / "tts_mvp"
ENV_FILE = ROOT / "config" / "doubao_voice.env"
ENDPOINT = "https://openspeech.bytedance.com/api/v3/tts/create"
MODEL = "seed-audio-1.0"


def _load_dotenv_file(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, _, v = s.partition("=")
        k, v = k.strip(), v.strip().strip("'").strip('"')
        if k and k not in os.environ:
            os.environ[k] = v


def _env(*names: str) -> str:
    for n in names:
        v = os.environ.get(n, "").strip()
        if v:
            return v
    return ""


def _auth_headers() -> tuple[dict[str, str], str]:
    """返回 (headers, auth_mode_label)。"""
    _load_dotenv_file(ENV_FILE)
    h = {
        "Content-Type": "application/json",
        "X-Api-Request-Id": str(uuid.uuid4()),
    }
    api_key = _env("DOUBAO_VOICE_API_KEY", "VOLC_TTS_API_KEY", "OPENSPEECH_API_KEY")
    if api_key:
        h["X-Api-Key"] = api_key
        return h, "X-Api-Key"

    app_id = _env("DOUBAO_VOICE_APP_ID", "VOLC_TTS_APP_ID")
    token = _env(
        "DOUBAO_VOICE_ACCESS_TOKEN",
        "VOLC_TTS_ACCESS_TOKEN",
        "DOUBAO_VOICE_ACCESS_KEY",
    )
    if app_id and token:
        # 文档里音频生成写 App-Id，音色设计写 App-Key；两头都带同一 APP ID
        h["X-Api-App-Id"] = app_id
        h["X-Api-App-Key"] = app_id
        h["X-Api-Access-Key"] = token
        return h, "AppId+AccessToken"

    raise SystemExit(
        "缺少鉴权。试用额度页面上的「APP ID + Access Token」即可：\n"
        "  写入 config/doubao_voice.env：\n"
        "    DOUBAO_VOICE_APP_ID=...\n"
        "    DOUBAO_VOICE_ACCESS_TOKEN=...\n"
        "或新版单 Key：DOUBAO_VOICE_API_KEY=...\n"
        "Secret Key 一般不需要。勿把 Token 贴到聊天。"
    )


def synthesize(
    text: str,
    *,
    speaker: str | None,
    fmt: str,
    sample_rate: int,
    pitch: int,
    speech: int,
    loudness: int,
) -> tuple[bytes, dict]:
    headers, auth_mode = _auth_headers()
    body: dict = {
        "model": MODEL,
        "text_prompt": text,
        "audio_config": {
            "format": fmt,
            "sample_rate": sample_rate,
            "pitch_rate": pitch,
            "speech_rate": speech,
            "loudness_rate": loudness,
        },
    }
    if speaker:
        body["references"] = [{"speaker": speaker}]

    req = urllib.request.Request(
        ENDPOINT,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    logid = ""
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            logid = resp.headers.get("X-Tt-Logid", "") or ""
            raw = resp.read()
    except urllib.error.HTTPError as e:
        detail = e.read()[:800]
        raise SystemExit(f"HTTP {e.code}: {detail!r}") from e

    data = json.loads(raw.decode("utf-8"))
    if isinstance(data, dict) and data.get("code") not in (None, 0, "0"):
        raise SystemExit(
            f"API error code={data.get('code')} msg={data.get('message') or data}"
        )

    audio_b64 = data.get("audio") if isinstance(data, dict) else None
    url = data.get("url") if isinstance(data, dict) else None
    meta = {
        "duration": data.get("duration") if isinstance(data, dict) else None,
        "original_duration": data.get("original_duration") if isinstance(data, dict) else None,
        "logid": logid,
        "model": MODEL,
        "speaker": speaker,
        "auth_mode": auth_mode,
        "endpoint": ENDPOINT,
    }
    if audio_b64:
        return base64.b64decode(audio_b64), meta
    if url:
        with urllib.request.urlopen(url, timeout=120) as r:
            return r.read(), meta
    raise SystemExit(
        f"响应无 audio/url 字段，keys={list(data) if isinstance(data, dict) else type(data)}"
    )


def main() -> None:
    p = argparse.ArgumentParser(description="豆包音频生成 1.0 最小 MVP")
    p.add_argument("text", nargs="?", default="宝宝你好呀")
    p.add_argument("--speaker", default="", help="音色 ID；默认读 DOUBAO_VOICE_SPEAKER")
    p.add_argument("--format", dest="fmt", default="mp3", choices=["mp3", "wav", "pcm", "ogg_opus"])
    p.add_argument("--rate", type=int, default=48000)
    p.add_argument("--pitch", type=int, default=0)
    p.add_argument("--speech", type=int, default=0)
    p.add_argument("--loudness", type=int, default=0)
    p.add_argument("--out", default="")
    args = p.parse_args()

    text = args.text.strip()
    if not text:
        raise SystemExit("text 为空")
    if len(text) > 3000:
        raise SystemExit("text 超过 3000 字符")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    audio, meta = synthesize(
        text,
        speaker=args.speaker.strip() or None,
        fmt=args.fmt,
        sample_rate=args.rate,
        pitch=args.pitch,
        speech=args.speech,
        loudness=args.loudness,
    )
    out = Path(args.out) if args.out else OUT_DIR / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.{args.fmt}"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(audio)
    meta_path = out.with_suffix(out.suffix + ".json")
    meta_path.write_text(
        json.dumps(
            {**meta, "text": text, "path": str(out), "bytes": len(audio), "format": args.fmt},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"ok path={out} bytes={len(audio)} duration={meta.get('duration')} auth={meta.get('auth_mode')}")
    print(f"meta={meta_path}")


if __name__ == "__main__":
    main()
