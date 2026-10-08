"""Offline checks only: fake model events, temporary conversation files."""

from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
import unittest
from uuid import uuid4

from scripts.chat import ConversationStore, chat_reply
from scripts.operations import OperationCancelled, OperationContext


class FakeClient:
    def __init__(self, events):
        self.events = events
        self.responses = self
        self.arguments = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def create(self, **arguments):
        self.arguments = arguments
        return self

    def __iter__(self):
        return iter(self.events)


def completed_events(text='Use oak for the floor.'):
    return [NS(type='response.output_text.delta', delta=text),
            NS(type='response.completed', response=NS(model='offline-fake', id='response-test', usage=None))]


class ChatTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = ConversationStore(Path(self.temp.name))
        self.record = self.store.create()
        self.context = OperationContext('chat', lambda _: None)

    def send(self, prompt, events=None):
        client = FakeClient(completed_events() if events is None else events)
        result = chat_reply(self.store, self.record['id'], uuid4().hex, prompt, self.context,
                            client_factory=lambda **_:client, settings_reader=lambda:('fake-offline-key','offline-fake'))
        return result, client

    def test_multiturn_survives_reopening_and_never_exposes_tools(self):
        self.send('Design a tiny oak cabin.')
        self.store = ConversationStore(Path(self.temp.name))
        result, client = self.send('Make the roof darker.')
        self.assertEqual(client.arguments['input'], [
            dict(role='user', content='Design a tiny oak cabin.'),
            dict(role='assistant', content='Use oak for the floor.'),
            dict(role='user', content='Make the roof darker.')])
        self.assertFalse(client.arguments['store'])
        self.assertTrue(client.arguments['stream'])
        self.assertNotIn('tools', client.arguments)
        self.assertEqual(len(result['conversation']['turns']), 2)

    def test_incomplete_reply_keeps_partial_text_out_of_future_context(self):
        with self.assertRaises(ValueError):
            self.send('First request', [NS(type='response.output_text.delta', delta='Partial idea'), NS(type='response.incomplete')])
        record = self.store.get(self.record['id'])
        self.assertEqual(record['turns'][0]['status'], 'failed')
        self.assertEqual(record['turns'][0]['assistant'], 'Partial idea')
        _, client = self.send('Try a different idea.')
        self.assertEqual(client.arguments['input'], [dict(role='user', content='Try a different idea.')])

    def test_stop_is_persisted_and_does_not_commit_complete_reply(self):
        def events():
            yield NS(type='response.output_text.delta', delta='Partial')
            self.context.cancel()
            yield NS(type='response.completed', response=NS(model='fake', id='fake', usage=None))
        client = FakeClient(events())
        with self.assertRaises(OperationCancelled):
            chat_reply(self.store, self.record['id'], uuid4().hex, 'A cabin', self.context,
                       client_factory=lambda **_:client, settings_reader=lambda:('fake','fake'))
        turn = self.store.get(self.record['id'])['turns'][0]
        self.assertEqual(turn['status'], 'cancelled')
        self.assertEqual(turn['assistant'], 'Partial')

    def test_interrupted_app_does_not_resume_requests(self):
        self.store.begin(self.record['id'], uuid4().hex, 'A cabin')
        self.store.recover()
        self.assertEqual(self.store.get(self.record['id'])['turns'][0]['status'], 'interrupted')

    def test_duplicate_id_and_path_traversal_rejected(self):
        request_id = uuid4().hex
        self.store.begin(self.record['id'], request_id, 'A cabin')
        self.store.finish(self.record['id'], request_id, 'An idea', 'completed')
        with self.assertRaises(ValueError):
            self.store.begin(self.record['id'], request_id, 'A cabin')
        with self.assertRaises(ValueError):
            self.store.get('../.env')


if __name__ == '__main__':
    unittest.main()
