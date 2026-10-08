"""Example wooden house in the test world: gradual staged construction with player protection."""
import argparse
import json
import os
from pathlib import Path
import time
from check_mcpfabric import MCPFabricClient

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'data/builds/wooden-small-house-20261004'
PLAN = json.loads((DATA / 'plan.json').read_text(encoding='utf-8-sig'))
DIM = PLAN['dimension']
def pos(u, y, v):
    return (70 + u, y, -76 - v)

def generate(stage):
    out = {}
    def put(u, y, v, block): out[pos(u,y,v)] = 'minecraft:' + block
    if stage == 'clear':
        survey = json.loads((DATA / 'survey.json').read_text())
        for b in survey['blocks']:
            x,y,z = b['x'],b['y'],b['z']
            u,v = x-70,-76-z
            inside = -1<=u<=15 and -1<=v<=13 or -6<=u<=-1 and -1<=v<=6
            natural = any(s in b['id'] for s in ('_leaves','_log','grass','fern','vine','flower','mushroom'))
            if inside and 64<=y<=81 and natural: out[x,y,z]='minecraft:air'
    elif stage == 'floor':
        for v in range(13):
            for u in range(15): put(u,64,v,'oak_planks')
        for v in range(6):
            for u in range(-5,0): put(u,64,v,'oak_planks')
    elif stage == 'frame':
        for y in range(65,74):
            for u,v in ((0,0),(5,0),(10,0),(14,0),(0,6),(14,6),(0,12),(5,12),(10,12),(14,12)):
                put(u,y,v,'dark_oak_log[axis=y]')
        for y in (68,73):
            for u in range(15):
                for v in (0,12): put(u,y,v,'dark_oak_log[axis=x]')
            for v in range(1,12):
                for u in (0,14): put(u,y,v,'dark_oak_log[axis=z]')
        for y in range(65,68):
            for u,v in ((-5,0),(-5,5),(-1,0),(-1,5)): put(u,y,v,'dark_oak_log[axis=y]')
    elif stage == 'upper-floor':
        for v in range(13):
            for u in range(15):
                if not (1<=u<=3 and 5<=v<=9): put(u,69,v,'oak_planks')
        for step in range(5):
            for u in range(1,4):
                put(u,65+step,4+step,'oak_stairs[facing=north,half=bottom,shape=straight,waterlogged=false]')
        for v in range(5,10): put(4,70,v,'oak_fence')
    elif stage == 'walls':
        for y in (65,66,67,70,71,72):
            for u in range(1,14):
                for v in (0,12):
                    if u in (5,10): continue
                    window = y in (66,67,71,72) and u in (2,3,4,6,7,8,11,12)
                    # 后门和侧翼入口保持可通行。
                    if v==12 and u in (6,7) and y in (65,66): continue
                    put(u,y,v,'glass_pane' if window else 'oak_planks')
            for v in range(1,12):
                for u in (0,14):
                    if v==6: continue
                    if u==0 and v in (2,3) and y in (65,66): continue
                    window = y in (66,67,71,72) and v in (2,3,4,8,9,10)
                    put(u,y,v,'glass_pane' if window else 'oak_planks')
        for y in range(65,68):
            for v in range(6):
                if v not in (0,5): put(-5,y,v,'glass_pane' if y==66 and v in (2,3) else 'oak_planks')
            for u in range(-4,0):
                for v in (0,5):
                    if v==0 and u in (-3,-2) and y in (65,66): continue
                    put(u,y,v,'oak_planks')
    elif stage == 'roof':
        for v in range(-1,14):
            for u in range(-1,16):
                d=min(u+1,15-u)
                y=74+d//2
                if u==7: b='spruce_slab[type=bottom,waterlogged=false]'
                elif d%2==0: b=f'spruce_stairs[facing={"east" if u<7 else "west"},half=bottom,shape=straight,waterlogged=false]'
                else: b='spruce_planks'
                put(u,y,v,b)
        for v in (0,12):
            for u in range(15):
                top=74+min(u+1,15-u)//2
                for y in range(74,top): put(u,y,v,'oak_planks')
                if 6<=u<=8: put(u,75,v,'glass_pane')
        for v in range(-1,7):
            for u in range(-6,1):
                d=min(u+6,-u)
                put(u,68+d//2,v,'spruce_slab[type=bottom,waterlogged=false]' if u==-3 else
                    f'spruce_stairs[facing={"east" if u<-3 else "west"},half=bottom,shape=straight,waterlogged=false]' if d%2==0 else 'spruce_planks')
    elif stage == 'terrace':
        survey=json.loads((DATA/'survey.json').read_text())
        for b in survey['blocks']:
            u,v=b['x']-70,-76-b['z']
            if 1<=u<=14 and -5<=v<=-1 and 65<=b['y']<=72 and any(s in b['id'] for s in ('_leaves','_log','grass','fern','vine','flower','mushroom')):
                out[b['x'],b['y'],b['z']]='minecraft:air'
        for v in range(-5,0):
            for u in range(1,15): put(u,64,v,'spruce_planks')
        for u in (1,7,14):
            for v in (-5,-1):
                for y in range(59,64): put(u,y,v,'dark_oak_log[axis=y]')
        for v in range(-5,0):
            for u in (1,14): put(u,65,v,'oak_fence')
        for u in range(2,14): put(u,65,-5,'oak_fence')
        for u,v in ((1,-5),(14,-5),(1,-1),(14,-1)):
            put(u,66,v,'lantern[hanging=false,waterlogged=false]')
        # 侧翼正门外宽石阶；背门留下宽通道。
        for u in range(-4,-1):
            put(u,64,-1,'stone_brick_stairs[facing=north,half=bottom,shape=straight,waterlogged=false]')
            put(u,63,-2,'stone_brick_stairs[facing=north,half=bottom,shape=straight,waterlogged=false]')
            put(u,62,-3,'stone_bricks')
        for u in (6,7):
            put(u,64,13,'stone_brick_stairs[facing=south,half=bottom,shape=straight,waterlogged=false]')
            put(u,63,14,'stone_brick_stairs[facing=south,half=bottom,shape=straight,waterlogged=false]')
    elif stage == 'interior':
        # 房门两半一起生成，随后再校验整体。
        for u,v,face in ((-3,0,'south'),(-2,0,'south'),(6,12,'north'),(7,12,'north')):
            for y,half in ((65,'lower'),(66,'upper')):
                put(u,y,v,f'oak_door[facing={face},half={half},hinge={"left" if u%2 else "right"},open=false,powered=false]')
        for u in (7,8):
            put(u,65,0,'air'); put(u,66,0,'air')
            for y,half in ((65,'lower'),(66,'upper')):
                put(u,y,0,f'oak_door[facing=south,half={half},hinge={"left" if u==7 else "right"},open=false,powered=false]')
        # 生活区、餐厨区，保留中央通路和楼梯前入口。
        for u in range(10,13): put(u,65,3,'oak_stairs[facing=north,half=bottom,shape=straight,waterlogged=false]')
        for u in range(9,13):
            for v in (4,5): put(u,65,v,'white_carpet')
        for u in (10,11): put(u,65,6,'spruce_slab[type=bottom,waterlogged=false]')
        for u in range(8,13): put(u,65,10,'oak_planks')
        put(8,65,10,'crafting_table')
        put(9,65,10,'furnace[facing=south,lit=false]')
        put(10,65,10,'smoker[facing=south,lit=false]')
        put(11,66,10,'lantern[hanging=false,waterlogged=false]')
        for u,v in ((12,9),(12,8),(5,10)): put(u,65,v,'barrel[facing=up,open=false]')
        for u in (5,6):
            for v in (7,8): put(u,65,v,'oak_slab[type=bottom,waterlogged=false]')
        for u in (4,7): put(u,65,7,f'oak_stairs[facing={"east" if u==4 else "west"},half=bottom,shape=straight,waterlogged=false]')
        for u,v in ((2,1),(12,1),(12,11),(2,11),(-4,4)):
            if u<0:
                put(u,67,v,'oak_planks')
                put(u,66,v,'lantern[hanging=true,waterlogged=false]')
            else: put(u,68,v,'lantern[hanging=true,waterlogged=false]')
        # 楼上卧室用隔墙和开放门洞连接书房。
        for v in range(1,12):
            if v in (5,6): continue
            for y in (70,71,72): put(8,y,v,'oak_planks')
        for u in (11,12):
            put(u,70,8,'white_bed[facing=north,part=foot,occupied=false]')
            put(u,70,9,'white_bed[facing=north,part=head,occupied=false]')
        for u in (10,13):
            put(u,70,9,'barrel[facing=up,open=false]')
            put(u,71,9,'lantern[hanging=false,waterlogged=false]')
        for v in (9,10,11):
            for y in (70,71): put(1,y,v,'bookshelf')
        for u in (3,4,5): put(u,70,11,'spruce_slab[type=bottom,waterlogged=false]')
        put(4,71,11,'lantern[hanging=false,waterlogged=false]')
        put(4,70,10,'oak_stairs[facing=north,half=bottom,shape=straight,waterlogged=false]')
        for u in (10,11,12):
            for v in (4,5): put(u,70,v,'white_carpet')
        for u,v in ((3,2),(6,8),(12,2)):
            put(u,70,v,'lantern[hanging=false,waterlogged=false]')
    elif stage == 'cleanup':
        out[85,64,-74]='minecraft:air'
    elif stage == 'access-fix':
        for y in (70,71):
            put(1,y,9,'air')
            put(6,y,11,'bookshelf')
    elif stage == 'finishing':
        for u in (1,2,3): put(u,69,9,'oak_planks')
        for u,v in ((4,5),(7,3),(7,8)):
            put(u,68,v,'lantern[hanging=true,waterlogged=false]')
        for x,g,z in ((63,63,-75),(68,62,-86),(80,62,-92),(86,62,-81)):
            out[x,g+1,z]='minecraft:oak_fence'
            out[x,g+2,z]='minecraft:lantern[hanging=false,waterlogged=false]'
        for u,v in ((-1,2),(15,2),(-1,10),(15,10)):
            put(u,73,v,'lantern[hanging=true,waterlogged=false]')
        c=MCPFabricClient(Path(os.environ['APPDATA'])/'.minecraft')
        region=c.call('world.getBlocks',dict(dimension=DIM,**{'from':dict(x=83,y=65,z=-76),'to':dict(x=88,y=71,z=-67)},includeAir=False,maxBlocks=500))
        for b in region['blocks']:
            if 'mushroom' in b['id']: out[b['x'],b['y'],b['z']]='minecraft:air'
    elif stage in ('garden-fence','garden-paths','garden-plants','garden-lights'):
        survey=json.loads((DATA/'survey.json').read_text())
        ground={}
        soil={'grass_block','dirt','coarse_dirt','podzol','mycelium','sand','gravel','stone','clay'}
        originals={}
        for b in survey['blocks']:
            x,y,z=b['x'],b['y'],b['z']
            if 56<=x<=88 and -104<=z<=-70:
                originals[x,y,z]=b['id']
                if b['id'].split(':')[-1] in soil and y<=67:
                    ground[x,z]=max(y,ground.get((x,z),58))
        def house(x,z): return 69<=x<=85 and -89<=z<=-75 or 64<=x<=69 and -82<=z<=-75
        def raw(x,y,z,b): out[x,y,z]='minecraft:'+b
        def height(x,z): return ground.get((x,z),61)
        def clear(x,z,top):
            for y in range(top+1,top+4):
                if originals.get((x,y,z),'minecraft:air')!='minecraft:air': raw(x,y,z,'air')
        if stage=='garden-fence':
            boundary=[]
            for x in range(57,88): boundary.append((x,-103))
            for z in range(-102,-72): boundary.append((57,z))
            for z in range(-102,-70): boundary.append((87,z))
            for x in range(58,72): boundary.append((x,-73))
            for z in (-72,-71): boundary.append((71,z))
            for x in (85,86): boundary.append((x,-71))
            for x,z in boundary:
                g=height(x,z)
                # 水边短段抬到露台标高，与露台围栏连接。
                if z>=-73 and x>=71:
                    g=64
                    for y in range(height(x,z)+1,65): raw(x,y,z,'dark_oak_log[axis=y]')
                clear(x,z,g)
                gate=(z==-73 and x in (64,65)) or (x==57 and z==-90)
                raw(x,g+1,z, 'oak_fence_gate[facing=north,open=false,powered=false,in_wall=false]' if gate else 'oak_fence')
        elif stage=='garden-paths':
            nodes=[(65,-74),(65,-79),(61,-84),(61,-92),(65,-97),(68,-99),(77,-99),(78,-94),(77,-89)]
            path=set()
            for (ax,az),(bx,bz) in zip(nodes,nodes[1:]):
                steps=max(abs(bx-ax),abs(bz-az))
                for t in range(steps+1):
                    x=round(ax+(bx-ax)*t/max(1,steps)); z=round(az+(bz-az)*t/max(1,steps))
                    for dx in (0,1): path.add((x+dx,z))
            path.update((x,-90) for x in range(58,62))
            for x,z in sorted(path):
                if house(x,z): continue
                g=height(x,z); clear(x,z,g); raw(x,g,z,'dirt_path')
        elif stage=='garden-plants':
            for x in range(79,86):
                for z in range(-100,-94):
                    if x in (79,85) or z in (-100,-95): raw(x,63,z,'stone_bricks')
                    elif x==82: raw(x,62,z,'dirt')
            for x,z in ((79,-98),(85,-98)):
                clear(x,z,63); raw(x,64,z,'oak_fence'); raw(x,65,z,'lantern[hanging=false,waterlogged=false]')
            for x in range(80,85):
                for z in range(-99,-95):
                    g=63; clear(x,z,g)
                    raw(x,g,z,'water[level=0]' if x==82 else 'farmland[moisture=7]')
                    if x!=82: raw(x,g+1,z,'carrots[age=7]' if z%2 else 'wheat[age=7]')
            for x,z in ((59,-78),(60,-79),(59,-81),(60,-82),(70,-96),(71,-97),(73,-97),(74,-96),(83,-92),(84,-93)):
                g=height(x,z); clear(x,z,g); raw(x,g,z,'grass_block')
                raw(x,g+1,z,('poppy','oxeye_daisy','cornflower')[abs(x+z)%3])
            for x,z in ((59,-85),(59,-86),(59,-87),(68,-96),(69,-96),(75,-95),(76,-95),(85,-91),(85,-92)):
                g=height(x,z); raw(x,g+1,z,'oak_leaves[persistent=true,waterlogged=false]')
            for u in (2,3,4,11,12):
                put(u,70,-1,'oak_leaves[persistent=true,waterlogged=false]')
                put(u,69,-1,'spruce_slab[type=top,waterlogged=false]')
            for u,v in ((2,-3),(3,-3),(4,-3)):
                put(u,65,v,'spruce_stairs[facing=south,half=bottom,shape=straight,waterlogged=false]')
        elif stage=='garden-lights':
            for x in (59,65,71,77,83):
                for z in (-77,-83,-89,-95,-101):
                    if house(x,z) or 80<=x<=84 and -99<=z<=-96: continue
                    g=height(x,z)
                    # 矮灯散在园内，围栏不做等间距火把阵列。
                    clear(x,z,g); raw(x,g+1,z,'oak_fence'); raw(x,g+2,z,'lantern[hanging=false,waterlogged=false]')
            for u,v in ((1,0),(13,0),(1,12),(13,12)):
                put(u,66,v,'lantern[hanging=false,waterlogged=false]')
    else: raise ValueError(stage)
    return out

