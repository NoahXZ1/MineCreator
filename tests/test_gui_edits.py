"""Offline edit reviews, exact proposal execution, and game readback integration."""
import json
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch, Mock
from uuid import uuid4

from scripts import edits, modify_with_openai, designs
from scripts.backend import MineCreatorBackend
from scripts.chat import chat_reply
from scripts.operations import OperationContext, OperationCancelled
from scripts.run_gui import ChatApplication
from test_chat import FakeClient
from test_designs import sample_result
import test_designs as design_fixture
import test_gui_build as build_fixture


def tool_events(name, arguments=None):
    response = NS(status='completed', model='offline', id='offline-edit', usage=None,
                  output=[NS(type='function_call', name=name, arguments=json.dumps(arguments or {}))])
    return [NS(type='response.completed', response=response)]


class EditTests(unittest.TestCase):
    complete_turn = design_fixture.DesignTests.complete_turn
    generate = design_fixture.DesignTests.generate
    setup_build = build_fixture.BuildTests.setup_build
    build = build_fixture.BuildTests.build

    def setUp(self):
        design_fixture.DesignTests.setUp(self)
        self.setup_build()
        self.built = self.build()
        self.game.writes.clear()
        self.events.clear()
        for module in (edits, modify_with_openai):
            patcher = patch.object(module, 'ROOT', self.root)
            patcher.start()
            self.addCleanup(patcher.stop)

    def prepare(self, material='minecraft:white_wool', prior=None):
        request_id = uuid4().hex
        self.store.begin(self.record['id'], request_id, 'Change the roof in Minecraft.')
        with patch.object(edits, 'generate_plan', return_value=sample_result(material)):
            review = edits.prepare_edit(self.store, self.record['id'], request_id,
                                        'Change the roof in Minecraft.', self.ctx, prior_review=prior)
        self.store.finish(self.record['id'], request_id, review['summary'], 'completed', edit_review=review)
        return review

    def apply(self, review):
        with patch.object(modify_with_openai, 'generate_plan', side_effect=AssertionError('Unexpected API request')):
            return edits.apply_edit(self.store, self.record['id'], uuid4().hex, review, self.game, self.ctx)

    def test_review_no_writes_apply_exact_proposal_verify_unchanged_and_next_edit(self):
        review = self.prepare()
        self.assertEqual(self.game.writes, [])
        self.assertEqual(review['counts'], dict(added=0, removed=0, replaced=25))
        result = self.apply(review)
        self.assertEqual((result['changed'], result['unchanged']), (25, 37))
        self.assertEqual(len(self.game.writes), 25)
        self.assertEqual(designs.plan_summary(self.store.get(self.record['id']))['version'], 2)
        self.assertTrue(any(event.data.get('scope') == 'edit' for event in self.events))
        next_review = self.prepare('minecraft:red_wool')
        self.assertIn('white_wool → red_wool', next_review['summary'])
        self.assertEqual(self.apply(next_review)['changed'], 25)

    def test_revised_proposal_uses_pending_version_without_writes(self):
        first = self.prepare()
        second = self.prepare('minecraft:red_wool', first)
        self.assertEqual(self.game.writes, [])
        self.assertEqual(self.store.get(self.record['id'])['edit_review']['id'], second['id'])
        self.apply(second)
        self.assertEqual(sum(value == 'minecraft:red_wool' for value in self.game.blocks.values()), 25)
        with self.assertRaisesRegex(ValueError, 'changed after review'):
            self.apply(first)

    def test_stale_target_tampered_proposal_and_foreign_conversation_rejected(self):
        review = self.prepare()
        other = self.store.create()
        with self.assertRaises(ValueError):
            edits.load_proposal(review, other)
        self.build()
        with self.assertRaisesRegex(ValueError, 'target changed'):
            self.apply(review)
        self.game.writes.clear()
        fresh = self.prepare()
        from pathlib import Path
        Path(fresh['path']).write_text('{}')
        with self.assertRaisesRegex(ValueError, 'changed after review'):
            self.apply(fresh)
        self.assertEqual(self.game.writes, [])

    def roof_position(self):
        x,y,z = self.built['origin']
        return x,y+4,z

    def test_manual_change_to_edit_target_rejected_before_any_write(self):
        review = self.prepare()
        self.game.blocks[self.roof_position()] = 'minecraft:diamond_block'
        with self.assertRaisesRegex(RuntimeError, 'Edit target conflicts'):
            self.apply(review)
        self.assertEqual(self.game.writes, [])
        self.assertEqual(self.store.get(self.record['id'])['edits'][-1]['status'], 'failed')

    def test_failed_button_read_then_yes_apply_reuses_original_proposal(self):
        review = self.prepare()
        position = self.roof_position()
        original = self.game.blocks[position]
        self.game.blocks[position] = 'minecraft:diamond_block'
        backend = MineCreatorBackend()
        backend.conversations = self.store
        self.addCleanup(backend.close)
        app = ChatApplication(backend)
        payload = dict(conversation_id=self.record['id'], request_id=uuid4().hex,
                       review_id=review['id'], confirmed=True)
        with patch.object(backend, '_client', return_value=self.game):
            app.apply_edit(payload)
            self.assertEqual(app.jobs[payload['request_id']]['task'].result(timeout=5).status, 'failed')
        record = self.store.get(self.record['id'])
        self.assertEqual(record['edit_review'], review)
        self.assertTrue(record['edits'][-1]['retryable'])
        self.assertIn('diamond_block', record['edits'][-1]['message'])
        self.assertEqual(self.game.writes, [])
        self.game.blocks[position] = original
        request_id = uuid4().hex
        client = FakeClient(tool_events('apply_edit', dict(review_id=review['id'])))
        revision = Mock(side_effect=AssertionError('Approval must not generate a new proposal'))
        with patch.object(modify_with_openai, 'generate_plan', side_effect=AssertionError('Unexpected generation')):
            result = chat_reply(self.store, self.record['id'], request_id, 'yes, apply', self.ctx,
                client_factory=lambda **_:client, settings_reader=lambda:('fake','offline'),
                edit_target=edits.selected_build(record), edit_review_handler=revision,
                edit_apply_handler=lambda pending:edits.apply_edit(self.store,self.record['id'],request_id,pending,self.game,self.ctx))
        self.assertIn('apply_edit', [tool.get('name') for tool in client.arguments['tools']])
        self.assertIn('diamond_block', str(client.arguments['input']))
        self.assertEqual(result['edit']['changed'], 25)
        revision.assert_not_called()
        self.assertEqual(len(self.game.writes), 25)

    def test_failed_chat_read_keeps_review_and_reports_specific_reason(self):
        review = self.prepare()
        self.game.blocks[self.roof_position()] = 'minecraft:stone'
        request_id = uuid4().hex
        client = FakeClient(tool_events('apply_edit', dict(review_id=review['id'])))
        with self.assertRaises(RuntimeError):
            chat_reply(self.store,self.record['id'],request_id,'yes, apply',self.ctx,
                client_factory=lambda **_:client,settings_reader=lambda:('fake','offline'),
                edit_target=edits.selected_build(self.store.get(self.record['id'])),edit_review_handler=Mock(),
                edit_apply_handler=lambda pending:edits.apply_edit(self.store,self.record['id'],request_id,pending,self.game,self.ctx))
        record = self.store.get(self.record['id'])
        self.assertEqual(record['edit_review'], review)
        self.assertIn('minecraft:stone', record['turns'][-1]['assistant'])
        self.assertIn('before placing blocks', record['turns'][-1]['assistant'])
        self.assertEqual(self.game.writes, [])

    def test_new_position_obstruction_rejected_before_writes(self):
        from scripts.blueprint import Blueprint, expand_blueprint
        proposal = sample_result('minecraft:white_wool')
        proposal['blueprint']['stages'][1]['components'].append(dict(kind='solid_box',
            origin=dict(x=2,y=1,z=2),size=dict(x=1,y=1,z=1),block='minecraft:oak_log'))
        proposal['expanded_stages'] = expand_blueprint(Blueprint.model_validate(proposal['blueprint']))
        with patch.object(edits, 'generate_plan', return_value=proposal):
            review = edits.prepare_edit(self.store, self.record['id'], uuid4().hex, 'Add a post.', self.ctx)
        origin = self.built['origin']
        self.game.blocks[(origin[0]+2,origin[1]+1,origin[2]+2)] = 'minecraft:chest'
        with self.assertRaisesRegex(RuntimeError, 'obstructed'):
            self.apply(review)
        self.assertEqual(self.game.writes, [])

    def test_untouched_block_changed_during_edit_fails_final_verification(self):
        review = self.prepare()
        def change_floor():
            self.game.blocks[tuple(self.built['origin'])] = 'minecraft:stone'
        self.game.after_write = change_floor
        with self.assertRaisesRegex(RuntimeError, 'An untouched block changed state'):
            self.apply(review)
        self.assertEqual(self.store.get(self.record['id'])['edits'][-1]['status'], 'failed')

    def test_unrelated_removed_replaced_and_state_changes_are_preserved(self):
        review = self.prepare()
        x,y,z = self.built['origin']
        custom = {(x,y,z):'minecraft:air', (x+1,y,z):'minecraft:diamond_block',
                  (x,y+1,z):'minecraft:oak_log[axis=x]'}
        self.game.blocks.update(custom)
        result = self.apply(review)
        self.assertEqual((result['changed'],result['unchanged']), (25,37))
        self.assertEqual(len(self.game.writes),25)
        for pos, material in custom.items():
            self.assertEqual(self.game.blocks[pos],material)
            self.assertNotIn(pos,self.game.writes)
        # A second roof edit must not quietly restore any of the player's changes.
        next_review = self.prepare('minecraft:red_wool')
        self.apply(next_review)
        for pos, material in custom.items():
            self.assertEqual(self.game.blocks[pos],material)
            self.assertNotIn(pos,self.game.writes)

    def test_stop_keeps_partial_changes_and_cannot_replay_review(self):
        review = self.prepare()
        self.game.after_write = self.ctx.cancel
        with self.assertRaises(OperationCancelled):
            self.apply(review)
        self.assertEqual(len(self.game.writes), 1)
        self.assertEqual(self.store.get(self.record['id'])['edits'][-1]['status'], 'cancelled')
        self.ctx = OperationContext('apply_edit', self.events.append)
        with self.assertRaisesRegex(ValueError, 'already submitted'):
            self.apply(review)
        self.assertEqual(len(self.game.writes), 1)

    def test_edit_log_disk_failure_preserves_parseable_history(self):
        from pathlib import Path
        review=self.prepare();original=Path.replace
        def fail_log(path,target):
            if path.parent==self.root/'data'/'modifications' and self.game.writes:raise OSError('Disk unavailable')
            return original(path,target)
        with patch.object(Path,'replace',fail_log),self.assertRaises(OSError):self.apply(review)
        self.assertEqual(len(self.game.writes),1)
        logs=list((self.root/'data'/'modifications').glob('*.json'));self.assertEqual(len(logs),1)
        self.assertNotEqual(json.loads(logs[0].read_text(encoding='utf-8'))['phase'],'verified')
        self.assertFalse(list((self.root/'data'/'modifications').glob('*.tmp')))
        self.assertEqual(self.store.get(self.record['id'])['edits'][-1]['status'], 'failed')

    def test_apply_button_confirmation_and_deduplication_without_api(self):
        review = self.prepare()
        backend = MineCreatorBackend()
        backend.conversations = self.store
        self.addCleanup(backend.close)
        app = ChatApplication(backend)
        payload = dict(conversation_id=self.record['id'], request_id=uuid4().hex, review_id=review['id'])
        with self.assertRaises(ValueError):
            app.apply_edit(payload)
        payload['confirmed'] = True
        with patch.object(backend, '_client', return_value=self.game), patch.object(OperationContext, 'pause', lambda ctx, _:ctx.check()), patch.object(modify_with_openai, 'generate_plan', side_effect=AssertionError('Unexpected API')):
            app.apply_edit(payload)
            outcome = app.jobs[payload['request_id']]['task'].result(timeout=5)
            app.apply_edit(payload)
        self.assertEqual(outcome.status, 'completed', outcome.error)
        self.assertEqual(len(self.game.writes), 25)

    def test_chat_review_then_approval_tools_and_same_proposal(self):
        target = edits.selected_build(self.store.get(self.record['id']))
        def chat(name, prompt, args=None):
            request_id = uuid4().hex
            client = FakeClient(tool_events(name, args))
            result = chat_reply(self.store, self.record['id'], request_id, prompt, self.ctx,
                client_factory=lambda **_:client, settings_reader=lambda:('fake','offline'), edit_target=target,
                edit_review_handler=lambda:edits.prepare_edit(self.store, self.record['id'], request_id, prompt, self.ctx),
                edit_apply_handler=lambda review:edits.apply_edit(self.store, self.record['id'], request_id, review, self.game, self.ctx))
            return result, client
        with patch.object(edits, 'generate_plan', return_value=sample_result('minecraft:white_wool')):
            first, client = chat('review_edit', 'Change the roof in Minecraft to white wool.')
        self.assertNotIn('apply_edit', [tool.get('name') for tool in client.arguments['tools']])
        self.assertEqual(self.game.writes, [])
        second, client = chat('apply_edit', 'Yes, apply it.', dict(review_id=first['edit_review']['id']))
        self.assertIn('apply_edit', [tool.get('name') for tool in client.arguments['tools']])
        self.assertEqual(second['edit']['changed'], 25)
        self.assertIn('37 other blocks unchanged', second['conversation']['turns'][-1]['assistant'])

    def test_no_edit_approval_tool_without_review(self):
        target = edits.selected_build(self.store.get(self.record['id']))
        handler = Mock()
        client = FakeClient(tool_events('apply_edit', {'review_id':uuid4().hex}))
        with self.assertRaises(ValueError):
            chat_reply(self.store, self.record['id'], uuid4().hex, 'Change the roof in Minecraft.', self.ctx,
                client_factory=lambda **_:client, settings_reader=lambda:('fake','offline'), edit_target=target,
                edit_review_handler=Mock(), edit_apply_handler=handler)
        handler.assert_not_called()
        self.assertEqual(self.game.writes, [])


if __name__ == '__main__':
    unittest.main()
