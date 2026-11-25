"""Preserve UniRig weights/materials and retarget SOMA motion onto its learned bones."""
import importlib.util
import json
import math
from pathlib import Path
import sys
import bpy
import numpy as np
from mathutils import Matrix, Quaternion, Vector


def vertex_array(data):
    values=np.empty(len(data.vertices)*3,dtype=np.float32)
    data.vertices.foreach_get('co',values)
    return values.reshape(-1,3)


def edge_array(data):
    values=np.empty(len(data.edges)*2,dtype=np.int32)
    data.edges.foreach_get('vertices',values)
    return values.reshape(-1,2)


def refine_weights(mesh,rig,analytic=False):
    """Reject distant-joint leakage, then diffuse learned weights across seams."""
    from scipy.spatial import cKDTree
    from scipy.sparse import coo_matrix,diags
    bones=list(rig.data.bones);coords=np.array([rig.matrix_world.inverted()@mesh.matrix_world@v.co for v in mesh.data.vertices])
    height=max(np.ptp(coords,axis=0));weights=np.zeros((len(coords),len(bones)),dtype=np.float32)
    groups={mesh.vertex_groups[b.name].index:i for i,b in enumerate(bones) if b.name in mesh.vertex_groups}
    for v in mesh.data.vertices:
        for g in v.groups:
            if g.group in groups:weights[v.index,groups[g.group]]=g.weight
    distances=[]
    for bone in bones:
        a=np.array(bone.head_local);d=np.array(bone.tail_local)-a
        t=np.clip((coords-a)@d/max(float(d@d),1e-12),0,1)
        distances.append(np.linalg.norm(coords-(a+t[:,None]*d),axis=1))
    distances=np.stack(distances,axis=1)
    try:semantic=humanoid_map(rig)
    except ValueError:semantic={}
    # A binary nearest-bone mask creates abrupt boundaries between adjacent
    # vertices. Use a continuous distance penalty instead: the transition must
    # remain blendable when two bones move apart.
    radius=distances.min(axis=1)[:,None]*1.5+height*.008
    support=np.exp(-np.minimum((distances/np.maximum(radius,1e-9))**2,40))
    for i,bone in enumerate(bones):
        ancestor=bone
        while ancestor and ancestor.name not in semantic:ancestor=ancestor.parent
        name=semantic.get(bone.name,semantic.get(ancestor.name,'') if ancestor else '')
        cap=.045 if name.endswith(('Arm','Shoulder')) else .028 if name.endswith(('ForeArm','Hand')) else None
        if cap:support[:,i]*=np.exp(-np.minimum((distances[:,i]/(height*cap))**2,40))
    weights*=support
    empty=weights.sum(axis=1)<1e-6
    # Smooth fallback instead of assigning a different single bone on either
    # side of an anatomical cutoff.
    weights[empty]=support[empty]
    weights/=weights.sum(axis=1)[:,None]
    edges=np.array([e.vertices[:] for e in mesh.data.edges]);row=np.r_[edges[:,0],edges[:,1]];col=np.r_[edges[:,1],edges[:,0]]
    # Spatial neighbours also weld duplicate UV/normal seam vertices.
    # Only join coincident seams. Nearby hands, sleeves and coat panels must
    # not become neighbours merely because they almost touch in the rest pose.
    pairs=cKDTree(coords).query_pairs(max(height*1e-6,1e-9),output_type='ndarray')
    if len(pairs):row=np.r_[row,pairs[:,0],pairs[:,1]];col=np.r_[col,pairs[:,1],pairs[:,0]]
    edge_lengths=np.linalg.norm(coords[row]-coords[col],axis=1)
    # Short edges need stronger agreement, not the same influence as long edges.
    conductance=1/np.maximum(edge_lengths,height*.0002)
    adjacency=coo_matrix((conductance,(row,col)),shape=(len(coords),len(coords))).tocsr()
    adjacency=diags(1/np.maximum(1,np.asarray(adjacency.sum(axis=1)).ravel()))@adjacency
    # Separate limbs by travel ALONG the surface, not Euclidean proximity.
    # Hands resting beside thighs are close in space but normally far apart
    # along the connected character surface. Only unambiguous seeds are used.
    if semantic:
        from scipy.sparse.csgraph import dijkstra
        families=['LeftArm','RightArm','LeftLeg','RightLeg','Body']
        membership=[]
        for bone in bones:
            ancestor=bone
            while ancestor and ancestor.name not in semantic:ancestor=ancestor.parent
            name=semantic.get(ancestor.name,'') if ancestor else ''
            side='Left' if name.startswith('Left') else 'Right' if name.startswith('Right') else ''
            membership.append(families.index(side+('Arm' if name.endswith(('Shoulder','Arm','ForeArm','Hand')) else 'Leg')) if side else 4)
        family_distance=np.stack([distances[:,np.array(membership)==i].min(axis=1) if i in membership else np.full(len(coords),np.inf) for i in range(5)],axis=1)
        order=np.argsort(family_distance,axis=1);nearest=order[:,0]
        first=np.take_along_axis(family_distance,order[:,:1],axis=1)[:,0]
        second=np.take_along_axis(family_distance,order[:,1:2],axis=1)[:,0]
        unambiguous=(second-first>height*.012)&(first<height*.06)
        lengths=np.maximum(np.linalg.norm(coords[row]-coords[col],axis=1),height*1e-7)
        surface=coo_matrix((lengths,(row,col)),shape=(len(coords),len(coords))).tocsr()
        travel=[]
        for i in range(5):
            seeds=np.flatnonzero(unambiguous&(nearest==i))
            travel.append(dijkstra(surface,directed=False,indices=seeds,min_only=True) if len(seeds) else np.full(len(coords),np.inf))
        travel=np.stack(travel,axis=1);reachable=np.isfinite(travel).any(axis=1)
        baseline=np.min(travel[reachable],axis=1)
        region=np.ones((len(coords),5),dtype=np.float32)
        region[reachable]=np.exp(-np.minimum((np.maximum(0,travel[reachable]-baseline[:,None])/(height*.035))**2,30))
        region=np.maximum(region,1e-6)
        support*=region[:,membership]
        weights*=region[:,membership]
        weights/=np.maximum(weights.sum(axis=1)[:,None],1e-30)
    if analytic:
        # Reconstruct a continuous bind instead of retaining noisy learned
        # finger/thigh mixtures. Surface regions keep nearby limbs separate.
        weights=support.copy()
        # Unmapped finger bones inherit the hand during SOMA motion. Bind the
        # hand as one unit rather than introducing independent finger fields.
        for i,bone in enumerate(bones):
            if bone.name not in semantic:
                ancestor=bone.parent
                while ancestor and ancestor.name not in semantic:ancestor=ancestor.parent
                if ancestor and semantic[ancestor.name].endswith('Hand'):
                    weights[:,bones.index(ancestor)]+=weights[:,i]
                    weights[:,i]=0
        weights/=np.maximum(weights.sum(axis=1)[:,None],1e-30)
    learned=weights.copy()
    for _ in range(400):
        # Screened surface diffusion. Multiplying by support on every iteration
        # effectively raised the anatomical prior to the 120th power, recreating
        # sharp weight boundaries even from a continuous initial prior.
        # Apply the prior once, then solve for smooth surface transitions.
        weights=.02*learned+.98*(adjacency@weights)
        weights/=np.maximum(weights.sum(axis=1)[:,None],1e-10)
    # glTF splits UV/normal seams into coincident vertices. Their weights must
    # be identical or skinning opens cracks although the rest mesh looks closed.
    _,groups=np.unique(np.round(coords/max(height*1e-6,1e-9)).astype(np.int64),axis=0,return_inverse=True)
    sums=np.zeros((int(groups.max())+1,len(bones)),dtype=np.float32)
    np.add.at(sums,groups,weights)
    weights=sums[groups]/np.bincount(groups)[groups,None]
    # Keep every learned contribution while evaluating; GLB/Unreal support four.
    influence_count=min(4,len(bones))
    top=np.argpartition(weights,-influence_count,axis=1)[:,-influence_count:];limited=np.zeros_like(weights)
    np.put_along_axis(limited,top,np.take_along_axis(weights,top,axis=1),axis=1)
    limited/=np.maximum(limited.sum(axis=1)[:,None],1e-10)
    for group in list(mesh.vertex_groups):mesh.vertex_groups.remove(group)
    for i,b in enumerate(bones):
        group=mesh.vertex_groups.new(name=b.name)
        for vertex in np.flatnonzero(limited[:,i]>1e-6):group.add([int(vertex)],float(limited[vertex,i]),'REPLACE')


