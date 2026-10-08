"""Render the actual expanded blueprint as an isometric PNG; no API or game writes."""
import argparse
import json
import math
from pathlib import Path
import random

from PIL import Image, ImageDraw, ImageFont

if __package__:
    from .blueprint import Blueprint, english_label, expand_blueprint
    from .operations import OperationContext, operation_context
else:
    from blueprint import Blueprint, english_label, expand_blueprint
    from operations import OperationContext, operation_context

PALETTE = {
    'oak_planks': (177, 139, 83), 'spruce_planks': (111, 79, 47),
    'oak_log': (111, 85, 51), 'dark_oak_log': (64, 46, 30),
    'stone_bricks': (137, 140, 141), 'cobblestone': (126, 128, 124),
    'glass': (152, 203, 211), 'white_wool': (234, 236, 237),
}
AA = 2


def font(size):
    for path in ('C:/Windows/Fonts/msyh.ttc', 'C:/Windows/Fonts/arial.ttf'):
        if Path(path).exists(): return ImageFont.truetype(path, size * AA)
    return ImageFont.load_default(size=size * AA)


def color(rgb, factor):
    return tuple(max(0, min(255, round(v * factor))) for v in rgb)


def render_view(draw, blocks, region, grain=True):
    blocks = {position:block for position,block in blocks.items() if block != 'minecraft:air'}
    if not blocks: return
    def project(p):
        x,y,z = p
        return (x-z)*math.sqrt(3)/2, (x+z)/2-y
    corners = [project((x+dx,y+dy,z+dz)) for x,y,z in blocks
               for dx,dy,dz in ((0,0,0),(1,1,1),(1,0,0),(0,1,1),(0,1,0),(1,0,1))]
    minx,maxx = min(p[0] for p in corners),max(p[0] for p in corners)
    miny,maxy = min(p[1] for p in corners),max(p[1] for p in corners)
    left,top,right,bottom = (v*AA for v in region)
    scale = min((right-left)/(maxx-minx+1.5),(bottom-top)/(maxy-miny+1.5))
    cx,cy = (left+right)/2,(top+bottom)/2
    def screen(p):
        sx,sy = project(p)
        return cx+(sx-(minx+maxx)/2)*scale,cy+(sy-(miny+maxy)/2)*scale
    xmin,xmax=min(p[0] for p in blocks),max(p[0] for p in blocks)+1
    zmin,zmax=min(p[2] for p in blocks),max(p[2] for p in blocks)+1
    ground=[screen((xmin-.7,-.06,zmin-.7)),screen((xmax+.7,-.06,zmin-.7)),
            screen((xmax+.7,-.06,zmax+.7)),screen((xmin-.7,-.06,zmax+.7))]
    draw.polygon(ground,fill=(223,229,222))
    faces=[]
    for (x,y,z),block in blocks.items():
        # Only outward-facing, exposed surfaces are drawn. Later stages overwrite earlier blocks.
        if (x+1,y,z) not in blocks:
            faces.append((x+y+z+2, 'x', block,(x,y,z),
                          [(x+1,y+1,z),(x+1,y+1,z+1),(x+1,y,z+1),(x+1,y,z)]))
        if (x,y,z+1) not in blocks:
            faces.append((x+y+z+2, 'z', block,(x,y,z),
                          [(x,y+1,z+1),(x+1,y+1,z+1),(x+1,y,z+1),(x,y,z+1)]))
        if (x,y+1,z) not in blocks:
            faces.append((x+y+z+2, 'top', block,(x,y,z),
                          [(x,y+1,z),(x+1,y+1,z),(x+1,y+1,z+1),(x,y+1,z+1)]))
    for _,side,block,xyz,vertices in sorted(faces,key=lambda f:f[0]):
        material=block.split(':')[-1].split('[')[0]
        rgb=PALETTE.get(material,(155,151,146))
        if material.endswith('_log') and side=='top': rgb=(167,133,81)
        shade=color(rgb,{'top':1.12,'x':.72,'z':.92}[side])
        quad=[screen(p) for p in vertices]
        draw.polygon(quad,fill=shade)
        def point(u,v):
            return ((1-u)*(1-v)*quad[0][0]+u*(1-v)*quad[1][0]+u*v*quad[2][0]+(1-u)*v*quad[3][0],
                    (1-u)*(1-v)*quad[0][1]+u*(1-v)*quad[1][1]+u*v*quad[2][1]+(1-u)*v*quad[3][1])
        if grain and scale>25:
            rng=random.Random(xyz[0]*100003+xyz[1]*997+xyz[2]*37)
            if material.endswith('_planks'):
                for v in (.25,.5,.75): draw.line((point(0,v),point(1,v)),fill=color(shade,.77),width=AA)
                for i in range(4):
                    u=.28 if i%2 else .72
                    draw.line((point(u,i/4),point(u,(i+1)/4)),fill=color(shade,.8),width=AA)
                for i in range(10):
                    v=rng.uniform(.04,.96);u=rng.uniform(.05,.55)
                    draw.line((point(u,v),point(min(.95,u+.28),v)),fill=color(shade,.94),width=AA)
            elif material.endswith('_log'):
                if side=='top':
                    for inset in (.14,.27,.4):
                        pts=[point(inset,inset),point(1-inset,inset),point(1-inset,1-inset),point(inset,1-inset)]
                        draw.line(pts+[pts[0]],fill=color(shade,.7),width=AA)
                else:
                    for i in range(7):
                        u=(i+1)/8+rng.uniform(-.03,.03)
                        draw.line((point(u,.04),point(u,.96)),fill=color(shade,.75 if i%2 else 1.12),width=AA)
        draw.line(quad+[quad[0]],fill=color(shade,.66),width=AA)


