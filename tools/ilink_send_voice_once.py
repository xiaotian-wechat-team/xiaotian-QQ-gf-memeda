#!/usr/bin/env python3
"""一次性：TTS2.0 → SILK → iLink send_voice。不抢长轮询（不 messages()）。

用法（仓库根）:
  .venv/bin/python tools/ilink_send_voice_once.py '崽崽听得到吗'
  .venv/bin/python tools/ilink_send_voice_once.py --silk data/tts_mvp/x.silk --ms 3000
不打印密钥。勿与改 iLink 配置混用；默认同 peer（凭据里的 userId）。
"""
from __future__ import annotations
import argparse, json, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("text", nargs="?", default="崽崽，这是小甜的语音条测试，听得到吗")
    ap.add_argument("--silk", default="")
    ap.add_argument("--ms", type=int, default=0)
    ap.add_argument("--peer", default="")
    ap.add_argument("--skip-tts", action="store_true")
    args = ap.parse_args()

    peer = args.peer.strip()
    if not peer:
        creds = json.loads((ROOT / "data/ilink_creds.json").read_text(encoding="utf-8"))
        peer = creds.get("userId") or ""
    if not peer:
        print("无 peer", file=sys.stderr)
        return 1

    out_dir = ROOT / "data" / "tts_mvp"
    out_dir.mkdir(parents=True, exist_ok=True)
    silk = Path(args.silk) if args.silk else out_dir / "voice_once.silk"
    playtime_ms = args.ms

    if not args.skip_tts and not args.silk:
        mp3 = out_dir / "voice_once.mp3"
        pcm = out_dir / "voice_once.pcm"
        r = subprocess.run(
            [str(ROOT / ".venv/bin/python"), str(ROOT / "tools/tts_doubao_tts20.py"), args.text, "--out", str(mp3)],
            cwd=str(ROOT),
        )
        if r.returncode != 0:
            return r.returncode
        subprocess.run(
            ["/opt/homebrew/bin/ffmpeg", "-y", "-i", str(mp3), "-ac", "1", "-ar", "24000", "-f", "s16le", str(pcm)],
            check=True, capture_output=True,
        )
        from weixin_ilink.voice import pcm_to_silk
        pcm_to_silk(pcm, silk, sample_rate=24000)
        playtime_ms = playtime_ms or max(1, int(pcm.stat().st_size / (24000 * 2) * 1000))
    else:
        if not silk.is_file():
            print(f"无 silk {silk}", file=sys.stderr)
            return 1
        if playtime_ms <= 0:
            pcm = silk.with_suffix(".pcm")
            playtime_ms = max(1, int(pcm.stat().st_size / (24000 * 2) * 1000)) if pcm.is_file() else 1000

    ctx = ""
    ctx_file = ROOT / "data/ilink_ctx.json"
    if ctx_file.is_file():
        try:
            ctx = (json.loads(ctx_file.read_text(encoding="utf-8")) or {}).get(peer) or ""
        except Exception:
            pass

    from weixin_ilink import WeixinBot
    from weixin_ilink.types import VoiceEncodeType

    bot = WeixinBot(credentials_file=str(ROOT / "data/ilink_creds.json"), auto_emoji=True)
    if ctx:
        bot.send_voice(peer, silk, playtime_ms=playtime_ms, sample_rate=24000,
                       encode_type=VoiceEncodeType.SILK, context_token=ctx)
    else:
        bot.client.send_voice(peer, silk, "", playtime_ms=playtime_ms, sample_rate=24000,
                              encode_type=VoiceEncodeType.SILK)
    print(f"ok voice peer={peer[:20]}… ms={playtime_ms} silk={silk}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
