<div align="center">
  <img src="assets/judger-banner.svg" width="100%" alt="Judger：重构人与 AI 的交流方式。少量信号，清晰意图，实际执行。">
  <br><br>
  <img src="assets/judger-status.svg" width="870" alt="键盘 Demo · Python 3.11+ · 已接入 Codex · EEG 规划中">
  <br><br>
  <strong>面向 AI Agent 的意图交互界面。</strong><br>
  让 AI 逐步理解你想做什么，用三个按键作出判断，在执行过程中持续指挥。
  <br><br>
  <a href="#开始体验">开始体验</a> ·
  <a href="#三个按键持续对话">交互方式</a> ·
  <a href="#从键盘走向-eeg">EEG 路线</a> ·
  <a href="README.md">English</a>
</div>

<br>

## 让意图成为界面。

AI 已能根据一份需求编写程序、撰写文章或制作网页。把想法整理成足够清晰的需求，并在想法变化时持续校准执行，仍然需要人投入大量表达和判断。

**Judger 尝试把这段交流重构为“提问、判断、行动”的连续循环。** 系统提出一个具体、容易回答的问题；你作出判断；它逐步缩小可能性，形成可执行的需求，再交给 Agent 完成。

想法还不完整，也可以开始。Agent 工作时，你仍能继续塑造结果。任何时候发现先前的判断不合适，都能回到历史问题中修改。

```text
JUDGER                                      MAIN QUESTION

    Is it for other people?

    = Yes                   - No                  0 Rephrase
```

长期目标是一套能够跨越任务、执行器和输入设备的交互词汇。**当前仓库提供的是这套想法的可运行键盘 Demo。** 程序界面和提示词均为英文，本文件提供中文产品说明。

## 少量信号，持续协作

<table>
<tr>
<td width="33%" valign="top">
<strong>01 / 理解意图</strong><br><br>
用类似 Akinator 的简短问题，把尚未成形的想法逐步变成可执行需求。后续问题会提前准备。
</td>
<td width="33%" valign="top">
<strong>02 / 持续指挥</strong><br><br>
Agent 执行时，支线问题仍可继续使用。新判断会成为同一任务的补充，也能提交给正在工作的 Codex。
</td>
<td width="33%" valign="top">
<strong>03 / 边看边改</strong><br><br>
在 Terminal 调节网页，在 Chrome 查看真实页面的变化。可以撤回判断、修改旧答案，并在本轮完成后继续操作。
</td>
</tr>
</table>

## 三个按键，持续对话

物理按键保持简单；屏幕上的问题和当前模式提供上下文。

| 场景 | `=` | `-` | `0` |
| :--- | :--- | :--- | :--- |
| **主线问题**：Agent 等你回答 | 是 | 不是 | 换个问法 |
| **支线问题**：Agent 仍在执行 | 是 | 不是 | 立即发送已收集的答案 |
| **具体执行**：调节选中的类别 | 增加 | 减少 | 立即发送当前调节 |

触发按键无需回车。肯定键是 `=`，不需要 Shift。

支线默认每两次选择自动提交，也可通过 `--auto-submit 1` 改成每次提交。提交后仍能继续回答缓存中的下一问。已提交的要求会在宣布完成前得到处理。

| 按键 | 操作 |
| :--- | :--- |
| `b` / Backspace | 回到上一题；具体执行中撤回上一次调节 |
| `v` | 查看本次会话的所有答案 |
| `e` / Enter，再按 `=` / `-` | 更改选中的历史答案 |
| `r` | 撤回选中的答案及之后的答案 |
| `C` | 进入或离开 Terminal 内的分栏调节 |
| `[` / `]`，也支持 `【` / `】` | 切换调节类别 |
| `i` | 输入补充文字；Enter 发送，Esc 取消 |
| `h` / `q` | 帮助 / 退出 |

修改旧答案时，会撤回可能依赖它的后续答案，再把有效的新上下文交给执行端。已经执行的命令不会自动回滚；文件修改需要执行端实际完成。

