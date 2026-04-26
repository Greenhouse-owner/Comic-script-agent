"""Auxiliary temporary subagent manager for Lead-driven JSON tasks."""

from __future__ import annotations

import json
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
    }

    def __init__(self, openai_client, model: str, skill_loader: Optional[SkillLoader] = None):
        self.client = openai_client
        self.model = model
        self.skill_loader = skill_loader or SkillLoader()

    def run(self, task_name: str, payload: dict, context: Optional[dict] = None) -> dict:
        context = context or {}
        if task_name == "input_classifier":
            return self.run_input_classifier(payload, context)
        if task_name == "choice_designer":
            return self.run_choice_designer(payload, context)
        return {"success": False, "error": f"Unknown aux subagent task: {task_name}"}

    def run_input_classifier(self, payload: dict, context: Optional[dict] = None) -> dict:
        context = context or {}
        if not payload.get("user_text"):
            return {"success": False, "error": "payload.user_text is required"}
        schema_hint = {
            "input_type": "new_story | write_chapter | vague_demand",
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
                    "target": "林夜.md 的 Level1 视觉锚点",
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
            return {"success": False, "error": "OpenAI client is not available"}

        real_skill_name = self.SKILL_ALIAS.get(skill_name, skill_name)
        skill_text = self.skill_loader.load(real_skill_name)
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
        for _ in range(retries + 1):
            prompt = base_prompt + ("\n\n上次输出错误：" + repair_note if repair_note else "")
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
            )
            text = (response.choices[0].message.content or "").strip()
            parsed = self._extract_json(text)
            if parsed is None:
                last_error = "model output is not valid JSON"
                repair_note = f"{last_error}。请严格只输出 JSON。"
                continue
            return {"success": True, "data": parsed}

        return {"success": False, "error": last_error}

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
        allowed_types = {"new_story", "write_chapter", "vague_demand"}
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
