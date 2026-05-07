---
name: director-context-planner
description: 为指定章节分镜选择必要角色与场景上下文，并支持有边界的二次读取申请
tags: [director, storyboard, context, progressive-disclosure]
---

## 输入

- `script.md`：用户确认进入分镜阶段的章节剧本
- `available_character_files`：代码从 `state/characters/*.md` 列出的角色文件路径
- `available_environment_files`：代码从 `state/environments/*.md` 列出的场景文件路径
- `project_id`
- `chapter_id`
- Director task brief
- 可选：已读取的第一批上下文摘要

## 输出格式

必须输出 JSON 对象，不要 Markdown。

```json
{
  "initial_selected_files": {
    "characters": [],
    "environments": []
  },
  "need_more_context": false,
  "additional_selected_files": {
    "characters": [],
    "environments": []
  },
  "missing_context_notes": [],
  "warnings": [],
  "selection_rounds": [
    {
      "round": 1,
      "reason": "根据剧本显式出现的人物、地点、道具和情节功能选择。"
    }
  ]
}
```

## 硬约束

1. 只能从输入的 available 文件列表中选择文件，不得发明路径。
2. 不要为了保险读取所有文件，只选择与当前章节明确相关的文件。
3. 如果不确定，把原因写入 `missing_context_notes`，不要乱选。
4. 二次读取最多申请一次。
5. `additional_selected_files.characters` 最多 3 个。
6. `additional_selected_files.environments` 最多 3 个。
7. 输出路径必须保持原样，不要改写文件名。

## 失败处理

- 如果剧本没有明确出现角色或场景，选择最相关的少量文件，并在 `warnings` 中说明。
- 如果 available 列表为空，返回空数组并在 `missing_context_notes` 中说明缺少上下文文件。
- 如果需要更多上下文但无法确定具体文件，设置 `need_more_context=false`，把说明写入 `missing_context_notes`。
