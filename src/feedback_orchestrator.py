"""Feedback orchestration for Lead-driven revision requests."""

from __future__ import annotations

import re
from typing import Any, Dict, Optional

from feedback_loop import apply_modification_instruction, validate_modification_instruction
from p0_runtime import run_read


class FeedbackOrchestrator:
    """Convert user feedback into validated fact-source patches and Architect follow-up metadata."""

    FEEDBACK_KEYWORDS = ["修改", "调整", "改成", "不要", "删掉", "替换", "优化", "重写", "建议"]

    def __init__(self, aux_subagent, submissions: Optional[Dict[str, Dict[str, Any]]] = None):
        self.aux_subagent = aux_subagent
        self.submissions = submissions or {}

    @classmethod
    def is_feedback_text(cls, user_input: str) -> bool:
        text = (user_input or "").lower()
        return any(keyword in text for keyword in cls.FEEDBACK_KEYWORDS)

    def process_feedback(
        self,
        user_input: str,
        classification: Dict[str, Any],
        project_id: str,
        chapter_id: str,
    ) -> Optional[Dict[str, Any]]:
        if self.aux_subagent is None:
            return None
        architect_output = self._read_architect_output(chapter_id)
        choice = self.aux_subagent.run(
            "choice_designer",
            payload={
                "mode": "modification",
                "user_text": user_input,
                "feedback_text": user_input,
                "project_id": project_id,
                "chapter_id": chapter_id,
                "architect_output": architect_output,
            },
            context={"classification": classification},
        )
        if not choice.get("success"):
            return None

        instruction = (
            choice.get("data", {})
            .get("result", {})
            .get("instruction", {})
        )
        validation = validate_modification_instruction(instruction)
        if not validation.get("ok"):
            return {
                "type": "feedback_instruction_invalid",
                "error": validation.get("error"),
                "raw": choice.get("data", {}),
            }

        target_type = self.infer_instruction_target_type(instruction.get("target", ""))
        target_name = self.extract_name_from_target(instruction.get("target", ""))

        # 对于分镜和剧本修改，不直接应用到文件，而是交给对应的 Bot 处理
        if target_type in ("storyboard", "script"):
            apply_result = {"success": True, "note": f"{target_type} 修改将由对应的 Bot 处理"}
            follow_up = self.build_bot_follow_up(project_id, chapter_id, instruction, target_type)
        else:
            # 角色和场景修改直接应用
            apply_result = {"success": False, "error": "missing target_name"}
            if project_id and target_name:
                kwargs = {"project_id": project_id, "instruction": instruction}
                if target_type == "environment":
                    kwargs["environment_name"] = target_name
                else:
                    kwargs["character_name"] = target_name
                apply_result = apply_modification_instruction(**kwargs)
            follow_up = self.build_architect_follow_up(project_id, chapter_id, instruction)

        return {
            "type": "feedback_instruction_applied",
            "classification": classification,
            "instruction": instruction,
            "target_type": target_type,
            "target_name": target_name,
            "apply_result": apply_result,
            "bot_follow_up": follow_up,
        }

    def _read_architect_output(self, chapter_id: str) -> str:
        if not chapter_id:
            return ""
        architect_submission = self.submissions.get("architect", {}).get(chapter_id, {})
        deliverables = architect_submission.get("deliverables", []) if isinstance(architect_submission, dict) else []
        if not deliverables:
            return ""
        try:
            return run_read(deliverables[0])
        except Exception:
            return ""

    @staticmethod
    def build_architect_follow_up(project_id: str, chapter_id: str, instruction: Dict[str, Any]) -> Dict[str, Any]:
        if not project_id or not chapter_id:
            return {"success": False, "error": "project_id/chapter_id 不完整，无法创建 Architect 反馈任务"}
        return {
            "success": True,
            "title": f"Architect 反馈修订 {chapter_id}",
            "assignee": "architect_bot",
            "message": {
                "type": "feedback_instruction",
                "project_id": project_id,
                "chapter_id": chapter_id,
                "instruction": instruction,
            },
            "metadata": {
                "protocol_version": "v1",
                "task_type": "architect_feedback_revision",
                "project_id": project_id,
                "chapter_id": chapter_id,
                "instruction": instruction,
                "handoff_target": "director_bot",
                "submission_target": "lead",
                "qa_target": "qa_bot",
                "quality_bar": [
                    "Level 只升不降",
                    "视觉锚点不删",
                    "与既有章节设定一致",
                ],
            },
        }

    @staticmethod
    def build_bot_follow_up(project_id: str, chapter_id: str, instruction: Dict[str, Any], target_type: str) -> Dict[str, Any]:
        """为分镜或剧本修改创建对应 Bot 的任务"""
        if not project_id or not chapter_id:
            return {"success": False, "error": "project_id/chapter_id 不完整"}

        if target_type == "storyboard":
            assignee = "director_bot"
            task_type = "director_feedback_revision"
            title = f"Director 反馈修订 {chapter_id} 分镜"
        elif target_type == "script":
            assignee = "architect_bot"
            task_type = "architect_feedback_revision"
            title = f"Architect 反馈修订 {chapter_id} 剧本"
        else:
            return {"success": False, "error": f"不支持的目标类型: {target_type}"}

        return {
            "success": True,
            "title": title,
            "assignee": assignee,
            "message": {
                "type": "feedback_instruction",
                "project_id": project_id,
                "chapter_id": chapter_id,
                "instruction": instruction,
            },
            "metadata": {
                "protocol_version": "v1",
                "task_type": task_type,
                "project_id": project_id,
                "chapter_id": chapter_id,
                "instruction": instruction,
                "submission_target": "lead",
                "qa_target": "qa_bot",
                "quality_bar": [
                    "Level 只升不降",
                    "视觉锚点不删",
                    "与既有章节设定一致",
                ],
            },
        }

    @staticmethod
    def infer_instruction_target_type(target: str) -> str:
        t = (target or "").lower()
        # 分镜相关
        if any(k in t for k in ["分镜", "storyboard", "page", "格子", "panel"]):
            return "storyboard"
        # 剧本相关
        if any(k in t for k in ["剧本", "script", "对白", "dialogue"]):
            return "script"
        # 场景相关
        if ".md" in t and any(k in t for k in ["场景", "环境", "environment", "env"]):
            return "environment"
        if any(k in t for k in ["场景", "环境", "environment", "env"]):
            return "environment"
        # 默认角色
        return "character"

    @staticmethod
    def extract_name_from_target(target: str) -> str:
        text = target or ""
        md_match = re.search(r"([^\s/\\]+)\.md", text)
        if md_match:
            return md_match.group(1)
        quote_match = re.search(r"[“\"]([^”\"]+)[”\"]", text)
        if quote_match:
            return quote_match.group(1)
        token_match = re.search(r"([\u4e00-\u9fffA-Za-z0-9_\-]{2,})", text)
        if token_match:
            return token_match.group(1)
        return ""
