# Director 优化计划：三阶段分镜 Pipeline

本计划基于工程风险复盘收敛：保留 Director 的专业分镜能力，但不再实现为 6-7 个强依赖串行 LLM 调用链。

核心原则：

```text
少阶段、强约束、可恢复、可调试。
```

---

## 1. 关键修正

前一版链路是：

```text
input-selector → beat-splitter → page-planner → tension-designer → layout-planner → draft-writer
```

问题：

```text
调用链过长；中间 JSON 依赖复杂；LLM 判断不稳定；修订时不知道复用还是重跑；失败恢复缺失；Lead 边界容易混乱。
```

新方案改成：

```text
阶段 0：Lead / aux_subagent 生成 Director task brief
阶段 1：Director Context Planner 选择上下文
阶段 2：Director Storyboard Planner 综合规划分镜
阶段 3：Storyboard Draft Writer 生成分镜脚本
```

节拍、分页、张力、格子布局仍然要做，但合并到 `director-storyboard-planner` 一次综合规划，不拆成多个强依赖串行调用。

---

## 2. 主流程

主流程不是用户一开始主动要求 Lead 转分镜，而是 Architect 完成后 Lead 推进下一阶段：

```text
Architect 完成章节剧本
↓
Lead 展示 Architect 交付摘要
↓
Lead 询问用户是否进入 Director 分镜阶段
↓
用户选择章节并确认
↓
Lead 调用 aux_subagent + director-task-brief-designer + 模型
↓
aux_subagent 输出 Director task brief
↓
Lead 校验 brief 并创建 director_delivery task
↓
Director 执行三阶段 pipeline
↓
写入 storyboard.md、selected_context.json、storyboard_plan.json、pipeline_status.json
↓
Lead 展示给用户
↓
用户反馈后，Lead 调用 aux_subagent + storyboard-revision-planner 生成修订 brief
↓
Director 按修订决策树局部重跑或全文重写
```

职责边界：

```text
Lead = workflow orchestrator
aux_subagent + skills + model = task brief designer
Director = task executor
```

---

## 3. Lead 边界

Lead 可以做：

```text
检测 Architect 是否完成；询问用户是否进入 Director；收集 project_id/chapter_id/script_path；读取可用文件名列表；调用 aux_subagent；校验 JSON 安全；创建 task；展示结果。
```

Lead 不可以做：

```text
不判断读哪些角色卡；不判断分几页；不判断每页几个格子；不判断哪个格子要大；不写 storyboard_plan；不直接构造业务细节。
```

Architect 完成后，Lead 示例话术：

```text
Architect 已完成 ch01 剧本。
是否进入下一阶段，让 Director 把 ch01 转成分镜脚本？
1. 进入 Director 分镜阶段
2. 先查看 ch01 剧本
3. 先修改 Architect 剧本
4. 暂不处理
```

Lead 调用：

```text
aux_subagent.run("director_task_brief_designer", context)
```

建议新增 skill：

```text
director-task-brief-designer
```

brief 示例：

```json
{
  "task_type": "director_delivery",
  "project_id": "story_20260504_144428",
  "chapter_id": "ch01",
  "script_path": "story_20260504_144428/chapters/ch01/script.md",
  "output_path": "story_20260504_144428/chapters/ch01/storyboard.md",
  "input_discovery_mode": "progressive_bounded",
  "pipeline": ["director-context-planner", "director-storyboard-planner", "storyboard-draft-writer"],
  "expected_outputs": {
    "selected_context": "story_20260504_144428/chapters/ch01/director_plan/selected_context.json",
    "storyboard_plan": "story_20260504_144428/chapters/ch01/director_plan/storyboard_plan.json",
    "storyboard": "story_20260504_144428/chapters/ch01/storyboard.md"
  },
  "requires_user_review": true,
  "submission_target": "lead"
}
```

Lead 只校验：路径在 workspace 内、文件存在、pipeline 属于允许枚举、输出目录合法；不改写业务判断。

---

## 4. 三阶段 Director Pipeline

### 4.1 Director Context Planner

建议 skill：

```text
director-context-planner
```

