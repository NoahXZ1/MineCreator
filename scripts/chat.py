"""Local conversations, streaming chat, and bounded design tool dispatch."""

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
from threading import RLock
from time import monotonic
from uuid import uuid4, UUID

from openai import OpenAI

from .operations import OperationCancelled, OperationContext
from .plan_with_openai import ROOT, read_settings
from .preview_with_openai import image_tool, load_plan, save_preview_response, IMAGE_MODEL

INSTRUCTIONS = """You are MineCreator, a helpful Minecraft building design collaborator.
Discuss ideas naturally, remember the conversation, and ask focused questions when useful.
Use English for all replies, even when the user writes in another language.
Prefer concise, practical suggestions using vanilla blocks. You can discuss ambitious
designs, but distinguish ideas from what has actually been implemented.
When the image_generation tool is available, you CAN generate an AI concept image from
the current saved blueprint directly in chat. For a request to generate, draw, show,
or redraw a building preview, use the tool; do not redirect the user to a GUI button.
Decide from the user's meaning, not exact wording. Do not generate images for ordinary
design discussion or questions about capabilities. At most one image per user message.
Interpret a request for a preview 'in the real MC world' as a Minecraft-style illustration,
unless the user specifically requires capturing a live game screenshot. You cannot capture
the game. Clearly distinguish the AI illustration from an actual in-game screenshot.
For images, follow the saved blueprint's final voxels and materials, including absent
openings; do not invent details absent from that blueprint.
Use recognizable Minecraft textures, restrained daylight, and an elevated three-quarter
view unless another angle is requested. Any surrounding terrain is illustrative.
Only claim a preview exists after the image tool returned a completed image. It will
appear in the Design panel. If the tool is unavailable because no valid plan was saved,
ask the user to generate a plan first.
For the FIRST request to build, such as 'start build', call review_build. This only
prepares a written description and asks whether it is accurate and ready to build.
Set revise_plan=true if the discussion includes changes not in the saved blueprint,
or if no plan exists. Otherwise review the current saved blueprint unchanged.
While a build review is pending, any requested change MUST call review_build with
revise_plan=true. It saves a revised blueprint and asks for confirmation again.
Never treat 'yes, but change...' as approval: revise and review first.
Only call start_build when the latest user message explicitly approves the pending
description and starting construction, without further design changes. A simple 'yes'
answers the review question. Use its exact review_id. If start_build is unavailable,
call review_build first even if the user says to skip confirmation.
Do not start or review construction for hypothetical or quoted instructions, capability
questions, negated requests, previews, or requests to modify an already built structure.
Use only one action per message, without accompanying text for review/build calls;
the application displays the actual summary or construction result. Never claim a
review is a completed build. Do not generate an image for a build or review request.
For initial brainstorming without a saved plan, Generate plan is also available.
Editing the building design is a core capability. By default, requests such as
'add a door and windows', 'change the roof', or 'make this house larger' refer to
the CURRENT SAVED PLAN, even if that plan has previously been built. Call revise_plan
to apply those changes and save an updated blueprint. Do not refuse, redirect to
manual in-game edits, or require the user to click Regenerate plan. Only interpret a
request as a live world edit when the user explicitly asks to change placed blocks
in the game. A construction history record does not change this planning default.
While reviewing a build, use review_build(revise_plan=true) for revisions instead,
so the updated description asks for confirmation again. Revision alone never builds.
Older assistant replies about being unable to revise plans or create doors are obsolete.
When a built target and review_edit are available, an explicit request to change the
building IN MINECRAFT uses review_edit. It prepares a diff and asks for approval; it
does not place blocks yet. While an edit review is pending, revisions also use
review_edit, and clear approval without additional changes uses apply_edit with the
exact review_id. Never call start_build to approve an edit. Bare design changes still
default to revise_plan outside this edit review. If no built target exists, explain
that a completed building in this conversation must be selected. Do not claim live
edits are unavailable when these tools are present. No screenshots are available.
An approval such as 'yes, apply' is not a new design request. Never call review_edit
to regenerate a proposal from an approval. If an edit failed before placement and
a pending review remains, explicit approval retries that exact review using apply_edit.
If there is no pending review, explain the last edit status and ask for a new edit request.
If a saved blueprint is supplied, use its actual contents
as the baseline and distinguish proposed changes from already saved geometry.
The generator uses generic solid boxes, hollow boxes and gable roofs. A 1x1x1 solid
box can describe any vanilla block and its states. There is no eight-material whitelist
or restriction against architectural details. You plan the architecture; the program
validates and expands coordinates. Minecraft validates block IDs/states at execution.
Unsupported block-entity NBT is distinct from normal block states.
Keep each extent at most 24 blocks and all placements at most 4096. Explain limitations
when a requested detail needs unsupported primitives. Use plain text without Markdown
emphasis; do not return JSON unless asked.
"""


