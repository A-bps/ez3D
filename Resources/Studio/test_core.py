import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from core import Studio


class PipelineTests(unittest.TestCase):
    def studio(self, root):
        return Studio({'work_root': root, 'project_roots': [], 'projects': [],
            'mesh_python':'mesh-python', 'image_python':'image-python', 'comfy_url':'http://127.0.0.1:8189',
            'comfy_root':'comfy', 'kimodo_root':'kimodo', 'engine':'engine'})

    def test_files_cannot_escape_work_root(self):
        with tempfile.TemporaryDirectory() as root:
            s = self.studio(root)
            with self.assertRaises(ValueError): s.inside(Path(root).parent / 'outside.png')

    def test_animation_requires_user_motion(self):
        with tempfile.TemporaryDirectory() as root:
            s = self.studio(root)
            with self.assertRaises(ValueError): s.chat('personagem', settings={'output':'animated'})
            self.assertIsNone(s.current)

    def test_internal_traceback_is_not_sent_to_chat(self):
        with tempfile.TemporaryDirectory() as root:
            s=self.studio(root)
            result=s.public_job({'status':'failed','error':'Traceback: C:/private/runtime.py RuntimeError GPU'})
            self.assertNotIn('Traceback',result['error'])
            self.assertNotIn('C:/',result['error'])

    def test_old_cancellation_is_not_displayed_as_failure(self):
        with tempfile.TemporaryDirectory() as root:
            s=self.studio(root)
            result=s.public_job({'status':'failed','error':'InterruptedError: Fluxo interrompido'})
            self.assertEqual(result['status'],'cancelled')
            self.assertEqual(result['error'],'')

    def test_old_questions_do_not_reopen_after_answer(self):
        with tempfile.TemporaryDirectory() as root:
            s=self.studio(root)
            result=s.public_job({'reply':{'questions':{'token':'old','questions':[]}}})
            self.assertFalse(result['questions_active'])

    def test_only_one_job_runs(self):
        with tempfile.TemporaryDirectory() as root:
            s = self.studio(root); gate = threading.Event(); started = threading.Event()
            def work(job): started.set();gate.wait(2)
            s.launch('test', work);started.wait(1)
            try:
                with self.assertRaises(RuntimeError): s.launch('other', work)
            finally:
                gate.set();s.lock.acquire(timeout=3);s.lock.release()

    def test_static_never_runs_kimodo_or_rig(self):
        with tempfile.TemporaryDirectory() as root:
            s=self.studio(root);folder=Path(root)/'job';folder.mkdir()
            image=Path(root)/'image.png';image.write_bytes(b'image')
            job={'folder':str(folder),'id':'test','artifacts':{},'events':[]}
            calls=[]
            def run(job,label,cmd,**kwargs):
                calls.append(label)
                if '--output' in cmd: Path(cmd[cmd.index('--output')+1]).write_bytes(b'x'*1024)
            with patch.object(s,'unload_planner') as unload,patch.object(s,'run',side_effect=run):
                s.build(job,image,{'output':'static'})
                unload.assert_called_once()
            self.assertEqual(calls,['Geometria','Texturas e materiais'])
            self.assertNotIn('fbx',job['artifacts'])

    def test_geometry_failure_blocks_texture(self):
        with tempfile.TemporaryDirectory() as root:
            s=self.studio(root);job={'folder':root,'id':'test','artifacts':{},'events':[]}
            with patch.object(s,'unload_planner'),patch.object(s,'run',side_effect=RuntimeError('geometry failed')) as run:
                with self.assertRaises(RuntimeError):s.build(job,Path(root)/'image.png',{'output':'static'})
                self.assertEqual(run.call_count,1)

    def test_unregistered_project_rejected_before_launch(self):
        with tempfile.TemporaryDirectory() as root:
            s=self.studio(root)
            with patch.object(s,'launch') as launch:
                with self.assertRaises(ValueError):s.import_job('job',str(Path(root)/'other.uproject'))
                launch.assert_not_called()

    def test_history_delete_preserves_artifacts_and_job_manifest(self):
        with tempfile.TemporaryDirectory() as root:
            s=self.studio(root);folder=Path(root)/'jobs'/'sample';folder.mkdir(parents=True)
            mesh=folder/'character.glb';mesh.write_bytes(b'preserved mesh')
            manifest=folder/'job.json';manifest.write_text(json.dumps({'id':'sample','status':'complete'}))
            s.edit_history('sample','Meu personagem')
            self.assertEqual(s.jobs()[0]['title'],'Meu personagem')
            s.edit_history('sample',hide=True)
            self.assertEqual(s.jobs(),[])
            self.assertEqual(mesh.read_bytes(),b'preserved mesh')
            self.assertTrue(manifest.is_file())

    def test_separate_studio_instances_cannot_overlap_gpu_jobs(self):
        with tempfile.TemporaryDirectory() as root:
            first, second = self.studio(root), self.studio(root)
            finish = threading.Event()
            first.launch('test', lambda job: finish.wait(3))
            try:
                with self.assertRaises(RuntimeError): second.launch('test', lambda job: None)
            finally:
                finish.set()
                for _ in range(100):
                    if first.current is None: break
                    threading.Event().wait(.02)
            self.assertIsNone(first.current)
            self.assertFalse(second.lock.locked())

    def test_artifact_missing_is_not_success(self):
        with tempfile.TemporaryDirectory() as root:
            s=self.studio(root);job={'folder':root,'id':'test','artifacts':{},'events':[]}
            with patch.object(s,'unload_planner'),patch.object(s,'run'):
                with self.assertRaises(RuntimeError):s.build(job,Path(root)/'image.png',{'output':'static'})


if __name__=='__main__':unittest.main()
