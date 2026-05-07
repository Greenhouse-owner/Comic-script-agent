# src/lead.py
"""
Lead Agent — 总编主循环
接收用户输入 → 判断阶段 → 派发任务 → 汇总结果
"""

import importlib.util
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from openai import OpenAI
except Exception:
    OpenAI = None

from config import load_config
from feedback_loop import (
    render_stage_delivery,
    validate_modification_instruction,
)
from feedback_orchestrator import FeedbackOrchestrator
from intake import IntakePersistenceService
from planning import ARCHITECT_ARTIFACT_CONTRACTS, ProjectStateScanner
from p0_runtime import TaskManager, BackgroundManager, MessageBus, EventLogger, auto_compact, run_read, run_write, safe_path
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
        self.event_logger = EventLogger()
        self.skill_loader = SkillLoader()
        self.background_manager = BackgroundManager()
        self.conversation_history = []
        self.system_prompt = self._build_system_prompt()
        self.team_manager = TeammateManager(self.client, task_manager=self.task_manager, message_bus=self.message_bus, event_logger=self.event_logger) if enable_teammates and self.client is not None else None
        aux_cls = _get_aux_subagent_class()
        self.aux_subagent = aux_cls(self.client, model=self.config["model"], skill_loader=self.skill_loader)
        self.intake_service = IntakePersistenceService()
        self.feedback_events = []
        self.pending_clarification: Optional[Dict[str, Any]] = None
        self.submissions = {
            "architect": {},
            "director": {},
            "qa": {},
        }
        self.feedback_orchestrator = FeedbackOrchestrator(self.aux_subagent, self.submissions)
        self.latest_planner_decision: Optional[Dict[str, Any]] = None
        self.pending_revision: Optional[Dict[str, Any]] = None
        self.pending_director_offer: Optional[Dict[str, Any]] = None
        self.project_state_scanner = ProjectStateScanner()
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
- **QA Bot**（qa_bot）：负责通用 QA 审查、修改影响分析、一致性诊断和修订回归检查

## 工作流程
1. **故事规划阶段**：用户提供想法 → 你用 `comic_init_project` 初始化项目 → 用 `start_architect_protocol` 派发 Architect 创作任务
2. **章节创作阶段**：Architect Bot 写剧本 → Director Bot 写分镜 → QA Bot 质检
3. **QA 审查阶段**：Architect / Director 交付后 → 通过 qa-task-brief-designer 生成通用 qa_review → QA Bot 自主加载 skills 审查
4. **修改闭环阶段**：用户提出修改 → QA Bot 先做影响分析 → 用户确认范围 → Architect 修订 → QA Bot 回归审查

## 关键工具使用规则
- **派发 Architect 创作任务时，必须使用 `start_architect_protocol` 工具**，传入 project_id、chapter_id（默认 "ch01"）和 goal。该工具会自动设置动态计划，让 Architect 按顺序生成 story_bible → chapter_outlines → 角色卡 → 场景卡 → 章节剧本。
- **不要用 `create_task` 给 architect_bot 派发创作任务**，因为 create_task 缺少必要的协议元数据，会导致 Architect 无法正确执行。
- `create_task` 仅用于非标准任务（如 QA 审查、Director 分镜等）。
- 初始化项目时先调用 `comic_init_project(project_id, brief)`，再调用 `start_architect_protocol`。
- project_id 从对话上下文中获取（如 clarification_summary 里的 persist_result.project_id）。

## 注意事项
- 永远不要自己写剧本或分镜，那是队友的工作
- 每次 QA 任务都使用通用 task_type=qa_review，具体 review_goal 由 qa-task-brief-designer skill 生成，不能由代码硬写业务审查规则
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

## artifact_revision 工作流
当任务 metadata.task_type == "artifact_revision" 时：
1. 你不是重新创作整个项目，而是根据用户修改请求和 QA 报告修订已有 artifact
2. 必须读取 metadata.change_request.raw_user_text
3. 必须参考 metadata.qa_report 或 metadata.qa_report_file；如果报告文件不存在，使用 metadata.qa_report 继续
4. 必须优先修订 metadata.target_files 中列出的文件；如果为空，根据 QA 报告和项目 artifact 自行判断最小必要范围
5. revision_mode 控制范围：local 只修直接目标，targeted 修直接目标和高置信度影响文件，cascade 级联修订所有可能受影响 artifact
6. 所有具体创作和一致性判断必须依据 story-planner、character-builder、chapter-expander、environment-builder 等 skills 和模型，不由代码规则决定
7. 不得覆盖用户明确事实，不得引入与项目事实源冲突的新内容
8. 修订完成后必须用 send_message 向 Lead 发送标准 submission，字段至少包含：type=submission、from_role=architect、task_type=artifact_revision、project_id、chapter_id、revision=true、revision_mode、change_request、qa_report_file、revised_files、deliverables、revision_summary、user_visible_summary

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
- director-context-planner：选择分镜所需角色/场景上下文，支持有边界二次读取
- director-storyboard-planner：综合规划故事节拍、页数、格子布局和张力策略
- storyboard-draft-writer：根据计划生成 storyboard.md
- panel-director：分镜导演基础规则
- reaction-polisher：人物反应优化
- character-db-manager：读取角色视觉锚点，确保分镜一致
- environment-db-manager：读取场景道具/氛围，确保画面一致

## 工作流程
1. 读取剧本文件
2. 用 director-context-planner 选择必要角色/场景上下文
3. 用 director-storyboard-planner 综合规划 beats、pages、layout、tension
4. 用 storyboard-draft-writer 生成完整 storyboard.md
5. 写入 selected_context.json、storyboard_plan.json、pipeline_status.json 和 storyboard.md
6. 用 send_message 向 Lead 发送标准 submission
7. 调用 idle 表示完成""",
            available_skills=["director-context-planner", "director-storyboard-planner", "storyboard-draft-writer",
                              "panel-director", "reaction-polisher",
                              "character-db-manager", "environment-db-manager"]
        )

        self.team_manager.spawn(
            name="qa_bot",
            role="质检编辑",
            system_prompt="""你是 QA Bot，负责项目质量审查、一致性诊断、修改影响分析和修订回归检查。

## 你的技能
- qa-review-router：根据通用 qa_review 的 review_goal / review_context 选择本次应使用的 QA skills
- change-impact-analyzer：分析用户修改请求可能影响哪些 artifact
- project-consistency-reviewer：审查完整项目或重点 artifact 之间的一致性
- revision-regression-reviewer：检查 Architect 修订是否落实用户请求并避免新冲突
- story-qa：审查单章剧本 / 分镜质量

