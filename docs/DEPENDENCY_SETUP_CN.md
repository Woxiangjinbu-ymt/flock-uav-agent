# 依赖修复与本机运行环境

更新日期：2026-09-22。基于原始 commit `1794e45`，本文件记录本机验证过的依赖组合，不代表已恢复论文作者当时的完整环境。

## 当前版本由谁管理

| 层次 | 当前版本/状态 | 管理方式与记录位置 |
| --- | --- | --- |
| 项目源码 | 历史起点 `1794e45`，当前源码版本由 Git SHA 标识 | Git 管理源码、依赖声明和锁文件；复现实验还需记录修复后的 commit |
| 项目 Python | CPython **3.11.16**，macOS ARM | `uv` 安装到 `.runtimes/python/`；版本写在 `scripts/setup_python_envs.sh` |
| Python 包管理器 | `uv 0.12.13` | 本机 Homebrew 安装；此版本是当前实测值，脚本未锁定 uv 自身版本 |
| Server Python 环境 | `server/.venv`，Python 3.11.16 | 独立虚拟环境；`server/requirements.in` → `server/requirements.txt` |
| Controller Python 环境 | `controller/.venv`，Python 3.11.16 | 独立虚拟环境；`controller/requirements.in` → `controller/requirements.txt` |
| Agent Python 环境 | `agent4drone/.venv`，Python 3.11.16 | 独立虚拟环境；运行依赖 `requirements.in/txt`，开发依赖 `requirements-dev.in/txt` |
| GUI 运行时 | Tcl/Tk **9.0.4** | 随项目 Python 提供；不通过 pip 安装 |
| 前端 Node.js | **24.14.0** | 本机 `fnm 1.39.0` 管理；`view3d/.node-version` 指定精确版本，`package.json` 的 engines 限定 24.x |
| 前端 npm | **11.9.0** | 随上述 Node 安装；此版本是当前实测值，未单独锁定 npm |
| 前端依赖 | Three.js 0.164.1、Vite 5.4.21、TypeScript 5.9.3、cross-env 7.0.3 | `package.json` 声明版本范围，`package-lock.json` 固定实际解析版本；用 `npm ci` 安装 |
| LLM 模型与服务 | 尚未配置和验证 | `agent4drone/llm_settings.json` 选择 provider、模型和端点；示例中的模型名不是已验证的实验配置 |
| Benchmark | 原始 `benchmark/benchmark.zip`，未修改 | SHA-256：`b5040097d2bfdd44600f3bf486fdb43ee3eb1247fec9c67900b5fcda6feb94a3` |

三个 Python 环境共享解释器版本，但各自安装库，避免 Server 与 Agent 的依赖约束冲突。当前关键版本如下；**完整的直接和传递依赖版本以各组件的 `.txt` 锁文件为准**。

| 组件 | 已锁定的关键库 |
| --- | --- |
| Server | FastAPI 0.107.0、Starlette 0.28.0、Pydantic 2.4.2、Uvicorn 0.23.2、HTTPX 0.27.2、Pygame 2.5.2、Shapely 2.1.2 |
| Controller | Requests 2.34.2、Pygame 2.5.2、tksheet 7.6.0、Shapely 2.1.2 |
| Agent | LangChain 1.4.2、langchain-core 1.6.4、langchain-openai 1.6.3、langchain-ollama 1.1.0、FastAPI 0.141.1、Pydantic 2.13.5、HTTPX 0.28.1 |
| 测试工具 | Server/Controller 使用 Python 自带 unittest；Agent 使用 pytest 8.4.2；前端使用 Node 自带 test runner |

终端中的裸 `python3` 当前仍由 pyenv shim 转发到本机 Python **3.14.3**，与项目解释器不同。启动时用 `.venv/bin/python`，不需要激活环境，也不会误用全局版本。项目采用 `uv pip` 加 requirements 锁文件的管理方式，没有使用 Conda，也没有 `uv.lock`。Python 锁文件固定了包版本，但未锁包文件哈希；因此还不能称为完整的原作者环境快照。

源码里的产品版本标签也不同于依赖版本：Server/Controller 为 `0.4.1`（Controller build `2026.06`），Server API 声明为 `1.0.0`，Agent API 为 `1.2.0`，前端包为 `0.2.0`。实验记录应以 Git commit、依赖锁文件、数据校验值及模型配置为依据，不能只写“平台 0.4.1”。

## 为什么 clone 后仍无法运行

`git clone` 复制仓库追踪的文件，不复制作者电脑的虚拟环境、已安装库、Python 编译选项、系统 Tcl/Tk 或模型服务配置。

