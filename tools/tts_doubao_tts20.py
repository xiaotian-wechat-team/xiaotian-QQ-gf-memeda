#!/usr/bin/env python3
"""豆包语音合成 2.0 (seed-tts-2.0) 最小脚本。

鉴权：X-Api-App-Key = APP ID，X-Api-Access-Key = Access Token（合成 2.0 服务页）
环境变量：DOUBAO_TTS20_ACCESS_TOKEN（或 DOUBAO_VOICE_ACCESS_TOKEN）、DOUBAO_VOICE_APP_ID
可写 config/doubao_voice.env。不碰 iLink。
"""
from __future__ import annotations
import argparse, base64, json, os, uuid, urllib.request, urllib.error
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "data" / "tts_mvp"
ENV_FILE = ROOT / "config" / "doubao_voice.env"
ENDPOINT = "https://openspeech.bytedance.com/api/v3/tts/unidirectional"


def _load_env() -> None:
    if not ENV_FILE.is_file():
        return
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, _, v = s.partition("=")
        k, v = k.strip(), v.strip().strip("'").strip('"')
        if k and k not in os.environ:
            os.environ[k] = v


def main() -> None:
    _load_env()
    p = argparse.ArgumentParser()
    p.add_argument("text", nargs="?", default="宝宝你好呀，今天想吃啥")
    p.add_argument("--speaker", default=os.environ.get("DOUBAO_VOICE_SPEAKER", "zh_female_vv_uranus_bigtts"))
    p.add_argument("--out", default="")
    p.add_argument("--rate", type=int, default=24000)
    args = p.parse_args()
    app = os.environ.get("DOUBAO_VOICE_APP_ID", "").strip() or "4032032273"
    tok = (
        os.environ.get("DOUBAO_TTS20_ACCESS_TOKEN")
        or os.environ.get("DOUBAO_VOICE_ACCESS_TOKEN")
        or ""
    ).strip()
    if not tok:
        raise SystemExit("缺少 DOUBAO_TTS20_ACCESS_TOKEN（合成 2.0 服务页 Access Token）")
    rid = os.environ.get("DOUBAO_TTS_RESOURCE_ID", "seed-tts-2.0").strip() or "seed-tts-2.0"
    body = {
        "req_params": {
            "text": args.text.strip(),
            "speaker": args.speaker.strip(),
            "audio_params": {"format": "mp3", "sample_rate": args.rate},
        }
    }
    req = urllib.request.Request(
        ENDPOINT,
        data=json.dumps(body, ensure_ascii=False).encode(),
        headers={
            "Content-Type": "application/json",
            "X-Api-App-Key": app,
            "X-Api-Access-Key": tok,
            "X-Api-Resource-Id": rid,
            "X-Api-Request-Id": str(uuid.uuid4()),
            "Connection": "keep-alive",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            raw = resp.read()
            logid = resp.headers.get("X-Tt-Logid", "")
    except urllib.error.HTTPError as e:
        raise SystemExit(f"HTTP {e.code}: {e.read()[:400]!r}") from e
    audio = bytearray()
    for line in raw.splitlines():
        if not line.strip():
            continue
        try:
            data = json.loads(line)
        except Exception:
            continue
        if data.get("code", 0) == 0 and data.get("data"):
            audio.extend(base64.b64decode(data["data"]))
        if data.get("code") == 20000000:
            break
    if not audio:
        raise SystemExit(f"无音频返回 raw[:200]={raw[:200]!r}")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = Path(args.out) if args.out else OUT_DIR / f"tts20_{datetime.now().strftime('%Y%m%d_%H%M%S')}.mp3"
    out.write_bytes(audio)
    print(f"ok path={out} bytes={len(audio)} speaker={args.speaker} logid={logid}")


if __name__ == "__main__":
    main()
