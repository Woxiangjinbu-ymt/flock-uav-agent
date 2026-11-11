"""Rebuild the editable, dependency-free SVG figures for the stage report."""
from pathlib import Path
from html import escape

OUT = Path(__file__).resolve().parent
COLORS = {
    'U': ('#f2f5f8', '#8a99ac', '上游 / 复用'),
    'I': ('#eaf4ff', '#3678b7', '已实现 · 有界'),
    'P': ('#fff5e3', '#b98632', '部分实现'),
    'D': ('#f5f0fc', '#9472b9', '设计 / 待实现'),
}

class Figure:
    def __init__(self, width, height, title, subtitle):
        self.width, self.height = width, height
        self.parts = [f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc">
<title id="title">{escape(title)}</title><desc id="desc">{escape(subtitle)}</desc>
<defs><marker id="arrow" markerWidth="9" markerHeight="9" refX="8" refY="4.5" orient="auto-start-reverse" markerUnits="userSpaceOnUse"><path d="M0 0L9 4.5L0 9Z" fill="#566c83"/></marker><marker id="purple" markerWidth="9" markerHeight="9" refX="8" refY="4.5" orient="auto" markerUnits="userSpaceOnUse"><path d="M0 0L9 4.5L0 9Z" fill="#9472b9"/></marker></defs>
<style>text{{font-family:-apple-system,BlinkMacSystemFont,'PingFang SC','Microsoft YaHei',Arial,sans-serif;fill:#1b3047}}.small{{font-size:17px;fill:#52677d}}.body{{font-size:20px}}.title{{font-size:22px;font-weight:650}}.edge-label{{font-size:16px;fill:#465f76;paint-order:stroke;stroke:#fff;stroke-width:7px;stroke-linejoin:round}}</style>
<rect width="100%" height="100%" fill="#fff"/>''']
        self.text(38, 48, title, 30, 700)
        self.text(38, 80, subtitle, 17, fill='#52677d')

    def text(self, x, y, s, size=20, weight=400, fill=None, anchor='start', cls=None):
        attrs = f'font-size="{size}" font-weight="{weight}" text-anchor="{anchor}"'
        if fill: attrs += f' style="fill:{fill}"'
        if cls: attrs += f' class="{cls}"'
        self.parts.append(f'<text x="{x}" y="{y}" {attrs}>{escape(s)}</text>')

    def rect(self, x, y, w, h, fill='#fff', stroke='#d7e1eb', dash=False, radius=14):
        self.parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{radius}" fill="{fill}" stroke="{stroke}" stroke-width="1.6"'+(' stroke-dasharray="8 5"' if dash else '')+'/>')

    def box(self, x, y, w, h, title, lines=(), state='U', size=20, badge=True):
        bg, border, tag = COLORS[state]
        self.parts.append(f'<g data-state="{state}">')
        self.rect(x, y, w, h, bg, border, state == 'D')
        self.text(x+20, y+34, title, 22, 650)
        line_start = y+60
        for i, line in enumerate(lines): self.text(x+20, line_start+i*25, line, size)
        if badge:
            self.text(x+w-17, y+h-12, state+' · '+tag, 15, 600, border, 'end')
        self.parts.append('</g>')

    def arrow(self, points, label=None, lx=None, ly=None, dashed=False, both=False):
        d = 'M'+' L'.join(f'{x},{y}' for x,y in points)
        self.parts.append(f'<path d="{d}" fill="none" stroke="'+('#9472b9' if dashed else '#566c83')+'" stroke-width="2" stroke-linejoin="round" marker-end="url(#'+('purple' if dashed else 'arrow')+')"'+(' stroke-dasharray="7 5"' if dashed else '')+(' marker-start="url(#arrow)"' if both else '')+'/>')
        if label: self.text(lx, ly, label, 16, cls='edge-label')

    def legend(self, y, start=38):
        for i, (state, (bg, border, label)) in enumerate(COLORS.items()):
            x=start+i*255
            self.rect(x,y-17,21,21,bg,border,state=='D',4)
            self.text(x+31,y,state+' '+label,16)
        self.text(start+1040,y,'实线：已有路径；虚线：拟议扩展',16,fill='#52677d')

    def save(self, name):
        p=OUT/(name+'.svg')
        p.write_text('\n'.join(self.parts)+'</svg>',encoding='utf-8')
        return p


def baseline():
    f=Figure(1680,700,'Agent4Drone baseline：论文 Fig. 4 的结构重绘',
             '依据 MultiUAV-Plat §3.3 / Fig. 4（2026，v1）｜概念模块，不代表六个独立 Agent；图中各组件均属上游方法描述。')
    f.rect(38,120,1604,510,'#f8fbff','#bed0e4')
    titles=['任务输入','观察','任务理解','规划','工具链落地','执行','验证']
    lines=[['指令与公开上下文'],['状态 / 局部感知'],['目标 / 对象 / 约束'],['子任务 / UAV 分配'],['有界动作链'],['动作 / 多机协作'],['进度 / 完成检查']]
    xs=[66,292,518,744,970,1196,1422]
    for x,t,l in zip(xs,titles,lines): f.box(x,165,192,112,t,l,badge=False,size=17)
    for i in range(6): f.arrow([(xs[i]+192,221),(xs[i+1],221)])
    f.box(292,367,650,115,'Memory · 共享黑板与交互记忆',
          ['局部观测、动作历史、中间结果、验证反馈'],badge=False)
    f.box(1020,367,594,115,'Decision Artifacts · 决策产物',
          ['子任务、UAV 分配、路线、工具链与停止条件'],badge=False)
    f.arrow([(942,425),(1020,425)],both=True)
    for x in [388,614,840]: f.arrow([(x,367),(x,277)])
    f.arrow([(1518,277),(1518,327),(865,327),(865,367)],'执行 / 验证反馈 → 更新记忆 → 重规划',1000,318)
    f.arrow([(1066,367),(1066,277)])
    f.box(292,530,1322,62,'外部交互边界：合法观测 ↔ 支持的动作 ↔ 多 UAV 环境执行反馈',badge=False)
    f.arrow([(1292,277),(1292,340),(1640,340),(1640,560),(1614,560)])
    f.text(42,671,'重绘只统一视觉语言；原论文已经包含短程规划、有界工具链、Memory 和 Verification，不能将这些概念本身视为本项目新增。',18)
    return f.save('agent4drone-baseline')


def complete():
    """Paper-style method overview; colors denote roles, not implementation status."""
    f=Figure(1680,1410,'公开证据驱动的多 UAV Agent Harness',
             'Evidence-Guided Multi-UAV Agent Harness · 计划提交、适用性执行与证据驱动接管')
    palette={
        'evidence':('#eef8f5','#548e80','证据与状态'),
        'decision':('#edf4fc','#507fac','决策与计划'),
        'execution':('#fff6e8','#b68b44','技能与执行'),
        'runtime':('#f2f5f8','#8a99ac','环境与运行'),
        'offline':('#f5f1fa','#9275ad','评估与学习'),
    }
    f.parts.append('<defs><marker id="evidence-arrow" markerWidth="9" markerHeight="9" refX="8" refY="4.5" orient="auto" markerUnits="userSpaceOnUse"><path d="M0 0L9 4.5L0 9Z" fill="#548e80"/></marker></defs>')

    def module(x,y,w,h,title,lines=(),role='decision',size=20):
        bg,border,_=palette[role]
        f.parts.append(f'<g data-role="{role}">')
        f.rect(x,y,w,h,bg,border)
        f.text(x+20,y+34,title,22,650)
        for i,line in enumerate(lines): f.text(x+20,y+69+i*29,line,size)
        f.parts.append('</g>')

    def flow(points,label=None,lx=None,ly=None,kind='control'):
        colors={'control':'#566c83','evidence':'#548e80','offline':'#9472b9'}
        markers={'control':'arrow','evidence':'evidence-arrow','offline':'purple'}
        d='M'+' L'.join(f'{x},{y}' for x,y in points)
        dash='' if kind=='control' else ' stroke-dasharray="7 5"'
        f.parts.append(f'<path d="{d}" fill="none" stroke="{colors[kind]}" stroke-width="2" stroke-linejoin="round" marker-end="url(#{markers[kind]})"{dash}/>')
        if label: f.text(lx,ly,label,16,cls='edge-label')

    for i,(role,(bg,border,label)) in enumerate(palette.items()):
        x=38+i*270
        f.rect(x,102,20,20,bg,border,radius=4)
        f.text(x+31,119,label,17)
    f.rect(38,148,1604,955,'#fcfdff','#d7e1eb')
    f.text(58,178,'在线决策与执行 · 集中式策略 / 多 UAV 协作 / 公开信息边界',18,600)
    module(60,202,340,123,'任务输入',[
        '自然语言指令与公开规则',
        '指定主体、任务目标与资源约束'],role='runtime',size=19)
    module(450,202,390,123,'任务义务与约束',[
        '主体 · 对象 · 动作 · 先后依赖',
        '来源条款 · 空间约束 · 最终状态'],role='evidence',size=19)
    module(900,202,680,123,'集中式策略 · LLM Planner',[
        '任务理解、UAV 分配、技能选择与重规划',
        'start / resume / cancel · 直接工具调用'],role='decision')
    flow([(400,258),(450,258)])
    flow([(840,258),(900,258)])

    # Public evidence supports both model context and programmatic applicability checks.
    module(60,385,440,348,'公开证据与任务状态',[
        '局部观测 · UAV 状态 · 动作回执',
        '来源 / 主体 / 时间 / 场景作用域',
        '已确认效果 / 部分效果 / 未知效果'],role='evidence')
    f.parts.append('<path d="M80,532 H480" stroke="#c2ddd5" stroke-width="1.4"/>')
    f.text(80,571,'决策上下文构建',21,650)
    for i,line in enumerate([
        '任务义务与执行证据匹配',
        '已完成步骤 · 剩余任务 · 相关失败',
        '有效依赖 · 未知信息 · 上下文投影']):
        f.text(80,610+i*29,line,20)
    module(560,385,470,155,'计划契约 · DecisionFrame',[
        '计划身份 · 执行主体 · 游标',
        '来源义务 · 证据依赖 · 有效条件',
        '修复范围 · 停止条件 · 执行预算'],role='decision')
    module(1090,385,490,155,'任务验证与决策交接',[
        '计划状态 · 退出原因 · 证据引用',
        '剩余义务与任务完成证据核对',
        '恢复 / 重规划 / 结束报告'],role='decision')
    flow([(1200,325),(1200,352),(795,352),(795,385)],'显式提交',930,345)
    flow([(1345,385),(1345,325)],'决策更新',1355,361)
    flow([(500,455),(527,455),(527,341),(870,341),(870,290),(900,290)],
         '决策上下文',563,335,kind='evidence')
    module(560,590,470,143,'适用性门控 · Applicability',[
        '检查计划依赖与局部修复条件',
        '继续执行 / 补充观察 / 失效交接',
        '停止条件与预算边界控制'],role='execution')
    module(1090,590,490,143,'有界技能与协作执行',[
        '顺序动作链 · 多 UAV 协同搜索',
        '状态修复 · 冲突证据共享',
        '已见目标跟进 · 进度确认'],role='execution')
    flow([(795,540),(795,590)])
    flow([(500,652),(560,652)],kind='evidence')
    flow([(1030,660),(1090,660)],'继续',1039,649)
    flow([(1030,623),(1060,623),(1060,458),(1090,458)],'失效 / 退出',1037,570)
    flow([(1335,590),(1335,540)],'技能退出',1348,568)

    module(60,790,440,120,'执行反馈与证据更新',[
        '动作回执关联 · 实际效果核对',
        '状态后读 · 证据追加 · 依赖更新'],role='evidence')
    module(560,790,1020,120,'公共工具与执行接口 · AGENT API',[
        '状态读取、局部感知、动作控制、导航与覆盖规划',
        '技能与直接工具共享执行边界；相关外部动作触发计划依赖失效'],role='execution')
    flow([(1335,733),(1335,790)])
    flow([(1580,270),(1615,270),(1615,845),(1580,845)])
    for i,char in enumerate('直接工具'): f.text(1596,536+i*23,char,15,600)
    flow([(560,850),(500,850)],'回执',510,840,kind='evidence')
    flow([(280,790),(280,733)],kind='evidence')
    # The full feedback loop is drawn explicitly, including dependencies used by the gate.
    module(60,970,440,120,'回合监督与资源管理',[
        '隔离执行 · 取消 / 截止 · 请求排空',
        '时间 / token / API / 修复与费用预算'],role='runtime',size=18)
    module(560,970,1020,120,'多 UAV 环境 · MultiUAV-Plat',[
        '多无人机实体、场景与任务动力学；动作执行、局部感知与公开状态反馈'],role='runtime')
    flow([(1000,910),(1000,970)],'动作',1014,947)
    flow([(1200,970),(1200,910)],'观测 / 回执',1214,947,kind='evidence')
    flow([(500,1015),(530,1015),(530,890),(560,890)],'执行约束',458,946)

    # Offline artifacts are outside the online policy information boundary.
    f.text(1090,1139,'离线评估与轨迹学习接口',19,650)
    module(60,1166,440,142,'交互轨迹归档',[
        '模型请求 / 响应 / 工具调用关联',
        '计划状态、执行回执与事件序列',
        '完整性校验与回合封存'],role='offline',size=18)
    module(560,1166,470,142,'独立任务评估',[
        '隔离检查器 · 隐藏任务验证',
        '成功率、进度、token、时间与动作',
        '私有真值仅用于离线评价'],role='offline',size=18)
    module(1090,1166,490,142,'训练样本与策略优化接口',[
        '轨迹转换、质量筛选与监督掩码',
        '公开上下文 + 真实模型决策',
        '版本化数据 → 学生策略训练'],role='offline',size=18)
    flow([(280,1090),(280,1166)],'回合封存',293,1118,kind='offline')
    flow([(795,1090),(795,1166)],'冻结世界',808,1118,kind='offline')
    flow([(500,1237),(530,1237),(530,1330),(1060,1330),(1060,1237),(1090,1237)],
         '策略可见轨迹',690,1323,kind='offline')
    flow([(1030,1220),(1090,1220)],kind='offline')
    f.text(1098,1157,'评估结果用于样本质量标注',15,fill='#77618d')
    # Flow semantics stay meaningful in a standalone exported figure.
    flow([(60,1380),(126,1380)])
    f.text(140,1386,'决策与执行控制',17)
    flow([(420,1380),(486,1380)],kind='evidence')
    f.text(500,1386,'公开证据与反馈',17)
    flow([(800,1380),(866,1380)],kind='offline')
    f.text(880,1386,'离线评估与数据流',17)
    f.text(1190,1386,'隐藏检查不进入在线策略输入',17,600)
    return f.save('harness-complete-architecture')


def comparison():
    """Method comparison at the workflow level, independent of implementation status."""
    f=Figure(1680,1110,'Agent4Drone 与公开证据驱动 Harness 的方法对比',
             '短程任务闭环与显式执行契约 · 统一公共接口、局部观测与独立任务评价边界')
    palette={
        'evidence':('#eef8f5','#548e80','证据与状态'),
        'decision':('#edf4fc','#507fac','决策与计划'),
        'execution':('#fff6e8','#b68b44','技能与执行'),
        'runtime':('#f2f5f8','#8a99ac','环境与运行'),
        'offline':('#f5f1fa','#9275ad','评估与学习'),
    }
    f.parts.append('<defs><marker id="evidence-arrow" markerWidth="9" markerHeight="9" refX="8" refY="4.5" orient="auto" markerUnits="userSpaceOnUse"><path d="M0 0L9 4.5L0 9Z" fill="#548e80"/></marker></defs>')

    def module(x,y,w,h,title,lines=(),role='decision',size=19):
        bg,border,_=palette[role]
        f.parts.append(f'<g data-role="{role}">')
        f.rect(x,y,w,h,bg,border)
        f.text(x+20,y+35,title,22,650)
        for i,line in enumerate(lines): f.text(x+20,y+72+i*27,line,size)
        f.parts.append('</g>')

    def feedback(points,label=None,lx=None,ly=None):
        d='M'+' L'.join(f'{x},{y}' for x,y in points)
        f.parts.append(f'<path d="{d}" fill="none" stroke="#548e80" stroke-width="2" stroke-dasharray="7 5" stroke-linejoin="round" marker-end="url(#evidence-arrow)"/>')
        if label:f.text(lx,ly,label,16,cls='edge-label')

    for i,(role,(bg,border,label)) in enumerate(palette.items()):
        x=38+i*270
        f.rect(x,102,20,20,bg,border,radius=4)
        f.text(x+31,119,label,17)
    f.rect(38,150,746,760,'#fcfdff','#c7d6e5')
    f.rect(816,150,826,760,'#fcfdff','#c7d6e5')
    f.text(66,191,'(a) Agent4Drone',25,700)
    f.text(844,191,'(b) Evidence-Guided Harness',25,700)

    module(70,225,680,106,'观察、任务理解与规划',[
        '公开上下文、任务约束、UAV 分配与短程子任务'],role='decision')
    module(70,375,680,106,'工具链落地与多机执行',[
        '有界工具链 · 动作执行 · 协作控制'],role='execution')
    module(70,525,680,106,'状态验证与反馈决策',[
        '预期 / 实际状态比较 → 完成、重试或重规划'],role='decision')
    module(70,675,680,106,'Memory 与 Decision Artifacts',[
        '局部观测、动作历史、分配、工具链与停止条件'],role='evidence')
    f.arrow([(410,331),(410,375)])
    f.arrow([(410,481),(410,525)])
    feedback([(410,631),(410,675)],'执行与验证反馈',426,660)
    feedback([(70,728),(52,728),(52,278),(70,278)])
    f.text(70,834,'反馈驱动的短程任务闭环',22,650)
    f.text(70,870,'共享记忆连接观察、规划、执行与验证。',19)

    module(850,225,756,106,'任务义务与公开证据 → 决策上下文',[
        '来源与作用域 · 已确认 / 未知效果 · 已完成 / 剩余义务'],role='evidence')
    module(850,375,756,106,'模型显式提交 → 计划契约 DecisionFrame',[
        '计划身份、主体、游标、证据依赖、适用条件与执行预算'],role='decision')
    module(850,525,756,106,'适用性门控 → 有界技能与协作执行',[
        '有效则继续或局部修复；依赖失效、效果不明或耗尽则交回'],role='execution')
    module(850,675,756,106,'任务验证 → 交接、重规划与完成报告',[
        '状态与原因 · 执行证据 · 剩余义务 · 可用后续控制'],role='decision')
    feedback([(1228,331),(1228,375)],'上下文输入',1244,360)
    f.arrow([(1228,481),(1228,525)])
    f.arrow([(1228,631),(1228,675)],'失效 / 技能退出',1244,660)
    feedback([(1606,728),(1624,728),(1624,278),(1606,278)])
    # A bounded skill's continuation loop stays inside the harness.
    f.arrow([(850,592),(834,592),(834,551),(850,551)])
    module(850,815,756,73,'轨迹归档 → 独立评估 → 训练样本接口',role='offline')
    f.arrow([(1228,781),(1228,815)],dashed=True)

    f.rect(38,944,1604,104,'#f2f5f8','#b9c8d7')
    f.text(64,983,'共同底座：MultiUAV-Plat · 公共动作与感知接口 · 多 UAV 场景 · 独立任务评估',23,650)
    f.text(64,1021,'方法扩展：将证据、计划有效性与控制权交接组织为显式契约，区分程序内继续执行与模型重新决策。',21)
    f.arrow([(60,1082),(126,1082)])
    f.text(140,1088,'决策与执行控制',17)
    feedback([(450,1082),(516,1082)])
    f.text(530,1088,'公开信息与执行反馈',17)
    f.arrow([(900,1082),(966,1082)],dashed=True)
    f.text(980,1088,'离线评估与数据流',17)
    f.text(1360,1088,'(a) 依据论文 Fig. 4',16,fill='#52677d')
    return f.save('architecture-comparison')


if __name__=='__main__':
    for fn in [baseline,complete,comparison]: print(fn())
