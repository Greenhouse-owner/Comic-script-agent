---
name: director-task-brief-designer
description: 在 Architect 完成后，为 Director 生成结构化分镜任务 brief
tags: [director, task-brief, lead, workflow]
---

## 输入

- Architect handoff / submission
- `project_id`
- `chapter_id`
- `script_path`
- `available_character_files`
- `available_environment_files`
- 用户确认进入 Director 阶段的信息
- 可选：用户分镜风格偏好

## 输出格式

必须输出 JSON 对象，不要 Markdown。

```json
{
  "task_type": "director_delivery",
  "project_id": "story_20260504_144428",
  "chapter_id": "ch01",
  "script_path": "story_20260504_144428/chapters/ch01/script.md",
  "output_path": "story_20260504_144428/chapters/ch01/storyboard.md",
  "input_discovery_mode": "progressive_bounded",
  "pipeline": [
    "director-context-planner",
    "director-storyboard-planner",
    "storyboard-draft-writer"
  ],
  "expected_outputs": {
    "selected_context": "story_20260504_144428/chapters/ch01/director_plan/selected_context.json",
    "storyboard_plan": "story_20260504_144428/chapters/ch01/director_plan/storyboard_plan.json",
    "pipeline_status": "story_20260504_144428/chapters/ch01/director_plan/pipeline_status.json",
    "storyboard": "story_20260504_144428/chapters/ch01/storyboard.md"
  },
  "requires_user_review": true,
  "submission_target": "lead"
}
```

## 硬约束

1. 只生成 Director 任务 brief，不写分镜内容。
2. 不判断具体页数、格子数、哪个格子要大。
3. 不选择具体角色/场景文件，只把可用文件列表交给 Director。
4. 路径必须在当前 project/chapter 下。
5. pipeline 只能包含：`director-context-planner`、`director-storyboard-planner`、`storyboard-draft-writer`。

## 失败处理

- 如果缺少 script_path，返回可推断的默认路径，并在 warnings 中说明。
- 如果可用角色/场景为空，不阻断任务，但在 warnings 中说明 Director 将使用基础上下文。
