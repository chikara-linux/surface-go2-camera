#!/usr/bin/env python3
"""赤外線発光体が点いた瞬間を記録し続ける。再現待ちのための常時観測。

## なぜ要るのか

「カメラが二回起動する」現象が偶発的に起き、いったん起きると再起動まで
毎回続くという報告がある。再現手順が定まらないため、起きたときの記録が
残っていないと原因を特定できない。

## 何をするのか

発光体の brightness を 100ms ごとに読み、0 から変化した瞬間だけを記録する。
そのとき動いている compare.py とグリーターの PID も添える。

## 軽さについて

**外部プロセスを一切起動しない。** sysfs と /proc を直接読むだけ。
以前 /proc の全エントリに対して tr を起動する観測スクリプトを回したところ、
毎秒数千プロセスを生成して PID が 40 万も進んだ。観測が別の異常を生んでは
本末転倒なので、この実装では 100ms ごとにファイルを 1 つ読むだけにしてある。
"""
import os
import sys
import time

LED = "/sys/class/leds/tps68470::ir_illuminator/brightness"
LOG = os.path.expanduser("~/.local/state/irwatch.log")
INTERVAL = 0.1
MAX_LINES = 5000          # これを超えたら古い行から捨てる


def read_led():
    try:
        with open(LED) as f:
            return int(f.read().strip())
    except Exception:
        return None


def scan_procs():
    """compare.py とグリーターの PID を返す。外部プロセスは使わない。"""
    compare, greeter = [], []
    try:
        entries = os.listdir("/proc")
    except Exception:
        return compare, greeter
    for name in entries:
        if not name.isdigit():
            continue
        try:
            with open("/proc/%s/cmdline" % name, "rb") as f:
                cmd = f.read().replace(b"\0", b" ").decode("utf-8", "replace")
        except Exception:
            continue
        if "howdy/compare.py" in cmd:
            compare.append(name)
        elif "libexec/kscreenlocker_greet" in cmd:
            greeter.append(name)
    return compare, greeter


def marks():
    """印の鮮度。負の値なら時計が巻き戻っている。"""
    out = []
    base = "/run/user/%d/" % os.getuid()
    for name in ("howdy-wake-injected", "howdy-last-success"):
        try:
            age = time.time() - float(open(base + name).read())
            out.append("%s=%.1fs" % (name.split("-")[-1], age))
        except Exception:
            out.append("%s=なし" % name.split("-")[-1])
    return " ".join(out)


def write(line):
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        lines = []
        if os.path.exists(LOG):
            with open(LOG) as f:
                lines = f.readlines()[-(MAX_LINES - 1):]
        lines.append(line + "\n")
        with open(LOG, "w") as f:
            f.writelines(lines)
    except Exception:
        pass


def main():
    prev = read_led()
    if prev is None:
        sys.exit("発光体が見つかりません。モジュールが読み込まれていない可能性があります。")
    write("%s 観測を開始（発光体=%s）" % (time.strftime("%m-%d %H:%M:%S"), prev))

    lit_since = None
    while True:
        time.sleep(INTERVAL)
        cur = read_led()
        if cur is None or cur == prev:
            continue

        ts = time.strftime("%m-%d %H:%M:%S") + ".%03d" % int((time.time() % 1) * 1000)
        if prev == 0 and cur > 0:                      # 消灯 → 点灯
            lit_since = time.time()
            c, g = scan_procs()
            write("%s 点灯(%d)  compare=[%s] greeter=[%s]  %s"
                  % (ts, cur, ",".join(c), ",".join(g), marks()))
        elif cur == 0 and prev > 0:                    # 点灯 → 消灯
            dur = (time.time() - lit_since) if lit_since else 0.0
            write("%s 消灯      点灯していた時間 %.1f 秒" % (ts, dur))
            lit_since = None
        prev = cur


if __name__ == "__main__":
    main()
