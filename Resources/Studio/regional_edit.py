"""Localized surface transfer. Original vertices/UVs outside the brush stay intact."""
import json
from pathlib import Path
import sys
import bpy
import numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree


def main(request):
    cfg=json.loads(Path(request).read_text(encoding='utf-8-sig'))
    def progress(message,value):
        path=Path(cfg['output']).parent/'trellis-request.progress.json'
        try:
            temp=path.with_suffix('.tmp');temp.write_text(json.dumps(dict(message=message,value=value,max=4,stage_index=min(value,3),stage_count=4,measured=False)),encoding='utf-8');temp.replace(path)
        except PermissionError:pass  # Progress readers must never abort an edit.
    progress('Preparando seleção e preservando o original',0)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=cfg['input'])
    meshes=[o for o in bpy.context.scene.objects if o.type=='MESH']
    if not meshes:raise ValueError('Malha original ausente.')
    brushes=[(Vector((b['point'][0],-b['point'][2],b['point'][1])),float(b['radius'])) for b in cfg.get('brushes',[])]
    selection=cfg.get('selection');projection=None
    alignment=cfg.get('alignment')
    if alignment:
        from regional_alignment import fit_alignment,warp_points
        alignment=fit_alignment(alignment['target'],alignment['reference'])
    if selection:
        projection=np.array(selection['matrix'],dtype=float).reshape(4,4).T
        polygon=np.array(selection['polygon'],dtype=float)
        polygon_low=polygon.min(axis=0);polygon_span=np.maximum(np.ptp(polygon,axis=0),1e-6)
    def project(points):
        gltf=np.stack([points[:,0],points[:,2],-points[:,1],np.ones(len(points))],axis=1)
        clip=gltf@projection.T;w=clip[:,3]
        ndc=clip[:,:2]/np.where(abs(w)<1e-10,1e-10,w)[:,None]
        return np.stack([ndc[:,0]*.5+.5,.5-ndc[:,1]*.5],axis=1),w
    def polygon_mask(points):
        screen,w=project(points);x,y=screen.T
        inside=np.zeros(len(points),dtype=bool);distance=np.full(len(points),np.inf)
        for a,b in zip(polygon,np.roll(polygon,-1,axis=0)):
            dy=b[1]-a[1]
            inside^=((a[1]>y)!=(b[1]>y))&(x<(b[0]-a[0])*(y-a[1])/(dy if abs(dy)>1e-12 else 1e-12)+a[0])
            edge=b-a;t=np.clip((screen-a)@edge/max(float(edge@edge),1e-12),0,1)
            distance=np.minimum(distance,np.linalg.norm(screen-(a+t[:,None]*edge),axis=1))
        t=np.clip(distance/max(float(polygon_span.min())*.07,1e-6),0,1)
        return (t*t*(3-2*t))*inside*(w>0)
    selected=[];masks={}
    for obj in meshes:
        points=np.array([obj.matrix_world@v.co for v in obj.data.vertices]);mask=polygon_mask(points) if selection else np.zeros(len(points))
        if selection and selection.get('camera'):
            c=selection['camera'];eye=np.array([c[0],-c[2],c[1]])
            normal=np.array([v.normal[:] for v in obj.data.vertices])@np.linalg.inv(np.array(obj.matrix_world)[:3,:3])
            mask*=np.sum(normal*(eye-points),axis=1)>0
        for center,radius in brushes:
            t=np.clip(1-np.linalg.norm(points-np.array(center),axis=1)/radius,0,1)
            mask=np.maximum(mask,t*t*(3-2*t))
        masks[obj.name]=mask
        selected.extend(points[mask>.05])
    if len(selected)<8:raise ValueError('Selecione uma região maior da superfície.')
    progress('Transferindo a forma dentro da seleção',1)
    points=np.array(selected);low=points.min(axis=0);high=points.max(axis=0);span=np.maximum(high-low,1e-5)
    moved=0;max_displacement=0.
    if cfg.get('patch'):
        before=set(bpy.context.scene.objects)
        bpy.ops.import_scene.gltf(filepath=cfg['patch'])
        patches=[o for o in bpy.context.scene.objects if o not in before and o.type=='MESH']
        vertices=[];faces=[]
        for obj in patches:
            offset=len(vertices);vertices.extend([obj.matrix_world@v.co for v in obj.data.vertices])
            obj.data.calc_loop_triangles();faces.extend([tuple(offset+i for i in t.vertices) for t in obj.data.loop_triangles])
        patch_points=np.array(vertices);pmin=patch_points.min(axis=0);pspan=np.maximum(np.ptp(patch_points,axis=0),1e-5)
        # Uniform scaling preserves the generated part's proportions.
        scale=min(span[0]/pspan[0],span[2]/pspan[2])
        patch_points=(patch_points-(pmin+pspan*.5))*scale+(low+high)*.5
        tree=BVHTree.FromPolygons([Vector(v) for v in patch_points],faces,all_triangles=True)
        limit=float(np.max(span))*.12
        for obj in meshes:
            inverse=obj.matrix_world.inverted()
            for v,weight in zip(obj.data.vertices,masks[obj.name]):
                if weight<=0:continue
                original=obj.matrix_world@v.co;hit=tree.find_nearest(original)
                if hit[0] is None:continue
                delta=hit[0]-original
                if delta.length>limit:delta=delta.normalized()*limit
                delta*=float(weight)*.5
                v.co=inverse@(original+delta);moved+=1;max_displacement=max(max_displacement,delta.length)
            obj.data.update()
        for obj in list(bpy.context.scene.objects):
            if obj not in before:bpy.data.objects.remove(obj,do_unlink=True)
    # Project the reference only onto UV pixels covered by selected triangles.
    # Composite onto copies so unselected texture pixels and source images remain intact.
    from PIL import Image
    photo=np.asarray(Image.open(cfg['reference']).convert('RGB'),dtype=np.float32)/255
    # Byte-backed bpy image pixels are stored in the image's encoded space.
    # Converting only the reference to linear darkens the painted face twice.
    progress('Aplicando referência na textura selecionada',2)
    changed_pixels=0
    for obj in meshes:
        uv=obj.data.uv_layers.active
        if not uv:continue
        mask=masks[obj.name];obj.data.calc_loop_triangles();cache={}
        for slot in obj.material_slots:
            if slot.material:slot.material=slot.material.copy()
        for tri in obj.data.loop_triangles:
            weights=mask[list(tri.vertices)]
            if weights.max()<=0:continue
            material=obj.material_slots[tri.material_index].material
            if not material or not material.use_nodes:continue
            shader=next((n for n in material.node_tree.nodes if n.type=='BSDF_PRINCIPLED'),None)
            links=shader.inputs['Base Color'].links if shader else []
            node=links[0].from_node if links else None
            if node is None or node.type!='TEX_IMAGE' or node.image is None:continue
            if node not in cache:
                node.image=node.image.copy();image=node.image;w,h=image.size
                pixels=np.empty(w*h*4,dtype=np.float32);image.pixels.foreach_get(pixels)
                cache[node]=(image,pixels.reshape(h,w,4),w,h,pixels.reshape(h,w,4).copy(),np.zeros((h,w),dtype=np.float32))
            image,pixels,w,h,original_pixels,coverage=cache[node]
            uvs=np.array([uv.data[i].uv[:] for i in tri.loops]);screen=uvs*np.array([w-1,h-1])
            lo=np.maximum(0,np.floor(screen.min(axis=0)).astype(int));hi=np.minimum([w-1,h-1],np.ceil(screen.max(axis=0)).astype(int))
            if np.any(hi<lo):continue
            xx,yy=np.meshgrid(np.arange(lo[0],hi[0]+1),np.arange(lo[1],hi[1]+1));coords=np.stack([xx,yy],axis=-1)
            a,b,c=screen;den=(b[1]-c[1])*(a[0]-c[0])+(c[0]-b[0])*(a[1]-c[1])
            if abs(den)<1e-8:continue
            u=((b[1]-c[1])*(coords[...,0]-c[0])+(c[0]-b[0])*(coords[...,1]-c[1]))/den
            v=((c[1]-a[1])*(coords[...,0]-c[0])+(a[0]-c[0])*(coords[...,1]-c[1]))/den
            bary=np.stack([u,v,1-u-v],axis=-1);inside=(bary>=-1e-6).all(axis=-1)
            if not inside.any():continue
            vertex_positions=np.array([obj.matrix_world@obj.data.vertices[i].co for i in tri.vertices])
            pos=bary@vertex_positions
            if projection is not None:
                screen,_=project(pos.reshape(-1,3));screen=screen.reshape(pos.shape[:-1]+(2,))
                if alignment:
                    mapped=warp_points(screen,alignment);px=mapped[...,0];py=mapped[...,1]
                else:
                    px=(screen[...,0]-polygon_low[0])/polygon_span[0];py=(screen[...,1]-polygon_low[1])/polygon_span[1]
            else:
                px=np.clip((pos[...,0]-low[0])/span[0],0,1);py=np.clip(1-(pos[...,2]-low[2])/span[2],0,1)
            valid=(px>=0)&(px<=1)&(py>=0)&(py<=1)
            fx=np.clip(px,0,1)*(photo.shape[1]-1);fy=np.clip(py,0,1)*(photo.shape[0]-1)
            ix=fx.astype(int);iy=fy.astype(int);dx=(fx-ix)[...,None];dy=(fy-iy)[...,None]
            nx=np.minimum(ix+1,photo.shape[1]-1);ny=np.minimum(iy+1,photo.shape[0]-1)
            colors=(photo[iy,ix]*(1-dx)+photo[iy,nx]*dx)*(1-dy)+(photo[ny,ix]*(1-dx)+photo[ny,nx]*dx)*dy
            alpha=np.clip(bary@weights,0,1)*inside*valid*float(cfg.get('texture_strength',.75))
            region=pixels[lo[1]:hi[1]+1,lo[0]:hi[0]+1,:3]
            original=original_pixels[lo[1]:hi[1]+1,lo[0]:hi[0]+1,:3]
            covered=coverage[lo[1]:hi[1]+1,lo[0]:hi[0]+1];replace=alpha>covered
            blended=original*(1-alpha[...,None])+colors*alpha[...,None]
            region[replace]=blended[replace];covered[replace]=alpha[replace];changed_pixels+=int(replace.sum())
        for image,pixels,w,h,original_pixels,coverage in cache.values():
            image.pixels.foreach_set(pixels.ravel());image.update();image.pack()
    if not moved and not changed_pixels:raise ValueError('A região não produziu alterações; selecione uma área texturizada maior.')
    progress('Salvando uma nova versão do modelo',3)
    bpy.ops.object.select_all(action='DESELECT')
    for obj in meshes:obj.select_set(True)
    bpy.ops.export_scene.gltf(filepath=cfg['output'],export_format='GLB',use_selection=True,export_animations=False)
    output=Path(cfg['output'])
    import struct
    raw=output.read_bytes()
    if len(raw)<28 or raw[:4]!=b'glTF' or struct.unpack_from('<I',raw,8)[0]!=len(raw):raise ValueError('GLB editado incompleto.')
    chunk=struct.unpack_from('<I',raw,12)[0];document=json.loads(raw[20:20+chunk])
    if not document.get('meshes'):raise ValueError('GLB editado sem malha.')
    Path(cfg['output']).with_suffix('.edit.json').write_text(json.dumps(dict(moved_vertices=moved,max_displacement=max_displacement,painted_pixel_samples=changed_pixels,original_preserved=True,alignment='six_point_thin_plate_spline' if alignment else 'bounding_box_projection',method='bounded_surface_transfer_and_reference_projection',requires_visual_review=True)),encoding='utf-8')
    progress('Versão editada salva',4)


if __name__=='__main__':
    main(sys.argv[1]);sys.stdout.flush();sys.stderr.flush()
    # Windows bpy can access freed native image buffers during interpreter
    # teardown. All files are synchronously written and validated above.
    import os;os._exit(0)
