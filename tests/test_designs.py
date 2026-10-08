"""Offline generation, revision, persistence, and preview checks."""

from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch
from uuid import uuid4

from scripts import designs, plan_with_openai, preview_with_openai
from scripts.backend import MineCreatorBackend
from scripts.blueprint import Blueprint, expand_blueprint
from scripts.chat import ConversationStore, chat_reply
from scripts.operations import OperationCancelled, OperationContext
from scripts.run_gui import ChatApplication
from test_chat import FakeClient, completed_events


def sample_result(material='minecraft:oak_planks'):
    blueprint = Blueprint.model_validate(dict(title='Small pavilion', description='An open oak pavilion with a gable roof.',
        bounds=dict(x=5,y=7,z=5), stages=[
            dict(name='Floor',description='Lay the floor.', components=[dict(kind='solid_box',origin=dict(x=0,y=0,z=0),size=dict(x=5,y=1,z=5),block='minecraft:oak_planks')]),
            dict(name='Pillars',description='Raise four corner pillars.', components=[dict(kind='solid_box',origin=dict(x=x,y=1,z=z),size=dict(x=1,y=3,z=1),block='minecraft:oak_log[axis=y]') for x,z in ((0,0),(0,4),(4,0),(4,4))]),
            dict(name='Roof',description='Place the gable roof.', components=[dict(kind='gable_roof',origin=dict(x=0,y=4,z=0),size=dict(x=5,y=3,z=5),block=material)])]))
    stages = expand_blueprint(blueprint)
    return dict(blueprint=blueprint.model_dump(), expanded_stages=stages,
                placement_count=sum(len(stage['blocks']) for stage in stages), model='offline-model', response_id='offline', usage=None)


class DesignTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for module in (designs, plan_with_openai, preview_with_openai):
            patcher = patch.object(module, 'ROOT', self.root)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.store = ConversationStore(self.root / 'data' / 'conversations')
        self.record = self.store.create()
        self.context = OperationContext('design_plan', lambda _:None)
        self.complete_turn('Design a 5-by-5 pavilion.', 'Use oak pillars and an oak roof.')

    def complete_turn(self, user, assistant):
        request_id = uuid4().hex
        self.store.begin(self.record['id'], request_id, user)
        self.store.finish(self.record['id'], request_id, assistant, 'completed')

    def generate(self, material='minecraft:oak_planks'):
        with patch.object(designs, 'generate_plan', return_value=sample_result(material)) as generate:
            result = designs.generate_design(self.store, self.record['id'], uuid4().hex, self.context)
        return result, generate.call_args

    def test_conversation_and_baseline_are_carried_into_revisions(self):
        result, arguments = self.generate()
        self.assertEqual(arguments.kwargs['history'][0]['content'], 'Design a 5-by-5 pavilion.')
        record = self.store.get(self.record['id'])
        summary = designs.plan_summary(record)
        self.assertEqual(summary['final_blocks'], 62)
        self.assertEqual(summary['bounds'], dict(x=5,y=7,z=5))
        self.assertFalse(summary['needs_regeneration'])
        self.assertTrue(designs.preview_file(record, result['design_id'], 'blocks').is_file())
        self.complete_turn('Change only the roof to white wool.', 'Keep the floor and pillars unchanged.')
        self.assertTrue(designs.plan_summary(self.store.get(self.record['id']))['needs_regeneration'])
        updated, arguments = self.generate('minecraft:white_wool')
        self.assertIn('Saved blueprint baseline', arguments.kwargs['history'][0]['content'])
        self.assertIn('Change only the roof to white wool.', [item['content'] for item in arguments.kwargs['history']])
        record = ConversationStore(self.store.folder).get(self.record['id'])
        summary = designs.plan_summary(record)
        self.assertEqual(summary['version'], 2)
        self.assertEqual(summary['id'], updated['design_id'])
        self.assertIn(dict(block='minecraft:white_wool', count=25), summary['materials'])
        self.assertTrue(Path(result['plan_path']).exists())

    def test_failure_retains_previous_plan_and_does_not_request_images(self):
        result, _ = self.generate()
        with patch.object(designs, 'generate_plan', side_effect=ValueError('Offline failure')), patch.object(designs, 'generate_preview') as image:
            with self.assertRaises(ValueError):
                designs.generate_design(self.store, self.record['id'], uuid4().hex, self.context)
            image.assert_not_called()
        self.assertEqual(designs.plan_summary(self.store.get(self.record['id']))['id'], result['design_id'])
        self.assertEqual(self.store.get(self.record['id'])['designs'][-1]['status'], 'failed')

    def test_stopped_and_interrupted_generation_do_not_replace_previous_plan(self):
        result, _ = self.generate()
        with patch.object(designs, 'generate_plan', side_effect=OperationCancelled('Stopped')):
            with self.assertRaises(OperationCancelled):
                designs.generate_design(self.store, self.record['id'], uuid4().hex, self.context)
        pending = uuid4().hex
        self.store.begin_design(self.record['id'], pending)
        self.store.recover()
        record = self.store.get(self.record['id'])
        self.assertEqual(record['designs'][-1]['status'], 'interrupted')
        self.assertEqual(designs.plan_summary(record)['id'], result['design_id'])

    def test_renderer_failure_preserves_saved_blueprint(self):
        with patch.object(designs, 'generate_plan', return_value=sample_result()), patch.object(designs, 'preview', side_effect=OSError('Offline render failure')):
            designs.generate_design(self.store, self.record['id'], uuid4().hex, self.context)
        summary = designs.plan_summary(self.store.get(self.record['id']))
        self.assertEqual(summary['final_blocks'], 62)
        self.assertFalse(summary['has_block_preview'])
        self.assertTrue(summary['preview_error'])

    def test_api_image_is_explicit_and_bound_to_selected_plan(self):
        result, _ = self.generate()
        png = designs.preview_file(self.store.get(self.record['id']), result['design_id'], 'blocks')
        with patch.object(designs, 'generate_preview', return_value=png) as image:
            designs.generate_design_image(self.store, self.record['id'], result['design_id'], uuid4().hex, self.context)
        self.assertEqual(image.call_count, 1)
        self.assertEqual(image.call_args.args[0], Path(result['plan_path']))
        self.assertEqual(image.call_args.args[1], 'low')
        self.assertTrue(designs.plan_summary(self.store.get(self.record['id']))['image_id'])
        self.assertEqual(designs.preview_file(self.store.get(self.record['id']), result['design_id'], 'concept'), png)
        self.assertRaises(ValueError, designs.preview_file, self.store.get(self.record['id']), uuid4().hex, 'blocks')

    def test_chat_receives_actual_saved_geometry(self):
        self.generate('minecraft:white_wool')
        client = FakeClient(completed_events())
        chat_reply(self.store, self.record['id'], uuid4().hex, 'What is the roof made of?',
                   OperationContext('chat', lambda _:None), client_factory=lambda **_:client, settings_reader=lambda:('fake','fake'))
        self.assertIn('minecraft:white_wool', client.arguments['input'][0]['content'])

    def test_missing_plan_does_not_block_chat_or_regeneration(self):
        result, _ = self.generate()
        Path(result['plan_path']).unlink()
        client = FakeClient(completed_events())
        chat_reply(self.store, self.record['id'], uuid4().hex, 'Generate a fresh version.',
                   OperationContext('chat', lambda _:None), client_factory=lambda **_:client, settings_reader=lambda:('fake','fake'))
        self.assertEqual(client.arguments['input'][-1]['content'], 'Generate a fresh version.')
        updated, _ = self.generate()
        self.assertEqual(designs.plan_summary(self.store.get(self.record['id']))['id'], updated['design_id'])

    def test_structured_sdk_call_receives_full_messages_without_live_request(self):
        blueprint = Blueprint.model_validate(sample_result()['blueprint'])
        response = NS(status='completed', output=[], output_parsed=blueprint, model='offline', id='offline', usage=None)
        client = FakeClient([])
        captured = {}
        def parse(**arguments):
            captured.update(arguments)
            return response
        client.parse = parse
        history = [dict(role='user', content='A pavilion'), dict(role='assistant', content='Five by five')]
        with patch.object(plan_with_openai, 'OpenAI', return_value=client), patch.object(plan_with_openai, 'read_settings', return_value=('fake', 'offline')):
            plan_with_openai.generate_plan('Generate the discussed plan.', context=self.context, history=history)
        self.assertEqual(captured['input'][:2], history)
        self.assertFalse(captured['store'])
        self.assertEqual(captured['text_format'], Blueprint)

    def test_gui_worker_generates_once_and_retains_association(self):
        backend = MineCreatorBackend()
        backend.conversations = self.store
        app = ChatApplication(backend)
        payload = dict(conversation_id=self.record['id'], request_id=uuid4().hex)
        with patch.object(designs, 'generate_plan', return_value=sample_result()) as generate:
            app.generate_plan(payload)
            outcome = app.jobs[payload['request_id']]['task'].result(timeout=3)
            app.generate_plan(payload)
        self.assertEqual(outcome.status, 'completed')
        self.assertEqual(generate.call_count, 1)
        self.assertEqual(app.conversation(self.record['id'])['design']['id'], payload['request_id'])
        events = app.poll(payload['request_id'], 0)['events']
        self.assertTrue(any(event['data'].get('phase') == 'rendering' for event in events))


if __name__ == '__main__':
    unittest.main()
