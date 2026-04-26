---
name: input-classifier
description: 判断用户输入属于哪个工作阶段，为 Lead 路由决策提供依据
tags: [classification, routing, lead]
---

## 输入

- **用户输入**：用户的原始文本
- **当前项目状态**（可选）：是否已有项目、已完成哪些章节

## 输出格式

```json
{
  "stage": "new_story | continue_chapter | modify | feedback | help | unclear",
  "confidence": 0.0-1.0,
  "reasoning": "判断依据的简要说明",
  "suggested_action": "建议 Lead 采取的下一步动作"
}
```

### 各阶段说明

| stage | 含义 | 示例输入 |
|-------|------|---------|
| `new_story` | 用户想创建新故事 | "我想写一个科幻故事" |
| `continue_chapter` | 用户想继续写下一章 | "开始第二章" / "通过" |
| `modify` | 用户想修改已有内容 | "把主角的名字改成小明" |
| `feedback` | 用户在给反馈或审批 | "这个角色不太好" / "通过" |
| `help` | 用户需要帮助 | "怎么用？" / "有什么功能？" |
| `unclear` | 无法判断意图 | "嗯" / "随便" |

## 硬约束

1. **必须返回 JSON 格式**
2. **confidence < 0.6 时，stage 应为 `unclear`**
   - 此时 suggested_action 应建议调用 `choice_designer` 生成选择题
3. **不能自行执行任何操作**
   - 只负责分类，不负责执行

## 失败处理

- **输入为空**：返回 `stage: "unclear"`
- **输入过长**（>500字）：截取前 500 字进行分类，标记 `[ℹ️ 输入已截断]`
- **多意图混合**：返回主要意图，在 reasoning 中说明次要意图
