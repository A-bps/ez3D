"""Load local flow weights without initializing billions of discarded parameters."""
import json
from pathlib import Path
import torch


def install():
    from trellis2 import models
    from safetensors.torch import load_file
    original=models.from_pretrained
    def load(path,**kwargs):
        config_path=Path(str(path)+'.json');weights_path=Path(str(path)+'.safetensors')
        if not config_path.is_file() or not weights_path.is_file():return original(path,**kwargs)
        cfg=json.loads(config_path.read_text())
        if cfg['name'] not in ('SLatFlowModel','SparseStructureFlowModel'):return original(path,**kwargs)
        try:
            with torch.device('meta'):model=getattr(models,cfg['name'])(**cfg['args'],**kwargs)
            state=load_file(str(weights_path))
            expected=model.state_dict()
            if cfg['name']=='SparseStructureFlowModel' and 'rope_phases' in expected and 'rope_phases' not in state:
                from trellis2.models.sparse_structure_flow import RotaryPositionEmbedder
                args=cfg['args'];resolution=args['resolution']
                coords=torch.stack(torch.meshgrid(*[torch.arange(resolution,device='cpu')]*3,indexing='ij'),dim=-1).reshape(-1,3)
                state['rope_phases']=RotaryPositionEmbedder(model.model_channels//model.num_heads,3)(coords)
            state={key:value.to(dtype=expected[key].dtype) if key in expected else value for key,value in state.items()}
            model.load_state_dict(state,strict=True,assign=True)
            # RoPE frequency caches are plain attributes in upstream code and
            # therefore absent from state_dict/named_buffers.
            for module in model.modules():
                if hasattr(module,'freqs') and getattr(module.freqs,'is_meta',False):
                    frequencies=torch.arange(module.freq_dim,dtype=torch.float32,device='cpu')/module.freq_dim
                    module.freqs=module.rope_freq[0]/(module.rope_freq[1]**frequencies)
                if any(isinstance(v,torch.Tensor) and v.is_meta for v in vars(module).values()):
                    raise RuntimeError('Unmaterialized model tensor attribute')
            if any(p.is_meta for p in list(model.parameters())+list(model.buffers())):
                raise RuntimeError('Unmaterialized model buffer')
            print('Pesos locais carregados diretamente: '+config_path.stem,flush=True)
            return model
        except (RuntimeError,NotImplementedError,ValueError) as error:
            print('Carregamento compatível: '+str(error)[:160],flush=True)
            return original(path,**kwargs)
    models.from_pretrained=load
