"""Isolated Linux GPU worker for Hunyuan3D-2mv (multiview). Exit releases the GPU."""
import gc
import json
import sys
from pathlib import Path

import torch
from PIL import Image

request = Path(sys.argv[1])
cfg = json.loads(request.read_text(encoding='utf-8'))
# Same progress file name the UI already reads.
progress = request.with_name('trellis-request.progress.json')
STAGES = 6


def phase(message, stage, fraction=None, detail=''):
    temporary = progress.with_suffix('.tmp')
    temporary.write_text(json.dumps({'message': message, 'detail': detail, 'stage_index': stage,
        'stage_count': STAGES, 'value': stage + (fraction or 0), 'max': STAGES,
        'measured': fraction is not None}, ensure_ascii=False), encoding='utf-8')
    try:
        temporary.replace(progress)
    except PermissionError:
        pass
    print(message, flush=True)


phase('Preparando as fotos', 0)
from hy3dgen.rembg import BackgroundRemover
from hy3dgen.shapegen import (DegenerateFaceRemover, FaceReducer, FloaterRemover,
                              Hunyuan3DDiTFlowMatchingPipeline)
from hy3dgen.texgen import Hunyuan3DPaintPipeline

remover = None
images = {}
for view in ('front', 'left', 'back', 'right'):
    if view not in cfg['images']:
        continue
    image = Image.open(cfg['images'][view]).convert('RGBA')
    if image.getextrema()[3][0] == 255:  # fully opaque: photo still has a background
        remover = remover or BackgroundRemover()
        image = remover(image.convert('RGB'))
    images[view] = image
if 'front' not in images:
    raise RuntimeError('A foto de frente é obrigatória.')

phase('Carregando o Hunyuan3D (na primeira vez baixa vários GB)', 1)
shape = Hunyuan3DDiTFlowMatchingPipeline.from_pretrained(
    'tencent/Hunyuan3D-2mv', subfolder='hunyuan3d-dit-v2-mv', variant='fp16')
phase(f'Criando a forma a partir de {len(images)} foto(s)', 2)
mesh = shape(image=images, num_inference_steps=int(cfg.get('steps', 50)),
             octree_resolution=int(cfg.get('octree', 380)), num_chunks=20000,
             generator=torch.manual_seed(int(cfg.get('seed', 42))), output_type='trimesh')[0]
del shape
gc.collect()
torch.cuda.empty_cache()

phase('Limpando a malha', 3)
mesh = DegenerateFaceRemover()(FloaterRemover()(mesh))
mesh = FaceReducer()(mesh, max_facenum=int(cfg.get('faces', 60000)))

phase('Pintando as texturas', 4)
paint = Hunyuan3DPaintPipeline.from_pretrained('tencent/Hunyuan3D-2', subfolder='hunyuan3d-paint-v2-0-turbo')
mesh = paint(mesh, image=list(images.values()))

phase('Exportando o modelo', 5)
output = Path(cfg['output'])
mesh.vertex_normals  # computed here so the GLB carries them
mesh.export(str(output), include_normals=True)
if not output.is_file() or output.stat().st_size < 1000:
    raise RuntimeError('Exportação GLB incompleta')
phase('Modelo e materiais exportados', 5, fraction=1)
