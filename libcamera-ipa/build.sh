#!/bin/bash
# 改造した IPU3 の IPA（ipa_ipu3.so）だけを作る。システムには何も入れない。
#
# 要るもの: sudo apt install --no-install-recommends meson libyaml-dev python3-ply python3-jinja2
# 所要: Surface Go 2 で 20 分前後
#
# 使い方: ./build.sh            … 入っている libcamera と同じ版のソースで作る
# できたもの: src/libcamera-<版>/build/src/ipa/ipu3/ipa_ipu3.so
set -eu
export LC_ALL=C.UTF-8
HERE="$(cd "$(dirname "$0")" && pwd)"
VER=$(dpkg-query -W -f='${Version}' libcamera0.7 2>/dev/null || dpkg-query -W -f='${Version}' 'libcamera0.*' | head -1)
echo "== 入っている libcamera: $VER =="
mkdir -p "$HERE/src" && cd "$HERE/src"
rm -rf libcamera-*/
apt-get source "libcamera=$VER"
S=$(ls -d "$HERE"/src/libcamera-*/ | head -1); cd "$S"
# IPA の中身の変更（ToneMapping がチューニングの gamma を読む）
patch -p1 < "$HERE/ipu3-tone-mapping-gamma.patch"
# ビルドだけの変更（GLES2 の開発ファイルが無い環境で egl.cpp を作らない）
patch -p1 < "$HERE/build-without-gles2.patch"
meson setup build --buildtype=release -Dwerror=false \
  -Dpipelines=ipu3 -Dipas=ipu3 \
  -Dcam=disabled -Dqcam=disabled -Dgstreamer=disabled -Ddocumentation=disabled \
  -Dtest=false -Dlc-compliance=disabled -Dpycamera=disabled -Dv4l2=false -Dtracing=disabled
systemd-inhibit --what=idle:sleep --why="libcamera IPA build" ninja -C build src/ipa/ipu3/ipa_ipu3.so
echo "$VER" > build/src/ipa/ipu3/BUILT_AGAINST
echo "== できました: $S/build/src/ipa/ipu3/ipa_ipu3.so（libcamera $VER 向け）=="
