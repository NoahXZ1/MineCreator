"""在现有木屋上精修室内；每次执行一个可见的小阶段。"""
import argparse
import json
import os
from pathlib import Path
import time
from check_mcpfabric import MCPFabricClient
from build_wooden_house import DATA, DIM, PLAN, pos

def design(stage):
    out={}
    def put(u,y,v,b): out[pos(u,y,v)]='minecraft:'+b
    def pot(u,y,v,flower='oxeye_daisy'): put(u,y,v,'potted_'+flower)
    def trap(u,y,v,face,wood='spruce'):
        put(u,y,v,f'{wood}_trapdoor[facing={face},half=bottom,open=true,powered=false,waterlogged=false]')
    if stage=='beams':
        for u in (5,10):
            for v in range(1,12): put(u,68,v,'stripped_dark_oak_log[axis=z]')
        for v in (3,9):
            for u in range(1,14): put(u,68,v,'stripped_dark_oak_log[axis=x]')
        for u,v in ((5,11),(10,11),(5,1),(10,1)):
            for y in (65,66,67): put(u,y,v,'stripped_dark_oak_log[axis=y]')
        for u,v in ((7,3),(10,5),(5,8),(10,9)):
            put(u,67,v,'lantern[hanging=true,waterlogged=false]')
        # 楼上木梁在离地三格处收住挑高空间，不封闭坡屋顶。
        for v in (3,10):
            for u in range(1,14): put(u,73,v,'stripped_dark_oak_log[axis=x]')
        for u in (5,10):
            for v in range(1,12): put(u,73,v,'stripped_dark_oak_log[axis=z]')
        for u,v in ((5,3),(10,3),(5,10),(10,10)):
            put(u,72,v,'lantern[hanging=true,waterlogged=false]')
    elif stage=='living':
        # 沙发面向茶几，扶手和窗边矮柜构成完整会客区。
        for u in (10,11,12): put(u,65,3,'spruce_stairs[facing=south,half=bottom,shape=straight,waterlogged=false]')
        for u in (9,13): trap(u,65,3,'east' if u==9 else 'west')
        for v in (4,5): put(13,65,v,'spruce_stairs[facing=east,half=bottom,shape=straight,waterlogged=false]')
        trap(13,65,6,'north')
        for u in range(9,13):
            for v in range(4,8): put(u,65,v,'white_carpet' if (u+v)%5 else 'light_gray_carpet')
        for u in (10,11): put(u,65,6,'spruce_slab[type=top,waterlogged=false]')
        pot(11,66,6)
        for u in (11,12): put(u,65,1,'spruce_planks')
        for u in (11,12): trap(u,65,2,'north','oak')
        pot(11,66,1,'fern'); put(12,66,1,'lantern[hanging=false,waterlogged=false]')
        # 入口有收纳台、细长地毯和盆栽，不挡双门。
        for u in (-4,-3): put(u,65,4,'spruce_planks')
        pot(-4,66,4); put(-3,66,4,'lantern[hanging=false,waterlogged=false]')
        for u in (-3,-2):
            for v in (1,2,3): put(u,65,v,'light_gray_carpet')
        put(1,65,1,'spruce_planks'); pot(1,66,1)
        put(1,65,2,'oak_leaves[persistent=true,waterlogged=false]')
        trap(2,65,2,'west')
    elif stage=='kitchen':
        # 保留炉子、烟熏炉和桶的内容；新增工作台面、吊柜、抽屉面和开放花架。
        for u in (8,9,10,11,12):
            put(u,67,11,'spruce_planks')
            trap(u,67,10,'north','oak')
        for u in (8,9,10,11,12): put(u,66,11,'spruce_slab[type=top,waterlogged=false]')
        for u,flower in ((8,'fern'),(10,'oxeye_daisy'),(12,'poppy')): pot(u,67,11,flower)
        # 吊柜向上移，给花架留下空间。
        for u in (8,9,10,11,12):
            put(u,68,11,'spruce_planks')
            trap(u,68,10,'north','oak')
            if u not in (10,): put(u,67,11,'air')
        put(10,67,11,'potted_oxeye_daisy')
        for u in (8,11): trap(u,65,9,'north','oak')
        for u in (5,6):
            for v in (7,8): put(u,65,v,'oak_slab[type=top,waterlogged=false]')
        pot(6,66,8)
        for u in (4,7): put(u,65,8,f'spruce_stairs[facing={"east" if u==4 else "west"},half=bottom,shape=straight,waterlogged=false]')
        for u in (4,7): put(u,65,7,f'spruce_stairs[facing={"east" if u==4 else "west"},half=bottom,shape=straight,waterlogged=false]')
        # 装饰层沿窗下安排，后门两格保持空旷。
        for u in (2,3,4): put(u,65,11,'spruce_slab[type=top,waterlogged=false]')
        pot(3,66,11,'fern')
    elif stage=='bedroom':
        # 背靠墙的木床头、脚凳、衣柜和宽地毯。
        for u in (10,11,12,13):
            put(u,70,10,'spruce_planks')
            trap(u,71,10,'south','oak')
        for u in (11,12): put(u,70,7,'spruce_stairs[facing=north,half=bottom,shape=straight,waterlogged=false]')
        for u in (10,13): trap(u,70,7,'east' if u==10 else 'west')
        for u in range(9,14):
            for v in range(6,11):
                if v in (8,9,10) or v==7 and u>=10: continue
                put(u,70,v,'white_carpet' if u%2 else 'light_gray_carpet')
        for u in (11,12,13):
            for y in (70,71,72): put(u,y,11,'spruce_planks')
            for y in (70,71): trap(u,y,10,'south','oak')
        # 柜旁台面和窗边小盆栽。
        put(9,70,11,'spruce_planks');pot(9,71,11)
        for u in (11,12): put(u,70,1,'spruce_slab[type=top,waterlogged=false]')
        pot(11,71,1,'fern')
        put(13,70,3,'oak_stairs[facing=east,half=bottom,shape=straight,waterlogged=false]')
        put(13,70,4,'oak_stairs[facing=east,half=bottom,shape=straight,waterlogged=false]')
        trap(13,70,2,'south');trap(13,70,5,'north')
        # 柱线强调寝室门洞，门洞两格不动。
        for v in (4,7):
            for y in (70,71,72): put(8,y,v,'stripped_dark_oak_log[axis=y]')
    elif stage=='polish':
        put(1,65,2,'air');put(2,65,2,'air')
        pot(8,67,11,'fern');pot(12,67,11,'poppy')
    elif stage=='accents':
        # 沿窗和入口的木地板收边，家具下面也保持材质连贯。
        for floor in (64,69):
            for u in range(1,14):
                for v in (1,11): put(u,floor,v,'stripped_oak_log[axis=x]')
            for v in range(2,11):
                for u in (1,13):
                    if floor==69 and u==1 and 5<=v<=8: continue
                    put(u,floor,v,'stripped_oak_log[axis=z]')
        # 有实体墙支撑的藤蔓，用来柔化木柱和房间转角。
        for y in (66,67):
            put(9,y,1,'vine[east=true,north=false,south=false,west=false,up=false]')
            put(4,y,11,'vine[east=false,north=true,south=false,west=false,up=false]')
        for y in (70,71,72):
            put(7,y,11,'vine[east=true,north=false,south=false,west=false,up=false]')
        put(9,65,1,'oak_leaves[persistent=true,waterlogged=false]')
        trap(9,65,2,'north')
        # 楼梯脚的木柱和低矮花台，不侵入踏步或上楼平台。
        for y in (65,66,67,68):put(4,y,4,'stripped_dark_oak_log[axis=y]')
        put(4,65,3,'spruce_planks');pot(4,66,3,'fern')
        put(2,65,6,'barrel[facing=south,open=false]')
        # 顶部书柜包边与床头的木饰面。
        for v in (10,11):put(1,73,v,'spruce_slab[type=top,waterlogged=false]')
        put(6,73,11,'spruce_slab[type=top,waterlogged=false]')
        for u in (9,10):
            put(u,72,10,'oak_trapdoor[facing=north,half=top,open=true,powered=false,waterlogged=false]')
    elif stage=='study':
        for v in (10,11):
            for y in (70,71,72): put(1,y,v,'bookshelf')
        for y in (70,71,72): put(6,y,11,'bookshelf')
        for u in (3,4,5): put(u,70,11,'spruce_slab[type=top,waterlogged=false]')
        trap(2,70,11,'east');trap(5,70,10,'north')
        pot(3,71,11)
        put(5,71,11,'lectern[facing=south,has_book=false,powered=false]')
        for u in (3,4,5,6):
            for v in (9,10):
                if u==4 and v==10 or u==5 and v==10: continue
                put(u,70,v,'white_carpet')
        # 楼梯平台沿边收口，维持三格出口和中央环绕通路。
        for v in (5,6,7,8): put(4,70,v,'spruce_fence')
        for u in (1,2,3,4):
            for v in (1,2): put(u,70,v,'light_gray_carpet')
        put(1,70,3,'spruce_planks');pot(1,71,3,'fern')
        put(6,70,2,'spruce_planks');pot(6,71,2)
    else: raise ValueError(stage)
    return out

