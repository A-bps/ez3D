#!/usr/bin/env bash
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
export UV_PYTHON_INSTALL_DIR=/opt/ez3d/python
export HF_HOME=/opt/ez3d/cache/huggingface
export PIP_NO_CACHE_DIR=1
export CUDA_HOME=/usr/local/cuda-12.4
export PATH="$CUDA_HOME/bin:$PATH"
export LD_LIBRARY_PATH="$CUDA_HOME/lib64:/usr/lib/wsl/lib:${LD_LIBRARY_PATH:-}"
export TORCH_CUDA_ARCH_LIST=8.6
export MAX_JOBS=4
mkdir -p /opt/ez3d /opt/ez3d/cache
echo 'Preparing isolated Python and native build tools'
apt-get update -qq
apt-get install -y --no-install-recommends python3-venv build-essential git ninja-build pkg-config libjpeg-dev libgl1 libglib2.0-0 curl ca-certificates
python3 -m venv /opt/ez3d/bootstrap
/opt/ez3d/bootstrap/bin/pip install --no-cache-dir uv
[ -x /opt/ez3d/venv/bin/python ] || /opt/ez3d/bootstrap/bin/uv venv --seed --python 3.11 /opt/ez3d/venv
/opt/ez3d/bootstrap/bin/uv pip install --python /opt/ez3d/venv/bin/python pip setuptools wheel
if [ "$(df --output=avail -B1 /opt/ez3d | tail -1)" -lt 42949672960 ]; then
 echo 'At least 40 GiB of free WSL storage is required to build the TRELLIS runtime.' >&2
 exit 2
fi
if [ ! -f /etc/apt/sources.list.d/cuda-wsl-ubuntu-x86_64.list ]; then
 curl -fL https://developer.download.nvidia.com/compute/cuda/repos/wsl-ubuntu/x86_64/cuda-keyring_1.1-1_all.deb -o /opt/ez3d/cuda-keyring.deb
 dpkg -i /opt/ez3d/cuda-keyring.deb
 apt-get update -qq
fi
apt-get install -y --no-install-recommends cuda-nvcc-12-4 cuda-cudart-dev-12-4 cuda-cccl-12-4
source /opt/ez3d/venv/bin/activate
pip install --no-cache-dir --upgrade pip wheel 'setuptools<81'
pip install --no-cache-dir torch==2.6.0 torchvision==0.21.0 --index-url https://download.pytorch.org/whl/cu124 --extra-index-url https://pypi.org/simple
pip install --no-cache-dir --no-deps xformers==0.0.29.post2 --index-url https://download.pytorch.org/whl/cu124
pip install --no-cache-dir imageio imageio-ffmpeg tqdm easydict opencv-python-headless ninja trimesh 'transformers==4.57.3' tensorboard pandas lpips zstandard pillow kornia timm huggingface_hub 'numpy<2'
pip install --no-cache-dir git+https://github.com/EasternJournalist/utils3d.git@9a4eb15e4021b67b12c460c7057d642626897ec8
export CPATH="$(find /opt/ez3d/venv/lib/python3.11/site-packages/nvidia -type d -name include | paste -sd:):${CPATH:-}"
mkdir -p /opt/ez3d/extensions
for entry in 'CuMesh https://github.com/JeffreyXiang/CuMesh.git' 'FlexGEMM https://github.com/JeffreyXiang/FlexGEMM.git' 'nvdiffrast https://github.com/NVlabs/nvdiffrast.git'; do
 read -r name url <<< "$entry"
 [ -d "/opt/ez3d/extensions/$name/.git" ] || git clone --recursive "$url" "/opt/ez3d/extensions/$name"
 pip install "/opt/ez3d/extensions/$name" --no-build-isolation --no-cache-dir
done
pip install /opt/ez3d/TRELLIS.2/o-voxel --no-build-isolation --no-cache-dir
python -c 'import torch,cumesh,flex_gemm,nvdiffrast,o_voxel; print("TRELLIS native runtime ready", torch.cuda.get_device_name())'
echo 'Runtime ready; model weights are fetched separately and only for inference.'