## 开始体验

**Python 3.11+ · macOS 或 Linux · 真实 Terminal · 程序运行无需第三方 Python 依赖。**

```sh
git clone https://github.com/MarvinCui/Judger.git
cd Judger
```

### 1. 离线试手感

```sh
python3 main.py --demo
```

体验 Terminal 调节和真实网页预览：

```sh
python3 main.py --demo --task 'Try the webpage controls'
```

网页准备好后按 `C`。键盘焦点留在 Terminal，Chrome 只显示项目网页。离线演示使用固定脚本，不请求模型。

### 2. 理解需求并实际执行

使用隐藏输入配置自己的 DeepSeek Key：

```sh
python3 main.py --setup
python3 main.py --check
python3 main.py
```

也可以设置环境变量 `DEEPSEEK_API_KEY`。Key 只属于本地私密配置，**本仓库不附带任何 Key**。

从一点线索开始：

```sh
python3 main.py --task 'I want to make something for my portfolio.'
```

开启本机命令执行后，可以实际运行程序和测试：

```sh
python3 main.py --allow-shell --task 'Build a small Python timer.'
```

`--allow-shell` 开启真实的本机执行能力，不构成系统沙箱。文件工具在选中的项目范围内工作，覆盖文件前保留备份。

### 3. 交给 Codex 做

先单独安装并登录 Codex CLI：

```sh
codex login
python3 main.py --codex --task 'Help me design a personal website.'
```

