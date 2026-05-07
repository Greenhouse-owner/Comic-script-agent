# Comic Script Agent - 工业级漫画分镜脚本创作系统

## 🎯 项目初衷

在接触真实商业项目的过程中，我发现许多客户都有创作 AI 漫画、AI 漫剧的需求，但他们面临着不同的起点和挑战：

- **有些客户**只有一个细微的灵感火花，想要探索这个想法能否发展成完整的故事
- **有些客户**希望将某个经典故事与新的创意结合，但不知道如何融合
- **有些客户**在内部讨论后已经有了大致的方向和章节大纲，但需要快速验证可行性
- **有些客户**已经有相对完善的剧本，但需要专业的分镜脚本来推进制作

**这个项目的核心目标是**：无论你处于创作的哪个阶段——从虚无缥缈的小灵感，到已经成型的剧本章节——都能快速验证、多次打磨，最终得到专业的分镜脚本和故事设定。

这不是一个简单的"输入 prompt 输出结果"的工具，而是一个**可以与你对话、理解你的想法、帮你完善创意、并生成工业级产物**的创作伙伴系统。

---

## 🏗️ 系统架构

### 核心设计理念

```
代码负责底层枢纽：路由、状态、文件、权限、任务、消息
业务判断交给 AI：分类、规划、创作、审查、修改影响分析
```

这意味着系统不会硬编码"剧情好不好"、"应该改哪个角色"这类创作判断，而是通过 **Skill 手册 + AI 模型**动态决策，保证了系统的灵活性和专业性。

### 多 Agent 协作架构

系统模拟了一个真实的漫画工作室，将创作流程拆分为多个专业角色：

```
用户（创作者）
    ↓
Lead Agent（总编）
    ├─→ 理解用户输入
    ├─→ 派发任务
    └─→ 汇总结果
    ↓
Architect Bot（剧情架构师）
    ├─→ 故事规划（Story Bible）
    ├─→ 角色设定（Character Cards）
    ├─→ 场景设定（Environment Cards）
    ├─→ 章节大纲（Chapter Outlines）
    └─→ 章节剧本（Chapter Scripts）
    ↓
Director Bot（分镜导演）
    ├─→ 上下文规划（Context Planning）
    ├─→ 分镜规划（Storyboard Planning）
    └─→ 分镜脚本（Storyboard Script）
    ↓
QA Bot（质检编辑）
    ├─→ 一致性审查（Consistency Review）
    ├─→ 影响分析（Impact Analysis）
    └─→ 修订回归检查（Revision Regression）
    ↓
返回给用户
```

### 分层架构设计

系统采用清晰的分层架构，每一层职责明确：

```
┌─────────────────────────────────────┐
│   Lead 编排层 (lead.py)              │  ← 总控：接收输入、派发任务、汇总结果
├─────────────────────────────────────┤
│   P3 Team 协作层 (p3_team.py)        │  ← 长期队友线程：Architect/Director/QA
├─────────────────────────────────────┤
│   P2 Content 内容层 (p2_content.py)  │  ← 漫画项目文件结构、角色/场景读写
├─────────────────────────────────────┤
│   P1 Skills 技能层 (p1_skills.py)    │  ← 技能加载器，读取 skills/*/SKILL.md
├─────────────────────────────────────┤
│   P0 Runtime 地基层 (p0_runtime.py)  │  ← 安全路径、文件读写、任务、消息、事件
└─────────────────────────────────────┘
        ↑                    ↑
   Planning 规划        Policy 权限
```

**P0 Runtime（地基层）**
- `safe_path`: 防止路径越界攻击
- `run_read/write`: 安全的文件读写
- `TaskManager`: 任务持久化到 tasks.json
- `MessageBus`: Agent 之间的消息传递
- `EventLogger`: 调试日志记录到 events.jsonl
- `BackgroundManager`: 后台任务管理

**P1 Skills（技能层）**
- 动态加载 `skills/*/SKILL.md` 手册
- 每个 Skill 是一份给 AI 看的专业指南
- 包含：输入输出格式、质量标准、约束条件、示例

**P2 Content（内容层）**
- 漫画项目文件结构管理
- 角色卡/场景卡的 CRUD 操作
- 章节脚本/分镜脚本的读写
- 项目状态扫描和一致性检查

**P3 Team（协作层）**
- `TeammateManager`: 管理 AI 队友的生命周期
- 每个 Bot 运行在独立的后台线程
- 通过 MessageBus 接收任务，执行后返回结果
- 支持动态规划（Plan-before-Execute）

**Lead 编排层**
- 接收用户输入，判断当前阶段
- 调用 Aux Subagent 进行分类、归一化、问题生成
- 派发任务给对应的 Bot
- 汇总结果返回给用户
- 处理用户反馈和修订请求

**Planning（规划治理）**
- Plan-before-Execute 模式
- 执行前先生成执行计划，校验产物契约
- 支持动态规划和静态规划两种模式

