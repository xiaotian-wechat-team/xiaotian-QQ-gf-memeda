#!/bin/bash
# 在 MacBook（用户 you）上跑：用 ChatGPT.app 自带的 Codex CLI（已用 ChatGPT 账号登录，复用 ~/.codex/auth.json，
# 不需要设备码）调内置 image_gen 生成小甜的原创图。
# 用法：tools/imagegen_macbook.sh "在图书馆窗边抱着书" [输出目录] [穿搭文字] [写实修饰文字]
# 表情：环境变量 EMOTION_TEXT（与 src/emotion.expression_cue 一致，缓解僵硬表情）
# 穿搭文字：第 3 个参数 > 环境变量 OUTFIT_TEXT > 本机有 ~/xiaotian-wechat 时调 src/outfit.py --text-only > 保守兜底。
# （src/imagegen.py 的 macbook 后端会用 OUTFIT_TEXT=... 把 outfit.py 推断的穿搭传进来）
# 环境变量：CODEX（默认 ChatGPT.app 内置）、ASSETS（默认 ~/xiaotian-assets/appearance/zhuzhuxiaoye）、MAX_REFS（默认 2）
# 参考图（2026-10-24）：$ASSETS/canonical/sheet.png（标准五视图，身份标准）放第一张；其余 3 张优先用本机 repo 的
#   src/refpick.py --identity（angles.json 随机、不同拍摄、头肩裁图），没有就退回 SAFE_REFS/refs/。环境变量 REF_FILES（换行分隔）可直接指定其余参考。
# 姿态五视图（2026-10-24）：正视 sheet.png 必附；姿态 sheet 优先 refpick.extra_kind 向量选择，bash 仅窄姿态词兜底（无看书/看手机）。
#   EXTRA_SHEET=up|down|back|none 可强制。
# 会话（2026-10-24）：同一天所有生图复用同一个 Codex 会话（codex exec resume <id>），状态文件 $SESSION_DIR/codex_session_<YYYY-MM-DD>
#   （默认 ~/.xiaotian）；日期变了就新开；当天记录的会话 resume 失败（no rollout found 等）就删掉记录、为当天新开。只保留最近 14 个日期文件。
# 注意：-i 是多值参数，会吞掉后面的位置参数，所以提示词一律从 stdin 传。
# 实测（2026-10-23）：穿吊带/紧身的参考图易在输出阶段被安全过滤（moderation_blocked: sexual）；
# 默认挑 refs/ 里的 SAFE_REFS，并在提示词里写明日常保守穿着。
set -euo pipefail
SCENE="${1:?用法: $0 \"画面描述\" [输出目录] [穿搭文字]}"
CODEX="${CODEX:-/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex}"
ASSETS="${ASSETS:-$HOME/xiaotian-assets/appearance/zhuzhuxiaoye}"
OUT="${2:-$HOME/xiaotian-assets/generated/$(date +%Y%m%d-%H%M%S)}"
MAX_REFS="${MAX_REFS:-2}"
[ -x "$CODEX" ] || command -v "$CODEX" >/dev/null || { echo "找不到 codex: $CODEX" >&2; exit 2; }
"$CODEX" login status 2>&1 | grep -q "Logged in" || { echo "Codex 未登录。不要自动发设备码；请用户在 ChatGPT.app 里登录后再试。" >&2; exit 3; }
mkdir -p "$OUT"; touch "$OUT/.t0"
REFS=(); NCAN=0
[ -f "$ASSETS/canonical/sheet.png" ] && { REFS+=(-i "$ASSETS/canonical/sheet.png"); NCAN=1; }
REPO="${REPO:-$HOME/xiaotian-wechat}"
OTHERS="${REF_FILES:-}"
if [ -z "$OTHERS" ] && [ "$NCAN" = 1 ] && [ -x "$REPO/.venv/bin/python" ]; then
  OTHERS="$(cd "$REPO/src" && ../.venv/bin/python refpick.py --identity "$SCENE" -n 3 2>/dev/null | sed -n 's/^other //p' || true)"
