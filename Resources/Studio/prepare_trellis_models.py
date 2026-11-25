"""Download only inference weights. Never print credentials or bypass gated access."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import requests
from huggingface_hub import snapshot_download, hf_hub_download, get_token

parser=argparse.ArgumentParser()
parser.add_argument('--models-root',required=True)
parser.add_argument('--work-root',required=True)
parser.add_argument('--wsl-models-root',required=True)
options=parser.parse_args()
root=Path(options.models_root);root.mkdir(parents=True,exist_ok=True)
status=Path(options.work_root)/'trellis-status.json'
studio_token=get_token()

def report(message,ready=False):
    status.write_text(json.dumps({'ready':ready,'message':message},ensure_ascii=False),encoding='utf-8')
    print(message,flush=True)
if shutil.disk_usage(root).free < 23*1024**3: raise RuntimeError('Espaço insuficiente para os pesos e arquivos temporários.')
report('Baixando os pesos de inferência do TRELLIS.2. Geração ainda indisponível.')
model=root/'TRELLIS.2'
snapshot_download('microsoft/TRELLIS.2-4B',local_dir=model,
    allow_patterns=['pipeline.json','ckpts/*.json','ckpts/*flow*.safetensors','ckpts/*dec*.safetensors'])
hf_hub_download('microsoft/TRELLIS-image-large','ckpts/ss_dec_conv3d_16l8_fp16.json',local_dir=root/'TRELLIS-base')
hf_hub_download('microsoft/TRELLIS-image-large','ckpts/ss_dec_conv3d_16l8_fp16.safetensors',local_dir=root/'TRELLIS-base')
blocked=[]
for repo,directory in [('facebook/dinov3-vitl16-pretrain-lvd1689m','DINOv3'),('briaai/RMBG-2.0','RMBG-2.0')]:
    token=studio_token
    response=requests.head('https://huggingface.co/'+repo+'/resolve/main/model.safetensors',
        headers={'Authorization':'Bearer '+token} if token else {},allow_redirects=True,timeout=30)
    if response.status_code!=200:
        blocked.append(repo);continue
    snapshot_download(repo,token=token,local_dir=root/directory,allow_patterns=['*.json','*.py','*.safetensors'])
if blocked:
    report('Pesos TRELLIS.2 baixados. Acesso ainda pendente: '+', '.join(blocked))
else:
    pipeline=json.loads((model/'pipeline.json').read_text(encoding='utf-8'))
    args=pipeline['args']
    wsl_root=options.wsl_models_root.rstrip('/')
    args['models']['sparse_structure_decoder']=wsl_root+'/TRELLIS-base/ckpts/ss_dec_conv3d_16l8_fp16'
    args['image_cond_model']['args']['model_name']=wsl_root+'/DINOv3'
    args['rembg_model']['args']['model_name']=wsl_root+'/RMBG-2.0'
    (model/'pipeline-local.json').write_text(json.dumps(pipeline),encoding='utf-8')
    report('Pesos baixados; validando o carregamento completo antes de habilitar a geração.')
    worker=Path(__file__).with_name('validate_trellis.py').resolve().as_posix()
    worker='/mnt/'+worker[0].lower()+worker[2:]
    result=subprocess.run(['wsl','-d','Ubuntu-24.04','--','/opt/ez3d/venv/bin/python',worker,
        options.wsl_models_root.rstrip('/')+'/TRELLIS.2'],
        timeout=900,check=False)
    if result.returncode: report('Falha na validação do carregamento do TRELLIS.2. Consulte o log de preparação.')
    else: report('TRELLIS.2 carregado e dependências verificadas. Pronto para testar a geração.',ready=True)
