"""Controlled export comparison from our own saved decoder output; no new inference."""
import os
os.environ['PYTORCH_CUDA_ALLOC_CONF']='expandable_segments:True'
from pathlib import Path
import sys,json,time,gc
sys.path.insert(0,'/opt/ez3d/TRELLIS.2')
import torch
import o_voxel

request=Path(sys.argv[1]);cfg=json.loads(request.read_text())
# This is a local tensor bundle written by trellis_worker, not third-party pickle.
bundle=torch.load(cfg['input'],map_location='cpu',weights_only=False)
for key in ('vertices','faces','attrs','coords'):bundle[key]=bundle[key].cuda()
results=[]
for index,(name,project,budget) in enumerate([('control',0.,240000),('projected',.9,500000)]):
    progress=request.parent/'trellis-request.progress.json'
    progress.write_text(json.dumps(dict(message='Comparando exportação: '+name,value=index,max=2,stage_index=index,stage_count=2,measured=False)))
    start=time.monotonic()
    mesh=o_voxel.postprocess.to_glb(vertices=bundle['vertices'],faces=bundle['faces'],
        attr_volume=bundle['attrs'],coords=bundle['coords'],attr_layout=bundle['layout'],voxel_size=bundle['voxel_size'],
        aabb=[[-.5,-.5,-.5],[.5,.5,.5]],decimation_target=budget,texture_size=4096,
        remesh=True,remesh_band=1,remesh_project=project,verbose=True)
    output=request.parent/(name+'.glb');mesh.export(str(output),extension_webp=False)
    results.append(dict(name=name,projection=project,triangles=len(mesh.faces),seconds=round(time.monotonic()-start,2)))
    del mesh;gc.collect();torch.cuda.empty_cache()
(request.parent/'comparison.json').write_text(json.dumps(results,indent=2))