def build_tool(review_id: str) -> dict:
    return dict(type='function', name='start_build', strict=True,
                description='Construct the previously reviewed blueprint only after the user approves that summary without changes.',
                parameters=dict(type='object', properties={'review_id':dict(type='string', enum=[review_id])},
                                required=['review_id'], additionalProperties=False))


def review_tool() -> dict:
    return dict(type='function', name='review_build', strict=True,
                description='Describe the proposed building and ask for approval; never edits Minecraft. '
                            'For changes, first regenerate the blueprint using the conversation.',
                parameters=dict(type='object', properties={'revise_plan':dict(type='boolean')},
                                required=['revise_plan'], additionalProperties=False))


def revision_tool() -> dict:
    return dict(type='function', name='revise_plan', strict=True,
                description='Update the building blueprint from the current conversation and save a new version. '
                            'For requests like add doors/windows or change the design. Never changes game blocks.',
                parameters=dict(type='object', properties={}, required=[], additionalProperties=False))


def edit_tools(review=None) -> list[dict]:
    tools = [dict(type='function', name='review_edit', strict=True,
                  description='Prepare a local edit of the selected built structure in Minecraft; asks for confirmation and never writes game blocks.',
                  parameters=dict(type='object', properties={}, required=[], additionalProperties=False))]
    if review and review.get('change_count', 0) > 0:
        tools.append(dict(type='function', name='apply_edit', strict=True,
                          description='Apply the reviewed changes to the selected building only after clear user approval without new changes.',
                          parameters=dict(type='object', properties={'review_id':dict(type='string', enum=[review['id']])},
                                          required=['review_id'], additionalProperties=False)))
    return tools


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def valid_id(value: str) -> str:
    try:
        if not isinstance(value, str) or UUID(value).hex != value:
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        raise ValueError('Invalid conversation or request ID.') from None
    return value


def validate_prompt(prompt: str) -> str:
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 8000:
        raise ValueError('Your message must contain 1-8000 characters.')
    return prompt.strip()


def completed_history(record: dict) -> list[dict]:
    history = []
    for turn in record['turns']:
        if turn['status'] == 'completed':
            history.extend([dict(role='user', content=turn['user']),
                            dict(role='assistant', content=turn['assistant'])])
    return history


def latest_design(record: dict) -> dict | None:
    return next((item for item in reversed(record.get('designs', [])) if item.get('plan_path')), None)


