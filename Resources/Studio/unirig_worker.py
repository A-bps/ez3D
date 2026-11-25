"""Isolated Linux UniRig phase. Each invocation releases its CUDA context on exit."""
import json
import os
from pathlib import Path
import runpy
import sys


def main(request, phase):
    # Official VAST checkpoints contain Box configuration objects, not tensors alone.
    os.environ['TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD']='1'
    cfg=json.loads(Path(request).read_text(encoding='utf-8-sig'))
    repo=Path('/opt/ez3d/UniRig'); os.chdir(repo);sys.path.insert(0,str(repo))
    folder=Path(cfg['folder']);folder.mkdir(parents=True,exist_ok=True)
    from src.data import extract
    source=cfg['input'] if phase in ('extract','skeleton') else str(folder/'skeleton.fbx')
    data=folder/('input_data' if phase in ('extract','skeleton') else 'skin_data')
    # Upstream joins absolute input paths onto npz_dir, escaping that directory.
    # Explicit per-job paths keep intermediate files away from the source asset.
    extract.get_files=lambda **kwargs: [(source,str(data))]
    if phase in ('extract','extract_skin'):
        extract.extract_builtin(output_folder=str(folder),target_count=50000,
            num_runs=1,id=0,time='studio',files=[(source,str(data))])
        if not (data/'raw_data.npz').is_file():raise RuntimeError('Extração UniRig não produziu dados.')
    elif phase in ('skeleton','skin'):
        import src.inference.download as download
        def local_checkpoint(name):
            path=repo/name
            if not path.is_file():raise RuntimeError('Checkpoint UniRig local ausente: '+name)
            return str(path)
        download.download=local_checkpoint
        task='quick_inference_skeleton_articulationxl_ar_256' if phase=='skeleton' else 'quick_inference_unirig_skin'
        output=folder/('skeleton.fbx' if phase=='skeleton' else 'skin.fbx')
        sys.argv=['run.py','--task=configs/task/'+task+'.yaml','--seed=12345',
            '--input='+source,'--npz_dir='+str(folder),'--output='+str(output),'--data_name=raw_data.npz']
        runpy.run_path(str(repo/'run.py'),run_name='__main__')
        if not output.is_file():raise RuntimeError('UniRig não exportou '+phase)
    elif phase=='merge':
        import bpy
        import src.inference.merge as merge
        # Upstream removes items while iterating live Blender collections, which
        # can leave debug meshes behind. Snapshot the collections before removal.
        def clean():
            bpy.ops.wm.read_factory_settings(use_empty=True)
        merge.clean_bpy=clean
        merge.transfer(str(folder/'skin.fbx'),cfg['input'],str(folder/'rigged.glb'),False)
        if not (folder/'rigged.glb').is_file():raise RuntimeError('UniRig não preservou a malha texturizada.')
    else:raise ValueError('Fase inválida')


if __name__=='__main__':main(*sys.argv[1:])
