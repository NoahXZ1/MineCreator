"""Offline Build integration: fake model/game, real placement and readback runner."""

from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch, Mock
from uuid import uuid4

from scripts import build_from_plan
from scripts.backend import MineCreatorBackend
from scripts.builds import build_design, review_design
from scripts import designs
from scripts.chat import chat_reply
from scripts.operations import OperationContext, OperationCancelled
from scripts.run_gui import ChatApplication
from test_chat import FakeClient, completed_events
import test_designs as fixture


class Game:
    def __init__(self):
        self.blocks = {}
        self.writes = []
        self.after_write = lambda: None

    def block(self, x, y, z):
        value = self.blocks.get((x, y, z), 'minecraft:dirt' if y < 64 else 'minecraft:air')
        name, _, states = value.partition('[')
        return dict(x=x, y=y, z=z, id=name,
                    properties=dict(pair.split('=') for pair in states.rstrip(']').split(',')) if states else {})

    def call(self, method, params=None):
        if method == 'info.status':
            return dict(integratedServer=True, capabilities=['world_write'])
        if method == 'player.getState':
            return dict(x=0, y=64, z=0, dimension='minecraft:overworld')
        if method == 'world.getBlocks':
            a, b = params['from'], params['to']
            return dict(blocks=[self.block(x,y,z) for x in range(a['x'],b['x']+1)
                               for y in range(a['y'],b['y']+1) for z in range(a['z'],b['z']+1)])
        if method == 'world.getBlock':
            return self.block(params['x'], params['y'], params['z'])
        if method == 'world.setBlock':
            position = tuple(params[k] for k in ('x','y','z'))
            self.blocks[position] = params['blockId']
            self.writes.append(position)
            self.after_write()
            return {}
        raise AssertionError(method)


def build_events(arguments='{}', count=1, review_id=None):
    if review_id:
        import json
        arguments = json.dumps(dict(review_id=review_id))
    response = NS(status='completed', model='offline', id='offline-build', usage=None,
                  output=[NS(type='function_call', name='start_build', arguments=arguments) for _ in range(count)])
    return [NS(type='response.completed', response=response)]


def review_events(revise=False):
    events = build_events('{"revise_plan":' + ('true' if revise else 'false') + '}')
    events[0].response.output[0].name = 'review_build'
    return events


