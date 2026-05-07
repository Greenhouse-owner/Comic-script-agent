# Comic Script Agent - 工业级漫画分镜脚本生成系统

> 从灵感到分镜，让 AI 与创作者共同打磨优秀故事

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)

[English](README.md) | 简体中文

---

## 📖 项目初衷

在接触真实的漫画和漫剧项目时，我发现了一个普遍的痛点：

- **有灵感的客户**：他们脑海中有一个虚无缥缈的小想法，或者想把某个故事和一个创意结合起来，但不知道如何具体化
- **有方向的团队**：他们在内部讨论中已经有了大致的方向和章纲，但需要快速验证这个方向是否可行
- **需要打磨的创作者**：他们有初步的剧本，但需要多次迭代才能达到理想的分镜效果

**这个项目的目标是**：无论你是一个虚无缥缈的小灵感，还是已经相对完善的剧本章节，都能快速验证是否是你想要的分镜脚本和故事基底。

---

## 🎯 核心价值

### 为什么这是一个工业级项目？

1. **完整的创作流程**
   - 从用户输入 → 故事规划 → 剧本创作 → 分镜脚本 → 质量检查
   - 每个环节都有专门的 Agent 负责，模拟真实漫画工作室的协作模式

2. **多轮迭代能力**
   - 支持针对任何环节的修改反馈
   - 自动分析修改影响范围
   - 智能回归检查，确保修改不破坏已有内容

3. **工业级质量保证**
   - 三层 QA 机制：一致性审查、影响分析、修订回归
   - 每个产物都有明确的质量标准（quality_bar）
   - 自动检测角色、场景、情节的一致性

4. **可扩展的架构**
   - 基于 Skill 的模块化设计，业务逻辑与代码解耦
   - 支持自定义 Agent 和工作流
   - 完善的权限和策略管理

---

## 🏗️ 系统架构

### 核心设计理念

```
代码做底层枢纽：路由、状态、文件、权限、任务、消息
业务判断交给 Skills + 模型：分类、规划、创作、审查、修改影响分析
```

### 多 Agent 协作模式

```
用户输入
    ↓
Lead Agent (总编)
    ├─ 理解输入意图
    ├─ 派发任务
    └─ 汇总结果
    ↓
Architect Bot (剧情架构师)
    ├─ 故事规划 (story_bible)
    ├─ 章节结构 (chapter_outlines)
    ├─ 角色设计 (characters)
    ├─ 场景设计 (environments)
    └─ 章节剧本 (script)
    ↓
Director Bot (分镜导演)
    ├─ 上下文规划 (context_planner)
    ├─ 分镜规划 (storyboard_planner)
    └─ 分镜脚本 (storyboard)
    ↓
QA Bot (质检编辑)
    ├─ 一致性审查
    ├─ 影响分析
    └─ 修订回归检查
    ↓
返回给用户
```

### 分层架构

```
┌─────────────────────────────────────┐
│   Lead Agent (编排层)                │
│   - 用户交互                         │
│   - 任务派发                         │
│   - 结果汇总                         │
└─────────────────────────────────────┘
              ↓
┌─────────────────────────────────────┐
│   P3 Team (协作层)                   │
│   - Architect Bot                   │
│   - Director Bot                    │
│   - QA Bot                          │
│   - 消息总线 (MessageBus)            │
└─────────────────────────────────────┘
              ↓
┌─────────────────────────────────────┐
│   P2 Content (内容层)                │
│   - 项目结构管理                     │
│   - 角色/场景读写                    │
│   - 文件组织                         │
└─────────────────────────────────────┘
              ↓
┌─────────────────────────────────────┐
│   P1 Skills (技能层)                 │
│   - 30+ 专业技能                     │
│   - 动态加载                         │
│   - 模型驱动                         │
└─────────────────────────────────────┘
              ↓
┌─────────────────────────────────────┐
│   P0 Runtime (地基层)                │
│   - 任务管理 (TaskManager)           │
│   - 消息总线 (MessageBus)            │
│   - 事件日志 (EventLogger)           │
│   - 安全文件操作                     │
└─────────────────────────────────────┘
```

### 关键特性

#### 1. Plan-Before-Execute 机制
每个任务执行前都会生成执行计划，确保：
- 明确的目标和产物
- 清晰的执行步骤
- 可验证的质量标准

#### 2. 动态技能系统
- 30+ 专业技能，涵盖故事创作的各个环节
- 技能以 Markdown 文档形式存储，易于维护和扩展
- 模型根据技能手册自主判断和执行

#### 3. 三阶段 Director Pipeline
```
Context Planner → Storyboard Planner → Draft Writer
     ↓                  ↓                   ↓
  选择上下文        规划分镜结构        生成最终脚本
```

#### 4. 完整的反馈闭环
```
用户反馈 → 影响分析 → 生成修改计划 → 执行修改 → 回归检查 → 返回结果
```

---

## 🚀 快速开始

### 安装

```bash
# 克隆项目
git clone https://github.com/yourusername/Comic-script-agent.git
cd Comic-script-agent

# 创建虚拟环境
python3 -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# 安装依赖
pip install -r requirements.txt

# 配置 API Key
cp .env.example .env
# 编辑 .env 文件，填入你的 OpenAI API Key
```

### 基础使用

```bash
# 启动交互式界面
python src/lead.py

# 或者使用测试脚本
python test_fixed.py
```

### 创作流程示例

