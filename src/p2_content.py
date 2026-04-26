# src/p2_content.py
"""
P2 层 — Content 内容管理层（V3）
项目初始化、角色/场景独立 .md 文件管理、QA 质检
"""

import json
from datetime import datetime

from p0_runtime import safe_path, run_read, run_write, WORKDIR
from p1_skills import SkillLoader


def comic_init_project(project_id: str, brief: str, num_chapters: int = 3) -> dict:
    """初始化漫画项目目录（V3：角色/场景为独立文件夹）"""
    project_path = safe_path(project_id)
    if project_path.exists():
        raise ValueError(f"⚠️ Project already exists: {project_id}")

    dirs_to_create = [
        project_path / "chapters",
        project_path / "state" / "characters",
        project_path / "state" / "environments",
        project_path / "qa",
        project_path / "feedback"
    ]
    for chapter_num in range(1, num_chapters + 1):
        dirs_to_create.append(project_path / "chapters" / f"ch{chapter_num:02d}")

    created = []
    for dir_path in dirs_to_create:
        dir_path.mkdir(parents=True, exist_ok=True)
        created.append(str(dir_path.relative_to(WORKDIR)))

    brief_file = project_path / "brief.md"
    brief_file.write_text(
        f"# 项目简介\n\n"
        f"**创建时间**：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        f"{brief}\n",
        encoding="utf-8"
    )

    return {
        "project_path": str(project_path.relative_to(WORKDIR)),
        "created_dirs": created
    }


# ============================================================
# 角色数据库（每角色一个 .md 文件）
# ============================================================

def comic_read_character(project_id: str, character_name: str) -> str:
    """读取单个角色文件"""
    char_file = safe_path(f"{project_id}/state/characters/{character_name}.md")
    if not char_file.exists():
        raise FileNotFoundError(f"角色文件不存在: {character_name}")
    return char_file.read_text(encoding="utf-8")


def comic_write_character(project_id: str, character_name: str, content: str) -> dict:
    """写入单个角色文件（整体覆盖，校验由 Skill 手册负责）"""
    char_file = safe_path(f"{project_id}/state/characters/{character_name}.md")
    char_file.write_text(content, encoding="utf-8")
    return {"success": True, "bytes_written": len(content.encode("utf-8"))}


def comic_list_characters(project_id: str) -> list:
    """列出所有角色文件名（不含后缀）"""
    char_dir = safe_path(f"{project_id}/state/characters")
    if not char_dir.exists():
        return []
    return [f.stem for f in sorted(char_dir.glob("*.md"))]


# ============================================================
# 场景数据库（每场景一个 .md 文件）
# ============================================================

def comic_read_environment(project_id: str, env_name: str) -> str:
    """读取单个场景文件"""
    env_file = safe_path(f"{project_id}/state/environments/{env_name}.md")
    if not env_file.exists():
        raise FileNotFoundError(f"场景文件不存在: {env_name}")
    return env_file.read_text(encoding="utf-8")


def comic_write_environment(project_id: str, env_name: str, content: str) -> dict:
    """写入单个场景文件（整体覆盖，校验由 Skill 手册负责）"""
    env_file = safe_path(f"{project_id}/state/environments/{env_name}.md")
    env_file.write_text(content, encoding="utf-8")
    return {"success": True, "bytes_written": len(content.encode("utf-8"))}


def comic_list_environments(project_id: str) -> list:
    """列出所有场景文件名（不含后缀）"""
    env_dir = safe_path(f"{project_id}/state/environments")
    if not env_dir.exists():
        return []
    return [f.stem for f in sorted(env_dir.glob("*.md"))]


# ============================================================
# 质检系统
# ============================================================

def comic_qa_check_chapter(project_id: str, chapter_id: str, openai_client, model: str = None) -> dict:
    """对章节进行质检（V3：读取独立 .md 文件）"""
    if model is None:
        from config import load_config
        model = load_config()["model"]

    script = run_read(f"{project_id}/chapters/{chapter_id}/script.md")
    storyboard_text = ""
    try:
        storyboard_text = run_read(f"{project_id}/chapters/{chapter_id}/storyboard.md")
    except FileNotFoundError:
        storyboard_text = "[分镜文件不存在，跳过分镜检查]"

    # 读取所有角色 .md 文件
    characters_content = ""
    for name in comic_list_characters(project_id):
        characters_content += comic_read_character(project_id, name) + "\n\n---\n\n"

    # 读取所有场景 .md 文件
    environments_content = ""
    for name in comic_list_environments(project_id):
        environments_content += comic_read_environment(project_id, name) + "\n\n---\n\n"

    loader = SkillLoader()
    qa_skill = loader.load("story-qa")

    prompt = f"""{qa_skill}

## 需要检查的内容

### 角色数据库
{characters_content if characters_content else "[无角色文件]"}

### 场景数据库
{environments_content if environments_content else "[无场景文件]"}

### 剧本
{script}

### 分镜脚本
{storyboard_text}

## 任务
请按照上述技能手册的要求，逐项检查这一章的质量。
只输出 JSON 结果，不要其他文字。
"""

    response = openai_client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}]
    )

    result_text = response.choices[0].message.content

    # 提取 JSON（AI 可能用 markdown 代码块包裹）
    if "```json" in result_text:
        result_text = result_text.split("```json")[1].split("```")[0]
    elif "```" in result_text:
        result_text = result_text.split("```")[1].split("```")[0]
    result = json.loads(result_text.strip())

    # 生成报告
    report = f"# QA 报告 — {chapter_id}\n\n"
    report += f"**检查时间**：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
    report += f"**状态**：{result['status']}\n\n"

    if result["status"] == "PASS":
        report += "✅ 本章质量合格，未发现问题。\n"
    else:
        report += f"❌ 发现 {len(result['issues'])} 个问题：\n\n"
        severity_emoji = {"critical": "🔴", "major": "🟡", "minor": "🟢"}
        for i, issue in enumerate(result["issues"], 1):
            emoji = severity_emoji.get(issue["severity"], "⚪")
            report += f"### {i}. {emoji} {issue['type']}\n"
            report += f"**严重程度**：{issue['severity']}\n"
            report += f"**位置**：{issue['location']}\n"
            report += f"**描述**：{issue['description']}\n\n"

    report_path = f"{project_id}/qa/{chapter_id}_report.md"
    run_write(report_path, report)

    return {
        "status": result["status"],
        "issues": result.get("issues", []),
        "report_path": report_path
    }
