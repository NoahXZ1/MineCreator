"""Conversation-bound blueprints and previews; never connects to Minecraft."""

from collections import Counter
import json
from pathlib import Path
from uuid import uuid4

from .blueprint import Blueprint, expand_blueprint
from .chat import ConversationStore, completed_history, latest_design, valid_id
from .operations import OperationCancelled, OperationContext
from .plan_with_openai import ROOT, generate_plan, save_plan
from .preview_plan import preview
from .preview_with_openai import generate_preview, IMAGE_MODEL

PLAN_REQUEST = """Generate the buildable blueprint for the design discussed above.
Treat the user's latest requirements as authoritative. Retain earlier agreed dimensions,
materials and layout unless the user changed them. A saved blueprint, when supplied,
is the baseline: preserve its unchanged features and apply the discussed revisions.
Stay within the supported schema. Do not promise unsupported details in descriptions.
This is a plan revision only, even if a previous version was built. Include the latest
requested openings, glass windows and wooden doors in the actual components.
"""


def local_file(value: str | Path, folders: tuple[Path, ...]) -> Path:
    path = Path(value).resolve()
    if not any(path.is_relative_to(folder.resolve()) for folder in folders) or not path.is_file():
        raise ValueError('The saved design file is missing or outside the project data folder.')
    return path


def read_saved_plan(value: str | Path) -> tuple[Path, dict]:
    path = local_file(value, (ROOT / 'data' / 'plans',))
    payload = json.loads(path.read_text(encoding='utf-8-sig'))
    blueprint = Blueprint.model_validate(payload['blueprint'])
    if expand_blueprint(blueprint) != payload.get('expanded_stages'):
        raise ValueError('Saved block data differs from the blueprint. Regenerate the plan.')
    return path, payload


def find_design(record: dict, design_id: str) -> dict:
    valid_id(design_id)
    design = next((item for item in record.get('designs', []) if item['id'] == design_id), None)
    if design is None or not design.get('plan_path'):
        raise ValueError('Saved plan not found in this conversation.')
    return design


def plan_summary(record: dict) -> dict | None:
    design = latest_design(record)
    if design is None:
        return None
    path, payload = read_saved_plan(design['plan_path'])
    blueprint = payload['blueprint']
    blocks = {}
    for stage in payload['expanded_stages']:
        for block in stage['blocks']:
            blocks[block['x'], block['y'], block['z']] = block['block']
    blocks = {position: block for position, block in blocks.items() if block != 'minecraft:air'}
    materials = Counter(blocks.values())
    image = next((item for item in reversed(design.get('images', [])) if item['status'] == 'ready'), None)
    return dict(id=design['id'], title=blueprint['title'], description=blueprint['description'],
                bounds=blueprint['bounds'], final_blocks=len(blocks),
                placements=sum(len(stage['blocks']) for stage in payload['expanded_stages']),
                materials=[dict(block=block, count=count) for block, count in sorted(materials.items())],
                stages=[dict(name=stage['name'], description=stage['description'], count=len(stage['blocks']))
                        for stage in payload['expanded_stages']],
                filename=path.name, version=sum(bool(item.get('plan_path')) for item in record['designs']),
                created_at=design['created_at'], has_block_preview=bool(design.get('preview_path')),
                image_id=image['id'] if image else None, preview_error=design.get('preview_error'),
                needs_regeneration=design['source_turn_ids'] != [turn['id'] for turn in record['turns'] if turn['status'] == 'completed'])


