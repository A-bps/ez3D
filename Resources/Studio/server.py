"""ez3D local HTTP shell. No remote service or API key is required."""
import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import mimetypes
import os
from pathlib import Path
import secrets
import sys
import threading
from urllib.parse import parse_qs, unquote, urlsplit
from PIL import Image
from core import HERE, Studio, read, save
from model_studio import ModelStudio
from motion_library import catalog as motion_catalog

config = read(HERE / 'studio_config.json')
studio = ModelStudio(config)
token = secrets.token_urlsafe(32)
port = int(config['port'])


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_): pass

    def host_ok(self):
        return self.headers.get('Host') in (f'127.0.0.1:{port}', f'localhost:{port}')

    def respond(self, value, code=200):
        data = json.dumps(value, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers(); self.wfile.write(data)

    def do_GET(self):
        if not self.host_ok(): return self.respond({'error': 'Host inválido'}, 403)
        parts = urlsplit(self.path)
        try:
            if parts.path == '/api/state':
                current = studio.public_job(studio.current) if studio.current else None
                if current and current.get('kind') in ('model','rig','repair','animation','region_edit','joint_edit'):
                    progress = read(Path(current['folder']) / 'trellis-request.progress.json', {})
                    current['progress'] = progress.get('message', '')
                    current['generation_progress'] = progress
                if current and current.get('stage') == 'Imagem e revisão visual':
                    progress = read(Path(current['folder']) / 'request.json.progress.json', {})
                    current['progress'] = progress.get('message', '')
                    current['progress_value'] = progress.get('value')
                    current['progress_max'] = progress.get('max')
                return self.respond({'version': 3, 'token': token, 'current': current, 'jobs': studio.jobs(),
                    'library': studio.library(), 'runtime': studio.runtime_status(),
                    'projects': studio.projects(), 'chat': studio.chat_history(), 'root': str(studio.root)})
            if parts.path == '/api/joints':
                return self.respond(studio.joint_config(parse_qs(parts.query)['job'][0]))
            if parts.path == '/api/motion-library':
                return self.respond(motion_catalog(studio.root))
            if parts.path == '/file':
                path = studio.inside(parse_qs(parts.query)['path'][0])
            else:
                relative = unquote(parts.path).lstrip('/') or 'studio3.html'
                path = (HERE / relative).resolve()
                if not path.is_relative_to(HERE) or not (relative in ('index.html','style.css','app.js','viewer.js','studio3.html','studio3.css','studio3.js','viewer3.js','hologram_intro.js','regional_alignment.js','generation_particles.js','theme-ezai.css','brand.js','ez3d-mark.svg','ez3d-loading.svg','ez3d.png','ez3d.ico') or relative.startswith('vendor/')):
                    raise ValueError('Arquivo indisponível.')
            if not path.is_file(): return self.respond({'error': 'Arquivo não encontrado'}, 404)
            self.send_response(200)
            self.send_header('Content-Type', mimetypes.guess_type(path)[0] or 'application/octet-stream')
            self.send_header('Content-Length', str(path.stat().st_size))
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Cache-Control', 'no-cache')
            self.end_headers()
            with path.open('rb') as source:
                while block := source.read(1024 * 1024): self.wfile.write(block)
        except (ValueError, KeyError) as exc: self.respond({'error': str(exc)}, 400)
        except (BrokenPipeError, ConnectionResetError): pass

    def do_POST(self):
        if not self.host_ok() or self.headers.get('X-Studio-Token') != token:
            return self.respond({'error': 'Sessão inválida'}, 403)
        origin = self.headers.get('Origin')
        if origin and origin not in (f'http://127.0.0.1:{port}', f'http://localhost:{port}'):
            return self.respond({'error': 'Origem inválida'}, 403)
        try:
            length = int(self.headers.get('Content-Length', 0))
            if not 0 < length <= 30 * 1024 * 1024: raise ValueError('Pedido vazio ou grande demais.')
            data = json.loads(self.rfile.read(length))
            route = urlsplit(self.path).path
            if route == '/api/shutdown':
                if not studio.lock.acquire(blocking=False):
                    raise RuntimeError('Espere o trabalho terminar antes de encerrar o sistema.')
                try:
                    if studio.current:raise RuntimeError('Espere o trabalho terminar antes de encerrar o sistema.')
                    studio.shutdown_requested=True
                finally:studio.lock.release()
                self.respond({'ok':True,'message':'ez3D encerrado.'})
                threading.Thread(target=self.server.shutdown,daemon=True).start()
                return
            if route == '/api/history':
                return self.respond(studio.edit_history(str(data['job']), data.get('title'), data.get('hide') is True))
            if route == '/api/joints':
                return self.respond({'job':studio.edit_joints(data['job'],data['configuration'],data.get('recalculate') is True)['id']})
            if route == '/api/upload':
                raw = base64.b64decode(data['data'].split(',')[-1], validate=True)
                image = Image.open(io.BytesIO(raw)); image.load()
                if image.width * image.height > 25_000_000: raise ValueError('Imagem grande demais.')
                folder = studio.root / 'references'; folder.mkdir(exist_ok=True)
                path = folder / (secrets.token_hex(10) + '.png')
                image.convert('RGBA').save(path)
                result={'path':str(path)}
                if data.get('suggest_face_crop') is True:
                    try:
                        import cv2,numpy as np
                        rgb=image.convert('RGB');scale=min(1.,1024/max(rgb.size))
                        small=rgb.resize((round(rgb.width*scale),round(rgb.height*scale)))
                        detector=cv2.CascadeClassifier(cv2.data.haarcascades+'haarcascade_frontalface_default.xml')
                        faces=detector.detectMultiScale(cv2.cvtColor(np.asarray(small),cv2.COLOR_RGB2GRAY),scaleFactor=1.1,minNeighbors=5,minSize=(50,50))
                        if len(faces):
                            x,y,w,h=sorted(faces,key=lambda f:(f[1],-f[2]*f[3]))[0]/scale
                            box=(max(0,int(x+w*.08)),max(0,int(y+h*.08)),min(rgb.width,int(x+w*.92)),min(rgb.height,int(y+h*1.06)))
                            face_path=path.with_name(path.stem+'-face.png');rgb.crop(box).save(face_path)
                            result['face_path']=str(face_path)
                    except (ImportError,AttributeError,ValueError):pass
                return self.respond(result)
            if route == '/api/message':
                return self.respond({'error':'O chat de imagens foi retirado. Use a aba Modelo.'}, 410)
            if route == '/api/model':
                job = studio.create_model(data['reference'], data.get('name'), data.get('auto_rig',True),data.get('quality'),data.get('diagnostics') is True,data.get('engine'),data.get('views'))
                return self.respond({'job':job['id']})
            if route == '/api/rig':
                job = studio.prepare_rig(data['job'], game_ready=data.get('game_ready') is True)
                return self.respond({'job':job['id']})
            if route == '/api/region-edit':
                job=studio.edit_region(data['job'],data['reference'],data.get('brushes'),data.get('reconstruct') is True,data.get('selection'),data.get('alignment'),data.get('strength',.75))
                return self.respond({'job':job['id']})
            if route == '/api/repair-rig':
                job = studio.repair_character(data['job'])
                return self.respond({'job':job['id']})
            if route == '/api/compare-exports':
                job=studio.compare_exports(data['job'])
                return self.respond({'job':job['id']})
            if route == '/api/library-edit':
                return self.respond(studio.edit_library(data['job'],data.get('target','model'),
                    data.get('clip'),data.get('name'),data.get('hide',False)))
            if route == '/api/thumbnail':
                return self.respond(studio.save_thumbnail(data['job'],data['data']))
            if route == '/api/asset-folder':
                folder=studio.asset_folder(data['job'])
                os.startfile(str(folder))
                return self.respond({'path':str(folder)})
            if route == '/api/export':
                job = studio.export_model(data['job'], data.get('name'), data['format'], data['resolution'])
                return self.respond({'job':job['id']})
            if route == '/api/import':
                job = studio.import_job(data['job'], data['project'])
                return self.respond({'job': job['id']})
            if route == '/api/build':
                return self.respond({'error':'Gerador antigo retirado. Use TRELLIS.2 na aba Modelo.'}, 410)
            if route == '/api/animation':
                job = studio.add_animation(data['job'], data.get('motion',''), preset=data.get('preset'),
                                           rear_garment=data.get('rear_garment') is True)
                return self.respond({'job': job['id']})
            if route == '/api/project':
                project = Path(data['path']).resolve()
                if not project.is_file() or project.suffix.lower() != '.uproject': raise ValueError('Escolha um arquivo .uproject existente.')
                json.loads(project.read_text(encoding='utf-8-sig'))
                config.setdefault('projects', []).append(str(project))
                config['projects'] = sorted(set(config['projects']))
                save(HERE / 'studio_config.json', config)
                return self.respond({'projects': studio.projects()})
            if route == '/api/pause':
                if studio.current: (Path(studio.current['folder']) / 'pause').touch()
                return self.respond({'message': 'O fluxo pausará antes da próxima etapa.'})
            if route == '/api/folder':
                path = studio.inside(data['path'])
                os.startfile(str(path if path.is_dir() else path.parent))
                return self.respond({'ok': True})
            return self.respond({'error': 'Operação desconhecida'}, 404)
        except Exception as exc:
            self.respond({'error': str(exc)}, 409 if isinstance(exc, RuntimeError) else 400)


if __name__ == '__main__':
    print(f'ez3D: http://127.0.0.1:{port}', flush=True)
    with ThreadingHTTPServer(('127.0.0.1', port), Handler) as httpd:
        httpd.serve_forever()
