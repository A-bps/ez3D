"""Continuous transfer from UniRig's learned low-resolution skin, without changing geometry."""
import json
import struct
import time
from pathlib import Path
import numpy as np


def glb_joint_positions(path):
    from mathutils import Quaternion
    with Path(path).open('rb') as stream:
        header=stream.read(12)
        if header[:4]!=b'glTF':raise ValueError('Referência GLB inválida.')
        size,kind=struct.unpack('<II',stream.read(8));doc=json.loads(stream.read(size))
    nodes=doc['nodes'];parents={child:i for i,node in enumerate(nodes) for child in node.get('children',[])};world={}
    def matrix(i):
        if i in world:return world[i]
        node=nodes[i]
        if 'matrix' in node:m=np.asarray(node['matrix']).reshape(4,4).T
        else:
            x,y,z,w=node.get('rotation',[0,0,0,1]);m=np.eye(4)
            m[:3,:3]=np.asarray(Quaternion((w,x,y,z)).to_matrix())@np.diag(node.get('scale',[1,1,1]));m[:3,3]=node.get('translation',[0,0,0])
        world[i]=matrix(parents[i])@m if i in parents else m;return world[i]
    result={}
    for i,node in enumerate(nodes):
        if node.get('name','').startswith('bone_'):
            x,y,z=matrix(i)[:3,3];result[node['name']]=np.array([x,-z,y])
    return result


