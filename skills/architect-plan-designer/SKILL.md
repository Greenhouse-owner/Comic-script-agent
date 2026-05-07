---
name: architect-plan-designer
description: 为 Architect 生成严格符合系统 ExecutionPlan 结构的动态执行 plan JSON
tags: [architect, planning, json, execution-plan]
---

## 目标

根据 `task_metadata`、`goal`、`project_state`、`artifact_contracts` 和 `planner_decision`，生成一个 Architect 可执行的 plan JSON。

你只负责生成计划，不执行计划，不写文件，不生成具体故事正文。

## 计划边界

你生成的是 **artifact 交付计划**，不是剧情创作计划。

允许规划：

- artifact 类型
- 输出路径
- 输入依赖
- producer skill
- tool
- validators / validation_rules
- 通用质量约束
- 交付顺序

禁止规划：

- 具体角色名，除非用户事实源或现有 artifact 已明确提供
- 具体场景名，除非用户事实源或现有 artifact 已明确提供
- 具体世界观设定，除非用户事实源或现有 artifact 已明确提供
- 具体章节事件
- 具体剧情反转
- 具体对白
- 具体结局
- 任何用户没有明确提供的故事内容

`step.description`、`purpose` 和 `risk_checklist` 都只能描述交付目标、依赖、路径、校验和约束，不能写剧情方案。
所有具体创作决策由 Architect 执行阶段加载对应 skills 后完成。

## 输入

- `task_metadata`：Lead 创建的 Architect task metadata。
- `goal`：Architect 任务目标，包含 `goal_type`、`project_id`、`chapter_ids`、`target_artifacts`。
- `project_state`：项目现有事实源状态，包含各 artifact 是否存在与是否非空。
- `artifact_contracts`：系统允许的 artifact 类型、路径模板、依赖输入、producer skill 和 validators。
- `planner_decision`：上游 planner_decider 的判断，例如 `use_ai_planner`、`reason`、`risk_flags`。

## 输出硬要求

必须只输出一个 JSON 对象。不要输出 Markdown、代码块、解释文字或额外字段说明。

输出 JSON 必须符合系统 `ExecutionPlan.to_dict()` 可消费的结构，并且能被 `PlanManager.validate_plan()` 校验。

```json
{
  "planning_mode": "ai_architect_dynamic_v1",
  "agent": "architect_bot",
  "project_id": "project_id",
  "chapter_id": "ch01",
  "task_type": "architect_delivery",
  "goal": {},
  "project_state": {},
  "target_artifacts": ["story_bible", "chapter_outlines", "chapter_outline", "character_cards", "environment_cards", "chapter_script"],
  "steps": [
    {
      "id": "step_1",
      "description": "补齐 Story Bible",
      "tool": "write_file",
      "inputs": {
        "file_path": "project_id/story_bible.md",
        "skill": "story-planner"
      },
      "expected_output": "story_bible",
      "skill": "story-planner",
      "input_artifacts": [
        {
          "artifact_type": "brief",
          "path": "project_id/brief.md"
        },
        {
          "artifact_type": "story_direction",
          "path": "project_id/story_direction.md"
        }
      ],
      "output_artifacts": [
        {
          "artifact_type": "story_bible",
          "path": "project_id/story_bible.md",
          "producer_skill": "story-planner"
        }
      ],
      "depends_on": [],
      "validation_rules": ["exists", "non_empty"],
      "purpose": "建立故事级事实源，供后续章纲、角色、场景和剧本使用。"
    }
  ],
  "deliverables": [
    {
      "artifact_type": "chapter_script",
      "path": "project_id/chapters/ch01/script.md"
    }
  ],
  "risk_checklist": [],
  "requires_user_approval": false
}
```

## 允许的固定值

- `planning_mode` 必须是 `ai_architect_dynamic_v1`。
- `agent` 必须是 `architect_bot`。
- `task_type` 必须是 `architect_delivery`。
- 每个 `step.tool` 必须是 `write_file`。
- `inputs.file_path` 必须等于该 step 第一个 `output_artifacts[0].path`。
- `skill` 必须来自对应 artifact contract 的 `producer_skills`。

## artifact 规则

只能使用 `artifact_contracts` 中存在的 artifact 类型。常见类型包括：

