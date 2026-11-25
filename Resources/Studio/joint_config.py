"""Validate user-authored joint annotations before starting a copy-only rig job."""
import math

ROLES={'Hips','Spine1','Spine2','Chest','Neck1','Head'} | {
    side+part for side in ('Left','Right') for part in
    ('Shoulder','Arm','ForeArm','Hand','Leg','Shin','Foot','ToeBase')}


def validate_joint_config(data):
    if not isinstance(data,dict) or data.get('coordinate_space')!='gltf_y_up':
        raise ValueError('Sistema de coordenadas das articulações inválido.')
    points=data.get('joints')
    if not isinstance(points,list) or not 1<=len(points)<=512:
        raise ValueError('Lista de articulações inválida.')
    names=set();roles=set();result=[]
    for point in points:
        if not isinstance(point,dict):raise ValueError('Articulação inválida.')
        name=point.get('name');position=point.get('position');role=point.get('role','')
        if not isinstance(name,str) or not name or len(name)>100 or name in names:
            raise ValueError('Nome de articulação inválido ou repetido.')
        if not isinstance(position,list) or len(position)!=3 or any(
            isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or abs(v)>1000 for v in position):
            raise ValueError('Posição de articulação inválida.')
        if role and role not in ROLES:raise ValueError('Identificação anatômica desconhecida.')
        if role and role in roles and role not in ('Spine1','Spine2','Chest','Neck1'):
            raise ValueError('Uma identificação foi atribuída a mais de uma articulação.')
        names.add(name)
        if role:roles.add(role)
        result.append(dict(name=name,position=position,role=role))
    required={'Hips','Head'} | {s+p for s in ('Left','Right') for p in ('Arm','ForeArm','Hand','Leg','Shin','Foot')}
    if not required.issubset(roles):raise ValueError('Identifique quadril, cabeça, braços, mãos, pernas e pés dos dois lados.')
    return dict(coordinate_space='gltf_y_up',joints=result)
