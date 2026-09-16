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

## ロック画面が自ら起こす版との共存

plasma-mobile に自前パッチ（画面点灯で startAuthenticating() を呼ぶ）が
入っている間、このサービスは注入しない。判定はロック画面 QML の中身
（目印のコメント）で行う。パッチが更新で外れれば自動で注入に戻る。
つまりこのサービスは**保険**として常駐し続ける。

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


INJECT_MARK = "/run/user/%d/howdy-wake-injected" % os.getuid()
RESUME_MARK = "/run/howdy-resume-timestamp"
LOCKSCREEN_QML = ("/usr/share/plasma/shells/org.kde.plasma.mobileshell"
                  "/contents/lockscreen/LockScreen.qml")
LOCKSCREEN_MARK = b"chikara: face-auth-lockscreen v1"


def _lockscreen_restarts_itself():
    """ロック画面 QML が、画面点灯で自ら非対話認証を起こす版か。

    plasma-mobile に当てた自前パッチ（plasma-mobile-lockscreen-face-auth.patch）
    が入っていれば、このサービスが Enter を注入する理由は無い。注入すると
    空 PIN の失敗と二重の認証を作る。

    **パッケージの版ではなく、実際に置かれているファイルの中身を見る。**
    更新でパッチが外れた瞬間から、このサービスは従来どおり注入に戻る。
    人の手を介さず、劣化もしない。毎回読むのは、更新が当たった直後にも
    正しく切り替わるため（ファイルは 10KB 程度）。
    """
    try:
        with open(LOCKSCREEN_QML, "rb") as f:
            return LOCKSCREEN_MARK in f.read()
    except Exception:
        return False
RESUME_GRACE = 15.0        # 復帰からこの秒数以内なら、自力で始めるかを見届ける
RESUME_WATCH = 5.0         # 見届ける長さ
WATCH_INTERVAL = 0.2


def _just_resumed():
    """サスペンドから復帰した直後か。

    印は systemd-sleep のフックが復帰時に書く（pam_howdy の resume_delay と
    同じもの）。
    """
    try:
        return time.time() - float(open(RESUME_MARK).read().strip()) < RESUME_GRACE
    except Exception:
        return False


def _is_compare_arg(arg):
    """この argv 要素が compare.py そのものを指しているか。

    **cmdline に部分一致させてはいけない。** シェルの `-c` は長い文字列を
    argv 1 個として持つため、たまたま "howdy/compare.py" という語を含む
    コマンド（診断スクリプトなど）に一致してしまう。この計画では
    `pgrep -f` の自己一致で何度も誤検出している。

    argv の要素が丸ごとパスであることを求め、空白を含むものは除く。
    """
    return (arg.endswith(b"/howdy/compare.py")
            and not any(ws in arg for ws in (b" ", b"\t", b"\n")))


def _auth_running():
    """顔認証が動き出したか。

    発光体が点いているか、compare.py が走っていれば動いている。
    外部プロセスは起動しない（sysfs と /proc を読むだけ）。
    """
    for path in glob.glob("/sys/class/leds/*ir_illuminator*/brightness"):
        try:
            if int(open(path).read().strip()) > 0:
                return True
        except Exception:
            pass
    try:
        entries = os.listdir("/proc")
    except Exception:
        return False
    for name in entries:
        if not name.isdigit():
            continue
        try:
            with open("/proc/%s/cmdline" % name, "rb") as f:
                argv = f.read().split(b"\0")
        except Exception:
            continue
        if any(_is_compare_arg(a) for a in argv):
            return True
    return False


def _reason_to_skip(seconds):
    """復帰直後の点灯で、注入を見送るべきか数秒見届ける。

    見送る理由を返す。注入すべきなら None。

    ## なぜ「待って見る」のか

    復帰の経路では、こちらが何もしなくても認証が始まることがある。
    そこへ Enter を注入すると対話型の認証が余計に 1 回失敗し、認証が
    二重に走る（2026-09-06 07:49 の事故）。

    しかし**自力で始まるのは、グリーターが復帰後に作られたときだけ**である。
    既にプロンプトを出したグリーターが残っていると、復帰しても始まらない。
    実測（2026-09-06）:

        時刻      復帰後にグリーターが新規か   自力で始まったか
        08:00     はい（新規ロック）           はい（0.1 秒後）
        12:52     いいえ（11:49 から継続）     いいえ
        17:30     いいえ                       いいえ
        18:18:05  いいえ                       いいえ
        18:18:23  いいえ                       いいえ

    当初は「復帰から 30 秒は注入しない」と時間だけで決めていたが、上の
    5 件のうち 4 件で誤って抑止し、認証が始まらないまま放置された。
    12:52 では利用者が画面に触れるまで 10 秒間なにも起きていない。

    **予測をやめ、実際に始まるかを見届けてから決める。** 復帰経路は
    "waiting 1-2s for display" と表示を待ってから撮影に入るので、点灯から
    発光まで実測 2.0-2.7 秒かかる。5 秒あれば足りる。
    """
    deadline = time.time() + seconds
    while time.time() < deadline:
        if _auth_running():
            return "自力で認証が始まった"
        if display_on() is False:
            return "見ている間に画面が消えた"
        time.sleep(WATCH_INTERVAL)
    return None


def log(msg):
    print(msg, flush=True)


def _mark_injected():
    """Enter を送った時刻を残す。

    空 PIN の送信は対話型の認証を 1 回失敗させ、kscreenlocker はその後
    約 2.3 秒（実測 2236-2343ms）のあいだ認証を受け付けなくなる。
    顔認証の成功がこの窓に落ちると解除できず、グリーターが終了も解錠も
    できない状態になる（KDE Bug 515299）。

    compare.py はこの印を見て、注入があったときだけ成功の報告を遅らせる。
    サスペンド復帰の経路には注入も失敗遅延も無いので、そちらは待たせない。
    """
    try:
        with open(INJECT_MARK, "w") as f:
            f.write(str(time.time()))
    except Exception:
        pass


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
        if _lockscreen_restarts_itself():
            log("画面が点灯（消灯 %.1f 秒）。ロック画面が自ら認証を起こす版なので"
                "注入しない" % off_for)
            last_fire = time.time()
            continue
        if _just_resumed():
            skip = _reason_to_skip(RESUME_WATCH)
            if skip:
                log("画面が点灯（消灯 %.1f 秒）。%s ので注入しない"
                    % (off_for, skip))
                last_fire = time.time()
                continue
            log("画面が点灯（消灯 %.1f 秒）。復帰直後だが %.0f 秒待っても"
                "始まらないので注入します" % (off_for, RESUME_WATCH))
        else:
            log("画面が点灯（消灯 %.1f 秒）。ロック中なので認証を起こします"
                % off_for)
        last_fire = time.time()
        try:
            send_enter()
            _mark_injected()
        except Exception as err:
            log("注入に失敗: %s" % err)


if __name__ == "__main__":
    main()
