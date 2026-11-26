#!/usr/bin/env bash
set -euo pipefail

SKIN_TOKENS="${1:?Windows-side SkinTokens folder required}"
UV=/opt/ez3d/bootstrap/bin/uv
ENV=/opt/ez3d/skintokens-venv
export CUDA_HOME=/usr/local/cuda-12.4
export PATH="$CUDA_HOME/bin:$PATH"
export MAX_JOBS=4
export TORCH_CUDA_ARCH_LIST="$(/usr/lib/wsl/lib/nvidia-smi --query-gpu=compute_cap --format=csv,noheader | head -1)"

if [ ! -x "$ENV/bin/python" ]; then
  "$UV" venv --seed --python /opt/ez3d/venv/bin/python "$ENV"
  printf '/opt/ez3d/venv/lib/python3.11/site-packages\n' > "$ENV/lib/python3.11/site-packages/ez3d-torch-runtime.pth"
fi
printf 'torch==2.6.0+cu124\ntorchvision==0.21.0+cu124\n' > /opt/ez3d/skintokens-constraints.txt
"$ENV/bin/python" -m pip install --no-cache-dir -c /opt/ez3d/skintokens-constraints.txt \
  'transformers==4.51.3' python-box einops omegaconf pytorch_lightning lightning \
  addict timm fast-simplification 'bpy==4.2.0' trimesh open3d pyrender huggingface_hub wandb \
  'numpy==1.26.4' 'spconv-cu124'
"$ENV/bin/python" -m pip install --no-cache-dir --no-deps \
  torch_scatter torch_cluster --find-links 'https://data.pyg.org/whl/torch-2.6.0+cu124.html'
"$ENV/bin/python" -m pip install --no-cache-dir --no-deps \
  'https://github.com/Dao-AILab/flash-attention/releases/download/v2.7.4.post1/flash_attn-2.7.4.post1%2Bcu12torch2.6cxx11abiFALSE-cp311-cp311-linux_x86_64.whl'
"$ENV/bin/python" -c 'import torch,bpy,transformers,spconv,torch_scatter,torch_cluster; from flash_attn.modules.mha import MHA; print("TokenRig dependencies ready",torch.__version__,transformers.__version__)'
cd "$SKIN_TOKENS"
"$ENV/bin/python" download.py --model
echo 'TokenRig and SkinTokens weights are installed.'
