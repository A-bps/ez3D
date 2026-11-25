"""Isolated Linux GPU worker. Exit releases the entire generation allocation."""
import json
import os
from pathlib import Path
import sys
import time
import torch
os.environ['ATTN_BACKEND']='sdpa'
os.environ['SPARSE_ATTN_BACKEND']='xformers'
os.environ['CUDA_HOME']='/usr/local/cuda-12.4'
os.environ['PATH']='/usr/local/cuda-12.4/bin:'+os.environ.get('PATH','')
os.environ['TORCH_CUDA_ARCH_LIST']='8.6'
os.environ['MAX_JOBS']='4'
os.environ['PYTORCH_CUDA_ALLOC_CONF']='expandable_segments:True'
sys.path.insert(0,'/opt/ez3d/TRELLIS.2')
from PIL import Image
from trellis2.pipelines import Trellis2ImageTo3DPipeline
from trellis_loading import install
install()
import o_voxel

request=Path(sys.argv[1]);cfg=json.loads(request.read_text(encoding='utf-8'))
progress=request.with_suffix('.progress.json')
stage_index=0
started=time.monotonic();measurements=[]
def phase(message, stage=None, fraction=None, detail=''):
    global stage_index
    if stage is not None: stage_index=max(stage_index,stage)
    temporary=progress.with_suffix('.tmp')
    temporary.write_text(json.dumps({'message':message,'detail':detail,'stage_index':stage_index,
        'stage_count':cfg.get('progress_stages',6),'value':stage_index+(fraction or 0),'max':cfg.get('progress_stages',6),
        'measured':fraction is not None},ensure_ascii=False),encoding='utf-8')
    try:temporary.replace(progress)
    except PermissionError:pass
    print(message,flush=True)
    measurements.append({'message':message,'seconds':round(time.monotonic()-started,2),
        'cuda_allocated_mib':round(torch.cuda.memory_allocated()/1048576),
        'cuda_peak_mib':round(torch.cuda.max_memory_allocated()/1048576)})
    request.with_suffix('.performance.json').write_text(json.dumps({'device':torch.cuda.get_device_name(),
        'mode':cfg.get('gpu_mode','staged'),'measurements':measurements}),encoding='utf-8')
phase('Carregando os pesos do TRELLIS.2')
unused_flow='tex_slat_flow_model_1024' if cfg.get('pipeline_type')=='512' else 'tex_slat_flow_model_512'
Trellis2ImageTo3DPipeline.model_names_to_load=[key for key in Trellis2ImageTo3DPipeline.model_names_to_load if key!=unused_flow]
pipeline=Trellis2ImageTo3DPipeline.from_pretrained(cfg['model'],config_file='pipeline-local.json')
pipeline.low_vram=True
if cfg.get('gpu_mode')=='resident' and torch.cuda.mem_get_info()[0]>=20*1024**3:
    pipeline.models.pop('tex_slat_flow_model_512',None)
    pipeline.low_vram=False
pipeline.cuda()
phase('Pesos prontos · RTX 3090',stage=1)
from trellis2.pipelines.samplers import flow_euler
original_tqdm=flow_euler.tqdm
shape_pass=0
def tracked_steps(iterable,*args,**kwargs):
    global shape_pass
    description=kwargs.get('desc','')
    if 'shape' in description.lower():
        passes=2 if cfg.get('pipeline_type','1024_cascade').endswith('_cascade') else 1
        stage,label=3,'Refinando a geometria';offset=shape_pass*len(iterable);shape_pass+=1;total=len(iterable)*passes
    elif 'tex' in description.lower():
        stage,label=4,'Criando os materiais PBR';offset=0;total=len(iterable)
    else:
        stage,label=2,'Construindo a estrutura 3D';offset=0;total=len(iterable)
    for index,item in enumerate(original_tqdm(iterable,*args,**kwargs)):
        yield item
        completed=offset+index+1
        phase(label,stage=stage,fraction=completed/total,detail=f'Passo {completed} de {total}')