**Policy（权限治理）**
- 每个 Agent 有明确的工具权限
- 每个 Agent 有明确的文件路径权限
- 防止越权操作和数据泄露

---

## 🚀 为什么这是工业级系统

### 1. **完整的创作流程覆盖**

从灵感到成品，系统覆盖了漫画创作的全流程：

```
用户输入（任意形式）
    ↓
智能分类（input-classifier）
    ↓
澄清问题（choice-designer）
    ↓
故事方向（story-direction-summarizer）
    ↓
Architect 创作
    ├─ Story Bible（故事圣经）
    ├─ Character Cards（角色卡）
    ├─ Environment Cards（场景卡）
    ├─ Chapter Outlines（章节大纲）
    └─ Chapter Scripts（章节剧本）
    ↓
Director 分镜
    ├─ Context Planning（上下文规划）
    ├─ Storyboard Planning（分镜规划）
    └─ Storyboard Script（分镜脚本）
    ↓
QA 质检
    ├─ Consistency Review（一致性审查）
    ├─ Impact Analysis（影响分析）
    └─ Revision Regression（修订回归）
    ↓
用户反馈修订（无限次迭代）
```

### 2. **专业的产物质量**

系统生成的每一份文档都符合工业标准：

- **Story Bible**: 包含世界观、主题、基调、核心冲突、角色关系图
- **Character Cards**: 包含角色的外貌、性格、背景、动机、成长弧线、视觉锚点
- **Environment Cards**: 包含场景的视觉描述、氛围、功能、故事作用
- **Chapter Scripts**: 包含场景、角色、对白、动作、情绪、镜头提示
- **Storyboard Scripts**: 专业的分镜脚本，包含页码、格子编号、画面描述、对白、镜头语言

### 3. **智能的反馈闭环**

系统支持用户随时修改任何产物，并自动处理修改的影响：

```
用户提出修改
    ↓
FeedbackOrchestrator 分析
    ├─ 识别修改目标类型（角色/场景/剧本/分镜）
    ├─ 调用 change-impact-analyzer 分析影响范围
    └─ 生成修改指令（带约束）
    ↓
应用修改
    ├─ 角色/场景修改 → 直接应用 + Architect 后续任务
    └─ 剧本/分镜修改 → 创建对应 Bot 任务
    ↓
QA 检查
    ├─ 一致性审查（是否与 Story Bible 冲突）
    ├─ 影响分析（影响了哪些章节/角色/场景）
    └─ 修订回归（修改是否引入新问题）
    ↓
返回给用户
```

**关键约束机制**：
- **Level 只升不降**：修改只能提升细节层级，不能降低（防止信息丢失）
- **视觉锚点不删**：角色的视觉识别特征不能删除（保证视觉一致性）
- **影响分析**：自动识别修改会影响哪些其他产物，提示用户

### 4. **可靠的任务管理**

- **任务持久化**：所有任务保存到 `tasks.json`，系统重启后可恢复
- **消息总线**：Agent 之间通过 MessageBus 异步通信，解耦合
- **事件日志**：所有操作记录到 `events.jsonl`，可追溯、可调试
- **后台执行**：长时间任务在后台线程执行，不阻塞用户交互
- **超时保护**：每个任务有超时限制，防止无限等待

### 5. **安全的权限控制**

- **路径沙箱**：所有文件操作限制在 `workspace/` 目录内
- **工具权限**：每个 Agent 只能使用被授权的工具
- **文件权限**：每个 Agent 只能访问被授权的文件路径
- **输入验证**：所有用户输入经过验证和归一化

### 6. **灵活的技能系统**

- **Skill 手册**：每个技能是一份 Markdown 文档，易于阅读和修改
- **动态加载**：修改 Skill 手册后无需重启系统
- **版本控制**：Skill 手册可以版本管理，支持 A/B 测试
- **可扩展**：添加新技能只需创建新的 SKILL.md 文件

### 7. **完善的测试覆盖**

系统经过完整的端到端测试：

- ✅ 系统初始化和 Bot 启动
- ✅ 基础命令（/help, /status, /list）
- ✅ 故事输入流程（分类 → 澄清 → 方向）
- ✅ Architect 完整流程（6步骤，所有产物生成）
- ✅ Director 完整流程（3阶段 Pipeline，专业分镜脚本）
- ✅ 修订功能（角色修改、分镜修改、影响分析）
- ✅ QA 检查（一致性、影响分析、回归检查）

---

## 📊 性能数据

基于真实测试的性能指标：

| 阶段 | 耗时 | 说明 |
|------|------|------|
| 输入分类 | ~4秒 | input-classifier 识别输入类型 |
| 澄清问题生成 | ~7秒 | choice-designer 生成5个问题 |
| 故事方向总结 | ~5秒 | story-direction-summarizer 整理回答 |
| **Architect 执行** | **~147秒** | 生成 Story Bible、角色卡、场景卡、章节剧本 |
| **Director 执行** | **~120秒** | 生成12页48格专业分镜脚本 |
| QA 检查 | ~30秒 | 一致性审查 + 影响分析 |

