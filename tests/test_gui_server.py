"""Local HTTP checks using an injected fake chat client, never the model API."""

from http.client import HTTPConnection
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
import unittest
from uuid import uuid4

from scripts.backend import MineCreatorBackend
from scripts.chat import ConversationStore, chat_reply
from scripts.run_gui import ChatApplication, make_server, window_should_close
from test_chat import FakeClient, completed_events


class OfflineBackend(MineCreatorBackend):
    def __init__(self, folder):
        super().__init__()
        self.conversations = ConversationStore(folder)
        self.request_count = 0

    def _execute(self, operation, context, **arguments):
        if operation != 'chat':
            raise AssertionError('The GUI must not expose game or image operations.')
        self.request_count += 1
        return chat_reply(self.conversations, arguments['conversation_id'], arguments['request_id'],
                          arguments['prompt'], context,
                          client_factory=lambda **_:FakeClient(completed_events()),
                          settings_reader=lambda:('fake-offline','offline-model'))


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.backend = OfflineBackend(Path(self.temp.name))
        self.app = ChatApplication(self.backend)
        self.server = make_server(self.app)
        self.worker = Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.worker.join()
        self.backend.close(timeout=2)
        self.temp.cleanup()

    def request(self, path, body=None, headers=None):
        connection = HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        try:
            connection.request('GET' if body is None else 'POST', path,
                               body=None if body is None else json.dumps(body),
                               headers=headers or {'X-MineCreator-Token':self.app.token, 'Content-Type':'application/json'})
            response = connection.getresponse()
            return response.status, response.read()
        finally:
            connection.close()

    def test_credentials_files_and_cross_origin_actions_are_not_exposed(self):
        self.assertEqual(self.request('/.env')[0], 404)
        self.assertEqual(self.request('/api/conversations', {}, {'Content-Type':'application/json'})[0], 403)
        headers = {'X-MineCreator-Token':self.app.token, 'Content-Type':'application/json', 'Origin':'https://example.com'}
        self.assertEqual(self.request('/api/conversations', {}, headers)[0], 403)
        self.assertEqual(self.backend.request_count, 0)

    def test_background_timer_suspension_does_not_close_the_server(self):
        self.request('/api/state')
        self.assertFalse(window_should_close(self.app, self.app.last_seen + 3600))
        self.assertEqual(self.backend.request_count, 0)

    def test_explicit_close_has_grace_period_and_reload_cancels_it(self):
        self.request('/api/state')
        self.assertEqual(self.request('/api/window-close', {})[0], 200)
        closed = self.app.window_closed_at
        self.assertFalse(window_should_close(self.app, closed + 9))
        self.assertTrue(window_should_close(self.app, closed + 11))
        self.request('/api/state')
        self.assertIsNone(self.app.window_closed_at)
        self.assertFalse(window_should_close(self.app, closed + 3600))

    def test_double_submission_is_one_request_and_events_can_be_replayed(self):
        record = self.backend.conversations.create()
        payload = dict(conversation_id=record['id'], request_id=uuid4().hex, prompt='Design a small cabin.')
        self.assertEqual(self.request('/api/chat', payload)[0], 202)
        self.app.jobs[payload['request_id']]['task'].result(timeout=2)
        self.assertEqual(self.request('/api/chat', payload)[0], 202)
        self.assertEqual(self.backend.request_count, 1)
        status, data = self.request('/api/task?id=' + payload['request_id'] + '&cursor=0')
        result = json.loads(data)
        self.assertEqual(status, 200)
        self.assertEqual(result['outcome']['status'], 'completed')
        self.assertTrue(any(event['kind'] == 'delta' for event in result['events']))
        self.assertEqual(self.backend.conversations.get(record['id'])['turns'][0]['status'], 'completed')
        again = json.loads(self.request('/api/task?id=' + payload['request_id'] + '&cursor=0')[1])
        self.assertEqual(again['events'], result['events'])


if __name__ == '__main__':
    unittest.main()
