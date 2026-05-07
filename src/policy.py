"""Agent tool and path authorization policy for Lab 13."""

from __future__ import annotations

from dataclasses import dataclass, field
from fnmatch import fnmatch
from typing import Any, Dict, List, Optional

from p0_runtime import safe_path


class PolicyViolation(Exception):
    """Raised when an agent tries to use a forbidden tool or path."""


@dataclass
class AgentPolicy:
    allowed_tools: List[str]
    read_globs: List[str] = field(default_factory=list)
    write_globs: List[str] = field(default_factory=list)
    allow_shell: bool = False


class ToolPolicy:
    """MVP policy layer for per-agent tools and workspace paths."""

    def __init__(self):
        # 配置表集中描述每个 agent 的工具白名单和路径白名单；后续所有工具调用都走这里校验。
        self.policies: Dict[str, AgentPolicy] = {
            "lead": AgentPolicy(
                allowed_tools=["*"],
                read_globs=["**"],
                write_globs=["**"],
            ),
            "architect_bot": AgentPolicy(
                allowed_tools=[
                    "read_file",
                    "write_file",
                    "load_skill",
                    "send_message",
                    "comic_read_character",
                    "comic_write_character",
                    "comic_list_characters",
                    "comic_read_environment",
                    "comic_write_environment",
                    "comic_list_environments",
                    "idle",
                ],
                read_globs=[
                    "{project_id}/brief.md",
                    "{project_id}/story_direction.md",
                    "{project_id}/story_bible.md",
                    "{project_id}/chapter_outlines.md",
                    "{project_id}/chapters/**/outline.md",
                    "{project_id}/state/**",
                    "{project_id}/chapters/**/script.md",
                    "{project_id}/qa/*.md",
                ],
                write_globs=[
                    "{project_id}/story_bible.md",
                    "{project_id}/chapter_outlines.md",
                    "{project_id}/chapters/**/outline.md",
                    "{project_id}/chapters/**/script.md",
                    "{project_id}/state/characters/*.md",
                    "{project_id}/state/environments/*.md",
                ],
            ),
            "director_bot": AgentPolicy(
                allowed_tools=[
                    "read_file",
                    "write_file",
                    "load_skill",
                    "send_message",
                    "comic_read_character",
                    "comic_list_characters",
                    "comic_read_environment",
                    "comic_list_environments",
                    "idle",
                ],
                read_globs=[
                    "{project_id}/chapters/**/script.md",
                    "{project_id}/state/**",
                ],
                write_globs=[
                    "{project_id}/chapters/**/storyboard.md",
                    "{project_id}/chapters/**/director_plan/*.json",
                ],
            ),
            "qa_bot": AgentPolicy(
                allowed_tools=[
                    "read_file",
                    "write_file",
                    "load_skill",
                    "send_message",
                    "idle",
                ],
                read_globs=[
                    "{project_id}/**",
                ],
                write_globs=[
                    "{project_id}/qa/*.md",
                ],
            ),
            "aux_subagent": AgentPolicy(
                allowed_tools=[],
                read_globs=[],
                write_globs=[],
            ),
        }

    def authorize_tool(self, agent_name: str, tool_name: str):
        policy = self._get_policy(agent_name)
        if "*" in policy.allowed_tools:
            return
        if tool_name not in policy.allowed_tools:
            raise PolicyViolation(f"{agent_name} cannot use tool: {tool_name}")

    def authorize_path(
        self,
        agent_name: str,
        file_path: str,
        mode: str,
        context: Optional[Dict[str, Any]] = None,
    ):
        if mode not in {"read", "write"}:
            raise PolicyViolation(f"Invalid path authorization mode: {mode}")

        safe_path(file_path)
        policy = self._get_policy(agent_name)
        patterns = policy.read_globs if mode == "read" else policy.write_globs
        # 路径策略里使用 {project_id}/{chapter_id} 占位符，执行时用当前任务上下文展开。
        expanded_patterns = [self._expand_pattern(pattern, context or {}) for pattern in patterns]

        # 只要命中任意一个 glob 就放行；否则抛 PolicyViolation 并把允许范围写进错误信息。
        if not any(fnmatch(file_path, pattern) for pattern in expanded_patterns):
            raise PolicyViolation(
                f"{agent_name} cannot {mode} path: {file_path}. Allowed: {expanded_patterns}"
            )

    def authorize_file_tool(
        self,
        agent_name: str,
        tool_name: str,
        tool_input: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
    ):
        self.authorize_tool(agent_name, tool_name)
        ctx = context or {}

        # 文件类工具没有直接传 file_path 时，先按业务参数拼出真实路径，再复用统一路径校验。
        if tool_name == "read_file":
            self.authorize_path(agent_name, tool_input["file_path"], "read", ctx)
        elif tool_name == "write_file":
            self.authorize_path(agent_name, tool_input["file_path"], "write", ctx)
        elif tool_name == "comic_read_character":
            path = f"{tool_input['project_id']}/state/characters/{tool_input['character_name']}.md"
            self.authorize_path(agent_name, path, "read", {**ctx, "project_id": tool_input["project_id"]})
        elif tool_name == "comic_write_character":
            path = f"{tool_input['project_id']}/state/characters/{tool_input['character_name']}.md"
            self.authorize_path(agent_name, path, "write", {**ctx, "project_id": tool_input["project_id"]})
        elif tool_name == "comic_read_environment":
            path = f"{tool_input['project_id']}/state/environments/{tool_input['env_name']}.md"
            self.authorize_path(agent_name, path, "read", {**ctx, "project_id": tool_input["project_id"]})
        elif tool_name == "comic_write_environment":
            path = f"{tool_input['project_id']}/state/environments/{tool_input['env_name']}.md"
            self.authorize_path(agent_name, path, "write", {**ctx, "project_id": tool_input["project_id"]})

    def _get_policy(self, agent_name: str) -> AgentPolicy:
        policy = self.policies.get(agent_name)
        if policy is None:
            raise PolicyViolation(f"Unknown agent policy: {agent_name}")
        return policy

    @staticmethod
    def _expand_pattern(pattern: str, context: Dict[str, Any]) -> str:
        result = pattern
        for key, value in context.items():
            result = result.replace("{" + key + "}", str(value))
        return result
