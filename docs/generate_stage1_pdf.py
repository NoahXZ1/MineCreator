"""Generate the one-page Chinese Stage 1 guide. Requires reportlab."""
from pathlib import Path
from html import escape
import argparse
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Paragraph, Table, TableStyle

ROOT = Path(__file__).resolve().parents[1]
INK = colors.HexColor('#18314A')
TEAL = colors.HexColor('#007F83')
MUTED = colors.HexColor('#617182')
PALE = colors.HexColor('#EFF5F8')


def generate(output: Path, font_dir: Path):
    pdfmetrics.registerFont(TTFont('CN', str(font_dir / 'Deng.ttf')))
    pdfmetrics.registerFont(TTFont('CNBold', str(font_dir / 'Dengb.ttf')))
    pdfmetrics.registerFontFamily('CN', normal='CN', bold='CNBold')
    styles = {
        'title': ParagraphStyle('title', fontName='CNBold', fontSize=20, leading=26, textColor=INK, spaceAfter=6),
        'body': ParagraphStyle('body', fontName='CN', fontSize=9.7, leading=15, textColor=INK, wordWrap='CJK', spaceAfter=5),
        'h': ParagraphStyle('h', fontName='CNBold', fontSize=12, leading=17, textColor=TEAL, spaceBefore=9, spaceAfter=5),
        'cell': ParagraphStyle('cell', fontName='CN', fontSize=9.4, leading=14.2, textColor=INK, wordWrap='CJK'),
        'head': ParagraphStyle('head', fontName='CNBold', fontSize=9.4, leading=14.2, textColor=colors.white),
        'small': ParagraphStyle('small', fontName='CN', fontSize=8.3, leading=12.5, textColor=MUTED, wordWrap='CJK', spaceAfter=3),
    }
    story = []

    def add(text, style='body'):
        story.append(Paragraph(text, styles[style]))

    add('MineCreator | Stage 1 技术说明', 'title')
    add('一页版  v0.2  |  2026-10-04  |  待实施方案', 'small')
    add('<b>目标：</b>通过 LLM API，从后台创建与修改 Minecraft 方块，并读回验证。验收示例：一句话创建石砖墙，再一句话只替换顶层材料。')
    add('<b>管线：</b>用户输入 → MineCreator 调用 LLM → 校验操作 → MCPQ → Paper 世界；官方 Java 客户端连接本地服务器，查看结果。')
    add('所需资源', 'h')
    add('官方 Java 客户端、Paper JAR、匹配的 Java 运行时、Python 3.10+、MCPQ Paper 插件与 Python 包；模型 API Key、接口地址、模型名和少量测试额度；独立测试世界与现有 GitHub 仓库。')
    add('主要步骤', 'h')
    rows = [
        ['步骤', '操作与通过标准'],
        ['1. 固定版本', '核对 Minecraft、Paper、MCPQ 的兼容性并记录版本。Paper 1.20 至 1.21.11 使用 Java 21；26.1+ 使用 Java 25。[1]'],
        ['2. 启动世界', '在 runtime/server/ 启动 Paper。用户阅读并同意 EULA 后设置 eula=true；官方客户端能连接 localhost:25565。'],
        ['3. 接通 MCPQ', '停服后把插件放进 plugins/，重启确认加载。Python 虚拟环境安装 mcpq；连接本机默认 1789 端口并读取已知方块。[2]'],
        ['4. 验证程序读写', '在已加载测试区保存原状态，创建方块、填充小区域、替换顶层；读回结果一致，并验证恢复。先不调用模型。[3]'],
        ['5. 接入 LLM API', '本地配置密钥与模型；验证简单请求、工具调用或结构化输出。程序能解析响应，并报告鉴权与额度错误。'],
        ['6. 执行模型操作', '包装读取、放置、区域填充工具。校验坐标、材料和修改量；展示计划并确认后执行，保存 edit_id 与旧状态。'],
        ['7. 完成闭环', '执行后读回比较，记录差异和模型用量。完成“创建墙 → 局部修改”，并验证停服重启后结果保留。'],
    ]
    cells = [[Paragraph(escape(c), styles['head' if i == 0 else 'cell']) for c in row] for i, row in enumerate(rows)]
    table = Table(cells, colWidths=[94, A4[0] - 92 - 94], hAlign='LEFT')
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), INK),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, PALE]),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 8),
        ('RIGHTPADDING', (0, 0), (-1, -1), 8),
        ('TOPPADDING', (0, 0), (-1, -1), 7),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 7),
        ('LINEBELOW', (0, 1), (-1, -1), 0.4, colors.HexColor('#D7E2E9')),
    ]))
    story.append(table)
    add('关键注意事项', 'h')
    add('<b>范围与状态：</b>限定测试区和修改量；明确维度、原点及角点包含规则。检查楼梯朝向等状态时用带数据的读取接口。首版用静态方块，不承诺液体、容器等完整撤销。')
    add('<b>执行与费用：</b>不执行模型生成的任意代码或命令；设置请求与重试上限。操作超时先读回，避免盲目重放；重复 edit_id 不重复施工。')
    add('<b>访问与备份：</b>MCPQ 仅监听本机；密钥不进 Git 或日志。代码与文档推送 GitHub；停服后另备份所有维度的世界目录。')
    links = [
        ('[1] Paper 启动与 Java 版本', 'https://docs.papermc.io/paper/getting-started/'),
        ('[2] MCPQ 插件与安装', 'https://github.com/mcpq/mcpq-plugin'),
        ('[3] MCPQ World API', 'https://mcpq.github.io/mcpq-python/classes/world.html'),
    ]
    add('资料：' + '　 '.join(f'<link href="{url}" color="#007F83">{title}</link>' for title, url in links), 'small')
    add('说明：Paper/MCPQ 尚未在本机联调；版本与模型待确认。MCPQ 使用 gRPC，与 Model Context Protocol（MCP）不同。GUI、概念图与建筑档案留待后续。', 'small')
    output.parent.mkdir(parents=True, exist_ok=True)

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setStrokeColor(colors.HexColor('#D7E2E9'))
        canvas.line(46, 33, A4[0] - 46, 33)
        canvas.setFont('CN', 8)
        canvas.setFillColor(MUTED)
        canvas.drawString(46, 21, 'MineCreator / Stage 1 / 待实施')
        canvas.drawRightString(A4[0] - 46, 21, f'{doc.page} / 1')
        canvas.restoreState()

    SimpleDocTemplate(
        str(output), pagesize=A4, leftMargin=46, rightMargin=46,
        topMargin=40, bottomMargin=45,
        title='MineCreator Stage 1 技术说明 - 一页版', author='MineCreator',
    ).build(story, onFirstPage=footer, onLaterPages=footer)
    print(output)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=ROOT / 'output/pdf/MineCreator_Stage1_Technical_Guide.pdf')
    parser.add_argument('--font-dir', type=Path, default=Path('C:/Windows/Fonts'))
    args = parser.parse_args()
    generate(args.output, args.font_dir)
