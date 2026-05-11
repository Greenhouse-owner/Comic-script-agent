# src/p3_team.py
"""
P3 层 — Team 协作层
TeammateManager：管理 AI 队友的生命周期和通信
"""

import json
import re
import time
import threading
from typing import Any, List, Dict, Optional

try:
    from openai import OpenAI
except Exception:
    OpenAI = None

from p0_runtime import TaskManager, MessageBus, EventLogger, run_read, run_write, safe_path
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

    def __init__(self, openai_client: OpenAI, task_manager: TaskManager = None, message_bus: MessageBus = None, event_logger: EventLogger = None):
        self.client = openai_client
        self.config = load_config()
        self.teammates: Dict[str, dict] = {}
        self._lock = threading.Lock()
        self.message_bus = message_bus or MessageBus()
        self.task_manager = task_manager or TaskManager()
        self.event_logger = event_logger or EventLogger()
        self.policy = ToolPolicy()
        self.plan_manager = PlanManager(policy=self.policy)

    @staticmethod
    def _is_architect(agent_name: str) -> bool:
        return agent_name == "architect_bot"

    def _debug_architect(self, stage: str, message: str, metadata: Optional[Dict[str, Any]] = None):
        """Architect 专用细粒度调试日志，用来定位最后卡住的具体步骤。"""
        self._emit_event("architect_bot", f"architect_debug_{stage}", message, metadata or {})

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
        self._emit_event(name, "spawned", f"{role} 已启动", {"available_skills": available_skills})
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
                if self._is_architect(name):
                    self._debug_architect("loop_shutdown", f"主循环结束：idle_seconds={time.time() - idle_start:.1f}, idle_timeout={idle_timeout}")
                self._emit_event(name, "shutdown", "队友因空闲超时自动关闭")
                break

            if self._is_architect(name):
                self._debug_architect("loop_tick", f"开始轮询：history_len={len(conversation_history)}, idle_seconds={time.time() - idle_start:.1f}")

            # 读取收件箱
            messages = self.message_bus.read_inbox(name, mark_read=True)
            if self._is_architect(name):
                self._debug_architect("inbox_checked", f"收件箱检查完成：messages={len(messages)}")
            if messages:
                idle_start = time.time()
                self._set_status(name, "working")
                self._emit_event(name, "messages_received", f"收到 {len(messages)} 条消息")
                for msg in messages:
                    if self._is_architect(name):
                        payload = msg.get("message", {})
                        self._debug_architect("message_start", f"开始处理消息：from={msg.get('from')}, type={payload.get('type')}", {"message": payload})
                    try:
                        response = self._handle_message(
                            name, msg, conversation_history, system_prompt, available_skills
                        )
                        if self._is_architect(name):
                            self._debug_architect("message_done", f"消息处理完成：response_len={len(response or '')}")
                    except Exception as e:
                        response = f"[message_error] {type(e).__name__}: {e}"
                        self._emit_event(name, "message_error", str(e), {"error_type": type(e).__name__})
                        self.message_bus.send(name, "lead", {
                            "type": "message_error",
                            "assignee": name,
                            "success": False,
                            "error_type": type(e).__name__,
                            "error": str(e),
                            "source_message": msg.get("message", {}),
                        })
                    conversation_history.append({"role": "assistant", "content": response})

            # 检查任务列表
            tasks = self.task_manager.list_for_assignee(name)
            pending_tasks = [t for t in tasks if t["status"] == "pending"]
            if self._is_architect(name):
                self._debug_architect("tasks_checked", f"任务检查完成：visible_tasks={len(tasks)}, pending_tasks={len(pending_tasks)}", {"task_ids": [t.get("id") for t in pending_tasks]})
            if pending_tasks:
                idle_start = time.time()
                self._set_status(name, "working")
                self._emit_event(name, "pending_tasks", f"发现 {len(pending_tasks)} 个待执行任务")
                for task in pending_tasks:
                    if self._is_architect(name):
                        meta = task.get("metadata", {}) if isinstance(task.get("metadata"), dict) else {}
                        self._debug_architect("task_loop_item_start", f"准备执行任务：id={task.get('id')}, title={task.get('title')}, type={meta.get('task_type')}", meta)
                    result = self._execute_task(
                        name, task, conversation_history, system_prompt, available_skills
                    )
                    if self._is_architect(name):
                        self._debug_architect("task_loop_item_done", f"任务执行函数返回：id={task.get('id')}, success={result.get('success')}, keys={list(result.keys())}", {"result": result})
                    if result["success"]:
                        self.task_manager.update(task["id"], "done", result)
                        self._emit_event(name, "task_done", f"任务完成：{task.get('title', task['id'])}", {"task_id": task["id"]})
                    else:
                        self.task_manager.update(task["id"], "error", result)
                        self._emit_event(name, "task_error", f"任务失败：{task.get('title', task['id'])} - {result.get('error')}", {"task_id": task["id"], "result": result})

            if not messages and not pending_tasks:
                self._set_status(name, "idle")
                if self._is_architect(name):
                    self._debug_architect("loop_idle", "本轮无消息、无待执行任务，休眠 5 秒")
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
        if self._is_architect(name):
            self._debug_architect("handle_message_before_model", f"即将调用模型处理消息：history_len={len(conversation_history)}, available_skills={available_skills}")

        tools = self._build_tools(name, available_skills)
        messages = [{"role": "system", "content": system_prompt}] + conversation_history
        response = self.client.chat.completions.create(
            model=self.config["model"],
            messages=messages,
            tools=tools
        )
        if self._is_architect(name):
            finish_reason = response.choices[0].finish_reason if response and response.choices else "unknown"
            self._debug_architect("handle_message_after_model", f"模型消息响应返回：finish_reason={finish_reason}")

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
        metadata = task.get("metadata", {}) if isinstance(task.get("metadata"), dict) else {}
        event_meta = {
            "task_id": task.get("id"),
            "task_type": metadata.get("task_type"),
            "project_id": metadata.get("project_id"),
            "chapter_id": metadata.get("chapter_id"),
        }
        self._emit_event(name, "task_started", f"开始执行任务：{task.get('title', task.get('id'))}", event_meta)
        if self._is_architect(name):
            self._debug_architect("execute_task_enter", f"进入 _execute_task：task_id={task.get('id')}, task_type={metadata.get('task_type')}, project_id={metadata.get('project_id')}, chapter_id={metadata.get('chapter_id')}", event_meta)

        try:
            if self._is_architect(name):
                self._debug_architect("plan_before", "准备生成并校验执行计划", event_meta)
            plan_result = self._require_execution_plan(name, task)
            self._emit_event(name, "plan_approved", f"执行计划已生成：{plan_result.get('plan_path')}", {**event_meta, "plan_path": plan_result.get("plan_path")})
            if self._is_architect(name):
                plan = plan_result.get("plan", {})
                self._debug_architect("plan_after", f"执行计划完成：plan_path={plan_result.get('plan_path')}, steps={len(plan.get('steps', []))}", {**event_meta, "plan_path": plan_result.get("plan_path"), "steps": plan.get("steps", [])})
            conversation_history.append({
                "role": "user",
                "content": self._build_architect_plan_execution_prompt(task, plan_result),
            })
        except Exception as e:
            self._emit_event(name, "plan_error", str(e), event_meta)
            return {"success": False, "error_type": "plan_error", "error": str(e)}

        dynamic_result = None
        if self._should_use_dynamic_plan_executor_fallback(metadata):
            try:
                dynamic_result = self._try_execute_dynamic_plan(name, task, plan_result.get("plan", {}))
            except Exception as e:
                self._emit_event(name, "dynamic_plan_error", str(e), event_meta)
                dynamic_result = None
            if dynamic_result is not None:
                if self._is_architect(name):
                    self._debug_architect("dynamic_plan_after", f"动态计划 fallback 执行完成：success={dynamic_result.get('success')}", event_meta)
                self.task_manager.update(task["id"], "done", dynamic_result)
                return dynamic_result

        if not self._should_use_architect_dynamic_plan(metadata):
            try:
                if self._is_architect(name):
                    self._debug_architect("protocol_before", "准备进入协议快捷执行分支", event_meta)
                protocol_result = self._try_execute_protocol_task(name, task)
            except Exception as e:
                self._emit_event(name, "protocol_error", str(e), event_meta)
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
                if self._is_architect(name):
                    self._debug_architect("protocol_after", f"协议快捷执行完成：protocol={protocol_result.get('protocol')}, success={protocol_result.get('success')}", event_meta)
                self.task_manager.update(task["id"], "done", protocol_result)
                return protocol_result

        tools = self._build_tools(name, available_skills)
        policy_context = {"project_id": metadata.get("project_id", ""), "chapter_id": metadata.get("chapter_id", "")}
        try:
            if self._is_architect(name):
                self._debug_architect("model_task_before", f"协议未命中，准备调用模型执行任务：tools={len(tools)}, history_len={len(conversation_history)}", event_meta)
            messages = [{"role": "system", "content": system_prompt}] + conversation_history
            response = self.client.chat.completions.create(
                model=self.config["model"],
                messages=messages,
                tools=tools
            )
            response = self._tool_use_loop(
                name, response, conversation_history, system_prompt, tools,
                policy_context=policy_context
            )
            output = response.choices[0].message.content or ""
            if self._is_architect(name):
                self._debug_architect("model_task_after", f"模型任务响应完成：output_len={len(output)}", event_meta)
            conversation_history.append({"role": "assistant", "content": output})

            # 检查动态计划的必要产出物是否已生成
            if self._is_architect(name) and self._should_use_architect_dynamic_plan(metadata):
                missing = self._check_plan_deliverables(plan_result.get("plan", {}))
                if missing:
                    self._emit_event(name, "deliverables_incomplete",
                                     f"产出物不完整：{missing}", event_meta)
                    self.message_bus.send(name, "lead", {
                        "type": "task_result",
                        "task_id": task["id"],
                        "assignee": name,
                        "title": task["title"],
                        "success": False,
                        "error": f"产出物不完整：{missing}",
                        "metadata": task.get("metadata", {}),
                    })
                    return {"success": False, "error": f"deliverables_incomplete: {missing}"}

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

    def _require_execution_plan(self, name: str, task: dict) -> dict:
        metadata = task.get("metadata", {}) if isinstance(task.get("metadata"), dict) else {}
        if self._is_architect(name) and self._should_use_architect_dynamic_plan(metadata):
            goal = self._architect_goal_from_task(task)
            return self.plan_manager.require_architect_dynamic_plan(task, goal)
        return self.plan_manager.require_plan(name, task)

    @staticmethod
    def _build_architect_plan_execution_prompt(task: dict, plan_result: dict) -> str:
        plan = plan_result.get("plan", {}) if isinstance(plan_result.get("plan", {}), dict) else {}
        metadata = task.get("metadata", {}) if isinstance(task.get("metadata", {}), dict) else {}
        if metadata.get("task_type") == "artifact_revision":
            return "\n".join([
                "[Architect artifact_revision 执行计划已批准]",
                "你现在执行的是修订任务，不是重写整个项目。",
                "执行规则：",
                "1. 读取 metadata.change_request.raw_user_text，明确用户原始修改请求。",
                "2. 读取 metadata.qa_report_file；如果文件不存在，使用 metadata.qa_report。",
                "3. 按 revision_mode 控制范围：local=直接目标，targeted=直接目标和高置信度影响文件，cascade=所有可能受影响 artifact。",
                "4. 优先修订 metadata.target_files；如果为空，依据 QA 报告和项目 artifact 判断最小必要范围。",
                "5. 每个文件写入前必须先 read_file，所有具体修订判断必须依据 skills 和模型完成，不由代码规则决定。",
                "6. 不得覆盖用户明确事实，不得引入与项目事实源冲突的新内容。",
                "7. 完成后必须 send_message 给 lead，type=submission，task_type=artifact_revision，revision=true，并包含 revised_files、deliverables、revision_summary、user_visible_summary。",
                "8. 最后调用 idle。",
                "任务 metadata：",
                json.dumps(metadata, ensure_ascii=False, indent=2),
                "批准的 plan result：",
                json.dumps(plan_result, ensure_ascii=False, indent=2),
            ])
        if plan.get("planning_mode") not in {"architect_dynamic_v1", "ai_architect_dynamic_v1"}:
            return f"[执行计划已批准] {json.dumps(plan_result, ensure_ascii=False)}"
        return "\n".join([
            "[Architect 执行计划已批准]",
            "你现在必须自己执行下面的 plan JSON。系统不会替你自动写事实源。",
            "执行规则：",
            "1. 严格按 plan.steps 顺序执行，不要跳步。",
            "2. 每个 step 只能使用 step.tool 指定的工具；当前 dynamic plan 只允许 write_file。",
            "3. 写入文件前，必须用 read_file 读取该 step.input_artifacts 中列出的已有输入文件；如果需要技能手册，先调用 load_skill。",
            "4. 写入内容必须继承用户事实源，不得编造已知事实；缺失内容要标注待补齐。",
            "5. 每个 write_file 的 file_path 必须是具体文件路径。如果 step.inputs 中有 path_glob（含 *），你需要为每个实体生成独立的 write_file 调用，路径格式为 `{project_id}/state/characters/{角色名}.md` 或 `{project_id}/state/environments/{场景名}.md`。不要把 path_glob 直接传给 write_file。如果 step.inputs 中有 file_path，直接使用该路径。",
            "6. 完成所有 steps 后，使用 send_message 向 submission_target 发送 submission；如果产物是 chapter_script，也向 Lead 发送 handoff。submission 里必须包含 user_visible_summary，列出本次生成/更新的角色、场景、章纲、细纲、剧本等文件，并提示用户可以查看后继续提建议。",
            "7. 最后调用 idle。",
            "任务 metadata：",
            json.dumps(metadata, ensure_ascii=False, indent=2),
            "批准的 plan result：",
            json.dumps(plan_result, ensure_ascii=False, indent=2),
        ])

    @staticmethod
    def _should_use_dynamic_plan_executor_fallback(metadata: dict) -> bool:
        return bool(metadata.get("enable_dynamic_plan_executor_fallback"))

    @staticmethod
    def _should_use_architect_dynamic_plan(metadata: dict) -> bool:
        return bool(metadata.get("use_dynamic_plan")) or metadata.get("planning_mode") == "architect_dynamic_v1"

    @staticmethod
    def _check_plan_deliverables(plan: dict) -> list:
        """检查计划中的产出物是否已实际生成（跳过 glob 占位路径）"""
        missing = []
        for step in plan.get("steps", []):
            for artifact in step.get("output_artifacts", []):
                path = artifact.get("path")
                if not path or "*" in path or "__planned__" in path:
                    continue
                if not safe_path(path).exists():
                    missing.append(path)
        return missing

    @staticmethod
    def _architect_goal_from_task(task: dict) -> dict:
        metadata = task.get("metadata", {}) if isinstance(task.get("metadata"), dict) else {}
        task_type = metadata.get("task_type", "")
        project_id = metadata.get("project_id", "")
        chapter_id = metadata.get("chapter_id", "")
        chapter_ids = metadata.get("chapter_ids") or ([chapter_id] if chapter_id else [])
        if isinstance(chapter_ids, str):
            chapter_ids = [chapter_ids]
        goal_type_by_task = {
            "architect_delivery": "create_chapter_script",
            "architect_concept_setup": "setup_story_world",
            "architect_chapter_outline_setup": "setup_story_world",
            "architect_character_setup": "setup_story_world",
            "architect_environment_setup": "setup_story_world",
        }
        goal = {
            "goal_type": metadata.get("goal_type") or goal_type_by_task.get(task_type, task_type or "architect_dynamic_plan"),
            "project_id": project_id,
            "chapter_ids": chapter_ids,
        }
        if metadata.get("target_artifacts"):
            goal["target_artifacts"] = metadata.get("target_artifacts")
        if metadata.get("requires_user_approval") is not None:
            goal["requires_user_approval"] = bool(metadata.get("requires_user_approval"))
        return goal

    def _try_execute_dynamic_plan(self, name: str, task: dict, plan: dict) -> Optional[dict]:
        if not (self._is_architect(name) and plan.get("planning_mode") in {"architect_dynamic_v1", "ai_architect_dynamic_v1"}):
            return None
        metadata = task.get("metadata", {}) if isinstance(task.get("metadata"), dict) else {}
        project_id = plan.get("project_id") or metadata.get("project_id")
        if not project_id:
            return None
        policy_context = {"project_id": project_id, "chapter_id": plan.get("chapter_id") or metadata.get("chapter_id", "")}
        deliverables: List[str] = []
        executed_steps: List[str] = []
        for step in plan.get("steps", []):
            output_artifacts = step.get("output_artifacts") or []
            if not output_artifacts:
                continue
            artifact = output_artifacts[0]
            artifact_type = artifact.get("artifact_type")
            chapter_id = artifact.get("chapter_id") or plan.get("chapter_id") or metadata.get("chapter_id", "")
            step_context = {**policy_context, "chapter_id": chapter_id or policy_context.get("chapter_id", "")}
            files = self._execute_architect_dynamic_step(name, project_id, chapter_id, artifact_type, artifact, step_context, metadata)
            if files is None:
                self._emit_event(name, "dynamic_plan_unsupported", f"动态执行器不支持产物：{artifact_type}", {"step": step})
                return None
            deliverables.extend(files)
            executed_steps.append(step.get("id", ""))
            self._emit_event(name, "dynamic_step_done", f"动态计划步骤完成：{artifact_type}", {"step_id": step.get("id"), "files": files})
        if not executed_steps:
            return None
        user_visible_summary = self._build_architect_user_visible_summary(
            project_id,
            plan.get("chapter_id") or metadata.get("chapter_id", ""),
            deliverables,
        )
        submission = {
            "type": "submission",
            "from_role": "architect",
            "project_id": project_id,
            "chapter_id": plan.get("chapter_id") or metadata.get("chapter_id", ""),
            "task_type": metadata.get("task_type", "architect_dynamic_plan"),
            "planning_mode": "architect_dynamic_v1",
            "executed_steps": executed_steps,
            "deliverables": deliverables,
            "updated_files": deliverables,
            "summary": "Architect 已按动态计划完成产物生成。",
            "user_visible_summary": user_visible_summary,
        }
        self.message_bus.send(name, metadata.get("submission_target", "lead"), submission)
        self._emit_event(name, "dynamic_plan_executed", "Architect 动态计划执行完成", {"project_id": project_id, "deliverables": deliverables})
        return {"success": True, "protocol": "architect_dynamic_plan", "submission": submission, "deliverables": deliverables}

    @staticmethod
    def _artifact_label_from_path(file_path: str) -> str:
        if file_path.endswith("/story_bible.md"):
            return "故事圣经"
        if file_path.endswith("/chapter_outlines.md"):
            return "全章节章纲"
        if "/chapters/" in file_path and file_path.endswith("/outline.md"):
            return "章节细纲"
        if "/state/characters/" in file_path:
            return "角色卡"
        if "/state/environments/" in file_path:
            return "场景卡"
        if "/chapters/" in file_path and file_path.endswith("/script.md"):
            return "章节剧本"
        return "产物"

    @classmethod
    def _build_architect_user_visible_summary(cls, project_id: str, chapter_id: str, files: List[str]) -> str:
        lines = [
            "Architect 已完成本轮创作产物并写入 workspace。",
            f"项目：{project_id}" + (f"，章节：{chapter_id}" if chapter_id else ""),
            "",
            "本次生成/更新：",
        ]
        for file_path in files:
            lines.append(f"- {cls._artifact_label_from_path(file_path)}：`{file_path}`")
        lines.extend([
            "",
            "你可以先打开这些文件查看角色、场景、章纲、细纲和剧本内容。",
            "如果有想调整的地方，直接告诉我你的建议；我会把反馈转成结构化修改任务交给 Architect 继续修订。你也可以查看后继续提建议。",
        ])
        return "\n".join(lines)

    def _execute_architect_dynamic_step(self, name: str, project_id: str, chapter_id: str, artifact_type: str, artifact: dict, policy_context: dict, metadata: dict) -> Optional[List[str]]:
        if artifact_type == "story_bible":
            brief_path = f"{project_id}/brief.md"
            direction_path = f"{project_id}/story_direction.md"
            output_path = artifact.get("path") or f"{project_id}/story_bible.md"
            self.policy.authorize_path(name, brief_path, "read", policy_context)
            self.policy.authorize_path(name, direction_path, "read", policy_context)
            self.policy.authorize_path(name, output_path, "write", policy_context)
            content = self._compose_story_bible(project_id, run_read(brief_path), run_read(direction_path), SkillLoader().load("story-planner"))
            run_write(output_path, content)
            return [output_path]

        if artifact_type in {"chapter_outlines", "chapter_outline"}:
            direction_path = f"{project_id}/story_direction.md"
            story_bible_path = f"{project_id}/story_bible.md"
            self.policy.authorize_path(name, direction_path, "read", policy_context)
            self.policy.authorize_path(name, story_bible_path, "read", policy_context)
            chapter_outlines, chapter_files = self._compose_chapter_outlines(project_id, run_read(direction_path), run_read(story_bible_path))
            if artifact_type == "chapter_outlines":
                output_path = artifact.get("path") or f"{project_id}/chapter_outlines.md"
                self.policy.authorize_path(name, output_path, "write", policy_context)
                run_write(output_path, chapter_outlines)
                return [output_path]
            output_path = artifact.get("path") or f"{project_id}/chapters/{chapter_id}/outline.md"
            content = chapter_files.get(output_path)
            if content is None:
                content = f"# {chapter_id} 细纲\n\n## 章节目标\n待根据用户事实源补齐。\n"
            self.policy.authorize_path(name, output_path, "write", policy_context)
            run_write(output_path, content)
            return [output_path]

        if artifact_type == "character_cards":
            story_bible_path = f"{project_id}/story_bible.md"
            outlines_path = f"{project_id}/chapter_outlines.md"
            self.policy.authorize_path(name, story_bible_path, "read", policy_context)
            self.policy.authorize_path(name, outlines_path, "read", policy_context)
            files = self._compose_character_files(project_id, run_read(story_bible_path), run_read(outlines_path), SkillLoader().load("character-builder"))
            for file_path, content in files.items():
                self.policy.authorize_path(name, file_path, "write", policy_context)
                run_write(file_path, content)
            return list(files.keys())

        if artifact_type == "environment_cards":
            story_bible_path = f"{project_id}/story_bible.md"
            outlines_path = f"{project_id}/chapter_outlines.md"
            self.policy.authorize_path(name, story_bible_path, "read", policy_context)
            self.policy.authorize_path(name, outlines_path, "read", policy_context)
            files = self._compose_environment_files(project_id, run_read(story_bible_path), run_read(outlines_path), SkillLoader().load("environment-builder"))
            for file_path, content in files.items():
                self.policy.authorize_path(name, file_path, "write", policy_context)
                run_write(file_path, content)
            return list(files.keys())

        if artifact_type == "chapter_script":
            output_path = artifact.get("path") or f"{project_id}/chapters/{chapter_id}/script.md"
            return [self._write_architect_dynamic_script(name, project_id, chapter_id, output_path, policy_context, metadata)]

        return None

    def _write_architect_dynamic_script(self, name: str, project_id: str, chapter_id: str, script_path: str, policy_context: dict, metadata: dict) -> str:
        missing = self._missing_architect_delivery_prerequisites(project_id, chapter_id)
        if missing:
            raise ValueError(f"动态计划缺少剧本前置设定：{missing}")
        architect_skill = SkillLoader().load("story-planner")
        chapter_skill = SkillLoader().load("chapter-expander")
        characters = comic_list_characters(project_id)
        environments = comic_list_environments(project_id)
        chapter_context = self._load_chapter_context(project_id, chapter_id)
        protagonist = self._select_contextual_item(project_id, characters, "characters", chapter_context, preferred_markers=["主角"])
        environment = self._select_contextual_item(project_id, environments, "environments", chapter_context, preferred_markers=["地点", "场景", "环境"])
        chapter_basis = self._summarize_chapter_basis(chapter_context)
        script_content = (
            f"# {chapter_id} 剧本\n\n"
            f"## 使用技能\n- story-planner\n- chapter-expander\n\n"
            f"## 技能依据摘要\n{architect_skill[:120]}\n\n"
            f"## 目标\n{metadata.get('goal', '')}\n\n"
            f"## 用户事实依据\n{chapter_basis}\n\n"
            f"## 场景 1\n{protagonist} 在 {environment} 根据上述用户事实依据展开本章事件。\n\n"
            f"## 角色动机\n{protagonist} 的行动必须继承用户事实依据中已明确的目标、线索与风险；缺失项标记为待补齐，不得由代码发明。\n\n"
            f"## 扩写约束\n{chapter_skill[:120]}\n"
        )
        self.policy.authorize_path(name, script_path, "write", policy_context)
        run_write(script_path, script_content)
        return script_path

    def _try_execute_protocol_task(self, name: str, task: dict) -> Optional[dict]:
        metadata = task.get("metadata", {})
        task_type = metadata.get("task_type")
        project_id = metadata.get("project_id")
        chapter_id = metadata.get("chapter_id")
        policy_context = {"project_id": project_id or "", "chapter_id": chapter_id or ""}
        if self._is_architect(name):
            self._debug_architect("protocol_enter", f"进入 _try_execute_protocol_task：task_type={task_type}, project_id={project_id}, chapter_id={chapter_id}", {"task_type": task_type, "project_id": project_id, "chapter_id": chapter_id})

        if task_type == "architect_concept_setup" and project_id:
            self._debug_architect("concept_setup_start", "Architect 故事概念阶段开始：读取 brief 与 story_direction", {"project_id": project_id})
            brief_path = f"{project_id}/brief.md"
            direction_path = f"{project_id}/story_direction.md"
            story_bible_path = f"{project_id}/story_bible.md"
            self.policy.authorize_path(name, brief_path, "read", policy_context)
            self.policy.authorize_path(name, direction_path, "read", policy_context)
            brief_text = run_read(brief_path)
            direction_text = run_read(direction_path)
            planner_skill = SkillLoader().load("story-planner")
            story_bible = self._compose_story_bible(project_id, brief_text, direction_text, planner_skill)
            self.policy.authorize_path(name, story_bible_path, "write", policy_context)
            run_write(story_bible_path, story_bible)
            self._emit_event(name, "story_bible_written", f"已写入故事圣经：{story_bible_path}", {"project_id": project_id, "file": story_bible_path})
            submission = {
                "type": "submission",
                "from_role": "architect",
                "project_id": project_id,
                "chapter_id": chapter_id or "",
                "task_type": task_type,
                "deliverables": [story_bible_path],
                "updated_files": [story_bible_path],
                "summary": "Architect 已完成故事世界观、风格与详细概念设定。",
                "next_recommended_task": "architect_chapter_outline_setup",
            }
            self.message_bus.send(name, "lead", submission)
            self._emit_event(name, "submission_sent", "已发送故事概念 submission", {"project_id": project_id, "task_type": task_type})
            return {"success": True, "protocol": task_type, "submission": submission, "deliverables": [story_bible_path]}

        if task_type == "architect_chapter_outline_setup" and project_id:
            self._debug_architect("chapter_outline_setup_start", "Architect 全章节细纲阶段开始：读取 story_bible 与 story_direction", {"project_id": project_id})
            direction_path = f"{project_id}/story_direction.md"
            story_bible_path = f"{project_id}/story_bible.md"
            outlines_path = f"{project_id}/chapter_outlines.md"
            self.policy.authorize_path(name, direction_path, "read", policy_context)
            self.policy.authorize_path(name, story_bible_path, "read", policy_context)
            direction_text = run_read(direction_path)
            story_bible_text = run_read(story_bible_path)
            chapter_outlines, chapter_files = self._compose_chapter_outlines(project_id, direction_text, story_bible_text)
            self.policy.authorize_path(name, outlines_path, "write", policy_context)
            run_write(outlines_path, chapter_outlines)
            for file_path, content in chapter_files.items():
                self.policy.authorize_path(name, file_path, "write", policy_context)
                run_write(file_path, content)
            deliverables = [outlines_path, *chapter_files.keys()]
            self._emit_event(name, "chapter_outlines_written", f"已写入全章节细纲：{outlines_path}", {"project_id": project_id, "files": deliverables})
            submission = {
                "type": "submission",
                "from_role": "architect",
                "project_id": project_id,
                "chapter_id": chapter_id or "",
                "task_type": task_type,
                "deliverables": deliverables,
                "updated_files": deliverables,
                "summary": "Architect 已完成全章节细纲，可进入角色与场景设计阶段。",
                "next_recommended_task": "architect_character_setup",
            }
            self.message_bus.send(name, "lead", submission)
            self._emit_event(name, "submission_sent", "已发送全章节细纲 submission", {"project_id": project_id, "task_type": task_type})
            return {"success": True, "protocol": task_type, "submission": submission, "deliverables": deliverables}

        if task_type == "architect_character_setup" and project_id:
            self._debug_architect("character_setup_start", "Architect 角色设定阶段开始：读取 story_bible 与 chapter_outlines", {"project_id": project_id})
            story_bible_path = f"{project_id}/story_bible.md"
            outlines_path = f"{project_id}/chapter_outlines.md"
            self.policy.authorize_path(name, story_bible_path, "read", policy_context)
            self.policy.authorize_path(name, outlines_path, "read", policy_context)
            story_bible_text = run_read(story_bible_path)
            outlines_text = run_read(outlines_path)
            character_skill = SkillLoader().load("character-builder")
            character_files = self._compose_character_files(project_id, story_bible_text, outlines_text, character_skill)
            for file_path, content in character_files.items():
                self.policy.authorize_path(name, file_path, "write", policy_context)
                run_write(file_path, content)
            deliverables = list(character_files.keys())
            self._emit_event(name, "characters_written", f"已写入角色设定：count={len(deliverables)}", {"project_id": project_id, "files": deliverables})
            submission = {
                "type": "submission",
                "from_role": "architect",
                "project_id": project_id,
                "chapter_id": chapter_id or "",
                "task_type": task_type,
                "deliverables": deliverables,
                "updated_files": deliverables,
                "summary": "Architect 已基于全章节细纲完成核心角色事实源。",
                "next_recommended_task": "architect_environment_setup",
            }
            self.message_bus.send(name, "lead", submission)
            self._emit_event(name, "submission_sent", "已发送角色设定 submission", {"project_id": project_id, "task_type": task_type})
            return {"success": True, "protocol": task_type, "submission": submission, "deliverables": deliverables}

        if task_type == "architect_environment_setup" and project_id:
            self._debug_architect("environment_setup_start", "Architect 场景设定阶段开始：读取 story_bible 与 chapter_outlines", {"project_id": project_id})
            story_bible_path = f"{project_id}/story_bible.md"
            outlines_path = f"{project_id}/chapter_outlines.md"
            self.policy.authorize_path(name, story_bible_path, "read", policy_context)
            self.policy.authorize_path(name, outlines_path, "read", policy_context)
            story_bible_text = run_read(story_bible_path)
            outlines_text = run_read(outlines_path)
            environment_skill = SkillLoader().load("environment-builder")
            environment_files = self._compose_environment_files(project_id, story_bible_text, outlines_text, environment_skill)
            for file_path, content in environment_files.items():
                self.policy.authorize_path(name, file_path, "write", policy_context)
                run_write(file_path, content)
            deliverables = list(environment_files.keys())
            self._emit_event(name, "environments_written", f"已写入场景设定：count={len(deliverables)}", {"project_id": project_id, "files": deliverables})
            submission = {
                "type": "submission",
                "from_role": "architect",
                "project_id": project_id,
                "chapter_id": chapter_id or "",
                "task_type": task_type,
                "deliverables": deliverables,
                "updated_files": deliverables,
                "summary": "Architect 已基于全章节细纲完成核心场景事实源。",
                "next_recommended_task": "architect_delivery",
            }
            self.message_bus.send(name, "lead", submission)
            self._emit_event(name, "submission_sent", "已发送场景设定 submission", {"project_id": project_id, "task_type": task_type})
            return {"success": True, "protocol": task_type, "submission": submission, "deliverables": deliverables}

        if task_type == "architect_delivery" and project_id and chapter_id:
            self._debug_architect("architect_delivery_start", "Architect 初稿协议开始：检查前置设定、加载技能与状态文件", {"project_id": project_id, "chapter_id": chapter_id})
            missing = self._missing_architect_delivery_prerequisites(project_id, chapter_id)
            if missing:
                error = {
                    "success": False,
                    "protocol": "architect_delivery",
                    "error_type": "missing_story_setup",
                    "error": "缺少正式剧本前置设定，已拒绝写入 script.md，避免生成占位稿。",
                    "missing": missing,
                    "required_order": [
                        "architect_concept_setup",
                        "architect_chapter_outline_setup",
                        "architect_character_setup",
                        "architect_environment_setup",
                        "architect_delivery",
                    ],
                }
                self._emit_event(
                    name,
                    "architect_delivery_blocked",
                    f"缺少前置设定，拒绝写入剧本：{missing}",
                    {"project_id": project_id, "chapter_id": chapter_id, "missing": missing},
                )
                return error

            architect_skill = SkillLoader().load("story-planner")
            self._debug_architect("skill_loaded", f"已加载 story-planner：chars={len(architect_skill)}")
            chapter_skill = SkillLoader().load("chapter-expander")
            self._debug_architect("skill_loaded", f"已加载 chapter-expander：chars={len(chapter_skill)}")
            characters = comic_list_characters(project_id)
            environments = comic_list_environments(project_id)
            self._debug_architect("state_loaded", f"状态读取完成：characters={characters}, environments={environments}", {"characters": characters, "environments": environments})
            chapter_context = self._load_chapter_context(project_id, chapter_id)
            protagonist = self._select_contextual_item(project_id, characters, "characters", chapter_context, preferred_markers=["主角"])
            environment = self._select_contextual_item(project_id, environments, "environments", chapter_context, preferred_markers=["地点", "场景", "环境"])
            script_path = f"{project_id}/chapters/{chapter_id}/script.md"
            chapter_basis = self._summarize_chapter_basis(chapter_context)
            script_content = (
                f"# {chapter_id} 剧本\n\n"
                f"## 使用技能\n- story-planner\n- chapter-expander\n\n"
                f"## 技能依据摘要\n{architect_skill[:120]}\n\n"
                f"## 目标\n{metadata.get('goal', '')}\n\n"
                f"## 用户事实依据\n{chapter_basis}\n\n"
                f"## 场景 1\n{protagonist} 在 {environment} 根据上述用户事实依据展开本章事件。\n\n"
                f"## 角色动机\n{protagonist} 的行动必须继承用户事实依据中已明确的目标、线索与风险；缺失项标记为待补齐，不得由代码发明。\n\n"
                f"## 扩写约束\n{chapter_skill[:120]}\n"
            )
            self._debug_architect("script_composed", f"剧本文本已组装：script_path={script_path}, bytes={len(script_content.encode('utf-8'))}, protagonist={protagonist}, environment={environment}", {"script_path": script_path, "protagonist": protagonist, "environment": environment})
            self._debug_architect("authorize_write_before", f"准备校验写权限：{script_path}", policy_context)
            self.policy.authorize_path(name, script_path, "write", policy_context)
            self._debug_architect("write_before", f"准备写入剧本文件：{script_path}")
            run_write(script_path, script_content)
            self._debug_architect("write_after", f"剧本文件写入完成：{script_path}")
            self._emit_event(name, "script_written", f"已写入剧本：{script_path}", {"project_id": project_id, "chapter_id": chapter_id, "file": script_path})
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
                "user_visible_summary": self._build_architect_user_visible_summary(project_id, chapter_id, [script_path]),
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
                "user_visible_summary": self._build_architect_user_visible_summary(project_id, chapter_id, [script_path]),
            }
            self.message_bus.send(name, "lead", handoff)
            self._emit_event(name, "handoff_sent", "已向 Lead 发送 Architect handoff", {"project_id": project_id, "chapter_id": chapter_id})
            self.message_bus.send(name, metadata.get("submission_target", metadata.get("qa_target", "qa_bot")), submission)
            self._emit_event(name, "submission_sent", "已发送 Architect submission", {"project_id": project_id, "chapter_id": chapter_id})
            return {"success": True, "protocol": "architect_delivery", "handoff": handoff, "submission": submission}

        if task_type == "architect_feedback_revision" and project_id and chapter_id:
            self._debug_architect("revision_start", "Architect 反馈修订协议开始：读取旧剧本并追加修订记录", {"project_id": project_id, "chapter_id": chapter_id})
            instruction = metadata.get("instruction") or {}
            script_path = f"{project_id}/chapters/{chapter_id}/script.md"
            old_script = ""
            try:
                self._debug_architect("revision_read_before", f"准备读取旧剧本：{script_path}", {"instruction": instruction})
                self.policy.authorize_path(name, script_path, "read", policy_context)
                old_script = run_read(script_path)
                self._debug_architect("revision_read_after", f"旧剧本读取完成：chars={len(old_script)}")
            except FileNotFoundError:
                old_script = f"# {chapter_id} 剧本\n\n"
                self._debug_architect("revision_read_missing", f"旧剧本不存在，使用空模板：{script_path}")

            revised_script = (
                old_script
                + "\n\n---\n\n"
                + "## Architect 修订记录\n"
                + f"- 修改目标: {instruction.get('target', '')}\n"
                + f"- 修改内容: {instruction.get('content', '')}\n"
                + f"- 约束: {', '.join(instruction.get('constraints', []))}\n"
            )
            self._debug_architect("revision_composed", f"修订文本已组装：old_chars={len(old_script)}, new_chars={len(revised_script)}", {"instruction": instruction})
            self._debug_architect("revision_authorize_write_before", f"准备校验修订写权限：{script_path}", policy_context)
            self.policy.authorize_path(name, script_path, "write", policy_context)
            self._debug_architect("revision_write_before", f"准备写入修订剧本：{script_path}")
            run_write(script_path, revised_script)
            self._debug_architect("revision_write_after", f"修订剧本写入完成：{script_path}")
            self._emit_event(name, "script_revised", f"已写入反馈修订：{script_path}", {"project_id": project_id, "chapter_id": chapter_id, "file": script_path})

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
            self._emit_event(name, "handoff_sent", "已向 Lead 发送 Architect 修订 handoff", {"project_id": project_id, "chapter_id": chapter_id, "revision": True})
            self.message_bus.send(name, metadata.get("submission_target", metadata.get("qa_target", "qa_bot")), submission)
            self._emit_event(name, "submission_sent", "已发送 Architect 修订 submission", {"project_id": project_id, "chapter_id": chapter_id, "revision": True})
            return {"success": True, "protocol": "architect_feedback_revision", "handoff": handoff, "submission": submission}

        if task_type in {"director_delivery", "director_feedback_revision"} and project_id and chapter_id:
            return self._execute_director_delivery_pipeline(name, task, metadata, project_id, chapter_id, policy_context)

        if task_type == "qa_review":
            return None

        return None

    def _execute_director_delivery_pipeline(self, name: str, task: dict, metadata: dict, project_id: str, chapter_id: str, policy_context: dict) -> dict:
        is_revision = bool(metadata.get("revision") or metadata.get("task_type") == "director_feedback_revision")
        input_paths = metadata.get("required_inputs", metadata.get("inputs", []))
        script_path = metadata.get("script_path") or (input_paths[0] if input_paths else f"{project_id}/chapters/{chapter_id}/script.md")
        storyboard_path = metadata.get("output_path") or f"{project_id}/chapters/{chapter_id}/storyboard.md"
        plan_dir = f"{project_id}/chapters/{chapter_id}/director_plan"
        selected_context_path = f"{plan_dir}/selected_context.json"
        storyboard_plan_path = f"{plan_dir}/storyboard_plan.json"
        pipeline_status_path = f"{plan_dir}/pipeline_status.json"
        status = {"project_id": project_id, "chapter_id": chapter_id, "current_stage": "queued", "stages": {}, "errors": []}
        warnings: List[str] = []
        try:
            self._director_write_status(name, pipeline_status_path, status, "context_planner", "running", policy_context)
            self.policy.authorize_path(name, script_path, "read", policy_context)
            script_text = run_read(script_path)
            selected_context, context_docs = self._director_build_selected_context(name, project_id, chapter_id, script_path, script_text, policy_context)
            warnings.extend(selected_context.get("warnings", []))
            self.policy.authorize_path(name, selected_context_path, "write", policy_context)
            run_write(selected_context_path, json.dumps(selected_context, ensure_ascii=False, indent=2))
            status["stages"]["context_planner"] = {"status": "completed", "output": selected_context_path, "warnings": selected_context.get("warnings", [])}

            self._director_write_status(name, pipeline_status_path, status, "storyboard_planner", "running", policy_context)
            storyboard_plan = self._director_build_storyboard_plan(chapter_id, script_text, selected_context, context_docs)
            warnings.extend(storyboard_plan.get("warnings", []))
            self.policy.authorize_path(name, storyboard_plan_path, "write", policy_context)
            run_write(storyboard_plan_path, json.dumps(storyboard_plan, ensure_ascii=False, indent=2))
            status["stages"]["storyboard_planner"] = {"status": "completed", "output": storyboard_plan_path, "warnings": storyboard_plan.get("warnings", [])}

            self._director_write_status(name, pipeline_status_path, status, "draft_writer", "running", policy_context)
            storyboard_content = self._director_build_storyboard_markdown(chapter_id, script_text, selected_context, storyboard_plan, context_docs, is_revision)
            self.policy.authorize_path(name, storyboard_path, "write", policy_context)
            run_write(storyboard_path, storyboard_content)
            status["stages"]["draft_writer"] = {"status": "completed", "output": storyboard_path, "warnings": []}
            status["current_stage"] = "submitted"
            self.policy.authorize_path(name, pipeline_status_path, "write", policy_context)
            run_write(pipeline_status_path, json.dumps(status, ensure_ascii=False, indent=2))
        except Exception as exc:
            status["current_stage"] = "error"
            status.setdefault("errors", []).append({"error_type": type(exc).__name__, "error": str(exc)})
            try:
                self.policy.authorize_path(name, pipeline_status_path, "write", policy_context)
                run_write(pipeline_status_path, json.dumps(status, ensure_ascii=False, indent=2))
            except Exception:
                pass
            raise

        summary = self._director_storyboard_summary(storyboard_plan, warnings)
        self._emit_event(name, "storyboard_written", f"已写入分镜：{storyboard_path}", {"project_id": project_id, "chapter_id": chapter_id, "file": storyboard_path, "revision": is_revision})
        submission = {
            "type": "submission",
            "from_role": "director",
            "task_type": "director_delivery",
            "project_id": project_id,
            "chapter_id": chapter_id,
            "goal": metadata.get("goal"),
            "deliverables": [storyboard_path],
            "updated_files": [storyboard_path, selected_context_path, storyboard_plan_path, pipeline_status_path],
            "depends_on_script": [script_path],
            "director_plan_files": {"selected_context": selected_context_path, "storyboard_plan": storyboard_plan_path, "pipeline_status": pipeline_status_path},
            "input_files_used": {"script": script_path, "characters": selected_context.get("final_selected_files", {}).get("characters", []), "environments": selected_context.get("final_selected_files", {}).get("environments", [])},
            "storyboard_summary": summary,
            "self_check": ["已生成 selected_context.json", "已生成 storyboard_plan.json", "已生成 storyboard.md"],
            "quality_bar": metadata.get("quality_bar", []),
            "revision": is_revision,
            "diff_summary": "基于反馈修订后的剧本，已刷新分镜规划与镜头依据。" if is_revision else "初版分镜交付。",
            "summary": "Director 已完成三阶段分镜 pipeline。",
            "user_visible_summary": f"Director 已完成 {chapter_id} 分镜草稿：`{storyboard_path}`。规划摘要：共 {summary.get('pages')} 页、{summary.get('panels')} 格。",
            "requires_user_review": True,
        }
        self.message_bus.send(name, metadata.get("submission_target", metadata.get("qa_target", "qa_bot")), submission)
        self._emit_event(name, "submission_sent", "已发送 Director submission", {"project_id": project_id, "chapter_id": chapter_id, "revision": is_revision})
        return {"success": True, "protocol": "director_delivery_pipeline", "submission": submission, "deliverables": [storyboard_path]}

    def _director_write_status(self, name: str, path: str, status: Dict[str, Any], stage: str, stage_status: str, policy_context: dict):
        status["current_stage"] = stage
        status.setdefault("stages", {})[stage] = {"status": stage_status, "warnings": []}
        self.policy.authorize_path(name, path, "write", policy_context)
        run_write(path, json.dumps(status, ensure_ascii=False, indent=2))
        self._emit_event(name, f"{stage}_{stage_status}", f"Director pipeline 阶段：{stage_status}", {"stage": stage, "file": path})

    def _director_run_json_skill(self, skill_name: str, task_description: str, payload: Dict[str, Any], schema_hint: Dict[str, Any], retries: int = 1) -> Optional[Dict[str, Any]]:
        if self.client is None:
            return None
        try:
            skill_text = SkillLoader().load(skill_name)
        except Exception as exc:
            self._emit_event("director_bot", "director_skill_load_failed", f"加载 Director skill 失败：{skill_name}", {"error": str(exc)})
            return None
        prompt = "\n\n".join([
            skill_text,
            f"## 任务\n{task_description}",
            f"## payload\n{json.dumps(payload, ensure_ascii=False)}",
            f"## 目标输出 Schema 示例\n{json.dumps(schema_hint, ensure_ascii=False)}",
            "只输出 JSON 对象本身，不要 Markdown，不要解释。",
        ])
        repair_note = ""
        for attempt in range(retries + 1):
            try:
                response = self.client.chat.completions.create(
                    model=self.config["model"],
                    messages=[{"role": "user", "content": prompt + repair_note}],
                )
                text = (response.choices[0].message.content or "").strip()
                parsed = self._extract_json_object(text)
                if parsed is not None:
                    return parsed
                repair_note = "\n\n上次输出不是合法 JSON，请修复并只输出 JSON。"
            except Exception as exc:
                self._emit_event("director_bot", "director_json_skill_failed", f"Director JSON skill 调用失败：{skill_name}", {"attempt": attempt + 1, "error": str(exc)})
                repair_note = f"\n\n上次调用异常：{type(exc).__name__}: {exc}。请只输出 JSON。"
        return None

    def _director_run_markdown_skill(self, skill_name: str, task_description: str, payload: Dict[str, Any]) -> Optional[str]:
        if self.client is None:
            return None
        try:
            skill_text = SkillLoader().load(skill_name)
        except Exception as exc:
            self._emit_event("director_bot", "director_skill_load_failed", f"加载 Director skill 失败：{skill_name}", {"error": str(exc)})
            return None
        prompt = "\n\n".join([
            skill_text,
            f"## 任务\n{task_description}",
            f"## payload\n{json.dumps(payload, ensure_ascii=False)}",
            "输出完整 Markdown 分镜脚本，不要包裹代码块。",
        ])
        try:
            response = self.client.chat.completions.create(
                model=self.config["model"],
                messages=[{"role": "user", "content": prompt}],
            )
            text = (response.choices[0].message.content or "").strip()
            return text if text.startswith("#") and "格" in text else None
        except Exception as exc:
            self._emit_event("director_bot", "director_markdown_skill_failed", f"Director Markdown skill 调用失败：{skill_name}", {"error": str(exc)})
            return None

    @staticmethod
    def _extract_json_object(text: str) -> Optional[Dict[str, Any]]:
        raw = text.strip()
        if raw.startswith("```json"):
            raw = raw.split("```json", 1)[1].split("```", 1)[0].strip()
        elif raw.startswith("```"):
            raw = raw.split("```", 1)[1].split("```", 1)[0].strip()
        try:
            data = json.loads(raw)
            return data if isinstance(data, dict) else None
        except Exception:
            return None

    def _director_build_selected_context(self, name: str, project_id: str, chapter_id: str, script_path: str, script_text: str, policy_context: dict) -> tuple[Dict[str, Any], Dict[str, str]]:
        character_files = self._director_list_context_files(project_id, "characters")
        environment_files = self._director_list_context_files(project_id, "environments")
        model_context = self._director_model_select_context(project_id, chapter_id, script_path, script_text, character_files, environment_files)
        if model_context is not None:
            selected_files = model_context.get("final_selected_files", {}).get("characters", []) + model_context.get("final_selected_files", {}).get("environments", [])
            return model_context, self._director_read_context_docs(name, selected_files, policy_context)
        initial_characters = self._director_select_files_by_script(script_text, character_files, limit=4)
        initial_environments = self._director_select_files_by_script(script_text, environment_files, limit=4)
        if not initial_characters and character_files:
            initial_characters = character_files[:1]
        if not initial_environments and environment_files:
            initial_environments = environment_files[:1]
        selected = initial_characters + initial_environments
        remaining = [p for p in character_files + environment_files if p not in selected]
        additional = self._director_select_files_by_script(script_text, remaining, limit=6)
        context_docs = self._director_read_context_docs(name, selected + additional, policy_context)
        missing_notes: List[str] = []
        warnings: List[str] = []
        if not character_files:
            missing_notes.append("未找到角色卡文件。")
        if not environment_files:
            missing_notes.append("未找到场景卡文件。")
        if not selected and not additional:
            warnings.append("未能根据剧本选择到上下文文件，将仅依据 script.md 生成基础分镜。")
        return {
            "project_id": project_id,
            "chapter_id": chapter_id,
            "script_path": script_path,
            "initial_selected_files": {"characters": initial_characters, "environments": initial_environments},
            "additional_selected_files": {"characters": [p for p in additional if "/state/characters/" in p], "environments": [p for p in additional if "/state/environments/" in p]},
            "final_selected_files": {"characters": [p for p in selected + additional if "/state/characters/" in p], "environments": [p for p in selected + additional if "/state/environments/" in p]},
            "missing_context_notes": missing_notes,
            "warnings": warnings,
            "selection_rounds": [
                {"round": 1, "reason": "根据剧本中出现的文件名/实体名选择初始上下文。"},
                {"round": 2, "reason": "有边界二次读取：补充仍在剧本中出现但未读取的少量文件。"} if additional else {"round": 2, "reason": "未发现需要二次读取的上下文。"},
            ],
            "skills_used": ["director-context-planner"],
        }, context_docs

    def _director_model_select_context(self, project_id: str, chapter_id: str, script_path: str, script_text: str, character_files: List[str], environment_files: List[str]) -> Optional[Dict[str, Any]]:
        schema_hint = {
            "initial_selected_files": {"characters": [], "environments": []},
            "additional_selected_files": {"characters": [], "environments": []},
            "final_selected_files": {"characters": [], "environments": []},
            "missing_context_notes": [],
            "warnings": [],
            "selection_rounds": [],
        }
        data = self._director_run_json_skill(
            "director-context-planner",
            "请为当前章节分镜选择必要角色/场景上下文。只能从 available 文件列表中选择，最多一轮二次读取。",
            {
                "project_id": project_id,
                "chapter_id": chapter_id,
                "script_path": script_path,
                "script_excerpt": script_text[:6000],
                "available_character_files": character_files,
                "available_environment_files": environment_files,
            },
            schema_hint,
        )
        if data is None:
            return None
        available_characters = set(character_files)
        available_environments = set(environment_files)
        initial = data.get("initial_selected_files", {}) if isinstance(data.get("initial_selected_files"), dict) else {}
        additional = data.get("additional_selected_files", {}) if isinstance(data.get("additional_selected_files"), dict) else {}
        initial_characters = [p for p in initial.get("characters", []) if p in available_characters][:4]
        initial_environments = [p for p in initial.get("environments", []) if p in available_environments][:4]
        additional_characters = [p for p in additional.get("characters", []) if p in available_characters and p not in initial_characters][:3]
        additional_environments = [p for p in additional.get("environments", []) if p in available_environments and p not in initial_environments][:3]
        warnings = data.get("warnings", []) if isinstance(data.get("warnings"), list) else []
        if initial_characters != initial.get("characters", []) or initial_environments != initial.get("environments", []):
            warnings.append("模型选择中包含非法或不可用路径，已由代码过滤。")
        return {
            "project_id": project_id,
            "chapter_id": chapter_id,
            "script_path": script_path,
            "initial_selected_files": {"characters": initial_characters, "environments": initial_environments},
            "additional_selected_files": {"characters": additional_characters, "environments": additional_environments},
            "final_selected_files": {"characters": initial_characters + additional_characters, "environments": initial_environments + additional_environments},
            "missing_context_notes": data.get("missing_context_notes", []) if isinstance(data.get("missing_context_notes"), list) else [],
            "warnings": warnings,
            "selection_rounds": data.get("selection_rounds", []) if isinstance(data.get("selection_rounds"), list) else [],
            "skills_used": ["director-context-planner"],
            "planner_mode": "model_first",
        }

    def _director_list_context_files(self, project_id: str, collection: str) -> List[str]:
        base = safe_path(f"{project_id}/state/{collection}")
        if not base.exists():
            return []
        return sorted([f"{project_id}/state/{collection}/{path.name}" for path in base.glob("*.md") if path.is_file()])

    @staticmethod
    def _director_select_files_by_script(script_text: str, files: List[str], limit: int) -> List[str]:
        scored: List[tuple[int, str]] = []
        for file_path in files:
            name = file_path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
            score = script_text.count(name) * 10
            for token in re.split(r"[-_\s]", name):
                if token and token in script_text:
                    score += 2
            if score > 0:
                scored.append((score, file_path))
        scored.sort(key=lambda item: (-item[0], files.index(item[1])))
        return [path for _, path in scored[:limit]]

    def _director_read_context_docs(self, name: str, files: List[str], policy_context: dict) -> Dict[str, str]:
        docs: Dict[str, str] = {}
        for file_path in files:
            try:
                self.policy.authorize_path(name, file_path, "read", policy_context)
                docs[file_path] = run_read(file_path)
            except Exception as exc:
                docs[file_path] = f"[读取失败] {type(exc).__name__}: {exc}"
        return docs

    def _director_build_storyboard_plan(self, chapter_id: str, script_text: str, selected_context: Dict[str, Any], context_docs: Dict[str, str]) -> Dict[str, Any]:
        model_plan = self._director_model_storyboard_plan(chapter_id, script_text, selected_context, context_docs)
        if model_plan is not None:
            return model_plan
        beats = self._director_fallback_beats(script_text)
        page_plan, layout_plan, tension_strategy = self._director_fallback_layout(beats)
        warnings = list(selected_context.get("warnings", []))
        warnings.extend(selected_context.get("missing_context_notes", []))
        return {
            "chapter_id": chapter_id,
            "recommended_page_count": len(page_plan),
            "total_panel_count": sum(page.get("panel_count", 0) for page in page_plan),
            "beats": beats,
            "page_plan": page_plan,
            "layout_plan": layout_plan,
            "tension_strategy": tension_strategy,
            "warnings": warnings,
            "skills_used": ["director-storyboard-planner", "panel-director"],
            "planner_mode": "deterministic_fallback_mvp",
        }

    def _director_model_storyboard_plan(self, chapter_id: str, script_text: str, selected_context: Dict[str, Any], context_docs: Dict[str, str]) -> Optional[Dict[str, Any]]:
        schema_hint = {
            "chapter_id": chapter_id,
            "recommended_page_count": 1,
            "total_panel_count": 4,
            "beats": [],
            "page_plan": [],
            "layout_plan": [],
            "tension_strategy": [],
            "warnings": [],
        }
        data = self._director_run_json_skill(
            "director-storyboard-planner",
            "请综合规划当前章节分镜，输出 beats、page_plan、layout_plan、tension_strategy。不要改写剧本事实。",
            {
                "chapter_id": chapter_id,
                "script_excerpt": script_text[:9000],
                "selected_context": selected_context,
                "context_docs_excerpt": {path: text[:1200] for path, text in context_docs.items()},
                "panel_director_skill_excerpt": SkillLoader().load("panel-director")[:2000],
            },
            schema_hint,
        )
        if data is None:
            return None
        required_lists = ["beats", "page_plan", "layout_plan", "tension_strategy"]
        if any(not isinstance(data.get(key), list) or not data.get(key) for key in required_lists):
            return None
        data.setdefault("chapter_id", chapter_id)
        data["recommended_page_count"] = int(data.get("recommended_page_count") or len(data.get("page_plan", [])) or 1)
        data["total_panel_count"] = int(data.get("total_panel_count") or sum(len(page.get("panels", [])) for page in data.get("layout_plan", [])))
        data.setdefault("warnings", [])
        data.setdefault("skills_used", ["director-storyboard-planner", "panel-director"])
        data["planner_mode"] = "model_first"
        return data

    @staticmethod
    def _director_fallback_beats(script_text: str) -> List[Dict[str, Any]]:
        chunks = [chunk.strip() for chunk in re.split(r"\n(?=#{1,3}\s|场景\s*\d+|第\s*\d+\s*场)", script_text) if chunk.strip()]
        if not chunks:
            chunks = [script_text.strip() or "剧本内容为空，需补充。"]
        beats = []
        for index, chunk in enumerate(chunks[:24], 1):
            tension = 3
            if any(word in chunk for word in ["突然", "冲突", "追", "爆", "危机", "真相", "高潮", "发现"]):
                tension = 5
            if any(word in chunk for word in ["安静", "沉默", "孤独", "等待"]):
                tension = 2
            beats.append({
                "beat_id": f"b{index:02d}",
                "summary": TeammateManager._compact_text(chunk, 80),
                "source_script_range": f"chunk_{index}",
                "story_function": "climax" if tension >= 5 else "setup" if index == 1 else "progression",
                "emotional_tension": tension,
                "visual_importance": min(5, max(2, tension)),
                "must_show": [],
            })
        return beats

    @staticmethod
    def _director_fallback_layout(beats: List[Dict[str, Any]]) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
        page_plan: List[Dict[str, Any]] = []
        layout_plan: List[Dict[str, Any]] = []
        tension_strategy: List[Dict[str, Any]] = []
        for page_index in range(0, len(beats), 4):
            page_no = page_index // 4 + 1
            page_beats = beats[page_index:page_index + 4]
            high_tension = any(beat.get("emotional_tension", 0) >= 5 for beat in page_beats)
            page_plan.append({"page": page_no, "beats": [beat["beat_id"] for beat in page_beats], "page_function": "climax" if high_tension else "story_progression", "emotional_goal": "强化冲突与视觉重点" if high_tension else "推进剧情并保持阅读清晰", "panel_count": len(page_beats), "density": "medium", "page_turn_hook": "下一页继续推进关键情节" if page_no * 4 < len(beats) else ""})
            panels = []
            for order, beat in enumerate(page_beats, 1):
                size = "large" if beat.get("emotional_tension", 0) >= 5 else "wide_large" if order == 1 else "medium"
                panels.append({"panel_id": f"p{page_no:02d}_{order:02d}", "beat_id": beat["beat_id"], "size": size, "relative_area": 0.4 if size in {"large", "wide_large"} else 0.2, "shot_type": "远景" if order == 1 else "中景" if order % 2 == 0 else "近景", "purpose": beat.get("summary", "推进剧情"), "reading_order": order})
                tension_strategy.append({"target": beat["beat_id"], "tension_level": beat.get("emotional_tension", 3), "pacing": "fast" if beat.get("emotional_tension", 0) >= 5 else "normal", "layout_advice": "使用较大格强调转折。" if beat.get("emotional_tension", 0) >= 5 else "保持清晰阅读顺序。"})
            layout_plan.append({"page": page_no, "panels": panels})
        return page_plan, layout_plan, tension_strategy

    def _director_build_storyboard_markdown(self, chapter_id: str, script_text: str, selected_context: Dict[str, Any], storyboard_plan: Dict[str, Any], context_docs: Dict[str, str], is_revision: bool) -> str:
        model_draft = self._director_model_storyboard_markdown(chapter_id, script_text, selected_context, storyboard_plan, context_docs, is_revision)
        if model_draft is not None:
            return model_draft
        lines = [
            f"# {chapter_id} 分镜脚本",
            "",
            "## 使用技能",
            "- director-context-planner",
            "- director-storyboard-planner",
            "- storyboard-draft-writer",
            "- panel-director",
            "- reaction-polisher",
            "",
            "## 分镜规划摘要",
            f"- 页数：{storyboard_plan.get('recommended_page_count', 0)}",
            f"- 总格数：{storyboard_plan.get('total_panel_count', 0)}",
            f"- 规划模式：{storyboard_plan.get('planner_mode', 'unknown')}",
        ]
        if storyboard_plan.get("warnings"):
            lines.extend(["", "## 注意事项"] + [f"- {warning}" for warning in storyboard_plan.get("warnings", [])])
        if is_revision:
            lines.extend(["", "## 修订说明", "- 本次为反馈修订后的二次分镜交付。"])
        beats_by_id = {beat.get("beat_id"): beat for beat in storyboard_plan.get("beats", [])}
        for page in storyboard_plan.get("layout_plan", []):
            page_no = page.get("page")
            page_meta = next((item for item in storyboard_plan.get("page_plan", []) if item.get("page") == page_no), {})
            lines.extend(["", f"## 第 {page_no} 页", f"- 本页功能：{page_meta.get('page_function', '')}", f"- 情绪目标：{page_meta.get('emotional_goal', '')}", f"- 格子数：{len(page.get('panels', []))}"])
            for panel in page.get("panels", []):
                beat = beats_by_id.get(panel.get("beat_id"), {})
                lines.extend([
                    "",
                    f"### 格 {panel.get('reading_order')}",
                    f"- 大小：{panel.get('size')}",
                    f"- 景别：{panel.get('shot_type')}",
                    "- 机位：根据画面重点选择平视、俯视或近距离特写。",
                    f"- 画面：{beat.get('summary') or panel.get('purpose')}",
                    "- 人物：参考已选角色卡；首次出场需体现视觉锚点。",
                    f"- 视觉重点：{panel.get('purpose')}",
                    "- 对白/旁白：依据 script.md 对应段落提取或概括。",
                    "- 音效：按动作和氛围需要添加。",
                ])
        lines.extend(["", "## 剧本依据摘录", self._compact_text(script_text, 800), "", "## 已读取上下文文件"])
        for file_path in selected_context.get("final_selected_files", {}).get("characters", []) + selected_context.get("final_selected_files", {}).get("environments", []):
            lines.append(f"- {file_path}")
        return "\n".join(lines) + "\n"

    def _director_model_storyboard_markdown(self, chapter_id: str, script_text: str, selected_context: Dict[str, Any], storyboard_plan: Dict[str, Any], context_docs: Dict[str, str], is_revision: bool) -> Optional[str]:
        text = self._director_run_markdown_skill(
            "storyboard-draft-writer",
            "请根据 script、selected_context 和 storyboard_plan 生成完整 storyboard.md。必须遵循计划，不得改写剧本事实。",
            {
                "chapter_id": chapter_id,
                "script_excerpt": script_text[:9000],
                "selected_context": selected_context,
                "storyboard_plan": storyboard_plan,
                "context_docs_excerpt": {path: value[:1200] for path, value in context_docs.items()},
                "reaction_polisher_skill_excerpt": SkillLoader().load("reaction-polisher")[:1200],
                "revision": is_revision,
            },
        )
        if text is None:
            return None
        if "## 分镜规划摘要" not in text:
            text = text.replace("\n", "\n", 1) + "\n"
            text = f"# {chapter_id} 分镜脚本\n\n## 分镜规划摘要\n- 由 storyboard-draft-writer 模型生成。\n\n" + text.lstrip("# ")
        return text.rstrip() + "\n"

    @staticmethod
    def _director_storyboard_summary(storyboard_plan: Dict[str, Any], warnings: List[str]) -> Dict[str, Any]:
        large_pages: List[int] = []
        splash_pages: List[int] = []
        for page in storyboard_plan.get("layout_plan", []):
            for panel in page.get("panels", []):
                if panel.get("size") in {"large", "wide_large"} and page.get("page"):
                    large_pages.append(page.get("page"))
                if panel.get("size") == "splash" and page.get("page"):
                    splash_pages.append(page.get("page"))
        pages = storyboard_plan.get("recommended_page_count", 0)
        panels = storyboard_plan.get("total_panel_count", 0)
        return {"pages": pages, "panels": panels, "average_panels_per_page": round(panels / max(1, pages), 2), "large_panel_pages": sorted(set(large_pages)), "splash_pages": sorted(set(splash_pages)), "warnings": warnings}

    @staticmethod
    def _compact_text(text: str, limit: int) -> str:
        cleaned = re.sub(r"\s+", " ", text).strip()
        return cleaned[:limit] + ("..." if len(cleaned) > limit else "")

    @staticmethod
    def _prefer_named_item(items: List[str], preferred_names: List[str]) -> str:
        if not items:
            return preferred_names[0] if preferred_names else ""
        for preferred in preferred_names:
            if preferred in items:
                return preferred
        for preferred in preferred_names:
            for item in items:
                if preferred in item or item in preferred:
                    return item
        return items[0]

    def _load_chapter_context(self, project_id: str, chapter_id: str) -> str:
        parts: List[str] = []
        for file_path in [
            f"{project_id}/chapters/{chapter_id}/outline.md",
            f"{project_id}/chapter_outlines.md",
            f"{project_id}/story_bible.md",
            f"{project_id}/story_direction.md",
        ]:
            try:
                parts.append(run_read(file_path))
            except FileNotFoundError:
                continue
        return "\n\n".join(parts)

    def _select_contextual_item(self, project_id: str, items: List[str], item_kind: str, chapter_context: str, preferred_markers: Optional[List[str]] = None) -> str:
        if not items:
            return ""

        preferred_markers = preferred_markers or []
        explicit_names = self._derive_generic_entity_names(chapter_context, preferred_markers, "") if preferred_markers else []
        for explicit_name in explicit_names:
            if explicit_name in items:
                return explicit_name

        scored: List[tuple[int, str]] = []
        for item in items:
            score = chapter_context.count(item) * 10
            file_path = f"{project_id}/state/{item_kind}/{item}.md"
            try:
                card = run_read(file_path)
            except FileNotFoundError:
                card = ""
            if "主角" in card or "角色定位\n主角" in card:
                score += 5
            if "ch01" in card or "登场章节" in card and "ch01" in card:
                score += 3
            if item and item in card:
                score += 1
            scored.append((score, item))

        scored.sort(key=lambda pair: (-pair[0], items.index(pair[1])))
        return scored[0][1]

    @staticmethod
    def _summarize_chapter_basis(chapter_context: str, max_lines: int = 10) -> str:
        lines: List[str] = []
        for raw_line in chapter_context.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("```"):
                continue
            if line.startswith("#") or line.startswith("{") or line.startswith("}") or line.startswith('"'):
                continue
            if line.startswith("-") and any(marker in line for marker in ["input_type", "confidence", "key_info", "suggestion"]):
                continue
            if "基于本方向生成或补齐" in line or "story_bible.md" in line:
                continue
            if line.startswith("## 技能依据摘要"):
                break
            if line.startswith("#") or "待" in line and len(line) < 40:
                continue
            lines.append(line)
            if len(lines) >= max_lines:
                break
        if not lines:
            return "- 待补齐：当前章节缺少可执行的用户事实依据。"
        return "\n".join(f"- {line}" for line in lines)

    def _missing_architect_delivery_prerequisites(self, project_id: str, chapter_id: str) -> List[str]:
        missing: List[str] = []
        required_files = [
            f"{project_id}/story_bible.md",
            f"{project_id}/chapter_outlines.md",
            f"{project_id}/chapters/{chapter_id}/outline.md",
        ]
        for file_path in required_files:
            try:
                if not run_read(file_path).strip():
                    missing.append(file_path)
            except FileNotFoundError:
                missing.append(file_path)

        if not comic_list_characters(project_id):
            missing.append(f"{project_id}/state/characters/*.md")
        if not comic_list_environments(project_id):
            missing.append(f"{project_id}/state/environments/*.md")
        return missing

    @staticmethod
    def _compose_story_bible(project_id: str, brief_text: str, direction_text: str, planner_skill: str) -> str:
        brief = brief_text.strip() or "（未提供项目简介）"
        direction = direction_text.strip() or "（未提供创作方向）"
        return (
            f"# Story Bible: {project_id}\n\n"
            "## 使用技能\n- story-planner\n\n"
            f"## 项目简介\n{brief}\n\n"
            f"## 创作方向依据\n{direction}\n\n"
            "## 核心卖点\n"
            "- 待从用户输入中提炼，不在代码中预设具体故事卖点。\n\n"
            "## 世界观\n"
            "- 待从用户输入、已有设定文件或后续创作模型输出中补齐。\n\n"
            "## 故事风格\n"
            "- 待根据用户提供的题材、基调、受众和叙事偏好归纳。\n\n"
            "## 主线冲突\n"
            "- 待根据用户输入确认主角目标、阻力来源、风险和最终抉择。\n\n"
            "## 主题表达\n"
            "- 待根据用户输入确认，不在运行时代码中预设主题。\n\n"
            "## 视觉风格\n"
            "- 待根据用户输入确认关键视觉锚点、色彩、时代感和可重复元素。\n\n"
            "## 后续约束\n"
            "- 全章节细纲必须继承用户输入和本文件中已确认的事实。\n"
            "- 角色与场景设计必须服务章节功能，不先孤立堆设定。\n"
            "- 正式剧本必须读取角色和场景事实源后再生成。\n"
            "- 不得在代码层补写具体剧情、角色名、地点名或世界观事实。\n\n"
            "## 技能依据摘要\n"
            f"{planner_skill[:500]}\n"
        )

    @staticmethod
    def _compose_chapter_outlines(project_id: str, direction_text: str, story_bible_text: str) -> tuple[str, Dict[str, str]]:
        source = direction_text.strip() or story_bible_text.strip() or "（缺少用户创作方向，需先补齐）"
        chapter_count = TeammateManager._infer_chapter_count(source)
        chapter_notes = TeammateManager._extract_chapter_notes(source, chapter_count)
        lines = [
            f"# 全章节细纲: {project_id}",
            "",
            "## 输入依据",
            "### 创作方向摘要",
            direction_text[:1200].strip(),
            "",
            "### Story Bible 摘要",
            story_bible_text[:1200].strip(),
            "",
            "## 章节列表",
        ]
        chapter_files: Dict[str, str] = {}
        for index in range(1, chapter_count + 1):
            chapter_id = f"ch{index:02d}"
            note = chapter_notes.get(chapter_id, "待根据用户事实源补齐本章事件。")
            title = TeammateManager._derive_chapter_title_from_note(chapter_id, note)
            goal = f"继承用户给定的 {chapter_id} 事实：{note}"
            lines.extend([
                "",
                f"## {chapter_id} {title}",
                f"- 章节目标：{goal}",
                "- 核心冲突：待从用户输入或后续模型输出中确认本章冲突。",
                "- 结尾钩子：待从用户输入或后续模型输出中确认本章结尾钩子。",
                "- 主要角色：待 character_setup 根据用户事实源生成或确认。",
                "- 主要场景：待 environment_setup 根据用户事实源生成或确认。",
            ])
            outline = (
                f"# {chapter_id} 细纲：{title}\n\n"
                f"## 章节目标\n{goal}\n\n"
                "## 核心冲突\n待从用户输入或后续模型输出中确认本章冲突。\n\n"
                "## 结尾钩子\n待从用户输入或后续模型输出中确认本章结尾钩子。\n\n"
                "## 用户依据摘录\n"
                f"{note}\n\n"
                "## 后续设计需求\n"
                "- character_setup 需要为本章确认登场角色、动机与成长变化。\n"
                "- environment_setup 需要为本章确认地点、道具与视觉锚点。\n"
                "- 缺失信息必须标记为待补齐，不得由代码发明。\n"
            )
            chapter_files[f"{project_id}/chapters/{chapter_id}/outline.md"] = outline
        lines.extend([
            "",
            "## 后续约束",
            "- 先基于本细纲生成角色设定和场景设定。",
            "- 正式 script.md 必须对应本章 outline.md，不得跳过章节目标。",
            "- 不得使用代码内置故事情节填充空白。",
            "",
        ])
        return "\n".join(lines), chapter_files

    @staticmethod
    def _infer_chapter_count(source_text: str) -> int:
        matches = [int(m.group(1)) for m in re.finditer(r"(\d{1,2})\s*章", source_text)]
        ch_ids = [int(m.group(1)) for m in re.finditer(r"ch(\d{1,2})", source_text, flags=re.IGNORECASE)]
        count = max(matches + ch_ids + [1])
        return max(1, min(count, 30))

    @staticmethod
    def _extract_chapter_notes(source_text: str, chapter_count: int) -> Dict[str, str]:
        notes: Dict[str, str] = {}
        matches = list(re.finditer(r"ch(\d{1,2})", source_text, flags=re.IGNORECASE))
        for idx, match in enumerate(matches):
            number = int(match.group(1))
            if number < 1 or number > chapter_count:
                continue
            start = match.start()
            end = matches[idx + 1].start() if idx + 1 < len(matches) else len(source_text)
            chunk = source_text[start:end].strip(" 。；;，,\n\t")
            notes[f"ch{number:02d}"] = chunk or "待根据用户事实源补齐本章事件。"
        if notes:
            return notes
        default_note = TeammateManager._extract_user_direction_line(source_text)
        return {f"ch{index:02d}": default_note for index in range(1, chapter_count + 1)}

    @staticmethod
    def _extract_user_direction_line(source_text: str) -> str:
        for line in source_text.splitlines():
            cleaned = line.strip()
            if cleaned and not cleaned.startswith("#") and not cleaned.startswith("**"):
                return cleaned
        return source_text[:500].strip() or "待根据用户事实源补齐。"

    @staticmethod
    def _derive_chapter_title_from_note(chapter_id: str, note: str) -> str:
        cleaned = re.sub(r"^ch\d{1,2}\s*", "", note.strip(), flags=re.IGNORECASE)
        cleaned = cleaned.strip("：: -，,。；;")
        return cleaned[:24] or f"{chapter_id} 用户章节"

    @staticmethod
    def _derive_generic_chapter_title(source_text: str) -> str:
        for line in source_text.splitlines():
            cleaned = line.strip().strip("#：: -")
            if 2 <= len(cleaned) <= 30 and not cleaned.startswith("项目 ID") and not cleaned.startswith("生成时间"):
                return cleaned
        return "用户方向整理"

    @staticmethod
    def _compose_character_files(project_id: str, story_bible_text: str, outlines_text: str, character_skill: str) -> Dict[str, str]:
        skill_summary = character_skill[:500]
        shared_basis = (
            "## 设定依据\n"
            f"- Story Bible 摘要：{story_bible_text[:500].strip()}\n"
            f"- 全章节细纲摘要：{outlines_text[:500].strip()}\n\n"
            "## 技能依据摘要\n"
            f"{skill_summary}\n"
        )
        source = story_bible_text + "\n" + outlines_text
        names = TeammateManager._derive_generic_entity_names(source, ["主角", "对手", "反派", "配角", "角色", "人物"], "主角")
        files: Dict[str, str] = {}
        chapter_count = TeammateManager._infer_chapter_count(source)
        chapter_lines = "\n".join(f"- ch{index:02d}" for index in range(1, chapter_count + 1))
        for name in names:
            content = (
                f"# {name}\n\n"
                "## Level\nLevel 1\n\n"
                "## 角色定位\n待根据用户输入和章节功能确认。\n\n"
                "## 外观锚点\n- 待补齐。\n\n"
                "## 性格与动机\n- 待补齐。\n\n"
                "## 角色弧光\n待补齐。\n\n"
                f"## 登场章节\n{chapter_lines}\n\n"
                "## 禁止改动\n- 不得覆盖用户已明确给出的角色事实。\n- 不得在代码层发明具体人物经历或视觉锚点。\n\n"
                f"{shared_basis}\n"
            )
            files[f"{project_id}/state/characters/{name}.md"] = content
        return files

    @staticmethod
    def _compose_environment_files(project_id: str, story_bible_text: str, outlines_text: str, environment_skill: str) -> Dict[str, str]:
        skill_summary = environment_skill[:500]
        shared_basis = (
            "## 设定依据\n"
            f"- Story Bible 摘要：{story_bible_text[:500].strip()}\n"
            f"- 全章节细纲摘要：{outlines_text[:500].strip()}\n\n"
            "## 技能依据摘要\n"
            f"{skill_summary}\n"
        )
        source = story_bible_text + "\n" + outlines_text
        names = TeammateManager._derive_generic_entity_names(source, ["地点", "场景", "环境"], "主要场景")
        files: Dict[str, str] = {}
        chapter_count = TeammateManager._infer_chapter_count(source)
        chapter_lines = "\n".join(f"- ch{index:02d}" for index in range(1, chapter_count + 1))
        for name in names:
            content = (
                f"# {name}\n\n"
                "## Level\nLevel 1\n\n"
                "## 空间定位\n待根据用户输入和章节功能确认。\n\n"
                "## 视觉锚点\n- 待补齐。\n\n"
                "## 关键道具\n- 待补齐。\n\n"
                "## 氛围\n待补齐。\n\n"
                f"## 登场章节\n{chapter_lines}\n\n"
                "## 剧情功能\n待根据本章目标确认。\n\n"
                "## 禁止改动\n- 不得覆盖用户已明确给出的场景事实。\n- 不得在代码层发明具体地点、道具或视觉锚点。\n\n"
                f"{shared_basis}\n"
            )
            files[f"{project_id}/state/environments/{name}.md"] = content
        return files

    @staticmethod
    def _derive_generic_entity_names(source_text: str, markers: List[str], fallback: str) -> List[str]:
        names: List[str] = []
        for marker in markers:
            for separator in ["：", ":"]:
                token = f"{marker}{separator}"
                if token not in source_text:
                    continue
                value = source_text.split(token, 1)[1].splitlines()[0].strip()
                value = value.split("。")[0].split(".")[0].split("；")[0].split(";")[0].strip()
                for raw_part in re.split(r"[、,，/]", value):
                    name = TeammateManager._safe_generated_name(raw_part, "")
                    if name and not TeammateManager._is_placeholder_entity_name(name) and name not in names:
                        names.append(name)
        return names or [fallback]

    @staticmethod
    def _derive_generic_entity_name(source_text: str, markers: List[str], fallback: str) -> str:
        for marker in markers:
            for separator in ["：", ":"]:
                token = f"{marker}{separator}"
                if token not in source_text:
                    continue
                value = source_text.split(token, 1)[1].splitlines()[0].strip()
                value = value.split("，")[0].split(",")[0].split("；")[0].split(";")[0].split("。")[0].split(".")[0].strip()
                if value and not TeammateManager._is_placeholder_entity_name(value):
                    return TeammateManager._safe_generated_name(value, fallback)
        return fallback

    @staticmethod
    def _is_placeholder_entity_name(value: str) -> bool:
        return any(marker in value for marker in ["待", "生成", "确认", "补齐", "setup", "根据用户事实源"])

    @staticmethod
    def _safe_generated_name(raw_name: str, fallback: str) -> str:
        forbidden = set('\\/:*?"<>|\n\r\t')
        name = "".join(ch for ch in raw_name if ch not in forbidden).strip()
        return name[:30] or fallback

    def _tool_use_loop(self, name: str, response, conversation_history: List[dict],
                       system_prompt: str, tools: List[dict],
                       policy_context: Optional[Dict[str, Any]] = None,
                       max_iterations: int = 20):
        """处理工具调用循环，直到 AI 不再请求工具"""
        iteration_count = 0
        tool_call_history: List[tuple[str, str]] = []  # (tool_name, tool_input_hash)

        while response.choices[0].finish_reason == "tool_calls":
            iteration_count += 1

            # 检查循环次数限制
            if iteration_count > max_iterations:
                error_msg = f"工具循环超过最大迭代次数 {max_iterations}，强制终止。最近调用：{tool_call_history[-5:]}"
                self._emit_event(name, "tool_loop_max_iterations", error_msg, {"iteration_count": iteration_count, "recent_calls": tool_call_history[-10:]})
                conversation_history.append({
                    "role": "user",
                    "content": f"[系统警告] 工具循环已达到 {max_iterations} 次迭代上限。请立即调用 idle 工具结束任务，或调用 send_message 提交结果。"
                })
                # 强制返回，让模型有机会调用 idle
                break

            assistant_msg = response.choices[0].message
            if self._is_architect(name):
                self._debug_architect("tool_loop_start", f"模型请求工具调用：iteration={iteration_count}, tool_calls={len(assistant_msg.tool_calls or [])}, history_len={len(conversation_history)}")

            # 把 assistant 的 tool_calls 消息加入历史
            conversation_history.append(assistant_msg.model_dump())

            # 执行每个 tool call 并收集结果
            for tool_call in assistant_msg.tool_calls:
                fn = tool_call.function
                tool_input = json.loads(fn.arguments)

                # 记录工具调用历史，用于检测重复调用
                tool_signature = f"{fn.name}({json.dumps(tool_input, sort_keys=True, ensure_ascii=False)})"
                tool_hash = f"{fn.name}:{hash(tool_signature)}"
                tool_call_history.append((fn.name, tool_hash))

                # 检测重复调用（连续3次相同调用）
                if len(tool_call_history) >= 3:
                    recent_three = tool_call_history[-3:]
                    if all(call[1] == recent_three[0][1] for call in recent_three):
                        warning_msg = f"检测到连续3次相同工具调用：{fn.name}，可能陷入循环"
                        self._emit_event(name, "tool_loop_repetition_warning", warning_msg, {"tool_name": fn.name, "iteration": iteration_count})

                if self._is_architect(name):
                    self._debug_architect("tool_call_before", f"准备执行工具：{fn.name}, input={tool_input}, iteration={iteration_count}", {"tool_name": fn.name, "tool_input": tool_input, "iteration": iteration_count})

                result = self._execute_tool(name, fn.name, tool_input, policy_context=policy_context)

                if self._is_architect(name):
                    self._debug_architect("tool_call_after", f"工具执行完成：{fn.name}, success={result.get('success')}, iteration={iteration_count}", {"tool_name": fn.name, "result": result, "iteration": iteration_count})

                conversation_history.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": json.dumps(result, ensure_ascii=False)
                })

            messages = [{"role": "system", "content": system_prompt}] + conversation_history
            if self._is_architect(name):
                self._debug_architect("tool_loop_model_before", f"工具结果已写入历史，准备再次调用模型：history_len={len(conversation_history)}, iteration={iteration_count}")

            response = self.client.chat.completions.create(
                model=self.config["model"],
                messages=messages,
                tools=tools
            )

            if self._is_architect(name):
                self._debug_architect("tool_loop_model_after", f"工具循环后模型返回：finish_reason={response.choices[0].finish_reason}, iteration={iteration_count}")

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

    def _execute_tool(self, name: str, tool_name: str, tool_input: dict,
                      policy_context: Optional[Dict[str, Any]] = None) -> dict:
        """执行工具调用"""
        try:
            if self._is_architect(name):
                self._debug_architect("execute_tool_enter", f"进入工具执行：tool={tool_name}, input={tool_input}", {"tool_name": tool_name, "tool_input": tool_input})
            self.policy.authorize_file_tool(name, tool_name, tool_input, context=policy_context)
            if self._is_architect(name):
                self._debug_architect("execute_tool_authorized", f"工具权限校验通过：tool={tool_name}")
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

    def _emit_event(self, agent: str, stage: str, message: str, metadata: Optional[Dict[str, Any]] = None):
        if self.event_logger is not None:
            self.event_logger.emit(agent, stage, message, metadata or {})

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