fi
EXTRA="${EXTRA_SHEET:-}"
if [ -z "$EXTRA" ] && [ "$NCAN" = 1 ] && [ -x "$REPO/.venv/bin/python" ]; then
  EXTRA="$(cd "$REPO/src" && ../.venv/bin/python -c 'import sys,refpick;print(refpick.extra_kind(sys.argv[1]) or "")' "$SCENE" 2>/dev/null || true)"
fi
if [ -z "$EXTRA" ]; then
  if   [[ "$SCENE" =~ (背影|背对|背身|回头|回眸|转身|从背后|从后面|后背|背面) ]]; then EXTRA=back
  elif [[ "$SCENE" =~ (仰头|抬头|仰望|仰着|往上看|向上看|看天|望天|看星星|看月亮|看烟花|看樱花|看雪|下巴抬) ]]; then EXTRA=up
  elif [[ "$SCENE" =~ (低头|垂眸|垂眼|俯身|往下看|向下看|看脚下) ]]; then EXTRA=down; fi
fi
EXTRA_LABEL=""
if [ "$NCAN" = 1 ] && [ -n "$EXTRA" ] && [ "$EXTRA" != none ] && [ -f "$ASSETS/canonical/$EXTRA/sheet.png" ]; then
  REFS+=(-i "$ASSETS/canonical/$EXTRA/sheet.png"); NCAN=2
  case "$EXTRA" in up) EXTRA_LABEL=仰头;; down) EXTRA_LABEL=低头;; back) EXTRA_LABEL=背影;; esac
fi
if [ -n "$OTHERS" ]; then while read -r r; do [ -n "$r" ] && REFS+=(-i "$r"); done <<< "$OTHERS"
elif [ -f "$ASSETS/SAFE_REFS" ]; then while read -r r; do [ -n "$r" ] && REFS+=(-i "$ASSETS/refs/$r"); done < <(head -n "$MAX_REFS" "$ASSETS/SAFE_REFS")
else for r in $(ls "$ASSETS/refs" | head -n "$MAX_REFS"); do REFS+=(-i "$ASSETS/refs/$r"); done; fi
OUTFIT="${3:-${OUTFIT_TEXT:-}}"
REPO="${REPO:-$HOME/xiaotian-wechat}"
if [ -z "$OUTFIT" ] && [ -x "$REPO/.venv/bin/python" ] && [ -f "$REPO/src/outfit.py" ]; then
  OUTFIT="$(cd "$REPO/src" && ../.venv/bin/python outfit.py "$SCENE" --text-only 2>/dev/null | tail -n 1 || true)"
