import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from p0_runtime import WORKDIR
from p2_content import comic_init_project
from p3_team import TeammateManager


def _cleanup(project_id: str):
    p = WORKDIR / project_id
    if p.exists():
        shutil.rmtree(p)


def test_architect_feedback_revision_protocol_writes_script_and_emits_messages():
    project_id = "p3_feedback_revision_demo"
    chapter_id = "ch01"
    _cleanup(project_id)
    comic_init_project(project_id, "p3 feedback revision", num_chapters=1)

    manager = TeammateManager(openai_client=None)
    task = {
        "metadata": {
            "task_type": "architect_feedback_revision",
            "project_id": project_id,
            "chapter_id": chapter_id,
            "instruction": {
                "target": "林夜.md 的 Level1 视觉锚点",
                "content": "新增视觉锚点：右手旧伤疤",
                "constraints": ["Level 只升不降", "视觉锚点不删"],
            },
            "submission_target": "lead",
            "qa_target": "qa_bot",
            "quality_bar": ["Level 只升不降", "视觉锚点不删"],
        }
    }

    result = manager._try_execute_protocol_task("architect_bot", task)
    assert result is not None
    assert result["success"] is True
    assert result["protocol"] == "architect_feedback_revision"

    script_path = WORKDIR / project_id / "chapters" / chapter_id / "script.md"
    text = script_path.read_text(encoding="utf-8")
    assert "Architect 修订记录" in text
    assert "右手旧伤疤" in text

    lead_msgs = manager.message_bus.read_inbox("lead", mark_read=True)
    assert len(lead_msgs) == 2
    assert lead_msgs[0]["message"]["type"] == "handoff"
    assert lead_msgs[1]["message"]["type"] == "submission"

    _cleanup(project_id)


if __name__ == "__main__":
    test_architect_feedback_revision_protocol_writes_script_and_emits_messages()
    print("✅ p3 feedback revision tests passed")