- `story_bible`
- `chapter_outlines`
- `chapter_outline`
- `character_cards`
- `environment_cards`
- `chapter_script`

不要为 `brief` 或 `story_direction` 生成写入 step；它们通常是输入事实源。

## 路径规则

每个输出路径必须严格符合 `artifact_contracts[artifact_type].path_template` 在替换 `project_id` / `chapter_id` 后的结果。

常用路径：

- `story_bible` → `{project_id}/story_bible.md`
- `chapter_outlines` → `{project_id}/chapter_outlines.md`
- `chapter_outline` → `{project_id}/chapters/{chapter_id}/outline.md`
- `character_cards` → `{project_id}/state/characters/*.md`
- `environment_cards` → `{project_id}/state/environments/*.md`
- `chapter_script` → `{project_id}/chapters/{chapter_id}/script.md`

对于 glob 类产物：

- `character_cards` 的 `inputs.file_path` 和 `output_artifacts[0].path` 可以使用 `{project_id}/state/characters/*.md`。
- `environment_cards` 的 `inputs.file_path` 和 `output_artifacts[0].path` 可以使用 `{project_id}/state/environments/*.md`。

## 跳过已有产物

如果 `project_state` 显示某个 artifact 已存在且 `non_empty: true`，默认不要为它生成 step。

例外：只有当 `goal` 或 `task_metadata` 明确要求重生成时，才可以加入对应 step。

## 依赖顺序

必须满足：

1. `story_bible` 依赖 `brief` 和 `story_direction`。
2. `chapter_outlines` 依赖 `story_bible` 和 `story_direction`。
3. `chapter_outline` 依赖 `chapter_outlines`。
4. `character_cards` 依赖 `story_bible` 和 `chapter_outlines`。
5. `environment_cards` 依赖 `story_bible` 和 `chapter_outlines`。
6. `chapter_script` 依赖 `story_bible`、`chapter_outline`、`character_cards` 和 `environment_cards`。

`depends_on` 必须引用同一 plan 中已有的 step id。不要引用不存在的 step。

如果依赖 artifact 已经存在且非空，可以不在 `depends_on` 中引用对应 step，但仍要在 `input_artifacts` 中列出它。

## 多章节规则

如果 `goal.chapter_ids` 有多个章节：

- 可以为每个章节生成一个 `chapter_outline` step。
- 可以为每个章节生成一个 `chapter_script` step。
- 每个章节 step 的路径必须使用对应 `chapter_id`。
- `chapter_script` step 必须依赖同章节的 `chapter_outline` step，或者依赖已存在的同章节 outline artifact。

## 禁止事项

1. 不要输出 Markdown 或代码块。
2. 不要生成 `read_file` step。Architect 在执行时会根据 `input_artifacts` 自己读取。
3. 不要生成 `load_skill` step。Architect 在执行时会根据 `skill` 字段自己加载。
4. 不要生成反馈修订计划；`architect_feedback_revision` 不允许使用 AI Planner。
5. 不要写具体故事正文、角色卡正文或剧本正文。
6. 不要把 plan 写成剧情创作方案；只能写 artifact 交付计划。
7. 不要在 `description`、`purpose` 或 `risk_checklist` 中写具体章节事件、剧情转折、对白、结局或用户未明确提供的新设定。
8. 不要输出系统不认识的 artifact 类型.
9. 不要输出绝对路径。
10. 不要把 path 写到项目目录之外。
11. 不要覆盖用户已明确事实；计划中要在 `risk_checklist` 提醒继承用户事实。

## risk_checklist 必须包含

至少包含这些风险项：

- `不得覆盖用户已明确事实`
- `输出路径必须符合 artifact contract`
- `chapter_script 必须在故事、章节、角色、场景事实源之后生成`

如果 `planner_decision.risk_flags` 非空，应把相关风险也转换为中文风险项加入 `risk_checklist`。

## 空计划处理

如果目标产物全部已经存在且非空，可以输出空 `steps` 吗？不可以。

此时输出一个 `chapter_script` 检查/重写 step，除非 `chapter_script` 也已存在且非空；如果全部完成，则输出一个最小 `chapter_script` step 指向当前章节脚本，并在 `purpose` 标注“确认最终剧本交付”。
