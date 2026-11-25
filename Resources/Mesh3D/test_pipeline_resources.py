import unittest
from unittest.mock import Mock, patch
from pipeline_resources import release_comfy


def response(data):
    result = Mock()
    result.json.return_value = data
    return result


class HandoffTests(unittest.TestCase):
    @patch('pipeline_resources.requests.post')
    @patch('pipeline_resources.requests.get')
    def test_busy_server_is_not_unloaded(self, get, post):
        get.return_value = response({'queue_running': [[0, 'other-job']], 'queue_pending': []})
        with self.assertRaises(RuntimeError):
            release_comfy('http://localhost:8188')
        post.assert_not_called()

    @patch('pipeline_resources.time.sleep')
    @patch('pipeline_resources.requests.post')
    @patch('pipeline_resources.requests.get')
    def test_acknowledgement_waits_for_actual_release(self, get, post, sleep):
        get.side_effect = [response({'queue_running': [], 'queue_pending': []}),
                           response({'devices': [{'type': 'cuda', 'torch_vram_total': 4_000_000_000}]}),
                           response({'devices': [{'type': 'cuda', 'torch_vram_total': 0}]})]
        self.assertTrue(release_comfy('http://localhost:8188')['released'])
        sleep.assert_called_once()

    @patch('pipeline_resources.requests.post')
    @patch('pipeline_resources.requests.get')
    def test_missing_counter_blocks_next_stage(self, get, post):
        get.side_effect = [response({'queue_running': [], 'queue_pending': []}),
                           response({'devices': [{'type': 'cuda'}]})]
        with self.assertRaises(TimeoutError):
            release_comfy('http://localhost:8188', timeout=0)

    @patch('pipeline_resources.requests.post')
    @patch('pipeline_resources.requests.get')
    def test_failed_unload_is_not_success(self, get, post):
        get.return_value = response({'queue_running': [], 'queue_pending': []})
        post.return_value.raise_for_status.side_effect = RuntimeError('HTTP failure')
        with self.assertRaises(RuntimeError):
            release_comfy('http://localhost:8188')

    @patch('pipeline_resources.requests.post')
    @patch('pipeline_resources.requests.get')
    def test_unknown_queue_cannot_be_treated_as_idle(self, get, post):
        get.return_value = response({})
        with self.assertRaises(RuntimeError):
            release_comfy('http://localhost:8188')
        post.assert_not_called()


if __name__ == '__main__':
    unittest.main()