def run(stage):
    c=MCPFabricClient(Path(os.environ['APPDATA'])/'.minecraft')
    if not c.call('info.status').get('integratedServer'): raise RuntimeError('请先进入单人存档')
    path=DATA/('detail-'+stage+'-pending.json')
    targets={tuple(v[:3]):v[3] for v in json.loads(path.read_text())} if path.exists() else design(stage)
    applied=[];deferred={};count=0
    original=json.loads((DATA/'interior-detail-request-position.json').read_text())
    p=c.call('player.getState')
    for i,(xyz,block) in enumerate(targets.items()):
        x,y,z=xyz
        if i%3==0:p=c.call('player.getState')
        if p['dimension']!=DIM:raise RuntimeError('玩家已离开当前维度，停止施工')
        req=PLAN['request_position']
        if abs(x-req['x'])<4 and abs(z-req['z'])<4:raise RuntimeError('发令位置保护范围')
        near=lambda q: abs(x-q['x'])<1.8 and abs(z-q['z'])<1.8 and q['y']-1.1<=y<q['y']+2.8
        if near(p) or near(original):deferred[xyz]=block;continue
        params=dict(x=x,y=y,z=z,dimension=DIM)
        old=c.call('world.getBlock',params)
        name=block.split('[')[0]
        props=dict(s.split('=') for s in block.split('[')[1].rstrip(']').split(',')) if '[' in block else {}
        if old['id']==name and all(old.get('properties',{}).get(k)==v for k,v in props.items()):applied.append((params,name,props));continue
        if old['id'] in {'minecraft:chest','minecraft:barrel','minecraft:furnace','minecraft:smoker','minecraft:hopper','minecraft:spawner'}:
            raise RuntimeError(f'保留已有功能方块和内容，需调整装饰位置：{xyz}')
        r=c.call('world.setBlock',dict(params,blockId=block))
        if not r.get('success'):raise RuntimeError(str(r))
        applied.append((params,name,props));count+=1
        if count%20==0:print(stage,'placed',count,flush=True)
        time.sleep(.035)
    issues=[]
    for params,name,props in applied:
        b=c.call('world.getBlock',params)
        checks={k:v for k,v in props.items() if k not in ('shape','distance','powered')}
        if b['id']!=name or any(b.get('properties',{}).get(k)!=v for k,v in checks.items()):issues.append(dict(position=params,expected=name,properties=checks,actual=b))
    path.write_text(json.dumps([list(k)+[v] for k,v in deferred.items()]))
    (DATA/('detail-'+stage+'-check.json')).write_text(json.dumps(dict(changed=count,verified=len(applied)-len(issues),deferred=len(deferred),issues=issues)))
    print(stage,'changed',count,'verified',len(applied)-len(issues),'deferred',len(deferred),'issues',issues,flush=True)
    if issues:raise RuntimeError('部分装饰回读不一致，需要修正')

if __name__=='__main__':
    a=argparse.ArgumentParser(description=__doc__);a.add_argument('stage');run(a.parse_args().stage)