def preview(path: Path, output: Path | None = None, *, context: OperationContext | None = None):
    ctx = operation_context(context, 'voxel_preview')
    ctx.check()
    ctx.report('Rendering a block-data preview.', phase='rendering')
    payload=json.loads(path.read_text(encoding='utf-8-sig'))
    blueprint=Blueprint.model_validate(payload['blueprint'])
    stages=expand_blueprint(blueprint)
    if payload.get('expanded_stages') != stages:
        raise ValueError('Saved block data differs from the blueprint; regenerate the plan before previewing.')
    blocks={};snapshots=[]
    for stage_index, stage in enumerate(stages, 1):
        for b in stage['blocks']:
            position=b['x'],b['y'],b['z']
            if b['block']=='minecraft:air': blocks.pop(position,None)
            else: blocks[position]=b['block']
        snapshots.append((english_label(stage['name'], f'Stage {stage_index}'),dict(blocks)))
    image=Image.new('RGB',(1400*AA,1200*AA),(246,245,239));draw=ImageDraw.Draw(image)
    def text(x,y,value,size=22,fill=(64,62,52)):
        draw.text((x*AA,y*AA),value,font=font(size),fill=fill)
    text(58,30,english_label(blueprint.title, 'Building preview'),34)
    b=blueprint.bounds
    text(60,86,f'{b.x} × {b.z} footprint · {b.y} blocks tall · {len(blocks)} final blocks',20,(98,96,84))
    text(60,119,'Voxel schematic · partial-block shapes and textures are simplified',17,(125,121,109))
    render_view(draw,blocks,(50,163,1350,885))
    draw.line((60*AA,911*AA,1340*AA,911*AA),fill=(214,212,201),width=AA)
    text(60,925,'Construction stages',20)
    selected=snapshots if len(snapshots)<=4 else [snapshots[0],snapshots[len(snapshots)//2],snapshots[-1]]
    width=1280/len(selected)
    for i,(name,snapshot) in enumerate(selected):
        left=60+i*width
        render_view(draw,snapshot,(left,967,left+width-16,1148),grain=False)
        text(left+15,1155,f'{i+1}. {name} · {len(snapshot)} blocks',18)
    output=output or path.with_suffix('.preview.png')
    ctx.check()
    image.resize((1400,1200),Image.Resampling.LANCZOS).save(output)
    ctx.report(f'Preview: {output.resolve()} ({len(blocks)} final blocks)', phase='saved', path=str(output.resolve()), blocks=len(blocks))
    return output


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('plan',type=Path)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    preview(args.plan,args.output)