flow_euler.tqdm=tracked_steps
for method,label in [('preprocess_image','Preparando a imagem de entrada'),('get_cond','Analisando a referência'),
                     ('sample_sparse_structure','Gerando a estrutura 3D'),('sample_shape_slat','Refinando a geometria'),
                     ('sample_shape_slat_cascade','Refinando a geometria em alta resolução'),
                     ('sample_tex_slat','Gerando os materiais PBR'),('decode_shape_slat','Decodificando a malha'),
                     ('decode_tex_slat','Decodificando os materiais')]:
    original=getattr(pipeline,method,None)
    if original:
        def wrapper(*args,_original=original,_label=label,_method=method,**kwargs):
            if _label=='Gerando os materiais PBR' and not pipeline.low_vram:
                for key,model in pipeline.models.items():
                    if key in ('sparse_structure_flow_model','sparse_structure_decoder','shape_slat_flow_model_512','shape_slat_flow_model_1024'):
                        model.cpu()
                torch.cuda.empty_cache()
            phase(_label)
            result=_original(*args,**kwargs)
            if _method=='preprocess_image' and cfg.get('diagnostics'):result.save(request.parent/'conditioning.png')
            return result
        setattr(pipeline,method,wrapper)
image=Image.open(cfg['image'])
if cfg.get('diagnostics'):
    import numpy as np,trimesh
    from trellis2.representations import Mesh
    original_fill=Mesh.fill_holes
    def capture_before_fill(self,*args,**kwargs):
        v=self.vertices.detach().cpu().numpy().copy();f=self.faces.detach().cpu().numpy()
        low,high=v[:,2].min(),v[:,2].max();f=f[np.all(v[f,2]>low+(high-low)*.80,axis=1)]
        v[:,1],v[:,2]=v[:,2].copy(),-v[:,1].copy()
        part=trimesh.Trimesh(vertices=v,faces=f,process=False);part.remove_unreferenced_vertices()
        part.export(str(request.parent/'decoder-before-hole-fill-head.glb'))
        return original_fill(self,*args,**kwargs)
    Mesh.fill_holes=capture_before_fill
# run() seeds internally; setting torch.manual_seed before run() was ignored.
mesh=pipeline.run(image,seed=int(cfg.get('seed',42)),pipeline_type=cfg.get('pipeline_type','1024_cascade'),
    sparse_structure_sampler_params={'steps':int(cfg.get('sparse_steps',12))},
    shape_slat_sampler_params={'steps':int(cfg.get('shape_steps',12))},
    tex_slat_sampler_params={'steps':int(cfg.get('texture_steps',12))})[0]
pipeline.cpu()
del pipeline
import gc
gc.collect();torch.cuda.empty_cache()
if cfg.get('diagnostics'):
    import numpy as np,trimesh
    bundle={key:getattr(mesh,key).detach().cpu() for key in ('vertices','faces','attrs','coords')}
    bundle.update(voxel_size=mesh.voxel_size,layout=mesh.layout)
    torch.save(bundle,request.parent/'decoded.pt')
    vertices=bundle['vertices'].numpy().copy();faces=bundle['faces'].numpy()
    bottom,top=vertices[:,2].min(),vertices[:,2].max()
    head=faces[np.all(vertices[faces,2]>bottom+(top-bottom)*.80,axis=1)]
    vertices[:,1],vertices[:,2]=vertices[:,2].copy(),-vertices[:,1].copy()
    preview=trimesh.Trimesh(vertices=vertices,faces=head,process=False);preview.remove_unreferenced_vertices()
    preview.export(str(request.parent/'decoded-head.glb'))
    request.with_suffix('.geometry.json').write_text(json.dumps({'decoded_vertices':len(vertices),'decoded_faces':len(faces),
        'profile':cfg.get('quality_profile'),'seed':int(cfg.get('seed',42)),'pipeline_type':cfg.get('pipeline_type'),
        'remesh':cfg.get('remesh',True),'projection':cfg.get('remesh_project',0),'triangle_budget':cfg.get('triangle_budget',240000)}))
    del bundle,preview,vertices,faces,head
mesh.simplify(16777216)
phase('Preparando UVs e texturas 4K',stage=5)
glb=o_voxel.postprocess.to_glb(vertices=mesh.vertices,faces=mesh.faces,
    attr_volume=mesh.attrs,coords=mesh.coords,attr_layout=mesh.layout,voxel_size=mesh.voxel_size,
    aabb=[[-.5,-.5,-.5],[.5,.5,.5]],decimation_target=int(cfg.get('triangle_budget',240000)),
    texture_size=cfg['texture_size'],remesh=bool(cfg.get('remesh',True)),remesh_band=1,
    remesh_project=float(cfg.get('remesh_project',0)),verbose=True)
output=Path(cfg['output']);glb.export(str(output),extension_webp=False)
if not output.is_file() or output.stat().st_size<1000: raise RuntimeError('Exportação GLB incompleta')
phase('Modelo e materiais exportados',stage=5,fraction=1)