合并原来的 input-selector、coverage check、二次读取申请。

目标：选择刚好够用的角色/场景上下文。

输入：

```text
script.md、available_character_files、available_environment_files、project_id、chapter_id、task brief
```

输出：

```text
chapters/{chapter_id}/director_plan/selected_context.json
```

结构：

```json
{
  "initial_selected_files": {"characters": [], "environments": []},
  "additional_selected_files": {"characters": [], "environments": []},
  "final_selected_files": [],
  "missing_context_notes": [],
  "warnings": [],
  "selection_rounds": []
}
```

约束：

```text
available 文件列表由代码从 workspace 得到；LLM 只能从 available 中选择；MVP 最多 1 次 additional read；每次最多追加 3 个角色文件和 3 个场景文件；非法路径丢弃并记录 warning；coverage check 失败不阻断主流程。
```

### 4.2 Director Storyboard Planner

建议 skill：

```text
director-storyboard-planner
```

合并原来的 beat-splitter、page-planner、tension-designer、layout-planner。

目标：一次性综合规划：

```text
故事节拍、页数、每页格子数、关键大格、留白、splash、阅读节奏、张力策略。
```

输出：

```text
chapters/{chapter_id}/director_plan/storyboard_plan.json
```

结构：

```json
{
  "chapter_id": "ch01",
  "recommended_page_count": 12,
  "total_panel_count": 48,
  "beats": [],
  "page_plan": [],
  "layout_plan": [],
  "tension_strategy": [],
  "warnings": []
}
```

格子大小枚举：

```text
small、medium、large、wide_large、tall_narrow、splash、silent_insert、reaction_closeup
```

规划原则：

```text
不是每页固定 4 格；安静铺垫页可 3-4 格；对话密集页可 5-6 格但避免拥挤；动作追逐用连续小格；奇观/高潮/转折用大格或 splash；每页有明确视觉目标；翻页点服务悬念或情绪停顿；角色首次出场要给视觉锚点；分镜规划不能改写剧本事实。
```

### 4.3 Storyboard Draft Writer

建议 skill：

```text
storyboard-draft-writer
```

输入：

```text
script.md、selected_context.json、storyboard_plan.json、已读取角色卡、已读取场景卡、panel-director、reaction-polisher
```

输出：

```text
chapters/{chapter_id}/storyboard.md
```

格式：

```text
# ch01 分镜脚本
## 分镜规划摘要
- 页数 / 总格数 / 关键大格 / 节奏说明
## 第 1 页
- 本页功能 / 情绪目标 / 格子数
### 格 1
- 大小 / 景别 / 机位 / 画面 / 人物 / 视觉重点 / 对白旁白 / 音效
```

---

## 5. 二次读取实现

二次读取是有边界的模型申请机制：

```text
第一次 LLM 选择上下文 → Director 读取第一批文件 → coverage check → 模型申请 additional_files → 代码校验路径和数量 → 再读取 → 合并进 selected_context.json → 最多 1 轮，失败不阻断主流程。
```

安全规则：

```text
LLM 只能从 available 里选；additional_files 必须路径校验；不能读 workspace 外文件；不能读不存在文件；不能重复读取；不能无限循环；coverage check 失败只记录 warning。
```

伪代码：

```python
def _discover_director_context(project_id, chapter_id, script_text, policy_context):
    available = list_director_context_files(project_id)
    selected = select_context_with_llm(script_text, available)
    selected_files = validate_selected_files(selected, available, policy_context)
    context_docs = read_context_files(selected_files)
    warnings = []
    additional_files = []
    try:
        coverage = coverage_check_with_llm(script_text, context_docs, available)
    except Exception as exc:
        coverage = {"need_more_context": False}
        warnings.append(f"coverage_check_failed: {exc}")
    if coverage.get("need_more_context"):
        requested = coverage.get("additional_files", [])
        additional_files = validate_selected_files(requested, available, policy_context, already_selected=selected_files, max_characters=3, max_environments=3)
        context_docs.update(read_context_files(additional_files))
    return {"initial_selected_files": selected_files, "additional_selected_files": additional_files, "final_selected_files": selected_files + additional_files, "warnings": warnings}
```

