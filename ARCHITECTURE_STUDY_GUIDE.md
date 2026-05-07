# Comic Script Agent 架构学习指南

这份文档帮助你用“老师讲课”的方式理解 `Comic-script-agent`：它有哪些层、每个模块做什么、信息如何从用户输入流转成漫画项目文件，以及修改和 QA 闭环是怎么跑的。

---

## 1. 一句话理解这个项目

这是一个漫画脚本创作多 Agent 系统。

它把一个漫画工作室拆成几个角色：

```text
用户
↓
Lead 总编：理解输入、派发任务、汇总结果
↓
Architect 剧情架构师：故事规划、角色、场景、章节剧本
↓
Director 分镜导演：把剧本变成分镜脚本
↓
QA 质检编辑：一致性审查、影响分析、修订回归
↓
Lead 返回给用户
```

项目的核心哲学是：

```text
代码做底层枢纽：路由、状态、文件、权限、任务、消息。
业务判断交给 skills + 模型：分类、规划、创作、审查、修改影响分析。
```

也就是说：代码不应该硬写“剧情好不好”“应该改哪个角色”“QA 应该审什么”。这些由 skill 手册和模型判断。

---

## 2. 项目目录怎么读

核心结构：

```text
Comic-script-agent/
├── src/                 # Python 运行代码
├── skills/              # 给模型看的技能手册
├── workspace/comics/    # 任务、消息、事件、项目产物
├── tests/               # 测试
├── config.json
└── .env
```

最重要的 `src/` 文件：

| 文件 | 作用 |
|---|---|
| `lead.py` | Lead 总编主控，接用户输入、派任务、汇总 Agent 结果 |
| `p0_runtime.py` | 地基层：安全路径、文件读写、任务、消息、事件日志 |
| `p1_skills.py` | 技能加载器，读取 `skills/*/SKILL.md` |
| `p2_content.py` | 漫画项目文件结构、角色/场景读写 |
| `p3_team.py` | 长期队友线程：Architect / Director / QA |
| `p3_team/aux_subagent.py` | Lead 临时 JSON 助手：分类、归一化、QA brief 等 |
| `planning.py` | Plan-before-execute，执行前先生成/校验计划 |
| `policy.py` | 每个 Agent 的工具和路径权限 |
| `intake.py` | 把用户输入归一化后写入项目事实源 |
| `feedback_orchestrator.py` | 修改反馈闭环编排辅助 |

---

## 3. 分层架构

可以把项目看成这些层：

```text
Lead 编排层
↑
P3 Team 协作层
↑
P2 Content 内容层
↑
P1 Skills 技能层
↑
P0 Runtime 地基层

旁路治理：Planning + Policy
```

### P0 Runtime：地基

文件：`src/p0_runtime.py`

负责：

```text
safe_path       防止路径越界
run_read/write 读写 workspace 文件
TaskManager    任务持久化到 tasks.json
MessageBus     Agent inbox 消息
EventLogger    写 events.jsonl 调试日志
BackgroundManager 后台任务
```

所有真实产物都限制在：

```text
workspace/comics/
```

所以 Agent 不应该随便写系统目录。

### P1 Skills：技能手册

文件：`src/p1_skills.py` 和 `skills/*/SKILL.md`

`SkillLoader` 会加载技能文档，例如：

```text
input-classifier
intake-normalizer
story-planner
character-builder
chapter-expander
qa-task-brief-designer
change-impact-analyzer
revision-regression-reviewer
```

Skill 是模型的“岗位说明书”。代码只加载它，不直接替模型做业务判断。

### P2 Content：漫画项目文件管理

文件：`src/p2_content.py`

负责创建和读写漫画项目结构：

```text
<project_id>/
├── brief.md
├── chapters/ch01/
├── state/characters/
├── state/environments/
├── qa/
└── feedback/
```

它提供：

```text
comic_init_project
comic_read_character / comic_write_character
comic_read_environment / comic_write_environment
comic_list_characters / comic_list_environments
```

### P3 Team：长期队友

文件：`src/p3_team.py`

管理三个长期 Agent：

```text
architect_bot  剧情架构师
director_bot   分镜导演
qa_bot         质检编辑
```

每个队友是一个线程，循环做三件事：

