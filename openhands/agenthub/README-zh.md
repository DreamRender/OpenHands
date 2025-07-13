# Agent 中心

在此文件夹中，可能包含多个将被框架使用的 `Agent` 实现。

例如：`openhands/agenthub/codeact_agent` 等。来自不同背景和兴趣的贡献者可以选择为任何（或所有）这些方向做出贡献。

## 构建一个 Agent

Agent 的抽象定义可在 [这里](../controller/agent.py) 找到。

Agents 在一个循环中运行。每次迭代，都会以一个 [State](../controller/state/state.py) 作为输入调用 `agent.step()`，Agent 必须输出一个 [Action](../events/action)。

每个 Agent 都有一个 `self.llm`，可以通过它与用户配置的 LLM 进行交互。详情请参阅 LiteLLM 的 [`self.llm.completion` 文档](https://docs.litellm.ai/docs/completion)。

## State

`State` 表示 OpenHands 系统中 Agent 的运行状态。该类负责保存和恢复 Agent 会话，并以 pickle 形式序列化。

State 对象存储的信息包括：

* **多 Agent 状态 / 委派**

  * “根任务”（Agent 与用户之间的对话）
  * 子任务（Agent 与用户或另一个 Agent 之间的对话）
  * 全局和本地迭代次数
  * 多 Agent 交互的委派层级
  * 即将卡住的状态
* **Agent 运行状态**

  * 当前 Agent 状态（例如 LOADING、RUNNING、PAUSED）
  * 流量控制状态（用于限流）
  * 确认模式
  * 最近遇到的错误
* **历史**

  * Agent 历史中事件的起止 ID，便于检索 Agent 执行动作和观察结果（例如文件内容、命令输出）
* **指标**

  * 当前任务的全局指标
  * 当前子任务的本地指标
* **附加数据**

  * 其他与任务相关的自定义数据

Agent 可以通过 `AddTaskAction` 和 `ModifyTaskAction` 添加或修改子任务。

## Actions

以下是 `agent.step()` 可以返回的 Actions 列表：

* [`CmdRunAction`](../events/action/commands.py) — 在沙箱终端运行命令
* [`IPythonRunCellAction`](../events/action/commands.py) — 交互式执行 Python 代码块（Jupyter notebook），并接收 `CmdOutputObservation`，需配置 `jupyter` [插件](../runtime/plugins)
* [`FileReadAction`](../events/action/files.py) — 读取文件内容
* [`FileWriteAction`](../events/action/files.py) — 写入文件内容
* [`BrowseURLAction`](../events/action/browse.py) — 获取指定 URL 的内容
* [`AddTaskAction`](../events/action/tasks.py) — 将子任务添加到计划中
* [`ModifyTaskAction`](../events/action/tasks.py) — 修改子任务状态
* [`AgentFinishAction`](../events/action/agent.py) — 停止控制循环，允许用户或委派 Agent 进入新任务
* [`AgentRejectAction`](../events/action/agent.py) — 停止控制循环，允许用户或委派 Agent 进入新任务
* [`MessageAction`](../events/action/message.py) — 表示来自 Agent 或用户的一条消息

要序列化和反序列化 Action，可使用：

* `action.to_dict()`：将 Action 序列化为字典，包含用户友好的字符串表示，用于 UI
* `action.to_memory()`：将 Action 序列化为字典，包含原始信息（如执行异常），用于发送给 LLM
* `action_from_dict(action_dict)`：从字典反序列化 Action

## Observations

还有多种类型的 Observations，通常在对应 Action 之后的下一步中出现，也可能因异步事件（如用户消息）而出现：

* [`CmdOutputObservation`](../events/observation/commands.py)
* [`BrowserOutputObservation`](../events/observation/browse.py)
* [`FileReadObservation`](../events/observation/files.py)
* [`FileWriteObservation`](../events/observation/files.py)
* [`ErrorObservation`](../events/observation/error.py)
* [`SuccessObservation`](../events/observation/success.py)

可使用 `observation.to_dict()` 和 `observation_from_dict` 进行序列化和反序列化。

## 接口

每个 Agent 必须实现以下方法：

### `step`

```python
def step(self, state: "State") -> "Action"
```

`step` 推动 Agent 向目标前进一步，通常是向 LLM 发送一个提示并将响应解析为一个 `Action`。

## Agent 委派

OpenHands 是一个多 Agent 系统。Agents 可以将任务委派给其他 Agents，既可以由用户触发，也可以由 Agent 自行决定请另一个 Agent 帮忙。例如，`CodeActAgent` 可能会将需要浏览网页的问题委派给 `BrowsingAgent`。委派 Agent 会将任务转发给微观 Agents，例如用于研究仓库的 `RepoStudyAgent`，或用于校验任务完成情况的 `VerifierAgent`。

### 术语说明

* **任务（task）**：OpenHands（整个系统）与用户之间的端到端对话，可能包含一次或多次用户输入。以用户的初始输入开始，以 `AgentFinishAction`、用户终止或错误结束。
* **子任务（subtask）**：Agent 与用户或另一个 Agent 之间的端到端对话。如果一个任务由单个 Agent 执行，那么该子任务即为该任务；否则，一个任务包含多个子任务，每个子任务由一个 Agent 执行。

**示例**：用户请求“告诉我 OpenHands 仓库在 GitHub 上有多少星”。假设默认 Agent 为 CodeActAgent。

```
-- 任务开始（子任务 0 开始） --

委派层级 0，迭代次数 0，本地迭代 0
CodeActAgent：我应该请求 BrowsingAgent 帮助

-- 委派开始（子任务 1 开始） --

委派层级 1，迭代次数 1，本地迭代 0
BrowsingAgent：让我去 GitHub 找答案

委派层级 1，迭代次数 2，本地迭代 1
BrowsingAgent：我找到了答案，准备返回结果并结束

-- 委派结束（子任务 1 结束） --

委派层级 0，迭代次数 3，本地迭代 1
CodeActAgent：我收到了 BrowsingAgent 的答案，准备返回给用户并结束

-- 任务结束（子任务 0 结束） --
```
