"""Plan-before-execute manager for Lab 13 teammate tasks."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from p0_runtime import run_write
from policy import ToolPolicy


@dataclass
class PlanStep:
    id: str
    description: str
    tool: str
    inputs: Dict[str, Any]
    expected_output: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "description": self.description,
            "tool": self.tool,
            "inputs": self.inputs,
            "expected_output": self.expected_output,
        }


@dataclass
class ExecutionPlan:
    task_id: str
    agent: str
    project_id: str
    chapter_id: str
    task_type: str
    steps: List[PlanStep]
    risk_checklist: List[str]
    status: str = "draft"
    requires_user_approval: bool = False
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "agent": self.agent,
            "project_id": self.project_id,
            "chapter_id": self.chapter_id,
            "task_type": self.task_type,
            "status": self.status,
            "steps": [step.to_dict() for step in self.steps],
            "risk_checklist": self.risk_checklist,
            "requires_user_approval": self.requires_user_approval,
            "created_at": self.created_at,
        }


class PlanManager:
    """Build fixed execution plans and validate them with ToolPolicy."""

    def __init__(self, policy: Optional[ToolPolicy] = None):
        self.policy = policy or ToolPolicy()

    def require_plan(self, agent_name: str, task: Dict[str, Any]) -> Dict[str, Any]:
        plan = self._build_plan(agent_name, task)
        self.validate_plan(plan)
        plan.status = "approved"
        plan_path = self._plan_path(plan.project_id, plan.task_id)
        plan_data = plan.to_dict()
        run_write(plan_path, json.dumps(plan_data, ensure_ascii=False, indent=2))
        return {"success": True, "plan_path": plan_path, "plan": plan_data}

    def validate_plan(self, plan: ExecutionPlan):
        context = {
            "project_id": plan.project_id,
            "chapter_id": plan.chapter_id,
        }
        for step in plan.steps:
            self.policy.authorize_tool(plan.agent, step.tool)
            file_path = step.inputs.get("file_path")
            if not file_path:
                continue
            mode = "read" if step.tool == "read_file" else "write"
            self.policy.authorize_path(plan.agent, file_path, mode, context)

    def _build_plan(self, agent_name: str, task: Dict[str, Any]) -> ExecutionPlan:
        metadata = task.get("metadata", {}) if isinstance(task.get("metadata"), dict) else {}
        task_id = task.get("id", "unknown_task")
        task_type = metadata.get("task_type", "")
        project_id = metadata.get("project_id", "")
        chapter_id = metadata.get("chapter_id", "")

        if task_type == "architect_delivery":
            return ExecutionPlan(
                task_id=task_id,
                agent=agent_name,
                project_id=project_id,
                chapter_id=chapter_id,
                task_type=task_type,
                steps=[
                    PlanStep(
                        id="step_1",
                        description="读取项目 brief",
                        tool="read_file",
                        inputs={"file_path": f"{project_id}/brief.md"},
                        expected_output="项目简介文本",
                    ),
                    PlanStep(
                        id="step_2",
                        description="写入章节剧本",
                        tool="write_file",
                        inputs={"file_path": f"{project_id}/chapters/{chapter_id}/script.md"},
                        expected_output="script.md",
                    ),
                ],
                risk_checklist=["剧情完整", "角色动机清晰", "角色与场景设定一致"],
            )

        if task_type == "architect_feedback_revision":
            return ExecutionPlan(
                task_id=task_id,
                agent=agent_name,
                project_id=project_id,
                chapter_id=chapter_id,
                task_type=task_type,
                steps=[
                    PlanStep(
                        id="step_1",
                        description="读取旧剧本",
                        tool="read_file",
                        inputs={"file_path": f"{project_id}/chapters/{chapter_id}/script.md"},
                        expected_output="旧 script.md",
                    ),
                    PlanStep(
                        id="step_2",
                        description="追加反馈修订记录",
                        tool="write_file",
                        inputs={"file_path": f"{project_id}/chapters/{chapter_id}/script.md"},
                        expected_output="修订后的 script.md",
                    ),
                ],
                risk_checklist=["Level 只升不降", "视觉锚点不删", "保留原有事实源"],
            )

        if task_type == "director_delivery":
            return ExecutionPlan(
                task_id=task_id,
                agent=agent_name,
                project_id=project_id,
                chapter_id=chapter_id,
                task_type=task_type,
                steps=[
                    PlanStep(
                        id="step_1",
                        description="读取剧本",
                        tool="read_file",
                        inputs={"file_path": f"{project_id}/chapters/{chapter_id}/script.md"},
                        expected_output="script.md",
                    ),
                    PlanStep(
                        id="step_2",
                        description="写入分镜",
                        tool="write_file",
                        inputs={"file_path": f"{project_id}/chapters/{chapter_id}/storyboard.md"},
                        expected_output="storyboard.md",
                    ),
                ],
                risk_checklist=["严格对应 script.md", "视觉锚点一致", "场景道具与氛围一致"],
            )

        if task_type == "qa_review":
            return ExecutionPlan(
                task_id=task_id,
                agent=agent_name,
                project_id=project_id,
                chapter_id=chapter_id,
                task_type=task_type,
                steps=[
                    PlanStep(
                        id="step_1",
                        description="读取剧本",
                        tool="read_file",
                        inputs={"file_path": f"{project_id}/chapters/{chapter_id}/script.md"},
                        expected_output="script.md",
                    ),
                    PlanStep(
                        id="step_2",
                        description="读取分镜",
                        tool="read_file",
                        inputs={"file_path": f"{project_id}/chapters/{chapter_id}/storyboard.md"},
                        expected_output="storyboard.md",
                    ),
                    PlanStep(
                        id="step_3",
                        description="写入 QA 报告",
                        tool="write_file",
                        inputs={"file_path": f"{project_id}/qa/{chapter_id}_report.md"},
                        expected_output="QA report",
                    ),
                ],
                risk_checklist=["检查角色一致性", "检查分镜一致性", "检查跨文件一致性"],
            )

        return ExecutionPlan(
            task_id=task_id,
            agent=agent_name,
            project_id=project_id,
            chapter_id=chapter_id,
            task_type=task_type,
            steps=[],
            risk_checklist=["未知任务类型，需要人工或 Lead 进一步确认"],
            requires_user_approval=True,
        )

    @staticmethod
    def _plan_path(project_id: str, task_id: str) -> str:
        if project_id:
            return f"{project_id}/plans/{task_id}.json"
        return f"plans/{task_id}.json"
