# src/lead.py
"""
Lead Agent — 总编主循环
接收用户输入 → 判断阶段 → 派发任务 → 汇总结果
"""

import importlib.util
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from openai import OpenAI
except Exception:
    OpenAI = None

from config import load_config
from feedback_loop import (
    apply_modification_instruction,
    render_stage_delivery,
    validate_modification_instruction,
)
from p0_runtime import TaskManager, BackgroundManager, MessageBus, auto_compact, run_read, run_write
from p1_skills import SkillLoader
from p2_content import (
    comic_init_project, comic_read_character, comic_write_character,
    comic_list_characters, comic_read_environment, comic_write_environment,
    comic_list_environments, comic_qa_check_chapter
)
from p3_team import TeammateManager


_AUX_SUBAGENT_CLASS = None


def _get_aux_subagent_class():
    global _AUX_SUBAGENT_CLASS
    if _AUX_SUBAGENT_CLASS is not None:
        return _AUX_SUBAGENT_CLASS

    module_path = Path(__file__).parent / "p3_team" / "aux_subagent.py"
    spec = importlib.util.spec_from_file_location("lead_aux_subagent_module", module_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载临时 subagent 模块: {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _AUX_SUBAGENT_CLASS = module.AuxSubagentManager
    return _AUX_SUBAGENT_CLASS

class LeadAgent:
    """总编 Agent"""

    def __init__(self, enable_teammates: bool = True):
        self.config = load_config()
        self.client = OpenAI(
            api_key=self.config["api_key"],
            base_url=self.config["api_base_url"]
        ) if OpenAI is not None else None
        self.task_manager = TaskManager()
        self.message_bus = MessageBus()
        self.skill_loader = SkillLoader()
        self.background_manager = BackgroundManager()
        self.conversation_history = []
        self.system_prompt = self._build_system_prompt()
        self.team_manager = TeammateManager(self.client, task_manager=self.task_manager, message_bus=self.message_bus) if enable_teammates and self.client is not None else None
        aux_cls = _get_aux_subagent_class()
        self.aux_subagent = aux_cls(self.client, model=self.config["model"], skill_loader=self.skill_loader)
        self.feedback_events = []
        self.submissions = {
            "architect": {},
            "director": {},
            "qa": {},
        }
        if self.team_manager is not None:
            self._init_teammates()

    def _build_system_prompt(self) -> str:
        return """你是漫画脚本创作系统的总编（Lead Agent）。

## 你的职责
1. 接收用户的故事想法和指令
2. 判断当前处于哪个创作阶段
3. 派发任务给你的队友（Architect Bot、Director Bot、QA Bot）
4. 汇总队友的成果，展示给用户
5. 处理用户的审批和修改意见

## 你的队友
- **Architect Bot**（architect_bot）：负责故事规划、角色设计、章节剧本编写
- **Director Bot**（director_bot）：负责将剧本转换为分镜脚本
- **QA Bot**（qa_bot）：负责质量检查，确保角色一致性和剧情逻辑

## 工作流程
1. **故事规划阶段**：用户提供想法 → 你初始化项目 → 派 Architect Bot 创建大纲和角色
2. **章节创作阶段**：Architect Bot 写剧本 → Director Bot 写分镜 → QA Bot 质检
3. **审批阶段**：向用户展示成果 → 通过则进入下一章；失败则返回修改

## 注意事项
- 永远不要自己写剧本或分镜，那是队友的工作
- 每完成一个章节，要通知用户生成出来具体内容，必须经过用户审批才能进入下一章
- 如果用户的指令模糊，使用 choice-designer skill 生成选择题
- 使用中文与用户交流"""

    def _init_teammates(self):
        """启动三个核心队友"""
        self.team_manager.spawn(
            name="architect_bot",
            role="剧情架构师",
            system_prompt="""你是 Architect Bot，负责剧情架构工作。

## 你的技能：
- story-planner：故事规划
- character-builder：角色构建
- character-db-manager：角色数据库管理（创建/查询/升级 Level）
- chapter-expander：章节扩写
- environment-builder：场景构建
- environment-db-manager：场景数据库管理（创建/查询/升级 Level）

## 工作流程
1. 收到任务后，先用 load_skill 加载对应的技能手册
2. 按照手册的要求完成工作
3. 每章创作前，自行检查角色/场景 Level 是否满足要求，不够则升级
4. 将结果写入指定文件
5. 调用 idle 表示完成""",
            available_skills=["story-planner", "character-builder", "character-db-manager",
                              "chapter-expander", "environment-builder", "environment-db-manager"]
        )

        self.team_manager.spawn(
            name="director_bot",
            role="分镜导演",
            system_prompt="""你是 Director Bot，依据Architect Bot提供的最终结构任务，负责生成分镜脚本部分。

## 你的技能
- panel-director：分镜导演
- reaction-polisher：反应优化
- character-db-manager：读取角色视觉锚点，确保分镜一致
- environment-db-manager：读取场景道具/氛围，确保画面一致

## 工作流程
1. 读取剧本文件
2. 加载 character-db-manager / environment-db-manager 读取角色和场景数据
3. 加载 panel-director 技能手册
4. 按照手册将剧本转换为分镜脚本（严格对照角色/场景 .md 文件）
5. 写入文件
6. 调用 idle 表示完成""",
            available_skills=["panel-director", "reaction-polisher",
                              "character-db-manager", "environment-db-manager"]
        )

        self.team_manager.spawn(
            name="qa_bot",
            role="质检编辑",
            system_prompt="""你是 QA Bot，负责质量检查。

## 你的技能
- story-qa：质检审查

## 工作流程
1. 读取剧本和分镜文件
2. 读取角色数据库
3. 加载 story-qa 技能手册
4. 逐项检查质量问题
5. 生成 QA 报告
6. 调用 idle 表示完成""",
            available_skills=["story-qa"]
        )

    def run(self):
        """主循环：REPL"""
        print("🎬 漫画脚本 Agent 已启动")
        print("输入你的故事想法，或输入 /help 查看帮助\n")

        while True:
            try:
                user_input = input("👤 你: ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n再见！")
                break

            if not user_input:
                continue
            if user_input == "/quit":
                print("再见！")
                break
            if user_input == "/help":
                self._show_help()
                continue
            if user_input == "/status":
                self._show_status()
                continue

            try:
                response = self._handle_input(user_input)
                print(f"\n🤖 Lead: {response}\n")
            except Exception as e:
                print(f"\n❌ 错误: {e}\n")

    def _handle_input(self, user_input: str) -> str:
        """处理用户输入"""
        self.conversation_history.append({"role": "user", "content": user_input})

        auto_response = self._auto_route_user_input(user_input)
        if auto_response is not None:
            self.conversation_history.append({"role": "assistant", "content": auto_response})
            self.conversation_history = auto_compact(self.conversation_history)
            return auto_response

        if self.client is None:
            fallback = "当前模型客户端不可用，请检查 API 配置后重试。"
            self.conversation_history.append({"role": "assistant", "content": fallback})
            self.conversation_history = auto_compact(self.conversation_history)
            return fallback

        tools = self._build_lead_tools()

        messages = [{"role": "system", "content": self.system_prompt}] + self.conversation_history
        response = self.client.chat.completions.create(
            model=self.config["model"],
            messages=messages,
            tools=tools
        )

        # 工具调用循环
        while response.choices[0].finish_reason == "tool_calls":
            assistant_msg = response.choices[0].message
            self.conversation_history.append(assistant_msg.model_dump())
            for tool_call in assistant_msg.tool_calls:
                fn = tool_call.function
                tool_input = json.loads(fn.arguments)
                result = self._execute_lead_tool(fn.name, tool_input)
                self.conversation_history.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": json.dumps(result, ensure_ascii=False)
                })
            messages = [{"role": "system", "content": self.system_prompt}] + self.conversation_history
            response = self.client.chat.completions.create(
                model=self.config["model"],
                messages=messages,
                tools=tools
            )

        # 检查后台通知
        self._drain_notifications()
        self._drain_agent_results()

        ai_response = response.choices[0].message.content or ""
        self.conversation_history.append({"role": "assistant", "content": ai_response})
        self.conversation_history = auto_compact(self.conversation_history)
        return ai_response

    def _auto_route_user_input(self, user_input: str) -> Optional[str]:
        """迭代3：预分类并自动走澄清/修改闭环。"""
        if self.aux_subagent is None:
            return None

        project_id, chapter_id = self._infer_active_project_chapter()
        classify = self.aux_subagent.run(
            "input_classifier",
            payload={
                "user_text": user_input,
                "project_id": project_id,
                "chapter_id": chapter_id,
            },
            context={"history_size": len(self.conversation_history)},
        )
        if not classify.get("success"):
            return None

        data = classify.get("data", {})
        input_type = data.get("input_type")
        confidence = float(data.get("confidence", 0.0) or 0.0)
        if confidence < 0.55:
            return None

        if input_type == "vague_demand":
            choice = self.aux_subagent.run(
                "choice_designer",
                payload={
                    "mode": "clarification",
                    "user_text": user_input,
                    "project_id": project_id,
                    "chapter_id": chapter_id,
                },
                context={"classification": data},
            )
            if choice.get("success"):
                return json.dumps(
                    {
                        "type": "clarification_questions",
                        "classification": data,
                        "questions": choice.get("data", {}),
                    },
                    ensure_ascii=False,
                )
            return None

        if self._is_feedback_text(user_input):
            architect_output = ""
            if chapter_id:
                architect_submission = self.submissions["architect"].get(chapter_id, {})
                deliverables = architect_submission.get("deliverables", [])
                if deliverables:
                    try:
                        architect_output = run_read(deliverables[0])
                    except Exception:
                        architect_output = ""

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
                context={"classification": data},
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
                return json.dumps(
                    {
                        "type": "feedback_instruction_invalid",
                        "error": validation.get("error"),
                        "raw": choice.get("data", {}),
                    },
                    ensure_ascii=False,
                )

            target_type = self._infer_instruction_target_type(instruction.get("target", ""))
            target_name = self._extract_name_from_target(instruction.get("target", ""))
            apply_result = {
                "success": False,
                "error": "missing target_name",
            }
            if project_id and target_name:
                kwargs = {
                    "project_id": project_id,
                    "instruction": instruction,
                }
                if target_type == "environment":
                    kwargs["environment_name"] = target_name
                else:
                    kwargs["character_name"] = target_name
                apply_result = apply_modification_instruction(**kwargs)

            follow_up = self._enqueue_architect_feedback_task(project_id, chapter_id, instruction)
            return json.dumps(
                {
                    "type": "feedback_instruction_applied",
                    "classification": data,
                    "instruction": instruction,
                    "target_type": target_type,
                    "target_name": target_name,
                    "apply_result": apply_result,
                    "architect_follow_up": follow_up,
                },
                ensure_ascii=False,
            )

        return None

    def _infer_active_project_chapter(self) -> Tuple[str, str]:
        project_id = ""
        chapter_id = ""

        all_tasks = list(self.task_manager.tasks.values())
        all_tasks.sort(key=lambda x: x.get("updated_at", ""), reverse=True)
        for task in all_tasks:
            meta = task.get("metadata", {}) if isinstance(task.get("metadata"), dict) else {}
            pid = meta.get("project_id")
            cid = meta.get("chapter_id")
            if pid and not project_id:
                project_id = pid
            if cid and not chapter_id:
                chapter_id = cid
            if project_id and chapter_id:
                break

        if not chapter_id:
            chapter_keys = list(self.submissions["architect"].keys())
            if chapter_keys:
                chapter_id = chapter_keys[-1]

        if not project_id:
            for role_key in ("architect", "director", "qa"):
                items = list(self.submissions.get(role_key, {}).values())
                if items:
                    pid = items[-1].get("project_id")
                    if pid:
                        project_id = pid
                        break

        return project_id, chapter_id

    @staticmethod
    def _is_feedback_text(user_input: str) -> bool:
        text = (user_input or "").lower()
        keywords = ["修改", "调整", "改成", "不要", "删掉", "替换", "优化", "重写", "建议"]
        return any(k in text for k in keywords)

    def _enqueue_architect_feedback_task(self, project_id: str, chapter_id: str, instruction: Dict[str, Any]) -> Dict[str, Any]:
        if not project_id or not chapter_id:
            return {"success": False, "error": "project_id/chapter_id 不完整，无法创建 Architect 反馈任务"}

        task_id = self.task_manager.create(
            title=f"Architect 反馈修订 {chapter_id}",
            assignee="architect_bot",
            metadata={
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
        )
        self.message_bus.send(
            "lead",
            "architect_bot",
            {
                "type": "feedback_instruction",
                "project_id": project_id,
                "chapter_id": chapter_id,
                "instruction": instruction,
                "task_id": task_id,
            },
        )
        return {"success": True, "task_id": task_id}

    @staticmethod
    def _infer_instruction_target_type(target: str) -> str:
        t = (target or "").lower()
        if ".md" in t and any(k in t for k in ["场景", "环境", "environment", "env"]):
            return "environment"
        if any(k in t for k in ["场景", "环境", "environment", "env"]):
            return "environment"
        return "character"

    @staticmethod
    def _extract_name_from_target(target: str) -> str:
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

    def _emit_stage_delivery(self, stage: str, files: List[str], summary: str = ""):
        event = render_stage_delivery(stage=stage, files=files, summary=summary)
        self.feedback_events.append(event)
        self.conversation_history.append({"role": "assistant", "content": event})

    def _build_lead_tools(self) -> List[dict]:
        """构建 Lead 专用工具（OpenAI function calling 格式）"""
        raw_tools = [
            {
                "name": "comic_init_project",
                "description": "初始化漫画项目",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "project_id": {"type": "string", "description": "项目ID，英文+下划线"},
                        "brief": {"type": "string", "description": "项目简介"},
                        "num_chapters": {"type": "integer", "description": "章节数，默认3"}
                    },
                    "required": ["project_id", "brief"]
                }
            },
            {
                "name": "create_task",
                "description": "创建任务并指派给队友",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "assignee": {"type": "string", "enum": ["architect_bot", "director_bot", "qa_bot"]},
                        "depends_on": {"type": "array", "items": {"type": "string"}},
                        "metadata": {"type": "object"}
                    },
                    "required": ["title", "assignee"]
                }
            },
            {
                "name": "start_architect_protocol",
                "description": "启动 Architect 的目标驱动任务协议",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "project_id": {"type": "string"},
                        "chapter_id": {"type": "string"},
                        "goal": {"type": "string"}
                    },
                    "required": ["project_id", "chapter_id", "goal"]
                }
            },
            {
                "name": "send_message",
                "description": "给队友发消息",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "to": {"type": "string", "enum": ["architect_bot", "director_bot", "qa_bot"]},
                        "message": {"type": "object"}
                    },
                    "required": ["to", "message"]
                }
            },
            {
                "name": "comic_read_character",
                "description": "读取单个角色 .md 文件",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "project_id": {"type": "string"},
                        "character_name": {"type": "string", "description": "角色名（文件名不含后缀）"}
                    },
                    "required": ["project_id", "character_name"]
                }
            },
            {
                "name": "comic_write_character",
                "description": "写入单个角色 .md 文件",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "project_id": {"type": "string"},
                        "character_name": {"type": "string"},
                        "content": {"type": "string", "description": "角色文件的完整 Markdown 内容"}
                    },
                    "required": ["project_id", "character_name", "content"]
                }
            },
            {
                "name": "comic_list_characters",
                "description": "列出项目所有角色名",
                "parameters": {
                    "type": "object",
                    "properties": {"project_id": {"type": "string"}},
                    "required": ["project_id"]
                }
            },
            {
                "name": "comic_read_environment",
                "description": "读取单个场景 .md 文件",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "project_id": {"type": "string"},
                        "env_name": {"type": "string", "description": "场景名（文件名不含后缀）"}
                    },
                    "required": ["project_id", "env_name"]
                }
            },
            {
                "name": "comic_write_environment",
                "description": "写入单个场景 .md 文件",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "project_id": {"type": "string"},
                        "env_name": {"type": "string"},
                        "content": {"type": "string", "description": "场景文件的完整 Markdown 内容"}
                    },
                    "required": ["project_id", "env_name", "content"]
                }
            },
            {
                "name": "comic_list_environments",
                "description": "列出项目所有场景名",
                "parameters": {
                    "type": "object",
                    "properties": {"project_id": {"type": "string"}},
                    "required": ["project_id"]
                }
            },
            {
                "name": "load_skill",
                "description": "加载技能手册（用于判断阶段或参考）",
                "parameters": {
                    "type": "object",
                    "properties": {"skill_name": {"type": "string"}},
                    "required": ["skill_name"]
                }
            },
            {
                "name": "read_file",
                "description": "读取文件内容",
                "parameters": {
                    "type": "object",
                    "properties": {"file_path": {"type": "string"}},
                    "required": ["file_path"]
                }
            },
            {
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
            },
            {
                "name": "list_teammates",
                "description": "列出所有队友及其当前状态",
                "parameters": {
                    "type": "object",
                    "properties": {}
                }
            },
            {
                "name": "list_tasks",
                "description": "列出指定队友的待办任务",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "assignee": {"type": "string", "description": "队友名字，留空则列出所有"}
                    }
                }
            },
            {
                "name": "list_agent_results",
                "description": "查看已回传给 Lead 的 agent 任务结果",
                "parameters": {
                    "type": "object",
                    "properties": {}
                }
            },
            {
                "name": "classify_input",
                "description": "调用临时 subagent 对用户输入做阶段分类（JSON）",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "user_text": {"type": "string"},
                        "project_id": {"type": "string"},
                        "chapter_id": {"type": "string"}
                    },
                    "required": ["user_text"]
                }
            },
            {
                "name": "design_choice",
                "description": "调用临时 subagent 生成澄清题或结构化修改指令（JSON）",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "mode": {"type": "string", "enum": ["clarification", "modification"]},
                        "user_text": {"type": "string"},
                        "project_id": {"type": "string"},
                        "chapter_id": {"type": "string"},
                        "architect_output": {"type": "string"},
                        "context": {"type": "object"}
                    },
                    "required": ["mode", "user_text"]
                }
            },
            {
                "name": "submit_feedback_instruction",
                "description": "应用结构化修改指令到事实源并执行 V3 约束校验",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "project_id": {"type": "string"},
                        "instruction": {"type": "object"},
                        "target_type": {"type": "string", "enum": ["character", "environment"]},
                        "target_name": {"type": "string"}
                    },
                    "required": ["project_id", "instruction"]
                }
            },
            {
                "name": "list_feedback_events",
                "description": "列出反馈闭环阶段展示事件",
                "parameters": {
                    "type": "object",
                    "properties": {}
                }
            },
            {
                "name": "get_protocol_state",
                "description": "查看当前章节的协议状态",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "chapter_id": {"type": "string"}
                    },
                    "required": ["chapter_id"]
                }
            },
            {
                "name": "get_user_chapter_summary",
                "description": "获取面向用户的章节状态摘要",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "chapter_id": {"type": "string"}
                    },
                    "required": ["chapter_id"]
                }
            }
        ]
        return [{"type": "function", "function": t} for t in raw_tools]

    def _execute_lead_tool(self, tool_name: str, tool_input: dict) -> dict:
        """执行 Lead 的工具调用"""
        try:
            if tool_name == "comic_init_project":
                return comic_init_project(**tool_input)
            elif tool_name == "create_task":
                task_id = self.task_manager.create(**tool_input)
                return {"success": True, "task_id": task_id}
            elif tool_name == "start_architect_protocol":
                return self._start_architect_protocol(
                    project_id=tool_input["project_id"],
                    chapter_id=tool_input["chapter_id"],
                    goal=tool_input["goal"],
                )
            elif tool_name == "send_message":
                if self.team_manager is None:
                    return {"success": False, "error": "当前未启用 teammates"}
                self.message_bus.send("lead", tool_input["to"], tool_input["message"])
                return {"success": True}
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
            elif tool_name == "load_skill":
                content = self.skill_loader.load(tool_input["skill_name"])
                return {"success": True, "content": content}
            elif tool_name == "read_file":
                content = run_read(tool_input["file_path"])
                return {"success": True, "content": content}
            elif tool_name == "write_file":
                return run_write(tool_input["file_path"], tool_input["content"])
            elif tool_name == "list_teammates":
                teammates = self.team_manager.list_all() if self.team_manager is not None else []
                return {"success": True, "teammates": teammates}
            elif tool_name == "list_tasks":
                assignee = tool_input.get("assignee", "")
                if assignee:
                    tasks = self.task_manager.list_for_assignee(assignee)
                else:
                    tasks = list(self.task_manager.tasks.values())
                return {"success": True, "tasks": tasks}
            elif tool_name == "list_agent_results":
                results = [
                    task for task in self.task_manager.tasks.values()
                    if task.get("result") and isinstance(task.get("result"), dict)
                ]
                return {"success": True, "results": results}
            elif tool_name == "classify_input":
                payload = {
                    "user_text": tool_input["user_text"],
                    "project_id": tool_input.get("project_id", ""),
                    "chapter_id": tool_input.get("chapter_id", ""),
                }
                context = {"history_size": len(self.conversation_history)}
                return self.aux_subagent.run("input_classifier", payload=payload, context=context)
            elif tool_name == "design_choice":
                mode = tool_input["mode"]
                payload = {
                    "mode": mode,
                    "user_text": tool_input["user_text"],
                    "feedback_text": tool_input.get("user_text", ""),
                    "project_id": tool_input.get("project_id", ""),
                    "chapter_id": tool_input.get("chapter_id", ""),
                    "architect_output": tool_input.get("architect_output", ""),
                }
                context = tool_input.get("context", {}) if isinstance(tool_input.get("context", {}), dict) else {}
                return self.aux_subagent.run("choice_designer", payload=payload, context=context)
            elif tool_name == "submit_feedback_instruction":
                instruction = tool_input["instruction"]
                validation = validate_modification_instruction(instruction)
                if not validation.get("ok"):
                    return {"success": False, "error": validation.get("error")}

                target_type = tool_input.get("target_type") or self._infer_instruction_target_type(instruction.get("target", ""))
                target_name = tool_input.get("target_name") or self._extract_name_from_target(instruction.get("target", ""))
                if not target_name:
                    return {"success": False, "error": "无法从 instruction.target 推断 target_name，请显式提供 target_name"}

                kwargs = {
                    "project_id": tool_input["project_id"],
                    "instruction": instruction,
                }
                if target_type == "environment":
                    kwargs["environment_name"] = target_name
                else:
                    kwargs["character_name"] = target_name

                applied = apply_modification_instruction(**kwargs)
                if applied.get("success"):
                    self.feedback_events.append(
                        f"修改已应用：{target_type}:{target_name} <- {instruction.get('content', '')}"
                    )
                return applied
            elif tool_name == "list_feedback_events":
                return {"success": True, "events": self.feedback_events}
            elif tool_name == "get_protocol_state":
                chapter_id = tool_input["chapter_id"]
                return {
                    "success": True,
                    "chapter_id": chapter_id,
                    "architect": self.submissions["architect"].get(chapter_id),
                    "director": self.submissions["director"].get(chapter_id),
                    "qa": self.submissions["qa"].get(chapter_id),
                }
            elif tool_name == "get_user_chapter_summary":
                return {"success": True, **self._build_user_chapter_summary(tool_input["chapter_id"])}
            else:
                return {"success": False, "error": f"Unknown tool: {tool_name}"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _drain_notifications(self):
        """检查后台任务通知"""
        notifications = self.background_manager.drain_notifications()
        for n in notifications:
            self.conversation_history.append({
                "role": "user",
                "content": f"[系统通知] 后台任务 {n['task_id']} 完成: {json.dumps(n, ensure_ascii=False)}"
            })

    def _drain_agent_results(self):
        """读取来自各 agent 的结果回传。"""
        messages = self.message_bus.read_inbox("lead", mark_read=True)
        for msg in messages:
            payload = msg.get("message", {})
            payload_type = payload.get("type")
            if payload_type == "task_result":
                task_id = payload.get("task_id")
                task = self.task_manager.get(task_id) if task_id else None
                if task_id and task is not None:
                    new_status = "done" if payload.get("success") else "error"
                    self.task_manager.update(task_id, new_status, payload)
                self.conversation_history.append({
                    "role": "user",
                    "content": (
                        f"[Agent结果] {payload.get('assignee', msg.get('from'))} 完成任务 "
                        f"{payload.get('title', '')}: {json.dumps(payload, ensure_ascii=False)}"
                    )
                })
            elif payload_type in {"handoff", "submission", "verdict"}:
                self._route_protocol_message(msg.get("from", "unknown"), payload)
            else:
                self.conversation_history.append({
                    "role": "user",
                    "content": f"[Agent消息] {json.dumps(msg, ensure_ascii=False)}"
                })

    def _start_architect_protocol(self, project_id: str, chapter_id: str, goal: str) -> dict:
        protocol = {
            "protocol_version": "v1",
            "task_type": "architect_delivery",
            "project_id": project_id,
            "chapter_id": chapter_id,
            "goal": goal,
            "deliverables": [f"{project_id}/chapters/{chapter_id}/script.md"],
            "required_inputs": [f"{project_id}/brief.md"],
            "state_inputs": {
                "characters": f"{project_id}/state/characters",
                "environments": f"{project_id}/state/environments",
            },
            "quality_bar": [
                "剧情完整",
                "角色动机清晰",
                "角色与场景设定一致",
                "可供 Director 直接消费",
            ],
            "required_deliverables": [f"{project_id}/chapters/{chapter_id}/script.md"],
            "qa_target": "qa_bot",
            "handoff_target": "director_bot",
            "submission_target": "qa_bot",
        }
        task_id = self.task_manager.create(
            title=f"Architect 交付 {chapter_id}",
            assignee="architect_bot",
            metadata=protocol,
        )
        return {"success": True, "task_id": task_id, "protocol": protocol}

    def _route_protocol_message(self, from_agent: str, payload: dict):
        payload_type = payload.get("type")
        chapter_id = payload.get("chapter_id", "")
        project_id = payload.get("project_id")
        if payload_type == "handoff" and from_agent == "architect_bot":
            self.submissions["architect"][chapter_id] = payload
            is_revision = bool(payload.get("revision"))
            if is_revision:
                self.submissions["director"].pop(chapter_id, None)
                self.submissions["qa"].pop(chapter_id, None)
            self._emit_stage_delivery(
                stage="Architect 剧本交付",
                files=payload.get("deliverables", []),
                summary=payload.get("summary", "")
            )
            director_task = {
                "protocol_version": "v1",
                "task_type": "director_delivery",
                "project_id": project_id,
                "chapter_id": chapter_id,
                "goal": "基于 Architect 交付生成高质量分镜",
                "inputs": payload.get("deliverables", []),
                "required_inputs": payload.get("deliverables", []),
                "state_inputs": payload.get("state_inputs", {}),
                "quality_bar": [
                    "严格对应 script.md",
                    "视觉锚点一致",
                    "场景道具与氛围一致",
                    "镜头语言清楚",
                ],
                "deliverables": [f"{project_id}/chapters/{chapter_id}/storyboard.md"],
                "required_deliverables": [f"{project_id}/chapters/{chapter_id}/storyboard.md"],
                "qa_target": "qa_bot",
                "submission_target": "lead" if is_revision else "qa_bot",
                "revision": is_revision,
            }
            self.task_manager.create(
                title=f"{'Director 修订交付' if is_revision else 'Director 交付'} {chapter_id}",
                assignee="director_bot",
                metadata=director_task,
            )
        elif payload_type == "submission":
            role_key = "architect" if from_agent == "architect_bot" else "director"
            self.submissions[role_key][chapter_id] = payload
            if from_agent == "architect_bot":
                stage_name = "Architect 修订提交" if payload.get("revision") else "Architect 提交"
                self._emit_stage_delivery(
                    stage=stage_name,
                    files=payload.get("deliverables", []),
                    summary=payload.get("summary", "")
                )
            elif from_agent == "director_bot":
                stage_name = "Director 修订分镜提交" if payload.get("revision") else "Director 分镜提交"
                summary_text = payload.get("summary", "")
                if payload.get("revision") and payload.get("diff_summary"):
                    summary_text = f"{summary_text}\n{payload.get('diff_summary')}"
                self._emit_stage_delivery(
                    stage=stage_name,
                    files=payload.get("deliverables", []),
                    summary=summary_text
                )
            architect_submission = self.submissions["architect"].get(chapter_id)
            director_submission = self.submissions["director"].get(chapter_id)
            if architect_submission and director_submission and chapter_id not in self.submissions["qa"]:
                is_revision_review = bool(architect_submission.get("revision") or director_submission.get("revision"))
                qa_task = {
                    "protocol_version": "v1",
                    "task_type": "qa_review",
                    "project_id": project_id,
                    "chapter_id": chapter_id,
                    "architect_submission": architect_submission,
                    "director_submission": director_submission,
                    "review_type": "revision_regression" if is_revision_review else "standard",
                    "review_axes": [
                        "architect_quality",
                        "director_quality",
                        "cross_consistency",
                    ],
                    "required_deliverables": [f"{project_id}/qa/{chapter_id}_report.md"],
                }
                self.task_manager.create(
                    title=f"{'QA 回归验收' if is_revision_review else 'QA 验收'} {chapter_id}",
                    assignee="qa_bot",
                    metadata=qa_task,
                )
        elif payload_type == "verdict":
            self.submissions["qa"][chapter_id] = payload
            review_type = payload.get("review_type", "standard")
            stage_name = "QA 回归验收完成" if review_type == "revision_regression" else "QA 验收完成"
            self._emit_stage_delivery(
                stage=stage_name,
                files=[payload.get("report_file")] if payload.get("report_file") else [],
                summary=payload.get("summary", "")
            )
            summary = self._build_user_chapter_summary(chapter_id)
            self.conversation_history.append({
                "role": "assistant",
                "content": (
                    f"章节 {chapter_id} 的 QA 已完成。最终结论：{payload.get('final_verdict')}。"
                    f"\n评审类型：{review_type}"
                    f"\n{summary['summary']}"
                    f"\n问题数：{len(payload.get('issues', []))}"
                )
            })

        self.conversation_history.append({
            "role": "user",
            "content": f"[协议消息] {from_agent}: {json.dumps(payload, ensure_ascii=False)}"
        })

    def _build_user_chapter_summary(self, chapter_id: str) -> dict:
        architect = self.submissions["architect"].get(chapter_id)
        director = self.submissions["director"].get(chapter_id)
        qa = self.submissions["qa"].get(chapter_id)
        issue_count = len(qa.get("issues", [])) if qa else 0
        verdict = qa.get("final_verdict") if qa else None
        return {
            "chapter_id": chapter_id,
            "architect_submitted": architect is not None,
            "director_submitted": director is not None,
            "qa_ready": qa is not None,
            "qa_final_verdict": verdict,
            "issue_count": issue_count,
            "report_file": qa.get("report_file") if qa else None,
            "summary": (
                f"{chapter_id}: Architect={'已提交' if architect else '未提交'}，"
                f"Director={'已提交' if director else '未提交'}，"
                f"QA={'已完成' if qa else '未完成'}，"
                f"Verdict={verdict or 'PENDING'}，Issues={issue_count}"
            )
        }

    def _show_status(self):
        """显示系统状态"""
        print("\n📊 系统状态")
        teammates = self.team_manager.list_all()
        for tm in teammates:
            status_emoji = {"idle": "💤", "working": "⚡", "shutdown": "🔴"}.get(tm["status"], "❓")
            print(f"  {status_emoji} {tm['name']} ({tm['role']}): {tm['status']}")
        print()

    def _show_help(self):
        print("""
╔══════════════════════════════════════════════════════════╗
║               漫画脚本 Agent 使用指南                      ║
╚══════════════════════════════════════════════════════════╝

## 快速开始
1. 输入你的故事想法，如：
   "我想创作一个关于时间旅行者的科幻故事"

2. 系统会引导你完善设定，然后自动生成：
   - 故事大纲
   - 角色卡
   - 章节剧本
   - 分镜脚本

3. 每完成一章，系统会展示成果并等待你的审批

## 命令
- /help    显示帮助
- /status  查看队友状态
- /quit    退出系统
""")


# 入口
if __name__ == "__main__":
    try:
        lead = LeadAgent()
        lead.run()
    except FileNotFoundError as e:
        print(f"❌ {e}")
        print("   请确保 config.json 存在且包含 api_key、api_base_url、model")
        exit(1)
