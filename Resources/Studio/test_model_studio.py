import tempfile
from pathlib import Path
import unittest
from core import save
from model_studio import ModelStudio


class ModelLibraryTests(unittest.TestCase):
    def test_learned_skin_recalculation_follows_original_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);studio=ModelStudio({'work_root':directory})
            original=root/'jobs/original';(original/'unirig').mkdir(parents=True)
            for name in ('skin.fbx','rigged.glb'):(original/'unirig'/name).write_bytes(b'test')
            save(original/'job.json',dict(id='original',folder=str(original)))
            settings=studio.learned_skin_source(dict(id='copy',folder=str(root/'jobs/copy'),source_job='original'))
            self.assertEqual(Path(settings['proxy_source']),original/'unirig/skin.fbx')
            self.assertTrue(settings['proxy_regions'])
            self.assertNotIn('separate_feet',settings)

    def test_asset_folder_contains_delivery_files_without_internal_logs(self):
        import json,struct
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);studio=ModelStudio({'work_root':directory});raw=root/'internal';raw.mkdir()
            document=json.dumps({'asset':{'version':'2.0'},'images':[]}).encode();document+=b' '*((-len(document))%4)
            data=struct.pack('<III',0x46546c67,2,28+len(document))+struct.pack('<II',len(document),0x4e4f534a)+document+struct.pack('<II',0,0x004e4942)
            mesh=raw/'mesh.glb';mesh.write_bytes(data);fbx=raw/'model.fbx';fbx.write_bytes(b'x'*128);clip=raw/'clip.fbx';clip.write_bytes(b'y'*128)
            save(root/'jobs/001/job.json',{'id':'001','title':'Personagem','status':'complete','folder':str(raw),
                'artifacts':{'textured':str(mesh),'fbx':str(fbx)},'animations':[{'name':'Walk','fbx':str(clip),'preview':str(raw/'clip.glb')}]})
            folder=studio.asset_folder('001')
            self.assertEqual((folder/'Modelo/personagem.glb').read_bytes(),data)
            self.assertTrue((folder/'Modelo/personagem.fbx').is_file())
            self.assertTrue((folder/'Animations/01_walk.fbx').is_file())
            self.assertFalse(list(folder.rglob('*.json')))
            self.assertTrue(mesh.exists() and fbx.exists() and clip.exists())

    def test_catalog_edits_survive_new_animation_versions_and_preserve_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);studio=ModelStudio({'work_root':directory})
            mesh=root/'mesh.glb';mesh.touch();clip=root/'walk.glb';clip.touch();fbx=root/'walk.fbx';fbx.touch()
            for ident in ['001','002']:
                save(root/'jobs'/ident/'job.json',{'id':ident,'status':'complete','title':'Original','folder':str(root),
                    'artifacts':{'textured':str(mesh)},'animations':[{'name':'Walk','preview':str(clip),'fbx':str(fbx)}]})
            studio.edit_library('001',name='Renamed')
            studio.edit_library('002',target='clip',clip=str(clip),name='Caminhada')
            item=studio.library()[0]
            self.assertEqual(item['name'],'Renamed');self.assertEqual(item['animations'][0]['name'],'Caminhada')
            self.assertEqual(item['animations'][0]['prompt'],'Walk')
            studio.edit_library('002',target='clip',clip=str(clip),hide=True)
            self.assertEqual(studio.library()[0]['animations'],[])
            studio.edit_library('002',hide=True)
            self.assertEqual(studio.library(),[])
            self.assertTrue(mesh.exists() and clip.exists() and fbx.exists())

    def test_deleting_chat_does_not_hide_model_or_lose_clips(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);studio=ModelStudio({'work_root':directory})
            mesh=root/'mesh.glb';mesh.touch()
            for ident,clips in [('001',[]),('002',[{'name':'Walk','preview':'clip.glb'}])]:
                save(root/'jobs'/ident/'job.json',{'id':ident,'status':'complete','title':'Personagem',
                    'folder':str(root),'artifacts':{'textured':str(mesh)},'animations':clips})
            save(root/'history.json',{'001':{'hidden':True},'002':{'hidden':True}})
            models=studio.library()
            self.assertEqual(len(models),1)
            self.assertEqual(models[0]['animations'][0]['name'],'Walk')
            self.assertTrue(mesh.exists())

    def test_unprepared_runtime_never_launches_a_fake_generation(self):
        with tempfile.TemporaryDirectory() as directory:
            studio=ModelStudio({'work_root':directory})
            with self.assertRaises(RuntimeError): studio.create_model('reference.png')
            self.assertIsNone(studio.current)
            self.assertFalse((Path(directory)/'jobs').exists())

    def test_character_waits_for_real_rigger_but_static_can_skip_it(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);studio=ModelStudio({'work_root':directory,'tokenrig_root':str(root/'SkinTokens')})
            save(root/'trellis-status.json',{'ready':True})
            image=root/'reference.png';image.write_bytes(b'x'*128)
            with self.assertRaises(RuntimeError):studio.create_model(image)
            with patch.object(studio,'launch',return_value={'id':'static'}) as launch:
                self.assertEqual(studio.create_model(image,auto_rig=False)['id'],'static')
                self.assertFalse(launch.call_args.kwargs['auto_rig'])

    def test_rig_replaces_static_library_entry_and_keeps_motion_compatibility(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);studio=ModelStudio({'work_root':directory})
            mesh=root/'textured.glb';mesh.touch();rig=root/'character.glb';rig.touch()
            fbx=root/'character.fbx';fbx.touch();fbx.with_suffix('.blend').touch()
            for ident,artifacts in [('001',{'textured':str(mesh)}),('002',{'textured':str(mesh),'character':str(rig),'fbx':str(fbx)})]:
                save(root/'jobs'/ident/'job.json',{'id':ident,'status':'complete','folder':str(root),
                    'artifacts':artifacts,'rig_validation':{'motion_compatible':False}})
            models=studio.library()
            self.assertEqual(len(models),1)
            self.assertEqual(models[0]['id'],'002')
            self.assertTrue(models[0]['rigged'])
            self.assertFalse(models[0]['motion_compatible'])

if __name__=='__main__': unittest.main()