本地完整 Git 历史可确认：首次源码提交 `b896c8e`（2026-06-30）就同时包含 Shapely 导入和漏列 Shapely 的 server/controller requirements。Agent 从首次提交就导入现代 LangChain，但没有依赖清单，安装说明只列了四个基础包。后续 `abfe5fc` 仅为 server 补了 HTTPX，没有补齐其余依赖。仓库中未找到干净环境安装的 CI 配置。

作者可能在已有环境里运行，预先安装的库使遗漏不易暴露，这是常见原因，但不能仅凭仓库断言作者的实际开发方式。已证实的是源码、依赖声明、安装文档之间存在不一致。

## 已解决的具体问题

| 问题 | 根因 | 本次处理 |
| --- | --- | --- |
| server/controller 缺 Shapely | 清单遗漏实际导入 | 补直接依赖，锁定 Shapely 2.1.2 及传递依赖 |
| Agent 缺 LangChain 等包 | 没有完整依赖清单 | 新增运行/开发依赖输入与版本锁，并实际测试 |
| 原 server 的 JSON POST 挂起 | FastAPI 0.104.1 带入 Starlette 0.27，无法复用日志中间件已读的请求体 | 最小升级为 FastAPI 0.107.0 / Starlette 0.28.0 |
| Server 与 Agent 不能共享同一版本组合 | server Pydantic 固定 2.4.2；当前 LangChain 要求至少 2.7.4 | 每组件独立 `.venv`，通过 HTTP 交互 |
| 本机 Python 缺 `_tkinter` | 现有解释器未提供 Tk 扩展 | 项目内安装带 Tcl/Tk 的 Python 3.11.16，不改全局 Python |
| 旧 Pygame 需要 `pkg_resources` | 该兼容模块由 Setuptools 提供 | 锁定 Setuptools 80.10.2（限制 `<81`） |
| SOCKS 代理环境下客户端导入失败 | 缺少可选的 socksio/PySocks | 安装 `httpx[socks]`、`requests[socks]` 所需依赖 |
| 前端 Node 版本说明不匹配测试脚本 | README 写 Node 18，脚本依赖新版本内置 TypeScript 支持 | 固定 Node 24.14.0，声明 engines，使用 `npm ci` |