## 工作原则
1. 你只接受通用 task_type=qa_review 的 QA 任务
2. 收到任务后，根据 review_goal 和 review_context 自行判断要加载哪些 QA skills
3. 优先加载 qa-review-router 做内部路由，再加载对应审查 skill
4. 你可以读取项目 artifact，但不直接修改创作文件
5. 审查报告需要同时包含面向用户的 summary 和机器可读结果
6. 你必须把报告写入 expected_outputs.report_file；如果没有该字段，则写入 `{project_id}/qa/latest_report.md`
7. 完成后必须用 send_message 给 Lead 发送 verdict，字段至少包含：type=verdict、from_role=qa、project_id、chapter_id、task_type=qa_review、report_kind、final_verdict、summary、summary_for_user、issues、report_file、review_context、recommended_actions、target_files、requires_architect_follow_up
8. final_verdict 只能使用 PASS、WARNING、FAIL；issues、recommended_actions、target_files 必须是数组
9. 如果是修改影响分析，请尽量把直接目标和可能受影响 artifact 路径放入 target_files；具体影响判断必须来自 QA skills 和模型
10. 所有业务审查判断必须通过 skills 和模型完成，不要依赖 Lead 代码硬编码规则
11. 完成后调用 idle 表示完成""",
            available_skills=[
                "qa-review-router",
                "change-impact-analyzer",
                "project-consistency-reviewer",
                "revision-regression-reviewer",
                "story-qa",
            ]
        )

    def run(self):
        """主循环：REPL"""
        print("🎬 漫画脚本 Agent 已启动")
        print("输入你的故事想法，或输入 /help 查看帮助\n")

        while True:
            try:
                user_input = input("👤 你: ").strip()
            except (EOFError, KeyboardInterrupt):
                self._wait_for_active_tasks()
                print("\n再见！")
                break

            # 先处理本地命令，避免 /status 等控制命令进入模型对话。
            if not user_input:
                continue
            if user_input == "/quit":
                self._wait_for_active_tasks()
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

    def _wait_for_active_tasks(self, timeout: int = 300):
        """退出前等待正在执行的队友任务完成，避免 daemon 线程被杀导致产物丢失。"""
        if self.task_manager is None:
            return
        poll_interval = 3
        waited = 0
        while waited < timeout:
            active = [
                t for t in self.task_manager.tasks.values()
                if t.get("status") in ("in_progress", "pending")
                and t.get("assignee") in ("architect_bot", "director_bot", "qa_bot")
            ]
            if not active:
                return
            if waited == 0:
                names = ", ".join(f"{t.get('title', t['id'])}({t['assignee']})" for t in active[:3])
                print(f"\n⏳ 等待队友完成任务：{names} ...")
            time.sleep(poll_interval)
            waited += poll_interval
        print("⚠️ 等待超时，部分任务可能未完成。")

    def _handle_input(self, user_input: str) -> str:
        """处理用户输入"""
        self.conversation_history.append({"role": "user", "content": user_input})

        # 本地工作流命令优先于自动分类和通用模型回复。
        local_workflow_response = self._handle_local_workflow_command(user_input)
        if local_workflow_response is not None:
            self.conversation_history.append({"role": "assistant", "content": local_workflow_response})
            self.conversation_history = auto_compact(self.conversation_history)
            return local_workflow_response

        # 自动路由优先于通用模型回复：明确的写章/反馈/澄清场景不再交给 Lead 大模型自由发挥。
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

        # Lead 模型也可能请求工具；这里持续执行工具直到模型给出普通文本回复。
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

        # 每次用户输入后顺手 drain 后台结果，避免 agent 已完成但 Lead 对话状态没有同步。
        self._drain_notifications()
        self._drain_agent_results()

        ai_response = response.choices[0].message.content or ""
        self.conversation_history.append({"role": "assistant", "content": ai_response})
        self.conversation_history = auto_compact(self.conversation_history)
        return ai_response

    def _handle_local_workflow_command(self, user_input: str) -> Optional[str]:
        text = user_input.strip()
        if not text.startswith("/"):
            return None
        lowered = text.lower()
        if lowered == "/list" or lowered.startswith("/list "):
            return self._handle_list_command(text)
        if lowered.startswith("/show"):
            return self._handle_show_command(text)
        if lowered.startswith("/director"):
            chapter_id = text[len("/director"):].strip() or None
            return self._start_pending_director_stage(chapter_id)
        if lowered.startswith("/impact"):
            change_request = text[len("/impact"):].strip()
            return self._start_revision_impact_review(change_request, command="impact")
        if lowered.startswith("/revise"):
            change_request = text[len("/revise"):].strip()
            return self._start_revision_impact_review(change_request, command="revise")
        if lowered.startswith("/apply"):
            mode = text[len("/apply"):].strip().lower()
            return self._apply_pending_revision(mode)
        if lowered in {"/check consistency", "/check", "/qa", "/qa check"}:
            return self._start_user_requested_qa(text)
        return None

    def _handle_list_command(self, text: str) -> str:
        project_id, chapter_id = self._infer_active_project_chapter()
        parts = text.split(maxsplit=1)
        if len(parts) > 1 and parts[1].strip():
            project_id = parts[1].strip()
        if not project_id:
            return "当前还没有可识别的项目。你也可以使用 `/list <project_id>` 指定项目。"
        chapter_ids = [chapter_id] if chapter_id else self._infer_project_chapter_ids(project_id)
        state = self.project_state_scanner.scan(project_id, chapter_ids)
        lines = [f"# 项目文件索引：{project_id}", ""]
        for label, key in [
            ("项目 brief", "brief"),
            ("创作方向", "story_direction"),
            ("故事圣经", "story_bible"),
            ("全章节章纲", "chapter_outlines"),
        ]:
            item = state.get(key, {})
            lines.append(f"- {label}: {self._format_artifact_status(item)}")
        lines.append(f"- 角色卡: {state.get('characters', {}).get('count', 0)} 个")
        for path in state.get("characters", {}).get("paths", []):
            lines.append(f"  - {path}")
        lines.append(f"- 场景卡: {state.get('environments', {}).get('count', 0)} 个")
        for path in state.get("environments", {}).get("paths", []):
            lines.append(f"  - {path}")
        if state.get("chapters"):
            lines.append("- 章节文件:")
            for cid, chapter_state in state["chapters"].items():
                lines.append(f"  - {cid} outline: {self._format_artifact_status(chapter_state.get('outline', {}))}")
                lines.append(f"  - {cid} script: {self._format_artifact_status(chapter_state.get('script', {}))}")
        qa_files = self._list_project_markdown_files(project_id, "qa")
        lines.append(f"- QA 报告: {len(qa_files)} 个")
        for path in qa_files:
            lines.append(f"  - {path}")
        lines.extend([
            "",
            "可用查看命令：",
            "- `/show story_bible`",
            "- `/show chapter_outlines`",
            "- `/show characters` / `/show character <name>`",
            "- `/show environments` / `/show environment <name>`",
            "- `/show chapter <chapter_id> outline|script`",
            "- `/show qa latest`",
        ])
        return "\n".join(lines)

    def _handle_show_command(self, text: str) -> str:
        project_id, active_chapter_id = self._infer_active_project_chapter()
        args = text.split()[1:]
        if not args:
            return self._show_usage()
        if args[0] == "project" and len(args) >= 3:
            project_id = args[1]
            args = args[2:]
        if not project_id:
            return "当前还没有可识别的项目。可用 `/show project <project_id> ...` 显式指定项目。"

        key = args[0].lower()
        if key in {"brief", "project_brief"}:
            return self._read_artifact_for_user(f"{project_id}/brief.md")
        if key in {"story_direction", "direction"}:
            return self._read_artifact_for_user(f"{project_id}/story_direction.md")
        if key in {"story_bible", "bible"}:
            return self._read_artifact_for_user(f"{project_id}/story_bible.md")
        if key in {"chapter_outlines", "outlines"}:
            return self._read_artifact_for_user(f"{project_id}/chapter_outlines.md")
        if key == "characters":
            return self._show_named_collection(project_id, "characters")
        if key == "character":
            if len(args) < 2:
                return "请指定角色名，例如：`/show character 唐棠`。"
            return self._read_artifact_for_user(f"{project_id}/state/characters/{' '.join(args[1:])}.md")
        if key == "environments":
            return self._show_named_collection(project_id, "environments")
        if key == "environment":
            if len(args) < 2:
                return "请指定场景名，例如：`/show environment 云上图书馆`。"
            return self._read_artifact_for_user(f"{project_id}/state/environments/{' '.join(args[1:])}.md")
        if key == "chapter":
            if len(args) < 3:
                return "请使用：`/show chapter <chapter_id> outline` 或 `/show chapter <chapter_id> script`。"
            chapter_id = args[1]
            artifact = args[2].lower()
            if artifact == "outline":
                return self._read_artifact_for_user(f"{project_id}/chapters/{chapter_id}/outline.md")
            if artifact == "script":
                return self._read_artifact_for_user(f"{project_id}/chapters/{chapter_id}/script.md")
            if artifact == "storyboard":
                return self._read_artifact_for_user(f"{project_id}/chapters/{chapter_id}/storyboard.md")
            return "章节 artifact 只支持：outline、script、storyboard。"
        if key in {"outline", "script", "storyboard"}:
            if not active_chapter_id:
                return f"当前无法推断章节，请使用 `/show chapter <chapter_id> {key}`。"
            suffix = "outline.md" if key == "outline" else f"{key}.md"
            return self._read_artifact_for_user(f"{project_id}/chapters/{active_chapter_id}/{suffix}")
        if key == "qa":
            target = args[1].lower() if len(args) > 1 else "latest"
            if target == "latest":
                return self._show_latest_qa_report(project_id)
            return self._read_artifact_for_user(f"{project_id}/qa/{target}")
        return self._show_usage()

    @staticmethod
    def _format_artifact_status(item: Dict[str, Any]) -> str:
        path = item.get("path", "")
        if item.get("non_empty"):
            return f"exists ({item.get('bytes', 0)} bytes) | {path}"
        if item.get("exists"):
            return f"empty | {path}"
        return f"missing | {path}"

    def _read_artifact_for_user(self, file_path: str) -> str:
        try:
            content = run_read(file_path)
        except FileNotFoundError:
            return f"文件不存在：{file_path}"
        except Exception as exc:
            return f"读取失败：{file_path}\n{type(exc).__name__}: {exc}"
        if not content.strip():
            return f"文件为空：{file_path}"
        return f"# {file_path}\n\n{content}"

    def _show_named_collection(self, project_id: str, collection: str) -> str:
        label = "角色" if collection == "characters" else "场景"
        files = self._list_project_markdown_files(project_id, f"state/{collection}")
        if not files:
            return f"暂无{label}卡。"
        lines = [f"# {label}卡列表", ""]
        for path in files:
            name = Path(path).stem
            command = "character" if collection == "characters" else "environment"
            lines.append(f"- {name}: `{path}`，查看：`/show {command} {name}`")
        return "\n".join(lines)

    def _show_latest_qa_report(self, project_id: str) -> str:
        latest_path = f"{project_id}/qa/latest_report.md"
        if safe_path(latest_path).exists():
            return self._read_artifact_for_user(latest_path)
        qa_files = self._list_project_markdown_files(project_id, "qa")
        if not qa_files:
            return "暂无 QA 报告。"
        newest = max(qa_files, key=lambda p: safe_path(p).stat().st_mtime if safe_path(p).exists() else 0)
        return self._read_artifact_for_user(newest)

    def _list_project_markdown_files(self, project_id: str, relative_dir: str) -> List[str]:
        directory = safe_path(f"{project_id}/{relative_dir}")
        if not directory.exists() or not directory.is_dir():
            return []
        project_root = safe_path(project_id)
        return [str(path.relative_to(project_root.parent)) for path in sorted(directory.glob("*.md")) if path.is_file()]

    def _infer_project_chapter_ids(self, project_id: str) -> List[str]:
        chapters_dir = safe_path(f"{project_id}/chapters")
        if not chapters_dir.exists() or not chapters_dir.is_dir():
            return []
        return [path.name for path in sorted(chapters_dir.iterdir()) if path.is_dir()]

    @staticmethod
    def _show_usage() -> str:
        return (
            "可用查看命令：\n"
            "- `/list` 或 `/list <project_id>`\n"
            "- `/show story_bible`\n"
            "- `/show chapter_outlines`\n"
            "- `/show characters` / `/show character <name>`\n"
            "- `/show environments` / `/show environment <name>`\n"
            "- `/show chapter <chapter_id> outline|script|storyboard`\n"
            "- `/show qa latest`\n"
            "也可以用 `/show project <project_id> ...` 显式指定项目。"
        )

    def _start_revision_impact_review(self, change_request: str, command: str) -> str:
        if not change_request:
            return f"请在 `/{command}` 后写明修改说明，例如：`/{command} 把主角改成更内向，并检查影响范围`。"
        project_id, chapter_id = self._infer_active_project_chapter()
        if not project_id:
            return "当前还没有可识别的项目，请先创建或生成一个项目后再提交修改。"
        self.pending_revision = {
            "project_id": project_id,
            "chapter_id": chapter_id,
            "user_request": change_request,
            "command": command,
            "awaiting_user_choice": True,
            "available_modes": ["local", "targeted", "cascade", "cancel"],
            "qa_task_id": None,
            "qa_report": {},
        }
        task_id = self._create_qa_review_from_event(
            "user_revision_request",
            project_id=project_id,
            chapter_id=chapter_id,
            raw_user_text=change_request,
            pending_revision=self.pending_revision,
        )
        if task_id:
            self.pending_revision["qa_task_id"] = task_id
            return (
                "已提交给 QA-bot 做修改影响分析。\n"
                f"- 项目：{project_id}\n"
                f"- 章节：{chapter_id or '全项目'}\n"
                f"- QA 任务：{task_id}\n"
                "QA 完成后，你可以根据报告选择：\n"
                "- `/apply local` 只修直接目标\n"
                "- `/apply targeted` 修直接目标和高置信度影响文件\n"
                "- `/apply cascade` 级联修订所有可能受影响内容\n"
                "- `/apply cancel` 取消本次修订"
            )
        return "QA 影响分析任务创建失败，请查看事件日志或稍后重试。"

    def _start_user_requested_qa(self, user_input: str) -> str:
        project_id, chapter_id = self._infer_active_project_chapter()
        if not project_id:
            return "当前还没有可识别的项目，请先创建或生成一个项目后再执行 QA 检查。"
        task_id = self._create_qa_review_from_event(
            "user_requested_qa",
            project_id=project_id,
            chapter_id=chapter_id,
            raw_user_text=user_input,
        )
        if task_id:
            return f"已创建通用 QA 检查任务：{task_id}。QA-bot 会根据项目上下文自行选择审查 skills。"
        return "QA 检查任务创建失败，请查看事件日志或稍后重试。"

    def _apply_pending_revision(self, mode: str) -> str:
        normalized_mode = (mode or "").strip().lower()
        if normalized_mode in {"", "help"}:
            return "请指定应用范围：`/apply local`、`/apply targeted`、`/apply cascade` 或 `/apply cancel`。"
        if normalized_mode == "cancel":
            self.pending_revision = None
            return "已取消本次 pending revision。"
        if normalized_mode not in {"local", "targeted", "cascade"}:
            return "未知修订范围。可选：`local`、`targeted`、`cascade`、`cancel`。"
        if not self.pending_revision:
            return "当前没有等待应用的修改影响分析。请先使用 `/impact <修改说明>` 或 `/revise <修改说明>`。"
        project_id = self.pending_revision.get("project_id", "")
        chapter_id = self.pending_revision.get("chapter_id", "")
        if not project_id:
            return "pending revision 缺少 project_id，无法创建 Architect 修订任务。"
        target_files = self._normalize_revision_target_files(project_id, chapter_id, self.pending_revision.get("target_files", []))
        before_snapshot = self._collect_revision_snapshot(project_id, chapter_id, target_files)
        self.pending_revision["target_files"] = target_files
        self.pending_revision["artifact_contents_before"] = before_snapshot
        metadata = {
            "protocol_version": "v1",
            "task_type": "artifact_revision",
            "project_id": project_id,
            "chapter_id": chapter_id,
            "revision_mode": normalized_mode,
            "change_request": {"raw_user_text": self.pending_revision.get("user_request", "")},
            "qa_report": self.pending_revision.get("qa_report", {}),
            "qa_task_id": self.pending_revision.get("qa_task_id"),
            "qa_report_file": self.pending_revision.get("qa_report_file"),
            "target_files": target_files,
            "artifact_contents_before": before_snapshot,
            "constraints": [
                "保留用户明确事实",
                "只修改与本次修改请求和 QA 报告相关的内容",
                "不得引入与项目事实源冲突的新内容",
                "如存在不确定项，应在修订总结中列出",
            ],
            "submission_target": "lead",
            "revision": True,
        }
        task_id = self.task_manager.create(
            title=f"Architect 修订 {chapter_id or project_id} ({normalized_mode})",
            assignee="architect_bot",
            metadata=metadata,
        )
        self.pending_revision.update({
            "awaiting_user_choice": False,
            "selected_mode": normalized_mode,
            "architect_task_id": task_id,
        })
        self.event_logger.emit(
            "lead",
            "task_created",
            "已创建 Architect artifact_revision 任务",
            {"task_id": task_id, "project_id": project_id, "chapter_id": chapter_id, "revision_mode": normalized_mode},
        )
        return (
            f"已创建 Architect 修订任务：{task_id}\n"
            f"- 修订范围：{normalized_mode}\n"
            "Architect 完成后，Lead 会再次通过 qa-task-brief-designer 创建通用 qa_review 做回归审查。"
        )

    def _auto_route_user_input(self, user_input: str) -> Optional[str]:
        """迭代3：预分类并自动走澄清/修改闭环。"""
        if self.aux_subagent is None:
            return None

        # 如果上一轮已经向用户提出选择题，本轮优先把用户回答归纳为创作方向，避免再次分类成新需求。
        pending_response = self._handle_pending_clarification_response(user_input)
        if pending_response is not None:
            return pending_response

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

        if self.feedback_orchestrator.is_feedback_text(user_input):
            self.feedback_orchestrator.aux_subagent = self.aux_subagent
            self.feedback_orchestrator.submissions = self.submissions
            feedback_result = self.feedback_orchestrator.process_feedback(
                user_input=user_input,
                classification=data,
                project_id=project_id,
                chapter_id=chapter_id,
            )
            if feedback_result is None:
                return None
            follow_up = feedback_result.get("bot_follow_up") or feedback_result.get("architect_follow_up", {})
            if follow_up.get("success") and follow_up.get("metadata"):
                task_id = self.task_manager.create(
                    title=follow_up["title"],
                    assignee=follow_up["assignee"],
                    metadata=follow_up["metadata"],
                )
                message = dict(follow_up.get("message", {}))
                message["task_id"] = task_id
                assignee = follow_up["assignee"]
                self.message_bus.send("lead", assignee, message)
                feedback_result["bot_follow_up"] = {"success": True, "task_id": task_id, "assignee": assignee}
            return json.dumps(feedback_result, ensure_ascii=False)

        if confidence < 0.55:
            return None

        if input_type in self._intake_normalization_types():
            aux_result = self._normalize_user_intake_with_aux(user_input, data, project_id, chapter_id)
            if aux_result is not None:
                recommended = aux_result.get("recommended_next", {}) if isinstance(aux_result.get("recommended_next"), dict) else {}
                dispatch = self._auto_dispatch_architect_if_ready(
                    aux_result.get("project_id", project_id),
                    recommended,
                )
                if dispatch:
                    aux_result["auto_dispatch"] = dispatch
                    aux_result["next_step"] = f"已自动派发 Architect 任务（{dispatch.get('task_id')}），Architect 将按动态计划生成 story_bible → 章纲 → 角色卡 → 场景卡 → 剧本。"
                return json.dumps(aux_result, ensure_ascii=False)
            return json.dumps(
                self._normalize_user_intake(user_input, data, project_id, chapter_id),
                ensure_ascii=False,
            )

        if input_type in {"new_story", "vague_demand"}:
            clarification_kind = "story_direction" if input_type == "new_story" else "general_clarification"
            choice = self.aux_subagent.run(
                "choice_designer",
                payload={
                    "mode": "clarification",
                    "user_text": user_input,
                    "project_id": project_id,
                    "chapter_id": chapter_id,
                },
                context={
                    "classification": data,
                    "clarification_kind": clarification_kind,
                    "instruction": (
                        "如果 classification.input_type 是 new_story，请围绕故事基调、章节规模、主角类型、反派/冲突形态、结局方向生成选择题。"
                    ),
                },
            )
            if choice.get("success"):
                questions_payload = choice.get("data", {})
                self.pending_clarification = {
                    "kind": clarification_kind,
                    "original_input": user_input,
                    "classification": data,
                    "questions": questions_payload,
                    "project_id": project_id,
                    "chapter_id": chapter_id,
                }
                return json.dumps(
                    {
                        "type": "clarification_questions",
                        "clarification_kind": clarification_kind,
                        "classification": data,
                        "questions": questions_payload,
                        "next_step": "请按题号回答选项，也可以直接补充自定义创作方向。",
                    },
                    ensure_ascii=False,
                )
            return None

        return None

    @staticmethod
    def _intake_normalization_types() -> set[str]:
        return {
            "story_direction",
            "worldbuilding",
            "chapter_outline",
            "chapter_detail_outline",
            "character_notes",
            "environment_notes",
            "script_draft",
            "mixed_notes",
        }

    def _normalize_user_intake_with_aux(
        self,
        user_input: str,
        classification: Dict[str, Any],
        project_id: str,
        chapter_id: str,
    ) -> Optional[Dict[str, Any]]:
        if self.aux_subagent is None or self.intake_service is None:
            return None
        target_project_id = (project_id or "").strip() or self._project_id_from_seed(user_input)
        target_chapter_id = (chapter_id or self._extract_chapter_id(user_input) or "ch01").strip()
        normalize = self.aux_subagent.run(
            "intake_normalizer",
            payload={
                "user_text": user_input,
                "classification": classification,
                "project_id": target_project_id,
                "chapter_id": target_chapter_id,
            },
            context={"history_size": len(self.conversation_history)},
        )
        if not normalize.get("success"):
            self.event_logger.emit(
                "lead",
                "aux_intake_normalizer_failed",
                "aux_subagent intake_normalizer 失败，回退旧归一化逻辑",
                {"error": normalize.get("error"), "project_id": target_project_id},
            )
            return None

        normalized = normalize.get("data", {})
        persist = self.intake_service.persist_normalized_intake(
            user_input=user_input,
            classification=classification,
            normalized=normalized,
            project_id=target_project_id,
            chapter_id=target_chapter_id,
        )
        if not persist.get("success"):
            self.event_logger.emit(
                "lead",
                "aux_intake_persist_failed",
                "IntakePersistenceService 写入失败，回退旧归一化逻辑",
                {"error": persist.get("error"), "project_id": target_project_id},
            )
            return None

        planner = self.aux_subagent.run(
            "planner_decider",
            payload={
                "user_text": user_input,
                "classification": classification,
                "normalized_facts": normalized,
                "project_state": {
                    "project_id": persist.get("project_id"),
                    "chapter_id": persist.get("chapter_id"),
                    "normalized_files": persist.get("normalized_files", []),
                    "missing": persist.get("missing", []),
                },
            },
            context={"history_size": len(self.conversation_history)},
        )
        if planner.get("success"):
            persist["planner_decision"] = planner.get("data", {})
            self.latest_planner_decision = planner.get("data", {})
            recommended = persist.get("recommended_next", {}) if isinstance(persist.get("recommended_next", {}), dict) else {}
            recommended["use_dynamic_plan"] = planner["data"].get("use_dynamic_plan", True)
            recommended["use_ai_planner"] = planner["data"].get("use_ai_planner", False)
            persist["recommended_next"] = recommended
        else:
            persist["planner_decision"] = {"success": False, "error": planner.get("error")}

        self.event_logger.emit(
            "lead",
            "intake_normalized_aux",
            f"已通过 aux_subagent 归一化用户输入：{persist.get('project_id')}",
            {"project_id": persist.get("project_id"), "files": persist.get("core_files", []) + persist.get("normalized_files", [])},
        )
        return persist

    def _normalize_user_intake(
        self,
        user_input: str,
        classification: Dict[str, Any],
        project_id: str,
        chapter_id: str,
    ) -> Dict[str, Any]:
        input_type = classification.get("input_type", "mixed_notes")
        target_project_id = (project_id or "").strip() or self._project_id_from_seed(user_input)
        target_chapter_id = (chapter_id or self._extract_chapter_id(user_input) or "ch01").strip()
        normalized = self._legacy_intake_to_normalized(user_input, classification, target_project_id, target_chapter_id)
        result = self.intake_service.persist_normalized_intake(
            user_input=user_input,
            classification=classification,
            normalized=normalized,
            project_id=target_project_id,
            chapter_id=target_chapter_id,
        )
        if not result.get("success"):
            return {
                "type": "intake_normalized",
                "success": False,
                "project_id": target_project_id,
                "chapter_id": target_chapter_id,
                "input_type": input_type,
                "classification": classification,
                "error": result.get("error", "unknown intake persistence error"),
            }
        result["source"] = "legacy_intake_service_fallback"
        self.event_logger.emit(
            "lead",
            "intake_normalized",
            f"已通过 IntakePersistenceService fallback 归一化用户输入：{target_project_id} ({input_type})",
            {"project_id": target_project_id, "input_type": input_type, "files": result.get("core_files", []) + result.get("normalized_files", [])},
        )
        return result

    def _legacy_intake_to_normalized(
        self,
        user_input: str,
        classification: Dict[str, Any],
        project_id: str,
        chapter_id: str,
    ) -> Dict[str, Any]:
        input_type = classification.get("input_type", "mixed_notes")
        facts: Dict[str, Any] = {
            "story_direction": "",
            "worldbuilding": "",
            "chapter_outlines": [],
            "characters": [],
            "environments": [],
            "script_draft": "",
        }
        if input_type in {"story_direction", "mixed_notes"}:
            facts["story_direction"] = user_input
        if input_type in {"worldbuilding", "mixed_notes"}:
            facts["worldbuilding"] = user_input
        if input_type in {"chapter_outline", "mixed_notes"}:
            facts["chapter_outlines"] = [{"chapter_id": chapter_id, "title": "用户提供章纲", "summary": user_input}]
        if input_type == "chapter_detail_outline":
            facts["chapter_outlines"] = [{"chapter_id": chapter_id, "title": "用户提供本章细纲", "summary": user_input}]
        if input_type == "script_draft":
            facts["script_draft"] = user_input
        if input_type in {"character_notes", "mixed_notes"}:
            for name in self._extract_entity_names(user_input, default_prefix="角色"):
                facts["characters"].append({"name": name, "role": "", "description": user_input, "visual_anchors": []})
        if input_type in {"environment_notes", "mixed_notes"}:
            for name in self._extract_entity_names(user_input, default_prefix="场景"):
                facts["environments"].append({"name": name, "description": user_input, "visual_anchors": []})
        missing_by_type = {
            "story_direction": ["story_bible", "chapter_outlines", "character_cards", "environment_cards", "chapter_script"],
            "worldbuilding": ["chapter_outlines", "character_cards", "environment_cards", "chapter_script"],
            "chapter_outline": ["story_bible", "character_cards", "environment_cards", "chapter_script"],
            "chapter_detail_outline": ["story_bible", "chapter_outlines", "character_cards", "environment_cards", "chapter_script"],
            "character_notes": ["story_bible", "chapter_outlines", "environment_cards", "chapter_script"],
            "environment_notes": ["story_bible", "chapter_outlines", "character_cards", "chapter_script"],
            "script_draft": ["diagnosis", "revision_plan", "facts_extraction"],
            "mixed_notes": ["review_missing_facts", "chapter_script"],
        }
        return {
            "project_id": project_id,
            "chapter_id": chapter_id,
            "facts": facts,
            "missing": missing_by_type.get(input_type, []),
            "recommended_next": {
                "task_type": "architect_delivery",
                "use_dynamic_plan": True,
                "use_ai_planner": input_type == "mixed_notes",
            },
        }

    @staticmethod
    def _extract_entity_names(text: str, default_prefix: str) -> List[str]:
        patterns = [
            rf"{default_prefix}[：: ]+([\u4e00-\u9fffA-Za-z0-9_\-]{{2,20}})",
            r"(?:主角|反派|配角|地点|场景)[：: ]+([\u4e00-\u9fffA-Za-z0-9_\-]{2,20})",
        ]
        names: List[str] = []
        for pattern in patterns:
            for match in re.finditer(pattern, text or ""):
                name = match.group(1).strip()
                if name and name not in names:
                    names.append(name)
        if not names and default_prefix in text:
            names.append(default_prefix)
        return names

    @staticmethod
    def _render_raw_user_input(user_input: str, classification: Dict[str, Any]) -> str:
        return "\n".join([
            "# 原始用户输入",
            "",
            f"**保存时间**：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "",
            "## 分类结果",
            "```json",
            json.dumps(classification, ensure_ascii=False, indent=2),
            "```",
            "",
            "## 原文",
            user_input,
            "",
        ])

    def _write_normalized_intake_files(
        self,
        project_id: str,
        chapter_id: str,
        user_input: str,
        classification: Dict[str, Any],
    ) -> List[str]:
        input_type = classification.get("input_type", "mixed_notes")
        normalized_files: List[str] = []

        if input_type in {"story_direction", "mixed_notes"}:
            path = f"{project_id}/story_direction.md"
            run_write(path, self._render_direct_story_direction(project_id, user_input, classification))
            normalized_files.append(path)
        if input_type in {"worldbuilding", "mixed_notes"}:
            path = f"{project_id}/story_bible.md"
            run_write(path, self._render_imported_story_bible(project_id, user_input, classification))
            normalized_files.append(path)
        if input_type in {"chapter_outline", "mixed_notes"}:
            path = f"{project_id}/chapter_outlines.md"
            run_write(path, self._render_imported_chapter_outlines(project_id, user_input, classification))
            normalized_files.append(path)
            for file_path, content in self._split_chapter_outline(project_id, user_input).items():
                run_write(file_path, content)
                normalized_files.append(file_path)
        if input_type == "chapter_detail_outline":
            path = f"{project_id}/chapters/{chapter_id}/outline.md"
            run_write(path, self._render_imported_chapter_detail(chapter_id, user_input, classification))
            normalized_files.append(path)
        if input_type in {"character_notes", "mixed_notes"}:
            for file_path, content in self._extract_character_note_files(project_id, user_input).items():
                run_write(file_path, content)
                normalized_files.append(file_path)
        if input_type in {"environment_notes", "mixed_notes"}:
            for file_path, content in self._extract_environment_note_files(project_id, user_input).items():
                run_write(file_path, content)
                normalized_files.append(file_path)
        if input_type == "script_draft":
            path = f"{project_id}/chapters/{chapter_id}/draft_input.md"
            run_write(path, self._render_script_draft(chapter_id, user_input, classification))
            normalized_files.append(path)
        return normalized_files

    @staticmethod
    def _render_direct_story_direction(project_id: str, user_input: str, classification: Dict[str, Any]) -> str:
        return "\n".join([
            "# 创作方向", "", f"**项目 ID**：{project_id}", f"**生成时间**：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", "",
            "## 用户已提供方向", user_input, "", "## 分类结果", "```json",
            json.dumps(classification, ensure_ascii=False, indent=2), "```", "",
            "## 后续建议", "基于本方向生成或补齐 `story_bible.md`、`chapter_outlines.md`、角色库和场景库。", "",
        ])

    @staticmethod
    def _render_imported_story_bible(project_id: str, user_input: str, classification: Dict[str, Any]) -> str:
        return "\n".join([
            f"# Story Bible: {project_id}", "", "## 来源", "用户直接提供或混合笔记中提取的世界观/故事设定。", "",
            "## 用户设定原文", user_input, "", "## 归一化状态", "- 已保留用户世界观原文。", "- 后续可由 Architect 基于本文件补齐规则、主题、视觉风格和章节规划。", "",
            "## 分类结果", "```json", json.dumps(classification, ensure_ascii=False, indent=2), "```", "",
        ])

    @staticmethod
    def _render_imported_chapter_outlines(project_id: str, user_input: str, classification: Dict[str, Any]) -> str:
        return "\n".join([
            f"# 全章节细纲: {project_id}", "", "## 来源", "用户已提供章纲，系统保留原主事件，不重新发明章节链路。", "",
            "## 用户章纲原文", user_input, "", "## 分类结果", "```json", json.dumps(classification, ensure_ascii=False, indent=2), "```", "",
            "## 后续约束", "- 正式剧本必须继承用户章纲。", "- 如需改动主事件，应先生成修改计划并让用户确认。", "",
        ])

    @staticmethod
    def _render_imported_chapter_detail(chapter_id: str, user_input: str, classification: Dict[str, Any]) -> str:
        return "\n".join([
            f"# {chapter_id} 用户细纲", "", "## 来源", "用户已提供本章细纲/事件节拍。", "",
            "## 用户细纲原文", user_input, "", "## 分类结果", "```json", json.dumps(classification, ensure_ascii=False, indent=2), "```", "",
            "## 后续约束", "正式剧本必须先读取本文件，不得跳过用户给定事件。", "",
        ])

    @staticmethod
    def _render_script_draft(chapter_id: str, user_input: str, classification: Dict[str, Any]) -> str:
        return "\n".join([
            f"# {chapter_id} 用户剧本草稿", "", "## 处理原则", "本文件是用户原稿，系统不得直接覆盖；后续应先生成诊断和修订计划。", "",
            "## 草稿原文", user_input, "", "## 分类结果", "```json", json.dumps(classification, ensure_ascii=False, indent=2), "```", "",
        ])

    def _split_chapter_outline(self, project_id: str, user_input: str) -> Dict[str, str]:
        matches = list(re.finditer(r"(?:第\s*([一二三四五六七八九十百\d]+)\s*章|chapter\s*(\d+))", user_input, flags=re.IGNORECASE))
        if not matches:
            return {f"{project_id}/chapters/ch01/outline.md": self._render_imported_chapter_detail("ch01", user_input, {"source": "chapter_outline"})}
        chapter_files: Dict[str, str] = {}
        for idx, match in enumerate(matches):
            start = match.start()
            end = matches[idx + 1].start() if idx + 1 < len(matches) else len(user_input)
            raw_num = match.group(1) or match.group(2) or str(idx + 1)
            chapter_id = f"ch{self._chapter_number_to_int(raw_num):02d}"
            chunk = user_input[start:end].strip()
            chapter_files[f"{project_id}/chapters/{chapter_id}/outline.md"] = self._render_imported_chapter_detail(chapter_id, chunk, {"source": "chapter_outline_split"})
        return chapter_files

    @staticmethod
    def _chapter_number_to_int(raw: str) -> int:
        if str(raw).isdigit():
            return int(raw)
        mapping = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
        text = str(raw).strip()
        if text in mapping:
            return mapping[text]
        if text.startswith("十") and len(text) > 1:
            return 10 + mapping.get(text[1:], 0)
        if "十" in text:
            left, right = text.split("十", 1)
            return mapping.get(left, 1) * 10 + mapping.get(right, 0)
        return 1

    @staticmethod
    def _safe_note_name(raw_name: str, fallback: str) -> str:
        name = re.sub(r"[\\/:*?\"<>|\n\r\t]+", "", raw_name).strip()
        return name[:30] or fallback

    def _extract_character_note_files(self, project_id: str, user_input: str) -> Dict[str, str]:
        names = re.findall(r"(?:主角|反派|配角|人物|角色)\s*[：:]\s*([^，,。；;\n]+)", user_input)
        if not names:
            names = ["用户角色设定"]
        files: Dict[str, str] = {}
        for idx, raw_name in enumerate(names, 1):
            name = self._safe_note_name(raw_name, f"用户角色设定{idx}")
            files[f"{project_id}/state/characters/{name}.md"] = "\n".join([
                f"# {name}", "", "## 来源", "用户输入的角色设定。", "", "## 用户原文", user_input, "",
                "## 待补齐项", "- 外观锚点", "- 角色动机", "- 禁止改动项", "",
            ])
        return files

    def _extract_environment_note_files(self, project_id: str, user_input: str) -> Dict[str, str]:
        names = re.findall(r"(?:场景|地点|环境)\s*[：:]\s*([^，,。；;\n]+)", user_input)
        if not names:
            names = ["用户场景设定"]
        files: Dict[str, str] = {}
        for idx, raw_name in enumerate(names, 1):
            name = self._safe_note_name(raw_name, f"用户场景设定{idx}")
            files[f"{project_id}/state/environments/{name}.md"] = "\n".join([
                f"# {name}", "", "## 来源", "用户输入的场景设定。", "", "## 用户原文", user_input, "",
                "## 待补齐项", "- 视觉锚点", "- 关键道具", "- 场景功能", "",
            ])
        return files

    @staticmethod
    def _render_intake_report(
        project_id: str,
        chapter_id: str,
        user_input: str,
        classification: Dict[str, Any],
        normalized_files: List[str],
    ) -> str:
        input_type = classification.get("input_type", "mixed_notes")
        missing_by_type = {
            "story_direction": ["story_bible.md", "chapter_outlines.md", "characters", "environments"],
            "worldbuilding": ["chapter_outlines.md", "characters", "environments"],
            "chapter_outline": ["story_bible.md", "characters", "environments"],
            "chapter_detail_outline": ["story_bible.md", "chapter_outlines.md", "characters", "environments"],
            "character_notes": ["story_bible.md", "chapter_outlines.md", "environments"],
            "environment_notes": ["story_bible.md", "chapter_outlines.md", "characters"],
            "script_draft": ["diagnosis", "revision_plan", "facts extraction"],
            "mixed_notes": ["人工确认或后续自动补齐未覆盖事实源"],
        }
        lines = [
            "# Intake Report", "", f"**项目 ID**：{project_id}", f"**章节 ID**：{chapter_id}",
            f"**输入类型**：{input_type}", f"**置信度**：{classification.get('confidence', '')}", "",
            "## 用户已提供", user_input[:2000], "", "## 已落盘事实源",
        ]
        lines.extend(f"- `{file_path}`" for file_path in normalized_files)
        lines.extend(["", "## 缺失/待补齐"])
        lines.extend(f"- {item}" for item in missing_by_type.get(input_type, []))
        lines.extend(["", "## 风险", "- 用户输入可能不是系统标准格式，后续生成必须优先读取原始输入与本报告。", "- 不应直接覆盖用户已提供的章纲、细纲或草稿。", ""])
        return "\n".join(lines)

    @staticmethod
    def _render_execution_plan(
        project_id: str,
        chapter_id: str,
        classification: Dict[str, Any],
        normalized_files: List[str],
    ) -> str:
        input_type = classification.get("input_type", "mixed_notes")
        plan_by_type = {
            "story_direction": ["执行 architect_concept_setup", "执行 architect_chapter_outline_setup", "补角色和场景", f"执行 architect_delivery {chapter_id}"],
            "worldbuilding": ["保留 story_bible.md", "执行 architect_chapter_outline_setup", "补角色和场景", f"执行 architect_delivery {chapter_id}"],
            "chapter_outline": ["保留 chapter_outlines.md 和章节 outline", "补 story_bible.md", "补角色和场景", f"执行 architect_delivery {chapter_id}"],
            "chapter_detail_outline": ["保留本章 outline.md", "补 chapter_outlines.md", "补 story_bible.md", "补角色和场景", f"执行 architect_delivery {chapter_id}"],
            "character_notes": ["保留角色设定", "补 story_bible.md", "补 chapter_outlines.md", "补场景", f"执行 architect_delivery {chapter_id}"],
            "environment_notes": ["保留场景设定", "补 story_bible.md", "补 chapter_outlines.md", "补角色", f"执行 architect_delivery {chapter_id}"],
            "script_draft": ["保存 draft_input.md", "诊断草稿", "抽取事实源", "生成 revision_plan.md", "用户确认后再改写"],
            "mixed_notes": ["保留所有已抽取事实源", "检查缺失项", "按缺失项补齐", f"设定齐全后执行 architect_delivery {chapter_id}"],
        }
        steps = plan_by_type.get(input_type, ["请用户补充目标"])
        lines = ["# Execution Plan", "", f"**项目 ID**：{project_id}", f"**输入类型**：{input_type}", "", "## 已有文件"]
        lines.extend(f"- `{file_path}`" for file_path in normalized_files)
        lines.extend(["", "## 执行步骤"])
        lines.extend(f"{idx}. {step}" for idx, step in enumerate(steps, 1))
        lines.extend(["", "## 不做的事", "- 不直接覆盖用户原始输入。", "- 不跳过事实源直接写正式 script.md。", "- 不重写用户已经提供的主事件，除非用户明确要求。", ""])
        return "\n".join(lines)

    @staticmethod
    def _extract_chapter_id(text: str) -> str:
        match = re.search(r"第\s*([一二三四五六七八九十百\d]+)\s*章|chapter\s*(\d+)", text, flags=re.IGNORECASE)
        if not match:
            return ""
        raw = match.group(1) or match.group(2) or "1"
        return f"ch{LeadAgent._chapter_number_to_int(raw):02d}"

    def _handle_pending_clarification_response(self, user_input: str) -> Optional[str]:
        """把用户对上一轮选择题的回答归纳成后续规划可消费的创作方向。"""
        if not self.pending_clarification:
            return None

        pending = self.pending_clarification
        self.pending_clarification = None
        aux_response = self._summarize_clarification_response_with_aux(pending, user_input)
        if aux_response is not None:
            return json.dumps(aux_response, ensure_ascii=False)

        direction = self._summarize_clarification_response(pending, user_input)
        persist_result = self._persist_story_direction(pending, direction)
        return json.dumps(
            {
                "type": "clarification_summary",
                "clarification_kind": pending.get("kind"),
                "original_input": pending.get("original_input", ""),
                "user_response": user_input,
                "story_direction": direction,
                "persist_result": persist_result,
                "next_step": (
                    "已归纳创作方向并尝试写入 story_direction.md。下一阶段应基于该方向生成 story_bible、chapter_outlines，"
                    "再设计 characters/environments，最后逐章写 script。"
                ),
            },
            ensure_ascii=False,
        )

    def _summarize_clarification_response_with_aux(self, pending: Dict[str, Any], user_input: str) -> Optional[Dict[str, Any]]:
        if self.aux_subagent is None or self.intake_service is None:
            return None
        project_id = (pending.get("project_id") or "").strip()
        classification = pending.get("classification", {}) if isinstance(pending.get("classification", {}), dict) else {}
        if pending.get("kind") == "story_direction" and classification.get("input_type") == "new_story":
            project_id = ""
        if not project_id:
            project_id = self._project_id_from_seed(pending.get("original_input", ""))

        summary = self.aux_subagent.run(
            "story_direction_summarizer",
            payload={
                "pending": pending,
                "user_response": user_input,
                "project_id": project_id,
            },
            context={"history_size": len(self.conversation_history)},
        )
        if not summary.get("success"):
            self.event_logger.emit(
                "lead",
                "aux_story_direction_failed",
                "aux_subagent story_direction_summarizer 失败，回退旧澄清归纳逻辑",
                {"error": summary.get("error"), "project_id": project_id},
            )
            return None

        summarized = summary.get("data", {})
        persist_result = self.intake_service.persist_story_direction(pending, summarized, project_id=project_id)
        if not persist_result.get("success"):
            self.event_logger.emit(
                "lead",
                "aux_story_direction_persist_failed",
                "IntakePersistenceService 写入 story_direction 失败，回退旧澄清归纳逻辑",
                {"error": persist_result.get("error"), "project_id": project_id},
            )
            return None

        self.event_logger.emit(
            "lead",
            "story_direction_written_aux",
            f"已通过 aux_subagent 写入创作方向：{persist_result.get('file')}",
            {"project_id": persist_result.get("project_id"), "file": persist_result.get("file")},
        )
        result = {
            "type": "clarification_summary",
            "clarification_kind": pending.get("kind"),
            "original_input": pending.get("original_input", ""),
            "user_response": user_input,
            "story_direction": summarized.get("story_direction", {}),
            "persist_result": persist_result,
            "recommended_next": summarized.get("recommended_next", {}) if isinstance(summarized.get("recommended_next", {}), dict) else {},
            "next_step": "已由 aux_subagent 归纳创作方向并写入 story_direction.md。下一阶段应基于该方向创建 Architect 交付任务。",
        }
        dispatch = self._auto_dispatch_architect_if_ready(
            persist_result.get("project_id", ""),
            summarized.get("recommended_next", {}),
        )
        if dispatch:
            result["auto_dispatch"] = dispatch
            result["next_step"] = f"已自动派发 Architect 任务（{dispatch.get('task_id')}），Architect 将按动态计划生成 story_bible → 章纲 → 角色卡 → 场景卡 → 剧本。"
        return result

    def _auto_dispatch_architect_if_ready(self, project_id: str, recommended_next: dict) -> Optional[dict]:
        """当 recommended_next 指向 architect_delivery 且 project_id 有效时，自动派发 Architect 任务。"""
        if not project_id:
            return None
        if not isinstance(recommended_next, dict):
            return None
        if recommended_next.get("task_type") != "architect_delivery":
            return None
        chapter_id = "ch01"
        try:
            result = self._start_architect_protocol(
                project_id=project_id,
                chapter_id=chapter_id,
                goal=f"基于 {project_id}/story_direction.md 生成完整创作产物（story_bible、章纲、角色卡、场景卡、剧本）",
            )
            self.event_logger.emit(
                "lead",
                "auto_dispatch_architect",
                f"已自动派发 Architect 任务：{result.get('task_id')}",
                {"project_id": project_id, "chapter_id": chapter_id, "task_id": result.get("task_id")},
            )
            return result
        except Exception as e:
            self.event_logger.emit(
                "lead",
                "auto_dispatch_architect_error",
                f"自动派发 Architect 任务失败：{type(e).__name__}: {e}",
                {"project_id": project_id},
            )
            return None

    def _persist_story_direction(self, pending: Dict[str, Any], direction: Dict[str, Any]) -> Dict[str, Any]:
        """把新故事方向写入项目事实源，供后续 story_bible/chapter_outlines 阶段读取。"""
        if pending.get("kind") != "story_direction":
            return {"success": False, "skipped": True, "reason": "not story_direction"}

        project_id = (pending.get("project_id") or "").strip()
        classification = pending.get("classification", {}) if isinstance(pending.get("classification", {}), dict) else {}
        if pending.get("kind") == "story_direction" and classification.get("input_type") == "new_story":
            project_id = ""
        if not project_id:
            project_id = self._project_id_from_seed(direction.get("seed", ""))

        try:
            self._ensure_story_project_structure(project_id, direction.get("seed", ""))
            content = self._render_story_direction_markdown(project_id, pending, direction)
            result = run_write(f"{project_id}/story_direction.md", content)
            self.event_logger.emit(
                "lead",
                "story_direction_written",
                f"已写入创作方向：{project_id}/story_direction.md",
                {"project_id": project_id, "file": f"{project_id}/story_direction.md"},
            )
            return {"success": True, "project_id": project_id, "file": f"{project_id}/story_direction.md", **result}
        except Exception as e:
            self.event_logger.emit(
                "lead",
                "story_direction_write_error",
                f"创作方向写入失败：{type(e).__name__}: {e}",
                {"project_id": project_id},
            )
            return {"success": False, "project_id": project_id, "error": str(e), "error_type": type(e).__name__}

    @staticmethod
    def _project_id_from_seed(seed: str) -> str:
        return f"story_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    @staticmethod
    def _ensure_story_project_structure(project_id: str, seed: str) -> None:
        project_path = safe_path(project_id)
        dirs = [
            project_path / "chapters",
            project_path / "chapters" / "ch01",
            project_path / "state" / "characters",
            project_path / "state" / "environments",
            project_path / "qa",
            project_path / "feedback",
            project_path / "plans",
        ]
        for directory in dirs:
            directory.mkdir(parents=True, exist_ok=True)

        brief_path = project_path / "brief.md"
        if seed and not brief_path.exists():
            brief_path.write_text(
                f"# 项目简介\n\n**创建时间**：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n{seed}\n",
                encoding="utf-8",
            )

    @staticmethod
    def _render_story_direction_markdown(project_id: str, pending: Dict[str, Any], direction: Dict[str, Any]) -> str:
        selections = direction.get("selections", []) if isinstance(direction.get("selections", []), list) else []
        lines = [
            "# 创作方向",
            "",
            f"**项目 ID**：{project_id}",
            f"**生成时间**：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "",
            "## 故事种子",
            direction.get("seed", "") or pending.get("original_input", ""),
            "",
            "## 用户选择 / 补充",
            direction.get("raw_user_response", ""),
            "",
            "## 选择题归纳",
        ]
        if selections:
            for item in selections:
                question = item.get("question", "")
                answer = item.get("user_answer", "") or "未明确回答"
                lines.append(f"- **{question}**：{answer}")
        else:
            lines.append("- 未捕获到结构化选择题，使用用户补充作为方向依据。")

        lines.extend([
            "",
            "## 分类结果",
            "```json",
            json.dumps(direction.get("classification", {}), ensure_ascii=False, indent=2),
            "```",
            "",
            "## 后续工作流建议",
            "1. 基于本文件生成 `story_bible.md`。",
            "2. 基于 `story_bible.md` 生成全章节 `chapter_outlines.md` 和 `chapters/chXX/outline.md`。",
            "3. 基于章节细纲生成 `state/characters/*.md` 和 `state/environments/*.md`。",
            "4. 设定齐全后，从 `chapters/ch01/script.md` 开始逐章创作。",
            "",
        ])
        return "\n".join(lines)

    def _summarize_clarification_response(self, pending: Dict[str, Any], user_input: str) -> Dict[str, Any]:
        data = pending.get("questions", {}) if isinstance(pending.get("questions", {}), dict) else {}
        result = data.get("result", {}) if isinstance(data.get("result", {}), dict) else {}
        questions = result.get("questions", []) if isinstance(result.get("questions", []), list) else []
        selections: List[Dict[str, Any]] = []
        for index, question in enumerate(questions, 1):
            if not isinstance(question, dict):
                continue
            selections.append({
                "id": question.get("id", f"q{index}"),
                "question": question.get("question", ""),
                "options": question.get("options", []),
                "user_answer": self._extract_answer_for_question(user_input, index),
            })
        return {
            "source": "pending_clarification",
            "summary": self._build_direction_summary(pending, user_input, selections),
            "seed": pending.get("original_input", ""),
            "classification": pending.get("classification", {}),
            "selections": selections,
            "raw_user_response": user_input,
        }

    @staticmethod
    def _extract_answer_for_question(user_input: str, index: int) -> str:
        text = user_input.strip()
        patterns = [
            rf"(?:^|[\\s,，;；]){index}[\\.、:：]?\\s*([^,，;；]+)",
            rf"q{index}[\\.、:：=]?\\s*([^,，;；]+)",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match:
                return match.group(1).strip()
        return text if index == 1 else ""

    @staticmethod
    def _build_direction_summary(pending: Dict[str, Any], user_input: str, selections: List[Dict[str, Any]]) -> str:
        lines = [f"故事种子：{pending.get('original_input', '')}", f"用户选择/补充：{user_input}"]
        answered = [s for s in selections if s.get("user_answer")]
        if answered:
            lines.append("已对应到选择题：")
            for item in answered:
                lines.append(f"- {item.get('question')}: {item.get('user_answer')}")
        return "\n".join(lines)

    def _start_pending_director_stage(self, chapter_id: Optional[str] = None) -> str:
        offer = self.pending_director_offer or {}
        project_id = offer.get("project_id")
        selected_chapter = chapter_id or offer.get("chapter_id")
        if not project_id or not selected_chapter:
            inferred_project, inferred_chapter = self._infer_active_project_chapter()
            project_id = project_id or inferred_project
            selected_chapter = selected_chapter or inferred_chapter
        if not project_id or not selected_chapter:
            return "当前没有可进入 Director 阶段的 Architect 交付。请先完成章节剧本。"
        script_path = offer.get("script_path") or f"{project_id}/chapters/{selected_chapter}/script.md"
        try:
            safe_path(script_path)
            run_read(script_path)
        except Exception as exc:
            return f"无法进入 Director 阶段：剧本文件不可用 `{script_path}`。{type(exc).__name__}: {exc}"
        payload = {
            "project_id": project_id,
            "chapter_id": selected_chapter,
            "script_path": script_path,
            "output_path": f"{project_id}/chapters/{selected_chapter}/storyboard.md",
            "architect_handoff": offer.get("architect_handoff", {}),
            "available_character_files": self._list_project_markdown_files(project_id, "state/characters"),
            "available_environment_files": self._list_project_markdown_files(project_id, "state/environments"),
            "user_confirmation": {"confirmed": True, "command": "/director"},
        }
        brief = None
        if self.aux_subagent is not None:
            result = self.aux_subagent.run("director_task_brief_designer", payload=payload, context={"history_size": len(self.conversation_history)})
            if result.get("success"):
                brief = result.get("data", {})
            else:
                self.event_logger.emit("lead", "director_brief_design_failed", "director-task-brief-designer 失败，使用规则 fallback brief", {"error": result.get("error"), "project_id": project_id, "chapter_id": selected_chapter})
        if not brief:
            brief = self._fallback_director_task_brief(project_id, selected_chapter, script_path)
        validation_error = self._validate_director_task_brief(brief, project_id, selected_chapter)
        if validation_error:
            return f"Director brief 校验失败：{validation_error}"
        task_id = self.task_manager.create(
            title=f"Director 交付 {selected_chapter}",
            assignee="director_bot",
            metadata=brief,
        )
        self.pending_director_offer = None
        self.event_logger.emit("lead", "task_created", "已创建 Director 三阶段 pipeline 任务", {"task_id": task_id, "project_id": project_id, "chapter_id": selected_chapter, "task_type": "director_delivery"})
        return f"已进入 Director 分镜阶段，创建任务：{task_id}。Director 将生成 storyboard.md、selected_context.json、storyboard_plan.json 和 pipeline_status.json。"

    @staticmethod
    def _fallback_director_task_brief(project_id: str, chapter_id: str, script_path: str) -> Dict[str, Any]:
        return {
            "protocol_version": "v2",
            "task_type": "director_delivery",
            "project_id": project_id,
            "chapter_id": chapter_id,
            "script_path": script_path,
            "output_path": f"{project_id}/chapters/{chapter_id}/storyboard.md",
            "inputs": [script_path],
            "required_inputs": [script_path],
            "input_discovery_mode": "progressive_bounded",
            "pipeline": ["director-context-planner", "director-storyboard-planner", "storyboard-draft-writer"],
            "expected_outputs": {
                "selected_context": f"{project_id}/chapters/{chapter_id}/director_plan/selected_context.json",
                "storyboard_plan": f"{project_id}/chapters/{chapter_id}/director_plan/storyboard_plan.json",
                "pipeline_status": f"{project_id}/chapters/{chapter_id}/director_plan/pipeline_status.json",
                "storyboard": f"{project_id}/chapters/{chapter_id}/storyboard.md",
            },
            "quality_bar": ["严格对应 script.md", "视觉锚点一致", "场景道具与氛围一致", "镜头语言清楚"],
            "deliverables": [f"{project_id}/chapters/{chapter_id}/storyboard.md"],
            "required_deliverables": [f"{project_id}/chapters/{chapter_id}/storyboard.md"],
            "submission_target": "lead",
            "requires_user_review": True,
        }

    @staticmethod
    def _validate_director_task_brief(brief: Dict[str, Any], project_id: str, chapter_id: str) -> str:
        if brief.get("task_type") != "director_delivery":
            return "task_type must be director_delivery"
        if brief.get("project_id") != project_id:
            return "project_id mismatch"
        if brief.get("chapter_id") != chapter_id:
            return "chapter_id mismatch"
        allowed_pipeline = ["director-context-planner", "director-storyboard-planner", "storyboard-draft-writer"]
        if any(item not in allowed_pipeline for item in brief.get("pipeline", [])):
            return "pipeline contains unsupported item"
        for key in ["script_path", "output_path"]:
            path = brief.get(key, "")
            if not path:
                return f"missing {key}"
            try:
                safe_path(path)
            except Exception as exc:
                return f"invalid {key}: {exc}"
        return ""

    def _infer_active_project_chapter(self) -> Tuple[str, str]:
        project_id = ""
        chapter_id = ""

        all_tasks = list(self.task_manager.tasks.values())
        all_tasks.sort(key=lambda x: x.get("updated_at", ""), reverse=True)
        # 优先从最近任务推断当前项目/章节；这比从文件系统猜测更贴近正在执行的流程。
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
        return FeedbackOrchestrator.is_feedback_text(user_input)

    def _enqueue_architect_feedback_task(self, project_id: str, chapter_id: str, instruction: Dict[str, Any]) -> Dict[str, Any]:
        follow_up = FeedbackOrchestrator.build_architect_follow_up(project_id, chapter_id, instruction)
        if not follow_up.get("success"):
            return follow_up
        task_id = self.task_manager.create(
            title=follow_up["title"],
            assignee=follow_up["assignee"],
            metadata=follow_up["metadata"],
        )
        message = dict(follow_up.get("message", {}))
        message["task_id"] = task_id
        self.message_bus.send("lead", "architect_bot", message)
        return {"success": True, "task_id": task_id}

    @staticmethod
    def _infer_instruction_target_type(target: str) -> str:
        return FeedbackOrchestrator.infer_instruction_target_type(target)

    @staticmethod
    def _extract_name_from_target(target: str) -> str:
        return FeedbackOrchestrator.extract_name_from_target(target)

    def _emit_stage_delivery(self, stage: str, files: List[str], summary: str = ""):
        event = render_stage_delivery(stage=stage, files=files, summary=summary)
        self.feedback_events.append(event)
        self.conversation_history.append({"role": "assistant", "content": event})

    def _normalize_qa_result(self, payload: Dict[str, Any], task_metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        metadata = task_metadata if isinstance(task_metadata, dict) else {}
        expected_outputs = metadata.get("expected_outputs", {}) if isinstance(metadata.get("expected_outputs", {}), dict) else {}
        review_context = payload.get("review_context") if isinstance(payload.get("review_context"), dict) else metadata.get("review_context", {})
        if not isinstance(review_context, dict):
            review_context = {}
        project_id = payload.get("project_id") or metadata.get("project_id") or review_context.get("project_id", "")
        chapter_id = payload.get("chapter_id") or metadata.get("chapter_id") or review_context.get("chapter_id", "")
        final_verdict = str(payload.get("final_verdict") or payload.get("verdict") or "WARNING").upper()
        if final_verdict not in {"PASS", "WARNING", "FAIL"}:
            final_verdict = "WARNING"
        report_kind = payload.get("report_kind") or payload.get("review_type") or review_context.get("event_type") or "mixed_review"
        issues = payload.get("issues") if isinstance(payload.get("issues"), list) else []
        report_file = payload.get("report_file") or expected_outputs.get("report_file") or (f"{project_id}/qa/latest_report.md" if project_id else "qa/latest_report.md")
        summary = payload.get("summary") or payload.get("summary_for_user") or payload.get("output") or "QA 返回了非标准结果，Lead 已归一化为 WARNING 结构。"
        normalized = dict(payload)
        normalized.update({
            "type": "verdict",
            "from_role": payload.get("from_role", "qa"),
            "project_id": project_id,
            "chapter_id": chapter_id,
            "task_type": "qa_review",
            "report_kind": report_kind,
            "final_verdict": final_verdict,
            "summary": summary,
            "summary_for_user": payload.get("summary_for_user") or summary,
            "issues": issues,
            "report_file": report_file,
            "review_context": review_context,
            "recommended_actions": payload.get("recommended_actions") if isinstance(payload.get("recommended_actions"), list) else [],
            "target_files": self._extract_target_files_from_qa_payload(payload),
            "requires_architect_follow_up": bool(payload.get("requires_architect_follow_up", False)),
            "normalized_by_lead": payload.get("type") != "verdict" or final_verdict != payload.get("final_verdict"),
        })
        return normalized

    def _extract_target_files_from_qa_payload(self, payload: Dict[str, Any]) -> List[str]:
        paths: List[str] = []

        def add_candidate(value: Any):
            if isinstance(value, str) and value.strip():
                paths.append(value.strip())
            elif isinstance(value, dict):
                for key in ("path", "file", "file_path", "artifact", "artifact_path"):
                    candidate = value.get(key)
                    if isinstance(candidate, str) and candidate.strip():
                        paths.append(candidate.strip())
                for key in ("paths", "files", "target_files", "affected_files", "recommended_files"):
                    nested = value.get(key)
                    if isinstance(nested, list):
                        for item in nested:
                            add_candidate(item)
            elif isinstance(value, list):
                for item in value:
                    add_candidate(item)

        for key in ("target_files", "affected_files", "recommended_files", "revised_files", "direct_targets", "possible_impacts"):
            add_candidate(payload.get(key))
        for key in ("impact_analysis", "result", "data"):
            nested = payload.get(key)
            if isinstance(nested, dict):
                for nested_key in ("target_files", "affected_files", "recommended_files", "direct_targets", "possible_impacts"):
                    add_candidate(nested.get(nested_key))
        deduped: List[str] = []
        for path in paths:
            if path not in deduped:
                deduped.append(path)
        return deduped

    def _collect_revision_snapshot(self, project_id: str, chapter_id: str, target_files: Optional[List[str]] = None) -> Dict[str, Any]:
        files = self._normalize_revision_target_files(project_id, chapter_id, target_files or [])
        contents: Dict[str, str] = {}
        missing: List[str] = []
        for file_path in files:
            path = safe_path(file_path)
            if not path.exists() or not path.is_file():
                missing.append(file_path)
                continue
            try:
                contents[file_path] = run_read(file_path)
            except Exception:
                missing.append(file_path)
        return {"contents": contents, "missing": missing, "files": files}

    def _normalize_revision_target_files(self, project_id: str, chapter_id: str, target_files: List[str]) -> List[str]:
        files = [path for path in target_files if isinstance(path, str) and path.strip()]
        if not files:
            files = [
                f"{project_id}/story_bible.md",
                f"{project_id}/chapter_outlines.md",
            ]
            if chapter_id:
                files.extend([
                    f"{project_id}/chapters/{chapter_id}/outline.md",
                    f"{project_id}/chapters/{chapter_id}/script.md",
                ])
            files.extend(self._list_project_markdown_files(project_id, "state/characters"))
            files.extend(self._list_project_markdown_files(project_id, "state/environments"))
        deduped: List[str] = []
        for file_path in files:
            if file_path not in deduped:
                deduped.append(file_path)
        return deduped

    def _attach_after_snapshot_to_pending_revision(self, submission: Dict[str, Any], project_id: str, chapter_id: str):
        if not self.pending_revision:
            return
        revised_files = submission.get("revised_files") or submission.get("updated_files") or submission.get("deliverables") or self.pending_revision.get("target_files", [])
        if not isinstance(revised_files, list):
            revised_files = []
        if not revised_files:
            before = self.pending_revision.get("artifact_contents_before", {}) if isinstance(self.pending_revision.get("artifact_contents_before"), dict) else {}
            revised_files = list(before.get("contents", {}).keys())
        self.pending_revision["revised_files"] = revised_files
        self.pending_revision["revision_summary"] = submission.get("revision_summary") or submission.get("summary") or {}
        self.pending_revision["artifact_contents_after"] = self._collect_revision_snapshot(project_id, chapter_id, revised_files)

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
                assignee = tool_input.get("assignee", "")
                metadata = tool_input.get("metadata", {}) if isinstance(tool_input.get("metadata"), dict) else {}
                if assignee == "architect_bot" and not metadata.get("task_type"):
                    return {
                        "success": False,
                        "error": "不要用 create_task 给 architect_bot 派发创作任务。请改用 start_architect_protocol(project_id, chapter_id, goal)，它会自动设置完整的协议元数据。",
                    }
                tool_input = self._maybe_enable_architect_dynamic_plan_for_task(tool_input)
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

                target_type = tool_input.get("target_type") or self.feedback_orchestrator.infer_instruction_target_type(instruction.get("target", ""))
                target_name = tool_input.get("target_name") or self.feedback_orchestrator.extract_name_from_target(instruction.get("target", ""))
                if not target_name:
                    return {"success": False, "error": "无法从 instruction.target 推断 target_name，请显式提供 target_name"}

                from feedback_loop import apply_modification_instruction

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

    def _run_with_timeout(self, label: str, func, *args, timeout_seconds: int = 5, **kwargs):
        """用短超时包住后台/消息/任务管理器调用，避免慢 IO 或锁等待卡住 Lead 主流程。"""
        started_at = time.time()
        self.event_logger.emit("lead", "timeout_call_start", f"开始执行：{label}", {"timeout_seconds": timeout_seconds})
        executor = ThreadPoolExecutor(max_workers=1)
        future = executor.submit(func, *args, **kwargs)
        try:
            result = future.result(timeout=timeout_seconds)
            elapsed = round(time.time() - started_at, 3)
            self.event_logger.emit("lead", "timeout_call_done", f"执行完成：{label}, elapsed={elapsed}s")
            executor.shutdown(wait=False, cancel_futures=True)
            return result
        except FutureTimeoutError:
            elapsed = round(time.time() - started_at, 3)
            future.cancel()
            executor.shutdown(wait=False, cancel_futures=True)
            self.event_logger.emit("lead", "timeout_call_timeout", f"执行超时：{label}, elapsed={elapsed}s", {"timeout_seconds": timeout_seconds})
            raise TimeoutError(f"{label} timed out after {timeout_seconds}s")
        except Exception as e:
            elapsed = round(time.time() - started_at, 3)
            executor.shutdown(wait=False, cancel_futures=True)
            self.event_logger.emit("lead", "timeout_call_error", f"执行异常：{label}, {type(e).__name__}: {e}", {"elapsed": elapsed})
            raise

    def _drain_notifications(self):
        """检查后台任务通知；任何慢响应或异常都只记录日志，不阻塞用户主流程。"""
        try:
            notifications = self._run_with_timeout(
                "background_manager.drain_notifications",
                self.background_manager.drain_notifications,
                timeout_seconds=5,
            )
        except Exception as e:
            self.event_logger.emit("lead", "drain_notifications_error", f"后台通知读取失败：{type(e).__name__}: {e}")
            return

        self.event_logger.emit("lead", "drain_notifications_start", f"开始处理后台通知：count={len(notifications)}")
        for index, n in enumerate(notifications, 1):
            try:
                self.event_logger.emit("lead", "drain_notification_item", f"处理后台通知 {index}/{len(notifications)}", {"notification": n})
                self.conversation_history.append({
                    "role": "user",
                    "content": f"[系统通知] 后台任务 {n.get('task_id')} 完成: {json.dumps(n, ensure_ascii=False)}"
                })
            except Exception as e:
                self.event_logger.emit("lead", "drain_notification_item_error", f"后台通知处理失败：{type(e).__name__}: {e}", {"notification": n})
        self.event_logger.emit("lead", "drain_notifications_done", f"后台通知处理结束：count={len(notifications)}")

    def _drain_agent_results(self):
        """读取来自各 agent 的结果回传；消息总线和任务管理器调用全部限制在 5 秒内。"""
        try:
            messages = self._run_with_timeout(
                "message_bus.read_inbox(lead)",
                self.message_bus.read_inbox,
                "lead",
                mark_read=True,
                timeout_seconds=5,
            )
        except Exception as e:
            self.event_logger.emit("lead", "drain_agent_results_error", f"Lead inbox 读取失败：{type(e).__name__}: {e}")
            return

        self.event_logger.emit("lead", "drain_agent_results_start", f"开始处理 Agent 消息：count={len(messages)}")
        for index, msg in enumerate(messages, 1):
            try:
                payload = msg.get("message", {})
                payload_type = payload.get("type")
                self.event_logger.emit("lead", "drain_agent_message", f"处理 Agent 消息 {index}/{len(messages)}：type={payload_type}, from={msg.get('from')}", {"payload": payload})
                if payload_type == "task_result":
                    task_id = payload.get("task_id")
                    task = self._run_with_timeout(
                        f"task_manager.get({task_id})",
                        self.task_manager.get,
                        task_id,
                        timeout_seconds=5,
                    ) if task_id else None
                    if task_id and task is not None:
                        new_status = "done" if payload.get("success") else "error"
                        self._run_with_timeout(
                            f"task_manager.update({task_id}, {new_status})",
                            self.task_manager.update,
                            task_id,
                            new_status,
                            payload,
                            timeout_seconds=5,
                        )
                        metadata = task.get("metadata", {}) if isinstance(task.get("metadata", {}), dict) else {}
                        if metadata.get("task_type") == "qa_review":
                            normalized_qa = self._normalize_qa_result(payload, metadata)
                            self.submissions["qa"][normalized_qa.get("chapter_id", "")] = normalized_qa
                            if self.pending_revision and self.pending_revision.get("qa_task_id") == task_id:
                                self.pending_revision["qa_report"] = normalized_qa
                                if normalized_qa.get("report_file"):
                                    self.pending_revision["qa_report_file"] = normalized_qa.get("report_file")
                                self.pending_revision["target_files"] = normalized_qa.get("target_files", [])
                                output = payload.get("output")
                                if isinstance(output, str) and output.strip():
                                    self.pending_revision["qa_output"] = output
                        if metadata.get("task_type") == "artifact_revision" and payload.get("success"):
                            revision_submission = {
                                "architect_task_result": payload,
                                "revised_files": metadata.get("target_files", []),
                                "revision_summary": payload.get("output", ""),
                            }
                            self._attach_after_snapshot_to_pending_revision(revision_submission, metadata.get("project_id", ""), metadata.get("chapter_id", ""))
                            self._create_qa_review_from_event(
                                "architect_revision_completed",
                                project_id=metadata.get("project_id", ""),
                                chapter_id=metadata.get("chapter_id", ""),
                                recent_submission=revision_submission,
                                pending_revision=self.pending_revision,
                            )
                    self.conversation_history.append({
                        "role": "user",
                        "content": (
                            f"[Agent结果] {payload.get('assignee', msg.get('from'))} 完成任务 "
                            f"{payload.get('title', '')}: {json.dumps(payload, ensure_ascii=False)}"
                        )
                    })
                elif payload_type in {"handoff", "submission", "verdict"}:
                    self._run_with_timeout(
                        f"_route_protocol_message({payload_type})",
                        self._route_protocol_message,
                        msg.get("from", "unknown"),
                        payload,
                        timeout_seconds=5,
                    )
                else:
                    self.conversation_history.append({
                        "role": "user",
                        "content": f"[Agent消息] {json.dumps(msg, ensure_ascii=False)}"
                    })
            except Exception as e:
                self.event_logger.emit("lead", "drain_agent_message_error", f"Agent 消息处理失败：{type(e).__name__}: {e}", {"message": msg})
        self.event_logger.emit("lead", "drain_agent_results_done", f"Agent 消息处理结束：count={len(messages)}")

    def _maybe_enable_architect_dynamic_plan_for_task(self, tool_input: Dict[str, Any]) -> Dict[str, Any]:
        if tool_input.get("assignee") != "architect_bot":
            return tool_input
        metadata = tool_input.get("metadata", {}) if isinstance(tool_input.get("metadata", {}), dict) else {}
        updated_metadata = self._enable_architect_dynamic_plan_metadata(metadata)
        if updated_metadata is metadata:
            return tool_input
        updated = dict(tool_input)
        updated["metadata"] = updated_metadata
        return updated

    @staticmethod
    def _enable_architect_dynamic_plan_metadata(metadata: Dict[str, Any]) -> Dict[str, Any]:
        task_type = metadata.get("task_type")
        if task_type != "architect_delivery":
            return metadata
        if metadata.get("revision") or metadata.get("instruction"):
            return metadata
        project_id = metadata.get("project_id", "")
        chapter_id = metadata.get("chapter_id", "")
        updated = dict(metadata)
        updated.setdefault("use_dynamic_plan", True)
        updated.setdefault("planning_mode", "architect_dynamic_v1")
        updated.setdefault("goal_type", "create_chapter_script")
        if chapter_id and not updated.get("chapter_ids"):
            updated["chapter_ids"] = [chapter_id]
        if not updated.get("target_artifacts"):
            updated["target_artifacts"] = [
                "story_bible",
                "chapter_outlines",
                "chapter_outline",
                "character_cards",
                "environment_cards",
                "chapter_script",
            ]
        planner_decision = updated.get("planner_decision", {}) if isinstance(updated.get("planner_decision", {}), dict) else {}
        if "use_ai_planner" not in updated and isinstance(planner_decision.get("use_ai_planner"), bool):
            updated["use_ai_planner"] = planner_decision["use_ai_planner"]
        if project_id and chapter_id:
            updated.setdefault("required_deliverables", [f"{project_id}/chapters/{chapter_id}/script.md"])
            updated.setdefault("deliverables", [f"{project_id}/chapters/{chapter_id}/script.md"])
        return updated

    def _design_architect_plan_with_aux(self, task_metadata: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if not task_metadata.get("use_ai_planner"):
            return None
        if self.aux_subagent is None:
            return None
        project_id = task_metadata.get("project_id", "")
        chapter_ids = task_metadata.get("chapter_ids") or ([task_metadata.get("chapter_id")] if task_metadata.get("chapter_id") else [])
        if isinstance(chapter_ids, str):
            chapter_ids = [chapter_ids]
        goal = {
            "goal_type": task_metadata.get("goal_type", "create_chapter_script"),
            "project_id": project_id,
            "chapter_ids": chapter_ids,
            "target_artifacts": task_metadata.get("target_artifacts", []),
        }
        if task_metadata.get("requires_user_approval") is not None:
            goal["requires_user_approval"] = bool(task_metadata.get("requires_user_approval"))
        project_state = self.project_state_scanner.scan(project_id, chapter_ids)
        result = self.aux_subagent.run(
            "architect_plan_designer",
            payload={
                "task_metadata": task_metadata,
                "goal": goal,
                "project_state": project_state,
                "artifact_contracts": ARCHITECT_ARTIFACT_CONTRACTS,
                "planner_decision": task_metadata.get("planner_decision", {}) if isinstance(task_metadata.get("planner_decision", {}), dict) else {},
            },
            context={"history_size": len(self.conversation_history)},
        )
        if not result.get("success"):
            self.event_logger.emit(
                "lead",
                "architect_plan_design_failed",
                "aux_subagent architect_plan_designer 失败，将由 PlanManager 规则 plan fallback",
                {"error": result.get("error"), "project_id": project_id, "chapter_ids": chapter_ids},
            )
            return None
        self.event_logger.emit(
            "lead",
            "architect_plan_designed_aux",
            "已通过 aux_subagent 生成 Architect proposed_plan",
            {"project_id": project_id, "chapter_ids": chapter_ids, "steps": len(result.get("data", {}).get("steps", []))},
        )
        return result.get("data", {})

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
        if self.latest_planner_decision:
            protocol["planner_decision"] = self.latest_planner_decision
        protocol = self._enable_architect_dynamic_plan_metadata(protocol)
        proposed_plan = self._design_architect_plan_with_aux(protocol)
        if proposed_plan:
            protocol["proposed_plan"] = proposed_plan
            protocol["proposed_plan_source"] = "aux_subagent.architect_plan_designer"
        task_id = self.task_manager.create(
            title=f"Architect 交付 {chapter_id}",
            assignee="architect_bot",
            metadata=protocol,
        )
        self.event_logger.emit("lead", "task_created", f"已创建 Architect 任务：{task_id}", {"task_id": task_id, "project_id": project_id, "chapter_id": chapter_id, "task_type": "architect_delivery"})
        return {"success": True, "task_id": task_id, "protocol": protocol}

    def _create_qa_review_from_event(
        self,
        event_type: str,
        project_id: str,
        chapter_id: str = "",
        raw_user_text: str = "",
        recent_submission: Optional[Dict[str, Any]] = None,
        pending_revision: Optional[Dict[str, Any]] = None,
    ) -> Optional[str]:
        """Create a generic qa_review task from an event-generated QA brief."""
        if not project_id:
            self.event_logger.emit("lead", "qa_review_skipped", "缺少 project_id，无法创建 QA 任务", {"event_type": event_type})
            return None
        if self.aux_subagent is None:
            self.event_logger.emit("lead", "qa_review_skipped", "aux_subagent 不可用，无法生成 QA brief", {"event_type": event_type, "project_id": project_id})
            return None

        chapter_ids = [chapter_id] if chapter_id else []
        artifact_index = self.project_state_scanner.scan(project_id, chapter_ids)
        payload = {
            "event": {
                "event_type": event_type,
                "source_agent": "lead",
                "raw_user_text": raw_user_text or "",
            },
            "project_context": {
                "project_id": project_id,
                "chapter_id": chapter_id or "",
                "artifact_index": artifact_index,
                "recent_submission": recent_submission or {},
                "pending_revision": pending_revision or self.pending_revision or {},
            },
            "system_policy": {
                "qa_task_type": "qa_review",
                "code_should_not_decide_business_goal": True,
                "qa_bot_selects_skills": True,
            },
        }
        result = self.aux_subagent.run(
            "qa_task_brief_designer",
            payload=payload,
            context={"history_size": len(self.conversation_history)},
        )
        if not result.get("success"):
            self.event_logger.emit(
                "lead",
                "qa_brief_design_failed",
                "qa-task-brief-designer 失败，未创建 QA 任务",
                {"event_type": event_type, "project_id": project_id, "chapter_id": chapter_id, "error": result.get("error")},
            )
            return None

        qa_task = result.get("data", {})
        qa_task.setdefault("task_type", "qa_review")
        qa_task.setdefault("project_id", project_id)
        qa_task.setdefault("chapter_id", chapter_id or "")
        expected_outputs = qa_task.setdefault("expected_outputs", {})
        report_file = expected_outputs.get("report_file") or f"{project_id}/qa/latest_report.md"
        expected_outputs["report_file"] = report_file
        expected_outputs.setdefault("machine_summary", True)
        expected_outputs.setdefault("lead_message_type", "verdict")
        expected_outputs.setdefault("allowed_final_verdict", ["PASS", "WARNING", "FAIL"])
        expected_outputs.setdefault("required_fields", [
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
        ])
        qa_task.setdefault("required_deliverables", [report_file])
        task_id = self.task_manager.create(
            title=f"QA Review {chapter_id or project_id}",
            assignee="qa_bot",
            metadata=qa_task,
        )
        self.event_logger.emit(
            "lead",
            "task_created",
            "已根据事件创建通用 QA 任务",
            {
                "task_id": task_id,
                "event_type": event_type,
                "project_id": project_id,
                "chapter_id": chapter_id,
                "task_type": "qa_review",
            },
        )
        return task_id

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
            self.pending_director_offer = {
                "project_id": project_id,
                "chapter_id": chapter_id,
                "architect_handoff": payload,
                "script_path": (payload.get("deliverables") or [f"{project_id}/chapters/{chapter_id}/script.md"])[0],
                "revision": is_revision,
            }
            self.conversation_history.append({
                "role": "assistant",
                "content": (
                    f"Architect 已完成 {chapter_id} 剧本交付。\n"
                    f"是否进入下一阶段，让 Director 把 `{chapter_id}` 转成分镜脚本？\n\n"
                    f"可输入 `/director` 进入 Director 分镜阶段，或 `/director {chapter_id}` 指定章节。"
                )
            })
            self.event_logger.emit("lead", "director_offer_created", "Architect 完成后已等待用户确认是否进入 Director 阶段", {"project_id": project_id, "chapter_id": chapter_id, "revision": is_revision})
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
                if payload.get("task_type") == "artifact_revision":
                    self._attach_after_snapshot_to_pending_revision(payload, project_id, chapter_id)
                    self._create_qa_review_from_event(
                        "architect_revision_completed",
                        project_id=project_id,
                        chapter_id=chapter_id,
                        recent_submission={"architect_submission": payload},
                        pending_revision=self.pending_revision,
                    )
                    user_summary = payload.get("user_visible_summary")
                    if isinstance(user_summary, str) and user_summary.strip():
                        self.feedback_events.append(user_summary)
                        self.conversation_history.append({"role": "assistant", "content": user_summary})
                    self.conversation_history.append({
                        "role": "user",
                        "content": f"[协议消息] {from_agent}: {json.dumps(payload, ensure_ascii=False)}"
                    })
                    return
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
                self._create_qa_review_from_event(
                    "architect_revision_completed" if is_revision_review else "architect_completed",
                    project_id=project_id,
                    chapter_id=chapter_id,
                    recent_submission={
                        "architect_submission": architect_submission,
                        "director_submission": director_submission,
                    },
                    pending_revision=self.pending_revision,
                )
        elif payload_type == "verdict":
            payload = self._normalize_qa_result(payload)
            chapter_id = payload.get("chapter_id", chapter_id)
            project_id = payload.get("project_id", project_id)
            self.submissions["qa"][chapter_id] = payload
            if self.pending_revision and self.pending_revision.get("awaiting_user_choice"):
                self.pending_revision["qa_report"] = payload
                if payload.get("report_file"):
                    self.pending_revision["qa_report_file"] = payload.get("report_file")
                self.pending_revision["target_files"] = payload.get("target_files", [])
            review_context = payload.get("review_context", {}) if isinstance(payload.get("review_context", {}), dict) else {}
            review_type = payload.get("review_type") or payload.get("report_kind") or review_context.get("event_type", "qa_review")
            stage_name = "QA 回归验收完成" if review_type in {"revision_regression", "architect_revision_completed"} else "QA 验收完成"
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

        user_summary = payload.get("user_visible_summary")
        if isinstance(user_summary, str) and user_summary.strip():
            self.feedback_events.append(user_summary)
            self.conversation_history.append({"role": "assistant", "content": user_summary})
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
        if self.team_manager is not None:
            teammates = self.team_manager.list_all()
            for tm in teammates:
                status_emoji = {"idle": "💤", "working": "⚡", "shutdown": "🔴"}.get(tm["status"], "❓")
                print(f"  {status_emoji} {tm['name']} ({tm['role']}): {tm['status']}")
        else:
            print("  队友管理器未启用")

        print("\n📌 任务状态")
        tasks = list(self.task_manager.tasks.values())
        if not tasks:
            print("  暂无任务")
        else:
            for task in sorted(tasks, key=lambda t: t.get("updated_at", ""), reverse=True)[:12]:
                meta = task.get("metadata", {}) if isinstance(task.get("metadata"), dict) else {}
                print(
                    f"  - {task.get('status')} | {task.get('assignee')} | {task.get('title')} "
                    f"| type={meta.get('task_type', '-')} | project={meta.get('project_id', '-')} | chapter={meta.get('chapter_id', '-')}"
                )

        print("\n🧾 最近事件")
        events = self.event_logger.recent(10)
        if not events:
            print("  暂无事件")
        else:
            for event in events:
                print(f"  - {event.get('time', '')} [{event.get('agent')}][{event.get('stage')}] {event.get('message')}")

        print("\n📁 最近交付文件")
        seen_files = []
        for event in reversed(events):
            metadata = event.get("metadata", {}) if isinstance(event.get("metadata"), dict) else {}
            file_path = metadata.get("file") or metadata.get("report_file") or metadata.get("plan_path")
            if file_path and file_path not in seen_files:
                seen_files.append(file_path)
        if not seen_files:
            print("  暂无最近文件事件")
        else:
            for file_path in list(reversed(seen_files))[:8]:
                exists = safe_path(file_path).exists()
                print(f"  - {'exists' if exists else 'missing'} | {file_path}")
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
- /list    列出当前项目已生成 artifact 索引
- /show story_bible    查看故事圣经
- /show chapter_outlines    查看全章节章纲
- /show characters / /show character <name>    查看角色卡
- /show environments / /show environment <name>    查看场景卡
- /show chapter <chapter_id> outline|script|storyboard    查看单章文件
- /show qa latest    查看最近 QA 报告
- /director [chapter_id]  Architect 完成后，确认进入 Director 分镜阶段
- /impact <修改说明>  只让 QA-bot 分析修改影响范围，不直接修订
- /revise <修改说明>  先让 QA-bot 做影响分析，再等待 /apply 选择修订范围
- /apply local|targeted|cascade|cancel  应用最近一次 QA 影响分析并创建 Architect 修订任务
- /check consistency  创建通用 QA 检查任务，由 QA-bot 自主选择审查 skills
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
