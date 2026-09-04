# Intel IPU3 経由の IR カメラ (OV7251) を OpenCV 互換の顔で読み出すリーダー
#
# 背景 (investigation/hikitugi §9-4):
#   Surface Go 2 の IR カメラは libcamera から見えない。ipu3 パイプラインハンドラが
#   `id < 4 && numCameras < 2` で走査を打ち切るため、3本目のセンサーに到達しない。
#   V4L2 を直接叩けば取得できるが、次の3点を誰かが吸収する必要がある。
#     1. media-ctl でリンクとフォーマットを毎回設定する (再起動で消える)
#     2. 形式が ip3y = 10bit モノクロ IPU3 パック。OpenCV は解釈できない
#     3. 自動露出が無い。既定のゲイン 16 では真っ黒 (平均 17/1023)
#   このクラスがその3点を担い、cv2.VideoCapture と同じ grab/read/release を提供する。

import glob
import os
import re
import atexit
import subprocess
import time
from collections import deque

import cv2
import numpy as np

MBUS = "Y10_1X10"
PIXFMT = "ip3y"


def _tool(name):
    """外部コマンドを絶対パスで解決する。

    PAM 経由で起動されると PATH は /usr/local/bin:/usr/bin:/bin に制限される。
    i2ctransfer は /usr/sbin にあるため、名前だけで呼ぶと PAM 下では
    見つからず、発光体が点灯しないまま暗い画像でタイムアウトする。
    """
    for d in ("/usr/bin", "/usr/sbin", "/bin", "/sbin",
              "/usr/local/bin", "/usr/local/sbin"):
        f = os.path.join(d, name)
        if os.path.exists(f):
            return f
    return name


MEDIA_CTL = _tool("media-ctl")
V4L2_CTL = _tool("v4l2-ctl")

# --- IR 発光体 -----------------------------------------------------------
# TPS68470 内蔵のフラッシュ LED ドライバを、独自のカーネルモジュール
# tps68470-irled が LED クラスデバイスとして公開している。
#
# 以前はここから /dev/i2c-2 に直接書いていたが、それには2つの問題があった。
#   1. root 権限が要る。画面ロッカー (kscreenlocker_greet) は非特権で動くため
#      PAM 経由では点灯できず、顔認証が必ず失敗していた
#   2. i2c への直接アクセスは PMIC を含む全チップを操作できてしまう。
#      実際、本開発中に直接書き込みで PMIC を停止させたことがある
# LED クラス経由なら「明るさを変える」権限だけを udev で開放でき、
# カーネル内で regmap のロックにより直列化されるので状態も壊れない。
ILLUM_SYSFS = "/sys/class/leds/tps68470::ir_illuminator/brightness"
ILLUM_MAX = 7
ILLUM_DEFAULT_CURRENT = 3


