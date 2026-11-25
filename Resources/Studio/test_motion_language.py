import unittest
from motion_language import kimodo_prompt

class MotionLanguageTests(unittest.TestCase):
    def test_short_portuguese_command_keeps_intended_action(self):
        self.assertEqual(kimodo_prompt('caminhas para frente'), 'A person walks forward.')
        self.assertEqual(kimodo_prompt('correr para frente'), 'A person runs forward.')
    def test_detailed_prompt_is_never_replaced_by_a_generic_action(self):
        text='caminhar para frente, parar, acenar e sentar'
        self.assertEqual(kimodo_prompt(text),text)
