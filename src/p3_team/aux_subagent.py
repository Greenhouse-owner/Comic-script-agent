"""Auxiliary temporary subagent manager for Lead-driven JSON tasks."""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from typing import Any, Dict, Optional

from p1_skills import SkillLoader


class AuxSubagentManager:
    """
    Lightweight temporary subagent runner.

    Notes:
    - No persistent thread/state.
    - Spawned by Lead on demand.
    - Always returns machine-parseable JSON dict.
    """

    SKILL_ALIAS = {
        "input_classifier": "input-classifier",
        "choice_designer": "choice-designer",
        "intake_normalizer": "intake-normalizer",
        "story_direction_summarizer": "story-direction-summarizer",
        "planner_decider": "planner-decider",
        "architect_plan_designer": "architect-plan-designer",
        "qa_task_brief_designer": "qa-task-brief-designer",
        "director_task_brief_designer": "director-task-brief-designer",
    }

    def __init__(self, openai_client, model: str, skill_loader: Optional[SkillLoader] = None, timeout_seconds: int = 45):
        self.client = openai_client
        self.model = model
        self.skill_loader = skill_loader or SkillLoader()
        self.timeout_seconds = timeout_seconds

    def _log(self, stage: str, message: str, metadata: Optional[dict] = None):
        metadata_text = f" | {json.dumps(metadata, ensure_ascii=False)}" if metadata else ""
        print(f"[aux_subagent][{stage}] {message}{metadata_text}", flush=True)

    def run(self, task_name: str, payload: dict, context: Optional[dict] = None) -> dict:
        context = context or {}
        self._log(
            "run_start",
            f"开始执行临时 subagent 任务：{task_name}",
            {"payload_keys": list(payload.keys()), "context_keys": list(context.keys())},
        )
        started_at = time.time()
        try:
            # 临时 subagent 只接受白名单任务名，避免 Lead 把任意字符串传成模型任务。
            if task_name == "input_classifier":
                result = self.run_input_classifier(payload, context)
            elif task_name == "choice_designer":
                result = self.run_choice_designer(payload, context)
            elif task_name == "intake_normalizer":
                result = self.run_intake_normalizer(payload, context)
            elif task_name == "story_direction_summarizer":
                result = self.run_story_direction_summarizer(payload, context)
            elif task_name == "planner_decider":
                result = self.run_planner_decider(payload, context)
            elif task_name == "architect_plan_designer":
                result = self.run_architect_plan_designer(payload, context)
            elif task_name == "qa_task_brief_designer":
                result = self.run_qa_task_brief_designer(payload, context)
            elif task_name == "director_task_brief_designer":
                result = self.run_director_task_brief_designer(payload, context)
            else:
                result = {"success": False, "error": f"Unknown aux subagent task: {task_name}"}
        except Exception as e:
            result = {"success": False, "error": str(e), "error_type": type(e).__name__}
            self._log("run_exception", f"任务异常：{type(e).__name__}: {e}", {"task_name": task_name})
        elapsed = round(time.time() - started_at, 3)
        self._log("run_done", f"任务结束：{task_name}, success={result.get('success')}, elapsed={elapsed}s")
        return result

    def run_input_classifier(self, payload: dict, context: Optional[dict] = None) -> dict:
        context = context or {}
        if not payload.get("user_text"):
            return {"success": False, "error": "payload.user_text is required"}
        schema_hint = {
            "input_type": "new_story | story_direction | worldbuilding | chapter_outline | chapter_detail_outline | character_notes | environment_notes | script_draft | mixed_notes | write_chapter | feedback | revision_request | vague_demand",
            "confidence": 0.0,
            "key_info": {},
            "suggestion": "",
        }
        result = self._run_json_skill(
            skill_name="input_classifier",
            task_description=(
                "请判断用户输入属于哪种类型，并输出结构化 JSON。"
                "必须只输出 JSON，不要 Markdown 或解释。"
            ),
            payload=payload,
            context=context,
            schema_hint=schema_hint,
        )
        if not result.get("success"):
            return result
        validation = self._validate_input_classifier_result(result["data"])
        if validation is not None:
            return {"success": False, "error": validation, "data": result["data"]}
        return result

    def run_choice_designer(self, payload: dict, context: Optional[dict] = None) -> dict:
        context = context or {}
        mode = payload.get("mode", "")
        if mode not in {"clarification", "modification"}:
            return {"success": False, "error": "payload.mode must be clarification or modification"}
        schema_hint = {
            "mode": mode,
            "result": {},
        }
        if mode == "clarification":
            schema_hint["result"] = {
                "questions": [{"id": "q1", "question": "", "options": ["", "", ""], "allow_custom": True}]
            }
        else:
            schema_hint["result"] = {
                "instruction": {
                    "target": "目标文件的 Level1 视觉锚点",
                    "content": "新增一个视觉锚点描述",
                    "constraints": ["Level 只升不降", "视觉锚点不删"],
                }
            }

        result = self._run_json_skill(
            skill_name="choice_designer",
            task_description=(
                "请根据输入场景输出结构化 JSON。"
                "若 mode=clarification，输出 3-5 道澄清题。"
                "若 mode=modification，输出结构化修改指令。"
                "必须只输出 JSON。"
            ),
            payload=payload,
            context=context,
            schema_hint=schema_hint,
        )
        if not result.get("success"):
            return result
        validation = self._validate_choice_designer_result(result["data"], mode=mode)
        if validation is not None:
            return {"success": False, "error": validation, "data": result["data"]}
        return result

    def run_intake_normalizer(self, payload: dict, context: Optional[dict] = None) -> dict:
        context = context or {}
        if not payload.get("user_text"):
            return {"success": False, "error": "payload.user_text is required"}
        classification = payload.get("classification")
        if not isinstance(classification, dict):
            return {"success": False, "error": "payload.classification must be object"}
        schema_hint = {
            "project_id": payload.get("project_id", ""),
            "chapter_id": payload.get("chapter_id", "ch01"),
            "facts": {
                "story_direction": "",
                "worldbuilding": "",
                "chapter_outlines": [{"chapter_id": "ch01", "title": "", "summary": ""}],
                "characters": [{"name": "", "role": "", "description": "", "visual_anchors": []}],
                "environments": [{"name": "", "description": "", "visual_anchors": []}],
                "script_draft": "",
            },
            "missing": [],
            "recommended_next": {
                "task_type": "architect_delivery",
                "use_dynamic_plan": True,
                "use_ai_planner": False,
            },
        }
        result = self._run_json_skill(
            skill_name="intake_normalizer",
            task_description=(
                "请把用户输入归纳成可写入漫画项目事实源的结构化 JSON。"
                "保留用户已确定的信息，不要虚构缺失事实。"
                "characters/environments 只放用户明确提到或强烈暗示的对象。"
                "missing 列出后续需要 Architect 补齐的产物。"
                "必须只输出 JSON。"
            ),
            payload=payload,
            context=context,
            schema_hint=schema_hint,
        )
        if not result.get("success"):
            return result
        validation = self._validate_intake_normalizer_result(result["data"])
        if validation is not None:
            return {"success": False, "error": validation, "data": result["data"]}
        return result

    def run_planner_decider(self, payload: dict, context: Optional[dict] = None) -> dict:
        context = context or {}
        classification = payload.get("classification")
        if not isinstance(classification, dict):
            return {"success": False, "error": "payload.classification must be object"}
        schema_hint = {
            "use_dynamic_plan": True,
            "use_ai_planner": False,
            "confidence": 0.0,
            "reason": "",
            "risk_flags": [],
        }
        result = self._run_json_skill(
            skill_name="planner_decider",
            task_description=(
                "请判断本次 Architect 交付任务是否需要 AI Planner。"
                "复杂、多章节、混合输入、缺失事实源较多、用户要求自由规划时 use_ai_planner=true。"
                "简单单章、信息完整、反馈修订或局部修改时 use_ai_planner=false。"
                "必须只输出 JSON。"
            ),
            payload=payload,
            context=context,
            schema_hint=schema_hint,
        )
        if not result.get("success"):
            return result
        validation = self._validate_planner_decider_result(result["data"])
        if validation is not None:
            return {"success": False, "error": validation, "data": result["data"]}
        return result

    def run_architect_plan_designer(self, payload: dict, context: Optional[dict] = None) -> dict:
        context = context or {}
        for field in ("task_metadata", "goal", "project_state", "artifact_contracts"):
            if not isinstance(payload.get(field), dict):
                return {"success": False, "error": f"payload.{field} must be object"}
        project_id = payload.get("goal", {}).get("project_id") or payload.get("task_metadata", {}).get("project_id", "")
        chapter_ids = payload.get("goal", {}).get("chapter_ids") or [payload.get("task_metadata", {}).get("chapter_id", "ch01")]
        chapter_id = chapter_ids[0] if isinstance(chapter_ids, list) and chapter_ids else "ch01"
        target_artifacts = payload.get("goal", {}).get("target_artifacts", [])
        schema_hint = {
            "planning_mode": "ai_architect_dynamic_v1",
            "agent": "architect_bot",
            "project_id": project_id,
            "chapter_id": chapter_id,
            "task_type": "architect_delivery",
            "goal": payload.get("goal", {}),
            "project_state": payload.get("project_state", {}),
            "target_artifacts": target_artifacts,
            "steps": [
                {
                    "id": "step_1",
                    "description": "补齐 Story Bible",
                    "tool": "write_file",
                    "inputs": {"file_path": f"{project_id}/story_bible.md", "skill": "story-planner"},
                    "expected_output": "story_bible",
                    "skill": "story-planner",
                    "input_artifacts": [{"artifact_type": "brief", "path": f"{project_id}/brief.md"}],
                    "output_artifacts": [{"artifact_type": "story_bible", "path": f"{project_id}/story_bible.md", "producer_skill": "story-planner"}],
                    "depends_on": [],
                    "validation_rules": ["exists", "non_empty"],
                    "purpose": "建立故事级事实源",
                }
            ],
            "deliverables": [{"artifact_type": "chapter_script", "path": f"{project_id}/chapters/{chapter_id}/script.md"}],
            "risk_checklist": ["不得覆盖用户已明确事实", "输出路径必须符合 artifact contract", "chapter_script 必须在故事、章节、角色、场景事实源之后生成"],
            "requires_user_approval": False,
        }
        result = self._run_json_skill(
            skill_name="architect_plan_designer",
            task_description=(
                "请生成 Architect 可执行的动态 plan JSON。"
                "输出必须符合 ExecutionPlan.to_dict 结构，并能通过 PlanManager.validate_plan。"
                "只规划 artifact 交付步骤、路径、依赖、producer skill 和校验规则。"
                "不要写具体剧情、角色行为、章节事件、对白或结局。"
                "只规划 write_file steps，不要写具体文件内容。"
                "必须只输出 JSON。"
            ),
            payload=payload,
            context=context,
            schema_hint=schema_hint,
        )
        if not result.get("success"):
            return result
        validation = self._validate_architect_plan_designer_result(result["data"])
        if validation is not None:
            return {"success": False, "error": validation, "data": result["data"]}
        return result

    def run_qa_task_brief_designer(self, payload: dict, context: Optional[dict] = None) -> dict:
        context = context or {}
        event = payload.get("event")
        project_context = payload.get("project_context")
        if not isinstance(event, dict):
            return {"success": False, "error": "payload.event must be object"}
        if not isinstance(project_context, dict):
            return {"success": False, "error": "payload.project_context must be object"}
        project_id = project_context.get("project_id", "")
        chapter_id = project_context.get("chapter_id", "")
        schema_hint = {
            "task_type": "qa_review",
            "project_id": project_id,
            "chapter_id": chapter_id,
            "review_goal": "根据事件上下文生成给 QA Bot 的自然语言审查目标",
            "review_context": {
                "trigger_summary": "",
                "event_type": event.get("event_type", ""),
                "user_request": event.get("raw_user_text", ""),
                "recent_submission": project_context.get("recent_submission", {}),
                "artifact_index": project_context.get("artifact_index", {}),
                "pending_revision": project_context.get("pending_revision", {}),
                "recommended_focus": [],
                "constraints": [
                    "QA-bot 应自行选择并加载合适的 QA skills",
                    "QA-bot 不直接修改创作文件",
                    "审查报告需包含面向用户的摘要和机器可读结果",
                ],
            },
            "expected_outputs": {
                "report_file": f"{project_id}/qa/latest_report.md" if project_id else "qa/latest_report.md",
                "machine_summary": True,
                "lead_message_type": "verdict",
                "required_fields": [
                    "type",
                    "from_role",
                    "project_id",
                    "chapter_id",
                    "task_type",
                    "report_kind",
                    "final_verdict",
                    "summary",
                    "summary_for_user",
                    "issues",
                    "report_file",
                    "review_context",
                    "recommended_actions",
                    "target_files",
                    "requires_architect_follow_up",
                ],
                "allowed_final_verdict": ["PASS", "WARNING", "FAIL"],
            },
            "handoff_message": "请基于 review_goal 和 review_context 执行通用 qa_review。",
        }
        result = self._run_json_skill(
            skill_name="qa_task_brief_designer",
            task_description=(
                "请根据系统事件和项目上下文生成 QA Bot 可执行的通用 qa_review 任务 brief。"
                "review_goal 必须由你根据上下文生成，代码层不做业务审查目标判断。"
                "必须只输出 JSON。"
            ),
            payload=payload,
            context=context,
            schema_hint=schema_hint,
        )
        if not result.get("success"):
            return result
        validation = self._validate_qa_task_brief_designer_result(result["data"])
        if validation is not None:
            return {"success": False, "error": validation, "data": result["data"]}
        return result

    def run_director_task_brief_designer(self, payload: dict, context: Optional[dict] = None) -> dict:
        context = context or {}
        project_id = payload.get("project_id", "")
        chapter_id = payload.get("chapter_id", "") or "ch01"
        if not project_id:
            return {"success": False, "error": "payload.project_id is required"}
        script_path = payload.get("script_path") or f"{project_id}/chapters/{chapter_id}/script.md"
        output_path = payload.get("output_path") or f"{project_id}/chapters/{chapter_id}/storyboard.md"
        schema_hint = {
            "task_type": "director_delivery",
            "project_id": project_id,
            "chapter_id": chapter_id,
            "script_path": script_path,
            "output_path": output_path,
            "input_discovery_mode": "progressive_bounded",
            "pipeline": ["director-context-planner", "director-storyboard-planner", "storyboard-draft-writer"],
            "expected_outputs": {
                "selected_context": f"{project_id}/chapters/{chapter_id}/director_plan/selected_context.json",
                "storyboard_plan": f"{project_id}/chapters/{chapter_id}/director_plan/storyboard_plan.json",
                "pipeline_status": f"{project_id}/chapters/{chapter_id}/director_plan/pipeline_status.json",
                "storyboard": output_path,
            },
            "requires_user_review": True,
            "submission_target": "lead",
            "warnings": [],
        }
        result = self._run_json_skill(
            skill_name="director_task_brief_designer",
            task_description=(
                "请根据 Architect 交付和用户确认信息生成 Director Bot 可执行的 director_delivery 任务 brief。"
                "只生成任务 brief，不要规划页数、格子数或具体画面。必须只输出 JSON。"
            ),
            payload=payload,
            context=context,
            schema_hint=schema_hint,
        )
        if not result.get("success"):
            return result
        data = result["data"]
        required_pipeline = ["director-context-planner", "director-storyboard-planner", "storyboard-draft-writer"]
        data.setdefault("task_type", "director_delivery")
        data.setdefault("project_id", project_id)
        data.setdefault("chapter_id", chapter_id)
        data.setdefault("script_path", script_path)
        data.setdefault("output_path", output_path)
        data.setdefault("input_discovery_mode", "progressive_bounded")
        data["pipeline"] = [item for item in data.get("pipeline", required_pipeline) if item in required_pipeline] or required_pipeline
        data.setdefault("expected_outputs", schema_hint["expected_outputs"])
        data.setdefault("requires_user_review", True)
        data.setdefault("submission_target", "lead")
        return {"success": True, "data": data}

    def run_story_direction_summarizer(self, payload: dict, context: Optional[dict] = None) -> dict:
        context = context or {}
        if not payload.get("user_response"):
            return {"success": False, "error": "payload.user_response is required"}
        pending = payload.get("pending")
        if not isinstance(pending, dict):
            return {"success": False, "error": "payload.pending must be object"}
        schema_hint = {
            "project_id": payload.get("project_id", ""),
            "story_direction": {
                "premise": "",
                "genre": "",
                "tone": "",
                "target_chapter_count": 1,
                "main_character": "",
                "central_conflict": "",
                "ending_direction": "",
                "visual_style": "",
                "user_constraints": [],
                "summary": "",
            },
            "recommended_next": {
                "task_type": "architect_delivery",
                "use_dynamic_plan": True,
                "use_ai_planner": True,
            },
        }
        result = self._run_json_skill(
            skill_name="story_direction_summarizer",
            task_description=(
                "请根据上一轮澄清题、原始故事想法和用户回答，归纳成 story_direction JSON。"
                "必须保留用户明确选择，不要虚构关键设定。"
                "如果用户没有回答某项，用空字符串或空数组表示。"
                "必须只输出 JSON。"
            ),
            payload=payload,
            context=context,
            schema_hint=schema_hint,
        )
        if not result.get("success"):
            return result
        validation = self._validate_story_direction_summarizer_result(result["data"])
        if validation is not None:
            return {"success": False, "error": validation, "data": result["data"]}
        return result

    def _run_json_skill(
        self,
        skill_name: str,
        task_description: str,
        payload: dict,
        context: dict,
        schema_hint: dict,
        retries: int = 2,
    ) -> dict:
        if self.client is None:
            self._log("json_skill_no_client", f"模型客户端不可用：skill={skill_name}")
            return {"success": False, "error": "OpenAI client is not available"}

        started_at = time.time()
        real_skill_name = self.SKILL_ALIAS.get(skill_name, skill_name)
        self._log("json_skill_start", f"开始 JSON 技能任务：{skill_name}", {"real_skill_name": real_skill_name, "retries": retries, "timeout_seconds": self.timeout_seconds})
        try:
            skill_text = self.skill_loader.load(real_skill_name)
            self._log("json_skill_loaded", f"技能加载完成：{real_skill_name}", {"skill_chars": len(skill_text)})
        except Exception as e:
            self._log("json_skill_load_error", f"技能加载失败：{type(e).__name__}: {e}", {"real_skill_name": real_skill_name})
            return {"success": False, "error": str(e), "error_type": type(e).__name__}

        base_prompt = (
            f"{skill_text}\n\n"
            f"## 任务\n{task_description}\n\n"
            f"## payload\n{json.dumps(payload, ensure_ascii=False)}\n\n"
            f"## context\n{json.dumps(context, ensure_ascii=False)}\n\n"
            f"## 目标输出 Schema 示例\n{json.dumps(schema_hint, ensure_ascii=False)}\n\n"
            "只输出 JSON 对象本身。"
        )

        last_error = "unknown"
        repair_note = ""
        for attempt in range(1, retries + 2):
            # 如果模型上一次没有输出合法 JSON，把错误作为修复提示追加进去再重试。
            prompt = base_prompt + ("\n\n上次输出错误：" + repair_note if repair_note else "")
            self._log(
                "json_skill_attempt_start",
                f"第 {attempt}/{retries + 1} 次调用模型：skill={skill_name}",
                {"prompt_chars": len(prompt), "has_repair_note": bool(repair_note)},
            )
            attempt_started_at = time.time()
            try:
                response = self._call_model_with_timeout(prompt)
            except FutureTimeoutError:
                last_error = f"model call timed out after {self.timeout_seconds}s"
                repair_note = f"{last_error}。请尽快严格只输出 JSON。"
                self._log("json_skill_timeout", last_error, {"attempt": attempt, "skill_name": skill_name})
                continue
            except Exception as e:
                last_error = f"{type(e).__name__}: {e}"
                repair_note = f"模型调用异常：{last_error}。请严格只输出 JSON。"
                self._log("json_skill_model_error", last_error, {"attempt": attempt, "skill_name": skill_name})
                continue

            elapsed = round(time.time() - attempt_started_at, 3)
            try:
                text = (response.choices[0].message.content or "").strip()
            except Exception as e:
                last_error = f"invalid model response: {type(e).__name__}: {e}"
                repair_note = f"{last_error}。请严格只输出 JSON。"
                self._log("json_skill_response_error", last_error, {"attempt": attempt, "elapsed": elapsed})
                continue

            self._log("json_skill_attempt_done", f"模型返回：chars={len(text)}, elapsed={elapsed}s", {"attempt": attempt, "preview": text[:120]})
            parsed = self._extract_json(text)
            if parsed is None:
                # 只要不能解析成 dict，就不让它进入后续业务逻辑，避免半结构化文本污染流程。
                last_error = "model output is not valid JSON"
                repair_note = f"{last_error}。请严格只输出 JSON。"
                self._log("json_skill_parse_error", last_error, {"attempt": attempt, "preview": text[:200]})
                continue

            total_elapsed = round(time.time() - started_at, 3)
            self._log("json_skill_success", f"JSON 技能任务成功：{skill_name}, elapsed={total_elapsed}s", {"attempt": attempt, "data_keys": list(parsed.keys())})
            return {"success": True, "data": parsed}

        total_elapsed = round(time.time() - started_at, 3)
        self._log("json_skill_failed", f"JSON 技能任务失败：{skill_name}, elapsed={total_elapsed}s, error={last_error}")
        return {"success": False, "error": last_error}

    def _call_model_with_timeout(self, prompt: str):
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(
                self.client.chat.completions.create,
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
            )
            return future.result(timeout=self.timeout_seconds)

    @staticmethod
    def _extract_json(text: str) -> Optional[Dict[str, Any]]:
        raw = text.strip()
        if raw.startswith("```json"):
            raw = raw.split("```json", 1)[1].split("```", 1)[0].strip()
        elif raw.startswith("```"):
            raw = raw.split("```", 1)[1].split("```", 1)[0].strip()
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                return data
            return None
        except Exception:
            return None

    @staticmethod
    def _validate_input_classifier_result(data: dict) -> Optional[str]:
        allowed_types = {
            "new_story",
            "story_direction",
            "worldbuilding",
            "chapter_outline",
            "chapter_detail_outline",
            "character_notes",
            "environment_notes",
            "script_draft",
            "mixed_notes",
            "write_chapter",
            "feedback",
            "revision_request",
            "vague_demand",
        }
        if data.get("input_type") not in allowed_types:
            return "input_type is invalid"
        confidence = data.get("confidence")
        if not isinstance(confidence, (int, float)):
            return "confidence must be number"
        if confidence < 0 or confidence > 1:
            return "confidence must be between 0 and 1"
        if not isinstance(data.get("key_info"), dict):
            return "key_info must be object"
        if "suggestion" not in data:
            return "suggestion is required"
        return None

    @staticmethod
    def _validate_choice_designer_result(data: dict, mode: str) -> Optional[str]:
        if data.get("mode") != mode:
            return "mode mismatch"
        result = data.get("result")
        if not isinstance(result, dict):
            return "result must be object"

        if mode == "clarification":
            questions = result.get("questions")
            if not isinstance(questions, list):
                return "questions must be list"
            if len(questions) < 3 or len(questions) > 5:
                return "questions length must be 3-5"
            for q in questions:
                if not isinstance(q, dict):
                    return "question item must be object"
                if "question" not in q or "options" not in q:
                    return "question/options are required"
                if not isinstance(q.get("options"), list) or len(q["options"]) < 2:
                    return "each question must have at least 2 options"
            return None

        instruction = result.get("instruction")
        if not isinstance(instruction, dict):
            return "instruction must be object"
        for field in ("target", "content", "constraints"):
            if field not in instruction:
                return f"instruction.{field} is required"
        if not isinstance(instruction["constraints"], list) or not instruction["constraints"]:
            return "instruction.constraints must be non-empty list"
        return None

    @staticmethod
    def _validate_intake_normalizer_result(data: dict) -> Optional[str]:
        if not isinstance(data.get("facts"), dict):
            return "facts must be object"
        facts = data["facts"]
        for field in ("story_direction", "worldbuilding", "script_draft"):
            if field in facts and not isinstance(facts[field], str):
                return f"facts.{field} must be string"
        for field in ("chapter_outlines", "characters", "environments"):
            if field in facts and not isinstance(facts[field], list):
                return f"facts.{field} must be list"
        if "missing" in data and not isinstance(data["missing"], list):
            return "missing must be list"
        recommended_next = data.get("recommended_next", {})
        if recommended_next and not isinstance(recommended_next, dict):
            return "recommended_next must be object"
        for field in ("use_dynamic_plan", "use_ai_planner"):
            if field in recommended_next and not isinstance(recommended_next[field], bool):
                return f"recommended_next.{field} must be bool"
        return None

    @staticmethod
    def _validate_story_direction_summarizer_result(data: dict) -> Optional[str]:
        direction = data.get("story_direction")
        if not isinstance(direction, dict):
            return "story_direction must be object"
        for field in (
            "premise",
            "genre",
            "tone",
            "main_character",
            "central_conflict",
            "ending_direction",
            "visual_style",
            "summary",
        ):
            if field in direction and not isinstance(direction[field], str):
                return f"story_direction.{field} must be string"
        if "target_chapter_count" in direction and not isinstance(direction["target_chapter_count"], int):
            return "story_direction.target_chapter_count must be int"
        if "user_constraints" in direction and not isinstance(direction["user_constraints"], list):
            return "story_direction.user_constraints must be list"
        recommended_next = data.get("recommended_next", {})
        if recommended_next and not isinstance(recommended_next, dict):
            return "recommended_next must be object"
        return None

    @staticmethod
    def _validate_qa_task_brief_designer_result(data: dict) -> Optional[str]:
        if data.get("task_type") != "qa_review":
            return "task_type must be qa_review"
        if not isinstance(data.get("project_id"), str):
            return "project_id must be string"
        if "chapter_id" in data and not isinstance(data.get("chapter_id"), str):
            return "chapter_id must be string"
        if not isinstance(data.get("review_goal"), str) or not data.get("review_goal", "").strip():
            return "review_goal is required"
        if not isinstance(data.get("review_context"), dict):
            return "review_context must be object"
        if not isinstance(data.get("expected_outputs"), dict):
            return "expected_outputs must be object"
        if not isinstance(data.get("handoff_message", ""), str):
            return "handoff_message must be string"
        return None

    @staticmethod
    def _validate_architect_plan_designer_result(data: dict) -> Optional[str]:
        if data.get("planning_mode") != "ai_architect_dynamic_v1":
            return "planning_mode must be ai_architect_dynamic_v1"
        if data.get("agent") != "architect_bot":
            return "agent must be architect_bot"
        if data.get("task_type") != "architect_delivery":
            return "task_type must be architect_delivery"
        steps = data.get("steps")
        if not isinstance(steps, list) or not steps:
            return "steps must be non-empty list"
        ids = set()
        for step in steps:
            if not isinstance(step, dict):
                return "step item must be object"
            step_id = step.get("id")
            if not isinstance(step_id, str) or not step_id:
                return "step.id is required"
            if step_id in ids:
                return "step.id must be unique"
            ids.add(step_id)
            if step.get("tool") != "write_file":
                return "step.tool must be write_file"
            inputs = step.get("inputs")
            if not isinstance(inputs, dict):
                return "step.inputs must be object"
            if not isinstance(inputs.get("file_path"), str) or not inputs.get("file_path"):
                return "step.inputs.file_path is required"
            outputs = step.get("output_artifacts")
            if not isinstance(outputs, list) or not outputs:
                return "step.output_artifacts must be non-empty list"
            for artifact in outputs:
                if not isinstance(artifact, dict):
                    return "output_artifact must be object"
                if not isinstance(artifact.get("artifact_type"), str) or not artifact.get("artifact_type"):
                    return "output_artifact.artifact_type is required"
                if not isinstance(artifact.get("path"), str) or not artifact.get("path"):
                    return "output_artifact.path is required"
            depends_on = step.get("depends_on", [])
            if depends_on and not isinstance(depends_on, list):
                return "step.depends_on must be list"
        if "risk_checklist" in data and not isinstance(data["risk_checklist"], list):
            return "risk_checklist must be list"
        if "deliverables" in data and not isinstance(data["deliverables"], list):
            return "deliverables must be list"
        return None

    @staticmethod
    def _validate_planner_decider_result(data: dict) -> Optional[str]:
        for field in ("use_dynamic_plan", "use_ai_planner"):
            if not isinstance(data.get(field), bool):
                return f"{field} must be bool"
        confidence = data.get("confidence")
        if not isinstance(confidence, (int, float)):
            return "confidence must be number"
        if confidence < 0 or confidence > 1:
            return "confidence must be between 0 and 1"
        if not isinstance(data.get("reason"), str):
            return "reason must be string"
        if not isinstance(data.get("risk_flags"), list):
            return "risk_flags must be list"
        return None