```python
# 1. 输入故事想法
"我想创作一个儿童奇幻漫画，主角是一个住在海边灯塔里的女孩米娅..."

# 2. 回答澄清问题
系统会自动生成 5 个澄清问题，帮助你明确创作方向

# 3. 自动生成产物
- story_bible.md (故事圣经)
- chapter_outlines.md (章节结构)
- characters/*.md (角色卡)
- environments/*.md (场景卡)
- chapters/ch01/script.md (章节剧本)
- chapters/ch01/storyboard.md (分镜脚本)

# 4. 查看和修改
/show story_bible
/show chapter ch01 storyboard
/revise "米娅的性格应该更活泼一些"
```

---

## 📂 项目结构

```
Comic-script-agent/
├── src/                          # 核心代码
│   ├── lead.py                   # Lead Agent 主控
│   ├── p0_runtime.py             # 地基层：任务、消息、事件
│   ├── p1_skills.py              # 技能加载器
│   ├── p2_content.py             # 内容管理
│   ├── p3_team.py                # 队友管理
│   ├── planning.py               # 计划生成
│   ├── policy.py                 # 权限策略
│   ├── intake.py                 # 输入归一化
│   └── feedback_orchestrator.py  # 反馈编排
│
├── skills/                       # 技能库 (30+ 技能)
│   ├── input-classifier/         # 输入分类
│   ├── story-planner/            # 故事规划
│   ├── character-builder/        # 角色构建
│   ├── panel-director/           # 分镜导演
│   ├── story-qa/                 # 故事质检
│   └── ...
│
├── workspace/                    # 工作空间
│   └── comics/                   # 漫画项目
│       └── story_*/              # 具体项目
│           ├── brief.md
│           ├── story_direction.md
│           ├── story_bible.md
│           ├── chapter_outlines.md
│           ├── state/
│           │   ├── characters/
│           │   └── environments/
│           └── chapters/
│               └── ch01/
│                   ├── outline.md
│                   ├── script.md
│                   └── storyboard.md
│
├── tests/                        # 测试文件
├── config.json                   # 配置文件
└── .env                          # 环境变量
```

---

## 🎨 核心功能

### 1. 智能输入理解
- 自动识别输入类型（新故事、修改请求、查询等）
- 生成针对性的澄清问题
- 归一化用户输入为结构化数据

### 2. 完整的故事创作流程
- **Architect 阶段**：故事规划、角色设计、场景设计、剧本创作
- **Director 阶段**：分镜规划、镜头设计、画面描述
- **QA 阶段**：一致性检查、质量保证

### 3. 多轮迭代支持
```bash
# 查看当前状态
/status

# 查看产物
/show story_bible
/show characters
/show chapter ch01 storyboard

# 提出修改
/revise "米娅的性格应该更活泼，增加一些幽默元素"

# 查看修改影响
/impact

# 应用修改
/apply
```

### 4. 质量保证机制
- **一致性审查**：检查角色、场景、情节的一致性
- **影响分析**：分析修改对其他部分的影响
- **回归检查**：确保修改不破坏已有内容

---

## 🔧 技术特点

### 1. 模块化设计
- 每个功能都是独立的模块，易于维护和扩展
- 基于 Skill 的业务逻辑，代码与业务解耦

### 2. 安全可靠
- 完善的权限管理（Policy）
- 安全的文件操作（safe_path）
- 详细的事件日志（EventLogger）

### 3. 高性能
- 并行任务执行
- 智能缓存机制
- 增量更新策略

### 4. 可观测性
- 完整的事件日志（events.jsonl）
- 任务状态追踪（tasks.json）
- 消息流追踪（inbox/*.json）

---

## 📊 性能数据

基于真实测试数据：

- **故事输入处理**：~16 秒（3 个 aux_subagent 调用）
- **Architect 执行**：~147 秒（生成完整的故事基底）
- **Director 执行**：~120 秒（生成 12 页 48 格分镜）
- **总体流程**：~5 分钟（从灵感到分镜脚本）

---

## 🛠️ 开发指南

### 添加新技能

1. 在 `skills/` 目录下创建新文件夹
2. 创建 `SKILL.md` 文件，定义技能手册
3. 在 `lead.py` 或 `p3_team.py` 中注册技能

### 添加新 Agent

1. 在 `p3_team.py` 中定义新的 Agent 配置
2. 实现 Agent 的主循环逻辑
3. 配置 Agent 的工具和权限

### 运行测试

```bash
# 完整流程测试
python test_fixed.py

# Director 测试
python test_director.py

# 单元测试
pytest tests/
```

---

## 📝 文档

- [架构学习指南](ARCHITECTURE_STUDY_GUIDE.md)
- [任务消息事件指南](TASK_MESSAGE_EVENT_GUIDE.md)
- [Director 优化计划](DIRECTOR_OPTIMIZATION_PLAN.md)
- [Bug 分析报告](BUG_ANALYSIS.md)

---

## 🤝 贡献

欢迎提交 Issue 和 Pull Request！

### 贡献指南

1. Fork 本项目
2. 创建特性分支 (`git checkout -b feature/AmazingFeature`)
3. 提交更改 (`git commit -m 'Add some AmazingFeature'`)
4. 推送到分支 (`git push origin feature/AmazingFeature`)
5. 开启 Pull Request

---

## 📄 许可证

本项目采用 MIT 许可证 - 详见 [LICENSE](LICENSE) 文件

---

## 🙏 致谢

- 感谢所有为漫画创作付出努力的创作者
- 感谢 OpenAI 提供的强大 API
- 感谢开源社区的支持

---

## 📧 联系方式

- 项目主页：[GitHub](https://github.com/yourusername/Comic-script-agent)
- 问题反馈：[Issues](https://github.com/yourusername/Comic-script-agent/issues)

---

## 🌟 Star History

如果这个项目对你有帮助，请给我们一个 Star ⭐️

---

**让 AI 成为你的创作伙伴，而不是替代品。**
