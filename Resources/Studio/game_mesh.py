"""Create a lower-density mesh for rigging while retaining its UVs and materials."""
import json
import os
from pathlib import Path
import sys

import bpy

request = json.loads(Path(sys.argv[-1]).read_text(encoding='utf-8'))
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=request['input'])
meshes = [obj for obj in bpy.context.scene.objects if obj.type == 'MESH']

def triangles(obj):
    obj.data.calc_loop_triangles()
    return len(obj.data.loop_triangles)

before = sum(map(triangles, meshes))
if not before:
    raise ValueError('Malha vazia.')
ratio = min(1, request['budget'] / before)
for obj in meshes:
    if ratio < 1:
        bpy.context.view_layer.objects.active = obj
        modifier = obj.modifiers.new('Game mesh', 'DECIMATE')
        modifier.ratio = ratio
        modifier.use_collapse_triangulate = True
        bpy.ops.object.modifier_apply(modifier=modifier.name)
after = sum(map(triangles, meshes))
if not after or after > request['budget'] * 1.05:
    raise ValueError(f'Redução fora do orçamento: {after} triângulos.')
bpy.ops.export_scene.gltf(filepath=request['output'], export_format='GLB', export_yup=True)
print(f'Triângulos: {before} -> {after}', flush=True)


# The standalone bpy runtime can crash during Windows interpreter teardown.
# The exporter has closed the GLB by this point.
os._exit(0)
