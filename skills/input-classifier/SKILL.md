---
name: input-classifier
description: 判断用户输入属于哪个工作阶段，为 Lead 路由决策提供依据
tags: [classification, routing, lead]
---

## 输入

- **用户输入**：用户的原始文本
- **当前项目状态**（可选）：是否已有项目、当前项目 ID、当前章节 ID、对话历史摘要

## 输出格式

必须只输出 JSON 对象，不要输出 Markdown、代码块或解释文字。

```json
{
  "input_type": "new_story | story_direction | worldbuilding | chapter_outline | chapter_detail_outline | character_notes | environment_notes | script_draft | mixed_notes | write_chapter | feedback | revision_request | vague_demand",
  "confidence": 0.0,
  "key_info": {
    "project_hint": "用户提到的项目或故事名，没有则为空字符串",
    "chapter_hint": "用户提到的章节，没有则为空字符串",
    "topic": "用户输入的核心主题或要求",
    "missing_info": ["仍需确认的信息"]
  },
  "suggestion": "建议 Lead 采取的下一步动作"
}
```

### input_type 说明

| input_type | 含义 | 示例输入 | 建议动作 |
|---|---|---|---|
| `new_story` | 用户提供一句话或短故事种子，准备开启新的漫画创作流程 | “我想写一个关于 `<主角>` 面对 `<核心冲突>` 的漫画” | 不要直接写剧本；先生成创作方向选择题 |
| `story_direction` | 用户已经给出较明确主题、基调、题材或创作方向 | “这是一个关于记忆交易和幸福代价的科幻故事” | 归一化为 `story_direction.md`，缺关键项再澄清 |
| `worldbuilding` | 用户主要提供世界观、规则、时代背景、组织结构 | “未来城市由三家公司控制，记忆可以交易……” | 归一化为 `story_bible.md`，再补章节规划 |
| `chapter_outline` | 用户提供多章章纲、章节列表或章节标题和主事件 | “第一章……第二章……第三章……” | 保留用户章纲，写 `chapter_outlines.md` 并拆分章节 outline |
| `chapter_detail_outline` | 用户提供某一章细纲、场景列表、事件节拍 | “第一章细纲：1……2……3……” | 写 `chapters/chXX/outline.md`，再补全局章纲 |
| `character_notes` | 用户主要提供人物设定、主角/反派/配角信息 | “主角：林小北，12岁……” | 写 `state/characters/*.md`，再补缺失事实源 |
| `environment_notes` | 用户主要提供场景、地点、环境或关键道具设定 | “场景：废弃天文台……” | 写 `state/environments/*.md`，再补缺失事实源 |
| `script_draft` | 用户给出已写好的剧本、对白、分场草稿 | “第一章草稿：林小北站在操场……” | 保存为 `draft_input.md`，生成诊断/修订计划，不直接覆盖 |
| `mixed_notes` | 用户输入混合多种材料，无法只归为一种 | 同时有章纲、角色和世界观 | 分类抽取，多文件归一化，生成执行计划 |
| `write_chapter` | 用户明确要求开始、继续或生成某一章 | “开始写第一章” / “继续第二章” | 检查是否已有故事方向、细纲、角色和场景，再派发章节任务 |
| `feedback` | 用户对已有交付物提出反馈 | “这里角色动机不够强” | 生成结构化修改指令 |
| `revision_request` | 用户明确要求修改、重写、润色已有稿件 | “把第一章重写得更紧张” | 保存需求并生成修订计划 |
| `vague_demand` | 用户意图不够明确，无法判断下一步具体动作 | “继续” / “你看着办” / “帮我弄一下” | 生成澄清选择题 |

## 判断规则

1. 用户给出短故事概念、题材、主角和冲突，即使信息还不完整，也应优先判为 `new_story`。
2. 用户给的是已经较成型的主题、类型、基调、结局方向，判为 `story_direction`。
3. 用户给的是世界观规则、组织、时代背景、能力系统，判为 `worldbuilding`。
4. 用户列出多章“第一章/第二章/第三章”等，判为 `chapter_outline`。
5. 用户给的是单章细纲、场景节拍、事件 1/2/3，判为 `chapter_detail_outline`。
6. 用户主要写人物、主角、反派、配角、角色卡，判为 `character_notes`。
7. 用户主要写地点、场景、环境、道具、氛围，判为 `environment_notes`。
8. 用户给出正文、对白、分镜式剧本草稿，判为 `script_draft`。
9. 多种材料混合且难以确定主类，判为 `mixed_notes`。
10. 用户明确说“写第几章”“继续下一章”“生成剧本/分镜”，判为 `write_chapter`。
11. 用户输入太短、指代不明或无法判断具体动作，判为 `vague_demand`。
12. `confidence` 必须是 0 到 1 的数字。
13. 如果 `confidence < 0.55`，应使用 `vague_demand`，并在 `key_info.missing_info` 里说明需要确认什么。
14. 不要自行执行任何操作，只负责分类。

## 失败处理

- 输入为空：返回 `input_type: "vague_demand"`，`confidence: 0.0`。
- 输入过长：只提取核心意图，不要复述全文。
- 多意图混合：选择当前最应该优先处理的主意图，并把次要信息放入 `key_info`。
