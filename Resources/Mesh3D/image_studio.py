"""Local, approval-gated image studio. No gameplay or 3D assets are modified."""
import argparse
import base64
import contextlib
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import time
import unicodedata
import uuid

import requests

APPROVALS = {'aprovar', 'aprovado', 'pode criar', 'pode gerar', 'iniciar fluxo',
             'sim pode criar', 'sim pode gerar', 'gerar agora', 'criar agora'}

def normalized(text):
    return re.sub(r'[^a-z0-9 ]', '', unicodedata.normalize('NFKD', text.lower()).encode('ascii', 'ignore').decode()).strip()

def slug(text):
    text = re.sub(r'\.(png|jpg|jpeg|webp)$', '', text, flags=re.IGNORECASE).replace('_', ' ')
    return re.sub(r'[^a-z0-9]+', '-', normalized(text)).strip('-')[:64] or 'criacao'

def save(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        for attempt in range(40):
            try:
                temp.replace(path)
                break
            except PermissionError:
                if attempt==39: raise
                time.sleep(.05)
    finally:
        with contextlib.suppress(FileNotFoundError):temp.unlink()

def read(path, default=None):
    return json.loads(Path(path).read_text(encoding='utf-8-sig')) if Path(path).exists() else default

def json_answer(text):
    text = text.strip()
    if text.startswith('```'):
        text = re.sub(r'^```(?:json)?\s*', '', text)
        text = re.sub(r'\s*```$', '', text)
    result, _ = json.JSONDecoder().raw_decode(text)
    if not isinstance(result, dict):
        raise ValueError('O modelo retornou JSON sem objeto.')
    return result

def cancel_check(request_file):
    if Path(str(request_file) + '.cancel').exists():
        raise InterruptedError('Fluxo interrompido por você; as versões já criadas foram preservadas.')

def planner_base_url(value):
    from urllib.parse import urlsplit
    url = str(value or '').strip().rstrip('/')
    parts = urlsplit(url)
    if parts.scheme not in ('http', 'https') or not parts.hostname:
        raise ValueError('Endereço do planejador de imagens inválido. Configure uma URL completa, como http://127.0.0.1:1234.')
    if parts.path.rstrip('/') == '/v1':
        url = url[:-3]
    return url


def ask(config, system, text, image=None, history=None):
    config = dict(config, llm_url=planner_base_url(config.get('llm_url')))
    if config['model'] == 'ez3d-image-planner':
        try:
            models = requests.get(config['llm_url'].rstrip('/') + '/api/v1/models', timeout=10)
            models.raise_for_status()
            loaded = [i for m in models.json()['models'] for i in m.get('loaded_instances', []) if i['id'] == config['model']]
        except requests.RequestException:
            loaded = []
        if not loaded or not loaded[0]['config'].get('speculative_draft_mtp'):
            subprocess.run(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
                            str(Path(__file__).with_name('Start-ImagePlanner.ps1'))],
                           check=True, timeout=120, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    messages = [{'role': 'system', 'content': system}]
    messages.extend((history or [])[-12:])
    content = text
    if image:
        from PIL import Image
        import io
        # Keep visual QA bounded and do not send the original image off-machine.
        content = [{'type': 'text', 'text': text}]
        for path in (image if isinstance(image, list) else [image]):
            im = Image.open(path).convert('RGB'); im.thumbnail((1024, 1024))
            buf = io.BytesIO(); im.save(buf, format='JPEG', quality=90)
            content.append({'type': 'image_url', 'image_url': {
                'url': 'data:image/jpeg;base64,' + base64.b64encode(buf.getvalue()).decode()}})
    messages.append({'role': 'user', 'content': content})
    budget = max(0, min(2048, int(config.get('thinking_budget', 512))))
    payload = {
        'model': config['model'], 'messages': messages, 'temperature': 0.2,
        'reasoning_effort': 'low' if budget else 'none', 'thinking_budget_tokens': budget,
        'max_tokens': max(4096, budget + 2048), 'response_format': {'type':'json_schema',
            'json_schema':{'name':'studio_reply','strict':False,'schema':{'type':'object','additionalProperties':True}}}}
    for attempt in range(2):
        response = requests.post(config['llm_url'].rstrip('/') + '/v1/chat/completions', json=payload, timeout=180)
        response.raise_for_status()
        data = response.json()
        content = data['choices'][0]['message']['content']
        try:
            if data['choices'][0].get('finish_reason') == 'length': raise ValueError('Resposta incompleta.')
            return json_answer(content)
        except (ValueError, TypeError):
            if attempt: raise RuntimeError('O planejador não retornou uma resposta válida após duas tentativas. Suas escolhas foram preservadas.')
            payload['messages'] = messages + [{'role':'assistant','content':content},
                {'role':'user','content':'Reemita a resposta completa como um único objeto JSON válido. Preserve todas as escolhas já feitas; corrija somente o formato. Seja conciso.'}]

def ensure_comfy(config):
    url = config['comfy_url'].rstrip('/')
    try:
        requests.get(url + '/system_stats', timeout=3).raise_for_status()
        return
    except requests.RequestException:
        pass
    from urllib.parse import urlparse
    parsed = urlparse(url)
    if parsed.hostname not in ('127.0.0.1', 'localhost'):
        raise ValueError('O estúdio usa somente o executor local configurado.')
    root = Path(config['comfy_root'])
    log = open(root.parent / 'comfy-image.log', 'a', encoding='utf-8')
    subprocess.Popen([config['python'], '-u', str(root / 'main.py'), '--listen', '127.0.0.1',
                      '--port', str(parsed.port or 8188), '--disable-auto-launch', '--reserve-vram', '5'],
                     cwd=root, stdout=log, stderr=log, stdin=subprocess.DEVNULL,
                     creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
                     env={**os.environ, 'PYTHONUTF8': '1', 'PYTHONIOENCODING': 'utf-8'})
    log.close()
    for _ in range(120):
        time.sleep(1)
        try:
            requests.get(url + '/system_stats', timeout=2).raise_for_status()
            return
        except requests.RequestException:
            pass
    raise RuntimeError('ComfyUI não iniciou. Consulte comfy-image.log.')

def workflow(config, prompt, seed, reference=None):
    def node(kind, **inputs): return {'class_type': kind, 'inputs': inputs}
    if config.get('backend') == 'flux2_klein':
        size = config.get('resolution', 768)
        graph = {
            '1': node('UnetLoaderGGUF', unet_name=config['diffusion_model']),
            '2': node('CLIPLoader', clip_name=config['text_encoder'], type='flux2', device='default'),
            '3': node('VAELoader', vae_name=config['vae']),
            '4': node('CLIPTextEncode', clip=['2', 0], text=prompt),
            '5': node('EmptyFlux2LatentImage', width=size, height=size, batch_size=1),
            '6': node('ConditioningZeroOut', conditioning=['4', 0]),
            '7': node('SamplerCustomAdvanced', noise=['11', 0], guider=['12', 0], sampler=['13', 0], sigmas=['14', 0], latent_image=['5', 0]),
            '8': node('VAEDecode', samples=['7', 0], vae=['3', 0]),
            '9': node('SaveImage', images=['8', 0], filename_prefix='ez3DImageStudio'),
            '11': node('RandomNoise', noise_seed=seed),
            '12': node('CFGGuider', model=['1', 0], positive=['4', 0], negative=['6', 0], cfg=1.0),
            '13': node('KSamplerSelect', sampler_name='euler'),
            '14': node('Flux2Scheduler', steps=4, width=size, height=size)
        }
        if reference:
            graph['10'] = node('LoadImage', image=reference)
            graph['15'] = node('ImageScaleToTotalPixels', image=['10', 0], upscale_method='lanczos', megapixels=size*size/1000000, resolution_steps=16)
            graph['16'] = node('VAEEncode', pixels=['15', 0], vae=['3', 0])
            graph['17'] = node('ReferenceLatent', conditioning=['4', 0], latent=['16', 0])
            graph['18'] = node('ReferenceLatent', conditioning=['6', 0], latent=['16', 0])
            graph['19'] = node('GetImageSize', image=['15', 0])
            graph['12']['inputs'].update(positive=['17', 0], negative=['18', 0])
            graph['5']['inputs'].update(width=['19', 0], height=['19', 1])
            graph['14']['inputs'].update(width=['19', 0], height=['19', 1])
        return graph
    graph = {
        '1': node('UnetLoaderGGUF', unet_name=config['diffusion_model']),
        '2': node('CLIPLoader', clip_name=config['text_encoder'], type='qwen_image', device='default'),
        '3': node('VAELoader', vae_name=config['vae']),
        '4': node('TextEncodeQwenImage21', clip=['2', 0], prompt=prompt, negative_prompt='', resolution=config.get('resolution', 768)),
        '5': node('EmptyLatentImage', width=config.get('resolution', 768), height=config.get('resolution', 768), batch_size=1),
        '6': node('QwenImage21Cache', model=['1', 0], device='auto', dtype='default'),
        '7': node('KSampler', model=['6', 0], positive=['4', 0], negative=['4', 1], latent_image=['5', 0],
                  seed=seed, steps=config.get('steps', 25), cfg=1.0, sampler_name='euler', scheduler='simple', denoise=1.0),
        '8': node('VAEDecode', samples=['7', 0], vae=['3', 0]),
        '9': node('SaveImage', images=['8', 0], filename_prefix='ez3DImageStudio')}
    if reference:
        graph['10'] = node('LoadImage', image=reference)
        graph['4']['inputs'].update({'images.image_1': ['10', 0], 'vae': ['3', 0]})
        graph['7']['inputs']['latent_image'] = ['4', 2]
    if config.get('turbo'):
        latent = graph['7']['inputs']['latent_image']
        graph['11'] = node('RandomNoise', noise_seed=seed)
        graph['12'] = node('BasicGuider', model=['6', 0], conditioning=['4', 0])
        graph['13'] = node('KSamplerSelect', sampler_name='euler')
        graph['14'] = node('ViggleTurboSigmas', latent=latent, nodes='1.0, 0.9375, 0.875, 0.75, 0.5, 0.25')
        graph['7'] = node('SamplerCustomAdvanced', noise=['11', 0], guider=['12', 0], sampler=['13', 0], sigmas=['14', 0], latent_image=latent)

    return graph

def generate(config, prompt, destination, request_file, reference=None):
    if config.get('unload_planner_between_stages'):
        # The external studio owns this instance. Unload it before diffusion;
        # ask() reloads it only after the generator has released its allocator.
        lms = Path.home() / '.lmstudio/bin/lms.exe'
        response = requests.get(planner_base_url(config['llm_url'])+'/api/v1/models',timeout=10)
        response.raise_for_status()
        loaded=[i for m in response.json().get('models',[]) for i in m.get('loaded_instances',[]) if i.get('id')==config['model']]
        if loaded:
            subprocess.run([str(lms), 'unload', config['model']], check=True, timeout=30,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    ensure_comfy(config)
    url = config['comfy_url'].rstrip('/')
    uploaded = None
    if reference:
        with open(reference, 'rb') as stream:
            r = requests.post(url + '/upload/image', files={'image': ('ez3d-reference-' + uuid.uuid4().hex + '.png', stream)}, timeout=30)
        r.raise_for_status(); info = r.json()
        uploaded = '/'.join(x for x in [info.get('subfolder'), info['name']] if x)
    seed = secrets.randbelow(2**31)
    graph = workflow(config, prompt, seed, uploaded)
    save(destination.with_suffix('.workflow.json'), graph)
    import websocket
    client_id = uuid.uuid4().hex
    ws = websocket.create_connection(url.replace('http', 'ws', 1) + '/ws?clientId=' + client_id, timeout=1)
    started = time.monotonic()
    response = requests.post(url + '/prompt', json={'prompt': graph, 'client_id': client_id}, timeout=30)
    if not response.ok:
        raise RuntimeError('Workflow recusado: ' + response.text[:1800])
    prompt_id = response.json()['prompt_id']
    save(destination.with_suffix('.job.json'), {'prompt_id': prompt_id, 'seed': seed, 'prompt': prompt})
    deadline = time.monotonic() + config.get('generation_timeout', 1200)
    try:
        while time.monotonic() < deadline:
            cancel_check(request_file)
            try:
                event = ws.recv()
                if isinstance(event, str):
                    event = json.loads(event); data = event.get('data', {})
                    if event.get('type') == 'progress' and data.get('prompt_id') == prompt_id:
                        save(str(request_file) + '.progress.json', {'message': f'Gerando imagem · passo {data.get("value")}/{data.get("max")} · {int(time.monotonic()-started)}s', 'value': data.get('value'), 'max': data.get('max')})
            except (websocket.WebSocketTimeoutException, websocket.WebSocketConnectionClosedException):
                pass
            result = requests.get(url + '/history/' + prompt_id, timeout=15); result.raise_for_status()
            item = result.json().get(prompt_id)
            if item:
                if item.get('status', {}).get('status_str') == 'error':
                    raise RuntimeError('Falha de geração: ' + json.dumps(item['status'])[:1600])
                images = [image for output in item.get('outputs', {}).values() for image in output.get('images', [])]
                if images:
                    blob = requests.get(url + '/view', params=images[0], timeout=60);blob.raise_for_status()
                    from PIL import Image
                    import io
                    Image.open(io.BytesIO(blob.content)).verify()
                    destination.write_bytes(blob.content)
                    return {'elapsed_seconds': round(time.monotonic()-started, 2), 'seed': seed, 'prompt_id': prompt_id, 'sha256': hashlib.sha256(blob.content).hexdigest()}
            time.sleep(1)
        raise TimeoutError('Tempo de geração esgotado; a imagem não foi aprovada.')
    except (InterruptedError, TimeoutError):
        queue = requests.get(url + '/queue', timeout=5).json()
        if any(row[1] == prompt_id for row in queue.get('queue_running', [])):
            requests.post(url + '/interrupt', json={}, timeout=5)
        else:
            requests.post(url + '/queue', json={'delete': [prompt_id]}, timeout=5)
        raise
    finally:
        ws.close()
        # This dedicated local image server is released before the reviewer runs.
        from pipeline_resources import release_comfy
        release_comfy(url)

def valid_proposal(proposal):
    assets = proposal.get('assets')
    if not isinstance(assets, list) or not 1 <= len(assets) <= 8:
        raise ValueError('O plano precisa conter de 1 a 8 imagens.')
    for asset in assets:
        if not isinstance(asset, dict) or not isinstance(asset.get('name'), str) or not isinstance(asset.get('prompt'), str):
            raise ValueError('Plano de imagem incompleto.')
        if not 3 <= len(asset['prompt']) <= 6000:
            raise ValueError('Descrição de imagem inválida.')
        if 'edit' in asset and not isinstance(asset['edit'], bool):
            raise ValueError('A operação de edição precisa ser verdadeira ou falsa.')
        if not asset.get('view'):
            words = set(normalized(asset['name'].replace('-',' ').replace('_',' ')).split())
            for direction, aliases in {'front':{'front','frente','frontal'},'back':{'back','costas','traseira'},
                                       'left':{'left','esquerdo','esquerda'},'right':{'right','direito','direita'}}.items():
                if words & aliases: asset['view']=direction;break
        if asset.get('view') not in (None,'front','back','left','right'):
            raise ValueError('Vista inválida no plano de imagem.')
    views = [a.get('view') for a in assets]
    if len(assets)==4 and all(views):
        if set(views)!={'front','back','left','right'}: raise ValueError('O conjunto de vistas contém lados repetidos ou ausentes.')
        assets.sort(key=lambda a:('front','back','left','right').index(a['view']))
    return proposal

def validated_questions(reply):
    questions = reply.get('questions')
    if not isinstance(questions, list) or not 1 <= len(questions) <= 3:
        raise ValueError('O planejador deve formular de uma a três perguntas relevantes.')
    seen = set()
    for question in questions:
        if not isinstance(question, dict) or not isinstance(question.get('question'), str) or not question['question'].strip():
            raise ValueError('Pergunta incompleta.')
        options = question.get('options')
        if not isinstance(options, list) or not 2 <= len(options) <= 4 or any(not isinstance(v, str) or not v.strip() for v in options) or len(set(options)) != len(options):
            raise ValueError('Opções de resposta inválidas.')
        question['id'] = str(question.get('id') or uuid.uuid4().hex)
        if question['id'] in seen: raise ValueError('Identificador de pergunta repetido.')
        seen.add(question['id'])
    return questions


def reviewed_verdict(qa):
    checks = qa.get('checks')
    if not isinstance(checks, list) or len(checks) < 2:
        return 'inconclusive'
    if any(not isinstance(c, dict) or c.get('met') is not True or not isinstance(c.get('evidence'), str) or len(c['evidence']) < 8 for c in checks):
        return 'rejected'
    return 'accepted' if qa.get('verdict') == 'accepted' else 'rejected'


def execute(request_file):
    req = read(request_file); config = req['config']; root = Path(req['root']).resolve(); root.mkdir(parents=True, exist_ok=True)
    state_path = root / 'active.json'
    state = read(state_path, {'messages': [], 'flow': None})
    if req.get('new_conversation'):
        state = {'messages': [], 'flow': None}
    text = req['text'].strip()
    answer_prefix = '__ez3d_answers__:'
    is_answer = text.startswith(answer_prefix)
    if normalized(text) in {'novo fluxo', 'nova criacao'}:
        save(state_path, {'messages': [], 'flow': None})
        return {'message': 'Descreva o que deseja criar.', 'images': []}
    if normalized(text) in {'galeria', 'mostrar galeria', 'ver imagens'}:
        folder = root / state['flow'] if state.get('flow') else None
        manifest = read(folder / 'manifest.json', {'images': []}) if folder else {'images': []}
        return {'message': '', 'images': [{'path': str(folder / r['file'])} for r in manifest['images'] if r.get('verdict') == 'accepted'], 'folder': str(folder or root)}
    if is_answer:
        answers = json.loads(text[len(answer_prefix):])
        pending = state.get('pending')
        if not pending or answers.get('token') != pending.get('token'):
            raise ValueError('Essas perguntas já não pertencem ao pedido ativo. Envie seu pedido novamente.')
        choices = answers.get('answers', {})
        for question in pending['questions']:
            custom = answers.get('custom_answers', {}).get(question['id'])
            if custom is not None:
                if not isinstance(custom,str) or not custom.strip() or len(custom)>1000:
                    raise ValueError('Digite uma resposta de até 1000 caracteres.')
                choices[question['id']] = custom.strip()
            elif choices.get(question['id']) not in question['options']:
                raise ValueError('Responda às perguntas antes de continuar.')
        state.setdefault('decisions', {}).update({q['question']:choices[q['id']] for q in pending['questions']})
        text = '\n'.join(q['question'] + ': ' + choices[q['id']] for q in pending['questions'])
        state['pending'] = None
        state['rounds'] = state.get('rounds', 0) + 1
    elif state.get('pending') and not req.get('reference'):
        state['rounds'] = state.get('rounds', 0) + 1
        state['pending'] = None
    else:
        state['rounds'] = 0
        state['pending'] = None
        state['request'] = text
        state['decisions'] = {}
        state['reference'] = req.get('reference')
    state.setdefault('messages', []).append({'role': 'user', 'content': text})
    reference = req.get('reference') or state.get('reference')
    system = (
        'Você coordena um estúdio local de IMAGENS. Responda em português e somente JSON. '
        'O pedido ATUAL tem prioridade absoluta: um novo personagem não herda espécie, cores, roupas ou materiais do anterior. Nunca transforme um humano em robô só porque houve um robô no histórico. '
        'O usuário já autoriza gerar o que pediu; NÃO peça para digitar aprovar e NÃO mostre prompts técnicos. '
        'Antes de criar, esclareça decisões visuais úteis que faltam no pedido com perguntas e opções que VOCÊ formula para aquele pedido. '
        'Não repita detalhes já definidos nem invente um questionário fixo. Pode perguntar de 1 a 3 coisas por rodada; no máximo 2 rodadas. '
        'Perguntas: {"action":"ask","questions":[{"id":"estilo","question":"pergunta contextual", "options":["opção A","opção B"]}]}. '
        'Se já tem detalhes suficientes ou o usuário pediu para executar sem perguntar: '
        '{"action":"create","name":"nome legível deste trabalho","new_flow":true,"assets":[{"name":"nome da imagem",'
        '"prompt":"instruction in English","view":null,"edit":false,"requirements":["critério visual objetivo","outro critério visual"]}]}. '
        'Use new_flow=true para um NOVO assunto/personagem; false para continuar ou editar o trabalho atual. '
        'Faça várias imagens somente se solicitado, separando cada vista quando pedida. '
        'Nunca herde quantidade de imagens, pose T, vistas ou exigências de rig de pedidos anteriores. Para uma imagem comum, use pose natural e um único asset. '
        'Para quatro lados do mesmo NPC, mantenha identidade/roupa/proporções e retorne quatro assets nesta ordem, com view: front, back, left, right. '
        'Cada imagem deve conter somente uma vista isolada; nunca faça colagem. Escreva no prompt a orientação exata da câmera. '
        'Com referência, escreva instrução de TRANSFORMAÇÃO explícita (Transform image 1 into...), nunca uma descrição que só reconstrua a foto. '
        'Se o usuário quer animação 3D, diga claramente stylized 3D animated character, modeled geometry, simplified materials; nunca photorealistic photograph. '
        'Conserve identidade e roupas, mas altere o estilo como solicitado. Use edit=true para editar a última imagem aprovada. '
        'Para simples conversa: {"action":"chat","message":"resposta"}. '
    )
    if state.get('rounds', 0) >= 2:
        system += 'Já houve duas rodadas de perguntas: conclua com action=create, respeitando as escolhas.'
    save(str(request_file) + '.progress.json', {'message': 'Analisando seu pedido e suas escolhas...'})
    reply = ask(config, system, text, reference, state['messages'][:-1][-10:])
    singular = bool(re.search(r'\b(uma|one|1)\s+imagem?\b|\bone image\b', state.get('request',''), re.I))
    if singular and len(reply.get('assets', [])) > 1:
        reply = ask(config, system + ' O pedido atual exige EXATAMENTE UM asset; nenhuma vista adicional ou pose T foi solicitada.',
                    state['request'] + '\nEscolhas atuais:\n' + text, reference, [])
        if len(reply.get('assets', [])) > 1:
            raise ValueError('O planejador tentou criar vistas que você não pediu. Nenhuma geração extra foi iniciada.')
    if reply.get('action') == 'ask':
        if state.get('rounds', 0) >= 2: raise ValueError('O planejador repetiu perguntas além do limite; nenhum fluxo foi iniciado.')
        questions = validated_questions(reply)
        state['pending'] = {'token': uuid.uuid4().hex, 'questions': questions}
        state['messages'].append({'role': 'assistant', 'content': json.dumps(reply, ensure_ascii=False)})
        save(state_path, state)
        return {'message': '', 'questions': state['pending'], 'images': []}
    if reply.get('action') not in ('create', 'propose'):
        message = str(reply.get('message', 'Descreva o que deseja criar.'))
        state['messages'].append({'role': 'assistant', 'content': message}); save(state_path, state)
        return {'message': message, 'images': []}
    proposal = valid_proposal(reply)
    if not state.get('flow') or reply.get('new_flow', not any(a.get('edit') for a in proposal['assets'])):
        state['flow'] = slug(proposal.get('name', proposal['assets'][0]['name'])) + '-' + dt.datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + secrets.token_hex(2)
    folder = root / state['flow']; folder.mkdir(exist_ok=True)
    manifest_path = folder / 'manifest.json'; manifest = read(manifest_path, {'name': proposal.get('name'), 'images': []})
    save(state_path, state)
    images = []
    for asset_index, asset in enumerate(proposal['assets']):
        cancel_check(request_file)
        source = reference
        if asset.get('view') in ('back','left','right') and images:
            source = images[0]['path']
        if asset.get('edit') and not req.get('reference'):
            previous = [r for r in manifest['images'] if r.get('verdict') == 'accepted']
            if previous: source = str(folder / previous[-1]['file'])
            elif not source: raise ValueError('Não existe imagem anterior para editar. Cole uma referência.')
        prompt = asset['prompt']
        for attempt in range(2):
            cancel_check(request_file)
            version = len(manifest['images']) + 1
            image = folder / (slug(asset['name']) + f'-v{version:03}.png')
            while image.exists() or image.with_suffix('.workflow.json').exists():
                version += 1; image = folder / (slug(asset['name']) + f'-v{version:03}.png')
            save(str(request_file) + '.progress.json', {'message': f'Preparando imagem {asset_index+1}/{len(proposal["assets"])}...'})
            record = {'file': image.name, 'name': asset['name'], 'prompt': prompt, 'reference': source,
                      'view': asset.get('view'),
                      'verdict': 'unreviewed', 'created': dt.datetime.now().isoformat()}
            record.update(generate(config, prompt, image, request_file, source))
            manifest['images'].append(record); save(manifest_path, manifest)
            cancel_check(request_file)
            save(str(request_file) + '.progress.json', {'message': 'Conferindo estilo, identidade e os detalhes pedidos...'})
            qa = ask(config, 'Você é revisor visual rigoroso. Compare a imagem REAL com o pedido e as escolhas. '
                'Em trabalhos com várias vistas, avalie somente a vista individual indicada no prompt desta imagem, não exija as outras vistas dentro dela. '
                'Uma fotografia não é uma personagem modelada de animação 3D; fundo desfocado não é fundo simples. '
                'Verifique primeiro a TRANSFORMAÇÃO de estilo, depois identidade, pose e detalhes. '
                'Uma cópia da referência deve ser REJEITADA quando foi pedida transformação. '
                'Liste cada exigência com evidência observável; não repita o prompt como se fosse evidência. '
                'Verifique TODAS as escolhas, inclusive materiais da roupa e equipamentos. Não aceite só porque estilo e pose estão certos. Couro com placas não é armadura integral de metal. '
                'JSON: {"verdict":"accepted|rejected|inconclusive","checks":[{"requirement":"critério", "met":true, "evidence":"o que você vê"}],'
                '"reason":"motivo", "correction":"explicit English transformation instruction"}. Pelo menos 2 checks.',
                'Pedido original: ' + state.get('request', '') + '\nEscolhas acumuladas: ' + json.dumps(state.get('decisions',{}),ensure_ascii=False) + '\nEscolhas: ' + text + '\nEsta imagem: ' + asset['prompt'] +
                '\nExigências: ' + json.dumps(asset.get('requirements', []), ensure_ascii=False) +
                '\nESCOPO DESTA REVISÃO: avalie APENAS a imagem individual ' + str(asset.get('view') or asset['name']) +
                '. As demais imagens são geradas em outras etapas e sua quantidade é validada pelo sistema. NÃO cobre quatro vistas, grade ou colagem nesta imagem. '
                'A correção, se necessária, deve pedir uma única figura nesta única vista.', [source, image] if source else image)
            record.update(verdict=reviewed_verdict(qa), review=qa); save(manifest_path, manifest)
            if record['verdict'] == 'accepted':
                images.append({'path': str(image), 'view': asset.get('view')}); break
            if attempt == 0:
                save(str(request_file) + '.progress.json', {'message': 'A revisão encontrou uma divergência; ajustando a imagem...'})
                prompt = asset['prompt'] + '\nCorrection required: ' + str(qa.get('correction', qa.get('reason', '')))
                if asset.get('view'):
                    prompt += '\nThis file must show exactly ONE full-body character, '+str(asset['view'])+' view only. No grid, no collage, no additional views or figures.'
        if record['verdict'] != 'accepted': break
    state['messages'].append({'role': 'assistant', 'content': 'Trabalho gerado: ' + str(folder)})
    save(state_path, state)
    message = '' if record['verdict'] == 'accepted' else 'A revisão não confirmou o resultado após duas tentativas. Ajuste o pedido para continuarmos; as versões estão preservadas.'
    return {'message': message, 'images': images, 'folder': str(folder),
            'complete': len(images)==len(proposal['assets']), 'expected_images':len(proposal['assets'])}

def main():
    parser = argparse.ArgumentParser();parser.add_argument('--request', required=True);args = parser.parse_args()
    request = Path(args.request).resolve()
    try:
        import msvcrt
        root = Path(read(request)['root']);root.mkdir(parents=True, exist_ok=True)
        with (root / 'studio.lock').open('a+b') as lock:
            if lock.tell() == 0: lock.write(b'0');lock.flush()
            lock.seek(0)
            try:
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                raise RuntimeError('Já existe um pedido em execução neste estúdio.')
            try:
                result = execute(request);result['ok'] = True
            finally:
                lock.seek(0);msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
    except Exception as exc:
        result = {'ok': False, 'message': str(exc), 'images': []}
    save(str(request) + '.result.json', result)
    print(json.dumps(result, ensure_ascii=True))
    return 0 if result['ok'] else 1

if __name__ == '__main__':
    sys.exit(main())