def generate_design(store: ConversationStore, conversation_id: str, request_id: str,
                    context: OperationContext, *, chat_request_id=None) -> dict:
    context.check()
    snapshot = store.begin_design(conversation_id, request_id, chat_request_id=chat_request_id)
    saved = False
    try:
        history = completed_history(snapshot)
        if chat_request_id:
            turn = next(turn for turn in snapshot['turns'] if turn['id'] == chat_request_id)
            history.append(dict(role='user', content=turn['user']))
        baseline = latest_design(snapshot)
        if baseline:
            try:
                _, payload = read_saved_plan(baseline['plan_path'])
            except (OSError, ValueError, KeyError):
                context.report('The previous plan is unavailable. Generating a fresh plan from the conversation.', phase='missing_baseline')
            else:
                history.insert(0, dict(role='user', content='Saved blueprint baseline (reference data):\n'
                                      + json.dumps(payload['blueprint'], ensure_ascii=False)))
        result = generate_plan(PLAN_REQUEST, context=context, history=history)
        context.check()
        path = save_plan(result)
        # Keep a valid saved blueprint available even if preview rendering fails.
        store.update_design(conversation_id, request_id, status='ready', plan_path=str(path.resolve()))
        saved = True
        try:
            image = preview(path, context=context)
            context.check()
            store.update_design(conversation_id, request_id, preview_path=str(image.resolve()), preview_error=None)
        except OperationCancelled:
            store.update_design(conversation_id, request_id, preview_error='Plan saved; block preview was stopped.')
            raise
        except Exception:
            store.update_design(conversation_id, request_id, preview_error='Plan saved; block preview could not be rendered.')
            context.report('Plan saved. Block preview rendering failed.', phase='preview_failed')
        return dict(conversation_id=conversation_id, design_id=request_id, plan_path=str(path.resolve()))
    except Exception as exc:
        if not saved:
            store.update_design(conversation_id, request_id,
                                status='cancelled' if isinstance(exc, OperationCancelled) else 'failed',
                                error='Plan generation stopped.' if isinstance(exc, OperationCancelled)
                                else 'Plan generation failed; the previous saved plan remains available.')
        raise


def generate_design_image(store: ConversationStore, conversation_id: str, design_id: str,
                          request_id: str, context: OperationContext) -> dict:
    context.check()
    design = store.begin_image(conversation_id, design_id, request_id)
    try:
        path, _ = read_saved_plan(design['plan_path'])
        image = generate_preview(path, 'low', IMAGE_MODEL, context=context)
        context.check()
        store.finish_image(conversation_id, design_id, request_id, status='ready', path=str(image.resolve()))
        return dict(conversation_id=conversation_id, design_id=design_id, image_id=request_id)
    except Exception as exc:
        store.finish_image(conversation_id, design_id, request_id,
                           status='cancelled' if isinstance(exc, OperationCancelled) else 'failed',
                           error='Image generation stopped.' if isinstance(exc, OperationCancelled) else 'Image generation failed.')
        raise


def revise_design(store, conversation_id, request_id, context):
    result = generate_design(store, conversation_id, uuid4().hex, context, chat_request_id=request_id)
    summary = plan_summary(store.get(conversation_id))
    size = summary['bounds']
    return dict(result, summary=f"Updated plan: {summary['title']}\n\n{summary['description']}\n\n"
                f"Footprint: {size['x']} × {size['z']} blocks; height: {size['y']} blocks. "
                + ' '.join(stage['description'] for stage in summary['stages'])
                + '\n\nThe new version and block preview are saved in the Design panel. No game blocks were changed.')


def preview_file(record: dict, design_id: str, kind: str, image_id: str | None = None) -> Path:
    design = find_design(record, design_id)
    read_saved_plan(design['plan_path'])
    if kind == 'blocks':
        value = design.get('preview_path')
    elif kind == 'concept':
        if image_id is not None:
            valid_id(image_id)
        image = next((item for item in reversed(design.get('images', [])) if item['status'] == 'ready'
                      and (image_id is None or item['id'] == image_id)), None)
        value = image.get('path') if image else None
    else:
        raise ValueError('Unsupported preview type.')
    if not value:
        raise ValueError('This preview is not available yet.')
    path = local_file(value, (ROOT / 'data' / 'plans', ROOT / 'data' / 'previews'))
    if path.suffix.lower() != '.png':
        raise ValueError('Invalid preview file type.')
    return path
