#!/bin/bash
# 小甜 launchd 总开关。用法：tools/xiaotian_ctl.sh {status|start|stop|restart|install|logs|disable-ocr} [job ...]
#   job 取 wechat ilink onebot magpie proactive campus collect materials-purge reflection schedule-month avatar-qq
#   省略 job 时默认 = DEFAULT（含 ilink+onebot，不含 wechat）：微信 iLink + QQ OneBot KeepAlive，OCR 默认不启。
#   显式 `start wechat` 仍可强制加载 OCR daemon（需 config ocr_daemon_enabled=true 才会真发）。
#   start   = bootstrap（未加载时）+ 若含 wechat/ilink 则 kickstart；定时任务只加载、按日程跑
#   stop    = bootout（卸载，立刻停进程，重启机器前不会再起；plist 留在 ~/Library/LaunchAgents）
#   install = 把仓库 launchd/*.plist 复制到 ~/Library/LaunchAgents（先 stop 再 start 才生效）
#   disable-ocr = bootout+disable com.xiaotian.wechat（iLink-only 策略）
#   logs    = tail ~/Library/Logs/xiaotian/*
#   ilink 若已有手动 `ilink_weixin_run.py --send`（非 launchd），start 会跳过以免双开；
#           要交给 KeepAlive：先结束该 PID，再 `start ilink`。
#   onebot 若已有手动 `onebot_qq_run.py --send`，start 同样跳过；先结束 PID 再 `start onebot`。
set -u
ALL="wechat ilink onebot magpie proactive campus collect materials-purge reflection schedule-month avatar-qq"
DEFAULT="ilink onebot magpie proactive campus collect materials-purge reflection schedule-month avatar-qq"
D="gui/$(id -u)"; LA="$HOME/Library/LaunchAgents"; LOG="$HOME/Library/Logs/xiaotian"
SRC="$(cd "$(dirname "$0")/.." && pwd)/launchd"
cmd="${1:-status}"; shift || true; JOBS="${*:-$DEFAULT}"
loaded() { launchctl print "$D/com.xiaotian.$1" >/dev/null 2>&1; }
ilink_orphan_pids() { pgrep -f 'ilink_weixin_run.py --send' 2>/dev/null || true; }
onebot_orphan_pids() { pgrep -f 'onebot_qq_run.py --send' 2>/dev/null || true; }
for j in $JOBS; do case " $ALL " in *" $j "*) ;; *) echo "未知 job: $j" >&2; exit 2;; esac; done
mkdir -p "$LOG"
case "$cmd" in
  status)
    printf "%-16s %-8s %-7s %-6s %s\n" JOB LOADED PID LAST_EXIT RUNS
    for j in $JOBS; do
      if loaded "$j"; then
        p=$(launchctl print "$D/com.xiaotian.$j")
        pid=$(awk '/^\tpid = /{print $3}' <<<"$p"); ex=$(awk -F'= ' '/^\tlast exit code/{print $2}' <<<"$p"); rn=$(awk '/^\truns = /{print $3}' <<<"$p")
        printf "%-16s %-8s %-7s %-6s %s\n" "$j" yes "${pid:--}" "${ex:--}" "${rn:-0}"
      else
        extra=""
        if [ "$j" = ilink ]; then
          op=$(ilink_orphan_pids | tr '\n' ',' | sed 's/,$//')
          [ -n "$op" ] && extra=" (manual pid $op)"
        fi
        if [ "$j" = onebot ]; then
          op=$(onebot_orphan_pids | tr '\n' ',' | sed 's/,$//')
          [ -n "$op" ] && extra=" (manual pid $op)"
        fi
        printf "%-16s %-8s%s\n" "$j" no "$extra"
      fi
    done;;
  install)
    for j in $JOBS; do cp "$SRC/com.xiaotian.$j.plist" "$LA/" && plutil -lint "$LA/com.xiaotian.$j.plist"; done;;
  start)
    for j in $JOBS; do
      [ -f "$LA/com.xiaotian.$j.plist" ] || { echo "$j: 未安装，先 install" >&2; continue; }
      if [ "$j" = ilink ] && ! loaded ilink; then
        op=$(ilink_orphan_pids)
        if [ -n "$op" ]; then
          echo "ilink: 已有非 launchd 进程 ($op)，跳过 bootstrap 以免双开"
          echo "ilink: 采纳 KeepAlive 步骤：结束该 PID → tools/xiaotian_ctl.sh start ilink"
          continue
        fi
      fi
      if [ "$j" = onebot ] && ! loaded onebot; then
        op=$(onebot_orphan_pids)
        if [ -n "$op" ]; then
          echo "onebot: 已有非 launchd 进程 ($op)，跳过 bootstrap 以免双开"
          echo "onebot: 采纳 KeepAlive 步骤：结束该 PID → tools/xiaotian_ctl.sh start onebot"
          continue
        fi
      fi
      loaded "$j" || launchctl bootstrap "$D" "$LA/com.xiaotian.$j.plist" || echo "$j: bootstrap 失败" >&2
      launchctl enable "$D/com.xiaotian.$j"
      if [ "$j" = wechat ] || [ "$j" = ilink ] || [ "$j" = onebot ] || [ "$j" = magpie ]; then
        launchctl kickstart -k "$D/com.xiaotian.$j" 2>/dev/null || launchctl kickstart "$D/com.xiaotian.$j"
      fi
      echo "$j: started"
    done;;
  stop)
    for j in $JOBS; do loaded "$j" && launchctl bootout "$D/com.xiaotian.$j" && echo "$j: stopped" || echo "$j: 未加载"; done;;
  restart)
    "$0" stop $JOBS; sleep 2; "$0" start $JOBS;;
  logs)
    cd "$LOG" && tail -n 30 -F $(for j in $JOBS; do echo "$j.log $j.err"; done) 2>/dev/null;;
  disable-ocr)
    # iLink-only：卸载并 disable OCR wechat daemon，避免登录会话再拉起
    if loaded wechat; then launchctl bootout "$D/com.xiaotian.wechat" && echo "wechat: stopped"; else echo "wechat: 未加载"; fi
    launchctl disable "$D/com.xiaotian.wechat" 2>/dev/null && echo "wechat: disabled" || echo "wechat: disable 跳过/失败"
    ;;
  *) sed -n 2,16p "$0"; exit 2;;
esac
