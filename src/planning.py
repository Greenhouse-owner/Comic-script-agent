"""Plan-before-execute manager for Lab 13 teammate tasks."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from p0_runtime import run_write, safe_path
from policy import ToolPolicy


ARCHITECT_AI_PLANNING_MODE = "ai_architect_dynamic_v1"
ARCHITECT_RULE_PLANNING_MODE = "architect_dynamic_v1"
SUPPORTED_DYNAMIC_PLANNING_MODES = {ARCHITECT_RULE_PLANNING_MODE, ARCHITECT_AI_PLANNING_MODE}


ARCHITECT_ARTIFACT_CONTRACTS: Dict[str, Dict[str, Any]] = {
    "brief": {"path_template": "{project_id}/brief.md", "required_inputs": [], "producer_skills": [], "validators": ["exists", "non_empty"]},
    "story_direction": {"path_template": "{project_id}/story_direction.md", "required_inputs": ["user_input"], "producer_skills": [], "validators": ["exists", "non_empty"]},
    "story_bible": {"path_template": "{project_id}/story_bible.md", "required_inputs": ["brief", "story_direction"], "producer_skills": ["story-planner"], "validators": ["exists", "non_empty", "inherits_user_facts"]},
    "chapter_outlines": {"path_template": "{project_id}/chapter_outlines.md", "required_inputs": ["story_bible", "story_direction"], "producer_skills": ["story-planner"], "validators": ["exists", "non_empty", "chapter_range_complete"]},
    "chapter_outline": {"path_template": "{project_id}/chapters/{chapter_id}/outline.md", "required_inputs": ["chapter_outlines"], "producer_skills": ["story-planner"], "validators": ["exists", "non_empty", "matches_chapter_id"]},
    "character_cards": {"path_template": "{project_id}/state/characters/*.md", "required_inputs": ["story_bible", "chapter_outlines"], "producer_skills": ["character-builder"], "validators": ["exists", "non_empty", "visual_anchors_present"]},
    "environment_cards": {"path_template": "{project_id}/state/environments/*.md", "required_inputs": ["story_bible", "chapter_outlines"], "producer_skills": ["environment-builder"], "validators": ["exists", "non_empty", "visual_anchors_present"]},
    "chapter_script": {"path_template": "{project_id}/chapters/{chapter_id}/script.md", "required_inputs": ["story_bible", "chapter_outline", "character_cards", "environment_cards"], "producer_skills": ["chapter-expander"], "validators": ["exists", "non_empty", "inherits_chapter_outline", "registered_roles_only", "registered_environments_only"]},
}


@dataclass
class PlanStep:
    id: str
    description: str
    tool: str
    inputs: Dict[str, Any]
    expected_output: str
    skill: Optional[str] = None
    input_artifacts: List[Dict[str, Any]] = field(default_factory=list)
    output_artifacts: List[Dict[str, Any]] = field(default_factory=list)
    depends_on: List[str] = field(default_factory=list)
    validation_rules: List[str] = field(default_factory=list)
    purpose: str = ""

    def to_dict(self) -> Dict[str, Any]:
        data = {
            "id": self.id,
            "description": self.description,
            "tool": self.tool,
            "inputs": self.inputs,
            "expected_output": self.expected_output,
        }
        if self.skill:
            data["skill"] = self.skill
        if self.input_artifacts:
            data["input_artifacts"] = self.input_artifacts
        if self.output_artifacts:
            data["output_artifacts"] = self.output_artifacts
        if self.depends_on:
            data["depends_on"] = self.depends_on
        if self.validation_rules:
            data["validation_rules"] = self.validation_rules
        if self.purpose:
            data["purpose"] = self.purpose
        return data


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
    goal: Dict[str, Any] = field(default_factory=dict)
    project_state: Dict[str, Any] = field(default_factory=dict)
    target_artifacts: List[str] = field(default_factory=list)
    deliverables: List[Dict[str, Any]] = field(default_factory=list)
    planning_mode: str = "fixed"

    def to_dict(self) -> Dict[str, Any]:
        data = {
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
            "planning_mode": self.planning_mode,
        }
        if self.goal:
            data["goal"] = self.goal
        if self.project_state:
            data["project_state"] = self.project_state
        if self.target_artifacts:
            data["target_artifacts"] = self.target_artifacts
        if self.deliverables:
            data["deliverables"] = self.deliverables
        return data


class ProjectStateScanner:
    """Read-only scanner for Architect artifacts inside one comic project."""

    def scan(self, project_id: str, chapter_ids: Optional[List[str]] = None) -> Dict[str, Any]:
        chapter_ids = chapter_ids or []
        project_root = safe_path(project_id)
        characters = self._list_markdown_files(safe_path(f"{project_id}/state/characters"), project_root)
        environments = self._list_markdown_files(safe_path(f"{project_id}/state/environments"), project_root)
        chapters: Dict[str, Any] = {}
        for chapter_id in chapter_ids:
            chapters[chapter_id] = {
                "outline": self._artifact_status(f"{project_id}/chapters/{chapter_id}/outline.md"),
                "script": self._artifact_status(f"{project_id}/chapters/{chapter_id}/script.md"),
            }
        state = {
            "project_id": project_id,
            "brief": self._artifact_status(f"{project_id}/brief.md"),
            "story_direction": self._artifact_status(f"{project_id}/story_direction.md"),
            "story_bible": self._artifact_status(f"{project_id}/story_bible.md"),
            "chapter_outlines": self._artifact_status(f"{project_id}/chapter_outlines.md"),
            "chapters": chapters,
            "characters": {"count": len(characters), "paths": characters, "exists": bool(characters)},
            "environments": {"count": len(environments), "paths": environments, "exists": bool(environments)},
        }
        state["missing_by_chapter"] = self._missing_by_chapter(state, chapter_ids)
        return state

    @staticmethod
    def _artifact_status(file_path: str) -> Dict[str, Any]:
        path = safe_path(file_path)
        exists = path.exists() and path.is_file()
        size = path.stat().st_size if exists else 0
        return {"path": file_path, "exists": exists, "non_empty": bool(size), "bytes": size}

    @staticmethod
    def _list_markdown_files(directory: Path, project_root: Path) -> List[str]:
        if not directory.exists() or not directory.is_dir():
            return []
        return [str(path.relative_to(project_root.parent)) for path in sorted(directory.glob("*.md")) if path.is_file()]

    @staticmethod
    def _missing_by_chapter(state: Dict[str, Any], chapter_ids: List[str]) -> Dict[str, List[str]]:
        missing: Dict[str, List[str]] = {}
        for chapter_id in chapter_ids:
            chapter_missing: List[str] = []
            if not state["story_bible"]["non_empty"]:
                chapter_missing.append("story_bible")
            if not state["chapter_outlines"]["non_empty"]:
                chapter_missing.append("chapter_outlines")
            if not state["chapters"].get(chapter_id, {}).get("outline", {}).get("non_empty"):
                chapter_missing.append("chapter_outline")
            if not state["characters"]["exists"]:
                chapter_missing.append("character_cards")
            if not state["environments"]["exists"]:
                chapter_missing.append("environment_cards")
            if not state["chapters"].get(chapter_id, {}).get("script", {}).get("non_empty"):
                chapter_missing.append("chapter_script")
            missing[chapter_id] = chapter_missing
        return missing


class ArchitectAIPlanner:
    """Constrained AI planner: model proposes JSON, code validates before execution."""

    def __init__(self, client: Any = None, model: Optional[str] = None):
        self.client = client
        self.model = model

    def build_plan(self, task: Dict[str, Any], goal: Dict[str, Any], state: Dict[str, Any], contracts: Dict[str, Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if self.client is None:
            return None
        prompt = self.build_prompt(task, goal, state, contracts)
        response = self.client.chat.completions.create(
            model=self.model or "gpt-5.5",
            messages=[
                {"role": "system", "content": "You are a constrained planning engine. Return only valid JSON."},
                {"role": "user", "content": prompt},
            ],
        )
        content = response.choices[0].message.content or ""
        return self._parse_json_object(content)

    @staticmethod
    def build_prompt(task: Dict[str, Any], goal: Dict[str, Any], state: Dict[str, Any], contracts: Dict[str, Dict[str, Any]]) -> str:
        allowed_artifacts = list(contracts.keys())
        allowed_skills = sorted({skill for contract in contracts.values() for skill in contract.get("producer_skills", [])})
        schema = {
            "planning_mode": ARCHITECT_AI_PLANNING_MODE,
            "agent": "architect_bot",
            "project_id": goal.get("project_id", ""),
            "chapter_id": (goal.get("chapter_ids") or [""])[0] if isinstance(goal.get("chapter_ids"), list) else goal.get("chapter_ids", ""),
            "task_type": "architect_delivery",
            "goal": goal,
            "steps": [
                {
                    "id": "step_1",
                    "description": "short action description",
                    "tool": "write_file",
                    "inputs": {"file_path": "project/path.md", "skill": "story-planner"},
                    "expected_output": "story_bible",
                    "skill": "story-planner",
                    "input_artifacts": [{"artifact_type": "brief"}],
                    "output_artifacts": [{"artifact_type": "story_bible", "path": "project/story_bible.md", "producer_skill": "story-planner"}],
                    "depends_on": [],
                    "validation_rules": ["exists", "non_empty"],
                }
            ],
            "deliverables": [{"artifact_type": "chapter_script", "path": "project/chapters/ch01/script.md"}],
            "risk_checklist": [],
        }
        return "\n".join([
            "Create a constrained Architect execution plan as JSON only. Do not use markdown.",
            "Hard constraints:",
            f"- planning_mode must be {ARCHITECT_AI_PLANNING_MODE}.",
            "- agent must be architect_bot.",
            "- task_type must remain architect_delivery; never plan feedback revision tasks.",
            "- Every step.tool must be write_file.",
            f"- Allowed artifact_type values: {json.dumps(allowed_artifacts, ensure_ascii=False)}",
            f"- Allowed skills: {json.dumps(allowed_skills, ensure_ascii=False)}",
            "- Output paths must exactly match the contract path_template after project_id/chapter_id substitution.",
            "- Skip artifacts that already exist and are non_empty unless goal explicitly requires regeneration.",
            "- chapter_script must depend on story_bible, chapter_outline, character_cards, and environment_cards when those are missing or produced in this plan.",
            "- Return one JSON object matching this shape:",
            json.dumps(schema, ensure_ascii=False, indent=2),
            "Goal:",
            json.dumps(goal, ensure_ascii=False, indent=2),
            "Project state:",
            json.dumps(state, ensure_ascii=False, indent=2),
            "Artifact contracts:",
            json.dumps(contracts, ensure_ascii=False, indent=2),
            "Task metadata:",
            json.dumps(task.get("metadata", {}), ensure_ascii=False, indent=2),
        ])

    @staticmethod
    def _parse_json_object(content: str) -> Optional[Dict[str, Any]]:
        text = content.strip()
        if text.startswith("```"):
            text = text.strip("`")
            if text.startswith("json"):
                text = text[4:].strip()
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None


class PlanManager:
    """Build fixed execution plans and validate them with ToolPolicy."""

    def __init__(self, policy: Optional[ToolPolicy] = None, scanner: Optional[ProjectStateScanner] = None, ai_planner: Optional[ArchitectAIPlanner] = None):
        self.policy = policy or ToolPolicy()
        self.scanner = scanner or ProjectStateScanner()
        self.ai_planner = ai_planner

    def require_plan(self, agent_name: str, task: Dict[str, Any]) -> Dict[str, Any]:
        plan = self._build_plan(agent_name, task)
        self.validate_plan(plan)
        plan.status = "approved"
        plan_path = self._plan_path(plan.project_id, plan.task_id)
        plan_data = plan.to_dict()
        run_write(plan_path, json.dumps(plan_data, ensure_ascii=False, indent=2))
        return {"success": True, "plan_path": plan_path, "plan": plan_data}

    def require_architect_dynamic_plan(self, task: Dict[str, Any], goal: Dict[str, Any]) -> Dict[str, Any]:
        metadata = task.get("metadata", {}) if isinstance(task.get("metadata"), dict) else {}
        proposed_plan = metadata.get("proposed_plan")
        fallback_reason = ""
        if isinstance(proposed_plan, dict):
            try:
                return self.approve_external_architect_plan(task, proposed_plan, goal)
            except Exception as exc:
                fallback_reason = f"proposed_plan_rejected: {type(exc).__name__}: {exc}"
        plan = self.build_architect_dynamic_plan(task, goal)
        if fallback_reason:
            plan.risk_checklist.append(f"External proposed plan fallback: {fallback_reason}")
        self.validate_plan(plan)
        plan.status = "approved"
        plan_path = self._plan_path(plan.project_id, plan.task_id)
        plan_data = plan.to_dict()
        run_write(plan_path, json.dumps(plan_data, ensure_ascii=False, indent=2))
        return {"success": True, "plan_path": plan_path, "plan": plan_data}

    def approve_external_architect_plan(self, task: Dict[str, Any], raw_plan: Dict[str, Any], goal: Dict[str, Any]) -> Dict[str, Any]:
        metadata = task.get("metadata", {}) if isinstance(task.get("metadata"), dict) else {}
        if metadata.get("task_type") == "architect_feedback_revision":
            raise ValueError("External AI proposed plans are not allowed for feedback revision tasks")
        project_id = metadata.get("project_id") or goal.get("project_id", "")
        chapter_ids = goal.get("chapter_ids") or goal.get("chapter_range") or []
        if isinstance(chapter_ids, str):
            chapter_ids = [chapter_ids]
        state = self.scanner.scan(project_id, chapter_ids)
        plan = self._execution_plan_from_dict(task, raw_plan, goal, state)
        self.validate_plan(plan)
        plan.status = "approved"
        plan_path = self._plan_path(plan.project_id, plan.task_id)
        plan_data = plan.to_dict()
        run_write(plan_path, json.dumps(plan_data, ensure_ascii=False, indent=2))
        return {"success": True, "plan_path": plan_path, "plan": plan_data}

    @staticmethod
    def _should_use_ai_planner(metadata: Dict[str, Any], goal: Dict[str, Any]) -> bool:
        return bool(metadata.get("use_ai_planner") or goal.get("use_ai_planner")) and metadata.get("task_type") != "architect_feedback_revision"

    def try_build_architect_ai_plan(self, task: Dict[str, Any], goal: Dict[str, Any]) -> Optional[ExecutionPlan]:
        if self.ai_planner is None:
            return None
        metadata = task.get("metadata", {}) if isinstance(task.get("metadata"), dict) else {}
        project_id = metadata.get("project_id") or goal.get("project_id", "")
        chapter_ids = goal.get("chapter_ids") or goal.get("chapter_range") or []
        if isinstance(chapter_ids, str):
            chapter_ids = [chapter_ids]
        state = self.scanner.scan(project_id, chapter_ids)
        raw_plan = self.ai_planner.build_plan(task, goal, state, ARCHITECT_ARTIFACT_CONTRACTS)
        if not raw_plan:
            return None
        plan = self._execution_plan_from_dict(task, raw_plan, goal, state)
        self.validate_plan(plan)
        return plan

    def build_architect_dynamic_plan(self, task: Dict[str, Any], goal: Dict[str, Any]) -> ExecutionPlan:
        metadata = task.get("metadata", {}) if isinstance(task.get("metadata"), dict) else {}
        project_id = metadata.get("project_id") or goal.get("project_id", "")
        chapter_ids = goal.get("chapter_ids") or goal.get("chapter_range") or []
        if isinstance(chapter_ids, str):
            chapter_ids = [chapter_ids]
        chapter_id = metadata.get("chapter_id") or (chapter_ids[0] if chapter_ids else "")
        target_artifacts = goal.get("target_artifacts") or self._target_artifacts_for_goal(goal)
        state = self.scanner.scan(project_id, chapter_ids)
        steps: List[PlanStep] = []

        if self._should_plan("story_bible", target_artifacts) and not state["story_bible"]["non_empty"]:
            steps.append(self._dynamic_step(steps, "建立故事级事实源", "story-planner", "story_bible", project_id, "", ["brief", "story_direction"], []))

        if self._should_plan("chapter_outlines", target_artifacts) and not state["chapter_outlines"]["non_empty"]:
            steps.append(self._dynamic_step(steps, "生成全章节结构与章节索引", "story-planner", "chapter_outlines", project_id, "", ["story_bible", "story_direction"], self._ids_for_outputs(steps, ["story_bible"])))

        if "chapter_outline" in target_artifacts or "chapter_script" in target_artifacts:
            for cid in chapter_ids:
                if not state["chapters"].get(cid, {}).get("outline", {}).get("non_empty"):
                    steps.append(self._dynamic_step(steps, f"生成 {cid} 章节细纲", "story-planner", "chapter_outline", project_id, cid, ["chapter_outlines"], self._ids_for_outputs(steps, ["chapter_outlines"])))

        if self._should_plan("character_cards", target_artifacts) and not state["characters"]["exists"]:
            steps.append(self._dynamic_step(steps, "建立角色事实源", "character-builder", "character_cards", project_id, "", ["story_bible", "chapter_outlines"], self._ids_for_outputs(steps, ["story_bible", "chapter_outlines"])))

        if self._should_plan("environment_cards", target_artifacts) and not state["environments"]["exists"]:
            steps.append(self._dynamic_step(steps, "建立场景事实源", "environment-builder", "environment_cards", project_id, "", ["story_bible", "chapter_outlines"], self._ids_for_outputs(steps, ["story_bible", "chapter_outlines"])))

        if "chapter_script" in target_artifacts:
            for cid in chapter_ids:
                if not state["chapters"].get(cid, {}).get("script", {}).get("non_empty"):
                    steps.append(self._dynamic_step(steps, f"生成 {cid} 章节剧本", "chapter-expander", "chapter_script", project_id, cid, ["story_bible", "chapter_outline", "character_cards", "environment_cards"], self._ids_for_outputs(steps, ["story_bible", "chapter_outline", "character_cards", "environment_cards"])))

        deliverables = [artifact for step in steps for artifact in step.output_artifacts]
        return ExecutionPlan(
            task_id=task.get("id", "unknown_task"),
            agent="architect_bot",
            project_id=project_id,
            chapter_id=chapter_id,
            task_type=metadata.get("task_type", goal.get("goal_type", "architect_dynamic_plan")),
            steps=steps,
            risk_checklist=["计划必须由目标产物反推所需输入", "跳过已存在且非空的产物，避免重复覆盖", "所有输出路径必须符合 Architect artifact contract", "正式剧本必须在角色与场景事实源之后生成"],
            requires_user_approval=bool(goal.get("requires_user_approval", False)),
            goal=goal,
            project_state=state,
            target_artifacts=target_artifacts,
            deliverables=deliverables,
            planning_mode="architect_dynamic_v1",
        )

    def validate_plan(self, plan: ExecutionPlan):
        """计划执行前的安全闸门：每一步的工具和路径都必须通过策略校验。"""
        if plan.planning_mode not in {"fixed", ARCHITECT_RULE_PLANNING_MODE, ARCHITECT_AI_PLANNING_MODE}:
            raise ValueError(f"Unsupported planning_mode: {plan.planning_mode}")
        if plan.planning_mode in SUPPORTED_DYNAMIC_PLANNING_MODES and plan.agent != "architect_bot":
            raise ValueError("Dynamic Architect plans must target architect_bot")
        if plan.planning_mode == ARCHITECT_AI_PLANNING_MODE and plan.task_type == "architect_feedback_revision":
            raise ValueError("AI planner is not allowed for feedback revision tasks")
        step_ids = [step.id for step in plan.steps]
        if len(step_ids) != len(set(step_ids)):
            raise ValueError("Duplicate step ids are not allowed")
        known_ids = set(step_ids)
        for step in plan.steps:
            for dep in step.depends_on:
                if dep not in known_ids:
                    raise ValueError(f"Unknown dependency {dep} in {step.id}")
        context = {
            "project_id": plan.project_id,
            "chapter_id": plan.chapter_id,
        }
        for step in plan.steps:
            # 先校验工具是否属于该 agent，再校验 file_path 是否落在允许读写范围。
            self.policy.authorize_tool(plan.agent, step.tool)
            if plan.planning_mode in SUPPORTED_DYNAMIC_PLANNING_MODES and step.tool != "write_file":
                raise ValueError("Dynamic Architect plans may only use write_file steps")
            file_path = step.inputs.get("file_path") or self._concrete_glob_path(step.inputs.get("path_glob"))
            if not file_path:
                continue
            mode = "read" if step.tool == "read_file" else "write"
            self.policy.authorize_path(plan.agent, file_path, mode, context)
            for artifact in step.output_artifacts:
                self._validate_output_artifact(plan, artifact, step.skill)

    def _build_plan(self, agent_name: str, task: Dict[str, Any]) -> ExecutionPlan:
        """根据协议任务类型生成固定计划模板，避免 agent 自由规划越权步骤。"""
        metadata = task.get("metadata", {}) if isinstance(task.get("metadata"), dict) else {}
        task_id = task.get("id", "unknown_task")
        task_type = metadata.get("task_type", "")
        project_id = metadata.get("project_id", "")
        chapter_id = metadata.get("chapter_id", "")

        if task_type == "architect_concept_setup":
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
                        description="读取创作方向",
                        tool="read_file",
                        inputs={"file_path": f"{project_id}/story_direction.md"},
                        expected_output="story_direction.md",
                    ),
                    PlanStep(
                        id="step_3",
                        description="写入故事圣经",
                        tool="write_file",
                        inputs={"file_path": f"{project_id}/story_bible.md"},
                        expected_output="story_bible.md",
                    ),
                ],
                risk_checklist=["世界观清晰", "主线冲突明确", "风格与用户方向一致"],
            )

        if task_type == "architect_chapter_outline_setup":
            return ExecutionPlan(
                task_id=task_id,
                agent=agent_name,
                project_id=project_id,
                chapter_id=chapter_id,
                task_type=task_type,
                steps=[
                    PlanStep(
                        id="step_1",
                        description="读取故事圣经",
                        tool="read_file",
                        inputs={"file_path": f"{project_id}/story_bible.md"},
                        expected_output="story_bible.md",
                    ),
                    PlanStep(
                        id="step_2",
                        description="读取创作方向",
                        tool="read_file",
                        inputs={"file_path": f"{project_id}/story_direction.md"},
                        expected_output="story_direction.md",
                    ),
                    PlanStep(
                        id="step_3",
                        description="写入全章节细纲",
                        tool="write_file",
                        inputs={"file_path": f"{project_id}/chapter_outlines.md"},
                        expected_output="chapter_outlines.md",
                    ),
                ],
                risk_checklist=["章节链路完整", "每章目标明确", "最终章方向清楚"],
            )

        if task_type == "architect_character_setup":
            return ExecutionPlan(
                task_id=task_id,
                agent=agent_name,
                project_id=project_id,
                chapter_id=chapter_id,
                task_type=task_type,
                steps=[
                    PlanStep(
                        id="step_1",
                        description="读取故事圣经",
                        tool="read_file",
                        inputs={"file_path": f"{project_id}/story_bible.md"},
                        expected_output="story_bible.md",
                    ),
                    PlanStep(
                        id="step_2",
                        description="读取全章节细纲",
                        tool="read_file",
                        inputs={"file_path": f"{project_id}/chapter_outlines.md"},
                        expected_output="chapter_outlines.md",
                    ),
                    PlanStep(
                        id="step_3",
                        description="写入角色事实源",
                        tool="write_file",
                        inputs={"file_path": f"{project_id}/state/characters/主角.md"},
                        expected_output="角色事实源文件",
                    ),
                ],
                risk_checklist=["角色动机具体", "视觉锚点可画", "登场章节对应细纲"],
            )

        if task_type == "architect_environment_setup":
            return ExecutionPlan(
                task_id=task_id,
                agent=agent_name,
                project_id=project_id,
                chapter_id=chapter_id,
                task_type=task_type,
                steps=[
                    PlanStep(
                        id="step_1",
                        description="读取故事圣经",
                        tool="read_file",
                        inputs={"file_path": f"{project_id}/story_bible.md"},
                        expected_output="story_bible.md",
                    ),
                    PlanStep(
                        id="step_2",
                        description="读取全章节细纲",
                        tool="read_file",
                        inputs={"file_path": f"{project_id}/chapter_outlines.md"},
                        expected_output="chapter_outlines.md",
                    ),
                    PlanStep(
                        id="step_3",
                        description="写入场景事实源",
                        tool="write_file",
                        inputs={"file_path": f"{project_id}/state/environments/主要场景.md"},
                        expected_output="场景事实源文件",
                    ),
                ],
                risk_checklist=["场景功能对应章节", "视觉锚点清晰", "关键道具可画"],
            )

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
                        description="读取故事圣经",
                        tool="read_file",
                        inputs={"file_path": f"{project_id}/story_bible.md"},
                        expected_output="story_bible.md",
                    ),
                    PlanStep(
                        id="step_3",
                        description="读取全章节细纲",
                        tool="read_file",
                        inputs={"file_path": f"{project_id}/chapter_outlines.md"},
                        expected_output="chapter_outlines.md",
                    ),
                    PlanStep(
                        id="step_4",
                        description="读取本章细纲",
                        tool="read_file",
                        inputs={"file_path": f"{project_id}/chapters/{chapter_id}/outline.md"},
                        expected_output="chapter outline",
                    ),
                    PlanStep(
                        id="step_5",
                        description="写入章节剧本",
                        tool="write_file",
                        inputs={"file_path": f"{project_id}/chapters/{chapter_id}/script.md"},
                        expected_output="script.md",
                    ),
                ],
                risk_checklist=["前置设定齐全", "剧情完整", "角色动机清晰", "角色与场景设定一致"],
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

        if task_type == "artifact_revision":
            expected_outputs = metadata.get("expected_outputs", {}) if isinstance(metadata.get("expected_outputs", {}), dict) else {}
            qa_report = metadata.get("qa_report", {}) if isinstance(metadata.get("qa_report", {}), dict) else {}
            report_file = metadata.get("qa_report_file") or qa_report.get("report_file") or qa_report.get("path") or f"{project_id}/qa/latest_report.md"
            target_files = metadata.get("target_files", []) if isinstance(metadata.get("target_files", []), list) else []
            if not target_files:
                target_files = [
                    f"{project_id}/story_bible.md",
                    f"{project_id}/chapter_outlines.md",
                ]
                if chapter_id:
                    target_files.extend([
                        f"{project_id}/chapters/{chapter_id}/outline.md",
                        f"{project_id}/chapters/{chapter_id}/script.md",
                    ])
            steps = [
                PlanStep(
                    id="step_1",
                    description="读取用户修改请求和 QA 影响报告",
                    tool="read_file",
                    inputs={"file_path": report_file},
                    expected_output="QA 影响报告；如果文件不存在，Architect 应基于任务 metadata 中的 qa_report 继续",
                )
            ]
            step_index = 2
            for file_path in target_files:
                if not isinstance(file_path, str) or not file_path.strip():
                    continue
                steps.append(PlanStep(
                    id=f"step_{step_index}",
                    description="读取待修订的项目 artifact",
                    tool="read_file",
                    inputs={"file_path": file_path},
                    expected_output="待修订 artifact 内容",
                ))
                step_index += 1
                steps.append(PlanStep(
                    id=f"step_{step_index}",
                    description="写入修订后的项目 artifact",
                    tool="write_file",
                    inputs={"file_path": file_path},
                    expected_output=expected_outputs.get("summary", "修订后的 artifact"),
                ))
                step_index += 1
            return ExecutionPlan(
                task_id=task_id,
                agent=agent_name,
                project_id=project_id,
                chapter_id=chapter_id,
                task_type=task_type,
                steps=steps,
                risk_checklist=[
                    "保留用户明确事实",
                    "只修改与本次修改请求和 QA 报告相关的内容",
                    "不得引入与项目事实源冲突的新内容",
                    "不确定项必须在修订总结中列出",
                ],
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
            expected_outputs = metadata.get("expected_outputs", {}) if isinstance(metadata.get("expected_outputs", {}), dict) else {}
            report_file = expected_outputs.get("report_file") or f"{project_id}/qa/{chapter_id or 'latest'}_report.md"
            review_context = metadata.get("review_context", {}) if isinstance(metadata.get("review_context", {}), dict) else {}
            event_type = review_context.get("event_type", "qa_review")
            return ExecutionPlan(
                task_id=task_id,
                agent=agent_name,
                project_id=project_id,
                chapter_id=chapter_id,
                task_type=task_type,
                steps=[
                    PlanStep(
                        id="step_1",
                        description="读取 QA 任务上下文中的项目 artifact",
                        tool="read_file",
                        inputs={"file_path": f"{project_id}/story_bible.md"},
                        expected_output="项目事实源；如果文件不存在，QA Bot 应记录缺失并继续审查可用 artifact",
                    ),
                    PlanStep(
                        id="step_2",
                        description="加载 QA 路由与审查技能",
                        tool="load_skill",
                        inputs={"skill_name": "qa-review-router"},
                        expected_output="QA 审查路由依据",
                    ),
                    PlanStep(
                        id="step_3",
                        description="写入 QA 报告",
                        tool="write_file",
                        inputs={"file_path": report_file},
                        expected_output="QA report",
                    ),
                ],
                risk_checklist=[
                    "QA 只能审查和建议，不直接修改创作文件",
                    "review_goal 由 qa-task-brief-designer 生成，QA Bot 自行选择 skills",
                    f"事件上下文：{event_type}",
                ],
            )

        # 未知 task_type 不直接执行，生成需要人工确认的空计划，防止误写文件。
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

    def _execution_plan_from_dict(self, task: Dict[str, Any], data: Dict[str, Any], goal: Dict[str, Any], state: Dict[str, Any]) -> ExecutionPlan:
        metadata = task.get("metadata", {}) if isinstance(task.get("metadata"), dict) else {}
        steps: List[PlanStep] = []
        for raw_step in data.get("steps", []):
            if not isinstance(raw_step, dict):
                continue
            steps.append(PlanStep(
                id=str(raw_step.get("id", f"step_{len(steps) + 1}")),
                description=str(raw_step.get("description", raw_step.get("purpose", "AI planned step"))),
                tool=str(raw_step.get("tool", "write_file")),
                inputs=raw_step.get("inputs", {}) if isinstance(raw_step.get("inputs", {}), dict) else {},
                expected_output=str(raw_step.get("expected_output", "")),
                skill=raw_step.get("skill"),
                input_artifacts=raw_step.get("input_artifacts", []) if isinstance(raw_step.get("input_artifacts", []), list) else [],
                output_artifacts=raw_step.get("output_artifacts", []) if isinstance(raw_step.get("output_artifacts", []), list) else [],
                depends_on=raw_step.get("depends_on", []) if isinstance(raw_step.get("depends_on", []), list) else [],
                validation_rules=raw_step.get("validation_rules", []) if isinstance(raw_step.get("validation_rules", []), list) else [],
                purpose=str(raw_step.get("purpose", "")),
            ))
        return ExecutionPlan(
            task_id=str(data.get("task_id") or task.get("id", "unknown_task")),
            agent=str(data.get("agent") or "architect_bot"),
            project_id=str(data.get("project_id") or metadata.get("project_id") or goal.get("project_id", "")),
            chapter_id=str(data.get("chapter_id") or metadata.get("chapter_id") or ((goal.get("chapter_ids") or [""])[0] if isinstance(goal.get("chapter_ids"), list) else "")),
            task_type=str(data.get("task_type") or metadata.get("task_type", "architect_delivery")),
            steps=steps,
            risk_checklist=data.get("risk_checklist", []) if isinstance(data.get("risk_checklist", []), list) else [],
            requires_user_approval=bool(data.get("requires_user_approval", False)),
            goal=data.get("goal", goal) if isinstance(data.get("goal", goal), dict) else goal,
            project_state=data.get("project_state", state) if isinstance(data.get("project_state", state), dict) else state,
            target_artifacts=data.get("target_artifacts", goal.get("target_artifacts", [])) if isinstance(data.get("target_artifacts", goal.get("target_artifacts", [])), list) else [],
            deliverables=data.get("deliverables", []) if isinstance(data.get("deliverables", []), list) else [],
            planning_mode=str(data.get("planning_mode", ARCHITECT_AI_PLANNING_MODE)),
        )

    @staticmethod
    def _target_artifacts_for_goal(goal: Dict[str, Any]) -> List[str]:
        goal_type = goal.get("goal_type", "")
        if goal_type in {"create_multi_chapter_story", "create_chapter_script", "architect_delivery"}:
            return ["story_bible", "chapter_outlines", "chapter_outline", "character_cards", "environment_cards", "chapter_script"]
        if goal_type == "setup_story_world":
            return ["story_bible", "chapter_outlines", "character_cards", "environment_cards"]
        return list(goal.get("target_artifacts", []))

    @staticmethod
    def _should_plan(artifact_type: str, target_artifacts: List[str]) -> bool:
        if artifact_type in target_artifacts:
            return True
        return any(artifact_type in ARCHITECT_ARTIFACT_CONTRACTS.get(target, {}).get("required_inputs", []) for target in target_artifacts)

    @staticmethod
    def _ids_for_outputs(steps: List[PlanStep], artifact_types: List[str]) -> List[str]:
        ids: List[str] = []
        for step in steps:
            output_types = {artifact.get("artifact_type") for artifact in step.output_artifacts}
            if output_types.intersection(artifact_types):
                ids.append(step.id)
        return ids

    def _dynamic_step(
        self,
        existing_steps: List[PlanStep],
        purpose: str,
        skill: str,
        output_type: str,
        project_id: str,
        chapter_id: str,
        input_artifacts: List[str],
        depends_on: List[str],
    ) -> PlanStep:
        step_id = f"step_{len(existing_steps) + 1}"
        output_path = self._contract_path(output_type, project_id, chapter_id)
        path_key = "path_glob" if "*" in output_path else "file_path"
        output_artifact: Dict[str, Any] = {
            "artifact_type": output_type,
            "producer_skill": skill,
            "validators": ARCHITECT_ARTIFACT_CONTRACTS[output_type].get("validators", []),
        }
        if path_key == "path_glob":
            output_artifact["path_glob"] = output_path
            output_artifact["path"] = self._concrete_glob_path(output_path)
        else:
            output_artifact["path"] = output_path
        if chapter_id:
            output_artifact["chapter_id"] = chapter_id
        return PlanStep(
            id=step_id,
            description=purpose,
            tool="write_file",
            inputs={path_key: output_path, "skill": skill},
            expected_output=output_type,
            skill=skill,
            input_artifacts=[{"artifact_type": item} for item in input_artifacts],
            output_artifacts=[output_artifact],
            depends_on=depends_on,
            validation_rules=ARCHITECT_ARTIFACT_CONTRACTS[output_type].get("validators", []),
            purpose=purpose,
        )

    def _validate_output_artifact(self, plan: ExecutionPlan, artifact: Dict[str, Any], step_skill: Optional[str] = None):
        artifact_type = artifact.get("artifact_type")
        path = artifact.get("path") or self._concrete_glob_path(artifact.get("path_glob"))
        if not artifact_type or not path or artifact_type not in ARCHITECT_ARTIFACT_CONTRACTS:
            raise ValueError(f"Unknown or incomplete output artifact: {artifact}")
        contract = ARCHITECT_ARTIFACT_CONTRACTS[artifact_type]
        producer_skill = artifact.get("producer_skill") or step_skill
        allowed_skills = contract.get("producer_skills", [])
        if allowed_skills and producer_skill not in allowed_skills:
            raise ValueError(f"Skill {producer_skill} cannot produce {artifact_type}")
        expected = self._contract_path(artifact_type, plan.project_id, artifact.get("chapter_id") or plan.chapter_id)
        expected = self._concrete_glob_path(expected)
        if path != expected:
            raise ValueError(f"Output path violates contract for {artifact_type}: {path} != {expected}")

    @staticmethod
    def _contract_path(artifact_type: str, project_id: str, chapter_id: str = "") -> str:
        return ARCHITECT_ARTIFACT_CONTRACTS[artifact_type]["path_template"].format(project_id=project_id, chapter_id=chapter_id)

    @staticmethod
    def _concrete_glob_path(path_or_glob: Optional[str]) -> Optional[str]:
        if not path_or_glob:
            return None
        if "*" not in path_or_glob:
            return path_or_glob
        return path_or_glob.replace("*.md", "__planned__.md").replace("*", "__planned__")

    @staticmethod
    def _plan_path(project_id: str, task_id: str) -> str:
        if project_id:
            return f"{project_id}/plans/{task_id}.json"
        return f"plans/{task_id}.json"
