"""Generate the concise Chinese Stage 1 implementation guide. Requires reportlab."""
from pathlib import Path
from html import escape
import argparse

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    PageBreak, KeepTogether, Preformatted,
)

ROOT = Path(__file__).resolve().parents[1]
INK = colors.HexColor('#18314A')
TEAL = colors.HexColor('#007F83')
MUTED = colors.HexColor('#617182')
PALE = colors.HexColor('#EFF5F8')
RULE = colors.HexColor('#D7E2E9')


def generate(output: Path, font_dir: Path):
    pdfmetrics.registerFont(TTFont('CN', str(font_dir / 'Deng.ttf')))
    pdfmetrics.registerFont(TTFont('CNBold', str(font_dir / 'Dengb.ttf')))
    pdfmetrics.registerFontFamily('CN', normal='CN', bold='CNBold')
    style = {
        'title': ParagraphStyle('title', fontName='CNBold', fontSize=25, leading=32, textColor=INK, spaceAfter=8),
        'sub': ParagraphStyle('sub', fontName='CN', fontSize=10.5, leading=16, textColor=MUTED, spaceAfter=12),
        'h': ParagraphStyle('h', fontName='CNBold', fontSize=13, leading=19, textColor=TEAL, spaceBefore=11, spaceAfter=6),
        'body': ParagraphStyle('body', fontName='CN', fontSize=10.2, leading=16.2, textColor=INK, wordWrap='CJK', spaceAfter=6),
        'small': ParagraphStyle('small', fontName='CN', fontSize=8.8, leading=13.4, textColor=MUTED, wordWrap='CJK', spaceAfter=4),
        'cell': ParagraphStyle('cell', fontName='CN', fontSize=9.2, leading=14, textColor=INK, wordWrap='CJK'),
        'headcell': ParagraphStyle('headcell', fontName='CNBold', fontSize=9.2, leading=14, textColor=colors.white, wordWrap='CJK'),
        'code': ParagraphStyle('code', fontName='Courier', fontSize=8.5, leading=12, textColor=INK, backColor=PALE, borderPadding=9, spaceBefore=4, spaceAfter=9),
    }
    story = []
    width = A4[0] - 92

    def p(text, kind='body'):
        return Paragraph(text, style[kind])

    def add(text, kind='body'):
        story.append(p(text, kind))

    def heading(text):
        add(text, 'h')

    def table(rows, widths):
        values = [[p(escape(str(c)), 'headcell' if i == 0 else 'cell') for c in row] for i, row in enumerate(rows)]
        t = Table(values, colWidths=[width * w for w in widths], hAlign='LEFT', repeatRows=1)
        t.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), INK),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, PALE]),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('LEFTPADDING', (0, 0), (-1, -1), 8),
            ('RIGHTPADDING', (0, 0), (-1, -1), 8),
            ('TOPPADDING', (0, 0), (-1, -1), 7),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 7),
            ('LINEBELOW', (0, 0), (-1, 0), 0.6, INK),
            ('LINEBELOW', (0, 1), (-1, -1), 0.4, RULE),
        ]))
        story.append(t)
        story.append(Spacer(1, 6))

    def step(number, title, operations, check):
        story.append(KeepTogether([
            p(f'{number:02d}  {title}', 'h'),
            p(operations),
            p(f'<b>通过标准：</b>{check}'),
        ]))

    def code(text):
        story.append(Preformatted(text, style['code']))

    # Page 1: scope and resources.
    add('MineCreator', 'title')
    add('Stage 1 技术实施简要说明', 'title')
    add('版本 0.1  |  2026-10-04  |  Windows / Minecraft Java Edition', 'sub')
    add('<b>目标：</b>用户输入自然语言，LLM 通过 API 产生结构化建筑操作；程序从后台创建、修改指定区域，并读取游戏世界验证结果。')
    add('<b>完成示例：</b>“在测试区域建一面石砖墙”，随后“只把最上面一层换成橡木”。两次操作均能在官方 Java 客户端中看到，且读回数据与计划一致。')
    heading('管线与职责')
    table([
        ['组件', '职责'],
        ['MineCreator + LLM API', '我们编写轻量对话与工具调用流程；模型提出操作，程序校验并执行。'],
        ['MCPQ Python 客户端 / 插件', '通过 gRPC 将程序连接到 Paper，提供方块及区域读写接口。[2][3]'],
        ['本地 Paper + 官方 Java 客户端', 'Paper 运行世界；客户端连接本机服务器并显示变化。Paper 是服务端软件。[1]'],
    ], [0.36, 0.64])
    add('数据流：用户输入 → MineCreator（调用 LLM API）→ 校验后的操作 → MCPQ → Paper → 读回检查。', 'small')
    heading('所需资源')
    table([
        ['资源', '准备内容'],
        ['Minecraft / Paper / Java', '官方正版客户端；Paper JAR；匹配服务端版本的 Java 运行时。'],
        ['Python / MCPQ', 'Python 3.10+、虚拟环境、mcpq 包与兼容的 Paper 版 MCPQ 插件。[2][4]'],
        ['模型 API', '支持工具调用或结构化输出的模型；API Key、接口地址、模型名及少量测试额度。供应商待选。'],
        ['测试数据 / GitHub', '独立测试世界、限定施工区域；现有 GitHub 仓库保存代码、模板和文档。'],
    ], [0.28, 0.72])
    add('<b>范围：</b>先做 CLI、方块与小区域操作。本阶段暂不包含完整 GUI、概念图、档案库或大型建筑；“区域修改”指方块编辑，不涉及重写 Chunk 地形生成器。', 'small')

    # Page 2: setup and deterministic smoke test.
    story.append(PageBreak())
    add('环境与基础连接', 'title')
    add('先验证程序能够修改世界，再接入模型。每一步通过后再继续。', 'sub')
    step(1, '确认并记录兼容版本',
         '核对 Minecraft 客户端、Paper、MCPQ 插件与 Python 包的支持范围，固定版本并记录下载来源。Paper 1.20 至 1.21.11 对应 Java 21；26.1+ 对应 Java 25。[1] 优先验证明确支持的版本组合，不把插件文档的“1.20.1+”视为全部未来版本的保证。[2]',
         'java -version 与 Python 版本可确认；选定的客户端版本与 Paper 一致。')
    step(2, '启动本地 Paper 测试世界',
         '在 runtime/server/ 放入下载的 Paper JAR，首次启动生成配置；由用户阅读并同意 Minecraft EULA 后，手动设置 eula=true。保持正版登录验证，创建独立测试世界；运行服务端，再从客户端“多人游戏”连接 localhost:25565。',
         '控制台启动完成；官方 Java 客户端能进入世界。测试世界尚未承载重要存档。')
    code('java -Xms2G -Xmx4G -jar paper.jar --nogui')
    add('命令为示例：在 runtime/server/ 执行，JAR 名称与内存分配按下载文件和本机资源调整。', 'small')
    step(3, '安装并确认 MCPQ 接口',
         '停服后将兼容的 MCPQ Paper 插件放入 plugins/，重启并检查加载日志。确认 plugins/mcpq/config.yml：接口仅监听 localhost，默认端口 1789；不要开放公网。为 MineCreator 建立 .venv 并安装固定版本的 mcpq。[2][4]',
         'Python 客户端能连接接口并读取指定维度中的已知方块；25565 是游戏端口，1789 是程序接口端口。')
    code('python -m venv .venv\n.\\.venv\\Scripts\\python.exe -m pip install mcpq==<verified-version>')
    add('安装命令中的 verified-version 必须替换为联调确认的实际版本；最终写入依赖清单。', 'small')
    step(4, '完成无模型的读写冒烟验证',
         '在玩家附近选一块已加载、清晰标记的测试区域。先读取并保存原方块状态，再用 getBlockWithData / setBlock 创建一个方块；用 setBlockCube 建一小段墙，随后替换顶层。读回比较，并用原状态验证恢复。[3]',
         '创建、修改和恢复均可见；维度、坐标、方块类型与状态一致。此时不消耗模型 token。')

    # Page 3: app / model interface.
    story.append(PageBreak())
    add('LLM API 与执行闭环', 'title')
    add('模型提出结构化操作，施工由程序完成；用户确认与执行结果都有记录。', 'sub')
    step(5, '先独立验证模型 API',
         '选定供应商及模型，使用其官方 SDK 或接口。将 MODEL_API_KEY、MODEL_BASE_URL、MODEL_NAME 放入本地环境变量或 .env，提交无密钥的 .env.example。先做一次简单请求，再验证工具调用或符合 schema 的结构化输出。',
         'API 请求成功；程序能解析响应；鉴权、额度、模型名和超时错误有明确提示。')
    heading('第一版的工具接口：以下为我们拟定义的包装层')
    table([
        ['工具', '主要输入 / 行为'],
        ['inspect_region', 'dimension、两个角点；读取限定区域的方块与状态，返回必要摘要。'],
        ['set_block / fill_region', '维度、坐标、block_id 与 block states；放置方块或填充小区域。'],
        ['verify_edit', 'edit_id；读回受影响的位置，比较预期与实际，返回差异。'],
        ['restore_edit', 'edit_id；恢复本次记录的原状态。首版仅保证已测试的简单静态方块。'],
    ], [0.31, 0.69])
    step(6, '实现校验、计划确认与确定性执行',
         '把模型输出解析为操作列表；校验维度、整数坐标、区域边界、材料白名单及修改量。先展示坐标、材料与预计方块数，经用户确认后保存旧状态并批量执行。模型不能直接运行任意 Python、Shell 或服务器命令。',
         '合法计划能执行；越界、非法材料、超额和格式错误均在修改世界之前拒绝。')
    add('<b>起始限制建议：</b>单次最多影响 512 个唯一位置、限定一个测试区域、单轮最多 6 次模型请求；数值均为初始工程设置，可在联调后调整。LLM API 调用不阻塞游戏主线程。', 'small')
    step(7, '闭环检查、日志与重复执行处理',
         '保存 edit_id、设计版本、请求摘要、操作、原状态、预期状态及读回结果。执行后调用 verify_edit，将摘要反馈给模型。超时先查询实际状态，确认差异再修复，避免整批盲目重放；错误重试有次数和费用上限。',
         'LLM 的完成答复由读回证据支持；重复 edit_id 不会再次产生同一施工任务。')
    heading('自然语言验收场景')
    add('第一次：“在已标记测试区创建一面 5 格宽、3 格高的石砖墙。”第二次：“只把最上面一层换成橡木板，其余保持不变。”程序应验证 15 个目标位置，其中 5 个在第二次变化，另 10 个保持石砖。')
    add('MCPQ 是 Minecraft Protobuf Queries，通过 gRPC 通信；它与 Model Context Protocol（MCP）不同。第一版可直接调用 MCPQ，无需另加 MCP 或完整 agent 平台。[2][4]', 'small')

    # Page 4: operational cautions, troubleshooting, and completion evidence.
    story.append(PageBreak())
    add('注意事项与验收清单', 'title')
    add('先完成一个可验证的小闭环，再扩大建筑规模。', 'sub')
    heading('实施时必须处理的细节')
    add('<b>坐标与状态：</b>明确维度和建筑原点，约定 x/y/z 方向；两个角点均包含在 setBlockCube 的范围内，尺寸计算要加 1。验证楼梯朝向、上下半砖等状态时，使用带数据的读取接口。[3]')
    add('<b>区块与物理：</b>先在已加载区域测试，再专门验证跨 Chunk 或未加载区域。液体、重力方块、红石、门和容器会引入更新或额外数据；第一版采用简单静态材料，不能承诺完整世界级撤销。')
    add('<b>权限与费用：</b>本地接口只供本机调用；API Key 不进入 Git 或日志。批量施工本身不消耗 LLM token；记录每轮模型用量，只回传有限区域摘要，不把完整世界塞进上下文。')
    add('<b>持久化与备份：</b>代码、锁文件、配置模板、文档提交并推送 GitHub。runtime/、data/ 和世界存档另做备份；停服后复制所有维度的完整世界目录作为一致快照。服务器用 stop 正常退出，重启后确认修改仍在。')
    heading('常见失败：按层定位')
    table([
        ['现象', '优先检查'],
        ['客户端无法进入世界', 'Paper 是否启动、客户端版本、游戏端口、正版登录与服务端日志。'],
        ['Python 接口拒绝连接', 'MCPQ 是否成功加载、host/port、插件与客户端协议兼容性。'],
        ['修改位置或结果不对', '世界维度、原点和角点、区块加载、方块状态及物理更新。'],
        ['LLM 输出无效 / API 超时', '模型工具能力、schema、鉴权与额度；超时后先读回，再决定是否重试。'],
    ], [0.36, 0.64])
    heading('Stage 1 完成标准')
    add('1. 官方 Java 客户端能进入本地 Paper 世界；Python 可读写并读回验证。<br/>2. 两次自然语言指令完成“创建墙 → 局部修改”，结果与预期一致。<br/>3. 非法或越界操作被拒绝；测试范围内的原状态能够恢复。<br/>4. 停服重启后修改保留；版本配置与代码已推送 GitHub，运行日志另存本地。')
    heading('资料与状态')
    sources = [
        ('[1] Paper：运行环境、下载与启动', 'https://docs.papermc.io/paper/getting-started/'),
        ('[2] MCPQ 插件：安装、版本与端口配置', 'https://github.com/mcpq/mcpq-plugin'),
        ('[3] MCPQ World API：读写、状态与区域接口', 'https://mcpq.github.io/mcpq-python/classes/world.html'),
        ('[4] MCPQ Python：安装及客户端兼容说明', 'https://github.com/mcpq/mcpq-python'),
    ]
    for title, url in sources:
        add(f'<link href="{url}" color="#007F83">{title}</link>', 'small')
    add('资料核对日期：2026-10-04。本文是待实施方案；Paper/MCPQ 尚未在本机联调，最终版本与模型供应商待确认。命令为操作示例，验收标准不代表功能已完成。', 'small')

    output.parent.mkdir(parents=True, exist_ok=True)

    def decorate(canvas, doc):
        canvas.saveState()
        canvas.setStrokeColor(RULE)
        canvas.line(46, A4[1] - 31, A4[0] - 46, A4[1] - 31)
        canvas.setFont('CN', 8)
        canvas.setFillColor(MUTED)
        canvas.drawString(46, A4[1] - 23, 'MineCreator / Stage 1')
        canvas.drawRightString(A4[0] - 46, A4[1] - 23, '技术实施说明  v0.1')
        canvas.line(46, 34, A4[0] - 46, 34)
        canvas.drawString(46, 22, '2026-10-04  |  待实施方案')
        canvas.drawRightString(A4[0] - 46, 22, f'{doc.page} / 4')
        canvas.restoreState()

    doc = SimpleDocTemplate(
        str(output), pagesize=A4, rightMargin=46, leftMargin=46,
        topMargin=48, bottomMargin=48,
        title='MineCreator Stage 1 技术实施简要说明', author='MineCreator',
        subject='Paper + MCPQ + LLM API 接入、资源、实施步骤与验收标准',
    )
    doc.build(story, onFirstPage=decorate, onLaterPages=decorate)
    print(output)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=ROOT / 'output/pdf/MineCreator_Stage1_Technical_Guide.pdf')
    parser.add_argument('--font-dir', type=Path, default=Path('C:/Windows/Fonts'))
    args = parser.parse_args()
    generate(args.output, args.font_dir)