Starlette 的请求体复用修复见 [0.28.0 发布说明](https://starlette.dev/release-notes/#0280-june-7-2023)，FastAPI 对应兼容版本见 [0.107.0 发布说明](https://fastapi.tiangolo.com/release-notes/#01070)。保留其他原 server 固定版本，仅改变已验证有必要的兼容约束。

## 本机环境布局

```text
.runtimes/python/       项目自己的 Python 3.11.16（macOS ARM，Tk/Tcl 9）
server/.venv/           仿真 server：FastAPI 0.107.0 / Pydantic 2.4.2
controller/.venv/       GUI controller：Pygame 2.5.2 / tksheet 7.6.0
agent4drone/.venv/      Agent：LangChain 1.4.2 / Pydantic 2.13.5
view3d/node_modules/    使用现有 package-lock.json 安装的前端依赖
.cache/                安装与绘图缓存
```

以上运行时、虚拟环境和缓存均已加入忽略规则，不应提交。`llm_settings.json` 也已忽略。虚拟环境依赖本机绝对路径，移动仓库后应重新创建，不应复制 `.venv` 到另一台机器。

`requirements.in` 声明直接依赖，`requirements.txt` 是其解析出的固定版本，包括传递依赖。Agent 另有 `requirements-dev.in/txt`，包含 pytest 与可选绘图依赖。安装使用锁文件，更新依赖时才重新编译 `.in`。

语音输入的 PyAudio、Torch、Transformers 等在源码中是可选功能，本次未安装；文本 agent 和 API service 不需要这些大体积依赖，也未下载模型权重。

## 重新安装 Python 环境

macOS/Linux 在仓库根目录运行，先确保 `uv` 可用。本机已经安装并验证完成，无需为启动再次安装：

```bash
bash scripts/setup_python_envs.sh --dev
```

该脚本将 Python 与缓存保存在项目目录，为三个组件分别创建虚拟环境，严格同步固定依赖，并执行 `uv pip check`。`--dev` 额外安装 Agent 测试与绘图依赖；不带它则只安装运行依赖。同步会移除该虚拟环境中不在相应锁文件里的包。

脚本不会修改 shell 配置、系统 Python 或全局 pyenv 设置。已有虚拟环境若使用不同 Python，会报错而不会自动删除；需明确重建该组件的 `.venv`。

Windows 尚未实机验证，应选用带 Tk 的 Python 3.11，为各组件分别执行 `python -m venv .venv`，再在其环境中运行 `python -m pip install -r requirements.txt`。Linux 的 GUI 还依赖可用的显示服务和系统图形库。跨平台锁文件只表示解析了条件依赖，不表示每个平台均完成运行验证。

## 启动方式

以下命令从仓库根目录开始执行，每个组件使用一个单独终端。依赖已安装，平时启动不需要重新安装。

**第一步：启动仿真 Server。** 先用 API 模式便于检查：

```bash
cd server
.venv/bin/python main.py --api-only
```

打开 `http://127.0.0.1:8000/docs` 查看接口，`http://127.0.0.1:8000/version` 检查版本。需要 Server 自带的二维地图和手动控制按钮时，将启动命令换为 `.venv/bin/python main.py --ui-drone-control`，它同时启动 API 与地图。`--ui` 单独使用只启动独立 UI，不适合这里的多组件联动。

**第二步：启动 Controller。**

```bash
cd controller
.venv/bin/python main.py
```

先运行这两个组件就可以熟悉场景管理与手动操作，不需要 LLM。Controller 中连接本地 Server `http://127.0.0.1:8000`，导入或创建场景后再查看对应场景状态。

**第三步：需要自然语言控制或自动评测时，再启动 Agent。** 首次安装且尚无 `llm_settings.json` 时复制示例；已有本地配置则保留：

```bash
cd agent4drone
# 仅在文件不存在时复制，不覆盖已有配置。
test -f llm_settings.json || cp llm_settings.example.json llm_settings.json
```

编辑 `llm_settings.json`：选择 `selected_provider`，设置其 `provider_configs` 下的 `default_model` 和凭据；OpenAI 或兼容服务还需确认 `base_url`。检查 `uav_base_url` 指向本地 Server；若终端设置过 `UAV_API_URL`，它会优先覆盖文件中的地址。示例默认选择 `volcengine`，必须改为你实际使用的配置。需要密钥的 provider 可以在文件里填写 `api_key`，也可以留空并在同一终端设置 `LLM_API_KEY`；若同时设置了 `OPENAI_API_KEY`，源码会优先读取它。只设置密钥不会自动改变 provider 或模型。

Ollama 另需先启动本地 Ollama 服务并准备所选模型。当前源码没有将 JSON 的 `base_url` 传给 `ChatOllama`，因此修改该字段不能配置远程 Ollama；本次仅验证了对象构建，未验证模型服务，也未修复远程 Ollama 地址透传。

配置完成后，在这个终端运行：

```bash
.venv/bin/python agent_api_service.py
```

检查 `http://127.0.0.1:18000/health`：应看到 `"agent_initialized": true`。单凭 HTTP 200 不够，因为初始化失败也会返回 200，内容为 `"status": "warning"`、`"agent_initialized": false`。初始化成功仅说明 Agent 对象已创建，模型端点、凭据和推理能力仍需后续小任务验证。API 文档在 `http://127.0.0.1:18000/docs`。

交互式 Agent GUI 可使用 `.venv/bin/python main.py`；接入 Controller 自动评测时使用上面的 API service。小规模验证阶段一次只运行一个控制当前场景的 Agent。本轮没有填写模型密钥、调用 LLM 或启动长期服务。

如果你的终端使用代理，应确保本地服务地址出现在 `NO_PROXY` 中（至少 `127.0.0.1,localhost`），同时保留你原有的其他例外域名。

**第四步（可选）：启动三维前端。**

```bash
cd view3d
fnm use 24.14.0
node --version
npm run dev
```

浏览器打开 `http://127.0.0.1:5173`。`npm run dev` 默认使用 backend 数据源；需要纯演示时用 `npm run dev:demo`。确认界面正在展示后端场景，不能以出现三维画面作为联通验证。

本机已有 Node 24.14.0 和 `node_modules`。新终端若提示 fnm 未初始化，先执行 `eval "$(fnm env --shell zsh)"`，再执行 `fnm use 24.14.0`；新机器需先 `fnm install 24.14.0`。新安装或锁文件变化后，在选定 Node 版本的 `view3d` 目录运行一次 `npm ci`。`.node-version` 本身不保证每个终端自动切换版本。

停止时在对应终端按 `Ctrl+C`，GUI 可关闭窗口。平时按 Server → Controller → Agent/前端的顺序启动即可。

## 本轮验证结果

| 验证 | 结果 |
| --- | --- |
| 三个 Python 环境的 `uv pip check` | 全部通过 |
| 安装脚本重复执行 | `UV_OFFLINE=1 bash scripts/setup_python_envs.sh --dev` 通过 |
| Server 实际启动 | 临时端口启动成功，`GET /version` 返回 200，JSON `POST /sessions` 返回 201，随后自动关闭 |
| Server 单元测试 | 修复后 113 项全部通过，无跳过 |
| Controller 入口及 Tk | main/session_manager/session_editor 可导入，真实隐藏 Tk 窗口与 ttk 控件可创建后销毁 |
| Controller 测试 | 修复后 137 项全部通过 |
| Agent 测试 | 141 项全部通过 |
| Agent runtime | OpenAI-compatible 与 Ollama 两种 runtime、29 个工具均可离线构建，没有发送 LLM 请求 |
| 前端 | build 通过；修复后 95 项测试全部通过，无跳过 |

Server、Controller 和前端在本次修复后均重新运行了完整测试。Agent 测试与前端 build 在依赖修复阶段验证通过，本次未改动它们的业务代码或依赖。Controller GUI 创建在可访问 macOS 图形服务的进程中验证；沙箱限制显示服务时的 Tk 错误不等于缺失依赖。

## 此前测试失败的原因与修复

依赖补齐后，剩下的失败不再是缺包问题。修复前后都没有跳过失败测试或修改 benchmark 来让结果通过。

| 失败位置 | 原因与证据 | 修复方式 |
| --- | --- | --- |
| Server 鉴权测试模块无法导入 | commit `8201cbd` 移除了 `SYSTEM_3D_VIEW_PRIVILEGE_KEY`，凭据统一进入 `SYSTEM_PRIVILEGE_KEYS`，测试还导入旧常量 | 按现有 USER/SYSTEM/ADMIN 配置校验全部密钥，并用 HTTP 测试 SYSTEM/ADMIN 可读、低权限角色被拒绝；不改鉴权实现 |
| 5 个任务模板的 `suitable_task` 断言 | 首次提交中测试就期待 `target_assignment`，模板却标记 `all`；这些模板使用通用的起飞/到达目标检查 | 保留模板定义，校验它们可用于全部受支持的任务类型；不改任务生成结果 |
| 模板检查的 `KeyError: target_id` | 测试固定读取第 0、3 个检查，但它们是起飞检查；目标到达检查在后面 | 按检查 endpoint 选择条目，验证四架无人机起飞、四个无人机与目标的正确对应关系，以及占位符全部替换 |
| 坐标解析未抛出 `ValueError` | 解析器从首次提交起就支持空格和逗号分隔，测试却把 `10 20` 当作非法输入 | 保留输入能力，增加空格/制表符的有效用例；缺少或多出坐标仍验证抛错 |
| 坐标输出精度断言 | 实现保留 1 位小数，测试期待 2 位；进一步检查发现格式化文本会在 GUI 保存时被解析回坐标，两种舍入都会损失数据 | 修改 `controller/utils.py`，编辑文本保留浮点数可往返的精度；增加相邻顶点、极小值和高精度值的往返回归测试 |
| 前端启动脚本断言 | commit `d800dad` 为跨平台增加 `cross-env`，测试仍期待旧字符串 | 校验三个脚本都含 `cross-env`，且各自的数据源、host、port 正确 |

坐标问题是真实的业务缺陷：例如三个相邻顶点 `(10.123, 20.456)`、`(10.124, 20.457)`、`(10.125, 20.456)` 以前会在编辑框中都变为 `(10.1, 20.5)`，直接保存可能把小三角形变成重合点。本次仅修改编辑坐标的格式化，不改普通数值显示规则、算法或现有 benchmark。

本次保持前端已有包版本，安装器提示的依赖安全公告未通过自动大版本升级处理。上述测试通过不代表完整集成验证或论文复现完成；首次审查中记录的会话状态、超时取消和评测口径问题仍需在大规模实验前另行处理。

## 后续验证命令

```bash
(cd server && .venv/bin/python -m unittest discover -s tests)
(cd controller && .venv/bin/python -m unittest discover -s tests)
(cd agent4drone && MPLBACKEND=Agg .venv/bin/python -m pytest tests -q)
(cd view3d && npm run build && npm test)
```

前端测试同样需在 Node 24.14.0 下执行。下一阶段先运行少量确定性动作与检查，再接入 LLM；不要以单元测试通过替代评测协议验证。

## 查看与更新依赖版本

从仓库根目录可检查实际运行环境，而不依赖终端是否激活过虚拟环境：

```bash
server/.venv/bin/python --version
controller/.venv/bin/python --version
agent4drone/.venv/bin/python --version
uv pip list --python server/.venv/bin/python
uv pip list --python controller/.venv/bin/python
uv pip list --python agent4drone/.venv/bin/python
```

日常安装使用锁文件，不要逐个 `pip install -U`。需要更新时，修改对应 `requirements.in`，参考 `.txt` 文件头记录的 `uv pip compile` 命令重新生成锁文件，然后同步环境并测试。Agent 开发锁引用运行锁，因此先更新 `requirements.txt`，再生成 `requirements-dev.txt`。使用 `bash scripts/setup_python_envs.sh --dev` 可统一同步本机三个环境。

前端更新时同时维护 `package.json` 与 `package-lock.json`，日常恢复环境用 `npm ci`。论文实验还需固定模型的具体标识、提示词、参数和评测设置；这些内容尚未通过当前依赖修复得到完整锁定。