class ConversationStore:
    def __init__(self, folder: Path | None = None):
        self.folder = folder if folder is not None else ROOT / 'data' / 'conversations'
        self.lock = RLock()

    def _path(self, conversation_id: str) -> Path:
        return self.folder / f'{valid_id(conversation_id)}.json'

    def _read(self, conversation_id: str) -> dict:
        try:
            record=json.loads(self._path(conversation_id).read_text(encoding='utf-8'))
            if not isinstance(record,dict) or record.get('id')!=conversation_id or not all(isinstance(record.get(k),str) for k in ('title','created_at','updated_at')) or not isinstance(record.get('turns'),list):
                raise ValueError('Invalid conversation data.')
            for turn in record['turns']:
                if not isinstance(turn,dict) or not all(isinstance(turn.get(k),str) for k in ('id','user','assistant','status','created_at')):
                    raise ValueError('Invalid conversation turn.')
            for group in ('designs','builds','edits'):
                if not isinstance(record.get(group,[]),list) or any(not isinstance(item,dict) or not isinstance(item.get('status'),str) for item in record.get(group,[])):
                    raise ValueError('Invalid saved operation.')
            for design in record.get('designs',[]):
                if not isinstance(design.get('images',[]),list) or any(not isinstance(item,dict) or not isinstance(item.get('status'),str) for item in design.get('images',[])):
                    raise ValueError('Invalid saved image operation.')
            return record
        except FileNotFoundError:
            raise ValueError('Conversation not found.') from None
        except (ValueError,TypeError):
            raise ValueError('This conversation could not be read. Its saved file has been kept.') from None

    def _save(self, record: dict) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        path = self._path(record['id'])
        temporary = path.with_suffix(f'.{uuid4().hex}.tmp')
        try:
            temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)

    def create(self) -> dict:
        with self.lock:
            record = dict(id=uuid4().hex, title='New conversation', created_at=now(), updated_at=now(), turns=[])
            self._save(record)
            return record

    def get(self, conversation_id: str) -> dict:
        with self.lock:
            return self._read(conversation_id)

    def list(self) -> list[dict]:
        with self.lock:
            records = []
            for path in self.folder.glob('*.json'):
                try:
                    record = self._read(path.stem)
                    records.append({key:record[key] for key in ('id', 'title', 'created_at', 'updated_at')})
                except (ValueError, KeyError, OSError):
                    continue
            return sorted(records, key=lambda item:item['updated_at'], reverse=True)

    def recover(self) -> None:
        """A previous interrupted process never triggers an automatic API retry."""
        with self.lock:
            for item in self.list():
                record = self._read(item['id'])
                changed = False
                for turn in record['turns']:
                    if turn['status'] == 'pending':
                        turn.update(status='interrupted', error='The application closed before this reply finished.')
                        changed = True
                for design in record.get('designs', []):
                    if design['status'] == 'pending':
                        design.update(status='interrupted', error='Plan generation was interrupted.')
                        changed = True
                    for image in design.get('images', []):
                        if image['status'] == 'pending':
                            image.update(status='interrupted', error='Image generation was interrupted.')
                            changed = True
                for build in record.get('builds', []):
                    if build['status'] == 'running':
                        build.update(status='interrupted', message='Construction interrupted. Placed blocks remain; no automatic restart.')
                        changed = True
                for edit in record.get('edits', []):
                    if edit['status'] == 'running':
                        edit.update(status='interrupted', message='Edit interrupted. Applied changes remain; no automatic restart.')
                        changed = True
                if changed:
                    self._save(record)

    def begin(self, conversation_id: str, request_id: str, prompt: str) -> list[dict]:
        prompt = validate_prompt(prompt)
        valid_id(request_id)
        with self.lock:
            record = self._read(conversation_id)
            if any(turn['id'] == request_id for turn in record['turns']):
                raise ValueError('This message was already submitted. Reload the conversation.')
            if any(turn['status'] == 'pending' for turn in record['turns']):
                raise ValueError('Wait for the current reply to finish.')
            history = completed_history(record)
            history.append(dict(role='user', content=prompt))
            if sum(len(item['content']) for item in history) > 96000:
                raise ValueError('This conversation is too long. Start a new chat to continue.')
            if not record['turns']:
                record['title'] = prompt[:42] + ('...' if len(prompt) > 42 else '') if prompt.isascii() else 'Building discussion'
            record['turns'].append(dict(id=request_id, user=prompt, assistant='', status='pending', created_at=now()))
            # Each user reply must be interpreted anew. A failed/revised reply
            # cannot leave an older approval usable by a later message.
            record.pop('build_review', None)
            record.pop('edit_review', None)
            record['updated_at'] = now()
            self._save(record)
            return deepcopy(history)

    def finish(self, conversation_id: str, request_id: str, text: str, status: str, **metadata) -> dict:
        with self.lock:
            record = self._read(conversation_id)
            turn = next(turn for turn in record['turns'] if turn['id'] == request_id)
            turn.update(assistant=text, status=status, **metadata)
            if status == 'completed' and metadata.get('review'):
                record['build_review'] = deepcopy(metadata['review'])
            if status == 'completed' and metadata.get('edit_review'):
                record['edit_review'] = deepcopy(metadata['edit_review'])
            record['updated_at'] = now()
            self._save(record)
            return record

    def begin_design(self, conversation_id: str, request_id: str, *, chat_request_id=None) -> dict:
        valid_id(request_id)
        with self.lock:
            record = self._read(conversation_id)
            if any(turn['status'] == 'pending' and turn['id'] != chat_request_id for turn in record['turns']):
                raise ValueError('Wait for the current reply before generating a plan.')
            if not completed_history(record) and not chat_request_id:
                raise ValueError('Discuss a building idea in chat before generating a plan.')
            designs = record.setdefault('designs', [])
            if any(item['id'] == request_id for item in designs):
                raise ValueError('This plan request was already submitted.')
            if any(item['status'] == 'pending' for item in designs):
                raise ValueError('A plan is already being generated.')
            snapshot = deepcopy(record)
            designs.append(dict(id=request_id, status='pending', created_at=now(),
                                source_turn_ids=[turn['id'] for turn in record['turns']
                                                 if turn['status'] == 'completed' or turn['id'] == chat_request_id]))
            record.pop('build_review', None)
            record.pop('edit_review', None)
            record['updated_at'] = now()
            self._save(record)
            return snapshot

    def update_design(self, conversation_id: str, design_id: str, **fields) -> dict:
        with self.lock:
            record = self._read(conversation_id)
            design = next((item for item in record.get('designs', []) if item['id'] == design_id), None)
            if design is None:
                raise ValueError('Plan not found in this conversation.')
            design.update(**fields)
            if 'plan_path' in fields:
                record.pop('build_review', None)
                record.pop('edit_review', None)
            record['updated_at'] = now()
            self._save(record)
            return record

    def begin_image(self, conversation_id: str, design_id: str, request_id: str) -> dict:
        valid_id(request_id)
        with self.lock:
            record = self._read(conversation_id)
            design = next((item for item in record.get('designs', []) if item['id'] == design_id), None)
            if design is None or not design.get('plan_path'):
                raise ValueError('Select a saved plan first.')
            images = design.setdefault('images', [])
            if any(item['id'] == request_id or item['status'] == 'pending' for item in images):
                raise ValueError('This image request is already submitted or still running.')
            images.append(dict(id=request_id, status='pending', created_at=now()))
            record['updated_at'] = now()
            self._save(record)
            return deepcopy(design)

    def begin_build(self, conversation_id: str, design_id: str, request_id: str) -> None:
        valid_id(request_id)
        with self.lock:
            record = self._read(conversation_id)
            builds = record.setdefault('builds', [])
            if any(item['id'] == request_id or item['status'] == 'running' for item in builds):
                raise ValueError('This build request was already submitted or is still running.')
            builds.append(dict(id=request_id, design_id=design_id, status='running', created_at=now(),
                               message='Checking Minecraft and selecting a nearby site.'))
            record.pop('build_review', None)
            record.pop('edit_review', None)
            record['updated_at'] = now()
            self._save(record)

    def update_build(self, conversation_id: str, request_id: str, **fields) -> None:
        with self.lock:
            record = self._read(conversation_id)
            build = next(item for item in record['builds'] if item['id'] == request_id)
            build.update(**fields)
            if fields.get('status') == 'completed':
                record['selected_build_id'] = request_id
            record['updated_at'] = now()
            self._save(record)

    def select_build(self, conversation_id, build_id):
        valid_id(build_id)
        with self.lock:
            record = self._read(conversation_id)
            if not any(item['id'] == build_id and item.get('status') == 'completed' and item.get('record_path')
                       for item in record.get('builds', [])):
                raise ValueError('Select a completed building from this conversation.')
            record['selected_build_id'] = build_id
            record.pop('edit_review', None)
            self._save(record)

    def begin_edit(self, conversation_id, request_id, review):
        valid_id(request_id)
        with self.lock:
            record = self._read(conversation_id)
            edits = record.setdefault('edits', [])
            if any(item['id'] == request_id or item['status'] == 'running'
                   or (item['review_id'] == review['id'] and not item.get('retryable', False)) for item in edits):
                raise ValueError('This edit was already submitted. Request a new review before retrying.')
            edits.append(dict(id=request_id, review_id=review['id'], build_id=review['build_id'],
                              status='running', created_at=now(), message='Checking the target building.'))
            record.pop('edit_review', None)
            self._save(record)

    def fail_edit(self, conversation_id, request_id, review, *, status, message, retryable):
        with self.lock:
            record = self._read(conversation_id)
            edit = next(item for item in record['edits'] if item['id'] == request_id)
            edit.update(status=status, message=message, error=message, retryable=retryable)
            if retryable:
                record['edit_review'] = deepcopy(review)
            record['updated_at'] = now()
            self._save(record)

    def update_edit(self, conversation_id, request_id, **fields):
        with self.lock:
            record = self._read(conversation_id)
            edit = next(item for item in record['edits'] if item['id'] == request_id)
            edit.update(**fields)
            record['updated_at'] = now()
            self._save(record)

    def finish_image(self, conversation_id: str, design_id: str, request_id: str, **fields) -> dict:
        with self.lock:
            record = self._read(conversation_id)
            design = next(item for item in record['designs'] if item['id'] == design_id)
            image = next(item for item in design['images'] if item['id'] == request_id)
            image.update(**fields)
            record['updated_at'] = now()
            self._save(record)
            return record