def _bytesperline(width):
    """IPU3 パック 10bit: 32バイト = 25画素。行は 64 バイト境界に切り上げ"""
    return ((width + 49) // 50) * 64


class IPU3IRReader:
    """OV7251 (IR) を V4L2 から直接読む。cv2.VideoCapture 互換の最小 API。"""

    def __init__(self, sensor="ov7251", width=640, height=480,
                 target=430, settle=8, verbose=False,
                 stack=12, enhance=True, vblank=None,
                 illuminate=True, illum_current=ILLUM_DEFAULT_CURRENT,
                 exp_cap=800):
        self.illuminated = False
        self.sensor_prefix = sensor
        self.width, self.height = width, height
        self.bpl = _bytesperline(width)
        self.frame_size = self.bpl * height
        # 点灯時は中央領域を測るので目標値が変わる。
        # 実測の最適点は 中央 140/255 前後（10bit で約 560）、飽和 5-8%。
        # そこで一致度 0.74-0.84。暗すぎる(中央52)と 0.48 まで落ちる。
        self.target = target          # 10bit スケールでの目標輝度
        self.target_lit = 560
        self.settle = settle          # AE が落ち着くまでの想定フレーム数
        self.verbose = verbose

        # ゲインが上限に張り付く暗所ではノイズで顔検出が破綻する。
        # 実測: 単フレームでは YuNet が検出 0、8枚平均で確信度 0.85、
        #       16枚平均 + CLAHE + ぼかしで 0.92。時間方向の平均化が決定打。
        # 発光体が点いていればノイズが少ないので平均は少なくてよい。
        # 平均枚数を減らすと被写体の動きによるブレにも強くなる。
        self._stack_req = max(1, stack)
        self.stack = self._stack_req
        # CLAHE とぼかしは「発光体なしの暗所」でのみ有効。
        # 点灯状態での実測では特徴量の再現性をむしろ下げた
        #   1枚: 0.899 → 0.884 /  3枚平均: 0.959 → 0.946
        # ノイズが無い画にコントラスト伸長をかけると、フレームごとの
        # ばらつきが増えるだけになる。点灯時は自動的に切る。
        self.enhance = enhance
        self._history = deque(maxlen=self.stack)
        self._clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))

        self._weights = (1 << np.arange(10)).astype(np.uint16)
        self._nblocks = self.bpl // 32

        # 露出とゲインの可動域はセンサーから読む
        self.exposure, self.gain = 500, 16
        self.exp_min = self.exp_max = self.gain_min = self.gain_max = None

        # 垂直ブランキングを伸ばすとフレーム長が延び、露出の上限が上がる。
        # 露出を稼げればゲインを下げられるため、暗所ではノイズも同時に減る。
        # フレームレートは (480 + vblank) に反比例して落ちる。
        self.vblank = vblank

        self.exp_cap = exp_cap      # 点灯時の露出上限（ブレ対策）
        self._settle = 0            # AE 変更後の反映待ちフレーム数
        self.proc = None
        self.frames_read = 0
        self.last_mean = 0.0

        self._discover()
        # 発光体はセンサー起動前に点けておく（起動後だと I2C が競合してブロックする）
        if illuminate:
            self.illuminator_on(illum_current)
            atexit.register(self.illuminator_off)
        if self.illuminated and self._stack_req > 3:
            # 点灯していればノイズが少ないので平均は 3 枚で足りる。
            # 枚数が少ないほど被写体の動きによるブレにも強い。
            self.stack = 3
            self._history = deque(maxlen=self.stack)
        self._setup_pipeline()
        self._read_ctrl_ranges()
        self._start()

    # -------------------------------------------------------------- 発光体

    def illuminator_on(self, current=None):
        """IR 発光体を点灯する。成功したら True。

        書き込むのは LED クラスの brightness ファイル 1 つだけ。
        udev で video グループに開放してあれば非特権でも動く。
        """
        v = ILLUM_DEFAULT_CURRENT if current is None else current
        v = max(0, min(ILLUM_MAX, int(v)))
        try:
            with open(ILLUM_SYSFS, "w") as f:
                f.write(str(v))
            self.illuminated = v > 0
        except Exception:
            # モジュール未読込や権限不足。暗所向けの処理へ自動的に落ちる
            self.illuminated = False
        return self.illuminated

    def illuminator_off(self):
        if not getattr(self, "illuminated", False):
            return
        try:
            with open(ILLUM_SYSFS, "w") as f:
                f.write("0")
        except Exception:
            pass
        self.illuminated = False

    # ------------------------------------------------------------------ 探索

    def _discover(self):
        """media トポロジから センサー / csi2 / cio2 の video ノードを特定する。
        エンティティ名も subdev 番号も起動ごとに変わりうるので毎回解決する。"""
        for md in sorted(glob.glob("/dev/media*")):
            try:
                out = subprocess.run([MEDIA_CTL, "-p", "-d", md],
                                     capture_output=True, text=True,
                                     timeout=10).stdout
            except Exception:
                continue
            if "ipu3-cio2" not in out:
                continue

            blocks = {}
            for blk in re.split(r"\n(?=- entity )", out):
                m = re.search(r"- entity \d+: (.+?) \(", blk)
                if not m:
                    continue
                name = m.group(1).strip()
                node = re.search(r"device node name (\S+)", blk)
                blocks[name] = (blk, node.group(1) if node else None)

            sensor = next((n for n in blocks if n.startswith(self.sensor_prefix)), None)
            if not sensor:
                continue

            # センサーを受けている csi2 を探す
            csi2 = None
            for name, (blk, _n) in blocks.items():
                if name.startswith("ipu3-csi2") and f'<- "{sensor}":0' in blk:
                    csi2 = name
                    break
            if not csi2:
                continue

            # その csi2 が繋がる cio2 の video ノード
            m = re.search(r'-> "(ipu3-cio2 \d+)":0', blocks[csi2][0])
            if not m or m.group(1) not in blocks:
                continue

            self.media = md
            self.sensor = sensor
            self.sensor_subdev = blocks[sensor][1]
            self.csi2 = csi2
            self.video = blocks[m.group(1)][1]
            return

        raise RuntimeError(
            f"{self.sensor_prefix} が media トポロジ上に見つかりません。"
            " カーネルがセンサーを認識しているか dmesg で確認してください。")

    # ------------------------------------------------------------ 事前設定

    def _mc(self, *args):
        subprocess.run([MEDIA_CTL, "-d", self.media, *args],
                       capture_output=True, timeout=10)

    def _setup_pipeline(self):
        self._mc("-l", f'"{self.sensor}":0 -> "{self.csi2}":0 [1]')
        fmt = f"[fmt:{MBUS}/{self.width}x{self.height}]"
        for pad in (f'"{self.sensor}":0', f'"{self.csi2}":0', f'"{self.csi2}":1'):
            self._mc("-V", f"{pad} {fmt}")

    def _read_ctrl_ranges(self):
        # vertical_blanking を「書く」と露出の上限が再計算される。
        # 書かないと probe 時の値のまま頭打ちになり、使える光を捨てることになる。
        vb = self.vblank if self.vblank is not None else 1244
        subprocess.run([V4L2_CTL, "-d", self.sensor_subdev,
                        f"--set-ctrl=vertical_blanking={vb}"],
                       capture_output=True, timeout=10)
        try:
            out = subprocess.run([V4L2_CTL, "-d", self.sensor_subdev, "--list-ctrls"],
                                 capture_output=True, text=True, timeout=10).stdout
        except Exception:
            out = ""
        for name, attr in (("exposure", "exp"), ("gain", "gain")):
            m = re.search(rf"^\s*{name}\s+0x\w+\s+\(int\)\s+:\s+min=(-?\d+)\s+max=(\d+)",
                          out, re.M)
            if m:
                setattr(self, f"{attr}_min", int(m.group(1)))
                setattr(self, f"{attr}_max", int(m.group(2)))
        # 読めなかった場合の保守的な既定値
        self.exp_min = self.exp_min or 1
        self.exp_max = self.exp_max or 1700
        self.gain_min = self.gain_min or 16
        self.gain_max = self.gain_max or 1023
        # 初期値。発光体が点いていればゲインは桁違いに小さくてよい。
        # 実測: 電流 0x02 / ゲイン 32 で平均 193、顔検出確信度 0.937。
        self.exposure = min(self.exp_max, 1200)
        self.gain = min(self.gain_max, 96 if self.illuminated else 256)
        if self.illuminated and self.exp_cap:
            self.exposure = min(self.exposure, self.exp_cap)
        self._apply_ctrl()

    def _apply_ctrl(self):
        subprocess.run([V4L2_CTL, "-d", self.sensor_subdev,
                        f"--set-ctrl=gain={self.gain}",
                        f"--set-ctrl=exposure={self.exposure}"],
                       capture_output=True, timeout=10)

    # -------------------------------------------------------------- 取り込み

    def _start(self):
        if self.proc:
            return
        self.proc = subprocess.Popen(
            [V4L2_CTL, "-d", self.video,
             f"--set-fmt-video=width={self.width},height={self.height},"
             f"pixelformat={PIXFMT}",
             "--stream-mmap", "--stream-to=-"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)

    def _read_exact(self, n):
        buf = bytearray()
        while len(buf) < n:
            chunk = self.proc.stdout.read(n - len(buf))
            if not chunk:
                return None
            buf += chunk
        return bytes(buf)

    def _unpack(self, raw):
        """ip3y (32バイト=25画素, LSB 先頭) を 10bit 値の 2次元配列へ"""
        f = np.frombuffer(raw, dtype=np.uint8).reshape(self.height, self.bpl)
        b = f[:, :self._nblocks * 32].reshape(self.height, -1, 32)
        bits = np.unpackbits(b, axis=2, bitorder="little")
        px = (bits[:, :, :250].reshape(self.height, -1, 10) * self._weights).sum(axis=2)
        return px.reshape(self.height, -1)[:, :self.width]

    # ------------------------------------------------------------------ AE

    def _ae_metric(self, px):
        """AE の制御量を返す。(測光値, 飽和率)

        発光体を使うと顔だけが明るく背景は真っ黒になる。全体平均で測ると
        黒い背景に引きずられて「まだ暗い」と判断し続け、ゲインが上がって
        顔が白飛びする（実測: 30秒でゲイン155・飽和11.8%・検出不能）。
        被写体のいる中央領域で測り、飽和率で頭を押さえる。
        """
        h, w = px.shape
        cen = px[int(0.20 * h):int(0.80 * h), int(0.25 * w):int(0.75 * w)]
        # 飽和率も中央領域で測る。フレーム全体で割ると、発光体で背景が真っ黒に
        # なるぶん飽和率が薄まり、顔が白飛びしていてもガードが発動しない。
        # 実測(近距離): 顔の32%が飽和していても全体基準では6.4%で閾値8%を下回り、
        # AE が不感帯に落ちて 20 フレーム全く動かなかった。中央基準なら 21%。
        sat = float((cen >= 1000).mean())
        return float(cen.mean()), sat

    def _auto_expose(self, mean10, sat=0.0):
        """露出を先に伸ばし、足りない分をゲインで補う。ゲインはノイズ源なので後回し。

        センサーは設定変更を数フレーム遅れて反映する。毎フレーム全量補正すると
        反映前に重ねて補正してしまい発振する（発光体を入れてゲインに余裕が
        できた途端に顕在化した）。そこで
          ・変更後は数フレーム AE を止めて反映を待つ
          ・補正は平方根で減衰させ、行き過ぎを防ぐ
        """
        if self._settle > 0:                 # 反映待ち
            self._settle -= 1
            return
        # 点灯時は露出を短く保つ。1704行はフレーム時間のほぼ全域(約33ms)で
        # 被写体の動きがブレになる。光は発光体で足りるのでゲインで補う。
        exp_ceiling = self.exp_cap if (self.illuminated and self.exp_cap) else self.exp_max
        mean10 = max(mean10, 1.0)
        # 飽和が進んだら測光値に関わらず落とす。白飛びは検出を殺す
        if sat > 0.08:
            ratio = 0.75
        else:
            ratio = (self.target_lit if self.illuminated else self.target) / mean10
            if 0.8 < ratio < 1.25:           # 十分近ければ触らない
                return
        ratio = ratio ** 0.5                 # 減衰。1回で寄せきらない
        ratio = max(0.5, min(2.0, ratio))
        total = self.exposure * self.gain * ratio
        exp = int(round(min(exp_ceiling, max(self.exp_min, total / self.gain_min))))
        gain = int(round(min(self.gain_max, max(self.gain_min, total / max(exp, 1)))))
        if (exp, gain) == (self.exposure, self.gain):
            return
        self.exposure, self.gain = exp, gain
        self._apply_ctrl()
        self._settle = 3                     # 反映されるまで触らない

    # ------------------------------------------------- cv2.VideoCapture 互換

    def grab(self):
        return self._read_exact(self.frame_size) is not None

    def read(self):
        raw = self._read_exact(self.frame_size)
        if raw is None:
            return False, None
        px = self._unpack(raw)
        if self.illuminated:
            metric, sat = self._ae_metric(px)
        else:
            metric, sat = float(px.mean()), 0.0
        self.last_mean = metric
        self.frames_read += 1
        self._auto_expose(metric, sat)

        gray = (px >> 2).astype(np.uint8)
        self._history.append(gray.astype(np.float32))

        # 直近フレームの移動平均でランダムノイズを落とす。
        # 被写体が動くとぶれるが、認証時は静止しているため実害は小さい。
        if len(self._history) > 1:
            gray = np.mean(self._history, axis=0).astype(np.uint8)
        if self.enhance and not self.illuminated:
            gray = cv2.GaussianBlur(self._clahe.apply(gray), (3, 3), 0)

        if self.verbose:
            print(f"  frame {self.frames_read:3d}  mean={self.last_mean:6.1f} "
                  f"exp={self.exposure:5d} gain={self.gain:5d} "
                  f"stack={len(self._history)}")
        # Howdy 側が BGR2GRAY をかけるので 3ch で返す
        return True, np.repeat(gray[:, :, None], 3, axis=2)

    @property
    def ready(self):
        """平均化バッファが満ちているか。満ちる前は検出精度が落ちる。"""
        return len(self._history) >= self.stack

    def release(self):
        self.illuminator_off()
        if self.proc:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=3)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass
            self.proc = None

    def set(self, prop, value):
        # 解像度はセンサー側で固定しているため設定は受け付けない
        return True

    def get(self, prop):
        # compare.py は CAP_PROP_FRAME_HEIGHT で縮小率を決める。
        # 0 を返すと `or 1` で高さ1と誤認され、縮小率が桁違いになるので必ず実値を返す。
        if prop == cv2.CAP_PROP_FRAME_WIDTH:
            return self.width
        if prop == cv2.CAP_PROP_FRAME_HEIGHT:
            return self.height
        return 0

    def __del__(self):
        try:
            self.release()
        except Exception:
            pass