```text
1. 读自己的 inbox
2. 查 tasks.json 里分配给自己的 pending 任务
3. 执行任务，完成后发消息给 Lead
```

### AuxSubagent：Lead 的临时 JSON 助手

文件：`src/p3_team/aux_subagent.py`

它不是长期线程，而是 Lead 临时调用的小助手。它只做白名单 JSON 任务：

| task_name | 用途 |
|---|---|
| `input_classifier` | 判断用户输入类型 |
| `intake_normalizer` | 把用户输入整理成结构化 facts |
| `choice_designer` | 生成澄清题或修改选项 |
| `story_direction_summarizer` | 总结用户澄清答案 |
| `planner_decider` | 判断是否需要动态计划 |
| `architect_plan_designer` | 生成 Architect 计划 |
| `qa_task_brief_designer` | 生成 QA 审查 brief |

它要求模型输出 JSON，这样 Lead 才能稳定读取字段。

---

## 4. LeadAgent 是什么

文件：`src/lead.py`

Lead 是总编，不是创作者。

它负责：

```text
接收用户输入
维护 conversation_history
处理 /list /show /revise /apply /check 等命令
调用 aux_subagent 分类和归一化
创建任务
启动 Architect 协议
接收 Agent submission / verdict
汇总结果给用户
维护 pending_clarification / pending_revision
```

它不负责：

```text
亲自写故事
亲自写分镜
亲自判断剧情质量
亲自判断修改影响范围
```

这些交给 Architect / Director / QA 和 skills。

Lead 初始化时会创建：

```text
TaskManager
MessageBus
EventLogger
SkillLoader
AuxSubagentManager
IntakePersistenceService
FeedbackOrchestrator
ProjectStateScanner
TeammateManager
```

---

## 5. 任务、消息、事件三件套

理解这个项目，必须理解这三个文件/目录。

### 任务：`workspace/comics/tasks.json`

每个任务类似：

```json
{
  "id": "task_xxxxxxxx",
  "title": "Architect 交付 ch01",
  "status": "pending",
  "assignee": "architect_bot",
  "metadata": {
    "task_type": "architect_delivery",
    "project_id": "demo_project",
    "chapter_id": "ch01"
  }
}
```

常见状态：

```text
pending      等待执行
in_progress  正在执行
blocked      等依赖
error        失败
done         完成
cancelled    已取消
```

重要点：队友启动后会自动读取自己的 `pending` 任务。所以历史 pending 太多会污染测试。

### 消息：`workspace/comics/inboxes/`

Agent 之间通过 inbox 发消息，不是直接函数调用。

常见消息：

```text
handoff      交接给下游 Agent
submission   正式提交成果
verdict      QA 结论
task_result  通用任务结果
message_error 错误回报
```

### 事件：`workspace/comics/events.jsonl`

每个阶段都会写事件：

```text
[architect_bot][task_started] 开始执行任务
[director_bot][storyboard_written] 已写入分镜
[qa_bot][submission_sent] 已发送 QA submission
```

排查“卡在哪里”时先看 events。

---

## 6. 一个漫画项目的文件结构

项目产物都在：

```text
workspace/comics/<project_id>/
```

典型结构：

```text
<project_id>/
├── brief.md
├── raw_user_input.md
├── intake_report.md
├── execution_plan.md
├── story_direction.md
├── story_bible.md
├── chapter_outlines.md
├── chapters/
│   └── ch01/
│       ├── outline.md
│       ├── script.md
│       └── storyboard.md
├── state/
│   ├── characters/
│   │   └── <角色名>.md
│   └── environments/
│       └── <场景名>.md
├── qa/
│   └── latest_report.md
├── feedback/
└── plans/
    └── task_xxxxxxxx.json
```

最核心的事实源：

| 文件 | 含义 |
|---|---|
| `story_direction.md` | 用户创作方向 |
| `story_bible.md` | 世界观、核心设定、风格 |
| `chapter_outlines.md` | 全章节章纲 |
| `chapters/ch01/outline.md` | 单章细纲 |
| `state/characters/*.md` | 角色卡 |
| `state/environments/*.md` | 场景卡 |
| `chapters/ch01/script.md` | 章节剧本 |
| `chapters/ch01/storyboard.md` | 分镜脚本 |
| `qa/latest_report.md` | 最新 QA 报告 |

