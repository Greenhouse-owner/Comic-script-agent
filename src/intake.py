"""Intake persistence service for Lead-normalized user facts."""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any, Dict, List

from p0_runtime import run_write, safe_path


class IntakePersistenceService:
    """Persist structured intake JSON into project fact sources."""

    def persist_normalized_intake(
        self,
        user_input: str,
        classification: Dict[str, Any],
        normalized: Dict[str, Any],
        project_id: str,
        chapter_id: str,
    ) -> Dict[str, Any]:
        target_project_id = (normalized.get("project_id") or project_id or "").strip()
        target_chapter_id = (normalized.get("chapter_id") or chapter_id or "ch01").strip()
        if not target_project_id:
            return {"success": False, "error": "project_id is required"}

        self.ensure_project_structure(target_project_id, user_input)
        facts = normalized.get("facts", {}) if isinstance(normalized.get("facts", {}), dict) else {}
        normalized_files = self._write_fact_files(target_project_id, target_chapter_id, facts, classification)

        raw_path = f"{target_project_id}/raw_user_input.md"
        report_path = f"{target_project_id}/intake_report.md"
        plan_path = f"{target_project_id}/execution_plan.md"
        run_write(raw_path, self._render_raw_user_input(user_input, classification, normalized))
        run_write(report_path, self._render_report(target_project_id, target_chapter_id, classification, normalized, normalized_files))
        run_write(plan_path, self._render_plan(target_project_id, target_chapter_id, normalized, normalized_files))

        return {
            "success": True,
            "type": "intake_normalized",
            "project_id": target_project_id,
            "chapter_id": target_chapter_id,
            "input_type": classification.get("input_type", "mixed_notes"),
            "classification": classification,
            "core_files": [raw_path, report_path, plan_path],
            "normalized_files": normalized_files,
            "missing": normalized.get("missing", []) if isinstance(normalized.get("missing", []), list) else [],
            "recommended_next": normalized.get("recommended_next", {}) if isinstance(normalized.get("recommended_next", {}), dict) else {},
            "next_step": "已由 aux_subagent 结构化用户输入并写入事实源；后续可创建 Architect 交付任务。",
        }

    def persist_story_direction(
        self,
        pending: Dict[str, Any],
        summarized: Dict[str, Any],
        project_id: str,
    ) -> Dict[str, Any]:
        target_project_id = (summarized.get("project_id") or project_id or "").strip()
        if not target_project_id:
            return {"success": False, "error": "project_id is required"}
        original_input = pending.get("original_input", "")
        classification = pending.get("classification", {}) if isinstance(pending.get("classification", {}), dict) else {}
        direction = summarized.get("story_direction", {}) if isinstance(summarized.get("story_direction", {}), dict) else {}
        self.ensure_project_structure(target_project_id, original_input)
        path = f"{target_project_id}/story_direction.md"
        run_write(path, self._render_story_direction(target_project_id, pending, direction, summarized))
        return {
            "success": True,
            "project_id": target_project_id,
            "file": path,
            "classification": classification,
            "recommended_next": summarized.get("recommended_next", {}) if isinstance(summarized.get("recommended_next", {}), dict) else {},
        }

    @staticmethod
    def ensure_project_structure(project_id: str, seed: str = "") -> None:
        project_path = safe_path(project_id)
        for rel in ["chapters/ch01", "state/characters", "state/environments", "qa", "feedback", "plans"]:
            (project_path / rel).mkdir(parents=True, exist_ok=True)
        brief_path = project_path / "brief.md"
        if seed and not brief_path.exists():
            brief_path.write_text(
                f"# 项目简介\n\n**创建时间**：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n{seed}\n",
                encoding="utf-8",
            )

    def _write_fact_files(self, project_id: str, chapter_id: str, facts: Dict[str, Any], classification: Dict[str, Any]) -> List[str]:
        files: List[str] = []
        if self._text(facts.get("story_direction")):
            path = f"{project_id}/story_direction.md"
            run_write(path, self._doc("创作方向", self._text(facts.get("story_direction")), classification))
            files.append(path)
        if self._text(facts.get("worldbuilding")):
            path = f"{project_id}/story_bible.md"
            run_write(path, self._doc(f"Story Bible: {project_id}", self._text(facts.get("worldbuilding")), classification))
            files.append(path)

        outlines = facts.get("chapter_outlines", [])
        if isinstance(outlines, list) and outlines:
            path = f"{project_id}/chapter_outlines.md"
            outline_text = "\n\n".join(self._outline_text(item) for item in outlines if isinstance(item, dict))
            run_write(path, self._doc(f"全章节细纲: {project_id}", outline_text, classification))
            files.append(path)
            for item in outlines:
                if isinstance(item, dict):
                    cid = self._chapter_id(item.get("chapter_id") or chapter_id)
                    detail_path = f"{project_id}/chapters/{cid}/outline.md"
                    run_write(detail_path, self._doc(f"{cid} 用户细纲", self._outline_text(item), classification))
                    files.append(detail_path)

        for character in facts.get("characters", []) if isinstance(facts.get("characters", []), list) else []:
            if isinstance(character, dict) and self._entity_name(character.get("name")):
                name = self._entity_name(character.get("name"))
                path = f"{project_id}/state/characters/{name}.md"
                run_write(path, self._entity_doc(name, character, classification, kind="character"))
                files.append(path)

        for environment in facts.get("environments", []) if isinstance(facts.get("environments", []), list) else []:
            if isinstance(environment, dict) and self._entity_name(environment.get("name")):
                name = self._entity_name(environment.get("name"))
                path = f"{project_id}/state/environments/{name}.md"
                run_write(path, self._entity_doc(name, environment, classification, kind="environment"))
                files.append(path)

        if self._text(facts.get("script_draft")):
            path = f"{project_id}/chapters/{chapter_id}/draft_input.md"
            run_write(path, self._doc(f"{chapter_id} 用户剧本草稿", self._text(facts.get("script_draft")), classification))
            files.append(path)
        return files

    @staticmethod
    def _render_story_direction(project_id: str, pending: Dict[str, Any], direction: Dict[str, Any], summarized: Dict[str, Any]) -> str:
        constraints = direction.get("user_constraints", []) if isinstance(direction.get("user_constraints", []), list) else []
        lines = [
            "# 创作方向",
            "",
            f"**项目 ID**：{project_id}",
            f"**生成时间**：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "",
            "## 故事种子",
            pending.get("original_input", ""),
            "",
            "## 用户回答归纳",
            direction.get("summary", ""),
            "",
            "## 核心设定",
            f"- 前提：{direction.get('premise', '')}",
            f"- 类型：{direction.get('genre', '')}",
            f"- 基调：{direction.get('tone', '')}",
            f"- 章节数：{direction.get('target_chapter_count', '')}",
            f"- 主角：{direction.get('main_character', '')}",
            f"- 核心冲突：{direction.get('central_conflict', '')}",
            f"- 结局方向：{direction.get('ending_direction', '')}",
            f"- 视觉风格：{direction.get('visual_style', '')}",
            "",
            "## 用户约束",
        ]
        lines.extend([f"- {item}" for item in constraints] or ["- 暂无明确约束"])
        lines.extend([
            "",
            "## Aux 结构化结果",
            "```json",
            json.dumps(summarized, ensure_ascii=False, indent=2),
            "```",
            "",
        ])
        return "\n".join(lines)

    @staticmethod
    def _render_raw_user_input(user_input: str, classification: Dict[str, Any], normalized: Dict[str, Any]) -> str:
        return "\n".join([
            "# 原始用户输入", "", f"**保存时间**：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", "",
            "## 分类结果", "```json", json.dumps(classification, ensure_ascii=False, indent=2), "```", "",
            "## Aux 归一化结果", "```json", json.dumps(normalized, ensure_ascii=False, indent=2), "```", "",
            "## 原文", user_input, "",
        ])

    @staticmethod
    def _render_report(project_id: str, chapter_id: str, classification: Dict[str, Any], normalized: Dict[str, Any], files: List[str]) -> str:
        missing = normalized.get("missing", []) if isinstance(normalized.get("missing", []), list) else []
        return "\n".join([
            "# Intake Report", "", f"**项目 ID**：{project_id}", f"**章节 ID**：{chapter_id}", "",
            "## 分类结果", "```json", json.dumps(classification, ensure_ascii=False, indent=2), "```", "",
            "## 已写入文件", *(f"- `{path}`" for path in files), "", "## 缺失项", *(f"- {item}" for item in missing), "",
        ])

    @staticmethod
    def _render_plan(project_id: str, chapter_id: str, normalized: Dict[str, Any], files: List[str]) -> str:
        recommended = normalized.get("recommended_next", {}) if isinstance(normalized.get("recommended_next", {}), dict) else {}
        return "\n".join([
            "# Execution Plan", "", f"**项目 ID**：{project_id}", f"**章节 ID**：{chapter_id}", "",
            "## 推荐下一步", "```json", json.dumps(recommended, ensure_ascii=False, indent=2), "```", "",
            "## 已有事实源", *(f"- `{path}`" for path in files), "",
        ])

    @staticmethod
    def _doc(title: str, body: str, classification: Dict[str, Any]) -> str:
        return "\n".join(["# " + title, "", body, "", "## 分类结果", "```json", json.dumps(classification, ensure_ascii=False, indent=2), "```", ""])

    @staticmethod
    def _outline_text(item: Dict[str, Any]) -> str:
        return f"### {item.get('chapter_id', '')} {item.get('title', '')}\n\n{item.get('summary', '')}".strip()

    @staticmethod
    def _entity_doc(name: str, data: Dict[str, Any], classification: Dict[str, Any], kind: str) -> str:
        anchors = data.get("visual_anchors", []) if isinstance(data.get("visual_anchors", []), list) else []
        title = f"# {name} | Level 1"
        role = f"- 角色定位：{data.get('role', '')}\n" if kind == "character" else ""
        anchor_text = "\n".join(f"  - {anchor}" for anchor in anchors) or "  - 待补齐"
        return "\n".join([title, "", role + f"- 描述：{data.get('description', '')}\n- 视觉锚点：\n{anchor_text}", "", "## 来源", "aux_subagent intake_normalizer", "", "## 分类结果", "```json", json.dumps(classification, ensure_ascii=False, indent=2), "```", ""])

    @staticmethod
    def _text(value: Any) -> str:
        return value.strip() if isinstance(value, str) else ""

    @staticmethod
    def _entity_name(value: Any) -> str:
        return re.sub(r"[/\\:\n\r\t]+", "_", str(value or "").strip())[:80]

    @staticmethod
    def _chapter_id(value: Any) -> str:
        raw = str(value or "ch01").strip().lower()
        match = re.search(r"(\d+)", raw)
        return f"ch{int(match.group(1)):02d}" if match else "ch01"
