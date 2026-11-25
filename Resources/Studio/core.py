"""Local production pipeline. Artifacts and real importer receipts are authoritative."""
import contextlib
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import uuid

HERE = Path(__file__).resolve().parent
MESH = HERE.parent / 'Mesh3D'
sys.path.insert(0, str(MESH))
from image_studio import read, save, slug


class Studio:
    def __init__(self, config):
        self.config = config
        self.root = Path(config['work_root']).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.current = None
        self.message_file = self.root / 'chat.json'

    def inside(self, value):
        path = Path(value).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError('Arquivo fora da pasta deste estúdio.')
        return path

    def jobs(self):
        paths = sorted((self.root / 'jobs').glob('*/job.json'), reverse=True)
        metadata = read(self.root / 'history.json', {})
        result = []
        for path in paths:
            job = read(path)
            entry = metadata.get(job['id'], {})
            if entry.get('hidden'): continue
            if entry.get('title'):
                job['title'] = entry['title']
                if job.get('settings', {}).get('conversation_id'):
                    job['settings']['conversation_title'] = entry['title']
            result.append(self.public_job(job))
            if len(result) == 100: break
        return result

    def edit_history(self, ident, title=None, hide=False):
        path = self.inside(self.root / 'jobs' / ident / 'job.json')
        if not path.is_file(): raise ValueError('Trabalho não encontrado.')
        if self.current and self.current['id'] == ident:
            raise RuntimeError('Aguarde o trabalho terminar antes de alterar o histórico.')
        metadata = read(self.root / 'history.json', {})
        source = read(path)
        group = source.get('settings', {}).get('conversation_id')
        if group and self.current and self.current.get('settings', {}).get('conversation_id') == group:
            raise RuntimeError('Aguarde esta conversa terminar antes de alterar o histórico.')
        targets = [ident]
        if group:
            targets = [j['id'] for j in (read(p) for p in (self.root/'jobs').glob('*/job.json'))
                       if j.get('settings', {}).get('conversation_id') == group]
        if not hide:
            title = str(title or '').strip()[:100]
            if not title: raise ValueError('Informe um nome.')
        for target in targets:
            entry = metadata.setdefault(target, {})
            if hide: entry['hidden'] = True
            else: entry['title'] = title
        save(self.root / 'history.json', metadata)
        return {'ok': True}

    def chat_history(self):
        metadata = read(self.root / 'history.json', {})
        return [item for item in read(self.message_file, [])
                if not metadata.get(item.get('job'), {}).get('hidden')]

    def public_job(self, job):
        result = dict(job)
        if 'error' in result:
            if 'esticou a malha' in result['error']:
                result['error']='Movimento bloqueado por deformação excessiva. Recalcular os pesos não garante corrigir o problema; o vínculo entre membros precisa de revisão localizada. Modelo preservado.'
            elif 'costura da malha' in result['error']:
                result['error']='Movimento bloqueado porque abriu uma costura da malha. O modelo foi preservado para revisão dos pesos.'
            elif 'CUDA driver error' in result['error']:
                result['error']='O driver CUDA interrompeu esta etapa. Os arquivos anteriores foram preservados. Consulte o diagnóstico do trabalho.'
            elif 'InterruptedError' in result['error'] or 'Fluxo interrompido' in result['error']:
                result['status'] = 'cancelled'
                result['error'] = ''
            else:
                result['error'] = 'Não foi possível concluir esta etapa. Os arquivos e detalhes estão na pasta do trabalho.'
        pending = read(self.root / 'images' / 'active.json', {}).get('pending')
        if result.get('reply', {}).get('questions'):
            result['questions_active'] = bool(pending and pending.get('token') == result['reply']['questions']['token'])
            if not result['questions_active'] and result.get('status') == 'awaiting_answers':
                result['status'] = 'answered'
                result['stage'] = 'Escolhas definidas'
        return result

    def projects(self):
        result = {str(Path(p).resolve()) for p in self.config.get('projects', []) if Path(p).is_file()}
        skipped = {'Content', 'Plugins', 'Intermediate', 'Saved', 'Binaries', '.git', 'DerivedDataCache', 'node_modules'}
        for root in self.config.get('project_roots', []):
            for directory, dirs, files in os.walk(root):
                depth = len(Path(directory).relative_to(root).parts)
                dirs[:] = [d for d in dirs if d not in skipped and depth < 5]
                result.update(str((Path(directory) / f).resolve()) for f in files if f.endswith('.uproject'))
        return [{'name': Path(p).stem, 'path': p} for p in sorted(result)]

    def launch(self, kind, operation, **payload):
        if not self.lock.acquire(blocking=False):
            raise RuntimeError('Há um trabalho ativo. Aguarde a etapa terminar.')
        if getattr(self,'shutdown_requested',False):
            self.lock.release()
            raise RuntimeError('O estúdio está encerrando.')
        process_lock = (self.root / 'pipeline.lock').open('a+b')
        try:
            process_lock.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(process_lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(process_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            process_lock.close(); self.lock.release()
            raise RuntimeError('Outro processo do estúdio está trabalhando. Aguarde para não acumular modelos na GPU.')
        try:
            ident = dt.datetime.now().strftime('%Y%m%d-%H%M%S-') + uuid.uuid4().hex[:6]
            folder = self.root / 'jobs' / ident
            folder.mkdir(parents=True)
            job = dict(id=ident, kind=kind, status='running', stage='Preparando', folder=str(folder),
                       started=dt.datetime.now().isoformat(), artifacts={}, events=[], **payload)
            save(folder / 'job.json', job)
            self.current = job
        except BaseException:
            process_lock.close()
            self.lock.release()
            raise
        def worker():
            try:
                operation(job)
                if job['status'] == 'running': job['status'] = 'complete'
            except Exception as exc:
                job.update(status='failed', error=str(exc))
            finally:
                job['finished'] = dt.datetime.now().isoformat()
                try:
                    save(folder / 'job.json', job)
                finally:
                    self.current = None
                    process_lock.close()
                    self.lock.release()
        threading.Thread(target=worker, daemon=True).start()
        return job

    def stage(self, job, label):
        if (Path(job['folder']) / 'pause').exists():
            raise RuntimeError('Pausado a seu pedido entre etapas. Os arquivos foram preservados.')
        job['stage'] = label
        job['events'].append({'stage': label, 'time': dt.datetime.now().isoformat()})
        save(Path(job['folder']) / 'job.json', job)

    def run(self, job, label, command, timeout=1800, env=None):
        self.stage(job, label)
        folder = Path(job['folder'])
        log = folder / (str(len(job['events'])).zfill(2) + '-' + slug(label) + '.log')
        job['log'] = str(log)
        started = time.monotonic()
        with log.open('w', encoding='utf-8') as stream:
            env = dict(os.environ if env is None else env, PYTHONUTF8='1', PYTHONIOENCODING='utf-8')
            proc = subprocess.Popen([str(c) for c in command], stdout=stream, stderr=subprocess.STDOUT,
                cwd=str(HERE), env=env, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.kill(); proc.wait()
                raise TimeoutError(f'{label}: tempo limite. Consulte o log; nenhum sucesso foi registrado.')
        job['events'][-1]['seconds'] = round(time.monotonic() - started, 2)
        output = log.read_text(encoding='utf-8', errors='replace')
        if proc.returncode:
            raise RuntimeError(f'{label} falhou (código {proc.returncode}). ' + output[-1800:])
        return output

    def unload_planner(self, job):
        lms = Path.home() / '.lmstudio/bin/lms.exe'
        # Only the instance owned by ez3D, never an unrelated user model.
        import requests
        try:
            response = requests.get('http://127.0.0.1:1234/api/v1/models', timeout=5)
            response.raise_for_status()
            loaded = [i for m in response.json().get('models', []) for i in m.get('loaded_instances', [])
                      if i.get('id') == 'ez3d-image-planner']
            if not loaded: return
        except requests.ConnectionError:
            # The CLI still verifies the local instance when HTTP is unavailable.
            pass
        self.run(job, 'Liberando Qwen', [lms, 'unload', 'ez3d-image-planner'], timeout=30)

    def chat(self, text, reference=None, settings=None):
        if not text.strip(): raise ValueError('Escreva seu pedido.')
        settings = dict(settings or {'output': 'image'})
        if settings.get('output') not in ('image', 'static', 'rig', 'animated'):
            raise ValueError('Escolha a saída do fluxo.')
        if settings['output'] == 'animated' and not str(settings.get('motion', '')).strip():
            raise ValueError('Descreva a animação desejada; ela não será escolhida automaticamente.')
        if reference: reference = str(self.inside(reference))
        conversation = read(self.root / 'conversation.json', {})
        if settings.get('new_conversation') or text.strip().lower() == 'novo fluxo': conversation = {}
        settings.setdefault('conversation_id', conversation.get('id') or uuid.uuid4().hex)
        settings.setdefault('conversation_title', conversation.get('title') or text.strip()[:90])
        save(self.root / 'conversation.json', {'id':settings['conversation_id'], 'title':settings['conversation_title']})
        def operation(job):
            request = Path(job['folder']) / 'request.json'
            cfg = read(MESH / 'image_studio_config.json')
            cfg['unload_planner_between_stages'] = True
            if settings['output'] in ('rig', 'animated') and not text.startswith('__ez3d_answers__:'):
                prompt_text = text + '\nSaída escolhida: humanoide para rig SOMA. Corpo inteiro em pose T, braços horizontais, mãos e pés separados, fundo simples, sem objetos sobrepondo as juntas. Preserve todas as vistas solicitadas em imagens separadas e use a frontal como primeira vista.'
            else:
                prompt_text = text
            save(request, {'root': str(self.root / 'images'), 'config': cfg, 'text': prompt_text, 'reference': reference,
                           'new_conversation':bool(settings.get('new_conversation')) and not text.startswith('__ez3d_answers__:')})
            script = ('import sys,json;sys.path.insert(0,sys.argv[1]);from image_studio import execute;'
                      'from pathlib import Path;r=execute(Path(sys.argv[2]));'
                      'Path(sys.argv[3]).write_text(json.dumps(r,ensure_ascii=False),encoding="utf-8")')
            result_path = Path(job['folder']) / 'reply.json'
            self.run(job, 'Imagem e revisão visual', [self.config['image_python'], '-u', '-c', script,
                     MESH, request, result_path], timeout=2400)
            reply = read(result_path)
            job['reply'] = reply
            if reply.get('questions'):
                job['status'] = 'awaiting_answers'
            elif reply.get('images'):
                front = next((i for i in reply['images'] if i.get('view')=='front'),reply['images'][0])
                image = self.inside(front['path'])
                job['artifacts']['image'] = str(image)
                job['views'] = reply['images']
                if not reply.get('complete',True): job['status']='needs_review'
                elif settings['output'] != 'image': self.build(job, image, settings)
            else:
                job['status'] = 'needs_input'
        return self.launch('creation', operation, settings=settings, user_text=text, reference=reference,
                           title=settings['conversation_title'])

    def build(self, job, image, settings):
        folder = Path(job['folder'])
        self.unload_planner(job)
        base = [self.config['mesh_python'], '-u', MESH / 'run_comfy_mesh3d.py',
                '--base-url', self.config['comfy_url'], '--image', image,
                '--project-name', 'Studio_' + job['id'].replace('-', '_'),
                '--comfy-root', self.config['comfy_root']]
        geometry, textured = folder / 'geometry.glb', folder / 'textured.glb'
        view_args=[]
        for view in job.get('views',[]):
            path=self.inside(view['path'])
            if path!=Path(image).resolve():view_args.extend(['--extra-image',path])
        self.run(job, 'Geometria', base + ['--mode', 'geometry', '--output', geometry,
                 '--geometry-resolution', '512', '--geometry-steps', '8']+view_args, timeout=1800)
        self.require_file(geometry); job['artifacts']['geometry'] = str(geometry)
        self.run(job, 'Texturas e materiais', base + ['--mode', 'texture', '--mesh', geometry,
                 '--output', textured, '--texture-view-size', '512', '--texture-size', '2048'], timeout=2400)
        self.require_file(textured); job['artifacts']['textured'] = str(textured)
        if settings['output'] in ('rig', 'animated'):
            motion = None
            rig_request = folder / 'rig-request.json'
            save(rig_request, {'input': str(textured), 'output': str(folder / 'character.fbx'),
                 'preview': str(folder / 'character.glb'), 'kimodo_root': self.config['kimodo_root'],
                 'motion': None, 'height': 1.75,
                 'allow_proximity_weights': True})
            self.run(job, 'Esqueleto e pesos',
                     [self.config['mesh_python'], '-u', HERE / 'rig_asset.py', rig_request], timeout=900)
            self.require_file(folder / 'character.fbx')
            job['artifacts'].update(fbx=str(folder / 'character.fbx'), character=str(folder / 'character.glb'))
            job['rig_validation'] = read(folder / 'character.validation.json')
            if settings['output'] == 'animated':
                motion = self.generate_motion(job, settings['motion'])
            if motion:
                clip_request = folder / 'animation-request.json'
                save(clip_request, {'rig_scene': str(folder / 'character.blend'),
                    'output': str(folder / 'motion_01.fbx'), 'preview': str(folder / 'motion_01.glb'),
                    'motion': str(motion), 'animation_only': True})
                self.run(job, 'Animação separada · preservando a malha base',
                    [self.config['mesh_python'], '-u', HERE / 'rig_asset.py', clip_request], timeout=900)
                job['animations'] = [{'name': settings['motion'], 'fbx': str(folder / 'motion_01.fbx'),
                    'preview': str(folder / 'motion_01.glb')}]
        job['stage'] = 'Pronto para importar · escolha o projeto'

    def generate_motion(self, job, description):
        folder = Path(job['folder'])
        settings = {'motion': description}
        root = Path(self.config['kimodo_root'])
        prompt = folder / 'motion.txt'; prompt.write_text(settings['motion'], encoding='utf-8')
        motion = folder / 'motion'; motion.mkdir(exist_ok=True)
        model = root / 'models/kimodo-soma-rp-v1.1-f32.gguf'
        signature = json.dumps({'prompt':settings['motion'].strip(), 'frames':90, 'steps':20, 'seed':77,
            'model':str(model.resolve()), 'bytes':model.stat().st_size, 'modified':model.stat().st_mtime_ns}, sort_keys=True)
        cache = self.root / 'cache/motion' / hashlib.sha256(signature.encode()).hexdigest()
        files = ('local_rotations_xyzw.f32','root_positions.f32')
        env = dict(os.environ, KIMODO_TEXT_LAYER_CHUNK='8')
        env['PATH'] = str(root / 'build-win/bin') + os.pathsep + env.get('PATH', '')
        import shutil
        if all((cache/f).is_file() for f in files) and (cache/files[0]).stat().st_size==90*30*4*4 and (cache/files[1]).stat().st_size==90*3*4:
            self.stage(job,'Reutilizando o movimento solicitado já gerado')
            for f in files: shutil.copy2(cache/f,motion/f)
        else:
            if self.config.get('kimodo_backend') == 'wsl-vulkan':
                def linux_path(value):
                    value = Path(value).resolve().as_posix()
                    if len(value) < 3 or value[1:3] != ':/':
                        raise ValueError('O modo Kimodo WSL requer arquivos em um volume do Windows.')
                    return '/mnt/' + value[0].lower() + value[2:]
                executable = str(self.config.get('kimodo_wsl_binary', '/opt/ez3d/Kimodo/build/release/kmd-generate'))
                self.run(job, 'Kimodo · movimento solicitado', ['wsl','-d','Ubuntu-24.04','--','env',
                    'KIMODO_BACKEND=vulkan','KIMODO_TEXT_LAYER_CHUNK=8', executable,
                    linux_path(model), linux_path(root/'generated/llm2vec-text-bundle'),
                    linux_path(prompt), '90', '20', '77', linux_path(motion)], timeout=1800)
            else:
                self.run(job, 'Kimodo · movimento solicitado', [root / 'build-win/kmd-generate.exe',
                    model, root / 'generated/llm2vec-text-bundle', prompt, '90', '20', '77', motion], timeout=1800, env=env)
            if (motion/files[0]).stat().st_size!=90*30*4*4 or (motion/files[1]).stat().st_size!=90*3*4:
                raise RuntimeError('Kimodo retornou um movimento incompleto ou de outro esqueleto.')
            cache.mkdir(parents=True,exist_ok=True)
            for f in files: shutil.copy2(motion/f,cache/f)
        self.require_file(motion / 'local_rotations_xyzw.f32')
        return motion

    def add_animation(self, job_id, description):
        source = read(self.inside(self.root / 'jobs' / Path(job_id).name / 'job.json'))
        if hasattr(self,'catalog_job'):source=self.catalog_job(source)
        if not source or source.get('status') != 'complete':
            raise ValueError('Selecione uma criação concluída.')
        description = str(description).strip()
        if not description: raise ValueError('Descreva a animação.')
        base = self.inside(Path(source.get('artifacts', {}).get('fbx', '')).with_suffix('.blend'))
        if not base.is_file(): raise ValueError('Selecione uma malha com esqueleto do novo fluxo.')
        def operation(job):
            self.unload_planner(job)
            motion = self.generate_motion(job, description)
            folder = Path(job['folder'])
            request = folder / 'animation-request.json'
            save(request, {'rig_scene':str(base), 'motion':str(motion), 'animation_only':True,
                'output':str(folder/'motion.fbx'), 'preview':str(folder/'motion.glb')})
            self.run(job, 'Aplicando movimento ao esqueleto existente',
                [self.config['mesh_python'], '-u', HERE/'rig_asset.py', request], timeout=900)
            job['artifacts'] = dict(source['artifacts'])
            job['animations'] = source.get('animations', []) + [{'name':description,
                'fbx':str(folder/'motion.fbx'), 'preview':str(folder/'motion.glb')}]
            job['rig_validation'] = source.get('rig_validation')
        return self.launch('animation', operation, source_job=source['id'],
            settings={'output':'animated', 'motion':description}, title=source.get('title','Personagem')+' · animação')

    def build_image_job(self, job_id, settings):
        source = read(self.root / 'jobs' / Path(job_id).name / 'job.json')
        image = source.get('artifacts', {}).get('image') if source else None
        if not image: raise ValueError('Selecione uma imagem aprovada para continuar.')
        if settings.get('output') not in ('static','rig','animated'): raise ValueError('Escolha uma saída 3D.')
        if settings['output']=='animated' and not settings.get('motion','').strip():
            raise ValueError('Descreva o movimento desejado.')
        image = self.inside(image)
        def operation(job):
            job['artifacts']['image'] = str(image)
            job['views'] = source.get('views',[])
            self.build(job, image, settings)
        return self.launch('creation', operation, settings=settings, source_job=source['id'],
                           title='3D · '+source.get('title','Imagem aprovada'))

    @staticmethod
    def require_file(path):
        if not Path(path).is_file() or Path(path).stat().st_size < 128:
            raise RuntimeError(f'Artefato ausente ou vazio: {path}')

    def import_job(self, job_id, project):
        project = str(Path(project).resolve())
        if project not in {p['path'] for p in self.projects()}:
            raise ValueError('Selecione um projeto registrado no estúdio.')
        association = str(read(project).get('EngineAssociation',''))
        engine_version = read(Path(self.config['engine']) / 'Engine/Build/Build.version', {})
        configured_version = f"{engine_version.get('MajorVersion',5)}.{engine_version.get('MinorVersion',6)}"
        if association and association[0].isdigit() and not association.startswith(configured_version):
            raise ValueError('Este projeto usa Unreal '+association+'. Configure a mesma versão no estúdio antes de importar.')
        try:
            import psutil
            for process in psutil.process_iter(['name','cmdline']):
                if 'UnrealEditor' in (process.info.get('name') or ''):
                    args = process.info.get('cmdline') or []
                    if any(a.lower().endswith('.uproject') and str(Path(a).resolve()).lower()==project.lower() for a in args):
                        raise RuntimeError('Salve e feche este projeto no Unreal antes de importar; os assets não serão gravados sobre um editor aberto.')
        except ImportError:
            raise RuntimeError('A verificação do editor aberto está indisponível; importe após reparar a dependência psutil.')
        source = read(self.root / 'jobs' / Path(job_id).name / 'job.json')
        if not source or source['status'] != 'complete': raise ValueError('A criação precisa estar concluída.')
        if hasattr(self,'catalog_job'):source=self.catalog_job(source)
        artifacts = source['artifacts']
        skeletal = bool(artifacts.get('fbx')) and source.get('rig_validation',{}).get('motion_compatible') is not False
        asset = artifacts.get('fbx') if skeletal else artifacts.get('textured')
        if not asset: raise ValueError('Este trabalho ainda não tem um 3D pronto.')
        asset = self.inside(asset)
        def operation(job):
            folder = Path(job['folder'])
            receipt = folder / 'import-result.json'
            request = folder / 'import-request.json'
            model_name = slug(source.get('title') or 'Modelo').replace('-', '_')
            destination = '/Game/ez3D/' + model_name + '/v_' + slug(source['id']).replace('-', '_')
            save(request, {'source': str(asset), 'destination': destination,
                           'model_name':model_name,
                           'animated': bool(source.get('animations')),
                           'skeletal': skeletal, 'receipt': str(receipt),
                           'appearance_source': str(self.inside(artifacts['textured'])) if skeletal else None})
            request_data = read(request)
            request_data['animation_files'] = [str(self.inside(a['fbx'])) for a in source.get('animations', [])]
            request_data['animation_names'] = [a.get('name', '') for a in source.get('animations', [])]
            save(request, request_data)
            env = dict(os.environ, ez3D_STUDIO_IMPORT_REQUEST=str(request))
            command = [Path(self.config['engine']) / 'Engine/Binaries/Win64/UnrealEditor-Cmd.exe', project,
                       '-run=pythonscript', '-script=' + str(HERE / 'unreal_import.py'),
                       '-EnablePlugins=PythonScriptPlugin,EditorScriptingUtilities', '-unattended', '-nop4',
                       '-NullRHI', '-nosound', '-DisablePlugins=Fab,Aura']
            try:
                self.run(job, 'Importando no projeto escolhido', command, timeout=1800, env=env)
            except RuntimeError:
                # Unreal can return 1 for pre-existing project initialization
                # errors after our importer saved and verified every asset.
                verified = read(receipt, {})
                if not verified.get('success') or not verified.get('assets'): raise
                job['project_warning'] = 'Unreal registrou avisos do projeto; a importação foi validada no recibo.'
            result = read(receipt)
            if not result or not result.get('success'):
                raise RuntimeError((result or {}).get('error', 'Unreal não confirmou a importação.'))
            job['receipt'] = result
            report = 'Importado no projeto ' + Path(project).stem + ':\n' + '\n'.join(result['assets'])
            job['ez3d_message'] = report + '\nUse esses assets existentes; não os gere novamente.'
            (folder / 'Para-o-ez3D.txt').write_text(job['ez3d_message'], encoding='utf-8')
        return self.launch('import', operation, project=project, source_job=source['id'])
