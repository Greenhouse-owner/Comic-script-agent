---
name: director-storyboard-planner
description: 综合规划指定章节分镜的故事节拍、页数、格子布局与张力策略
tags: [director, storyboard, pacing, layout]
---

## 输入

- `script.md`：章节剧本全文
- `selected_context.json`：上下文选择结果
- 已读取的角色卡内容
- 已读取的场景卡内容
- `panel-director` 技能手册
- 用户风格偏好或 task brief

## 输出格式

必须输出 JSON 对象，不要 Markdown。

```json
{
  "chapter_id": "ch01",
  "recommended_page_count": 12,
  "total_panel_count": 48,
  "beats": [
    {
      "beat_id": "b01",
      "summary": "本节拍摘要",
      "source_script_range": "对应剧本位置",
      "story_function": "setup | conflict | reveal | action | emotional_pause | climax | resolution",
      "emotional_tension": 1,
      "visual_importance": 1,
      "must_show": []
    }
  ],
  "page_plan": [
    {
      "page": 1,
      "beats": ["b01"],
      "page_function": "establishing",
      "emotional_goal": "本页情绪目标",
      "panel_count": 4,
      "density": "low | medium | high",
      "page_turn_hook": "翻页钩子或空字符串"
    }
  ],
  "layout_plan": [
    {
      "page": 1,
      "panels": [
        {
          "panel_id": "p01_01",
          "beat_id": "b01",
          "size": "wide_large",
          "relative_area": 0.4,
          "shot_type": "远景",
          "purpose": "本格目的",
          "reading_order": 1
        }
      ]
    }
  ],
  "tension_strategy": [
    {
      "target": "b01",
      "tension_level": 1,
      "pacing": "slow | normal | fast | accelerate_then_release",
      "layout_advice": "张力与版面建议"
    }
  ],
  "warnings": []
}
```

## 硬约束

1. 不得改写剧本事实。
2. 每页建议 3-6 格，特殊高潮页可 splash。
3. 格子大小只能使用：`small`、`medium`、`large`、`wide_large`、`tall_narrow`、`splash`、`silent_insert`、`reaction_closeup`。
4. 安静铺垫页可以低密度，动作页可以连续小格，高潮/奇观/转折必须给更高视觉权重。
5. 角色首次出场要给足视觉锚点，场景首次建立要有环境格。
6. 每个 layout panel 必须能追溯到一个 beat_id。
7. 页码、格子阅读顺序必须连续。

## 失败处理

- 如果剧本结构不清，按自然段粗切 beats，并在 `warnings` 中说明。
- 如果无法可靠判断页数，使用每页 4 格的 fallback 密度。
- 如果缺少角色/场景上下文，仍输出计划，但在 `warnings` 中标明。
