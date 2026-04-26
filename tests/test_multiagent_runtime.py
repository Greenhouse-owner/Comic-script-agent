# tests/test_multiagent_runtime.py

import sys
import shutil
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from p0_runtime import WORKDIR, TaskManager, MessageBus
from p2_content import comic_init_project


def cleanup(project_id: str):
    project_path = WORKDIR / project_id
    if project_path.exists():
        shutil.rmtree(project_path)


def test_shared_task_manager_and_lead_result_inbox():
    project_id = "multiagent_runtime_demo"
    cleanup(project_id)
    comic_init_project(project_id, "测试多 agent 主链路", num_chapters=1)

    task_manager = TaskManager(storage_file=f"{project_id}/tasks.json")
    message_bus = MessageBus(inbox_dir=f"{project_id}/inboxes")

    task_id = task_manager.create(
        title="写第1章",
        assignee="architect_bot",
        metadata={"project_id": project_id, "chapter_id": "ch01"},
    )

    architect_tasks = task_manager.list_for_assignee("architect_bot")
    assert len(architect_tasks) == 1
    assert architect_tasks[0]["id"] == task_id
    assert architect_tasks[0]["status"] == "pending"

    message_bus.send(
        "architect_bot",
        "lead",
        {
            "type": "task_result",
            "task_id": task_id,
            "assignee": "architect_bot",
            "title": "写第1章",
            "success": True,
            "output": "已完成 ch01 剧本",
            "metadata": {"project_id": project_id, "chapter_id": "ch01"},
        },
    )

    lead_inbox = message_bus.read_inbox("lead", mark_read=True)
    assert len(lead_inbox) == 1
    payload = lead_inbox[0]["message"]
    assert payload["type"] == "task_result"
    assert payload["task_id"] == task_id
    assert payload["success"] is True
    assert payload["assignee"] == "architect_bot"

    task_manager.update(task_id, "done", payload)
    saved_task = task_manager.get(task_id)
    assert saved_task is not None
    assert saved_task["status"] == "done"
    assert saved_task["result"]["output"] == "已完成 ch01 剧本"

    cleanup(project_id)
