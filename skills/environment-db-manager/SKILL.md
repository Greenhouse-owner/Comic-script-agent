---
name: environment-db-manager
description: 管理场景活数据库文件夹（state/environments/），每个场景一个独立 .md 文件，支持创建、查询、渐进式升级、锁定
tags: [environment, database, progressive-disclosure]
---

## 输入

- **操作类型**: `create` | `upgrade` | `query` | `query_all` | `lock`
- **场景名字**: 要操作的场景（对应文件名，如 `老城区街头` → `state/environments/老城区街头.md`）
- **目标 Level**: 要升级到的层次 (1-2)
- **新增内容**: 要追加的场景信息（仅 upgrade 时需要）

## 输出格式

对 `state/environments/{场景名}.md` 文件的**创建、追加或编辑操作**。

### Level 1 文件模板

```markdown
# {场景名} | {定位} | Level 1 🔵

> 首次出现: {chapter_id} | 上次更新: {chapter_id}

## Level 1 — 轮廓 ({chapter_id} 创建)
- **地理位置**: {具体位置}
- **一句话氛围**: {氛围描述}
- **时间段**: {时间}
- **关键道具**:
  1. {道具1}（说明）
  2. {道具2}（说明）
  3. {道具3}（说明）
- **详细描述**: {场景的详细文字描述}

## Level 2 — 完稿 (尚未解锁)
- 🔒 等待后续章节解锁
```

### Level 2 升级后模板

```markdown
# {场景名} | {定位} | Level 2 🟢 🔒

> 首次出现: {chapter_id} | 上次更新: {upgrade_chapter_id}

## Level 1 — 轮廓 ({chapter_id} 创建)
- （保持不变）

## Level 2 — 完稿 ({upgrade_chapter_id} 补充) 🔒
- **场景背后的故事**: {这个地方的历史或隐藏故事}
- **情感象征意义**: {在故事中象征什么}
- **隐藏细节**: {不易察觉但重要的细节}
- **在故事中的演变**: {第N章:事件A → 第M章:事件B → ...}
- **forbidden_changes**: [{不可更改项1}, {不可更改项2}, ...]
```

### query_all 操作

遍历 `state/environments/` 文件夹下所有 `.md` 文件，返回每个场景的摘要：
```
- {场景名} | {定位} | Level {N} | 一句话氛围
```

## 硬约束

1. **Level 只能升不能降**
   - Level 2 的场景不能退回 Level 1
   - 已确定的信息不能被修改（只能追加新信息）

2. **只有两级，从 Level 1 直接到 Level 2**
   - Level 1 创建时包含：地理位置 + 一句话氛围 + 时间段 + 关键道具(≥3个) + 详细描述
   - Level 2 升级时追加：场景背后的故事 + 情感象征意义 + 隐藏细节 + 在故事中的演变 + forbidden_changes

3. **Level 2 = 锁定**
   - 达到 Level 2 后，场景信息不可再更改
   - 必须填写 `forbidden_changes` 列表
   - Level 2 必须包含情感象征意义

4. **场景 Level 1 必须包含至少 3 个关键道具**
   - 道具必须是具体可画的物件

5. **每个场景一个独立文件**
   - 文件路径: `state/environments/{场景名}.md`
   - 文件名就是场景名（不含后缀）
   - 标题行格式: `# 场景名 | 定位 | Level X`

## 失败处理

- **场景文件不存在**: 返回错误，要求先 create
- **试图降级**: 返回错误，说明 Level 只能升不能降
- **试图修改已锁定内容**: 返回错误，说明 Level 2 场景已锁定
- **信息与现有数据冲突**: 标记 `[⚠️ 冲突]`，返回冲突详情，要求人工或 Lead 仲裁
