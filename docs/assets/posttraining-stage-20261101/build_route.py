"""Render the Stage II method diagram as editable SVG, using only the stdlib."""
from html import escape
from pathlib import Path
import re

OUT = Path(__file__).resolve().parent
PALETTE = {
    'evidence': ('#eef8f5', '#548e80', '证据与数据'),
    'decision': ('#edf4fc', '#507fac', '模型与策略'),
    'verification': ('#fff6e8', '#b68b44', '验证与评测'),
    'runtime': ('#f2f5f8', '#8a99ac', '环境与协议'),
    'training': ('#f5f1fa', '#9275ad', '训练与优化'),
}


class Figure:
    def __init__(self):
        title = 'Harness 引导的多无人机小模型后训练路线'
        desc = '真实 Teacher 交互 → 执行感知的数据验证 → Student SFT → 回接评测；Agentic RL 作为条件分支。'
        self.parts = [f'''<svg xmlns="http://www.w3.org/2000/svg" width="1680" height="1430" viewBox="0 0 1680 1430" role="img" aria-labelledby="title desc">
<title id="title">{title}</title><desc id="desc">{desc}</desc>
<defs>
<marker id="arrow" markerWidth="9" markerHeight="9" refX="8" refY="4.5" orient="auto" markerUnits="userSpaceOnUse"><path d="M0 0L9 4.5L0 9Z" fill="#566c83"/></marker>
<marker id="feedback" markerWidth="9" markerHeight="9" refX="8" refY="4.5" orient="auto" markerUnits="userSpaceOnUse"><path d="M0 0L9 4.5L0 9Z" fill="#548e80"/></marker>
<marker id="optional" markerWidth="9" markerHeight="9" refX="8" refY="4.5" orient="auto" markerUnits="userSpaceOnUse"><path d="M0 0L9 4.5L0 9Z" fill="#9275ad"/></marker>
</defs>
<style>text{{font-family:-apple-system,BlinkMacSystemFont,'PingFang SC','Microsoft YaHei',Arial,sans-serif;fill:#1b3047}}.edge-label{{font-size:16px;fill:#465f76;paint-order:stroke;stroke:#fff;stroke-width:7px;stroke-linejoin:round}}</style>
<rect width="100%" height="100%" fill="#fff"/>''']
        self.text(38,48,title,30,700)
        self.text(38,80,'Harness-guided Small Language Model Post-training · 数据构建、监督迁移与条件式策略优化',17,fill='#52677d')
        for i,(bg,border,label) in enumerate(PALETTE.values()):
            x=38+i*270
            self.rect(x,102,20,20,bg,border,radius=4)
            self.text(x+31,119,label,17)

    def text(self,x,y,text,size=20,weight=400,fill=None,cls=None):
        attrs=f'font-size="{size}" font-weight="{weight}"'
        if fill: attrs+=f' style="fill:{fill}"'
        if cls: attrs+=f' class="{cls}"'
        self.parts.append(f'<text x="{x}" y="{y}" {attrs}>{escape(text)}</text>')

    def rect(self,x,y,w,h,fill,stroke,radius=14,dashed=False):
        dash=' stroke-dasharray="8 5"' if dashed else ''
        self.parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{radius}" fill="{fill}" stroke="{stroke}" stroke-width="1.6"{dash}/>')

    def module(self,x,y,w,h,title,lines=(),role='decision',size=20,dashed=False):
        bg,border,_=PALETTE[role]
        self.parts.append(f'<g data-role="{role}">')
        self.rect(x,y,w,h,bg,border,dashed=dashed)
        self.text(x+20,y+35,title,22,650)
        leading=27 if h<=140 else 29
        for i,line in enumerate(lines):self.text(x+20,y+71+i*leading,line,size)
        self.parts.append('</g>')

    def flow(self,points,label=None,lx=None,ly=None,kind='main'):
        colors={'main':'#566c83','feedback':'#548e80','optional':'#9275ad'}
        marker='arrow' if kind=='main' else kind
        d='M'+' L'.join(f'{x},{y}' for x,y in points)
        dash='' if kind=='main' else ' stroke-dasharray="7 5"'
        self.parts.append(f'<path d="{d}" fill="none" stroke="{colors[kind]}" stroke-width="2" stroke-linejoin="round" marker-end="url(#{marker})"{dash}/>')
        if label:self.text(lx,ly,label,16,cls='edge-label')

    def save(self):
        p=OUT/'posttraining-route.svg'
        p.write_text('\n'.join(self.parts)+'</svg>',encoding='utf-8')
        return p