DeepSeek 负责猜目的和生成后台问题。信息足够时，Judger 把需求自动交给本机 [Codex app-server](https://developers.openai.com/codex/app-server/) 会话。

执行过程中提交的支线答案会追加到正在工作的 Codex 轮次。如果这一轮刚好结束，则作为同一对话的下一轮发送。Codex 遵循自己的工作区沙箱和权限流程，额外权限请求会作为 Terminal 中的主线问题显示。

### 每个项目都有独立空间

新任务会在 `workbench/` 下创建带时间戳的独立文件夹。继续已有项目：

```sh
python3 main.py --codex --project ./workbench/your-project
```

使用 `--workspace /path/to/projects` 指定新项目的父目录。已有项目会恢复保存的 Codex 对话；Judger 自己的问答历史目前在每次启动时重新开始。

## 操作留在 Terminal，作品显示在浏览器

**Judger 的软件界面始终在 Terminal。** Chrome 只预览正在开发的网页。提问、类别、按键、文字输入、历史记录和更正，全部留在终端。

按 `C` 进入分栏，在 **Color、Boldness、Density、Detail、Expression** 之间切换，用 `-` / `=` 调节。

- 色彩、字重、留白和装饰，通过保存的样式表和本地预览服务即时更新。
- 短暂停顿后，连续调节会合并成一组补充要求交给执行端。
- HTML 文件被实际修改后，浏览器自动刷新。
- 内容改写和结构重排，需要等待 Agent 实际完成对应编辑。

当前预览支持**静态 HTML 和相对路径资源**。React/Vite 开发服务器尚未接入。可通过 `--preview-file pages/index.html` 选择其他 HTML 入口。

## 从键盘走向 EEG

> **长期目标：键盘 Trigger 与 EEG Trigger，能够表达同一个判断。**

键盘 Demo 让我们先研究交互词汇，再决定硬件接口。计划中的下一步，是把少量经过个人校准的 EEG 分类结果，映射到相同的上下文触发事件。

| 输入方式 | 映射方式 | 状态 |
| :--- | :--- | :--- |
| **键盘** | 实际的 `=` / `-` / `0` → 上下文判断 → Agent 行动 | 已实现 |
| **EEG** | 经过校准的信号类别 → 置信度与无指令状态处理 → 对应 Trigger → 同一判断循环 | 研究路线 |

EEG 阶段计划研究：

- 哪种信号范式和 Trigger 数量，能在可靠性、速度、训练负担和成本之间取得适合使用的平衡。
- 如何通过个人校准、明确的无指令状态和置信度阈值减少误触发。
- 少通道、紧凑设备能否支持日常环境中的交互。
- 分类不确定时，键盘回退、撤回和确认应该如何工作。

**当前仓库没有实现 EEG 采集、解码、模型训练或硬件。** 我们尚未证明最优 Trigger 数量，没有验证 EEG 分类准确率，也没有展示硬件系统。三个键盘 Trigger 是现阶段的产品实验。

## 为什么值得构建这一层

我们的产品判断是：持续指挥能力越来越强的 Agent，可以形成独立的产品空间——**在人类意图与机器执行之间，建立可复用的交互界面。**

Judger 从今天就能检查结果的任务切入：文章、代码和网页。紧凑的 Trigger 词汇，为未来接入其他输入设备提供方向，同时保留澄清、纠正和持续指挥的能力。

下一阶段需要测量任务完成、输入负担、决策延迟、更正频率和用户理解程度。产品主张可以被检验；相较提示词交互的生产力提升仍有待验证。

## 当前能力与后续方向

| 当前 Demo 已有 | 后续计划研究 |
| :--- | :--- |
| Terminal 内即时三键输入 | 与提示词工作流的实测比较 |
| 七层主线问题预生成 | 更好的提问选择和缓存效率 |
| 执行期间持续使用支线 | 面向其他输入设备的正式适配接口 |
| Codex 委托和运行中补充 | EEG 分类及个人校准实验 |
| 历史更正和 HTML 实时调节 | 紧凑硬件可行性与更多预览类型 |

七层二叉树包含 **127 个问题节点、128 条完整路径**。这些是推测路径，不代表用户必须作出 127 次判断。首次准备、网络请求和缓存补充仍然需要时间，也会消耗模型额度；线索足够后会提前返回执行。

## 查看实现

| 文件 | 用途 |
| :--- | :--- |
| [`trigger_agent/prompts/`](trigger_agent/prompts/) | 猜目的、短问题规则和后续问题规划 |
| [`state.py`](trigger_agent/state.py) | 按键含义、答案历史和反馈状态 |
| [`engine.py`](trigger_agent/engine.py) | Agent 执行、工具、规划器和离线示例 |
| [`codex_bridge.py`](trigger_agent/codex_bridge.py) | 持续的 Codex 对话及工作中追加要求 |
| [`execution.py`](trigger_agent/execution.py) | Terminal 内调节与纯网页预览 |
| [`ui.py`](trigger_agent/ui.py) | Terminal 界面 |

为兼容早期 Demo，内部 Python 包名仍为 `trigger_agent`；产品和界面统一使用 **Judger**。

### 检查

```sh
python3 -m unittest discover -s tests -v
python3 tests/terminal_smoke.py
python3 tests/terminal_execution_smoke.py
```

真实模型联调需要使用你自己的账户：

```sh
python3 tests/live_smoke.py
python3 tests/live_revision.py
python3 tests/live_codex.py --agent
```

浏览器检查的可选依赖见[开发说明](docs/DEVELOPMENT.md)，已经验证的范围和限制见 [QA.md](QA.md)。

### 本地数据

`.local/` 保存私密 Key 配置、可选会话记录和本地测试产物；`workbench/` 保存生成的项目。两者均由 Git 忽略。项目元数据和备份位于 `.trigger-agent/`，实时样式文件沿用 `trigger-style.css`。

会话日志可能包含提示词和文件内容。`--no-log` 关闭 Judger 的对话及按键日志，但项目元数据、备份及 Codex 自身的记录仍可能写入。不要提交生成的项目或私密配置。

## 一起构建下一种交互方式

欢迎围绕提问设计、交互评估、无障碍操作、执行器集成及未来输入适配参与开发。可以通过仓库的 [Issues](https://github.com/MarvinCui/Judger/issues) 发起讨论。

Judger 使用 [MIT 许可证](LICENSE)。

---

<div align="center">
  <strong>JUDGER</strong><br>
  少量信号，清晰意图，实际执行。
</div>
