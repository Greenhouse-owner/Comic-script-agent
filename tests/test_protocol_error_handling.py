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


def test_director_missing_script_returns_protocol_error_without_crashing():
    project_id = "protocol_missing_script_demo"
    chapter_id = "ch01"
    _cleanup(project_id)
    comic_init_project(project_id, "missing script demo", num_chapters=1)

    manager = TeammateManager(openai_client=None)
    task_id = manager.task_manager.create(
        title="Director 交付 ch01",
        assignee="director_bot",
        metadata={
            "task_type": "director_delivery",
            "project_id": project_id,
            "chapter_id": chapter_id,
            "inputs": [f"{project_id}/chapters/{chapter_id}/script.md"],
            "submission_target": "lead",
        },
    )
    task = manager.task_manager.get(task_id)

    result = manager._execute_task(
        "director_bot",
        task,
        conversation_history=[],
        system_prompt="director test",
        available_skills=[],
    )

    assert result["success"] is False
    assert result["error_type"] == "protocol_error"
    assert "File not found" in result["error"]

    lead_msgs = manager.message_bus.read_inbox("lead", mark_read=True)
    assert lead_msgs
    assert lead_msgs[-1]["message"]["success"] is False

    _cleanup(project_id)


if __name__ == "__main__":
    test_director_missing_script_returns_protocol_error_without_crashing()
    print("✅ protocol error handling tests passed")
