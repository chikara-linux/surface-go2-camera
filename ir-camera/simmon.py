#!/usr/bin/env python3
"""登録済みモデルとの一致度と、網膜反射の強さをリアルタイム表示する。

顔認証の閾値を決めるための測定ツール。遮蔽・閉眼・視線外しなどの条件で
実際の数値がどう動くかを見る。他人に協力してもらえば他人受入率も測れる。

    sudo python3 simmon.py [ユーザー名] [秒数] [ゲイン固定値]
    SIMMON_QUIET=1 を付けると統計だけを出す
"""
import sys, os, json, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, "/usr/lib/x86_64-linux-gnu/howdy")
import numpy as np, cv2
import paths_factory
from ipu3_ir_reader import IPU3IRReader

user = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("SUDO_USER") or os.environ.get("USER")
secs = float(sys.argv[2]) if len(sys.argv) > 2 else 30
fixed_gain = int(sys.argv[3]) if len(sys.argv) > 3 else 0
quiet = os.environ.get("SIMMON_QUIET") == "1"
debug = os.environ.get("SIMMON_DEBUG") == "1"

models = json.load(open(paths_factory.user_model_path(user)))
enc = [np.array(e, dtype=np.float32) for m in models for e in m["data"]]
det = cv2.FaceDetectorYN.create(paths_factory.face_detector_path(), "", (640, 480),
                                score_threshold=0.7)
rec = cv2.FaceRecognizerSF.create(paths_factory.face_recognizer_path(), "")


def eye_reflection(gray, face):
    """網膜反射の強さを両目について返す。

    赤外線が瞳孔から入って網膜で反射しカメラへ戻る（bright pupil effect）。
    発光体がカメラと同軸にあるため明瞭に出る。目が開いていて、かつカメラを
    見ているときだけ現れるので、開眼と注視の判定に使える。
    顔領域の 75 パーセンタイルを基準に、目の位置の輝点の突出量を返す。
    """
    x, y, w, h = (int(v) for v in face[:4])
    lm = np.asarray(face[4:14], dtype=np.float32).reshape(5, 2)
    H, W = gray.shape
    x0, y0 = max(0, x), max(0, y)
    roi = gray[y0:min(H, y + h), x0:min(W, x + w)]
    if roi.size == 0:
        return 0.0, 0.0, 0.0
    base = float(np.percentile(roi, 75))
    r = max(3, int(w * 0.05))
    peaks = []
    for i in (0, 1):
        cx, cy = int(lm[i][0]), int(lm[i][1])
        p = gray[max(0, cy - r):min(H, cy + r + 1), max(0, cx - r):min(W, cx + r + 1)]
        # 目の周辺での「局所的な突出」を見る。顔全体が飽和していても効くよう
        # 目パッチ内の中央値との差を使う。
        if p.size:
            peaks.append(float(p.max()) - float(np.median(p)))
        else:
            peaks.append(0.0)
    return peaks[0], peaks[1], base


cam = IPU3IRReader(stack=3)
if fixed_gain:
    cam._auto_expose = lambda *a, **k: None
    cam.gain, cam.exposure = fixed_gain, 800
    cam._apply_ctrl()
if not quiet:
    print(f"登録モデル {len(models)}個 / 特徴量 {len(enc)}個")
    print(f"発光体: {'点灯' if cam.illuminated else '消灯'}")
    print(f"\n{'秒':>5} {'検出確信':>8} {'一致度':>8}  {'目(右/左)':>12}  判定")

LED = "/sys/class/leds/tps68470::ir_illuminator/brightness"
def rearm():
    try:
        with open(LED, "w") as f:
            f.write("3" if cam.illuminated else "0")
    except Exception:
        pass

t0 = last = time.time()
sims, eyes, nodet = [], [], 0
while time.time() - t0 < secs:
    if time.time() - last > 8:
        rearm(); last = time.time()
    ok, frame = cam.read()
    if not ok:
        break
    g = frame[:, :, 0]
    det.setInputSize((640, 480))
    _, fa = det.detect(frame)
    if fa is None:
        nodet += 1
        if not quiet:
            print(f"{time.time()-t0:5.1f} {'-':>8} {'-':>8}  {'-':>12}  顔なし")
        continue
    f = fa[0]
    er, el, base = eye_reflection(g, f)
    weak = min(er, el)
    eyes.append(weak)
    e = rec.feature(rec.alignCrop(frame, f))
    best = max(float(rec.match(e, s.reshape(1, -1), cv2.FaceRecognizerSF_FR_COSINE))
               for s in enc)
    sims.append(best)
    if not quiet:
        extra = f"  [顔基準{base:5.0f} 飽和{(g>=254).mean()*100:4.1f}%]"
        print(f"{time.time()-t0:5.1f} {float(f[-1]):8.3f} {best:8.3f}  "
              f"{er:+5.0f}/{el:+5.0f}  {'一致' if best>=0.70 else '不一致'}{extra}")
cam.release()

tot = len(sims) + nodet
if sims:
    a, ey = np.array(sims), np.array(eyes)
    print(f"検出 {len(a)}/{tot} ({len(a)/tot*100:.0f}%)  "
          f"一致度 平均{a.mean():.3f} 最小{a.min():.3f} 最大{a.max():.3f}  "
          f"| 0.70超 {(a>=0.70).mean()*100:3.0f}%  0.85超 {(a>=0.85).mean()*100:3.0f}%")
    print(f"  網膜反射(弱い方の目): 平均{ey.mean():6.1f} 最小{ey.min():6.1f} "
          f"最大{ey.max():6.1f}  | 5超 {(ey>=5).mean()*100:3.0f}%  "
          f"15超 {(ey>=15).mean()*100:3.0f}%  30超 {(ey>=30).mean()*100:3.0f}%")
else:
    print(f"検出 0/{tot} — 一度も顔を検出せず")
