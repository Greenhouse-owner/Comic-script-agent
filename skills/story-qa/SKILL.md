---
name: story-qa
description: 质检剧本和分镜，重点检查与角色/场景数据库的一致性
tags: [qa, quality, consistency, data-driven]
---

## 输入

- **项目 ID**：project_id
- **章节 ID**：chapter_id（如 "ch01"）
- **剧本**：`chapters/{chapter_id}/script.md`
- **分镜**：`chapters/{chapter_id}/storyboard.md`
- **角色数据库**：`state/characters/` 文件夹下所有角色 `.md` 文件
- **场景数据库**：`state/environments/` 文件夹下所有场景 `.md` 文件

## 输出格式

```json
{
  "status": "PASS" | "FAIL",
  "issues": [
    {
      "type": "unregistered_character | visual_anchor_mismatch | personality_mismatch | level_violation | prop_mismatch | logic_error | storyboard_gap",
      "severity": "critical | major | minor",
      "description": "具体问题描述",
      "location": "script:场景3 或 storyboard:第5页"
    }
  ]
}
```

## 硬约束

1. **角色注册检查**
   - 剧本/分镜中出现的每个角色名，必须在 `state/characters/` 文件夹中有对应的 `.md` 文件
   - 违反 → `type: "unregistered_character"`, `severity: "critical"`
2. **角色视觉一致性**
   - 分镜中的角色外貌描写，必须与对应角色文件中的视觉锚点一致
   - 角色首次登场必须体现全部视觉锚点
   - 违反 → `type: "visual_anchor_mismatch"`, `severity: "critical"`
3. **角色行为一致性**
   - 角色的言行必须符合其角色文件中的性格特质设定
   - 违反 → `type: "personality_mismatch"`, `severity: "major"`
4. **Level 越权检查**
   - Level 1 角色不能有内心独白和深层动机展示
   - 违反 → `type: "level_violation"`, `severity: "major"`
5. **场景道具检查**
   - 场景描写中的道具必须与对应场景文件中的 key_props 一致
   - 违反 → `type: "prop_mismatch"`, `severity: "minor"`
6. **必须检查剧情逻辑**
   - 情节发展必须合理，不能有因果矛盾
   - 不能违反 `forbidden_changes`
7. **必须检查分镜覆盖度**
   - 剧本中的关键情节节拍必须有对应的分镜格子
5. **判定规则**
   - 任何 `critical` 问题 → 整体 **FAIL**
   - `major` 问题 ≥ 3 个 → 整体 **FAIL**
   - 仅有 `minor` 问题 → **PASS**（附带建议）
   - 0 个问题 → **PASS**

## 失败处理

- **剧本文件不存在**：返回错误
- **分镜文件不存在**：仅检查剧本部分，标记 `[ℹ️ 分镜缺失，跳过分镜检查]`
- **角色数据库为空**：标记所有角色为 `unregistered_character` (critical)
- **场景数据库为空**：跳过场景相关检查，标记 `[ℹ️ 场景数据库为空]`
