"""Repair a generated bridge between touching feet on an isolated copy."""
import bmesh
from mathutils import Vector


def separate_touching_feet(mesh,rig,mapping):
    inverse={role:name for name,role in mapping.items()}
    required=('Hips','LeftFoot','RightFoot','LeftShin','RightShin')
    if not all(role in inverse for role in required):raise ValueError('Articulações das pernas incompletas.')
    transform=mesh.matrix_world.inverted()@rig.matrix_world
    points={role:transform@rig.data.bones[inverse[role]].head_local for role in required}
    # Use the same sagittal plane as the weight region constraint.
    middle=points['Hips'].x
    height=max(mesh.dimensions)
    cutoff=points['Hips'].z-height*.02
    bm=bmesh.new()
    try:
        bm.from_mesh(mesh.data)
        # Imported UV seams duplicate vertices; close those topological seams
        # before cutting so the new boundary forms fillable loops. Loop UVs
        # remain independent across the welded vertex.
        bmesh.ops.remove_doubles(bm,verts=list(bm.verts),dist=height*1e-7)
        faces=[f for f in bm.faces if max(v.co.z for v in f.verts)<cutoff]
        crossing=[f for f in faces if min(v.co.x for v in f.verts)<middle<max(v.co.x for v in f.verts)]
        if not crossing:return {'crossing_faces':0,'cut_edges':0}
        edges={e for f in faces for e in f.edges};vertices={v for f in faces for v in f.verts}
        result=bmesh.ops.bisect_plane(bm,geom=list(vertices)+list(edges)+faces,
            plane_co=Vector((middle,0,0)),plane_no=Vector((1,0,0)),dist=height*1e-8,
            clear_inner=False,clear_outer=False)
        seam=[e for e in result['geom_cut'] if isinstance(e,bmesh.types.BMEdge) and all(v.co.z<cutoff for v in e.verts)]
        if not seam:raise ValueError('Não foi possível isolar a ligação entre os pés.')
        bmesh.ops.split_edges(bm,edges=seam)
        boundary=[e for e in bm.edges if e.is_boundary and all(v.co.z<cutoff and abs(v.co.x-middle)<height*1e-6 for v in e.verts)]
        seam_vertices={v for e in boundary for v in e.verts}
        for vertex in seam_vertices:
            centers=[f.calc_center_median().x for f in vertex.link_faces]
            if not centers:continue
            side=1 if sum(centers)/len(centers)>middle else -1
            vertex.co.x=middle+side*height*.00002
        filled=bmesh.ops.holes_fill(bm,edges=boundary,sides=0).get('faces',[])
        uv=bm.loops.layers.uv.active
        for face in filled:
            side=1 if face.calc_center_median().x>middle else -1
            face.normal_update()
            if face.normal.x*side>0:face.normal_flip()
            if uv:
                for loop in face.loops:
                    reference=next((other for other in loop.vert.link_loops if other.face not in filled),None)
                    if reference:loop[uv].uv=reference[uv].uv
            face.smooth=False
        if not filled:raise ValueError('A separação dos pés deixou uma abertura sem fechamento.')
        report=dict(crossing_faces=len(crossing),cut_edges=len(seam),closed_faces=len(filled))
        bm.to_mesh(mesh.data);mesh.data.update();return report
    finally:bm.free()
