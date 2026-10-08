"""One conversation-bound construction path for the GUI button and chat tool."""

from .build_from_plan import run
from .chat import latest_design
from uuid import uuid4

from .designs import find_design, read_saved_plan, generate_design
from .operations import OperationCancelled, OperationEvent
from .preview_with_openai import load_plan


def review_design(store, conversation_id, request_id, revise_plan, context):
    """Prepare an exact, saved proposal. Never connects to Minecraft."""
    context.check()
    design = latest_design(store.get(conversation_id))
    if revise_plan or design is None:
        context.report('Updating the blueprint before preparing its description.', phase='review_plan')
        generate_design(store, conversation_id, uuid4().hex, context, chat_request_id=request_id)
        design = latest_design(store.get(conversation_id))
    path, payload = read_saved_plan(design['plan_path'])
    source_hash, _ = load_plan(path)
    blueprint = payload['blueprint']
    size = blueprint['bounds']
    materials = sorted({block['block'].removeprefix('minecraft:').split('[')[0].replace('_', ' ')
                        for stage in payload['expanded_stages'] for block in stage['blocks']})
    # The description and stage prose come from the LLM's validated blueprint.
    # Dimensions/materials are derived from the same data that construction uses.
    summary = (f"Before building: {blueprint['title']}\n\n{blueprint['description']}\n\n"
               f"Footprint: {size['x']} × {size['z']} blocks; height: {size['y']} blocks. "
               f"Materials: {', '.join(materials)}.\n"
               + ' '.join(stage['description'] for stage in blueprint['stages'])
               + '\n\nI will build this near you, clearing and leveling the footprint if needed. '
                 'Is this description accurate, and shall I start building? '
                 'Confirm to begin, or tell me what to change.')
    context.check()
    return dict(id=request_id, design_id=design['id'], source_sha256=source_hash, summary=summary)


def build_design(store, conversation_id, design_id, request_id, client, context,
                 request_player=None):
    context.check()
    record = store.get(conversation_id)
    design = find_design(record, design_id)
    if latest_design(record)['id'] != design_id:
        raise ValueError('The displayed plan has changed. Review the latest plan before building.')
    path, _ = read_saved_plan(design['plan_path'])
    store.begin_build(conversation_id, design_id, request_id)
    sink = context.sink

    def progress(event):
        fields = {key: value for key, value in event.data.items() if key in
                  {'phase', 'origin', 'dimension', 'site_mode', 'placement_count',
                   'terrain_placements', 'stage', 'current', 'total', 'changed', 'verified', 'record_path'}}
        store.update_build(conversation_id, request_id, message=event.message, **fields)
        sink(OperationEvent(event.operation, event.kind, event.message,
                            dict(event.data, scope='build', conversation_id=conversation_id,
                                 design_id=design_id, build_id=request_id)))

    context.sink = progress
    try:
        context.report('Checking Minecraft and selecting a nearby site.', phase='connecting')
        result = run(path, client, None, True, 0.12, context=context, request_player=request_player)
        store.update_build(conversation_id, request_id, status='completed',
                           message=f"Construction completed; {result['verified']} blocks verified.", **result)
        return dict(result, conversation_id=conversation_id, design_id=design_id, build_id=request_id)
    except Exception as exc:
        cancelled = isinstance(exc, OperationCancelled)
        message = ('Construction stopped. Placed blocks remain.' if cancelled else
                   str(exc) if isinstance(exc, (ValueError, RuntimeError)) else
                   'Construction failed. Check Minecraft and MCPFabric. Placed blocks remain.')
        store.update_build(conversation_id, request_id, status='cancelled' if cancelled else 'failed', message=message)
        raise
    finally:
        context.sink = sink
