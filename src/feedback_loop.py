"""Feedback loop helpers for V3 comic workflow."""

from __future__ import annotations

from typing import Dict, List, Optional

from p2_content import (
    comic_read_character,
    comic_read_environment,
    comic_write_character,
    comic_write_environment,
)


REQUIRED_V3_CONSTRAINTS = [
    "Level 只升不降",
    "视觉锚点不删",
]


def render_stage_delivery(stage: str, files: List[str], summary: str = "") -> str:
    file_lines = "\n".join([f"- {f}" for f in files]) if files else "- [无文件]"
    return (
        f"## {stage} 阶段已完成\n\n"
        f"{summary}\n\n"
        f"### 交付文件\n{file_lines}\n"
    )


def validate_modification_instruction(instruction: Dict) -> Dict:
    if not isinstance(instruction, dict):
        return {"ok": False, "error": "instruction must be object"}

    for key in ("target", "content", "constraints"):
        if key not in instruction:
            return {"ok": False, "error": f"instruction.{key} is required"}

    constraints = instruction.get("constraints", [])
    if not isinstance(constraints, list) or not constraints:
        return {"ok": False, "error": "instruction.constraints must be non-empty list"}

    for required in REQUIRED_V3_CONSTRAINTS:
        if required not in constraints:
            return {"ok": False, "error": f"missing v3 constraint: {required}"}

    target_text = str(instruction.get("target", ""))
    content_text = str(instruction.get("content", ""))

    if "Level" in target_text and "降级" in content_text:
        return {"ok": False, "error": "禁止降级角色/场景 Level"}

    if "视觉锚点" in target_text and any(x in content_text for x in ("删除", "移除")):
        return {"ok": False, "error": "禁止删除视觉锚点，可新增或重写描述"}

    return {"ok": True}


def apply_modification_instruction(
    project_id: str,
    instruction: Dict,
    *,
    character_name: Optional[str] = None,
    environment_name: Optional[str] = None,
) -> Dict:
    """
    Apply modification by appending a structured patch section.

    This keeps V3 facts on disk and avoids silent destructive overwrite.
    """
    validated = validate_modification_instruction(instruction)
    if not validated["ok"]:
        return {"success": False, "error": validated["error"]}

    target = str(instruction["target"])
    content = str(instruction["content"])
    constraints = instruction.get("constraints", [])

    patch_block = (
        "\n\n---\n\n"
        "## 修改指令应用记录\n"
        f"- 修改目标: {target}\n"
        f"- 修改内容: {content}\n"
        f"- 约束: {', '.join(constraints)}\n"
    )

    if character_name:
        old_text = comic_read_character(project_id, character_name)
        new_text = old_text + patch_block
        write_result = comic_write_character(project_id, character_name, new_text)
        return {"success": True, "target_type": "character", "name": character_name, "write": write_result}

    if environment_name:
        old_text = comic_read_environment(project_id, environment_name)
        new_text = old_text + patch_block
        write_result = comic_write_environment(project_id, environment_name, new_text)
        return {"success": True, "target_type": "environment", "name": environment_name, "write": write_result}

    return {"success": False, "error": "either character_name or environment_name is required"}
