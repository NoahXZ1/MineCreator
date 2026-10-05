"""分段精修正面、门廊和混合照明；逐块施工并避开玩家。"""
import argparse
import json
import os
from pathlib import Path
import time
from check_mcpfabric import MCPFabricClient
from build_wooden_house import DATA, DIM, PLAN, pos

def design(stage):
    out={}
    def put(u,y,v,b):out[pos(u,y,v)]='minecraft:'+b
    def raw(x,y,z,b):out[x,y,z]='minecraft:'+b
    def shutter(u,y,v):put(u,y,v,'spruce_trapdoor[facing=north,half=bottom,open=true,powered=false,waterlogged=false]')
    if stage=='facade':
        # 檐线有实际进深，不用平墙上的色块代替。
        for u in range(0,15):
            put(u,69,-1,'spruce_planks')
            put(u,73,-1,'spruce_planks')
        for u in (0,5,10,14):
            put(u,68,-1,'dark_oak_log[axis=z]')
        for u in (5,10):put(u,67,-1,'lantern[hanging=true,waterlogged=false]')
        for u in (1,9,13):
            for y in (71,72):shutter(u,y,-1)
        for u in (2,8,12):put(u,72,-1,'lantern[hanging=true,waterlogged=false]')
        # 三个上层花箱，木箱前板、土层、灌木与真正的花组合。
        for us in ((2,3,4),(6,7,8),(11,12)):
            for u in us:
                put(u,70,-1,'grass_block')
                shutter(u,70,-2)
                put(u,71,-1,'oxeye_daisy' if u%3 else 'poppy')
        # 下层花箱放在窗下，露台主通道和双门前不动。
        for us in ((2,3,4),(11,12)):
            for u in us:
                put(u,65,-1,'grass_block')
                shutter(u,65,-2)
                put(u,66,-1,'fern' if u in (2,4,11) else 'oxeye_daisy')
        for u in (1,13):
            for y in (66,67):shutter(u,y,-1)
    elif stage=='porch':
        for u in (-5,-1):
            put(u,64,-1,'mossy_cobblestone')
            for y in (65,66,67):put(u,y,-1,'dark_oak_log[axis=y]')
        for u in range(-5,0):
            put(u,68,-1,'spruce_planks')
            put(u,68,-2,'spruce_slab[type=bottom,waterlogged=false]')
        for u in (-4,-1):put(u,67,-2,'lantern[hanging=true,waterlogged=false]')
        # 门框两侧有木饰面和小盆栽，石阶中间留三格宽。
        for u in (-5,-1):
            put(u,65,0,'spruce_trapdoor[facing=north,half=bottom,open=true,powered=false,waterlogged=false]')
        for u in (-6,0):
            put(u,64,-1,'mossy_cobblestone')
            put(u,65,-1,'potted_fern')
    elif stage=='gate':
        # 原来的两个栅栏门高差一格；先统一门槛标高。
        for x in (64,65):
            for z in (-74,-73):raw(x,63,z,'stone_bricks')
            raw(x,63,-72,'stone_brick_stairs[facing=north,half=bottom,shape=straight,waterlogged=false]')
            raw(x,62,-71,'stone_brick_stairs[facing=north,half=bottom,shape=straight,waterlogged=false]')
        # 原有树叶会挡门框，清理门框正上方小范围。
        for x in range(63,67):
            for y in range(64,69):raw(x,y,-73,'air')
        for x in (63,66):
            raw(x,63,-73,'mossy_cobblestone')
            for y in (64,65,66,67):raw(x,y,-73,'dark_oak_log[axis=y]')
            for z in (-73,-72):raw(x,68,z,'spruce_slab[type=bottom,waterlogged=false]')
            raw(x,67,-72,'lantern[hanging=true,waterlogged=false]')
        for x in (64,65):
            for y,half in ((64,'lower'),(65,'upper')):
                raw(x,y,-73,f'oak_door[facing=south,half={half},hinge={"right" if x==64 else "left"},open=false,powered=false]')
    elif stage=='touchups':
        for x in (64,65):
            raw(x,62,-71,'dirt')
            raw(x,63,-71,'stone_brick_stairs[facing=north,half=bottom,shape=straight,waterlogged=false]')
            raw(x,63,-72,'stone_bricks')
        raw(73,63,-92,'oak_fence');raw(73,64,-92,'torch')
    elif stage=='torches':
        survey=json.loads((DATA/'survey.json').read_text());ground={}
        soil={'grass_block','dirt','coarse_dirt','podzol','mycelium','sand','gravel','stone','clay'}
        for b in survey['blocks']:
            if b['id'].split(':')[-1] in soil and b['y']<=67:
                k=b['x'],b['z'];ground[k]=max(b['y'],ground.get(k,58))
        # 少量围栏火把，特意不用固定间隔。
        for x,z in ((57,-82),(57,-96),(68,-103),(81,-103),(87,-87)):
            raw(x,ground.get((x,z),62)+2,z,'torch')
        # 一部分原有低灯换成火把，其他灯笼仍保留。
        for x,z in ((59,-89),(65,-101),(71,-95),(83,-77)):
            raw(x,ground.get((x,z),62)+2,z,'torch')
        # 地面小路边不整齐的两根矮火把柱。
        for x,z in ((62,-80),(74,-99)):
            y=ground.get((x,z),62)
            raw(x,y+1,z,'oak_fence');raw(x,y+2,z,'torch')
    else:raise ValueError(stage)
    return out

