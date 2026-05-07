---
name: story-direction-summarizer
description: 将用户对澄清题的回答归纳为 story_direction 结构化 JSON
tags: [clarification, story-direction, intake, lead]
---

## 目标

根据上一轮澄清题、原始故事种子和用户回答，生成可写入 `story_direction.md` 的结构化创作方向。

你只负责归纳，不负责写文件，不负责生成完整故事。

## 输入

- `pending.original_input`：用户最初的新故事输入。
- `pending.questions`：上一轮生成的澄清题。
- `pending.classification`：分类结果。
- `user_response`：用户对澄清题的回答。
- `project_id`：建议项目 ID。

## 输出格式

必须只输出 JSON 对象，不要 Markdown、代码块或解释。

```json
{
  "project_id": "demo",
  "story_direction": {
    "premise": "",
    "genre": "",
    "tone": "",
    "target_chapter_count": 3,
    "main_character": "",
    "central_conflict": "",
    "ending_direction": "",
    "visual_style": "",
    "user_constraints": [],
    "summary": ""
  },
  "recommended_next": {
    "task_type": "architect_delivery",
    "use_dynamic_plan": true,
    "use_ai_planner": true
  }
}
```

## 归纳规则

1. 优先保留用户回答中的明确选择。
2. 如果用户只回答选项编号，要结合 `pending.questions` 还原对应含义。
3. 未回答的信息保持空字符串或空数组，不要虚构。
4. `summary` 用 1 到 3 句话概括创作方向。
5. `target_chapter_count` 必须是整数；无法判断时用 1。
6. `user_constraints` 记录用户明确说的限制，例如“不幼稚”“不要黑暗”“适合儿童”。
7. 不要生成完整 `story_bible`、章纲或剧本。

## recommended_next 判断

- 新故事方向已明确时，`task_type` 通常为 `architect_delivery`。
- 如果仍缺大量事实源，`use_dynamic_plan` 应为 `true`。
- 如果方向复杂、多章节或需要 Architect 自行补齐世界观/角色/场景，`use_ai_planner` 可为 `true`。