---

## 7. 新故事创作的信息流

假设用户输入：

```text
我想做一个海边灯塔女孩米娅发现星星变成小鱼的短篇漫画。
```

流程是：

```text
用户输入
↓
Lead._handle_input
↓
Lead 判断是不是 /命令
↓
不是命令，进入自动路由
↓
aux_subagent.input_classifier 判断输入类型
↓
aux_subagent.intake_normalizer 归一化为 facts JSON
↓
IntakePersistenceService 写入项目事实源
↓
Lead 调 start_architect_protocol
↓
创建 Architect 任务
↓
PlanManager 生成/校验计划
↓
Architect 加载 skills 生成 story_bible / 角色 / 场景 / 剧本
↓
Architect 发 submission / handoff
↓
Lead 创建 Director 任务
↓
Director 读取 script + 角色卡 + 场景卡，写 storyboard
↓
Director 发 submission
↓
Lead 创建 QA review
↓
qa-task-brief-designer 生成 QA brief
↓
QA-bot 加载 QA skills，写 qa/latest_report.md
↓
QA 发 verdict
↓
Lead 汇总给用户
```

这里有两个重点：

```text
1. Lead 派 Architect 创作任务时，应该用 start_architect_protocol，而不是普通 create_task。
2. QA 任务统一用 task_type=qa_review，具体审什么由 qa-task-brief-designer 和 QA skills 决定。
```

---

## 8. 修改闭环的信息流

用户可能说：

```text
/revise 我希望米娅更内向，开头不要那么勇敢
```

流程：

```text
用户 /revise
↓
Lead 保存 pending_revision
↓
Lead 创建 QA impact analysis 任务
↓
QA-bot 用 change-impact-analyzer 判断影响范围
↓
QA 写报告并发 verdict
↓
Lead 展示 local / targeted / cascade 三种修订范围
↓
用户 /apply targeted
↓
Lead 采集 before snapshot
↓
Lead 创建 Architect artifact_revision 任务
↓
Architect 读取用户修改请求 + QA 报告 + target_files
↓
Architect 修订相关文件
↓
Architect 发 revision submission
↓
Lead 采集 after snapshot
↓
Lead 创建 QA regression review
↓
QA-bot 用 revision-regression-reviewer 检查是否落实修改、有无副作用
↓
QA 发 verdict
↓
Lead 返回最终结果
```

三个修订范围：

| 模式 | 含义 |
|---|---|
| `local` | 只修直接目标文件 |
| `targeted` | 修直接目标和高置信度影响文件 |
| `cascade` | 级联修订所有可能受影响产物 |

这个设计很关键：

```text
用户提出修改后，不是 Architect 直接改。
先让 QA 分析影响范围，再让用户选择范围，再修订，再 QA 回归。
```

---

## 9. Planning 和 Policy 为什么重要

### Planning：先计划再执行

文件：`src/planning.py`

Agent 写文件前会生成 `ExecutionPlan`，保存在：

```text
<project_id>/plans/task_xxxxxxxx.json
```

Plan 说明：

```text
这个任务要读什么
要写什么
用什么工具
产物是什么
有哪些风险
```

Architect 主要产物 contract 包括：

```text
brief
story_direction
story_bible
chapter_outlines
chapter_outline
character_cards
environment_cards
chapter_script
```

### Policy：工具和路径权限

文件：`src/policy.py`

它限制每个 Agent 能做什么。

例如：

```text
Architect 可以写 story_bible / script / 角色卡 / 场景卡
Director 只能写 storyboard
QA 只能写 qa/*.md，不能改创作文件
Lead 权限最大
```

这能防止模型乱写文件。

---

## 10. QA 体系怎么理解

现在 QA 的方向是“通用 QA 任务”：

```text
task_type = qa_review
```

Lead 不硬编码 QA 查什么，而是：

```text
事件上下文
↓
qa-task-brief-designer
↓
review_goal / review_context
↓
QA-bot
↓
qa-review-router 选择具体 QA skills
↓
输出 verdict
```

标准 QA verdict 应该包含：

