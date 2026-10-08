"""Plan-only edits through chat and generic vanilla block-state handling."""
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

from pydantic import ValidationError
from scripts import designs
from scripts.blueprint import Blueprint, Component, expand_blueprint
from scripts.chat import chat_reply
from scripts.operations import OperationContext
from scripts.preview_with_openai import load_plan
from test_chat import FakeClient
import test_designs as fixture


def revised_result():
    result = fixture.sample_result()
    blueprint = result['blueprint']
    blueprint['description'] = 'An oak pavilion with a door and glass panes.'
    blocks = [(1,1,0,'minecraft:oak_door[facing=north,half=lower,hinge=left,open=false,powered=false]'),
              (1,2,0,'minecraft:oak_door[facing=north,half=upper,hinge=left,open=false,powered=false]'),
              (3,1,0,'minecraft:glass_pane[north=false,south=false,east=true,west=true,waterlogged=false]'),
              (0,1,0,'minecraft:air')]
    blueprint['stages'].append(dict(name='Details', description='Add the door and glass panes, and clear the opening.',
        components=[dict(kind='solid_box', origin=dict(x=x,y=y,z=z), size=dict(x=1,y=1,z=1), block=block)
                    for x,y,z,block in blocks]))
    validated = Blueprint.model_validate(blueprint)
    result['blueprint'] = validated.model_dump()
    result['expanded_stages'] = expand_blueprint(validated)
    result['placement_count'] = sum(len(stage['blocks']) for stage in result['expanded_stages'])
    return result


class RevisionTests(unittest.TestCase):
    setUp = fixture.DesignTests.setUp
    complete_turn = fixture.DesignTests.complete_turn
    generate = fixture.DesignTests.generate

    def test_revision_after_previous_build_saves_plan_without_dispatching_game(self):
        original, _ = self.generate()
        build_id = uuid4().hex
        self.store.begin_build(self.record['id'], original['design_id'], build_id)
        self.store.update_build(self.record['id'], build_id, status='completed', origin=[1,64,1], verified=62)
        request_id = uuid4().hex
        response = NS(status='completed', model='offline', id='revision', usage=None,
                      output=[NS(type='function_call', name='revise_plan', arguments='{}')])
        client = FakeClient([NS(type='response.completed', response=response)])
        build_handler = Mock()
        context = OperationContext('chat', lambda _:None)
        with patch.object(designs, 'generate_plan', return_value=revised_result()) as generate:
            result = chat_reply(self.store, self.record['id'], request_id,
                                'This house has no door and windows, add them!', context,
                                client_factory=lambda **_:client, settings_reader=lambda:('fake','offline'),
                                build_handler=build_handler,
                                revision_handler=lambda:designs.revise_design(self.store, self.record['id'], request_id, context))
        build_handler.assert_not_called()
        self.assertIn('revise_plan', [tool.get('name') for tool in client.arguments['tools']])
        self.assertIn('This house has no door and windows, add them!', [item['content'] for item in generate.call_args.kwargs['history']])
        record = self.store.get(self.record['id'])
        self.assertEqual(len(record['builds']), 1)
        self.assertEqual(designs.plan_summary(record)['version'], 2)
        self.assertNotEqual(result['revision']['design_id'], original['design_id'])
        self.assertIn('No game blocks were changed.', record['turns'][-1]['assistant'])
        _, plan = load_plan(designs.read_saved_plan(record['designs'][-1]['plan_path'])[0])
        materials = [voxel[3] for voxel in plan['final_voxels']]
        self.assertTrue(any('oak_door[' in block for block in materials))
        self.assertTrue(any('glass_pane[' in block for block in materials))
        self.assertNotIn('minecraft:air', materials)
        self.assertNotIn('minecraft:air', [entry['block'] for entry in designs.plan_summary(record)['materials']])

    def test_generic_blocks_and_states_need_no_architectural_special_cases(self):
        for block in ('minecraft:cherry_planks', 'minecraft:blue_wool',
                      'minecraft:stone_brick_stairs[facing=west,half=bottom,shape=straight,waterlogged=false]',
                      'minecraft:lantern[hanging=true,waterlogged=false]', 'minecraft:air'):
            component = Component.model_validate(dict(kind='solid_box', origin=dict(x=0,y=0,z=0),
                                                      size=dict(x=1,y=1,z=1), block=block))
            self.assertEqual(component.block, block)
        self.assertEqual(set(Component.model_fields['kind'].annotation.__args__), {'solid_box','hollow_box','gable_roof'})

    def test_identifiers_reject_commands_nbt_nonvanilla_and_duplicate_states(self):
        for block in ('mod:door', 'minecraft:stone run say hello', 'minecraft:chest{Items:[]}',
                      'minecraft:oak_log[axis=x,axis=y]'):
            with self.assertRaises(ValidationError):
                Component.model_validate(dict(kind='solid_box', origin=dict(x=0,y=0,z=0),
                                               size=dict(x=1,y=1,z=1), block=block))


if __name__ == '__main__':
    unittest.main()