fi
OUTFIT="${OUTFIT:-穿搭：日常、得体、保守。整体好看、有分寸，不暴露、不性感化。}"
# 表情提示：环境变量 EMOTION_TEXT（由 imagegen.macbook_cmd 传入，与 PAD→mood/energy 一致）
EMOTION="${EMOTION_TEXT:-}"
# 写实修饰：第 4 个参数 > 环境变量 REALISM_TEXT（可为空串=本张不加）> 本机 src/realism.py --text-only；REALISM=0 关闭
if [ $# -ge 4 ]; then REAL="$4"
elif [ -n "${REALISM_TEXT+x}" ]; then REAL="$REALISM_TEXT"
elif [ "${REALISM:-1}" != 0 ] && [ -x "$REPO/.venv/bin/python" ] && [ -f "$REPO/src/realism.py" ]; then
  REAL="$(cd "$REPO/src" && ../.venv/bin/python realism.py "$SCENE" --text-only ${REALISM_SEED:+--seed "$REALISM_SEED"} 2>/dev/null | tail -n 1 || true)"
else REAL=""; fi
DESC="$(cat "$ASSETS/description.md" 2>/dev/null || true)"
BODY="体型锁定：身高约 173 cm、体重约 50 kg，纤细修长；头身比符合该身高，与场景门框/桌椅透视尺度一致。"
if [ "$NCAN" = 1 ]; then REFTXT="附带的参考图都是同一个女生（小甜）。第 1 张「正视标准肖像五视图」是默认必附的唯一身份标准（脸型、五官比例、发型、肤色以它为准）；五视图只锁长相，不要照搬侧脸转头或倾斜。其余几张只作角度/表情参考，不要照搬穿着、构图和背景。如果本会话之前生成过图，忽略之前的画面，只按本条消息的参考图和要求画新的一张。${BODY}"
elif [ "$NCAN" = 2 ]; then REFTXT="附带的参考图都是同一个女生（小甜）。第 1 张「正视标准肖像五视图」是唯一身份标准；第 2 张是「${EXTRA_LABEL}五视图」，仅参照俯仰/朝向与发型轮廓，不要左右歪头，长相与体型仍以第 1 张为准。其余几张只作角度/表情参考。如果本会话之前生成过图，忽略之前的画面，只按本条消息的参考图和要求画新的一张。${BODY}"
else REFTXT="人物外貌参考（附带的参考图只用于把握发型、气质，生成一个原创的、风格相似的人物，不要复刻或换脸）。${BODY}"; fi
PROMPT="用内置 image_gen 工具生成一张图片（不要用代码/PIL 画，不要改动任何文件）。
任务核心是场景与活动（不是摆姿势）：${SCENE}。按这个场景里自然会发生的动作来画，不要额外加歪头/托腮/回眸等定型姿势。
${REFTXT}
${DESC}
${OUTFIT}${EMOTION:+
${EMOTION}}手机直出照片质感，竖构图。${REAL}生成一张后停止。 \$imagegen"
# 注：聊天多图（N>1）走 Mac mini imagegen.py 同一次 prompt 一次出齐；本应急脚本仍按单张。
SESSION_DIR="${SESSION_DIR:-$HOME/.xiaotian}"; mkdir -p "$SESSION_DIR"
SFILE="$SESSION_DIR/codex_session_$(date +%F)"; SID="$(cat "$SFILE" 2>/dev/null || true)"
ls -1 "$SESSION_DIR"/codex_session_* 2>/dev/null | sort -r | tail -n +15 | xargs rm -f 2>/dev/null || true
CH="${CODEX_HOME:-$HOME/.codex}"
# 复用会话时 rollout 文件里有之前的图；收图只看 generated_images 里比 .t0 新的文件，不受影响
if [ -n "$SID" ]; then
  (cd "$OUT" && printf '%s' "$PROMPT" | "$CODEX" exec resume --json --skip-git-repo-check "$SID" - "${REFS[@]}") > "$OUT/codex.log" 2>&1 || true
  if grep -qi 'no rollout found\|thread/resume failed\|session not found' "$OUT/codex.log"; then echo "当天会话 $SID 无法 resume，新开" >&2; rm -f "$SFILE"; SID=""; fi
fi
if [ -z "$SID" ]; then
  printf '%s' "$PROMPT" | "$CODEX" exec --json --skip-git-repo-check --sandbox workspace-write -C "$OUT" "${REFS[@]}" > "$OUT/codex.log" 2>&1 || true
  NEW="$(grep -o '"thread_id":"[^"]*"' "$OUT/codex.log" | head -1 | cut -d'"' -f4 || true)"
  [ -n "$NEW" ] && printf '%s' "$NEW" > "$SFILE"
fi
find "$CH/generated_images" "$OUT" -newer "$OUT/.t0" -type f -name '*.png' 2>/dev/null | while read -r f; do [ "$(dirname "$f")" = "$OUT" ] || cp "$f" "$OUT/"; done
N=$(ls "$OUT"/*.png 2>/dev/null | wc -l | tr -d ' ')
if [ "$N" = 0 ]; then grep -o 'moderation_blocked\|safety_violations=\[[a-z_,]*\]' "$OUT/codex.log" | sort -u >&2 || true; echo "没有产出图片，见 $OUT/codex.log" >&2; exit 1; fi
ls "$OUT"/*.png