def transfer_learned_skin(mesh,rig,source,reference,progress=None,semantic=None,smooth_surface=False):
    import bpy
    import open3d as o3d
    from scipy.sparse import coo_matrix,diags
    from mathutils import Vector
    started=time.monotonic()
    def step(message,fraction):
        if progress:progress(message,fraction)
    step('Lendo os pesos aprendidos pelo UniRig',0)
    existing=set(bpy.context.scene.objects)
    bpy.ops.import_scene.fbx(filepath=str(source),ignore_leaf_bones=False,use_image_search=False)
    imported=set(bpy.context.scene.objects)-existing
    try:
        source_rig=next(o for o in imported if o.type=='ARMATURE')
        low=max((o for o in imported if o.type=='MESH'),key=lambda o:len(o.data.vertices))
        names=[b.name for b in rig.data.bones];indices={n:i for i,n in enumerate(names)}
        target_heads=glb_joint_positions(reference)
        common=[n for n in names if n in source_rig.data.bones and n in target_heads]
        if len(common)<12:raise ValueError('Articulações insuficientes para alinhar a transferência.')
        a=np.array([source_rig.matrix_world@source_rig.data.bones[n].head_local for n in common]);b=np.array([target_heads[n] for n in common])
        ac=a-a.mean(0);bc=b-b.mean(0);u,s,vt=np.linalg.svd(ac.T@bc);rotation=u@vt;scale=s.sum()/np.sum(ac*ac);offset=b.mean(0)-a.mean(0)@rotation*scale
        height=np.ptp(b,axis=0).max();error=np.sqrt(np.mean(np.sum((a@rotation*scale+offset-b)**2,axis=1)))
        if error>height*.01:raise ValueError(f'Alinhamento do vínculo fora da tolerância ({error/height:.1%} da altura).')
        vertices=np.empty(len(low.data.vertices)*3,dtype=np.float32);low.data.vertices.foreach_get('co',vertices);vertices=vertices.reshape(-1,3)
        lm=np.asarray(low.matrix_world);vertices=(vertices@lm[:3,:3].T+lm[:3,3])@rotation*scale+offset
        low.data.calc_loop_triangles();faces=np.empty(len(low.data.loop_triangles)*3,dtype=np.int32);low.data.loop_triangles.foreach_get('vertices',faces);faces=faces.reshape(-1,3)
        weights=np.zeros((len(vertices),len(names)),dtype=np.float32)
        group_map={g.index:indices[g.name] for g in low.vertex_groups if g.name in indices}
        for vertex in low.data.vertices:
            for group in vertex.groups:
                if group.group in group_map:weights[vertex.index,group_map[group.group]]=group.weight
        if np.any(weights.sum(1)<.99):raise ValueError('Pesos aprendidos incompletos na malha auxiliar.')
        step('Suavizando pesos na malha auxiliar',.15)
        edges=np.concatenate([faces[:,[0,1]],faces[:,[1,2]],faces[:,[2,0]]]);edges=np.unique(np.sort(edges,axis=1),axis=0)
        row=np.r_[edges[:,0],edges[:,1]];col=np.r_[edges[:,1],edges[:,0]]
        adj=coo_matrix((np.ones(len(row)),(row,col)),shape=(len(vertices),len(vertices))).tocsr();adj=diags(1/np.maximum(1,np.asarray(adj.sum(1)).ravel()))@adj
        original=weights.copy()
        for _ in range(16):weights=.3*original+.7*(adj@weights)
        weights/=np.maximum(weights.sum(1)[:,None],1e-20)
        target=np.empty(len(mesh.data.vertices)*3,dtype=np.float32);mesh.data.vertices.foreach_get('co',target);target=target.reshape(-1,3)
        tm=np.asarray(mesh.matrix_world);target=target@tm[:3,:3].T+tm[:3,3]
        # One query per coincident seam position guarantees identical weights.
        _,first,inverse=np.unique(np.round(target/(height*1e-6)).astype(np.int64),axis=0,return_index=True,return_inverse=True)
        query=target[first].astype(np.float32);dense=np.empty((len(query),len(names)),dtype=np.float32)
        scene=o3d.t.geometry.RaycastingScene();scene.add_triangles(o3d.core.Tensor(vertices.astype(np.float32)),o3d.core.Tensor(faces.astype(np.uint32)))
        for begin in range(0,len(query),32768):
            end=min(len(query),begin+32768);answer=scene.compute_closest_points(o3d.core.Tensor(query[begin:end]))
            triangles=faces[answer['primitive_ids'].numpy().astype(np.int64)];uv=answer['primitive_uvs'].numpy();bary=np.c_[1-uv.sum(1),uv]
            out=np.zeros((end-begin,len(names)),dtype=np.float32)
            for k in range(3):out+=weights[triangles[:,k]]*bary[:,k,None]
            dense[begin:end]=out;step('Interpolando o vínculo para a malha detalhada',.25+.5*end/len(query))
        if semantic:
            # Close/touching feet can receive the opposite leg's weights on
            # the coarse prediction. Restrict only the lower-body cross-side
            # leakage, fading the constraint toward the pelvis.
            hips=next((b for b in rig.data.bones if semantic.get(b.name)=='Hips'),None)
            if hips:
                hip=rig.matrix_world@hips.head_local
                joints={role:rig.matrix_world@rig.data.bones[name].head_local for name,role in semantic.items()}
                cutoff=hip.z-height*.02
                # Only impose a hard side boundary inside the explicitly cut
                # lower legs. A hard sagittal boundary above the cut tears
                # connected triangles at the crotch.
                strength=np.clip((cutoff-query[:,2])/(height*.05),0,1)
                for i,bone in enumerate(rig.data.bones):
                    ancestor=bone
                    while ancestor and ancestor.name not in semantic:ancestor=ancestor.parent
                    role=semantic.get(ancestor.name,'') if ancestor else ''
                    if role.endswith(('Leg','Shin','Foot','ToeBase')):
                        wrong=query[:,0]<hip.x if role.startswith('Left') else query[:,0]>hip.x
                        dense[wrong,i]*=1-strength[wrong]
                if np.any(dense.sum(1)<1e-8):raise ValueError('Vínculo de uma região não pôde ser recuperado com segurança.')
        if smooth_surface:
            step('Suavizando transições na malha final',.76)
            edges=np.empty(len(mesh.data.edges)*2,dtype=np.int32)
            mesh.data.edges.foreach_get('vertices',edges)
            edges=np.unique(np.sort(inverse[edges.reshape(-1,2)],axis=1),axis=0)
            edges=edges[edges[:,0]!=edges[:,1]]
            distances=np.linalg.norm(query[edges[:,0]]-query[edges[:,1]],axis=1)
            conductance=1/np.maximum(distances,height*.0005)
            row=np.r_[edges[:,0],edges[:,1]];col=np.r_[edges[:,1],edges[:,0]]
            adjacency=coo_matrix((np.tile(conductance,2),(row,col)),shape=(len(query),len(query))).tocsr()
            totals=np.asarray(adjacency.sum(1)).ravel()
            adjacency=diags(np.divide(1,totals,out=np.zeros_like(totals),where=totals>0))@adjacency
            original=dense.copy()
            for _ in range(32):
                dense=.035*original+.965*(adjacency@dense)
            dense[totals==0]=original[totals==0]
        top=np.argpartition(dense,-4,axis=1)[:,-4:];values=np.take_along_axis(dense,top,axis=1);values/=np.maximum(values.sum(1)[:,None],1e-20)
        top=top[inverse];values=values[inverse]
        step('Gravando os pesos interpolados',.8)
        for group in list(mesh.vertex_groups):mesh.vertex_groups.remove(group)
        for i,name in enumerate(names):
            group=mesh.vertex_groups.new(name=name);rows,slots=np.where(top==i)
            for vertex,slot in zip(rows,slots):
                value=float(values[vertex,slot])
                if value>1e-7:group.add([int(vertex)],value,'REPLACE')
        return dict(method='learned_proxy_barycentric_v1',source_vertices=len(vertices),target_vertices=len(target),alignment_rms=float(error),seconds=round(time.monotonic()-started,2))
    finally:
        for obj in imported:bpy.data.objects.remove(obj,do_unlink=True)
