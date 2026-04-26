import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from feedback_loop import (
    apply_modification_instruction,
    render_stage_delivery,
    validate_modification_instruction,
)
from p0_runtime import WORKDIR
from p2_content import comic_init_project, comic_write_character


def cleanup(project_id: str):
    p = WORKDIR / project_id
    if p.exists():
        shutil.rmtree(p)


def test_validate_instruction_constraints():
    valid = {
        "target": "林夜.md 的 Level1 视觉锚点",
        "content": "新增一个锚点：右手旧伤疤",
        "constraints": ["Level 只升不降", "视觉锚点不删"],
    }
    assert validate_modification_instruction(valid)["ok"] is True

    invalid = {
        "target": "林夜.md 的 Level1 视觉锚点",
        "content": "删除左眼金色竖瞳",
        "constraints": ["Level 只升不降", "视觉锚点不删"],
    }
    res = validate_modification_instruction(invalid)
    assert res["ok"] is False


def test_apply_instruction_to_character_file():
    project_id = "feedback_loop_demo"
    cleanup(project_id)
    comic_init_project(project_id, "反馈闭环测试", num_chapters=1)
    comic_write_character(project_id, "林夜", "# 林夜 | 主角 | Level 1 🔵\n\n- 视觉锚点：黑色风衣\n")

    instruction = {
        "target": "林夜.md 的 Level1 视觉锚点",
        "content": "新增一个锚点：右手旧伤疤",
        "constraints": ["Level 只升不降", "视觉锚点不删"],
    }
    result = apply_modification_instruction(
        project_id=project_id,
        instruction=instruction,
        character_name="林夜",
    )
    assert result["success"] is True

    file_text = (WORKDIR / project_id / "state" / "characters" / "林夜.md").read_text(encoding="utf-8")
    assert "修改指令应用记录" in file_text
    assert "右手旧伤疤" in file_text
    cleanup(project_id)


def test_render_stage_delivery():
    text = render_stage_delivery("角色设计", ["demo/state/characters/林夜.md"], "本阶段已完成角色初稿")
    assert "角色设计" in text
    assert "林夜.md" in text


if __name__ == "__main__":
    test_validate_instruction_constraints()
    test_apply_instruction_to_character_file()
    test_render_stage_delivery()
    print("✅ feedback_loop tests passed")
