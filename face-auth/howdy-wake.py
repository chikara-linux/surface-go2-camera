#!/usr/bin/env python3
"""ロック中に画面が点いたら、顔認証を開始させる常駐サービス。

## なぜ要るのか

Plasma のロック画面は、放置による自動ロックでは認証を開始しない。
グリーターは生成されるが、利用者が画面に触れるまで PAM の会話が始まらない。
実測（2026-09-04）:

    20:42:30  グリーター生成
    20:42:32  ロック成立
    20:43:32  画面消灯
    20:45:24  画面点灯          ← ここでも認証は始まらない
    20:45:45  触った瞬間に認証開始（3分15秒後）

サスペンド復帰では操作なしで始まる（同日25回観測）。契機によって挙動が違う。

## 何をするのか

画面が消灯から点灯へ変わり、かつロック中なら、Enter を1回注入する。
「空の PIN を送信した」のと同じ刺激になり、グリーターが認証を開始する。

Enter でなければ駄目である。修飾キー（Shift）では認証が始まらない
（30秒観測して無反応）。空 PIN の送信が引き金になっている。

## なぜこの方法なのか

* グリーターを殺して作り直させる回避策は、kscreenlocker が異常終了を
  3回までしか許容せず（解除するまでカウンタが戻らない）、4回目で
  EmergencyWindow に落ちて TTY からしか復帰できない。カバンの中での
  誤操作を考えると採れない。
* 常駐サービスがセッションを直接解錠することはできない。
  org.freedesktop.login1.lock-sessions は allow_active=auth_admin_keep。
  認証の判断は PAM に残る。このサービスは「触った」のと同じ刺激を
  与えるだけで、認証そのものには関与しない。

## 安全側の作り

* 発火は「消灯 → 点灯」の遷移のみ。点灯したままの再発火はしない。
  PIN を入力している最中に Enter を送ると入力途中の内容が送信されるため。
* 最短間隔を設ける。連打を防ぐ。
* 回数の上限は無い。カバンの中で何度誤操作されても壊れない。
"""
import fcntl
import glob
import os
import select
import struct
import subprocess
import sys
import time

MIN_OFF_SECONDS = 2.0      # これ以上消えていた場合のみ、点灯を「復帰」とみなす
MIN_INTERVAL = 5.0         # 前回の注入からこれだけ空ける
FALLBACK_POLL_MS = 5000    # 通知が来ない環境向けの空振り

UI_DEV_CREATE, UI_DEV_DESTROY = 0x5501, 0x5502
UI_SET_EVBIT, UI_SET_KEYBIT = 0x40045564, 0x40045565
UI_DEV_SETUP = 0x405C5503
EV_SYN, EV_KEY, SYN_REPORT = 0, 1, 0
KEY_ENTER = 28


def log(msg):
    print(msg, flush=True)


def display_on():
    """画面が点いているか。判定できなければ None。"""
    for path in sorted(glob.glob("/sys/class/drm/*/dpms")):
        d = os.path.dirname(path)
        try:
            if open(os.path.join(d, "enabled")).read().strip() != "enabled":
                continue
            return open(path).read().strip().lower() == "on"
        except Exception:
            continue
    for path in sorted(glob.glob("/sys/class/backlight/*/bl_power")):
        try:
            return open(path).read().strip() == "0"
        except Exception:
            continue
    return None


def is_locked():
    """ロック画面が出ているか。判定できなければ None。"""
    try:
        out = subprocess.run(
            ["qdbus6", "org.freedesktop.ScreenSaver", "/ScreenSaver",
             "org.freedesktop.ScreenSaver.GetActive"],
            capture_output=True, text=True, timeout=5).stdout.strip()
    except Exception:
        return None
    if out == "true":
        return True
    if out == "false":
        return False
    return None


def send_enter():
    """Enter を1回押して離す。依存を増やさないため ioctl を直接叩く。"""
    fd = os.open("/dev/uinput", os.O_WRONLY | os.O_NONBLOCK)
    try:
        fcntl.ioctl(fd, UI_SET_EVBIT, EV_KEY)
        # 一般的なキーボードとして申告する。修飾キーだけだと udev が
        # ID_INPUT_KEYBOARD を付けず、libinput が入力として扱わない。
        for code in range(1, 84):
            fcntl.ioctl(fd, UI_SET_KEYBIT, code)
        fcntl.ioctl(fd, UI_DEV_SETUP,
                    struct.pack("HHHH80sI", 0x03, 0x1234, 0x5678, 1,
                                b"howdy-wake", 0))
        fcntl.ioctl(fd, UI_DEV_CREATE)
        time.sleep(1.2)                 # コンポジタが認識するのを待つ
        for value in (1, 0):
            os.write(fd, struct.pack("llHHi", 0, 0, EV_KEY, KEY_ENTER, value))
            os.write(fd, struct.pack("llHHi", 0, 0, EV_SYN, SYN_REPORT, 0))
            time.sleep(0.05)
        time.sleep(0.3)
        fcntl.ioctl(fd, UI_DEV_DESTROY)
    finally:
        os.close(fd)


def main():
    state = display_on()
    if state is None:
        sys.exit("画面の状態を読めません。対応していない環境です。")

    watch = None
    poller = None
    for cand in sorted(glob.glob("/sys/class/backlight/*/actual_brightness")):
        try:
            watch = open(cand, "rb")
            watch.read()
            poller = select.poll()
            poller.register(watch.fileno(), select.POLLPRI | select.POLLERR)
            break
        except Exception:
            watch = None
    log("開始しました。画面の状態: %s / 通知: %s"
        % ("点灯" if state else "消灯", "あり" if poller else "空振りのみ"))

    off_since = None if state else time.time()
    last_fire = 0.0
    while True:
        if poller is not None:
            poller.poll(FALLBACK_POLL_MS)
            try:
                watch.seek(0)
                watch.read()
            except Exception:
                pass
        else:
            time.sleep(0.25)

        now = display_on()
        if now is None or now == state:
            continue
        state = now

        if not state:                    # 点灯 → 消灯
            off_since = time.time()
            continue

        # 消灯 → 点灯
        off_for = time.time() - off_since if off_since else 0.0
        off_since = None
        if off_for < MIN_OFF_SECONDS:
            continue
        if time.time() - last_fire < MIN_INTERVAL:
            continue
        locked = is_locked()
        if locked is not True:
            continue
        last_fire = time.time()
        log("画面が点灯（消灯 %.1f 秒）。ロック中なので認証を起こします" % off_for)
        try:
            send_enter()
        except Exception as err:
            log("注入に失敗: %s" % err)


if __name__ == "__main__":
    main()
