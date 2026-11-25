#!/usr/bin/env bash
set -euo pipefail
export CUDA_HOME=/usr/local/cuda-12.4
export PATH="$CUDA_HOME/bin:$PATH"
export MAX_JOBS=4
if [ "$(id -u)" = 0 ]; then
  apt-get update -qq
  apt-get install -y -qq libsm6 libxrender1 libxext6 libgl1 libgomp1 libusb-1.0-0
fi
UV=/opt/ez3d/bootstrap/bin/uv
ENV=/opt/ez3d/unirig-venv
if [ ! -x "$ENV/bin/python" ]; then
  "$UV" venv --seed --python /opt/ez3d/venv/bin/python "$ENV"
  # Reuse the existing CUDA/Torch runtime without changing TRELLIS dependencies.
  printf '/opt/ez3d/venv/lib/python3.11/site-packages\n' > "$ENV/lib/python3.11/site-packages/ez3d-torch-runtime.pth"
fi
printf 'torch==2.6.0+cu124\ntorchvision==0.21.0+cu124\n' > /opt/ez3d/unirig-constraints.txt
"$ENV/bin/python" -m pip install --no-cache-dir -c /opt/ez3d/unirig-constraints.txt \
  'transformers==4.51.3' python-box einops omegaconf pytorch_lightning lightning \
  addict timm fast-simplification 'bpy==4.2.0' trimesh open3d pyrender huggingface_hub wandb \
  'numpy==1.26.4' 'spconv-cu124'
"$ENV/bin/python" -m pip install --no-cache-dir --no-deps \
  torch_scatter torch_cluster --find-links 'https://data.pyg.org/whl/torch-2.6.0+cu124.html'
"$ENV/bin/python" -m pip install --no-cache-dir --no-deps \
  'https://github.com/Dao-AILab/flash-attention/releases/download/v2.7.4.post1/flash_attn-2.7.4.post1%2Bcu12torch2.6cxx11abiFALSE-cp311-cp311-linux_x86_64.whl'
"$ENV/bin/python" -c 'import torch,bpy,transformers,spconv,torch_scatter,torch_cluster; from flash_attn.modules.mha import MHA; print("UniRig dependencies ready",torch.__version__,transformers.__version__)'
mkdir -p /opt/ez3d/UniRig/experiments
for component in skeleton skin; do
  destination="/opt/ez3d/UniRig/experiments/$component"
  if [ ! -e "$destination" ]; then
    ln -s "/mnt/b/ez3D/Models/UniRig/$component" "$destination"
  fi
done
