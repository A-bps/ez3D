"""Explicit, reproducible native TRELLIS generation/export profiles."""
PROFILES={
    'character':dict(pipeline_type='1024_cascade',sparse_steps=20,shape_steps=32,texture_steps=20,
                     remesh=True,remesh_project=.9,triangle_budget=500000,gpu_mode='staged'),
    'character_maximum':dict(pipeline_type='1024_cascade',sparse_steps=24,shape_steps=36,texture_steps=24,
                             remesh=True,remesh_project=.9,triangle_budget=2000000,gpu_mode='staged'),
    'maximum':dict(pipeline_type='1024_cascade',sparse_steps=20,shape_steps=20,texture_steps=16,
                   remesh=True,remesh_project=0.,triangle_budget=2000000,gpu_mode='resident'),
    'head_compact':dict(pipeline_type='512',sparse_steps=12,shape_steps=16,texture_steps=12,
                        remesh=True,remesh_project=.9,triangle_budget=120000,gpu_mode='staged'),
    'balanced':dict(pipeline_type='1024_cascade',sparse_steps=12,shape_steps=12,texture_steps=12,
                    remesh=True,remesh_project=0.,triangle_budget=240000,gpu_mode='resident'),
    'anatomy_detail':dict(pipeline_type='1024',sparse_steps=32,shape_steps=32,texture_steps=24,
                         remesh=True,remesh_project=.9,triangle_budget=500000,gpu_mode='staged'),
    'head_detail':dict(pipeline_type='1024_cascade',sparse_steps=20,shape_steps=24,texture_steps=16,
                      remesh=True,remesh_project=.9,triangle_budget=150000,gpu_mode='staged'),
}


def generation_settings(profile):
    if profile not in PROFILES:raise ValueError('Perfil de geração desconhecido.')
    return dict(PROFILES[profile],seed=42,quality_profile=profile)
