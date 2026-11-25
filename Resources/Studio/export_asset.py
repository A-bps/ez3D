"""Export a real model, keeping textures at or below their source resolution."""
import json
import os
from pathlib import Path
import sys
import zipfile
import bpy

cfg=json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
folder=Path(cfg['folder']);folder.mkdir(parents=True,exist_ok=True)
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=cfg['input'])
for image in bpy.data.images:
    width,height=image.size
    if max(width,height)>cfg['resolution']:
        scale=cfg['resolution']/max(width,height)
        image.scale(max(1,round(width*scale)),max(1,round(height*scale)))
format=cfg['format'];path=folder/(cfg['name']+'.'+format)
if format=='glb': bpy.ops.export_scene.gltf(filepath=str(path),export_format='GLB',export_animations=True)
elif format=='fbx': bpy.ops.export_scene.fbx(filepath=str(path),add_leaf_bones=False,path_mode='COPY',embed_textures=True,bake_anim=True)
else:
    bpy.ops.wm.obj_export(filepath=str(path),export_materials=True,path_mode='COPY')
    archive=folder/(cfg['name']+'.zip')
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as output:
        for file in folder.rglob('*'):
            if file.is_file() and file!=archive: output.write(file,file.relative_to(folder))
    path=archive
(folder/'receipt.json').write_text(json.dumps({'output':str(path),'texture_limit':cfg['resolution']}),encoding='utf-8')
print(str(path),flush=True)
sys.stdout.flush();os._exit(0)