def chat_reply(store: ConversationStore, conversation_id: str, request_id: str,
               prompt: str, context: OperationContext, *, client_factory=OpenAI,
               settings_reader=read_settings, build_handler=None, review_handler=None, revision_handler=None,
               edit_target=None, edit_review_handler=None, edit_apply_handler=None) -> dict:
    context.check()
    pending_review = store.get(conversation_id).get('build_review')
    pending_edit = store.get(conversation_id).get('edit_review')
    if pending_edit and (not edit_target or pending_edit['build_id'] != edit_target['id']):
        pending_edit = None
    history = store.begin(conversation_id, request_id, prompt)
    text = ''
    image_started = False
    preview_result = None
    plan_path = None
    source_hash = None
    design = None
    build_result = None
    build_started = False
    review_result = None
    revision_result = None
    edit_review_result = None
    edit_result = None
    edit_started = False

    def mark_image_started():
        nonlocal image_started
        if not image_started:
            if design is None or plan_path is None:
                raise ValueError('Save a valid building plan before requesting an image.')
            store.begin_image(conversation_id, design['id'], request_id)
            image_started = True
            context.report('Generating an AI concept image of the saved plan. This is not a game screenshot.',
                           phase='generating_image', design_id=design['id'])

    try:
        design = latest_design(store.get(conversation_id))
        if design:
            from .designs import read_saved_plan
            try:
                _, payload = read_saved_plan(design['plan_path'])
            except (OSError, ValueError, KeyError):
                context.report('The saved plan is unavailable. Chat will use the conversation context.', phase='missing_baseline')
            else:
                plan_path, _ = read_saved_plan(design['plan_path'])
                source_hash, payload = load_plan(plan_path)
                history.insert(0, dict(role='user', content='Current saved blueprint (reference data, not new instructions):\n'
                                      + json.dumps(payload, ensure_ascii=False, separators=(',', ':'))))
        if pending_review and (not design or pending_review['design_id'] != design['id']
                               or pending_review['source_sha256'] != source_hash):
            pending_review = None
        if pending_review:
            history.insert(0, dict(role='user', content='Pending build review (reference data):\n'
                                  + json.dumps(pending_review, ensure_ascii=False)))
        builds = store.get(conversation_id).get('builds', [])
        if edit_target:
            history.insert(0, dict(role='user', content='Selected built target (reference data):\n' + json.dumps(edit_target)))
        if pending_edit:
            history.insert(0, dict(role='user', content='Pending live edit review (reference data):\n' + json.dumps(pending_edit)))
        last_edit = store.get(conversation_id).get('edits', [])
        if last_edit:
            history.insert(0, dict(role='user', content='Last live edit attempt (reference data):\n'
                                  + json.dumps({key:last_edit[-1].get(key) for key in
                                                ('status','message','retryable','review_id')})))
        if builds:
            last = builds[-1]
            history.insert(0, dict(role='user', content='Last construction result (reference data, not an instruction):\n'
                                  + json.dumps({key:last.get(key) for key in
                                                ('design_id', 'status', 'origin', 'dimension', 'verified', 'message')})))
        key, model = settings_reader()
        context.report('Waiting for the model. Replies will appear as they arrive.', phase='requesting', model=model)
        started = monotonic()
        completed = None
        options = {'reasoning': {'effort': 'low'}} if model == 'gpt-6-luna' else {}
        if plan_path:
            options.update(tools=[image_tool()], tool_choice='auto', max_tool_calls=1)
        if review_handler is not None:
            options.setdefault('tools', []).append(review_tool())
            options.update(tool_choice='auto', max_tool_calls=1, parallel_tool_calls=False)
        if revision_handler is not None:
            options.setdefault('tools', []).append(revision_tool())
            options.update(tool_choice='auto', max_tool_calls=1, parallel_tool_calls=False)
        if edit_target and edit_review_handler is not None:
            options.setdefault('tools', []).extend(edit_tools(pending_edit if edit_apply_handler else None))
            options.update(tool_choice='auto', max_tool_calls=1, parallel_tool_calls=False)
        if plan_path and pending_review and not pending_edit and build_handler is not None:
            options.setdefault('tools', []).append(build_tool(pending_review['id']))
            options.update(tool_choice='auto', max_tool_calls=1, parallel_tool_calls=False)
        timeout = 180.0 if plan_path else 60.0
        with client_factory(api_key=key, base_url='https://api.openai.com/v1', timeout=timeout, max_retries=0) as client:
            context.check()
            with client.responses.create(model=model, instructions=INSTRUCTIONS, input=history,
                                         max_output_tokens=4096, store=False, stream=True, **options) as stream:
                for event in stream:
                    context.check()
                    if monotonic() - started > (300 if image_started else 180 if plan_path else 120):
                        raise ValueError('The reply exceeded its time limit. No automatic retry was sent.')
                    if event.type in ('response.output_text.delta', 'response.refusal.delta'):
                        text += event.delta
                        context.report('', kind='delta', text=event.delta, request_id=request_id)
                    elif event.type == 'response.completed':
                        completed = event.response
                    elif event.type.startswith('response.image_generation_call.'):
                        mark_image_started()
                    elif event.type == 'response.output_item.added' and event.item.type == 'image_generation_call':
                        mark_image_started()
                    elif event.type in ('response.failed', 'response.incomplete', 'error'):
                        raise ValueError('The model did not complete its reply. You can send your request again.')
        context.check()
        if completed is None:
            raise ValueError('No complete text reply was received. You can send your request again.')
        calls = [item for item in getattr(completed, 'output', []) if item.type == 'function_call']
        if calls:
            if (len(calls) != 1 or image_started
                    or any(item.type == 'image_generation_call' for item in completed.output)):
                raise ValueError('Invalid build tool request. No construction started.')
            context.check()
            arguments = json.loads(calls[0].arguments)
            if calls[0].name == 'review_edit' and edit_target and edit_review_handler is not None:
                if arguments != {}:
                    raise ValueError('Invalid edit review request.')
                edit_review_result = edit_review_handler()
                text = edit_review_result['summary']
                context.report('', kind='replace_text', text=text, request_id=request_id)
            elif (calls[0].name == 'apply_edit' and pending_edit and edit_apply_handler is not None
                  and pending_edit.get('change_count', 0) > 0 and arguments == {'review_id':pending_edit['id']}):
                edit_started = True
                edit_result = edit_apply_handler(pending_edit)
                text = f"Edit verified: {edit_result['changed']} changed blocks; {edit_result.get('unchanged', 0)} other blocks unchanged."
                context.report('', kind='replace_text', text=text, request_id=request_id)
            elif calls[0].name == 'revise_plan' and revision_handler is not None:
                if arguments != {}:
                    raise ValueError('Invalid plan revision request. No construction started.')
                if pending_edit and edit_review_handler is not None:
                    edit_review_result = edit_review_handler()
                    text = edit_review_result['summary']
                elif pending_review and review_handler is not None:
                    review_result = review_handler(True)
                    text = review_result['summary']
                else:
                    revision_result = revision_handler()
                    text = revision_result['summary']
                context.report('', kind='replace_text', text=text, request_id=request_id)
            elif calls[0].name == 'review_build' and review_handler is not None:
                if (not isinstance(arguments, dict) or set(arguments) != {'revise_plan'}
                        or type(arguments['revise_plan']) is not bool):
                    raise ValueError('Invalid review request. No construction started.')
                review_result = review_handler(arguments['revise_plan'])
                text = review_result['summary']
                context.report('', kind='replace_text', text=text, request_id=request_id)
            elif (calls[0].name == 'start_build' and pending_review and not pending_edit and plan_path and build_handler is not None
                  and arguments == {'review_id':pending_review['id']}):
                # Bind approval to the exact reviewed bytes, not merely a filename.
                if load_plan(plan_path)[0] != pending_review['source_sha256']:
                    raise ValueError('The plan changed after review. Request a new build summary.')
                build_started = True
                context.report('Starting construction of the approved plan.', phase='build_requested', design_id=design['id'])
                build_result = build_handler(design['id'])
                text = f"Construction completed at {build_result['origin']}. {build_result['verified']} blocks verified."
                context.report('', kind='replace_text', text=text, request_id=request_id)
            else:
                raise ValueError('Review the building description and confirm it before construction.')
        if any(item.type == 'image_generation_call' for item in getattr(completed, 'output', [])):
            mark_image_started()
            image = save_preview_response(completed, plan_path, source_hash, history, 'low', IMAGE_MODEL,
                                          INSTRUCTIONS, context=context)
            context.check()
            store.finish_image(conversation_id, design['id'], request_id, status='ready', path=str(image.resolve()),
                               source_chat=request_id)
            preview_result = dict(kind='concept', conversation_id=conversation_id, design_id=design['id'], image_id=request_id)
            context.report('AI concept image saved.', phase='image_saved', **preview_result)
            if not text.strip():
                text = 'Generated an AI concept image of the saved plan. View it in the Design panel. This is an illustration, not an in-game screenshot.'
                context.report('', kind='delta', text=text, request_id=request_id)
        elif image_started:
            raise ValueError('The model started an image but did not return a completed preview.')
        if not text.strip():
            raise ValueError('No complete text reply was received. You can send your request again.')
        record = store.finish(conversation_id, request_id, text, 'completed', model=completed.model,
                              response_id=completed.id,
                              usage=completed.usage.model_dump() if completed.usage else None,
                              preview=preview_result, build=build_result, review=review_result, revision=revision_result,
                              edit_review=edit_review_result, edit=edit_result)
        return dict(conversation=record, preview=preview_result, build=build_result, review=review_result,
                    revision=revision_result, edit_review=edit_review_result, edit=edit_result)
    except Exception as exc:
        cancelled = isinstance(exc, OperationCancelled)
        if edit_started:
            attempts = store.get(conversation_id).get('edits', [])
            attempt = next((item for item in attempts if item['id'] == request_id), None)
            text = attempt['message'] if attempt else 'Edit stopped before completion. Check the edit status for details.'
            context.report('', kind='replace_text', text=text, request_id=request_id)
        if build_started:
            detail = str(exc) if isinstance(exc, (ValueError, RuntimeError)) else 'The construction operation could not finish.'
            text = ('Construction stopped. Placed blocks remain.' if cancelled else
                    f'Construction stopped before completion. {detail} Placed blocks remain.')
            context.report('', kind='replace_text', text=text, request_id=request_id)
        if image_started and preview_result is None:
            store.finish_image(conversation_id, design['id'], request_id,
                               status='cancelled' if cancelled else 'failed', error='Preview generation stopped.' if cancelled else 'Preview generation failed.')
        store.finish(conversation_id, request_id, text, 'cancelled' if cancelled else 'failed',
                     preview=preview_result,
                     error='Edit stopped. Applied changes remain. See edit status.' if edit_started
                     else 'Construction stopped. Placed blocks remain. See construction status.' if build_started
                     else 'Reply stopped. Partial text is not used as context.' if cancelled
                     else 'The reply failed. Check configuration, model access, credit, and connectivity.')
        raise