def bind_rear_garment(mesh, rig, mapping):
    """Keep a long rear garment attached to the torso during locomotion."""
    bones = {role: rig.matrix_world @ rig.data.bones[name].head_local for name, role in mapping.items()}
    hip = bones['Hips']
    ankle = (bones['LeftFoot'] + bones['RightFoot']) * .5
    toe = (bones['LeftToeBase'] + bones['RightToeBase']) * .5
    up = Vector((0, 0, 1))
    forward = toe - ankle
    forward.z = 0
    height = (hip - ankle).dot(up)
    if height <= 0 or forward.length < height * .02:
        raise ValueError('Ajuste tornozelos e base dos dedos antes de corrigir a capa.')
    forward.normalize()
    coords = vertex_array(mesh.data)
    matrix = np.asarray(mesh.matrix_world)
    coords = coords @ matrix[:3, :3].T + matrix[:3, 3]
    _, first, groups = np.unique(np.round(coords / (height * 2e-6)).astype(np.int64), axis=0,
                                 return_index=True, return_inverse=True)
    rear = (np.asarray(hip) - coords[first]) @ np.asarray(forward)
    fade = np.clip((rear / height - .025) / .10, 0, 1)
    fade = (fade * fade * (3 - 2 * fade))[groups]
    anchor = next(name for name, role in mapping.items() if role == 'Hips')
    anchor_group = mesh.vertex_groups[anchor]
    for index in np.flatnonzero(fade > 0):
        vertex = mesh.data.vertices[int(index)]
        amount = float(fade[index])
        weights = {group.group: group.weight * (1 - amount) for group in vertex.groups}
        weights[anchor_group.index] = weights.get(anchor_group.index, 0) + amount
        strongest = sorted(weights, key=weights.get, reverse=True)[:4]
        total = sum(weights[group] for group in strongest)
        for group in weights:
            if group in strongest and weights[group] > 0:
                mesh.vertex_groups[group].add([int(index)], weights[group] / total, 'REPLACE')
            else:
                mesh.vertex_groups[group].remove([int(index)])
    return int(np.count_nonzero(fade))


