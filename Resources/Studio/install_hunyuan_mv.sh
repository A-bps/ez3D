#!/usr/bin/env bash
# Instala o Hunyuan3D-2 (multivista) em ambiente SEPARADO do TRELLIS.2.
# Nada do codigo da Tencent e distribuido pelo ez3D: tudo e baixado da fonte oficial.
# Licenca: Tencent Hunyuan 3D 2.0 Community License (vale fora da UE, Reino Unido e Coreia do Sul).
set -euo pipefail
ROOT="${1:-/opt/ez3d-hunyuan}"
export CUDA_HOME=/usr/local/cuda-12.4
export PATH="$CUDA_HOME/bin:$PATH"
export LD_LIBRARY_PATH="$CUDA_HOME/lib64:/usr/lib/wsl/lib:${LD_LIBRARY_PATH:-}"
export TORCH_CUDA_ARCH_LIST="${TORCH_CUDA_ARCH_LIST:-$(/usr/lib/wsl/lib/nvidia-smi --query-gpu=compute_cap --format=csv,noheader | head -1)}"

export UV_PYTHON_INSTALL_DIR=/opt/ez3d/python
apt-get install -y --no-install-recommends libopengl0 libegl1 libgl1 libglib2.0-0
mkdir -p "$ROOT"
cd "$ROOT"
[ -d Hunyuan3D-2 ] || git clone --depth 1 https://github.com/Tencent-Hunyuan/Hunyuan3D-2.git
[ -x venv/bin/python ] || /opt/ez3d/bootstrap/bin/uv venv --seed --python 3.11 venv
source venv/bin/activate
pip install -q pip setuptools wheel
pip install --no-cache-dir torch==2.6.0 torchvision==0.21.0 --index-url https://download.pytorch.org/whl/cu124 --extra-index-url https://pypi.org/simple
export CPATH="$(find "$ROOT/venv/lib/python3.11/site-packages/nvidia" -type d -name include | paste -sd:):${CPATH:-}"
cd Hunyuan3D-2
pip install --no-cache-dir -r requirements.txt
pip install --no-cache-dir -e .
pip install --no-cache-dir "transformers==4.49.0" "diffusers==0.32.2" "huggingface_hub<1.0"
( cd hy3dgen/texgen/custom_rasterizer && python setup.py install )
( cd hy3dgen/texgen/differentiable_renderer && python setup.py install )
echo "Hunyuan3D-2 instalado em $ROOT"
