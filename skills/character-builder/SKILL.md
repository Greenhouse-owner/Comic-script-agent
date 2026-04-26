---
name: character-builder
description: 为故事角色创建完整的角色卡
tags: [character, design, visual]
---

## 输入

- **角色名字**
- **角色定位**：主角/对手/配角/群众
- **故事大纲**（用于确保角色符合世界观）

## 输出格式

输出结构化 JSON（存入 `state/characters.json`）：

```json
{
  "name": "林夜",
  "role": "主角",
  "visual_anchors": [
    "黑色风衣（过膝长款）",
    "左眼竖瞳（金色）",
    "银色短发（碎发造型）"
  ],
  "personality": {
    "core_trait": "沉默但内心炽热",
    "motivation": "寻找失踪的妹妹",
    "fatal_flaw": "过度自责，不愿求助"
  },
  "forbidden_changes": [
    "性别不能改变",
    "左眼竖瞳是能力象征，不能消失"
  ],
  "relationships": {
    "李雨": "青梅竹马，暗恋对象",
    "黑狐": "亦师亦友，引路人"
  },
  "first_appearance": "ch01"
}
```

## 硬约束

1. **visual_anchors 至少 3 个**
   - 这是 QA 检查角色一致性的核心依据
   - 必须是具体可画的特征（"帅气"不行，"剑眉星目"可以）
2. **forbidden_changes 至少 1 个**
   - 防止后续章节无意中改变关键设定
3. **personality.motivation 必须具体**
   - 不能是"想变强"这种空泛目标
   - 要有具体的事件驱动，如"为了救回被绑架的妹妹"

## 失败处理

- **信息不足**：返回带 `[待补充]` 标记的半成品角色卡，要求补充
- **与世界观冲突**：标记冲突点，提供修改建议