def build():
    f=Figure()
    f.module(38,151,1604,137,'任务协议与独立划分 · 采集前冻结',[
        '决策职责、公开输入、工具权限、场景 / 种子 / 任务族、Harness 版本与执行预算',
        'Train：采样与训练　｜　Dev：选择配置、分析错误与修订数据　｜　Test：模型与协议冻结后统一评价'
    ],role='runtime')
    f.text(60,329,'WP1 / 执行感知的 Teacher 轨迹构建',19,650)
    f.module(60,350,440,315,'共享 Harness 与多 UAV 环境',[
        '公开任务、局部观测与执行证据',
        '计划契约、适用性门控与宏技能',
        '工具执行、局部修复与模型接管',
        '独立回合、时限、归档与请求预算',
        'Teacher / Student 复用执行语义',
        '私有状态仅供隔离评分 / 奖励'
    ],role='runtime',size=19)
    f.module(560,350,470,130,'Teacher Agent · 真实交互',[
        '理解、规划、分配、工具与恢复决策',
        '冻结模型身份、采样参数与调用预算'
    ],role='decision',size=19)
    f.module(560,535,470,130,'回合轨迹与模型 I/O',[
        '消息顺序、调用 ID、并行关系与回执',
        '模型决策 / Harness 内部执行分开'
    ],role='evidence',size=19)
    f.module(1090,350,490,315,'Execution-aware Verification',[
        'L1 结构：消息、Schema、调用关联',
        'L2 执行：参数、动作效果与重复',
        'L3 任务：隔离 Scorer 与义务满足',
        'L4 质量：冗余、恢复价值与可解释性',
        '保存质量标签、异常类型与筛选理由',
        '隐藏检查不进入模型输入与示范'
    ],role='verification',size=19)
    f.flow([(470,288),(470,350)])
    f.flow([(795,288),(795,350)])
    f.flow([(500,409),(560,409)],'观测',508,399)
    f.flow([(560,454),(500,454)],'动作',508,444)
    f.flow([(795,480),(795,535)],'交互归档',809,515)
    f.flow([(1030,600),(1090,600)])
    f.module(60,745,440,140,'失败、部分完成与排除记录',[
        '保留来源、质量标签及排除理由',
        '用于诊断、纠错与任务设计',
        '不默认混入正向 SFT 标签'
    ],role='runtime',size=18)
    f.module(560,745,470,140,'版本化决策数据集',[
        '成功 / 有效恢复 / 接管等能力标签',
        '公开上下文 + 审核后的模型输出',
        '训练 / 开发划分 · 输入预算 · Loss Mask'
    ],role='evidence',size=18)
    f.module(1090,745,490,140,'WP2 / 约 2B Student SFT',[
        '只监督有效 assistant 决策与工具调用',
        '模板适配、参数高效微调与检查点导出',
        '环境回执和技能内部动作不作生成标签'
    ],role='training',size=18)
    f.flow([(1210,665),(1210,694),(280,694),(280,745)],'分流与追溯',610,686)
    f.flow([(1400,665),(1400,715),(795,715),(795,745)],'验证通过的决策样本',1040,708)
    f.flow([(1030,815),(1090,815)])
    f.text(60,945,'回接、验证与策略优化',19,650)
    f.module(1090,970,490,140,'Student 回接同一 Harness',[
        '模型替换；固定公开权限与技能语义',
        '多轮工具调用、异常处理与停止判断',
        '导出模型 / Adapter 与推理配置留档'
    ],role='decision',size=18)
    f.module(560,970,470,140,'开发集验证与错误分析',[
        '同 Harness：基座 vs SFT',
        '任务成功、工具正确性、恢复与成本',
        '检查输入、标签、分布偏移与策略回归'
    ],role='verification',size=18)
    f.module(60,970,440,140,'RL 准入：奖励、采样与成本',[
        '可用 SFT 起点与有效交互信号',
        '奖励语义、回合隔离与反例检验',
        '训练器适配、吞吐与预算可承担'
    ],role='verification',size=18)
    f.flow([(1335,885),(1335,970)],'导出与部署',1348,933)
    f.flow([(1090,1040),(1030,1040)])
    f.flow([(560,1040),(500,1040)],kind='optional')
    f.flow([(760,970),(760,924),(535,924),(535,815),(560,815)],
           '仅开发集诊断回流',557,918,kind='feedback')
    f.module(60,1190,440,140,'WP3 / Agentic RL · 条件分支',[
        'Student rollout ↔ 隔离奖励 ↔ 更新',
        '仅优化真实模型生成的决策序列',
        '与固定 SFT 起点对照'
    ],role='training',size=18,dashed=True)
    f.module(560,1190,1020,140,'WP4 / 冻结后的统一评估与贡献归因',[
        '基座 / SFT / SFT+RL（如开展）· 同 Harness · 新独立测试 · 关键系统与数据消融',
        '主指标：端到端任务成功；同时报告工具正确性、恢复、泛化、token、时间、动作与训练成本',
        'Test 不参与数据生成、样本筛选、调参与反复选择；各模型使用相同初态与评估协议'
    ],role='verification',size=18)
    f.flow([(280,1110),(280,1190)],'通过准入后',293,1159,kind='optional')
    f.flow([(795,1110),(795,1190)],'SFT 路径可直接交付',810,1159)
    f.flow([(500,1260),(560,1260)],kind='optional')
    f.flow([(60,1385),(126,1385)])
    f.text(140,1391,'核心研究路线',17)
    f.flow([(430,1385),(496,1385)],kind='feedback')
    f.text(510,1391,'开发反馈与数据修订',17)
    f.flow([(900,1385),(966,1385)],kind='optional')
    f.text(980,1391,'条件式 RL 路径',17)
    f.text(1270,1391,'能力迁移优先通过 SFT 实现',17,600)
    return f.save()


def embed(report):
    svg=(OUT/'posttraining-route.svg').read_text()
    prefix='posttraining-route'
    for ident in ['title','desc','arrow','feedback','optional']:
        svg=svg.replace(f'id="{ident}"',f'id="{prefix}-{ident}"').replace(f'url(#{ident})',f'url(#{prefix}-{ident})')
    svg=svg.replace('aria-labelledby="title desc"',f'aria-labelledby="{prefix}-title {prefix}-desc"')
    html=report.read_text()
    if '@@ROUTE@@' in html:
        html=html.replace('@@ROUTE@@',svg)
    else:
        html=re.sub(r'(<figure class="fig" id="fig-route"><div class="fig-shell">)<svg.*?</svg>',lambda m:m.group(1)+svg,html,flags=re.S)
    report.write_text(html)


if __name__=='__main__':
    print(build())
    report=OUT.parents[1]/'POSTTRAINING_STAGE_REPORT_CN.html'
    if report.exists():embed(report)
