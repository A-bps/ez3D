"""Blender worker: preserve an existing rig, or bind a neutral humanoid to SOMA30.

The automatic path is explicitly humanoid/T-pose, not a general anatomical rigger.
Runs in its own process so Blender/mesh allocations are released on exit.
"""
import importlib.util
import json
import math
from pathlib import Path
import sys

import bpy
import numpy as np
from mathutils import Matrix, Quaternion, Vector


def main(request):
    cfg = json.loads(Path(request).read_text(encoding='utf-8-sig'))
    output = Path(cfg['output']); output.parent.mkdir(parents=True, exist_ok=True)
    if cfg.get('rig_scene'):
        bpy.ops.wm.open_mainfile(filepath=cfg['rig_scene'])
        data=json.loads(Path(cfg['rig_scene']).with_suffix('.rig.json').read_text(encoding='utf-8'))
        rig=next(o for o in bpy.context.scene.objects if o.type=='ARMATURE')
        armature=rig.data
        meshes=[o for o in bpy.context.scene.objects if o.type=='MESH']
        names,parents,offsets=data['names'],data['parents'],data['offsets']
        C=Matrix.Rotation(math.pi/2,4,'X'); S=Matrix.Scale(data['scale'],4)
        source_rest=[Matrix(m) for m in data['source_rest']]
        source_orientations={n:Matrix(m) for n,m in data['orientations'].items()}
        bottom,scale,total,coverage,weight_method=[data[k] for k in ('bottom','scale','total','coverage','weight_method')]
    else:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        bpy.ops.import_scene.gltf(filepath=cfg['input'])
        meshes = [o for o in bpy.context.scene.objects if o.type == 'MESH']
        if not meshes: raise ValueError('Nenhuma malha encontrada.')
        if any(o.type == 'ARMATURE' for o in bpy.context.scene.objects):
            raise ValueError('A entrada já contém esqueleto. Preserve-o; o vínculo SOMA automático não sobrescreve rigs existentes.')
        # Work in metres with feet at z=0; keep materials and UVs from GLB.
        points = [o.matrix_world @ Vector(c) for o in meshes for c in o.bound_box]
        low = Vector(tuple(min(p[i] for p in points) for i in range(3)))
        high = Vector(tuple(max(p[i] for p in points) for i in range(3)))
        if high.z - low.z < 0.01: raise ValueError('Altura da malha inválida.')
        factor = float(cfg.get('height', 1.75)) / (high.z - low.z)
        center = Vector(((low.x + high.x) / 2, (low.y + high.y) / 2, low.z))
        for obj in meshes:
            transform = obj.matrix_world.copy()
            for v in obj.data.vertices: v.co = (transform @ v.co - center) * factor
            obj.matrix_world = Matrix.Identity(4)
            # Limit skinning cost without changing the original textured GLB on disk.
            triangle_budget = int(cfg.get('triangle_budget', 120000))
            if len(obj.data.polygons) > triangle_budget:
                bpy.context.view_layer.objects.active = obj
                modifier = obj.modifiers.new('StudioSkinBudget', 'DECIMATE')
                modifier.ratio = triangle_budget / len(obj.data.polygons)
                bpy.ops.object.modifier_apply(modifier=modifier.name)
        spec = importlib.util.spec_from_file_location('soma_export', Path(cfg['kimodo_root']) / 'scripts/export_glb.py')
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        skeleton = module.SKELETONS['soma30']
        names, parents, offsets = skeleton['names'], skeleton['parents'], skeleton['offsets']
        C = Matrix.Rotation(math.pi / 2, 4, 'X')
        source_rest = []
        for index, offset in enumerate(offsets):
            local = Matrix.Translation(Vector(offset))
            source_rest.append(local if parents[index] < 0 else source_rest[parents[index]] @ local)
        converted = [C @ m.translation for m in source_rest]
        bottom = min(p.z for p in converted)
        span = max(p.z for p in converted) - bottom
        scale = float(cfg.get('height', 1.75)) / span
        translate = Matrix.Translation(Vector((0, 0, -bottom * scale)))
        S = Matrix.Scale(scale, 4)
        coordinate = translate @ S @ C
        rest = [coordinate @ m for m in source_rest]
        armature = bpy.data.armatures.new('SOMA30')
        rig = bpy.data.objects.new('StudioCharacter', armature)
        bpy.context.collection.objects.link(rig)
        bpy.context.view_layer.objects.active = rig
        rig.select_set(True)
        bpy.ops.object.mode_set(mode='EDIT')
        for i, name in enumerate(names):
            bone = armature.edit_bones.new(name)
            bone.head = rest[i].translation
            children = [j for j, p in enumerate(parents) if p == i]
            end = rest[children[0]].translation if children else bone.head + Vector((0, 0, 0.035))
            if (end - bone.head).length < 0.005: end = bone.head + Vector((0, 0, 0.035))
            bone.tail = end
            if parents[i] >= 0: bone.parent = armature.edit_bones[names[parents[i]]]
            bone.use_connect = False
        bpy.ops.object.mode_set(mode='OBJECT')
        source_orientations = {b.name: b.matrix_local.to_3x3().copy() for b in armature.bones}
        # Fit the bind joints to the generated humanoid instead of assuming that
        # its head, torso and arm pose match the adult SOMA template exactly.
        height = float(cfg.get('height',1.75))
        fitted = {
            'Hips':(0,0,.48),'Spine1':(0,0,.53),'Spine2':(0,0,.60),'Chest':(0,0,.68),
            'Neck1':(0,0,.77),'Neck2':(0,0,.80),'Head':(0,0,.86),'Jaw':(0,-.045,.83),
            'LeftEye':(.035,-.07,.88),'RightEye':(-.035,-.07,.88),
        }
        positions = np.array([v.co[:] for mesh in meshes for v in mesh.data.vertices])
        for side, sign in [('Left',1),('Right',-1)]:
            candidates = positions[(positions[:,0]*sign>0)&(positions[:,2]>.35*height)&(positions[:,2]<.82*height)]
            edge = np.quantile(candidates[:,0]*sign,.97)
            hand_points = candidates[candidates[:,0]*sign>=edge]
            hand = Vector(np.median(hand_points,axis=0).tolist()) / height
            hand.x *= .94
            upper = Vector((sign*.15,0,.71))
            elbow = upper.lerp(hand,.50)
            fitted.update({side+'Shoulder':(sign*.105,0,.71),side+'Arm':tuple(upper),
                side+'ForeArm':tuple(elbow),side+'Hand':tuple(hand),
                side+'HandThumbEnd':tuple(hand+Vector((sign*.025,-.012,-.01))),
                side+'HandMiddleEnd':tuple(hand+Vector((sign*.045,0,-.015))),
                side+'Leg':(sign*.085,0,.47),side+'Shin':(sign*.085,0,.255),
                side+'Foot':(sign*.085,0,.055),side+'ToeBase':(sign*.085,-.075,.035)})
        bpy.ops.object.mode_set(mode='EDIT')
        for i,name in enumerate(names):
            bone=armature.edit_bones[name]
            bone.head=Vector(fitted[name])*height
            children=[j for j,p in enumerate(parents) if p==i]
            bone.tail=Vector(fitted[names[children[0]]])*height if children else bone.head+Vector((0,0,.025))
            if (bone.tail-bone.head).length<.005:bone.tail=bone.head+Vector((0,0,.025))
            if name in {'Jaw','LeftEye','RightEye','LeftHandThumbEnd','LeftHandMiddleEnd','RightHandThumbEnd','RightHandMiddleEnd'}:
                bone.use_deform=False
        bpy.ops.object.mode_set(mode='OBJECT')
        bpy.ops.object.select_all(action='DESELECT')
        for mesh in meshes: mesh.select_set(True)
        rig.select_set(True); bpy.context.view_layer.objects.active = rig
        bpy.ops.object.parent_set(type='ARMATURE_AUTO')
        weight_method = 'blender_bone_heat'
        total = covered = 0
        for mesh in meshes:
            bone_groups = {g.index for g in mesh.vertex_groups if g.name in names}
            for vertex in mesh.data.vertices:
                total += 1
                if sum(g.weight for g in vertex.groups if g.group in bone_groups) > 0.001: covered += 1
        coverage = covered / max(1, total)
        if coverage < 0.98 and cfg.get('allow_proximity_weights', False):
            # Explicit fallback for the automatic humanoid mode. It is recorded as
            # approximate skinning and always requires visual review before import.
            weight_method = 'proximity_4_bones_requires_visual_review'
            excluded = {'Jaw','LeftEye','RightEye','LeftHandThumbEnd','LeftHandMiddleEnd','RightHandThumbEnd','RightHandMiddleEnd'}
            bones = [b for b in armature.bones if b.name not in excluded]
            heads = np.array([b.head_local[:] for b in bones])
            tails = np.array([b.tail_local[:] for b in bones])
            directions = tails - heads
            lengths = np.maximum(np.sum(directions * directions, axis=1), 1e-8)
            for mesh in meshes:
                for group in list(mesh.vertex_groups): mesh.vertex_groups.remove(group)
                groups = [mesh.vertex_groups.new(name=b.name) for b in bones]
                vertices = np.array([v.co[:] for v in mesh.data.vertices])
                for start in range(0, len(vertices), 2048):
                    points = vertices[start:start+2048]
                    delta = points[:, None, :] - heads[None, :, :]
                    t = np.clip(np.sum(delta * directions[None, :, :], axis=2) / lengths, 0, 1)
                    nearest = heads[None, :, :] + t[:, :, None] * directions[None, :, :]
                    distances = np.linalg.norm(points[:, None, :] - nearest, axis=2)
                    indices = np.argsort(distances, axis=1)[:, :4]
                    for row, ids in enumerate(indices):
                        weights = 1 / np.maximum(distances[row, ids], 0.015) ** 4
                        weights /= weights.sum()
                        for idx, weight in zip(ids, weights):
                            groups[int(idx)].add([start + row], float(weight), 'REPLACE')
            coverage = 1.0
        if coverage < 0.98:
            raise RuntimeError(f'Pesos automáticos cobrem só {coverage:.1%} da malha. Ajuste a pose ou use um rig revisado.')
        scene=output.with_suffix('.blend')
        bpy.ops.wm.save_as_mainfile(filepath=str(scene))
        data={'names':names,'parents':parents,'offsets':offsets,'scale':scale,'bottom':bottom,
              'total':total,'coverage':coverage,'weight_method':weight_method,
              'source_rest':[[list(r) for r in m] for m in source_rest],
              'orientations':{n:[list(r) for r in m] for n,m in source_orientations.items()}}
        scene.with_suffix('.rig.json').write_text(json.dumps(data),encoding='utf-8')
    frame_count = 0
    if cfg.get('motion'):
        directory = Path(cfg['motion'])
        rotations = np.fromfile(directory / 'local_rotations_xyzw.f32', dtype='<f4')
        roots = np.fromfile(directory / 'root_positions.f32', dtype='<f4')
        if rotations.size % (len(names) * 4): raise ValueError('Contagem de juntas do movimento incompatível.')
        rotations = rotations.reshape(-1, len(names), 4)
        roots = roots.reshape(-1, 3)
        frame_count = len(rotations)
        if len(roots) != frame_count or not np.isfinite(rotations).all() or not np.isfinite(roots).all():
            raise ValueError('Movimento incompleto ou não finito.')
        rig.animation_data_create()
        rig.animation_data.action = bpy.data.actions.new('RequestedMotion')
        for frame in range(frame_count):
            bpy.context.scene.frame_set(frame + 1)
            globals_ = []
            for i, name in enumerate(names):
                x, y, z, w = map(float, rotations[frame, i])
                q = Quaternion((w, x, y, z))
                if q.magnitude < 0.5: raise ValueError('Quaternion inválido no movimento.')
                q.normalize()
                # Kimodo roots are measured from the ground, while SOMA rest
                # offsets start at the hip. Avoid adding the hip height twice.
                source_hip_height = float(source_rest[0].translation.y)
                location = (Vector(roots[frame]) - Vector((float(roots[0,0]), source_hip_height, float(roots[0,2])))) if parents[i] < 0 else Vector(offsets[i])
                local = Matrix.Translation(location) @ q.to_matrix().to_4x4()
                global_ = local if parents[i] < 0 else globals_[parents[i]] @ local
                globals_.append(global_)
                pose = rig.pose.bones[name]
                pose.rotation_mode = 'QUATERNION'
                rotation = C.to_3x3() @ global_.to_3x3() @ C.to_3x3().inverted() @ source_orientations[name]
                target = rotation.to_4x4()
                if parents[i]<0:
                    leg_scale = armature.bones[name].head_local.z / max(.01,(source_hip_height-bottom)*scale)
                    target.translation = armature.bones[name].head_local + ((S @ C).to_3x3() @ location)*leg_scale
                else:
                    parent_name=names[parents[i]]
                    target.translation = rig.pose.bones[parent_name].matrix @ armature.bones[parent_name].matrix_local.inverted() @ armature.bones[name].head_local
                pose.matrix = target
                bpy.context.view_layer.update()
                pose.keyframe_insert('location', frame=frame+1)
                pose.keyframe_insert('rotation_quaternion', frame=frame+1)
                pose.keyframe_insert('scale', frame=frame+1)
            floor = min(rig.pose.bones[n].matrix.translation.z for n in ('LeftFoot','RightFoot','LeftToeBase','RightToeBase'))
            if floor < .035:
                root_pose=rig.pose.bones['Hips'];matrix=root_pose.matrix.copy();matrix.translation.z += .035-floor
                root_pose.matrix=matrix;root_pose.keyframe_insert('location',frame=frame+1)
                bpy.context.view_layer.update()
        bpy.context.scene.frame_end = frame_count
        bpy.context.scene.render.fps = 30
    bpy.context.scene.frame_set(1)
    # Verify evaluated skin remains finite at representative animation frames.
    for frame in sorted({1, max(1, frame_count // 2), max(1, frame_count)}):
        bpy.context.scene.frame_set(frame)
        depsgraph = bpy.context.evaluated_depsgraph_get()
        for mesh in meshes:
            evaluated = mesh.evaluated_get(depsgraph)
            bounds = [evaluated.matrix_world @ Vector(c) for c in evaluated.bound_box]
            if any(not math.isfinite(v) or abs(v) > 100 for p in bounds for v in p):
                diagnostic = {'frame': frame, 'bounds': [list(p) for p in bounds],
                    'bones': {p.name: {'location': list(p.location), 'scale': list(p.scale), 'head': list(p.matrix.translation)} for p in rig.pose.bones}}
                output.with_suffix('.failure.json').write_text(json.dumps(diagnostic, indent=2), encoding='utf-8')
                raise RuntimeError('Deformação inválida detectada; exportação bloqueada.')
    bpy.context.scene.frame_set(1)
    bpy.ops.object.select_all(action='DESELECT')
    for obj in meshes + [rig]: obj.select_set(True)
    bpy.ops.export_scene.fbx(filepath=str(output), use_selection=True, object_types={'ARMATURE'} if cfg.get('animation_only') else {'ARMATURE','MESH'},
        add_leaf_bones=False, bake_anim=bool(frame_count), bake_anim_use_all_actions=False,
        bake_anim_use_nla_strips=False, path_mode='COPY', embed_textures=True,
        axis_forward='-Y', axis_up='Z')
    bpy.ops.export_scene.gltf(filepath=cfg['preview'], export_format='GLB', use_selection=True,
        export_animations=bool(frame_count), export_skins=True)
    report = {'method': 'SOMA30_humanoid_neutral_pose_auto_weights', 'vertices': total,
              'bind_fit': 'humanoid_proportions_and_mesh_hand_extents',
              'weight_method': weight_method,
              'weighted_fraction': coverage, 'bones': len(names), 'animation_frames': frame_count,
              'fps': 30, 'technical_validation': True, 'visual_approval_required': True}
    output.with_suffix('.validation.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report), flush=True)


if __name__ == '__main__':
    main(sys.argv[1])
    # bpy's embedded Windows shutdown can fail after successful exports. All
    # files are closed and validation is written before exiting this worker.
    import os
    sys.stdout.flush(); sys.stderr.flush()
    os._exit(0)
