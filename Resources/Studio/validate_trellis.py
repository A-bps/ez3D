"""Validate model loading in isolation, releasing all allocations on exit."""
import os
import sys
from pathlib import Path
os.environ['ATTN_BACKEND']='sdpa'
os.environ['SPARSE_ATTN_BACKEND']='xformers'
os.environ['CUDA_HOME']='/usr/local/cuda-12.4'
os.environ['PATH']='/usr/local/cuda-12.4/bin:'+os.environ.get('PATH','')
os.environ['HF_HUB_OFFLINE']='1'
sys.path.insert(0,'/opt/ez3d/TRELLIS.2')
import torch
import cumesh
import flex_gemm
import nvdiffrast.torch
import o_voxel
from trellis2.pipelines import Trellis2ImageTo3DPipeline
assert torch.cuda.is_available()
model=Path(sys.argv[1]) if len(sys.argv)>1 else Path('/mnt/b/ez3D/Models/TRELLIS.2')
pipeline=Trellis2ImageTo3DPipeline.from_pretrained(str(model),config_file='pipeline-local.json')
assert len(pipeline.models)==8
pipeline.cuda()
print('Pesos e dependências do TRELLIS.2 carregados.',flush=True)