class BuildTests(unittest.TestCase):
    setUp = fixture.DesignTests.setUp
    complete_turn = fixture.DesignTests.complete_turn
    generate = fixture.DesignTests.generate

    def setup_build(self):
        self.plan, _ = self.generate()
        self.game = Game()
        self.events = []
        self.ctx = OperationContext('design_build', self.events.append)
        self.ctx.pause = lambda _: self.ctx.check()
        patcher = patch.object(build_from_plan, 'ROOT', self.root)
        patcher.start()
        self.addCleanup(patcher.stop)

    def build(self, request_id=None):
        return build_design(self.store, self.record['id'], self.plan['design_id'], request_id or uuid4().hex,
                            self.game, self.ctx)

    def test_real_runner_places_and_reads_back_then_persists_result(self):
        self.setup_build()
        result = self.build()
        self.assertEqual(result['verified'], 62)
        self.assertEqual(len(self.game.writes), 62)
        self.assertEqual(self.store.get(self.record['id'])['builds'][-1]['status'], 'completed')
        self.assertTrue(any(event.data.get('phase') == 'stage_verified' for event in self.events))
        self.assertTrue(all(event.data.get('scope') == 'build' for event in self.events))

    def test_progress_disk_failure_stops_writes_and_keeps_parseable_log(self):
        import json
        from pathlib import Path
        self.setup_build();original=Path.replace
        def fail_log(path,target):
            if path.parent==self.root/'data'/'builds' and self.game.writes:raise OSError('Disk unavailable')
            return original(path,target)
        with patch.object(Path,'replace',fail_log),self.assertRaises(OSError):self.build()
        self.assertEqual(len(self.game.writes),1)
        logs=list((self.root/'data'/'builds').glob('*.json'));self.assertEqual(len(logs),1)
        self.assertNotEqual(json.loads(logs[0].read_text(encoding='utf-8'))['phase'],'completed')
        self.assertFalse(list((self.root/'data'/'builds').glob('*.tmp')))

    def test_stop_preserves_blocks_and_never_restarts_request(self):
        self.setup_build()
        request_id = uuid4().hex
        self.game.after_write = self.ctx.cancel
        with self.assertRaises(OperationCancelled):
            self.build(request_id)
        self.assertEqual(len(self.game.writes), 1)
        self.assertEqual(self.store.get(self.record['id'])['builds'][-1]['status'], 'cancelled')
        self.ctx = OperationContext('design_build', lambda _:None)
        with self.assertRaises(ValueError):
            self.build(request_id)
        self.assertEqual(len(self.game.writes), 1)

    def test_failed_readback_is_not_reported_as_completed(self):
        self.setup_build()
        self.game.after_write = self.game.blocks.clear
        with self.assertRaisesRegex(RuntimeError, 'readback mismatch'):
            self.build()
        self.assertEqual(self.store.get(self.record['id'])['builds'][-1]['status'], 'failed')

    def test_foundation_grass_growth_does_not_abort_later_stage_checks(self):
        self.setup_build()
        position = (7,63,-2)
        self.game.blocks[position] = 'minecraft:air'
        preparation = [dict(name='Level foundation', terrain=True,
                            blocks=[dict(x=7,y=63,z=-2,block='minecraft:dirt',before_id='minecraft:air')])]
        def grow_grass():
            if self.game.blocks.get(position) == 'minecraft:dirt':
                self.game.blocks[position] = 'minecraft:grass_block'
        self.game.after_write = grow_grass
        with patch.object(build_from_plan, 'choose_site', return_value=((7,64,-2), preparation, 'prepared terrain')):
            result = self.build()
        self.assertEqual(result['verified'], 63)
        self.assertEqual(self.game.blocks[position], 'minecraft:grass_block')
        self.assertEqual(self.store.get(self.record['id'])['builds'][-1]['status'], 'completed')

    def test_terrain_exception_does_not_allow_wrong_support_or_blueprint_materials(self):
        match = build_from_plan.placement_matches
        self.assertFalse(match(dict(id='minecraft:grass_block'), 'minecraft:dirt'))
        self.assertFalse(match(dict(id='minecraft:air'), 'minecraft:dirt', terrain=True))
        self.assertFalse(match(dict(id='minecraft:stone'), 'minecraft:dirt', terrain=True))
        self.assertFalse(match(dict(id='minecraft:grass_block'), 'minecraft:oak_planks', terrain=True))

    def test_wrong_conversation_or_outdated_design_never_writes(self):
        self.setup_build()
        with self.assertRaises(ValueError):
            build_design(self.store, self.store.create()['id'], self.plan['design_id'], uuid4().hex, self.game, self.ctx)
        self.generate('minecraft:white_wool')
        with self.assertRaisesRegex(ValueError, 'plan has changed'):
            self.build()
        self.assertEqual(self.game.writes, [])

    def test_build_button_duplicate_job_uses_one_runner_without_openai(self):
        self.setup_build()
        backend = MineCreatorBackend()
        backend.conversations = self.store
        self.addCleanup(backend.close)
        app = ChatApplication(backend)
        payload = dict(conversation_id=self.record['id'], design_id=self.plan['design_id'],
                       request_id=uuid4().hex, confirmed=True)
        with patch.object(backend, '_client', return_value=self.game), patch.object(OperationContext, 'pause', lambda ctx, _:ctx.check()):
            app.build(payload)
            outcome = app.jobs[payload['request_id']]['task'].result(timeout=5)
            app.build(payload)
        self.assertEqual(outcome.status, 'completed')
        self.assertEqual(len(self.game.writes), 62)
        self.assertEqual(len(self.store.get(self.record['id'])['builds']), 1)

    def chat(self, events, handler, prompt='Start build'):
        client = FakeClient(events)
        request_id = uuid4().hex
        result = chat_reply(self.store, self.record['id'], request_id, prompt, self.ctx,
                            client_factory=lambda **_:client, settings_reader=lambda:('fake','offline'),
                            build_handler=handler,
                            review_handler=lambda revise:review_design(self.store, self.record['id'], request_id, revise, self.ctx))
        return result, client

    def test_natural_language_tool_runs_same_pipeline_and_reports_verified_result(self):
        self.setup_build()
        handler = Mock(side_effect=lambda design_id:self.build())
        review, first_client = self.chat(review_events(), handler)
        handler.assert_not_called()
        self.assertNotIn('start_build', [tool.get('name') for tool in first_client.arguments['tools']])
        self.assertIn('Is this description accurate', review['conversation']['turns'][-1]['assistant'])
        result, client = self.chat(build_events(review_id=review['review']['id']), handler, prompt='Yes, start building.')
        handler.assert_called_once_with(self.plan['design_id'])
        self.assertEqual(result['build']['verified'], 62)
        self.assertIn('62 blocks verified', result['conversation']['turns'][-1]['assistant'])
        self.assertFalse(client.arguments['parallel_tool_calls'])
        self.assertEqual(client.arguments['tools'][-1]['name'], 'start_build')
        self.assertNotIn('build_review', self.store.get(self.record['id']))

    def test_plain_discussion_and_negation_do_not_dispatch_without_tool_call(self):
        self.setup_build()
        handler = Mock()
        for prompt in ('How does start build work?', 'Do not build yet.'):
            self.chat(completed_events('No construction started.'), handler, prompt)
        handler.assert_not_called()
        self.assertEqual(self.game.writes, [])

    def test_invalid_or_multiple_tool_calls_never_dispatch(self):
        self.setup_build()
        handler = Mock()
        for events in (build_events('{"path":"arbitrary.json"}'), build_events(count=2)):
            with self.assertRaises(ValueError):
                self.chat(events, handler)
        handler.assert_not_called()

    def test_unavailable_game_failure_is_saved_without_success(self):
        self.setup_build()
        review, _ = self.chat(review_events(), Mock())
        self.game.call = Mock(side_effect=OSError('Offline disconnected'))
        with self.assertRaises(OSError):
            self.chat(build_events(review_id=review['review']['id']), lambda _:self.build(), prompt='Yes')
        record = self.store.get(self.record['id'])
        self.assertEqual(record['builds'][-1]['status'], 'failed')
        self.assertEqual(record['turns'][-1]['status'], 'failed')
        self.assertNotIn('Construction completed', record['turns'][-1]['assistant'])

    def test_request_position_and_current_position_are_both_protected(self):
        self.setup_build()
        # The chosen site is near x=9. Walk into it after the first placement.
        original_call = self.game.call
        def call(method, params=None):
            if method == 'player.getState' and self.game.writes:
                return dict(x=9,y=64,z=0,dimension='minecraft:overworld')
            return original_call(method, params)
        self.game.call = call
        with self.assertRaises(RuntimeError):
            self.build()
        self.assertEqual(len(self.game.writes), 1)

    def test_recovery_marks_build_interrupted_without_replay(self):
        self.setup_build()
        self.store.begin_build(self.record['id'], self.plan['design_id'], uuid4().hex)
        self.store.recover()
        self.assertEqual(self.store.get(self.record['id'])['builds'][-1]['status'], 'interrupted')
        self.assertEqual(self.game.writes, [])

    def test_first_build_cannot_skip_review_even_if_model_requests_it(self):
        self.setup_build()
        handler = Mock()
        with self.assertRaisesRegex(ValueError, 'Review the building description'):
            self.chat(build_events(), handler)
        handler.assert_not_called()
        self.assertEqual(self.game.writes, [])

    def test_revision_changes_actual_plan_and_requires_fresh_confirmation(self):
        self.setup_build()
        handler = Mock()
        first, _ = self.chat(review_events(), handler)
        with patch.object(designs, 'generate_plan', return_value=fixture.sample_result('minecraft:white_wool')) as generate:
            updated, _ = self.chat(review_events(True), handler, prompt='Yes, but change the roof to white wool.')
        handler.assert_not_called()
        self.assertIn('white wool', updated['review']['summary'])
        self.assertNotEqual(first['review']['design_id'], updated['review']['design_id'])
        self.assertIn('Yes, but change the roof to white wool.', [item['content'] for item in generate.call_args.kwargs['history']])
        revised_plan = designs.read_saved_plan(self.store.get(self.record['id'])['designs'][-1]['plan_path'])[1]
        self.assertEqual(revised_plan['blueprint']['stages'][-1]['components'][0]['block'], 'minecraft:white_wool')
        with self.assertRaises(ValueError):
            self.chat(build_events(review_id=first['review']['id']), handler, prompt='Yes')
        handler.assert_not_called()

    def test_failed_revision_clears_previous_review_without_building(self):
        self.setup_build()
        handler = Mock()
        self.chat(review_events(), handler)
        with patch.object(designs, 'generate_plan', side_effect=ValueError('Offline generation failed')):
            with self.assertRaises(ValueError):
                self.chat(review_events(True), handler, prompt='Change the roof to wool.')
        self.assertNotIn('build_review', self.store.get(self.record['id']))
        handler.assert_not_called()

    def test_approval_constructs_revised_blueprint_not_original(self):
        self.setup_build()
        handler = Mock(side_effect=lambda design_id:build_design(self.store, self.record['id'], design_id,
                                                               uuid4().hex, self.game, self.ctx))
        self.chat(review_events(), handler)
        with patch.object(designs, 'generate_plan', return_value=fixture.sample_result('minecraft:white_wool')):
            reviewed, _ = self.chat(review_events(True), handler, prompt='Change the roof to wool.')
        handler.assert_not_called()
        result, _ = self.chat(build_events(review_id=reviewed['review']['id']), handler, prompt='Yes')
        self.assertEqual(result['build']['design_id'], reviewed['review']['design_id'])
        self.assertEqual(sum(block == 'minecraft:white_wool' for block in self.game.blocks.values()), 25)

    def test_review_without_saved_plan_generates_but_does_not_construct(self):
        self.setup_build()
        self.record = self.store.create()
        handler = Mock()
        with patch.object(designs, 'generate_plan', return_value=fixture.sample_result()):
            result, _ = self.chat(review_events(True), handler, prompt='Build a small oak pavilion.')
        self.assertIsNotNone(result['review'])
        self.assertEqual(len(self.store.get(self.record['id'])['designs']), 1)
        handler.assert_not_called()
        self.assertEqual(self.game.writes, [])

    def test_review_persists_but_regeneration_invalidates_approval(self):
        self.setup_build()
        handler = Mock()
        result, _ = self.chat(review_events(), handler)
        self.store.recover()
        self.assertEqual(self.store.get(self.record['id'])['build_review']['id'], result['review']['id'])
        self.generate('minecraft:white_wool')
        self.assertNotIn('build_review', self.store.get(self.record['id']))
        with self.assertRaises(ValueError):
            self.chat(build_events(review_id=result['review']['id']), handler, prompt='Yes')
        handler.assert_not_called()


if __name__ == '__main__':
    unittest.main()
