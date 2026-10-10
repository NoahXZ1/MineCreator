"""Conversation-bound, reviewed edits to a recorded Minecraft building."""

from collections import Counter
import hashlib
import json
from uuid import uuid4

from .blueprint import Blueprint, MAX_PLACEMENTS, expand_blueprint
from .chat import now, valid_id
from .designs import local_file
from .modify_with_openai import current_blueprint, blueprint_hash, block_diff, flatten, edit_request, run
from .operations import OperationCancelled, OperationContext, OperationEvent
from .plan_with_openai import ROOT, generate_plan, save_plan
from .preview_plan import preview


def selected_build(record):
    candidates = [build for build in record.get('builds', [])
                  if build.get('status') == 'completed' and build.get('record_path')]
    selected = record.get('selected_build_id')
    if selected:
        return next((item for item in candidates if item['id'] == selected), None)
    return candidates[-1] if candidates else None


def target_data(record):
    target = selected_build(record)
    if target is None:
        raise ValueError('Select a completed building from this conversation first.')
    path = local_file(target['record_path'], (ROOT / 'data' / 'builds',))
    build = json.loads(path.read_text(encoding='utf-8-sig'))
    if build.get('phase') != 'completed':
        raise ValueError('This building did not complete construction.')
    local_file(build['source_plan'], (ROOT / 'data' / 'plans',))
    return target, path, build


def load_proposal(review, record):
    target, path, build = target_data(record)
    if review.get('build_id') != target['id']:
        raise ValueError('The edit target changed. Request a new edit summary.')
    proposal_path = local_file(review['path'], (ROOT / 'data' / 'edit-proposals',))
    raw = proposal_path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != review['sha256']:
        raise ValueError('The proposed edit changed after review. Request a new summary.')
    proposal = json.loads(raw)
    if (proposal['conversation_id'] != record['id'] or proposal['id'] != review['id']
            or proposal['build_id'] != target['id'] or proposal['source_build'] != str(path)):
        raise ValueError('The edit does not belong to the selected building.')
    latest, source_hash, prior = current_blueprint(path, build)
    if (proposal['baseline_sha256'] != blueprint_hash(latest)
            or proposal['source_sha256'] != source_hash or proposal['prior_edit'] != prior):
        raise ValueError('The building changed after review. Request a new edit summary.')
    return proposal, path, build


def prepare_edit(store, conversation_id, request_id, prompt, context, prior_review=None):
    context.check()
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 8000:
        raise ValueError('The edit request must contain 1-8000 characters.')
    record = store.get(conversation_id)
    target, path, build = target_data(record)
    current, source_hash, prior = current_blueprint(path, build)
    baseline = current
    if prior_review:
        proposed, _, _ = load_proposal(prior_review, record)
        baseline = Blueprint.model_validate(proposed['model_result']['blueprint'])
    context.report('Preparing an edit proposal. Minecraft will not be changed until confirmation.', phase='edit_planning')
    result = generate_plan(prompt, history=[dict(role='user', content=edit_request(prompt, baseline))], context=context)
    revised = Blueprint.model_validate(result['blueprint'])
    if expand_blueprint(revised) != result['expanded_stages']:
        raise ValueError('The edit proposal does not match its blueprint.')
    old, new = flatten(expand_blueprint(current)), flatten(result['expanded_stages'])
    changes = block_diff(old, new)
    if len(changes) > MAX_PLACEMENTS:
        raise ValueError('The edit exceeds 4096 changed blocks.')
    counts = dict(added=0, removed=0, replaced=0)
    transitions = Counter()
    for pos, block in changes.items():
        before = old.get(pos, 'minecraft:air')
        counts['added' if before == 'minecraft:air' else 'removed' if block == 'minecraft:air' else 'replaced'] += 1
        transitions[before, block] += 1
    location = ', '.join(map(str, build['origin']))
    summary = (f"Proposed edit to {build['title']} at {location} ({build['dimension']}).\n\n"
               f"{revised.description}\n\n{len(changes)} changed blocks: {counts['added']} added, "
               f"{counts['removed']} removed, {counts['replaced']} replaced.\n")
    for (before, after), count in transitions.most_common(8):
        summary += f"{count} × {before.removeprefix('minecraft:')} → {after.removeprefix('minecraft:')}\n"
    summary += ('\nApply these changes to this building in Minecraft? Confirm to apply, or describe revisions.'
                if changes else '\nThe proposed block data is unchanged. No game edits are needed.')
    proposal = dict(id=request_id, conversation_id=conversation_id, build_id=target['id'],
                    source_build=str(path), source_sha256=source_hash, prior_edit=prior,
                    baseline_sha256=blueprint_hash(current), model_result=result, request=prompt,
                    created_at=now(), change_count=len(changes), counts=counts, summary=summary)
    folder = ROOT / 'data' / 'edit-proposals'
    context.check()
    folder.mkdir(parents=True, exist_ok=True)
    destination = folder / f'{valid_id(request_id)}.json'
    raw = json.dumps(proposal, ensure_ascii=False, indent=2).encode('utf-8')
    with destination.open('xb') as file:
        file.write(raw)
    return dict(id=request_id, build_id=target['id'], path=str(destination.resolve()),
                sha256=hashlib.sha256(raw).hexdigest(), summary=summary,
                change_count=len(changes), counts=counts, origin=build['origin'], dimension=build['dimension'])


