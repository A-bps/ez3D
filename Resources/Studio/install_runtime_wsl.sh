#!/usr/bin/env bash
set -euo pipefail

APP_ROOT="${1:?ez3D install root required}"
MODEL_ROOT="${2:?ez3D model root required}"
APP_WSL="$(wslpath -a "$APP_ROOT")"
MODELS_WSL="$(wslpath -a "$MODEL_ROOT")"
SKIN_WSL="$(wslpath -a "$APP_ROOT/Runtime/SkinTokens")"
APP="$APP_WSL/Resources/Studio"

export DEBIAN_FRONTEND=noninteractive
mkdir -p /opt/ez3d
if ! nvidia-smi >/dev/null 2>&1; then
  echo 'NVIDIA GPU is not visible inside WSL. Install/update the Windows NVIDIA driver and retry.' >&2
  exit 3
fi
if [ "$(df --output=avail -B1 /opt/ez3d | tail -1)" -lt 42949672960 ]; then
  echo 'At least 40 GiB of free WSL storage is required for TRELLIS build files.' >&2
  exit 4
fi
apt-get update -qq
apt-get install -y --no-install-recommends python3-venv build-essential git ninja-build pkg-config libjpeg-dev libgl1 libglib2.0-0 curl ca-certificates libsm6 libxrender1 libxext6 libgomp1 libusb-1.0-0
if [ ! -d /opt/ez3d/TRELLIS.2/.git ]; then
  git clone --recursive https://github.com/microsoft/TRELLIS.2.git /opt/ez3d/TRELLIS.2
fi
if [ ! -d "$SKIN_WSL/.git" ]; then
  git clone https://github.com/VAST-AI-Research/SkinTokens.git "$SKIN_WSL"
fi
bash "$APP/install_trellis.sh"
bash "$APP/install_skintokens.sh" "$SKIN_WSL"
bash "$APP/install_kimodo.sh" "$MODELS_WSL/Kimodo"
echo 'GPU runtimes, rigging, and motion generation are ready.'