def humanoid_map(rig):
    if rig.get('ez3d_semantic_map'):
        return json.loads(rig['ez3d_semantic_map'])
    bones=list(rig.data.bones)
    root=next(b for b in bones if b.parent is None)
    while len(root.children)==1:root=root.children[0]
    children=list(root.children)
    if len(children)!=3:raise ValueError('Topologia UniRig não reconhecida como humanoide.')
    spine=max(children,key=lambda b:b.tail_local.z)
    legs=[b for b in children if b!=spine]
    result={root.name:'Hips'}
    trunk=[];bone=spine
    while len(bone.children)==1:
        trunk.append(bone);bone=bone.children[0]
    trunk.append(bone)
    if len(bone.children)!=3:raise ValueError('Ombros UniRig não reconhecidos.')
    labels=['Spine1','Spine2','Chest']
    for i,b in enumerate(trunk):result[b.name]=labels[min(2,round(i*2/max(1,len(trunk)-1)))]
    neck=min(bone.children,key=lambda b:abs(b.head_local.x-root.head_local.x))
    necks=[];b=neck
    while True:
        necks.append(b)
        if not b.children:break
        b=max(b.children,key=lambda x:x.tail_local.z)
    for i,b in enumerate(necks):result[b.name]='Head' if i==len(necks)-1 else 'Neck1'
    def chain(start):
        chain=[];b=start
        while True:
            chain.append(b)
            if not b.children:break
            b=max(b.children,key=lambda c:(c.tail_local-c.head_local).length)
        return chain
    for starts,labels in [(legs,['Leg','Shin','Foot','ToeBase']),
                          ([b for b in bone.children if b!=neck],['Shoulder','Arm','ForeArm','Hand'])]:
        if len(starts)!=2:raise ValueError('Membros UniRig incompletos.')
        for start in starts:
            side='Left' if start.head_local.x>root.head_local.x else 'Right'
            branch=chain(start)
            if len(branch)<len(labels):raise ValueError('Articulações insuficientes no humanoide UniRig.')
            for b,label in zip(branch,labels):result[b.name]=side+label
    return result


