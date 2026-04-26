# tests/test_protocol_flow.py

import sys
import shutil
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from p0_runtime import WORKDIR, TaskManager, MessageBus, run_write, run_read
from p2_content import comic_init_project


def cleanup(project_id: str):
    project_path = WORKDIR / project_id
    if project_path.exists():
        shutil.rmtree(project_path)


def test_protocol_message_flow():
    project_id = "protocol_flow_demo"
    chapter_id = "ch01"
    cleanup(project_id)
    comic_init_project(project_id, "协议流测试", num_chapters=1)

    task_manager = TaskManager(storage_file=f"{project_id}/tasks.json")
    bus = MessageBus(inbox_dir=f"{project_id}/inboxes")

    architect_task = task_manager.create(
        title="Architect 交付 ch01",
        assignee="architect_bot",
        metadata={
            "protocol_version": "v1",
            "task_type": "architect_delivery",
            "project_id": project_id,
            "chapter_id": chapter_id,
            "goal": "提交可供 Director 使用的剧本",
            "deliverables": [f"{project_id}/chapters/{chapter_id}/script.md"],
            "qa_target": "qa_bot",
            "handoff_target": "director_bot",
        },
    )

    assert architect_task

    script_path = f"{project_id}/chapters/{chapter_id}/script.md"
    storyboard_path = f"{project_id}/chapters/{chapter_id}/storyboard.md"
    run_write(script_path, f"# {chapter_id} 剧本\n\n## 使用技能\n- story-planner\n\n## 场景 1\nhero 在 city 遭遇危机。\n\n## 角色动机\nhero 必须主动应对当前危机。\n")
    run_write(storyboard_path, f"# {chapter_id} 分镜\n\n## 使用技能\n- panel-director\n\n## 剧本依据\n来自 {chapter_id} 剧本，hero 在 city 遭遇危机。\n")

    architect_submission = {
        "type": "submission",
        "from_role": "architect",
        "project_id": project_id,
        "chapter_id": chapter_id,
        "deliverables": [script_path],
        "updated_files": [script_path],
        "self_check": ["已生成章节剧本"],
        "quality_bar": ["剧情完整"],
        "summary": "Architect 请求 QA 审核剧本交付。",
    }
    director_submission = {
        "type": "submission",
        "from_role": "director",
        "project_id": project_id,
        "chapter_id": chapter_id,
        "deliverables": [storyboard_path],
        "updated_files": [storyboard_path],
        "depends_on_script": [script_path],
        "visual_constraints_used": ["角色视觉锚点"],
        "self_check": ["已生成分镜"],
        "quality_bar": ["严格对应 script.md"],
        "summary": "Director 请求 QA 审核分镜交付。",
    }

    bus.send("architect_bot", "lead", {
        "type": "handoff",
        "from_role": "architect",
        "project_id": project_id,
        "chapter_id": chapter_id,
        "deliverables": [script_path],
        "state_inputs": {
            "characters": f"{project_id}/state/characters",
            "environments": f"{project_id}/state/environments",
        },
        "summary": "Architect 已交付剧本，可供 Director 使用。",
    })
    bus.send("architect_bot", "qa_bot", architect_submission)
    bus.send("director_bot", "qa_bot", director_submission)
    bus.send("qa_bot", "lead", {
        "type": "verdict",
        "from_role": "qa",
        "project_id": project_id,
        "chapter_id": chapter_id,
        "architect_verdict": True,
        "director_verdict": True,
        "cross_consistency_verdict": True,
        "issues": [],
        "report_file": f"{project_id}/qa/{chapter_id}_report.md",
        "final_verdict": "PASS",
        "summary": "QA 已完成双提交验收。",
    })

    lead_msgs = bus.read_inbox("lead", mark_read=True)
    qa_msgs = bus.read_inbox("qa_bot", mark_read=True)

    assert len(lead_msgs) == 2
    assert len(qa_msgs) == 2
    assert lead_msgs[0]["message"]["type"] == "handoff"
    assert lead_msgs[1]["message"]["type"] == "verdict"
    assert qa_msgs[0]["message"]["type"] == "submission"
    assert qa_msgs[1]["message"]["type"] == "submission"
    assert qa_msgs[0]["message"]["updated_files"] == [script_path]
    assert qa_msgs[1]["message"]["depends_on_script"] == [script_path]

    report_path = f"{project_id}/qa/{chapter_id}_report.md"
    run_write(report_path, "# QA 报告 ch01\n")
    report_text = run_read(report_path)
    assert "QA 报告" in report_text

    cleanup(project_id)