```json
{
  "type": "verdict",
  "from_role": "qa",
  "project_id": "...",
  "chapter_id": "...",
  "task_type": "qa_review",
  "report_kind": "impact_analysis | project_consistency | revision_regression | story_quality",
  "final_verdict": "PASS | WARNING | FAIL",
  "summary": "...",
  "issues": [],
  "report_file": "...",
  "target_files": [],
  "recommended_actions": []
}
```

如果 QA 输出不标准，Lead 会尝试归一化。

---

## 11. 用户查看结果的命令

Lead 支持一些本地命令，不需要调用模型：

```text
/list
/show story_bible
/show chapter_outlines
/show characters
/show character <name>
/show environments
/show environment <name>
/show chapter ch01 outline
/show chapter ch01 script
/show chapter ch01 storyboard
/show qa latest
/status
```

这些命令本质上是读 `workspace/comics/<project_id>/` 里的文件。

---

## 12. 读代码的推荐顺序

如果你现在觉得乱，建议按这个顺序看：

### 第一步：`p0_runtime.py`

理解：

```text
workspace 在哪里
tasks.json 是什么
inboxes 是什么
events.jsonl 是什么
safe_path 如何保护路径
```

### 第二步：`lead.py`

重点找：

```text
_handle_input
_handle_local_workflow_command
_auto_route_user_input
_create_qa_review_from_event
_apply_pending_revision
_normalize_qa_result
```

### 第三步：`p3_team/aux_subagent.py`

理解每个 JSON task 对应哪个 skill。

### 第四步：`p3_team.py`

理解队友线程如何：

```text
读 inbox
读 pending tasks
执行任务
发 submission / verdict
```

### 第五步：`planning.py` 和 `policy.py`

理解为什么要先计划、再校验权限、再执行。

### 第六步：`skills/`

重点看：

```text
input-classifier
intake-normalizer
architect-plan-designer
qa-task-brief-designer
qa-review-router
change-impact-analyzer
revision-regression-reviewer
```

你会发现真正的业务判断基本都在 skills 里。

---

## 13. 最容易混乱的几个点

### 1. Lead 和 Architect 的边界

Lead 是总编，只调度，不创作。

Architect 才负责：

```text
story_bible
chapter_outlines
角色卡
场景卡
script
```

### 2. AuxSubagent 和 Teammate 的区别

```text
AuxSubagent：Lead 临时调用，返回 JSON，不是线程。
Teammate：长期线程，有 inbox，有 tasks，会自动执行任务。
```

### 3. QA 不应该直接改创作文件

QA 可以读全部项目，但只能写：

```text
qa/*.md
```

它给结论和建议，真正改文件的是 Architect。

### 4. tasks.json 会污染测试

队友启动后会自动捞 pending 任务。

如果历史任务没清理，测试时可能会突然跑旧项目任务。

### 5. API 不通时，业务链路没法验证

目前你这里 Cursor 环境里 API 报过：

```text
ProxyError 403 Forbidden
```

这会让 `input_classifier` 第一步就失败。

---

## 14. 最终心智模型

把系统想象成漫画工作室：

| 系统组件 | 工作室角色 |
|---|---|
| Lead | 总编 / 项目经理 |
| AuxSubagent | 总编的临时分析助理 |
| Architect | 编剧 / 世界观负责人 |
| Director | 分镜导演 |
| QA | 质检编辑 |
| Skills | 岗位工作手册 |
| TaskManager | 工作订单系统 |
| MessageBus | 内部邮件系统 |
| EventLogger | 工作日志 |
| Workspace | 项目资料库 |
| PlanManager | 开工前审批流程 |
| Policy | 权限制度 |

一次完整工作就是：

```text
用户想法
↓
总编整理事实
↓
开工作单
↓
先生成计划
↓
编剧写故事产物
↓
导演做分镜
↓
质检审查
↓
总编汇总
↓
用户提出修改
↓
质检分析影响
↓
用户选范围
↓
编剧修订
↓
质检回归
↓
总编返回最终结果
```

---

## 15. 最短总结

如果只记三句话：

```text
1. Lead 不创作，Lead 只编排。
2. 代码不做业务判断，业务交给 skills + 模型。
3. workspace/comics 是真相源：任务、消息、事件、项目文件都在那里。
```

这三句话就是这个项目的核心架构。
