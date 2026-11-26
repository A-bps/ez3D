#!/usr/bin/env bash
set -euo pipefail
source /opt/ez3d/venv/bin/activate
export CUDA_HOME=/usr/local/cuda-12.4
export PATH="$CUDA_HOME/bin:$PATH"
export LD_LIBRARY_PATH="$CUDA_HOME/lib64:/usr/lib/wsl/lib:${LD_LIBRARY_PATH:-}"
export TORCH_CUDA_ARCH_LIST="$(/usr/lib/wsl/lib/nvidia-smi --query-gpu=compute_cap --format=csv,noheader | head -1)" MAX_JOBS=4 PIP_NO_CACHE_DIR=1
export CPATH="$(find /opt/ez3d/venv/lib/python3.11/site-packages/nvidia -type d -name include | paste -sd:):${CPATH:-}"
for entry in 'CuMesh https://github.com/JeffreyXiang/CuMesh.git' 'FlexGEMM https://github.com/JeffreyXiang/FlexGEMM.git' 'nvdiffrast https://github.com/NVlabs/nvdiffrast.git'; do
 read -r name url <<< "$entry"
 [ -d "/opt/ez3d/extensions/$name/.git" ] || git clone --recursive "$url" "/opt/ez3d/extensions/$name"
 pip install "/opt/ez3d/extensions/$name" --no-build-isolation --no-cache-dir
done
pip install /opt/ez3d/TRELLIS.2/o-voxel --no-build-isolation --no-cache-dir
python -c 'import torch,cumesh,flex_gemm,nvdiffrast,o_voxel; print("TRELLIS native runtime ready", torch.cuda.get_device_name())'