---

## 6. 状态管理与 fallback

Director task 状态：

```text
queued、running_context_planner、context_ready、running_storyboard_planner、plan_ready、running_draft_writer、draft_ready、submitted、revision_requested、finalized、error
```

写入：

```text
chapters/{chapter_id}/director_plan/pipeline_status.json
```

错误恢复：

| 错误类型 | 处理方式 |
|---|---|
| LLM 输出非 JSON | retry 1 次 |
| JSON 字段缺失 | 默认值补齐并 warning |
| 路径非法 | 丢弃并 warning |
| 文件不存在 | 跳过并 missing_context_notes |
| coverage check 失败 | 跳过二次读取 |
| storyboard_plan 失败 | 使用 fallback plan |
| storyboard.md 失败 | 简化 prompt 重试 1 次 |
| 多次失败 | task error，并给 Lead 可读失败报告 |

fallback storyboard_plan：

```text
按 script 标题/段落粗切 beats；每页默认 4 格；每 4 个 beats 组成一页；高张力关键词给 large panel；对话页 medium panels；动作页 small panels；写入 warnings。
```

---

## 7. 修订决策树

建议新增：

```text
storyboard-revision-planner
```

MVP 反馈分类：

| revision_type | 示例 | 重跑范围 |
|---|---|---|
| `text_revision_only` | 改表情、对白、画面描述 | 复用 selected_context 和 storyboard_plan，只重写 storyboard |
| `layout_or_pacing_revision` | 页太挤、节奏太快、某处要大格 | 复用 selected_context，重写 storyboard_plan 和 storyboard |
| `context_missing_revision` | 漏了角色/场景设定 | 重跑 selected_context、storyboard_plan、storyboard |

Lead 不判断 `revision_type`，只调用 aux_subagent + `storyboard-revision-planner`，校验 revision brief 后转成 `director_storyboard_revision` task。

---

## 8. 输出文件与 submission

MVP 输出：

```text
chapters/{chapter_id}/storyboard.md
chapters/{chapter_id}/director_plan/selected_context.json
chapters/{chapter_id}/director_plan/storyboard_plan.json
chapters/{chapter_id}/director_plan/pipeline_status.json
```

submission 摘要应包含：

```json
{
  "type": "submission",
  "from_role": "director",
  "task_type": "director_delivery",
  "deliverables": [".../storyboard.md"],
  "director_plan_files": {
    "selected_context": ".../selected_context.json",
    "storyboard_plan": ".../storyboard_plan.json",
    "pipeline_status": ".../pipeline_status.json"
  },
  "storyboard_summary": {
    "pages": 12,
    "panels": 48,
    "large_panel_pages": [2, 9],
    "warnings": []
  },
  "requires_user_review": true
}
```

---

## 9. 实施计划

```text
Phase 1：Lead/aux_subagent 生成 Director task brief
Phase 2：Director Context Planner + selected_context.json
Phase 3：Director Storyboard Planner + storyboard_plan.json + fallback plan
Phase 4：Storyboard Draft Writer + storyboard.md
Phase 5：storyboard-revision-planner + 修订决策树
Phase 6：finalize storyboard + 可选 QA
```

MVP 只做：

```text
director-task-brief-designer
director-context-planner
director-storyboard-planner
storyboard-draft-writer
selected_context.json
storyboard_plan.json
pipeline_status.json
storyboard.md
```

暂时不做：

```text
独立 beat/page/tension/layout 串行链；复杂多轮二次读取；自动 QA；复杂版本历史；局部 panel patch 写回。
```

---

## 10. 一句话总结

下一阶段 Director 的核心不是堆更多串行 skills，而是构建稳健的三阶段分镜 Pipeline：

```text
Lead 在 Architect 完成后询问用户是否进入分镜阶段；Lead 调用 aux_subagent 生成 Director task brief；Director 用 context-planner 选择必要上下文；用 storyboard-planner 综合规划节拍、页数、张力和布局；用 draft-writer 生成 storyboard.md；整个过程有状态、有 warning、有 fallback、有修订决策树。
```