def run(stage, limit):
    client = MCPFabricClient(Path(os.environ['APPDATA'])/'.minecraft')
    status = client.call('info.status')
    if not status.get('integratedServer'): raise RuntimeError('No single-player world is open.')
    targets = generate(stage)
    pending_path = DATA / (stage + '-pending.json')
    if pending_path.exists():
        targets = {tuple(t[:3]): t[3] for t in json.loads(pending_path.read_text())}
    player = client.call('player.getState')
    deferred = {}; applied = []; changed = 0
    for i, ((x,y,z),block) in enumerate(targets.items()):
        if changed>=limit:
            deferred[x,y,z]=block; continue
        if i%4==0: player=client.call('player.getState')
        if player['dimension']!=DIM: raise RuntimeError('The player changed dimensions; construction stopped.')
        req=PLAN['request_position']
        if abs(x-req['x'])<4 and abs(z-req['z'])<4: raise RuntimeError('The block is inside the protected request position.')
        if abs(x-player['x'])<2.5 and abs(z-player['z'])<2.5:
            deferred[x,y,z]=block; continue
        params=dict(x=x,y=y,z=z,dimension=DIM)
        old=client.call('world.getBlock',params)
        name=block.split('[')[0]
        if stage=='garden-paths' and name=='minecraft:dirt_path':
            above=client.call('world.getBlock',dict(params,y=y+1))
            if not above.get('air'): continue
        properties=dict(s.split('=') for s in block.split('[')[1].rstrip(']').split(',')) if '[' in block else {}
        if old['id']==name and all(old.get('properties',{}).get(k)==v for k,v in properties.items()):
            applied.append((params,name,properties)); continue
        if old['id'] in {'minecraft:chest','minecraft:barrel','minecraft:furnace','minecraft:smoker','minecraft:hopper','minecraft:spawner'}:
            raise RuntimeError(f'Existing functional block; overwrite stopped: {x},{y},{z}')
        result=client.call('world.setBlock',dict(params,blockId=block))
        if not result.get('success'): raise RuntimeError(str(result))
        applied.append((params,name,properties)); changed+=1
        if changed%25==0: print(f'{stage}: placed {changed}',flush=True)
        time.sleep(.035)
    for params,name,properties in applied:
        actual=client.call('world.getBlock',params)
        check={k:v for k,v in properties.items() if not (name=='minecraft:farmland' and k=='moisture')}
        if actual['id']!=name or any(actual.get('properties',{}).get(k)!=v for k,v in check.items()):
            raise RuntimeError(f'Readback mismatch: {params}; expected={name},{check}; actual={actual}')
    pending_path.write_text(json.dumps([list(p)+[b] for p,b in deferred.items()]),encoding='utf-8')
    print(f'{stage}: VERIFIED {len(applied)}, CHANGED {changed}, REMAINING {len(deferred)}',flush=True)
    if not deferred:
        current=json.loads((DATA/'plan.json').read_text())
        current['stage']=stage+'-complete'
        current['stages'].append(dict(name=stage,blocks=len(applied),verified=True))
        (DATA/'plan.json').write_text(json.dumps(current,ensure_ascii=False,indent=2),encoding='utf-8')

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage')
    parser.add_argument('--limit',type=int,default=200)
    args=parser.parse_args()
    run(args.stage,args.limit)
