import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch
import image_studio as studio
from run_comfy_mesh3d import geometry_workflow


class MultiviewTests(unittest.TestCase):
    def test_atomic_save_tolerates_a_windows_reader(self):
        with tempfile.TemporaryDirectory() as root:
            destination=Path(root)/'result.json'
            replace=Path.replace
            failures=[True,True,False]
            def retry(path,target):
                if failures.pop(0):raise PermissionError('reader temporarily holds file')
                return replace(path,target)
            with patch.object(Path,'replace',retry),patch.object(studio.time,'sleep'):
                studio.save(destination,{'complete':True})
            self.assertTrue(studio.read(destination)['complete'])
            self.assertEqual(list(Path(root).glob('*.tmp')),[])
    def test_named_views_are_normalized_and_sorted(self):
        proposal={'assets':[{'name':name,'prompt':'A single character view'} for name in
            ['npc-costas','npc_perfil_esquerdo','npc-frente','npc perfil direito']]}
        result=studio.valid_proposal(proposal)
        self.assertEqual([a['view'] for a in result['assets']],['front','back','left','right'])
    def test_geometry_consumes_every_view(self):
        graph=geometry_workflow('front.png','npc',512,8,['back.png','left.png','right.png'])
        self.assertEqual(graph['5']['inputs']['mode'],'multi')
        loads=[n['inputs']['image'] for n in graph.values() if n['class_type']=='LoadImage']
        self.assertEqual(loads,['front.png','back.png','left.png','right.png'])
        self.assertEqual(sum(n['class_type']=='ImageBatch' for n in graph.values()),3)
        self.assertEqual(graph['5']['inputs']['images'],['18',0])

    def test_other_views_use_approved_front_as_reference(self):
        proposal={'action':'create','name':'NPC','new_flow':True,'assets':[
            {'name':v,'view':v,'prompt':'Same NPC '+v,'requirements':['pose','style']}
            for v in ('front','back','left','right')]}
        qa={'verdict':'accepted','checks':[{'met':True,'evidence':'correct pose'}, {'met':True,'evidence':'correct style'}]}
        refs=[]
        def generate(config,prompt,destination,request_file,reference):
            refs.append(reference);destination.write_bytes(b'image');return {'seed':1}
        with tempfile.TemporaryDirectory() as root:
            request=Path(root)/'request.json';studio.save(request,{'root':root,'text':'quatro vistas','config':{}})
            with patch.object(studio,'ask',side_effect=[proposal]+[qa]*4),patch.object(studio,'generate',side_effect=generate):
                result=studio.execute(request)
            self.assertTrue(result['complete'])
            self.assertIsNone(refs[0])
            self.assertEqual(refs[1:],[result['images'][0]['path']]*3)


if __name__=='__main__':unittest.main()