def run(stage):
    c=MCPFabricClient(Path(os.environ['APPDATA'])/'.minecraft')
    if not c.call('info.status').get('integratedServer'):raise RuntimeError('未进入单人世界')
    pending=DATA/('exterior-'+stage+'-pending.json')
    targets={tuple(t[:3]):t[3] for t in json.loads(pending.read_text())} if pending.exists() else design(stage)
    original=json.loads((DATA/'exterior-detail-request-position.json').read_text())
    deferred={};applied=[];changed=0;p=c.call('player.getState')
    for i,(xyz,block) in enumerate(targets.items()):
        x,y,z=xyz
        if i%3==0:p=c.call('player.getState')
        if p['dimension']!=DIM:raise RuntimeError('玩家维度变化，暂停施工')
        near=lambda q:abs(x-q['x'])<1.8 and abs(z-q['z'])<1.8 and q['y']-1.1<=y<q['y']+2.8
        if near(p) or near(original):deferred[xyz]=block;continue
        req=PLAN['request_position']
        if abs(x-req['x'])<4 and abs(z-req['z'])<4:raise RuntimeError('初始发令位置保护范围')
        params=dict(x=x,y=y,z=z,dimension=DIM);old=c.call('world.getBlock',params)
        name=block.split('[')[0]
        props=dict(t.split('=') for t in block.split('[')[1].rstrip(']').split(',')) if '[' in block else {}
        if old['id']==name and all(old.get('properties',{}).get(k)==v for k,v in props.items()):applied.append((params,name,props));continue
        if old['id'] in {'minecraft:chest','minecraft:barrel','minecraft:furnace','minecraft:smoker','minecraft:hopper','minecraft:spawner'}:raise RuntimeError(f'保留现有功能方块：{xyz}')
        r=c.call('world.setBlock',dict(params,blockId=block))
        if not r.get('success'):raise RuntimeError(str(r))
        applied.append((params,name,props));changed+=1
        if changed%20==0:print(stage,'placed',changed,flush=True)
        time.sleep(.035)
    issues=[]
    for params,name,props in applied:
        b=c.call('world.getBlock',params)
        if b['id']!=name or any(b.get('properties',{}).get(k)!=v for k,v in props.items() if k not in ('shape','powered')):issues.append(dict(position=params,expected=name,actual=b))
    pending.write_text(json.dumps([list(k)+[v] for k,v in deferred.items()]))
    (DATA/('exterior-'+stage+'-check.json')).write_text(json.dumps(dict(changed=changed,verified=len(applied)-len(issues),issues=issues,deferred=len(deferred))))
    print(stage,'changed',changed,'verified',len(applied)-len(issues),'deferred',len(deferred),'issues',issues,flush=True)
    if issues:raise RuntimeError('部分装饰未保持预期状态，需要修正')

if __name__=='__main__':
    a=argparse.ArgumentParser(description=__doc__);a.add_argument('stage');run(a.parse_args().stage)