def main(request):
    cfg=json.loads(Path(request).read_text(encoding='utf-8-sig'));output=Path(cfg['output'])
    def progress(message,value,total,stage):
        path=output.parent/'trellis-request.progress.json'
        temp=path.with_suffix('.tmp')
        temp.write_text(json.dumps(dict(message=message,value=value,max=total,stage_index=stage,stage_count=3,measured=True,detail=f'{value} de {total} passos concluídos')),encoding='utf-8')
        try:temp.replace(path)
        except PermissionError:pass
    progress('Carregando malha e esqueleto',0,3,0)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    if cfg.get('rig_scene'):bpy.ops.wm.open_mainfile(filepath=cfg['rig_scene'])
    else:bpy.ops.import_scene.gltf(filepath=cfg['input'])
    rigs=[o for o in bpy.context.scene.objects if o.type=='ARMATURE']
    if len(rigs)!=1:raise ValueError('UniRig deve produzir exatamente um esqueleto.')
    rig=rigs[0]
    if cfg.get('native_tokenrig'):
        rig['ez3d_rig_backend']='TokenRig'
        rig['ez3d_weight_method']='tokenrig_native'
    if cfg.get('rig_scene') and not rig.get('ez3d_weight_method'):
        prior=Path(cfg['rig_scene']).with_suffix('.validation.json')
        if prior.is_file():rig['ez3d_weight_method']=json.loads(prior.read_text()).get('weight_refinement','surface_analytic_v7')
    if cfg.get('joint_config'):
        from joint_config import validate_joint_config
        configuration=validate_joint_config(cfg['joint_config'])
        by_name={p['name']:p for p in configuration['joints']}
        if set(by_name)!={b.name for b in rig.data.bones}:
            raise ValueError('As articulações não correspondem ao esqueleto selecionado.')
        roles={p['role']:name for name,p in by_name.items() if p['role']}
        for side in ('Left','Right'):
            for parent_role,child_role in (('Arm','ForeArm'),('ForeArm','Hand'),('Leg','Shin'),('Shin','Foot'),('Foot','ToeBase')):
                parent=roles.get(side+parent_role);child=roles.get(side+child_role)
                if not parent or not child:continue
                ancestor=rig.data.bones[child].parent
                while ancestor and ancestor.name!=parent:ancestor=ancestor.parent
                if not ancestor:raise ValueError('As identificações do membro não seguem a ordem das articulações. Confira braço, cotovelo, punho, joelho e pé.')
        original={b.name:(b.head_local.copy(),b.tail_local.copy()) for b in rig.data.bones}
        height=max(max(h.z,t.z) for h,t in original.values())-min(min(h.z,t.z) for h,t in original.values())
        inverse=rig.matrix_world.inverted()
        targets={}
        for name,point in by_name.items():
            x,y,z=point['position'];target=inverse@Vector((x,-z,y))
            if (target-original[name][0]).length>height*.3:
                raise ValueError('Uma articulação foi deslocada demais. Confira frente e lateral.')
            targets[name]=target
        bpy.ops.object.select_all(action='DESELECT');rig.select_set(True);bpy.context.view_layer.objects.active=rig
        for pose in rig.pose.bones:pose.matrix_basis.identity()
        bpy.ops.object.mode_set(mode='EDIT')
        for bone in rig.data.edit_bones:
            head,tail=original[bone.name];bone.head=targets[bone.name]
            connected=next((c for c in bone.children if (original[c.name][0]-tail).length<height*1e-5),None)
            bone.tail=targets[connected.name] if connected else tail+(bone.head-head)
            if bone.length<height*.0001:
                bpy.ops.object.mode_set(mode='OBJECT')
                raise ValueError('Duas articulações ficaram sobrepostas.')
        bpy.ops.object.mode_set(mode='OBJECT')
        rig['ez3d_semantic_map']=json.dumps({n:p['role'] for n,p in by_name.items() if p['role']})
        rig['ez3d_joint_config']=json.dumps(configuration)
        bpy.context.view_layer.update()
    # UniRig/Blender's glTF round-trip emits the bone display Icosphere as a
    # separate 42-vertex mesh. It is a rig widget, never part of the character.
    for obj in list(bpy.context.scene.objects):
        if obj.type=='MESH' and obj.name=='Icosphere' and len(obj.data.vertices)==42 and not obj.vertex_groups:
            bpy.data.objects.remove(obj,do_unlink=True)
    meshes=[o for o in bpy.context.scene.objects if o.type=='MESH']
    if not meshes:raise ValueError('Malha UniRig ausente.')
    proxy_reports=[]
    if cfg.get('proxy_source'):
        from proxy_skin import transfer_learned_skin
        for mesh in meshes:
            if cfg.get('separate_feet'):
                from foot_separation import separate_touching_feet
                progress('Separando a ligação indevida entre os pés',0,100,1)
                print(json.dumps({'foot_separation':separate_touching_feet(mesh,rig,humanoid_map(rig))}),flush=True)
            proxy_reports.append(transfer_learned_skin(mesh,rig,cfg['proxy_source'],cfg['proxy_reference'],
                lambda message,fraction:progress(message,round(fraction*100),100,1),humanoid_map(rig) if cfg.get('proxy_regions') else None,
                smooth_surface=cfg.get('smooth_surface',False)))
        rig['ez3d_weight_method']='learned_proxy_barycentric_v1'
    elif (not cfg.get('rig_scene') and not cfg.get('native_tokenrig')) or cfg.get('refine_weights'):
        analytic=cfg.get('weight_method','surface_analytic')=='surface_analytic'
        for mesh in meshes:refine_weights(mesh,rig,analytic)
        if analytic:
            rig['ez3d_weight_method']='surface_analytic_v7'
    if cfg.get('rear_garment'):
        mapping = humanoid_map(rig)
        count = sum(bind_rear_garment(mesh, rig, mapping) for mesh in meshes)
        print(json.dumps({'rear_garment_vertices': count}), flush=True)
    progress('Pesos preparados; verificando vínculo',1,3,1)
    total=weighted=0
    for mesh in meshes:
        names={g.index for g in mesh.vertex_groups if g.name in rig.data.bones}
        for v in mesh.data.vertices:
            total+=1;weighted+=sum(g.weight for g in v.groups if g.group in names)>.99
        if not any(m.type=='ARMATURE' for m in mesh.modifiers):raise ValueError('Malha sem vínculo ao esqueleto.')
        if not mesh.data.uv_layers or not mesh.data.materials:raise ValueError('Materiais ou UVs perdidos no rig.')
    coverage=weighted/max(1,total)
    if coverage<.99:raise ValueError(f'Cobertura de pesos incompleta: {coverage:.1%}')
    try:mapping=humanoid_map(rig);mapping_error=None
    except ValueError as error:mapping={};mapping_error=str(error)
    frame_count=0
    if cfg.get('motion'):
        if not mapping:raise ValueError(mapping_error)
        spec=importlib.util.spec_from_file_location('soma_export',Path(cfg['kimodo_root'])/'scripts/export_glb.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        source=module.SKELETONS['soma30'];names=source['names'];parents=source['parents'];offsets=source['offsets']
        rest=[]
        for i,offset in enumerate(offsets):rest.append(Vector(offset)+(rest[parents[i]] if parents[i]>=0 else Vector()))
        rotations=np.fromfile(Path(cfg['motion'])/'local_rotations_xyzw.f32',dtype='<f4').reshape(-1,len(names),4)
        roots=np.fromfile(Path(cfg['motion'])/'root_positions.f32',dtype='<f4').reshape(-1,3)
        if len(roots)!=len(rotations) or not np.isfinite(rotations).all() or not np.isfinite(roots).all():raise ValueError('Movimento inválido.')
        frame_count=len(roots);index={n:i for i,n in enumerate(names)}
        inverse={v:k for k,v in mapping.items()};hip=rig.data.bones[inverse['Hips']]
        # Convert SOMA motion into the target's horizontal body frame.
        # Apply rotations as changes from rest so an identity clip retains
        # the learned bind pose, including the original foot pitch.
        lateral = (rig.data.bones[inverse['LeftLeg']].head_local
                   - rig.data.bones[inverse['RightLeg']].head_local)
        lateral.z = 0
        if lateral.length < 1e-6:
            raise ValueError('Não foi possível determinar a orientação do quadril.')
        lateral.normalize()
        vertical = Vector((0, 0, 1))
        forward = lateral.cross(vertical)
        C = Matrix((lateral, vertical, forward)).transposed()
        C_inverse = C.transposed()
        foot=rig.data.bones[inverse['LeftFoot']]
        motion_scale=(hip.head_local-foot.head_local).length/max(.01,(rest[index['Hips']]-rest[index['LeftFoot']]).length)
        rig.animation_data_create();rig.animation_data.action=bpy.data.actions.new('RequestedMotion')
        for frame in range(frame_count):
            progress('Adaptando movimento ao esqueleto',frame,frame_count*2+1,0)
            bpy.context.scene.frame_set(frame+1);world=[]
            for i in range(len(names)):
                x,y,z,w=map(float,rotations[frame,i]);q=Quaternion((w,x,y,z));q.normalize()
                world.append(q.to_matrix() if parents[i]<0 else world[parents[i]]@q.to_matrix())
            for bone in rig.data.bones:
                if bone.name not in mapping:continue
                i=index[mapping[bone.name]];pose=rig.pose.bones[bone.name]
                rotation=C@world[i]@C_inverse@bone.matrix_local.to_3x3()
                target=rotation.to_4x4()
                if bone==hip:
                    delta=Vector(roots[frame])-Vector((float(roots[0,0]),rest[0].y,float(roots[0,2])))
                    target.translation=bone.head_local+(C@delta)*motion_scale
                else:target.translation=rig.pose.bones[bone.parent.name].matrix@bone.parent.matrix_local.inverted()@bone.head_local
                pose.rotation_mode='QUATERNION';pose.matrix=target
                # Motion is rotation plus root translation, never bone scaling.
                pose.scale=(1,1,1)
                bpy.context.view_layer.update()
                for prop in ('location','rotation_quaternion','scale'):pose.keyframe_insert(prop,frame=frame+1)
        bpy.context.scene.frame_end=frame_count;bpy.context.scene.render.fps=30
        for curve in rig.animation_data.action.fcurves:
            for key in curve.keyframe_points:key.interpolation='LINEAR'
    output.parent.mkdir(parents=True,exist_ok=True)
    stretch_max=1.0;seam_gap_max=0.0
    worst_visible_edge=None
    worst_stretch_frame=None
    rest_edges={}
    for mesh in meshes:
        edges=edge_array(mesh.data)
        points=vertex_array(mesh.data)
        lengths=np.linalg.norm(points[edges[:,0]]-points[edges[:,1]],axis=1)
        height=max(np.ptp(points,axis=0))
        _,first,groups,counts=np.unique(np.round(points/max(height*1e-6,1e-9)).astype(np.int64),axis=0,return_index=True,return_inverse=True,return_counts=True)
        duplicated=np.flatnonzero(counts[groups]>1)
        rest_edges[mesh.name]=(edges,lengths,height,duplicated,first[groups[duplicated]])
    for frame in range(1,frame_count+1) if frame_count else [1]:
        progress('Verificando deformação quadro a quadro',frame_count+frame-1,frame_count*2+1,1) if frame_count else progress('Verificando malha e materiais',2,3,1)
        bpy.context.scene.frame_set(frame);dg=bpy.context.evaluated_depsgraph_get()
        for mesh in meshes:
            bounds=[mesh.evaluated_get(dg).matrix_world@Vector(p) for p in mesh.evaluated_get(dg).bound_box]
            if any(not math.isfinite(v) or abs(v)>100 for p in bounds for v in p):raise ValueError('Deformação inválida.')
            if frame_count:
                evaluated=mesh.evaluated_get(dg);data=evaluated.to_mesh()
                edges,lengths,height,duplicates,originals=rest_edges[mesh.name];points=vertex_array(data)
                gap=float(np.linalg.norm(points[duplicates]-points[originals],axis=1).max()) if len(duplicates) else 0
                seam_gap_max=max(seam_gap_max,gap)
                if gap>height*.001:raise ValueError('A animação abriu uma costura da malha. Modelo preservado para revisão.')
                current=np.linalg.norm(points[edges[:,0]]-points[edges[:,1]],axis=1)
                ratios=current/np.maximum(lengths,height*.0005)
                if len(ratios) and float(ratios.max())>stretch_max:
                    stretch_max=float(ratios.max())
                    worst_stretch_frame=frame
                evaluated.to_mesh_clear()
                visible=np.flatnonzero(current>height*.025)
                if len(visible):
                    edge=int(visible[np.argmax(ratios[visible])])
                    if worst_visible_edge is None or ratios[edge]>worst_visible_edge['ratio']:
                        worst_visible_edge={'frame':frame,'mesh':mesh.name,'ratio':float(ratios[edge]),
                            'length':float(current[edge]),'height_fraction':float(current[edge]/height),
                            'vertices':[
                                {'position':list(mesh.data.vertices[int(v)].co),
                                 'weights':[(mesh.vertex_groups[g.group].name,g.weight)
                                            for g in mesh.data.vertices[int(v)].groups]}
                                for v in edges[edge]]}
                failing=visible[ratios[visible]>12]
                if len(failing):
                    worst=failing[np.argsort(ratios[failing])[-10:][::-1]]
                    diagnostic={'frame':frame,'max_ratio':float(ratios[worst[0]]),'edges':[
                        {'ratio':float(ratios[e]),'length':float(current[e]),'vertices':[
                            {'position':list(mesh.data.vertices[int(v)].co),'weights':[(mesh.vertex_groups[g.group].name,g.weight) for g in mesh.data.vertices[int(v)].groups]}
                            for v in edges[e]]} for e in worst]}
                    output.with_suffix('.failure.json').write_text(json.dumps(diagnostic,indent=2),encoding='utf-8')
                    if cfg.get('diagnostic_preview'):
                        bpy.ops.export_scene.gltf(filepath=cfg['diagnostic_preview'],export_format='GLB',export_animations=True,export_skins=True)
                    raise ValueError('O movimento esticou a malha além do limite. Modelo preservado; animação bloqueada para revisão dos pesos.')
    bpy.context.scene.frame_set(1)
    if not cfg.get('motion'):
        bpy.ops.wm.save_as_mainfile(filepath=str(output.with_suffix('.blend')))
        output.with_suffix('.unirig.json').write_text(json.dumps({'mapping':mapping,'mapping_error':mapping_error,
            'joint_config':json.loads(rig['ez3d_joint_config']) if rig.get('ez3d_joint_config') else None}),encoding='utf-8')
    bpy.ops.object.select_all(action='DESELECT')
    progress('Exportando arquivos do personagem',frame_count*2 if frame_count else 2,frame_count*2+1 if frame_count else 3,2)
    for obj in meshes+[rig]:obj.select_set(True)
    bpy.ops.export_scene.gltf(filepath=cfg['preview'],export_format='GLB',use_selection=True,export_skins=True,export_animations=bool(frame_count))
    bpy.ops.export_scene.fbx(filepath=str(output),use_selection=True,object_types={'ARMATURE'} if cfg.get('motion') else {'ARMATURE','MESH'},
        add_leaf_bones=False,bake_anim=bool(frame_count),bake_anim_use_all_actions=False,bake_anim_use_nla_strips=False,
        path_mode='COPY',embed_textures=True,axis_forward='-Y',axis_up='Z')
    report={'method':rig.get('ez3d_rig_backend','UniRig')+'_learned_skeleton_and_skin','bones':len(rig.data.bones),'vertices':total,
        'weighted_fraction':coverage,'animation_frames':frame_count,'motion_compatible':bool(mapping),
        'mapping_error':mapping_error,'technical_validation':True,'visual_approval_required':True}
    report['max_edge_stretch']=round(stretch_max,3)
    report['max_edge_stretch_frame']=worst_stretch_frame
    report['max_seam_gap']=seam_gap_max
    if worst_visible_edge:report['worst_visible_edge']=worst_visible_edge
    report['validated_frames']=frame_count or 1
    report['weight_refinement']=rig.get('ez3d_weight_method','surface_analytic_v7')
    if proxy_reports:report.update(weight_refinement='learned_proxy_barycentric_v1',proxy_transfer=proxy_reports)
    report['finger_motion']='SOMA hand inheritance; independent finger articulation not generated'
    output.with_suffix('.validation.json').write_text(json.dumps(report,indent=2),encoding='utf-8');print(json.dumps(report),flush=True)
    progress('Arquivos concluídos',frame_count*2+1 if frame_count else 3,frame_count*2+1 if frame_count else 3,2)


if __name__=='__main__':
    main(sys.argv[1]);import os;sys.stdout.flush();sys.stderr.flush();os._exit(0)

