"""Model library independent of the retired image chat."""
from pathlib import Path
from core import Studio, read, save, HERE, slug
from motion_library import CLIPS, clip_folder
import hashlib
import json
import shutil
import struct


class ModelStudio(Studio):
    def learned_skin_source(self,source):
        ancestor=source;visited=set()
        while ancestor and ancestor.get('id') not in visited:
            visited.add(ancestor.get('id'))
            folder=Path(ancestor.get('folder',''))/'unirig'
            if (folder/'skin.fbx').is_file() and (folder/'rigged.glb').is_file():
                return dict(proxy_source=str(folder/'skin.fbx'),proxy_reference=str(folder/'rigged.glb'),proxy_regions=True)
            ident=ancestor.get('source_job')
            ancestor=read(self.inside(self.root/'jobs'/Path(ident).name/'job.json'),{}) if ident else None
        raise ValueError('A malha auxiliar dos pesos aprendidos não foi encontrada. Modelo preservado.')

    def joint_config(self,ident):
        source=read(self.inside(self.root/'jobs'/Path(ident).name/'job.json'),{})
        fbx=source.get('artifacts',{}).get('fbx')
        if not fbx:raise ValueError('Prepare o esqueleto antes de editar as articulações.')
        metadata=read(self.inside(Path(fbx).with_suffix('.unirig.json')), {})
        return {'mapping':metadata.get('mapping',{}),'saved':metadata.get('joint_config')}

    def edit_joints(self,ident,configuration,recalculate=False):
        from joint_config import validate_joint_config
        configuration=validate_joint_config(configuration)
        source=self.catalog_job(read(self.inside(self.root/'jobs'/Path(ident).name/'job.json'),{}))
        if source.get('status')!='complete' or not source.get('artifacts',{}).get('fbx'):
            raise ValueError('Selecione um personagem com esqueleto concluído.')
        base=self.inside(Path(source['artifacts']['fbx']).with_suffix('.blend'));self.require_file(base)
        def operation(job):
            folder=Path(job['folder']);request=folder/'joint-edit.json'
            settings=dict(rig_scene=str(base),joint_config=configuration,refine_weights=bool(recalculate),
                output=str(folder/'character.fbx'),preview=str(folder/'character.glb'))
            if recalculate and source.get('rig_validation',{}).get('weight_refinement')=='learned_proxy_barycentric_v1':
                settings.update(self.learned_skin_source(source))
            save(request,settings)
            label='Aplicando articulações e recalculando vínculo' if recalculate else 'Salvando articulações e preservando os pesos'
            self.run(job,label,[self.config['mesh_python'],'-u',HERE/'unirig_asset.py',request],timeout=1200)
            job['artifacts']=dict(source['artifacts'],character=str(folder/'character.glb'),fbx=str(folder/'character.fbx'))
            job['rig_validation']=read(folder/'character.validation.json');job['animations']=[]
            self.mark_rig_review(job)
        return self.launch('joint_edit',operation,source_job=ident,title=source.get('title','Personagem'))

    def catalog_job(self,job):
        job=dict(job);meta=read(self.root/'library-meta.json',{})
        key=str(job.get('artifacts',{}).get('textured') or job.get('artifacts',{}).get('character') or job.get('id'))
        entry=meta.get('models',{}).get(key,{})
        job['title']=entry.get('name') or job.get('title','Modelo')
        job['library_hidden']=bool(entry.get('hidden'))
        job['review_required']=bool(entry.get('review_required'))
        clips=[]
        for original in job.get('animations',[]):
            clip=dict(original);entry=meta.get('clips',{}).get(clip['preview'],{})
            clip.setdefault('prompt',clip['name'])
            if entry.get('hidden'):continue
            clip['name']=entry.get('name') or clip['name'];clips.append(clip)
        job['animations']=clips
        return job

    def mark_rig_review(self,source):
        key=str(source.get('artifacts',{}).get('textured') or source.get('artifacts',{}).get('character') or source['id'])
        meta=read(self.root/'library-meta.json',{})
        meta.setdefault('models',{}).setdefault(key,{})['review_required']=True
        save(self.root/'library-meta.json',meta)

    def edit_library(self,ident,target='model',clip=None,name=None,hide=False):
        if self.current:raise RuntimeError('Aguarde o trabalho terminar antes de organizar a biblioteca.')
        source=read(self.inside(self.root/'jobs'/Path(ident).name/'job.json'),{})
        if source.get('status')!='complete':raise ValueError('Modelo indisponível.')
        key=str(source.get('artifacts',{}).get('textured') or source.get('artifacts',{}).get('character') or ident)
        if target=='clip':
            if clip not in {c['preview'] for c in source.get('animations',[])}:raise ValueError('Animação desconhecida.')
            key=clip
        elif target!='model':raise ValueError('Tipo inválido.')
        meta=read(self.root/'library-meta.json',{});entry=meta.setdefault('clips' if target=='clip' else 'models',{}).setdefault(key,{})
        if hide:entry['hidden']=True
        else:
            name=str(name or '').strip()[:100]
            if not name:raise ValueError('Digite um nome.')
            entry['name']=name
        save(self.root/'library-meta.json',meta)
        return {'ok':True}

    def thumbnail_path(self,job):
        path=self.inside(job['artifacts'].get('character') or job['artifacts']['textured'])
        signature=hashlib.sha256((str(path)+str(path.stat().st_mtime_ns)).encode()).hexdigest()[:24]
        return self.root/'library-previews'/(signature+'.png')

    def save_thumbnail(self,ident,data):
        import base64,io
        from PIL import Image
        source=read(self.inside(self.root/'jobs'/Path(ident).name/'job.json'),{})
        if source.get('status')!='complete':raise ValueError('Modelo indisponível.')
        if not data.startswith('data:image/png;base64,') or len(data)>1500000:raise ValueError('Prévia inválida.')
        image=Image.open(io.BytesIO(base64.b64decode(data.split(',',1)[1],validate=True)))
        if image.width>512 or image.height>512:raise ValueError('Prévia muito grande.')
        path=self.thumbnail_path(source);path.parent.mkdir(exist_ok=True);image.save(path,format='PNG')
        return {'path':str(path)}

    def asset_folder(self,ident):
        source=self.catalog_job(read(self.inside(self.root/'jobs'/Path(ident).name/'job.json'),{}))
        if source.get('status')!='complete':raise ValueError('Modelo indisponível.')
        signature=hashlib.sha256(json.dumps([ident,source['title'],source['animations']],sort_keys=True).encode()).hexdigest()[:8]
        folder=self.root/'Modelos'/slug(source['title'])/('v_'+signature)
        model=folder/'Modelo';model.mkdir(parents=True,exist_ok=True)
        artifacts=source['artifacts'];base=slug(source['title'])
        skeletal=bool(artifacts.get('fbx')) and source.get('rig_validation',{}).get('motion_compatible') is not False
        for key,ext in [('character','.glb'),('fbx','.fbx')]:
            if skeletal and artifacts.get(key):shutil.copy2(self.inside(artifacts[key]),model/(base+ext))
        if not skeletal or not artifacts.get('character'):shutil.copy2(self.inside(artifacts['textured']),model/(base+'.glb'))
        if source['animations']:
            clips=folder/'Animations';clips.mkdir(exist_ok=True)
            for i,clip in enumerate(source['animations']):shutil.copy2(self.inside(clip['fbx']),clips/(str(i+1).zfill(2)+'_'+slug(clip['name'])+'.fbx'))
        raw=self.inside(artifacts['textured']).read_bytes();length=struct.unpack_from('<I',raw,12)[0]
        document=json.loads(raw[20:20+length]);binary=raw[28+length:]
        textures=folder/'Texturas';textures.mkdir(exist_ok=True)
        for i,image in enumerate(document.get('images',[])):
            if 'bufferView' not in image:continue
            view=document['bufferViews'][image['bufferView']];offset=view.get('byteOffset',0)
            extension={'image/jpeg':'.jpg','image/webp':'.webp'}.get(image.get('mimeType'),'.png')
            (textures/('textura_'+str(i+1)+extension)).write_bytes(binary[offset:offset+view['byteLength']])
        return folder

    def generate_motion(self,job,description):
        from motion_language import kimodo_prompt
        return super().generate_motion(job,kimodo_prompt(description))

    def edit_region(self,ident,reference,brushes=None,reconstruct=False,selection=None,alignment=None,strength=.75):
        import math
        source=self.catalog_job(read(self.inside(self.root/'jobs'/Path(ident).name/'job.json'),{}))
        if source.get('status')!='complete':raise ValueError('Selecione um modelo concluído.')
        image=self.inside(reference);self.require_file(image)
        if not isinstance(strength,(float,int)) or not math.isfinite(strength) or not 0<strength<=1:raise ValueError('Intensidade inválida.')
        if alignment:
            from regional_alignment import fit_alignment
            if not isinstance(alignment,dict):raise ValueError('Alinhamento inválido.')
            fit_alignment(alignment.get('target'),alignment.get('reference'))
            if not selection:raise ValueError('O alinhamento precisa de um contorno.')
        if selection is not None:
            if not isinstance(selection,dict):raise ValueError('Seleção inválida.')
            polygon=selection.get('polygon');matrix=selection.get('matrix')
            if not isinstance(polygon,list) or not 3<=len(polygon)<=2048 or not all(isinstance(p,list) and len(p)==2 and all(isinstance(v,(int,float)) and math.isfinite(v) and 0<=v<=1 for v in p) for p in polygon):raise ValueError('Feche um contorno com pelo menos três pontos.')
            if not isinstance(matrix,list) or len(matrix)!=16 or not all(isinstance(v,(int,float)) and math.isfinite(v) and abs(v)<1e9 for v in matrix):raise ValueError('Câmera de seleção inválida.')
            camera=selection.get('camera')
            if camera is not None and (not isinstance(camera,list) or len(camera)!=3 or not all(isinstance(v,(float,int)) and math.isfinite(v) and abs(v)<10000 for v in camera)):raise ValueError('Posição de câmera inválida.')
            area=abs(sum(a[0]*b[1]-b[0]*a[1] for a,b in zip(polygon,polygon[1:]+polygon[:1])))*.5
            if area<1e-5:raise ValueError('O contorno precisa envolver uma área da malha.')
            brushes=[]
        elif not isinstance(brushes,list) or not 1<=len(brushes)<=256:raise ValueError('Selecione uma região do modelo.')
        for brush in brushes:
            point=brush.get('point');radius=brush.get('radius')
            if not isinstance(point,list) or len(point)!=3 or not all(isinstance(v,(int,float)) and math.isfinite(v) and abs(v)<10000 for v in point):raise ValueError('Seleção inválida.')
            if not isinstance(radius,(int,float)) or not math.isfinite(radius) or not 0<radius<100:raise ValueError('Raio inválido.')
        if reconstruct and read(self.root / 'trellis-status.json', {}).get('ready') is not True:raise RuntimeError('TRELLIS.2 indisponível.')
        def operation(job):
            folder=Path(job['folder']);patch=None
            if reconstruct:
                patch=folder/'reference-part.glb';request=folder/'trellis-request.json'
                save(request,dict(image=self.linux_path(image),output=self.linux_path(patch),model=self.config['trellis_model'],texture_size=2048,gpu_mode='resident',triangle_budget=120000))
                self.run(job,'Reconstruindo referência da região · experimental',['wsl','-d','Ubuntu-24.04','--','/opt/ez3d/venv/bin/python',self.linux_path(HERE/'trellis_worker.py'),self.linux_path(request)],timeout=2400)
                self.require_file(patch)
            request=folder/'region-request.json';output=folder/'textured.glb'
            save(request,dict(input=source['artifacts']['textured'],reference=str(image),brushes=brushes,selection=selection,alignment=alignment,texture_strength=strength,patch=str(patch) if patch else None,output=str(output)))
            self.run(job,'Aplicando alteração somente à região selecionada',[self.config['mesh_python'],'-u',HERE/'regional_edit.py',request],timeout=1200)
            self.require_file(output)
            job['artifacts']=dict(textured=str(output),image=source['artifacts'].get('image',str(image)))
            job['edit_report']=read(output.with_suffix('.edit.json'),{})
            job['animations']=[]
        return self.launch('region_edit',operation,title=source['title']+' · edição',source_job=source['id'],reference=str(image))
    def tokenrig_root(self):
        return Path(self.config.get('tokenrig_root', 'Runtime/SkinTokens'))

    def runtime_status(self):
        status = read(self.root / 'trellis-status.json', {})
        ready = status.get('ready') is True
        rig_root = self.tokenrig_root()
        rig_ready = all((rig_root / filename).is_file() for filename in (
            'demo.py', 'models/Qwen3-0.6B/config.json',
            'experiments/skin_vae_2_10_32768/last.ckpt',
            'experiments/articulation_xl_quantization_256_token_4/grpo_1400.ckpt'))
        return {'catalog_version':1,'generation_ready':ready, 'rig_ready':rig_ready,
            'rig_message':'TokenRig disponível.' if rig_ready else 'Instalação do TokenRig incompleta.', 'message':status.get('message') or 'TRELLIS.2 indisponível.'}

    @staticmethod
    def linux_path(path):
        value = Path(path).resolve().as_posix()
        return '/mnt/' + value[0].lower() + value[2:]

    def create_model(self, reference, name=None, auto_rig=True,quality=None,diagnostics=False,engine=None,views=None):
        from generation_quality import generation_settings
        settings=generation_settings(quality or self.config.get('generation_quality','maximum'))
        status = self.runtime_status()
        if not status['generation_ready']:
            raise RuntimeError(status['message'])
        if auto_rig and not status['rig_ready']:raise RuntimeError(status['rig_message'])
        image = self.inside(reference)
        self.require_file(image)
        if engine == 'hunyuan':
            return self.create_multiview_model(image, views or {}, name, auto_rig, str(quality or ''), settings)
        def operation(job):
            folder = Path(job['folder'])
            output = folder / 'textured.glb'
            request = folder / 'trellis-request.json'
            save(request, {**settings, 'diagnostics': bool(diagnostics),
                'image': self.linux_path(image), 'output': self.linux_path(output),
                'model': self.config['trellis_model'], 'texture_size': 4096,
                'progress_stages': 12 if auto_rig else 6})
            self.run(job, 'Gerando geometria e materiais PBR · TRELLIS.2',
                ['wsl', '-d', 'Ubuntu-24.04', '--', '/opt/ez3d/venv/bin/python',
                 self.linux_path(HERE/'trellis_worker.py'), self.linux_path(request)], timeout=2400)
            self.require_file(output)
            job['artifacts'] = {'textured': str(output), 'image': str(image)}
            if auto_rig:self.rig_into(job)
        return self.launch('model',operation,title=str(name or 'Modelo')[:80],reference=str(image),auto_rig=auto_rig,generation_settings=settings)

    def create_multiview_model(self, front, views, name, auto_rig, quality, settings):
        photos = {'front': front}
        for key in ('left', 'back', 'right'):
            if views.get(key):
                photos[key] = self.inside(views[key])
                self.require_file(photos[key])
        detail = ({'steps': 30, 'octree': 256, 'faces': 40000} if 'balanced' in quality or quality == 'character'
                  else {'steps': 50, 'octree': 380, 'faces': 90000})
        def operation(job):
            folder = Path(job['folder'])
            output = folder / 'textured.glb'
            request = folder / 'hunyuan-request.json'
            save(request, {**detail, 'seed': 42, 'output': self.linux_path(output),
                'images': {key: self.linux_path(value) for key, value in photos.items()}})
            self.run(job, 'Gerando geometria e texturas · Hunyuan3D multivista',
                ['wsl', '-d', 'Ubuntu-24.04', '--', '/opt/ez3d-hunyuan/venv/bin/python',
                 self.linux_path(HERE/'hunyuan_worker.py'), self.linux_path(request)], timeout=3600)
            self.require_file(output)
            job['artifacts'] = {'textured': str(output), 'image': str(front)}
            if auto_rig:self.rig_into(job)
        return self.launch('model',operation,title=str(name or 'Modelo')[:80],reference=str(front),auto_rig=auto_rig,generation_settings=settings)

    def rig_into(self,job):
        folder = Path(job['folder'])
        rig_folder = folder / 'tokenrig'
        rig_folder.mkdir(exist_ok=True)
        rigged = rig_folder / 'rigged.glb'
        save(folder/'trellis-request.progress.json', {'message':'TokenRig · gerando esqueleto e pesos',
            'stage_index':6,'stage_count':12,'value':6,'max':12,'measured':False})
        self.run(job, 'TokenRig · gerando esqueleto e pesos',
            ['wsl','-d','Ubuntu-24.04','--cd',self.linux_path(self.tokenrig_root()),'--',
             '/opt/ez3d/skintokens-venv/bin/python','-u','demo.py',
             '--input',self.linux_path(job['artifacts']['textured']),
             '--output',self.linux_path(rigged),'--use_transfer'], timeout=1200)
        self.require_file(rigged)
        final = folder / 'rig-finalize.json'
        save(final, {'input':str(rigged),'output':str(folder/'character.fbx'),
            'preview':str(folder/'character.glb'),'native_tokenrig':True})
        self.run(job,'Validando esqueleto e materiais',[self.config['mesh_python'],'-u',HERE/'unirig_asset.py',final],timeout=600)
        report = read(folder/'character.validation.json', {})
        if not report.get('technical_validation'):
            raise RuntimeError('Validação do TokenRig ausente.')
        job['artifacts'].update(character=str(folder/'character.glb'),fbx=str(folder/'character.fbx'))
        job['rig_validation']=report

    def prepare_rig(self,job_id,game_ready=False):
        if not self.runtime_status()['rig_ready']:raise RuntimeError(self.runtime_status()['rig_message'])
        source=self.catalog_job(read(self.inside(self.root/'jobs'/Path(job_id).name/'job.json'),{}))
        if source.get('status')!='complete' or not source.get('artifacts',{}).get('textured'):
            raise ValueError('Selecione uma malha concluída.')
        if game_ready:
            def operation(job):
                folder = Path(job['folder'])
                request = folder / 'game-mesh.json'
                output = folder / 'textured.glb'
                save(request, dict(input=source['artifacts']['textured'], output=str(output), budget=60000))
                self.run(job, 'Reduzindo malha para jogo', [self.config['mesh_python'], '-u',
                    HERE/'game_mesh.py', request], timeout=600)
                self.require_file(output)
                job['artifacts'] = dict(textured=str(output), image=source['artifacts'].get('image'))
                self.rig_into(job)
            return self.launch('rig', operation, source_job=source['id'],
                title=source['title']+' · versão para jogo')
        if source.get('rig_validation',{}).get('method')=='TokenRig_learned_skeleton_and_skin':
            return self.repair_character(job_id,reapply_clips=False)
        def operation(job):
            job['artifacts']=dict(source['artifacts']);self.rig_into(job)
        return self.launch('rig',operation,source_job=source['id'],asset_id=source.get('asset_id',source['id']),
            title=source.get('title','Personagem'))

    def compare_exports(self,ident):
        source=read(self.inside(self.root/'jobs'/Path(ident).name/'job.json'),{})
        bundle=self.inside(Path(source['folder'])/'decoded.pt');self.require_file(bundle)
        if source.get('status')!='complete':raise ValueError('Espere a geração terminar.')
        def operation(job):
            folder=Path(job['folder']);request=folder/'export-comparison.json'
            save(request,{'input':self.linux_path(bundle)})
            self.run(job,'Comparando preservação da superfície',['wsl','-d','Ubuntu-24.04','--','/opt/ez3d/venv/bin/python',self.linux_path(HERE/'trellis_export_compare.py'),self.linux_path(request)],timeout=1800)
            self.require_file(folder/'projected.glb')
            job['artifacts']={'textured':str(folder/'projected.glb'),'image':source['artifacts']['image']}
            job['comparison']=read(folder/'comparison.json',[])
        return self.launch('export_comparison',operation,title='Ban · superfície preservada',source_job=ident)

    def add_animation(self,job_id,description='',preset=None,rear_garment=False):
        source=self.catalog_job(read(self.inside(self.root/'jobs'/Path(job_id).name/'job.json'),{}))
        preset_folder=clip_folder(self.root,preset) if preset is not None else None
        if source.get('rig_validation',{}).get('method') not in {'UniRig_learned_skeleton_and_skin','TokenRig_learned_skeleton_and_skin'}:
            if preset_folder or rear_garment:raise ValueError('Prepare um esqueleto humanoide para aplicar esta animação.')
            return super().add_animation(job_id,description)
        if source.get('status')!='complete' or not source['rig_validation'].get('motion_compatible'):
            raise ValueError('Este esqueleto precisa de adaptação para movimentos humanoides.')
        if not rear_garment and source['rig_validation'].get('weight_refinement') not in ('surface_analytic_v7','learned_proxy_barycentric_v1','tokenrig_native'):
            raise ValueError('Clique em Revisar esqueleto antes de gerar movimentos neste modelo antigo.')
        description=CLIPS[preset] if preset_folder else str(description).strip()
        if not description:raise ValueError('Descreva a animação.')
        base=self.inside(Path(source['artifacts']['fbx']).with_suffix('.blend'));self.require_file(base)
        proxy=self.learned_skin_source(source) if rear_garment else None
        def operation(job):
            motion=preset_folder or self.generate_motion(job,description)
            folder=Path(job['folder']);request=folder/'animation-request.json'
            rig_scene=base
            artifacts=dict(source['artifacts'])
            validation=source['rig_validation']
            def rebuild_binding(settings, cape):
                nonlocal rig_scene, validation
                bind_request=folder/'binding.json'
                fbx=folder/'character.fbx';preview=folder/'character.glb'
                save(bind_request,dict(settings,rig_scene=str(base),proxy_regions=False,
                    rear_garment=cape,smooth_surface=not cape,output=str(fbx),preview=str(preview)))
                self.run(job,'Ajustando pesos na malha',[self.config['mesh_python'],'-u',HERE/'unirig_asset.py',bind_request],timeout=1200)
                rig_scene=fbx.with_suffix('.blend')
                artifacts.update(fbx=str(fbx),character=str(preview))
                validation=read(fbx.with_suffix('.validation.json'))

            def apply_motion():
                save(request,{'rig_scene':str(rig_scene),'motion':str(motion),'kimodo_root':self.config['kimodo_root'],
                    'refine_weights':False,'output':str(folder/'motion.fbx'),'preview':str(folder/'motion.glb')})
                self.run(job,'Adaptando movimento ao esqueleto',[self.config['mesh_python'],'-u',HERE/'unirig_asset.py',request],timeout=600)

            if proxy:
                rebuild_binding(proxy,True)
            recovered=False
            try:
                apply_motion()
            except RuntimeError as error:
                if 'esticou a malha' not in str(error) and 'costura da malha' not in str(error):
                    raise
                self.mark_rig_review(source)
                if proxy:
                    raise
                if validation.get('weight_refinement')=='tokenrig_native':
                    rebuild_binding({'refine_weights':True},False)
                elif validation.get('weight_refinement')=='surface_analytic_v7':
                    try:
                        settings=self.learned_skin_source(source)
                    except ValueError:
                        raise error from None
                    rebuild_binding(settings,False)
                else:
                    raise
                apply_motion()
                recovered=True
            if recovered:
                job['title']=source.get('title','Personagem')+' · vínculo suavizado'
                job['asset_id']=job['id']
            job['artifacts']=artifacts;job['rig_validation']=validation
            clip={'name':description,'fbx':str(folder/'motion.fbx'),'preview':str(folder/'motion.glb')}
            if preset_folder:clip['preset']=preset
            job['animations']=([] if proxy or recovered else source.get('animations',[]))+[clip]
            if proxy or recovered:
                textured=folder/'textured.glb'
                shutil.copy2(self.inside(artifacts['textured']),textured)
                artifacts['textured']=str(textured)
                return
            meta=read(self.root/'library-meta.json',{})
            key=str(source['artifacts'].get('textured') or source['id'])
            meta.setdefault('models',{}).setdefault(key,{})['review_required']=False
            save(self.root/'library-meta.json',meta)
        title=source.get('title','Personagem')
        if proxy:
            return self.launch('animation',operation,source_job=source['id'],title=title+' · capa revisada')
        return self.launch('animation',operation,source_job=source['id'],asset_id=source.get('asset_id',source['id']),title=title)

    def repair_character(self,job_id,reapply_clips=True):
        source=self.catalog_job(read(self.inside(self.root/'jobs'/Path(job_id).name/'job.json'),{}))
        if source.get('rig_validation',{}).get('method') not in {'UniRig_learned_skeleton_and_skin','TokenRig_learned_skeleton_and_skin'}:
            raise ValueError('Selecione um personagem com esqueleto.')
        def operation(job):
            folder=Path(job['folder']);request=folder/'repair.json'
            raw=Path(source['artifacts']['fbx']).parent/'unirig/rigged.glb'
            ancestor=source;visited=set()
            while not raw.is_file() and ancestor.get('source_job') and ancestor['source_job'] not in visited:
                visited.add(ancestor['source_job'])
                ancestor=read(self.inside(self.root/'jobs'/Path(ancestor['source_job']).name/'job.json'),{})
                candidate=Path(ancestor.get('folder',folder))/'unirig/rigged.glb'
                if candidate.is_file():raw=candidate
            save(request,{'input':str(raw) if raw.is_file() else source['artifacts']['character'],'output':str(folder/'character.fbx'),
                'preview':str(folder/'character.glb'),
                'native_tokenrig':source['rig_validation']['method']=='TokenRig_learned_skeleton_and_skin',
                'refine_weights':True})
            saved_rig=Path(source['artifacts']['fbx']).with_suffix('.blend')
            annotations=read(saved_rig.with_suffix('.unirig.json'),{}).get('joint_config')
            if annotations:
                save(request,{'rig_scene':str(saved_rig),'refine_weights':True,
                    'output':str(folder/'character.fbx'),'preview':str(folder/'character.glb')})
            if source.get('rig_validation',{}).get('weight_refinement')=='learned_proxy_barycentric_v1':
                settings=read(request);settings.update(rig_scene=str(saved_rig),**self.learned_skin_source(source))
                settings.pop('input',None);save(request,settings)
            self.run(job,'Revisando pesos e vínculo',[self.config['mesh_python'],'-u',HERE/'unirig_asset.py',request],timeout=600)
            job['artifacts']=dict(source['artifacts'],character=str(folder/'character.glb'),fbx=str(folder/'character.fbx'))
            job['rig_validation']=read(folder/'character.validation.json');job['animations']=[]
            for i,clip in enumerate(source.get('animations',[]) if reapply_clips else []):
                motion=clip_folder(self.root,clip['preset']) if clip.get('preset') else self.generate_motion(job,clip.get('prompt',clip['name']))
                request=folder/f'clip-{i}.json'
                save(request,{'rig_scene':str(folder/'character.blend'),'motion':str(motion),
                    'kimodo_root':self.config['kimodo_root'],'output':str(folder/f'clip-{i}.fbx'),
                    'preview':str(folder/f'clip-{i}.glb')})
                self.run(job,'Revisando animação '+str(i+1),[self.config['mesh_python'],'-u',HERE/'unirig_asset.py',request],timeout=600)
                job['animations'].append(dict(clip,fbx=str(folder/f'clip-{i}.fbx'),preview=str(folder/f'clip-{i}.glb')))
        return self.launch('repair',operation,source_job=source['id'],title=source.get('title','Personagem'))

    def export_model(self, job_id, name, format, resolution):
        if format not in ('glb','obj','fbx') or resolution not in (512,1024,2048,4096):
            raise ValueError('Formato ou resolução inválidos.')
        source = read(self.inside(self.root/'jobs'/Path(job_id).name/'job.json'),{})
        if source.get('status') != 'complete': raise ValueError('Modelo indisponível.')
        artifacts=source.get('artifacts',{})
        asset=self.inside(artifacts.get('character') or artifacts.get('textured') or '')
        self.require_file(asset)
        def operation(job):
            folder=Path(job['folder'])
            request=folder/'export-request.json'
            save(request, {'input':str(asset),'folder':str(folder/'files'),'name':slug(name or 'modelo'),
                'format':format,'resolution':resolution})
            self.run(job,'Exportando modelo',[self.config['mesh_python'],'-u',HERE/'export_asset.py',request],timeout=600)
            receipt=read(folder/'files'/'receipt.json',{})
            output=self.inside(receipt['output']);self.require_file(output)
            job['export_file']=str(output)
        return self.launch('export',operation,source_job=job_id,title='Exportação · '+str(name or 'Modelo'))

    def library(self):
        result, seen = [], set()
        for path in sorted((self.root / 'jobs').glob('*/job.json'), reverse=True):
            job = self.catalog_job(read(path, {}))
            artifacts = job.get('artifacts', {})
            preview = artifacts.get('character') or artifacts.get('textured')
            if job.get('status') != 'complete' or not preview:
                continue
            try:
                preview = self.inside(preview)
            except ValueError:
                continue
            key=str(artifacts.get('textured') or preview)
            if not preview.is_file() or str(preview) in seen or key in seen:
                continue
            seen.add(str(preview))
            seen.add(key)
            if job.get('library_hidden'):continue
            fbx = artifacts.get('fbx')
            rigged = bool(fbx and Path(fbx).with_suffix('.blend').is_file())
            result.append(dict(id=job['id'], name=job.get('title') or 'Modelo',
                preview=str(preview), folder=job['folder'], rigged=rigged,
                motion_compatible=job.get('rig_validation',{}).get('motion_compatible',rigged),review_required=job.get('review_required',False),
                thumbnail=str(self.thumbnail_path(job)) if self.thumbnail_path(job).is_file() else None,
                reference=artifacts.get('image'), animations=job.get('animations', []), artifacts=artifacts))
        return result

    def unload_planner(self, job):
        # The new Studio has no conversational model to keep resident.
        return
