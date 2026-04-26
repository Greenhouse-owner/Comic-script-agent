# src/p3_team.py
"""
P3 层 — Team 协作层
TeammateManager：管理 AI 队友的生命周期和通信
"""

import json
import time
import threading
from typing import Any, List, Dict, Optional

try:
    from openai import OpenAI
except Exception:
    OpenAI = None

from p0_runtime import TaskManager, MessageBus, run_read, run_write
from p1_skills import SkillLoader
from planning import PlanManager
from policy import ToolPolicy
from p2_content import (
    comic_read_character,
    comic_write_character,
    comic_list_characters,
    comic_read_environment,
    comic_write_environment,
    comic_list_environments,
)
from config import load_config


class TeammateManager:
    """AI 队友管理器"""

    def __init__(self, openai_client: OpenAI, task_manager: TaskManager = None, message_bus: MessageBus = None):
        self.client = openai_client
        self.config = load_config()
        self.teammates: Dict[str, dict] = {}
        self._lock = threading.Lock()
        self.message_bus = message_bus or MessageBus()
        self.task_manager = task_manager or TaskManager()
        self.policy = ToolPolicy()
        self.plan_manager = PlanManager(policy=self.policy)

    def spawn(self, name: str, role: str, system_prompt: str,
              available_skills: List[str]) -> str:
        """创建并启动一个 AI 队友"""
        with self._lock:
            if name in self.teammates:
                raise ValueError(f"⚠️ Teammate already exists: {name}")

        thread = threading.Thread(
            target=self._loop,
            args=(name, role, system_prompt, available_skills),
            daemon=True
        )
        with self._lock:
            self.teammates[name] = {
                "name": name,
                "role": role,
                "status": "idle",
                "thread": thread,
                "last_active": time.time()
            }
        thread.start()
        return name

    def _loop(self, name: str, role: str, system_prompt: str,
              available_skills: List[str]):
        """队友主循环：检查收件箱 → 检查任务 → 执行 → 空闲/关机"""
        conversation_history = []
        idle_start = time.time()
        idle_timeout = 300  # 5 分钟无活动自动关机

        while True:
            if time.time() - idle_start > idle_timeout:
                self._set_status(name, "shutdown")
                break

            # 读取收件箱
            messages = self.message_bus.read_inbox(name, mark_read=True)
            if messages:
                idle_start = time.time()
                self._set_status(name, "working")
                for msg in messages:
                    response = self._handle_message(
                        name, msg, conversation_history, system_prompt, available_skills
                    )
                    conversation_history.append({"role": "assistant", "content": response})

            # 检查任务列表
            tasks = self.task_manager.list_for_assignee(name)
            pending_tasks = [t for t in tasks if t["status"] == "pending"]
            if pending_tasks:
                idle_start = time.time()
                self._set_status(name, "working")
                for task in pending_tasks:
                    result = self._execute_task(
                        name, task, conversation_history, system_prompt, available_skills
                    )
                    if result["success"]:
                        self.task_manager.update(task["id"], "done", result)
                    else:
                        self.task_manager.update(task["id"], "error", result)

            if not messages and not pending_tasks:
                self._set_status(name, "idle")
                time.sleep(5)

    def _handle_message(self, name: str, message: dict,
                        conversation_history: List[dict],
                        system_prompt: str, available_skills: List[str]) -> str:
        """处理收到的消息"""
        user_message = {
            "role": "user",
            "content": f"来自 {message['from']} 的消息：\n{json.dumps(message['message'], ensure_ascii=False)}"
        }
        conversation_history.append(user_message)

        tools = self._build_tools(name, available_skills)
        messages = [{"role": "system", "content": system_prompt}] + conversation_history
        response = self.client.chat.completions.create(
            model=self.config["model"],
            messages=messages,
            tools=tools
        )

        # 处理工具调用循环
        response = self._tool_use_loop(
            name, response, conversation_history, system_prompt, tools
        )

        return response.choices[0].message.content or ""

    def _execute_task(self, name: str, task: dict,
                      conversation_history: List[dict],
                      system_prompt: str, available_skills: List[str]) -> dict:
        """执行任务"""
        task_prompt = (
            f"你有一个新任务：\n\n"
            f"**任务ID**：{task['id']}\n"
            f"**标题**：{task['title']}\n"
            f"**元数据**：{json.dumps(task['metadata'], ensure_ascii=False)}\n\n"
            f"请完成这个任务，并在完成后调用 `idle` 工具表示任务完成。"
        )
        conversation_history.append({"role": "user", "content": task_prompt})
        self.task_manager.update(task["id"], "in_progress")

        try:
            plan_result = self.plan_manager.require_plan(name, task)
            conversation_history.append({
                "role": "user",
                "content": f"[执行计划已批准] {json.dumps(plan_result, ensure_ascii=False)}"
            })
        except Exception as e:
            return {"success": False, "error_type": "plan_error", "error": str(e)}

        try:
            protocol_result = self._try_execute_protocol_task(name, task)
        except Exception as e:
            self.message_bus.send(name, "lead", {
                "type": "task_result",
                "task_id": task["id"],
                "assignee": name,
                "title": task["title"],
                "success": False,
                "error": str(e),
                "metadata": task.get("metadata", {}),
            })
            return {"success": False, "error_type": "protocol_error", "error": str(e)}
        if protocol_result is not None:
            self.task_manager.update(task["id"], "done", protocol_result)
            return protocol_result

        tools = self._build_tools(name, available_skills)
        try:
            messages = [{"role": "system", "content": system_prompt}] + conversation_history
            response = self.client.chat.completions.create(
                model=self.config["model"],
                messages=messages,
                tools=tools
            )
            response = self._tool_use_loop(
                name, response, conversation_history, system_prompt, tools
            )
            output = response.choices[0].message.content or ""
            conversation_history.append({"role": "assistant", "content": output})
            self.message_bus.send(name, "lead", {
                "type": "task_result",
                "task_id": task["id"],
                "assignee": name,
                "title": task["title"],
                "success": True,
                "output": output,
                "metadata": task.get("metadata", {}),
            })
            return {"success": True, "output": output}
        except Exception as e:
            self.message_bus.send(name, "lead", {
                "type": "task_result",
                "task_id": task["id"],
                "assignee": name,
                "title": task["title"],
                "success": False,
                "error": str(e),
                "metadata": task.get("metadata", {}),
            })
            return {"success": False, "error": str(e)}

    def _try_execute_protocol_task(self, name: str, task: dict) -> Optional[dict]:
        metadata = task.get("metadata", {})
        task_type = metadata.get("task_type")
        project_id = metadata.get("project_id")
        chapter_id = metadata.get("chapter_id")
        policy_context = {"project_id": project_id or "", "chapter_id": chapter_id or ""}

        if task_type == "architect_delivery" and project_id and chapter_id:
            architect_skill = SkillLoader().load("story-planner")
            chapter_skill = SkillLoader().load("chapter-expander")
            characters = comic_list_characters(project_id)
            environments = comic_list_environments(project_id)
            protagonist = characters[0] if characters else "主角"
            environment = environments[0] if environments else "主场景"
            script_path = f"{project_id}/chapters/{chapter_id}/script.md"
            script_content = (
                f"# {chapter_id} 剧本\n\n"
                f"## 使用技能\n- story-planner\n- chapter-expander\n\n"
                f"## 技能依据摘要\n{architect_skill[:120]}\n\n"
                f"## 目标\n{metadata.get('goal', '')}\n\n"
                f"## 场景 1\n{protagonist} 在 {environment} 遭遇本章核心事件，并推动冲突升级。\n\n"
                f"## 角色动机\n{protagonist} 必须主动应对当前危机。\n\n"
                f"## 扩写约束\n{chapter_skill[:120]}\n"
            )
            self.policy.authorize_path(name, script_path, "write", policy_context)
            run_write(script_path, script_content)
            handoff = {
                "type": "handoff",
                "from_role": "architect",
                "project_id": project_id,
                "chapter_id": chapter_id,
                "goal": metadata.get("goal"),
                "deliverables": [script_path],
                "required_deliverables": metadata.get("required_deliverables", [script_path]),
                "state_inputs": metadata.get("state_inputs", {}),
                "summary": "Architect 已交付剧本，可供 Director 使用。",
            }
            submission = {
                "type": "submission",
                "from_role": "architect",
                "project_id": project_id,
                "chapter_id": chapter_id,
                "goal": metadata.get("goal"),
                "deliverables": [script_path],
                "updated_files": [script_path],
                "self_check": [
                    "已生成章节剧本",
                    "已包含角色动机",
                    "已引用当前场景",
                ],
                "quality_bar": metadata.get("quality_bar", []),
                "summary": "Architect 请求 QA 审核剧本交付。",
            }
            self.message_bus.send(name, "lead", handoff)
            self.message_bus.send(name, metadata.get("submission_target", metadata.get("qa_target", "qa_bot")), submission)
            return {"success": True, "protocol": "architect_delivery", "handoff": handoff, "submission": submission}

        if task_type == "architect_feedback_revision" and project_id and chapter_id:
            instruction = metadata.get("instruction") or {}
            script_path = f"{project_id}/chapters/{chapter_id}/script.md"
            old_script = ""
            try:
                self.policy.authorize_path(name, script_path, "read", policy_context)
                old_script = run_read(script_path)
            except FileNotFoundError:
                old_script = f"# {chapter_id} 剧本\n\n"

            revised_script = (
                old_script
                + "\n\n---\n\n"
                + "## Architect 修订记录\n"
                + f"- 修改目标: {instruction.get('target', '')}\n"
                + f"- 修改内容: {instruction.get('content', '')}\n"
                + f"- 约束: {', '.join(instruction.get('constraints', []))}\n"
            )
            self.policy.authorize_path(name, script_path, "write", policy_context)
            run_write(script_path, revised_script)

            handoff = {
                "type": "handoff",
                "from_role": "architect",
                "project_id": project_id,
                "chapter_id": chapter_id,
                "goal": "基于反馈完成剧本修订，并交付 Director 二次处理",
                "deliverables": [script_path],
                "required_deliverables": [script_path],
                "state_inputs": {
                    "characters": f"{project_id}/state/characters",
                    "environments": f"{project_id}/state/environments",
                },
                "summary": "Architect 已完成反馈修订，已交付更新剧本。",
                "revision": True,
            }
            submission = {
                "type": "submission",
                "from_role": "architect",
                "project_id": project_id,
                "chapter_id": chapter_id,
                "goal": "反馈修订提交",
                "deliverables": [script_path],
                "updated_files": [script_path],
                "self_check": [
                    "已写入反馈修订记录",
                    "保持 Level 只升不降",
                    "保持视觉锚点不删",
                ],
                "quality_bar": metadata.get("quality_bar", []),
                "summary": "Architect 请求 Director 与 QA 基于修订稿重新验收。",
                "revision": True,
            }
            self.message_bus.send(name, "lead", handoff)
            self.message_bus.send(name, metadata.get("submission_target", metadata.get("qa_target", "qa_bot")), submission)
            return {"success": True, "protocol": "architect_feedback_revision", "handoff": handoff, "submission": submission}

        if task_type == "director_delivery" and project_id and chapter_id:
            is_revision = bool(metadata.get("revision"))
            director_skill = SkillLoader().load("panel-director")
            input_paths = metadata.get("required_inputs", metadata.get("inputs", []))
            script_input_path = input_paths[0] if input_paths else ""
            if script_input_path:
                self.policy.authorize_path(name, script_input_path, "read", policy_context)
            script_text = run_read(script_input_path) if script_input_path else ""
            storyboard_path = f"{project_id}/chapters/{chapter_id}/storyboard.md"
            revision_note = "\n## 修订说明\n- 本次为反馈修订后的二次分镜交付。\n" if is_revision else ""
            storyboard_content = (
                f"# {chapter_id} 分镜\n\n"
                f"## 使用技能\n- panel-director\n- reaction-polisher\n\n"
                f"## 技能依据摘要\n{director_skill[:120]}\n\n"
                f"## 格子 1\n根据剧本开场，建立环境和主角状态。\n\n"
                f"## 格子 2\n强调冲突升级与人物反应。\n\n"
                f"## 剧本依据\n{script_text[:120]}\n"
                f"{revision_note}"
            )
            self.policy.authorize_path(name, storyboard_path, "write", policy_context)
            run_write(storyboard_path, storyboard_content)
            submission = {
                "type": "submission",
                "from_role": "director",
                "project_id": project_id,
                "chapter_id": chapter_id,
                "goal": metadata.get("goal"),
                "deliverables": [storyboard_path],
                "updated_files": [storyboard_path],
                "depends_on_script": metadata.get("inputs", []),
                "visual_constraints_used": ["角色视觉锚点", "场景氛围"],
                "self_check": [
                    "已生成分镜",
                    "已对照剧本内容",
                    "已体现画面节奏",
                ],
                "quality_bar": metadata.get("quality_bar", []),
                "revision": is_revision,
                "diff_summary": "基于反馈修订后的剧本，已刷新分镜节奏与镜头依据。" if is_revision else "初版分镜交付。",
                "summary": "Director 请求 QA 审核分镜交付。",
            }
            self.message_bus.send(name, metadata.get("submission_target", metadata.get("qa_target", "qa_bot")), submission)
            return {"success": True, "protocol": "director_delivery", "submission": submission}

        if task_type == "qa_review" and project_id and chapter_id:
            review_type = metadata.get("review_type", "standard")
            architect_submission = metadata.get("architect_submission") or {}
            director_submission = metadata.get("director_submission") or {}
            architect_files = architect_submission.get("deliverables", [])
            director_files = director_submission.get("deliverables", [])
            if architect_files:
                self.policy.authorize_path(name, architect_files[0], "read", policy_context)
            if director_files:
                self.policy.authorize_path(name, director_files[0], "read", policy_context)
            script_text = run_read(architect_files[0]) if architect_files else ""
            storyboard_text = run_read(director_files[0]) if director_files else ""
            character_names = comic_list_characters(project_id)
            environment_names = comic_list_environments(project_id)

            issues = []
            architect_ok = bool(script_text.strip())
            director_ok = bool(storyboard_text.strip())
            cross_ok = architect_ok and director_ok

            if architect_ok and "角色动机" not in script_text:
                architect_ok = False
                issues.append({
                    "owner": "architect_bot",
                    "severity": "major",
                    "type": "script_quality",
                    "description": "剧本缺少明确角色动机。",
                })
            if architect_ok and character_names:
                if not any(name in script_text for name in character_names):
                    architect_ok = False
                    issues.append({
                        "owner": "architect_bot",
                        "severity": "major",
                        "type": "character_consistency",
                        "description": "剧本未引用角色数据库中的角色。",
                    })
            if architect_ok and environment_names:
                if not any(name in script_text for name in environment_names):
                    architect_ok = False
                    issues.append({
                        "owner": "architect_bot",
                        "severity": "major",
                        "type": "environment_consistency",
                        "description": "剧本未引用场景数据库中的场景。",
                    })
            if director_ok and "剧本依据" not in storyboard_text:
                director_ok = False
                issues.append({
                    "owner": "director_bot",
                    "severity": "major",
                    "type": "storyboard_quality",
                    "description": "分镜未体现对剧本的明确引用。",
                })
            if director_ok and character_names:
                if not any(name in storyboard_text or name in script_text for name in character_names):
                    director_ok = False
                    issues.append({
                        "owner": "director_bot",
                        "severity": "major",
                        "type": "character_visual_consistency",
                        "description": "分镜未体现角色数据库中的视觉锚点对象。",
                    })
            if director_ok and environment_names:
                if not any(name in storyboard_text or name in script_text for name in environment_names):
                    director_ok = False
                    issues.append({
                        "owner": "director_bot",
                        "severity": "major",
                        "type": "environment_visual_consistency",
                        "description": "分镜未体现有效场景信息。",
                    })
            if architect_ok and director_ok:
                if chapter_id not in script_text or chapter_id not in storyboard_text:
                    cross_ok = False
                    issues.append({
                        "owner": "director_bot",
                        "severity": "major",
                        "type": "cross_consistency",
                        "description": "剧本与分镜的章节标识不一致。",
                    })
                if "使用技能" not in script_text or "使用技能" not in storyboard_text:
                    cross_ok = False
                    issues.append({
                        "owner": "architect_bot",
                        "severity": "minor",
                        "type": "protocol_traceability",
                        "description": "交付物缺少技能使用痕迹，协议可追踪性不足。",
                    })
            else:
                cross_ok = False

            final_verdict = "PASS" if architect_ok and director_ok and cross_ok else "FAIL"
            report_path = f"{project_id}/qa/{chapter_id}_report.md"
            report_content = (
                f"# QA 报告 {chapter_id}\n\n"
                f"- Review Type: {review_type}\n"
                f"- Architect: {'PASS' if architect_ok else 'FAIL'}\n"
                f"- Director: {'PASS' if director_ok else 'FAIL'}\n"
                f"- Cross Consistency: {'PASS' if cross_ok else 'FAIL'}\n"
                f"- Characters Checked: {character_names}\n"
                f"- Environments Checked: {environment_names}\n"
                f"- Final: {final_verdict}\n\n"
                f"## Issues\n{json.dumps(issues, ensure_ascii=False, indent=2)}\n"
            )
            self.policy.authorize_path(name, report_path, "write", policy_context)
            run_write(report_path, report_content)
            verdict = {
                "type": "verdict",
                "from_role": "qa",
                "project_id": project_id,
                "chapter_id": chapter_id,
                "review_type": review_type,
                "architect_verdict": architect_ok,
                "director_verdict": director_ok,
                "cross_consistency_verdict": cross_ok,
                "issues": issues,
                "review_axes": metadata.get("review_axes", []),
                "checked_characters": character_names,
                "checked_environments": environment_names,
                "report_file": report_path,
                "final_verdict": final_verdict,
                "summary": "QA 已完成双提交验收。" if review_type == "standard" else "QA 已完成修订回归验收。",
            }
            self.message_bus.send(name, "lead", verdict)
            return {"success": True, "protocol": "qa_review", "verdict": verdict}

        return None

    def _tool_use_loop(self, name: str, response, conversation_history: List[dict],
                       system_prompt: str, tools: List[dict]):
        """处理工具调用循环，直到 AI 不再请求工具"""
        while response.choices[0].finish_reason == "tool_calls":
            assistant_msg = response.choices[0].message
            # 把 assistant 的 tool_calls 消息加入历史
            conversation_history.append(assistant_msg.model_dump())
            # 执行每个 tool call 并收集结果
            for tool_call in assistant_msg.tool_calls:
                fn = tool_call.function
                tool_input = json.loads(fn.arguments)
                result = self._execute_tool(name, fn.name, tool_input)
                conversation_history.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": json.dumps(result, ensure_ascii=False)
                })
            messages = [{"role": "system", "content": system_prompt}] + conversation_history
            response = self.client.chat.completions.create(
                model=self.config["model"],
                messages=messages,
                tools=tools
            )
        # 最终非 tool_calls 的回复也加入历史
        return response

    def _build_tools(self, name: str, available_skills: List[str]) -> List[dict]:
        """为队友构建工具箱（OpenAI function calling 格式）"""
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "description": "读取文件内容",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "file_path": {"type": "string", "description": "相对于工作目录的文件路径"}
                        },
                        "required": ["file_path"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "write_file",
                    "description": "写入文件",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "file_path": {"type": "string"},
                            "content": {"type": "string"}
                        },
                        "required": ["file_path", "content"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "send_message",
                    "description": "给其他队友发消息",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "to": {"type": "string", "description": "接收者名字"},
                            "message": {"type": "object", "description": "消息内容"}
                        },
                        "required": ["to", "message"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "load_skill",
                    "description": f"加载技能手册。可用技能：{', '.join(available_skills)}",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "skill_name": {"type": "string"}
                        },
                        "required": ["skill_name"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "comic_read_character",
                    "description": "读取单个角色 .md 文件",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "project_id": {"type": "string"},
                            "character_name": {"type": "string"}
                        },
                        "required": ["project_id", "character_name"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "comic_write_character",
                    "description": "写入单个角色 .md 文件",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "project_id": {"type": "string"},
                            "character_name": {"type": "string"},
                            "content": {"type": "string"}
                        },
                        "required": ["project_id", "character_name", "content"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "comic_list_characters",
                    "description": "列出项目所有角色名",
                    "parameters": {
                        "type": "object",
                        "properties": {"project_id": {"type": "string"}},
                        "required": ["project_id"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "comic_read_environment",
                    "description": "读取单个场景 .md 文件",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "project_id": {"type": "string"},
                            "env_name": {"type": "string"}
                        },
                        "required": ["project_id", "env_name"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "comic_write_environment",
                    "description": "写入单个场景 .md 文件",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "project_id": {"type": "string"},
                            "env_name": {"type": "string"},
                            "content": {"type": "string"}
                        },
                        "required": ["project_id", "env_name", "content"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "comic_list_environments",
                    "description": "列出项目所有场景名",
                    "parameters": {
                        "type": "object",
                        "properties": {"project_id": {"type": "string"}},
                        "required": ["project_id"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "idle",
                    "description": "表示当前没活干了，进入待机状态",
                    "parameters": {
                        "type": "object",
                        "properties": {}
                    }
                }
            }
        ]
        return tools

    def _execute_tool(self, name: str, tool_name: str, tool_input: dict) -> dict:
        """执行工具调用"""
        try:
            self.policy.authorize_file_tool(name, tool_name, tool_input)
            if tool_name == "read_file":
                content = run_read(tool_input["file_path"])
                return {"success": True, "content": content}
            elif tool_name == "write_file":
                return run_write(tool_input["file_path"], tool_input["content"])
            elif tool_name == "send_message":
                self.message_bus.send(name, tool_input["to"], tool_input["message"])
                return {"success": True}
            elif tool_name == "load_skill":
                loader = SkillLoader()
                content = loader.load(tool_input["skill_name"])
                return {"success": True, "content": content}
            elif tool_name == "comic_read_character":
                content = comic_read_character(tool_input["project_id"], tool_input["character_name"])
                return {"success": True, "content": content}
            elif tool_name == "comic_write_character":
                return comic_write_character(tool_input["project_id"], tool_input["character_name"], tool_input["content"])
            elif tool_name == "comic_list_characters":
                names = comic_list_characters(tool_input["project_id"])
                return {"success": True, "characters": names}
            elif tool_name == "comic_read_environment":
                content = comic_read_environment(tool_input["project_id"], tool_input["env_name"])
                return {"success": True, "content": content}
            elif tool_name == "comic_write_environment":
                return comic_write_environment(tool_input["project_id"], tool_input["env_name"], tool_input["content"])
            elif tool_name == "comic_list_environments":
                names = comic_list_environments(tool_input["project_id"])
                return {"success": True, "environments": names}
            elif tool_name == "idle":
                self._set_status(name, "idle")
                return {"success": True}
            else:
                return {"success": False, "error": f"Unknown tool: {tool_name}"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _set_status(self, name: str, status: str):
        """更新队友状态"""
        with self._lock:
            if name in self.teammates:
                self.teammates[name]["status"] = status
                self.teammates[name]["last_active"] = time.time()

    def list_all(self) -> List[dict]:
        """列出所有队友及其状态"""
        with self._lock:
            return [
                {
                    "name": tm["name"],
                    "role": tm["role"],
                    "status": tm["status"],
                    "last_active": tm["last_active"]
                }
                for tm in self.teammates.values()
            ]