def apply_edit(store, conversation_id, request_id, review, client, context, request_player=None):
    context.check()
    proposal, path, build = load_proposal(review, store.get(conversation_id))
    store.begin_edit(conversation_id, request_id, review)
    sink = context.sink
    writes_possible = False

    def progress(event):
        nonlocal writes_possible
        if event.data.get('phase') == 'applying':
            writes_possible = True
        fields = {key:value for key,value in event.data.items()
                  if key in {'phase','current','total','record_path','applied','changed','unchanged'}}
        store.update_edit(conversation_id, request_id, message=event.message, **fields)
        sink(OperationEvent(event.operation, event.kind, event.message,
                            dict(event.data, scope='edit', edit_id=request_id, conversation_id=conversation_id)))

    context.sink = progress
    try:
        result = run(proposal['request'], path, context=context, prepared=proposal,
                     client=client, request_player=request_player)
        store.update_edit(conversation_id, request_id, status='completed', **result,
                          message=f"Edit verified: {result['changed']} changed blocks.")
        # Synchronize the design panel with the verified blueprint, without any API call.
        if result['phase'] == 'verified':
            design_started = False
            plan_saved = False
            try:
                design_id = uuid4().hex
                record = store.get(conversation_id)
                pending_turn = next((turn['id'] for turn in record['turns']
                                     if turn['id'] == request_id and turn['status'] == 'pending'), None)
                store.begin_design(conversation_id, design_id, chat_request_id=pending_turn)
                design_started = True
                plan = save_plan(proposal['model_result'])
                store.update_design(conversation_id, design_id, status='ready', plan_path=str(plan.resolve()))
                plan_saved = True
                image = preview(plan, context=OperationContext('voxel_preview', lambda _:None))
                store.update_design(conversation_id, design_id, preview_path=str(image.resolve()))
            except Exception:
                # The world edit is committed; a preview failure cannot undo success.
                if design_started:
                    store.update_design(conversation_id, design_id,
                                        **({'preview_error':'Could not render the verified design.'} if plan_saved else
                                           {'status':'failed', 'error':'Could not save the verified design.'}))
                context.report('Edit verified. The design panel could not be refreshed.', phase='preview_failed')
        return dict(result, edit_id=request_id, build_id=review['build_id'], origin=build['origin'])
    except Exception as exc:
        detail = str(exc) if isinstance(exc, (ValueError, RuntimeError)) else (
            'Stop requested.' if isinstance(exc, OperationCancelled) else 'A local file or Minecraft connection operation failed.')
        message = (f'Edit stopped before placing blocks. {detail} The reviewed proposal is kept; '
                   'confirm again or click Apply edit to retry after resolving the issue.' if not writes_possible else
                   f'Edit stopped. {detail} Applied changes remain; no automatic retry.')
        store.fail_edit(conversation_id, request_id, review,
                        status='cancelled' if isinstance(exc, OperationCancelled) else 'failed',
                        message=message, retryable=not writes_possible)
        raise
    finally:
        context.sink = sink
