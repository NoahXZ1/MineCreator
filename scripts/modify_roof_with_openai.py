"""Ask OpenAI to change a built pavilion's roof, execute only that diff, and read it back."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import time
from uuid import uuid4

from openai import OpenAIError

from blueprint import expand_blueprint
from build_from_plan import matches, read_plan
from check_mcpfabric import MCPFabricClient
from plan_with_openai import ROOT, generate_plan

REQUEST = ("Hey, could you change the roof of this wooden pavilion to spruce planks? "
           "Keep its shape and size the same, leave the floor and all four pillars "
           "untouched, and check afterward that only the roof has changed.")


def flatten(stages):
    return {(b['x'], b['y'], b['z']): b['block']
            for stage in stages for b in stage['blocks']}


def run(build_path: Path) -> None:
    build = json.loads(build_path.read_text(encoding='utf-8-sig'))
    if build['phase'] != 'completed':
        raise ValueError('The selected build is incomplete; roof edit cannot proceed.')
    source_path = Path(build['source_plan'])
    original, stages, source_hash = read_plan(source_path)
    if source_hash != build['source_sha256']:
        raise ValueError('The original plan does not match the build record.')
    old_blocks = flatten(stages)
    roof = set()
    for stage in original.stages:
        for component in stage.components:
            if component.kind == 'gable_roof':
                part = original.model_copy(update={'stages': [stage.model_copy(update={'components': [component]})]})
                roof.update(flatten(expand_blueprint(part)))
    if not roof:
        raise ValueError('No gable_roof component found in the blueprint.')
    origin = build['origin']
    dimension = build['dimension']
    client = MCPFabricClient(Path(os.environ['APPDATA']) / '.minecraft', timeout=5)
    status = client.call('info.status')
    if not status.get('integratedServer') or 'world_write' not in status.get('capabilities', []):
        raise RuntimeError('Enter a single-player test world with MCPFabric world writes enabled.')
    request_player = client.call('player.getState')
    if request_player['dimension'] != dimension:
        raise RuntimeError('The player and pavilion are in different dimensions.')

    def params(local):
        return dict(x=origin[0]+local[0], y=origin[1]+local[1], z=origin[2]+local[2], dimension=dimension)

    def guard(local):
        position = params(local)
        current = client.call('player.getState')
        for player in (request_player, current):
            if player['dimension'] != dimension:
                raise RuntimeError('The player changed dimensions; edit stopped.')
            if (abs(position['x'] - player['x']) < 1.5
                    and abs(position['z'] - player['z']) < 1.5
                    and math.floor(player['y']) - 1 <= position['y'] <= math.ceil(player['y']) + 2):
                raise RuntimeError('The edit is too close to the player or their support block; stopped.')

    print(f'Pavilion origin: {origin}; reading {len(old_blocks)} blocks to confirm its current state.', flush=True)
    before = {}
    for local, block in old_blocks.items():
        actual = client.call('world.getBlock', params(local))
        if not matches(actual, block):
            raise RuntimeError(f'The building differs from the original plan at {params(local)}; edit stopped.')
        before[local] = actual
    context = (REQUEST + '\n\nExisting blueprint (building data):\n'
               + original.model_dump_json() + '\n\nReturn the complete revised Blueprint. '
               'Only replace the gable_roof material with minecraft:spruce_planks. '
               'Keep all component kinds, origins, sizes, bounds, and other materials unchanged. '
               'The program will check the diff and perform the requested in-game verification.')
    print('Sending natural-language edit request: ' + REQUEST, flush=True)
    revised = generate_plan(context)
    new_blocks = flatten(revised['expanded_stages'])
    if set(old_blocks) != set(new_blocks):
        raise ValueError('The API changed the building geometry; edit rejected.')
    changed = {local: material for local, material in new_blocks.items() if material != old_blocks[local]}
    if set(changed) != roof or any(material != 'minecraft:spruce_planks' for material in changed.values()):
        raise ValueError('The API did not strictly replace only the roof with spruce planks; edit rejected.')

    folder = ROOT / 'data' / 'modifications'
    folder.mkdir(parents=True, exist_ok=True)
    record_path = folder / f'roof-{uuid4().hex[:12]}.json'
    record = dict(created_at=datetime.now(timezone.utc).isoformat(), request=REQUEST,
                  source_build=str(build_path.resolve()), source_sha256=source_hash,
                  dimension=dimension, origin=origin, model_result=revised,
                  roof_blocks=len(roof), phase='validated', applied=0)

    def save():
        record_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')

    save()
    print(f'API plan validated: replacing only {len(changed)} roof blocks gradually.', flush=True)
    try:
        for index, (local, material) in enumerate(sorted(changed.items()), 1):
            actual = client.call('world.getBlock', params(local))
            guard(local)
            if not matches(actual, old_blocks[local]):
                raise RuntimeError(f'Roof changed during the API request at {params(local)}; stopped.')
            try:
                client.call('world.setBlock', {**params(local), 'blockId': material})
            except (OSError, RuntimeError):
                if not matches(client.call('world.getBlock', params(local)), material):
                    raise
            if not matches(client.call('world.getBlock', params(local)), material):
                raise RuntimeError(f'Roof write readback failed at {params(local)}.')
            record['applied'] = index
            record['phase'] = 'applying'
            save()
            if index % 5 == 0 or index == len(changed):
                print(f'Roof edit: {index}/{len(changed)}', flush=True)
            time.sleep(0.15)
        untouched = 0
        for local, expected in new_blocks.items():
            actual = client.call('world.getBlock', params(local))
            if not matches(actual, expected):
                raise RuntimeError(f'Final readback mismatch at {params(local)}.')
            if local not in roof:
                if actual['id'] != before[local]['id'] or actual.get('properties', {}) != before[local].get('properties', {}):
                    raise RuntimeError(f'Floor or pillar state changed at {params(local)}.')
                untouched += 1
        record.update(phase='verified', verified_roof=len(roof), verified_unchanged=untouched)
        save()
        print(f'Verified: {len(roof)} roof blocks replaced with spruce planks; {untouched} floor and pillar blocks unchanged.', flush=True)
        print(f'Request and execution record: {record_path}', flush=True)
    except (Exception, KeyboardInterrupt) as exc:
        record.update(phase='stopped', error=type(exc).__name__)
        save()
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('build', type=Path, help='Completed pavilion build record JSON')
    args = parser.parse_args()
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    try:
        run(args.build)
        return 0
    except OpenAIError as exc:
        print(f'API request failed: {type(exc).__name__}; HTTP {getattr(exc, "status_code", None)}', flush=True)
        return 1
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
        print(f'Edit stopped: {exc}', flush=True)
        return 1
    except KeyboardInterrupt:
        print('Edit interrupted; applied changes remain.', flush=True)
        return 130


if __name__ == '__main__':
    raise SystemExit(main())
