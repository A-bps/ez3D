#!/usr/bin/env bash
set -euo pipefail

ASSET_ROOT="${1:?Windows-side Kimodo models folder required}"
REPO=/opt/ez3d/Kimodo
REV=568b0253f346fbe369587c7dae73d58594a14c90
MODEL_ROOT="$(wslpath -a "$ASSET_ROOT")"

export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y --no-install-recommends git cmake ninja-build g++ pkg-config libvulkan-dev glslc spirv-headers glslang-tools spirv-tools
if [ ! -d "$REPO/.git" ]; then
  git clone https://github.com/localai-org/kimodo.cpp.git "$REPO"
fi
git -C "$REPO" fetch --all --tags
git -C "$REPO" checkout "$REV"
git -C "$REPO" submodule update --init --recursive
cmake -S "$REPO" -B "$REPO/build/release" -G Ninja \
  -DCMAKE_BUILD_TYPE=Release -DKIMODO_BUILD_TESTS=OFF -DKIMODO_ENABLE_VULKAN=ON
cmake --build "$REPO/build/release" --target kmd-generate --parallel 4
"/opt/ez3d/venv/bin/python" "$REPO/scripts/download_gguf_weights.py" \
  --output "$MODEL_ROOT" --model soma-rp-v1.1
test -x "$REPO/build/release/kmd-generate"
echo 'Kimodo runtime and SOMA RP model are installed.'
