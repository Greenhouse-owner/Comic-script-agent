---
name: character-db-manager
description: 管理角色活数据库文件夹（state/characters/），每个角色一个独立 .md 文件，支持创建、查询、渐进式升级、锁定
tags: [character, database, progressive-disclosure]
---

## 输入

- **操作类型**: `create` | `upgrade` | `query` | `query_all` | `lock`
- **角色名字**: 要操作的角色（对应文件名，如 `林夜` → `state/characters/林夜.md`）
- **目标 Level**: 要升级到的层次 (1-2)
- **新增内容**: 要追加的角色信息（仅 upgrade 时需要）

## 输出格式

对 `state/characters/{角色名}.md` 文件的**创建、追加或编辑操作**。

### Level 1 文件模板

```markdown
# {角色名} | {定位} | Level 1 🔵

> 首次登场: {chapter_id} | 上次更新: {chapter_id}

## Level 1 — 轮廓 ({chapter_id} 创建)
- **视觉锚点**:
  1. {锚点1}（说明）
  2. {锚点2}（说明）
  3. {锚点3}（说明）
- **一句话定位**: {一句话描述角色}
- **性格特质**: {性格描述}
- **关键关系**:
  - {角色A}: {关系描述}
  - {角色B}: {关系描述}

## Level 2 — 完稿 (尚未解锁)
- 🔒 等待后续章节解锁
```

### Level 2 升级后模板

```markdown
# {角色名} | {定位} | Level 2 🟢 🔒

> 首次登场: {chapter_id} | 上次更新: {upgrade_chapter_id}

## Level 1 — 轮廓 ({chapter_id} 创建)
- （保持不变）

## Level 2 — 完稿 ({upgrade_chapter_id} 补充) 🔒
- **动机**: {角色的核心动机}
- **背景故事**: {关键背景}
- **致命缺陷**: {性格弱点}
- **内心独白风格**: {独白特征}
- **角色弧线**: {从A → 经过B → 到达C}
- **forbidden_changes**: [{不可更改项1}, {不可更改项2}, ...]
```

### query_all 操作

遍历 `state/characters/` 文件夹下所有 `.md` 文件，返回每个角色的摘要：
```
- {角色名} | {定位} | Level {N} | 一句话定位
```

## 硬约束

1. **Level 只能升不能降**
   - Level 2 的角色不能退回 Level 1
   - 已确定的信息不能被修改（只能追加新信息）

2. **只有两级，从 Level 1 直接到 Level 2**
   - Level 1 创建时包含：视觉锚点(≥3个) + 一句话定位 + 性格特质 + 关键关系
   - Level 2 升级时追加：动机 + 背景故事 + 致命缺陷 + 内心独白风格 + 角色弧线 + forbidden_changes

3. **Level 2 = 锁定**
   - 达到 Level 2 后，角色信息不可再更改
   - 必须填写 `forbidden_changes` 列表
   - Level 2 必须包含完整的角色弧线描述

4. **每个角色一个独立文件**
   - 文件路径: `state/characters/{角色名}.md`
   - 文件名就是角色名（不含后缀）
   - 标题行格式: `# 角色名 | 定位 | Level X`

5. **创建时必须达到 Level 1 的完整要求**
   - 新角色必须提供：名字、≥3个视觉锚点、一句话定位、性格特质、关键关系

## 失败处理

- **角色文件不存在**: 返回错误，要求先 create
- **试图降级**: 返回错误，说明 Level 只能升不能降
- **试图修改已锁定内容**: 返回错误，说明 Level 2 角色已锁定
- **信息与现有数据冲突**: 标记 `[⚠️ 冲突]`，返回冲突详情，要求人工或 Lead 仲裁
