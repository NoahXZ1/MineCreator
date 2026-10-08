"""Offline tests for the hosted image tool inside a normal chat response."""

from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from uuid import uuid4

from scripts.chat import chat_reply
from scripts.designs import preview_file
from scripts.operations import OperationCancelled, OperationContext
from test_chat import FakeClient, completed_events
import test_designs as fixture

PNG_BASE64 = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jWZkAAAAASUVORK5CYII='


def image_events():
    response = NS(status='completed', model='offline', id='offline-image', usage=None,
                  output=[NS(type='image_generation_call', id='call-image', status='completed', result=PNG_BASE64)])
    return [NS(type='response.image_generation_call.in_progress'), NS(type='response.completed', response=response)]


class ChatPreviewTests(unittest.TestCase):
    # Reuse temporary roots and a real validated saved-plan fixture.
    setUp = fixture.DesignTests.setUp
    complete_turn = fixture.DesignTests.complete_turn
    generate = fixture.DesignTests.generate

    def chat(self, events, prompt='Can you generate a figure for this building in a Minecraft world?', context=None):
        client = FakeClient(events)
        request_id = uuid4().hex
        result = chat_reply(self.store, self.record['id'], request_id, prompt,
                            context or OperationContext('chat', lambda _:None),
                            client_factory=lambda **_:client, settings_reader=lambda:('fake','offline'))
        return result, client, request_id

    def test_preview_is_generated_in_one_chat_request_and_attached_to_plan(self):
        plan, _ = self.generate()
        before = Path(plan['plan_path']).read_bytes()
        result, client, request_id = self.chat(image_events())
        self.assertEqual(client.arguments['tool_choice'], 'auto')
        self.assertEqual(client.arguments['max_tool_calls'], 1)
        self.assertEqual(client.arguments['tools'][0]['type'], 'image_generation')
        self.assertEqual(client.arguments['tools'][0]['quality'], 'low')
        self.assertIn('final_voxels', client.arguments['input'][0]['content'])
        self.assertFalse(client.arguments['store'])
        preview = result['preview']
        self.assertEqual(preview['design_id'], plan['design_id'])
        self.assertEqual(preview['image_id'], request_id)
        record = self.store.get(self.record['id'])
        image = preview_file(record, plan['design_id'], 'concept', image_id=request_id)
        self.assertTrue(image.read_bytes().startswith(b'\x89PNG'))
        self.assertTrue(image.with_suffix('.json').is_file())
        self.assertEqual(record['turns'][-1]['preview'], preview)
        self.assertIn('not an in-game screenshot', record['turns'][-1]['assistant'])
        self.assertEqual(Path(plan['plan_path']).read_bytes(), before)

    def test_ordinary_discussion_does_not_save_or_start_an_image(self):
        plan, _ = self.generate()
        result, _, _ = self.chat(completed_events(), prompt='What wood is used for the floor?')
        self.assertIsNone(result['preview'])
        self.assertEqual(self.store.get(self.record['id'])['designs'][0].get('images', []), [])
        self.assertFalse((self.root / 'data' / 'previews').exists())
        self.assertTrue(Path(plan['plan_path']).exists())

    def test_chat_without_a_plan_does_not_offer_the_image_tool(self):
        result, client, _ = self.chat(completed_events('Generate a saved plan first.'))
        self.assertNotIn('tools', client.arguments)
        self.assertIsNone(result['preview'])

    def test_cancelled_generation_keeps_attempt_and_does_not_save_image(self):
        self.generate()
        context = OperationContext('chat', lambda _:None)
        def events():
            yield NS(type='response.image_generation_call.in_progress')
            context.cancel()
            yield image_events()[-1]
        with self.assertRaises(OperationCancelled):
            self.chat(events(), context=context)
        record = self.store.get(self.record['id'])
        self.assertEqual(record['turns'][-1]['status'], 'cancelled')
        self.assertEqual(record['designs'][0]['images'][-1]['status'], 'cancelled')
        self.assertFalse((self.root / 'data' / 'previews').exists())

    def test_failed_image_is_not_reported_as_success(self):
        self.generate()
        with self.assertRaises(ValueError):
            self.chat([NS(type='response.image_generation_call.in_progress'), NS(type='response.incomplete')])
        record = self.store.get(self.record['id'])
        self.assertEqual(record['designs'][0]['images'][-1]['status'], 'failed')
        self.assertIsNone(record['turns'][-1]['preview'])
        self.assertFalse((self.root / 'data' / 'previews').exists())

    def test_saved_chat_links_retain_their_own_image_version(self):
        plan, _ = self.generate()
        _, _, first_id = self.chat(image_events())
        _, _, second_id = self.chat(image_events(), prompt='Draw another preview angle.')
        record = self.store.get(self.record['id'])
        first = preview_file(record, plan['design_id'], 'concept', image_id=first_id)
        second = preview_file(record, plan['design_id'], 'concept', image_id=second_id)
        self.assertNotEqual(first, second)
        self.assertTrue(first.is_file())
        self.assertEqual(preview_file(record, plan['design_id'], 'concept'), second)
