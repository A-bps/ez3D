"""Smooth inverse correspondence from model-screen points to reference UVs."""
import numpy as np


def fit_alignment(target, reference):
    target=np.asarray(target,dtype=float);reference=np.asarray(reference,dtype=float)
    if target.shape!=(6,2) or reference.shape!=(6,2) or not np.isfinite(target).all() or not np.isfinite(reference).all():
        raise ValueError('Marque os mesmos seis pontos no modelo e na referência.')
    if (target<0).any() or (target>1).any() or (reference<0).any() or (reference>1).any():raise ValueError('Os pontos precisam estar dentro das imagens.')
    if np.linalg.matrix_rank(np.c_[np.ones(6),reference])<3:raise ValueError('Distribua também os pontos da referência pelo rosto.')
    center=target.mean(axis=0);scale=float(np.max(np.ptp(target,axis=0)))
    if scale<1e-5:raise ValueError('Os pontos do modelo estão muito próximos.')
    points=(target-center)/scale
    if np.linalg.matrix_rank(np.c_[np.ones(6),points])<3:raise ValueError('Os pontos não podem ficar todos em uma linha.')
    if min(np.linalg.norm(points[i]-points[j]) for i in range(6) for j in range(i))<.01:
        raise ValueError('Há pontos repetidos ou muito próximos no modelo.')
    delta=points[:,None,:]-points[None,:,:];r2=(delta*delta).sum(axis=-1)
    kernel=r2*np.log(np.maximum(r2,1e-12));poly=np.c_[np.ones(6),points]
    system=np.block([[kernel+np.eye(6)*1e-8,poly],[poly.T,np.zeros((3,3))]])
    coeff=np.linalg.solve(system,np.r_[reference,np.zeros((3,2))])
    return dict(center=center.tolist(),scale=scale,points=points.tolist(),coeff=coeff.tolist())


def warp_points(coords, fit):
    coords=np.asarray(coords,dtype=float);shape=coords.shape
    points=(coords.reshape(-1,2)-fit['center'])/fit['scale']
    delta=points[:,None,:]-np.asarray(fit['points'])[None,:,:];r2=(delta*delta).sum(axis=-1)
    basis=np.c_[r2*np.log(np.maximum(r2,1e-12)),np.ones(len(points)),points]
    return (basis@np.asarray(fit['coeff'])).reshape(shape)
