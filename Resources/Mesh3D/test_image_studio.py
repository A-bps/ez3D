import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
spec = importlib.util.spec_from_file_location('studio', Path(__file__).with_name('image_studio.py'))
studio = importlib.util.module_from_spec(spec); spec.loader.exec_module(studio)

class StudioContracts(unittest.TestCase):
    def test_malformed_model_json_is_retried_with_choices_preserved(self):
        from unittest.mock import Mock
        bad=Mock();bad.json.return_value={'choices':[{'message':{'content':'{"action":"create", invalid'},'finish_reason':'stop'}]}
        good=Mock();good.json.return_value={'choices':[{'message':{'content':'{"action":"chat","message":"ok"}'},'finish_reason':'stop'}]}
        with patch.object(studio.requests,'post',side_effect=[bad,good]) as post:
            result=studio.ask({'llm_url':'http://127.0.0.1:1234','model':'test'},'system','Minha escolha: couro')
        self.assertEqual(result['message'],'ok')
        self.assertEqual(post.call_count,2)
        self.assertIn('Minha escolha: couro',str(post.call_args.kwargs['json']['messages']))
        self.assertEqual(post.call_args.kwargs['json']['response_format']['type'],'json_schema')

    def test_custom_answer_reaches_model_as_user_choice(self):
        with tempfile.TemporaryDirectory() as root:
            with patch.object(studio,'ask',return_value=self.questions()):r=studio.execute(self.request(root,'Crie'))
            payload={'token':r['questions']['token'],'answers':{'background':'Estúdio'},'custom_answers':{'style':'Pintura em aquarela'}}
            with patch.object(studio,'ask',return_value={'action':'chat','message':'ok'}) as ask:
                studio.execute(self.request(root,'__ez3d_answers__:'+json.dumps(payload)))
            self.assertIn('Pintura em aquarela',ask.call_args.args[2])

    def request(self, root, text, reference=None):
        p=Path(root)/'request.json';studio.save(p,dict(root=root,config={},text=text,reference=reference));return p
    def questions(self):
        return {'action':'ask','questions':[{'id':'style','question':'Qual acabamento combina com seu personagem?', 'options':['Estilizado','Realista']}, {'id':'background','question':'Onde ele deve aparecer?', 'options':['Estúdio','Cenário']}]}
    def proposal(self):
        return {'action':'create','name':'Personagem','new_flow':True,'assets':[{'name':'Frente','prompt':'Transform into stylized character','requirements':['style','background']}]}
    def qa(self, valid=True):
        return {'verdict':'accepted','checks':[{'met':valid,'evidence':'Surface is modeled and stylized'},{'met':True,'evidence':'Plain gray studio background'}], 'reason':'Visual checks passed.'}
    def test_json_object_with_trailing_explanation_is_read_without_executing_prose(self):
        self.assertEqual(studio.json_answer('{"action":"chat","message":"ok"}\nConcluído')["message"],"ok")
    def test_invalid_urls_never_launch_process_or_network(self):
        for url in ['http:', 'http:/v1', '', 'localhost:1234']:
            with patch.object(studio.requests,'get') as get, patch.object(studio.subprocess,'run') as run:
                with self.assertRaises(ValueError):studio.ask({'llm_url':url,'model':'ez3d-image-planner'},'test','test')
                get.assert_not_called();run.assert_not_called()
        self.assertEqual(studio.planner_base_url('http://127.0.0.1:1234/v1/'),'http://127.0.0.1:1234')
    def test_model_questions_are_preserved_and_do_not_generate(self):
        with tempfile.TemporaryDirectory() as root:
            with patch.object(studio,'ask',return_value=self.questions()),patch.object(studio,'generate') as generate:
                result=studio.execute(self.request(root,'Crie um personagem','reference.png'))
            generate.assert_not_called();self.assertEqual(result['questions']['questions'],self.questions()['questions'])
            self.assertEqual(result['message'],'')
    def test_stale_and_partial_answers_rejected_before_model(self):
        with tempfile.TemporaryDirectory() as root:
            with patch.object(studio,'ask',return_value=self.questions()):r=studio.execute(self.request(root,'Crie'))
            for token,answers in [('stale',{}),(r['questions']['token'],{'style':'Estilizado'})]:
                with patch.object(studio,'ask') as ask:
                    with self.assertRaises(ValueError):studio.execute(self.request(root,'__ez3d_answers__:'+json.dumps(dict(token=token,answers=answers))))
                    ask.assert_not_called()
    def test_complete_answers_create_without_approval_and_keep_reference(self):
        with tempfile.TemporaryDirectory() as root:
            with patch.object(studio,'ask',return_value=self.questions()):r=studio.execute(self.request(root,'Crie um personagem','original.png'))
            payload={'token':r['questions']['token'],'answers':{'style':'Estilizado','background':'Estúdio'}}
            def render(config,prompt,destination,request_file,reference):
                self.assertEqual(reference,'original.png');destination.write_bytes(b'preserved');return {'seed':1}
            with patch.object(studio,'ask',side_effect=[self.proposal(),self.qa()]),patch.object(studio,'generate',side_effect=render) as gen:
                result=studio.execute(self.request(root,'__ez3d_answers__:'+json.dumps(payload)))
            self.assertEqual(gen.call_count,1);self.assertEqual(len(result['images']),1)
            self.assertEqual(result['message'],'');self.assertNotIn('caption',result['images'][0])
    def test_claimed_acceptance_without_checks_never_passes(self):
        self.assertEqual(studio.reviewed_verdict({'verdict':'accepted','reason':'Looks correct'}),'inconclusive')
        self.assertEqual(studio.reviewed_verdict(self.qa(False)),'rejected')
    def test_rejections_stay_internal_and_stop_after_two(self):
        with tempfile.TemporaryDirectory() as root:
            def render(config,prompt,destination,request_file,reference):destination.write_bytes(b'preserved');return {'seed':1}
            with patch.object(studio,'ask',side_effect=[self.proposal(),self.qa(False),self.qa(False)]),patch.object(studio,'generate',side_effect=render) as gen:
                result=studio.execute(self.request(root,'Crie sem perguntar'))
            self.assertEqual(gen.call_count,2);self.assertEqual(result['images'],[])
            self.assertEqual(len(list(Path(root).rglob('*.png'))),2)
    def test_new_subject_gets_new_named_folder(self):
        with tempfile.TemporaryDirectory() as root:
            studio.save(Path(root)/'active.json',{'flow':'old-explorer','messages':[]})
            def render(config,prompt,destination,request_file,reference):destination.write_bytes(b'x');return {'seed':1}
            with patch.object(studio,'ask',side_effect=[self.proposal(),self.qa()]),patch.object(studio,'generate',side_effect=render):r=studio.execute(self.request(root,'Novo personagem'))
            self.assertNotIn('old-explorer',r['folder'])
    def test_turbo_sampler_uses_trained_sigmas_and_reference_latent(self):
        g=studio.workflow({'diffusion_model':'turbo','text_encoder':'te','vae':'vae','turbo':True},'test',1,'ref.png')
        self.assertEqual(g['7']['class_type'],'SamplerCustomAdvanced')
        self.assertEqual(g['7']['inputs']['latent_image'],['4',2])
        self.assertEqual(g['14']['inputs']['nodes'],'1.0, 0.9375, 0.875, 0.75, 0.5, 0.25')
    def test_klein_9b_workflow_uses_native_four_step_scheduler_and_reference_conditioning(self):
        g=studio.workflow({'backend':'flux2_klein','diffusion_model':'klein9b','text_encoder':'te','vae':'vae'},'transform',1,'ref.png')
        self.assertEqual(g['14']['class_type'],'Flux2Scheduler')
        self.assertEqual(g['14']['inputs']['steps'],4)
        self.assertEqual(g['2']['inputs']['type'],'flux2')
        self.assertEqual(g['12']['inputs']['positive'],['17',0])
        self.assertEqual(g['17']['class_type'],'ReferenceLatent')
    def test_questions_require_unique_ids_and_distinct_options(self):
        for questions in [[],[{'id':'x','question':'a','options':['a','a']}]]:
            with self.assertRaises(ValueError):studio.validated_questions({'questions':questions})

if __name__=='__main__':unittest.main()