**从灵感到分镜脚本，全流程约 5-6 分钟。**

---

## 🎨 适用场景

### 场景 1：灵感验证
**用户**：我有一个想法，"一个能看到别人梦境的少年"，但不知道能不能做成故事。

**系统**：
1. 提出5个澄清问题（基调、冲突、章节数、反派、结局）
2. 根据回答生成 Story Bible 和角色设定
3. 生成第一章剧本和分镜脚本
4. 用户看到成品，决定是否继续

### 场景 2：故事融合
**用户**：我想把《小王子》的主题和赛博朋克世界观结合。

**系统**：
1. 理解两个元素的核心特征
2. 提出融合方案的澄清问题
3. 生成融合后的 Story Bible
4. 创建符合赛博朋克风格的角色和场景
5. 生成保留《小王子》主题的剧本

### 场景 3：大纲扩展
**用户**：我已经有了3章的大纲，需要扩展成完整剧本和分镜。

**系统**：
1. 读取用户提供的大纲
2. 生成 Story Bible 确保一致性
3. 为每一章生成详细剧本
4. 为每一章生成分镜脚本
5. QA 检查章节之间的连贯性

### 场景 4：剧本打磨
**用户**：我有完整剧本，但需要调整角色性格和部分情节。

**系统**：
1. 导入现有剧本
2. 用户提出修改（"把主角改成更勇敢的性格"）
3. 系统分析影响范围（哪些章节需要调整）
4. 应用修改并更新所有受影响的产物
5. QA 检查修改是否引入矛盾

---

## 🛠️ 技术栈

- **语言**: Python 3.9+
- **AI 模型**: OpenAI GPT-4 / Claude (可配置)
- **架构**: 多 Agent 协作 + 消息总线
- **存储**: 文件系统（JSON + Markdown）
- **并发**: 线程池 + 后台任务管理

---

## 📦 项目结构

```
Comic-script-agent/
├── src/                      # 核心代码
│   ├── lead.py               # Lead Agent 主控
│   ├── p0_runtime.py         # 地基层：文件、任务、消息、事件
│   ├── p1_skills.py          # 技能加载器
│   ├── p2_content.py         # 内容层：项目文件管理
│   ├── p3_team.py            # 协作层：Bot 管理
│   ├── planning.py           # 规划治理
│   ├── policy.py             # 权限治理
│   ├── intake.py             # 输入归一化
│   ├── feedback_orchestrator.py  # 反馈闭环编排
│   └── feedback_loop.py      # 反馈循环逻辑
├── skills/                   # AI 技能手册
│   ├── input-classifier/     # 输入分类
│   ├── choice-designer/      # 澄清问题生成
│   ├── story-direction-summarizer/  # 故事方向总结
│   ├── architect-plan-designer/     # Architect 规划
│   ├── story-planner/        # 故事规划
│   ├── character-builder/    # 角色创建
│   ├── environment-builder/  # 场景创建
│   ├── chapter-expander/     # 章节扩展
│   ├── director-context-planner/    # Director 上下文规划
│   ├── director-storyboard-planner/ # 分镜规划
│   ├── panel-director/       # 分镜脚本生成
│   ├── change-impact-analyzer/      # 修改影响分析
│   ├── project-consistency-reviewer/  # 一致性审查
│   └── revision-regression-reviewer/  # 修订回归检查
├── workspace/                # 工作空间
│   └── comics/               # 漫画项目
│       └── <project_id>/     # 单个项目
│           ├── brief.md      # 项目简介
│           ├── story_direction.md  # 故事方向
│           ├── story_bible.md      # 故事圣经
│           ├── chapter_outlines.md # 章节大纲
│           ├── chapters/     # 章节
│           │   └── ch01/
│           │       ├── outline.md   # 章节大纲
│           │       ├── script.md    # 章节剧本
│           │       └── storyboard.md  # 分镜脚本
│           ├── state/        # 项目状态
│           │   ├── characters/  # 角色卡
│           │   └── environments/  # 场景卡
│           ├── plans/        # 执行计划
│           ├── qa/           # QA 报告
│           └── feedback/     # 反馈记录
├── tests/                    # 测试
├── config.json               # 配置文件
└── .env                      # 环境变量
```

---

## 🎯 核心优势总结

1. **从灵感到成品的完整流程**：无论你的起点在哪里，系统都能帮你走到终点
2. **工业级产物质量**：生成的文档符合专业漫画制作标准
3. **智能反馈闭环**：支持无限次修改和打磨，自动处理影响分析
4. **多 Agent 协作**：模拟真实工作室，分工明确，协作高效
5. **安全可靠**：完善的权限控制、任务管理、错误处理
6. **灵活可扩展**：Skill 手册易于修改，系统易于扩展

---

## 📄 License

MIT License

---

## 🤝 贡献

欢迎提交 Issue 和 Pull Request！

---

**让创作回归创意本身，让 AI 处理繁琐的执行细节。**
