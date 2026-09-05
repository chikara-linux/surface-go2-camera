#!/bin/bash
# ロック画面が固まったときの復旧スイッチ。クイック設定から呼ばれる。
#
# 症状: 顔認証が成功したのにセッションが解錠されず、グリーターは終了も
# 解錠もできない状態になる。PIN パッドも操作できなくなる。
# 原因は kscreenlocker の既知バグ（KDE Bug 515299）。認証が失敗すると
# 「次に認証を受け付ける時刻」が記録され、それ以前に届いた認証は黙って
# 破棄される。顔認証の成功がその窓に落ちると成功が捨てられる。
#
# グリーターを終了させると kscreenlocker が作り直す。ロックは維持される
# （異常終了は解錠にならない）ので、これで復旧できる。
#
# **ただし kscreenlocker は異常終了を 3 回までしか許容しない。**
# 4 回目で EmergencyWindow に落ち、TTY からしか復帰できなくなる。
# カウンタは解錠でしかリセットされない。そのため 1 ロックセッションにつき
# 1 回だけ許可し、2 回目は警告のみ出して強制再起動を促す。
# notify-send に日本語を渡すため UTF-8 が要る。C だと
# "Invalid byte sequence in conversion input" で通知が出ない。
export LC_ALL=C.UTF-8

MARK="/run/user/$(id -u)/lockscreen-restart.mark"
DBUS_ARGS=(org.freedesktop.ScreenSaver /ScreenSaver)

notify() {  # urgency title body
    notify-send -u "$1" -a "ロック画面" -i system-lock-screen -t 12000 "$2" "$3" 2>/dev/null
}

ss() {  # ScreenSaver のメソッドを呼び、Qt のロケール警告を落とす
    qdbus6 "${DBUS_ARGS[@]}" "org.freedesktop.ScreenSaver.$1" 2>/dev/null | tail -1
}

greeter_pids() {
    # comm は 15 文字で切れるので cmdline を直接見る。
    # pgrep -f と grep は「探している文字列が自分のコマンドラインに入る」ため
    # 自分自身にマッチする。この取り違えは何度も踏んだので、外部プロセスを
    # 使わず bash の case で判定する。
    local p c out=""
    for p in /proc/[0-9]*; do
        [ -r "$p/cmdline" ] || continue
        c=$(tr '\0' ' ' < "$p/cmdline" 2>/dev/null)
        case "$c" in
            *libexec/kscreenlocker_greet*) out="$out ${p#/proc/}" ;;
        esac
    done
    echo $out
}

locked=$(ss GetActive)
if [ "$locked" != "true" ]; then
    notify normal "ロックされていません" "この操作はロック画面が固まったときだけ使います。"
    exit 0
fi

pids=$(greeter_pids)
if [ -z "$pids" ]; then
    notify critical "グリーターが見つかりません" \
        "既に画面ロッカーが壊れている可能性があります。電源ボタン長押しで強制再起動してください。"
    exit 1
fi

# 同じロックセッションか判定する。GetActiveTime はロックしてからの経過秒で、
# グリーターを作り直しても継続する。現在時刻から引けば開始時刻になる。
active=$(ss GetActiveTime)
[ -n "$active" ] || active=0
now=$(date +%s)
session_start=$(( now - active ))

if [ -f "$MARK" ]; then
    prev=$(cat "$MARK" 2>/dev/null || echo 0)
    diff=$(( session_start - prev )); [ "$diff" -lt 0 ] && diff=$(( -diff ))
    if [ "$diff" -le 10 ]; then
        notify critical "これ以上は再起動できません" \
"このロックのあいだに既に 1 回試しています。
繰り返すと画面ロッカーが壊れ、TTY でしか復帰できなくなります。
電源ボタンの長押しで強制再起動してください。"
        exit 1
    fi
fi

echo "$session_start" > "$MARK"
notify normal "ロック画面を再起動します" "数秒で認証画面が戻ります。戻らなければ強制再起動してください。"
sleep 1
for p in $pids; do kill -TERM "$p" 2>/dev/null; done
exit 0
