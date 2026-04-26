import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from lead import LeadAgent
from p0_runtime import WORKDIR
from p2_content import comic_init_project, comic_write_character, comic_write_environment
from p3_team import TeammateManager


class _StubAuxSubagentRevision:
    def run(self, task_name, payload, context=None):
        if task_name == "input_classifier":
            return {
                "success": True,
                "data": {
                    "input_type": "write_chapter",
                    "confidence": 0.93,
                    "key_info": {"raw": payload.get("user_text", "")},
                    "suggestion": "进入修订",
                },
            }
        if task_name == "choice_designer":
            return {
                "success": True,
                "data": {
                    "mode": "modification",
                    "result": {
                        "instruction": {
                            "target": "林夜.md 的 Level1 视觉锚点",
                            "content": "新增视觉锚点：右手旧伤疤",
                            "constraints": ["Level 只升不降", "视觉锚点不删"],
                        }
                    },
                },
            }
        return {"success": False, "error": "unknown"}


def _cleanup(project_id: str):
    p = WORKDIR / project_id
    if p.exists():
        shutil.rmtree(p)


def test_revision_regression_end_to_end_flow():
    project_id = "e2e_revision_regression_demo"
    chapter_id = "ch01"
    _cleanup(project_id)

    comic_init_project(project_id, "revision e2e", num_chapters=1)
    comic_write_character(project_id, "林夜", "# 林夜 | Level 1\n\n- 视觉锚点：黑色风衣\n")
    comic_write_environment(project_id, "夜城", "# 夜城 | Level 1\n\n- 氛围：霓虹\n")

    lead = LeadAgent(enable_teammates=False)
    lead.aux_subagent = _StubAuxSubagentRevision()

    lead.task_manager.create(
        title="seed task",
        assignee="architect_bot",
        metadata={"project_id": project_id, "chapter_id": chapter_id},
    )

    routed = lead._auto_route_user_input("请修改林夜设定，强化视觉锚点")
    assert routed is not None

    follow_task = None
    for task in lead.task_manager.tasks.values():
        if task.get("metadata", {}).get("task_type") == "architect_feedback_revision":
            follow_task = task
            break
    assert follow_task is not None

    team = TeammateManager(openai_client=None, task_manager=lead.task_manager, message_bus=lead.message_bus)

    # Architect revision executes: emits handoff + submission to lead
    architect_result = team._try_execute_protocol_task("architect_bot", follow_task)
    assert architect_result and architect_result["success"] is True
    lead._drain_agent_results()

    # Lead should create director revision task with submission_target=lead
    director_task = None
    for task in lead.task_manager.tasks.values():
        meta = task.get("metadata", {})
        if meta.get("task_type") == "director_delivery" and meta.get("revision"):
            director_task = task
            break
    assert director_task is not None
    assert director_task["metadata"]["submission_target"] == "lead"

    director_result = team._try_execute_protocol_task("director_bot", director_task)
    assert director_result and director_result["success"] is True
    lead._drain_agent_results()

    # Lead should create QA regression task
    qa_task = None
    for task in lead.task_manager.tasks.values():
        meta = task.get("metadata", {})
        if meta.get("task_type") == "qa_review" and meta.get("review_type") == "revision_regression":
            qa_task = task
            break
    assert qa_task is not None

    qa_result = team._try_execute_protocol_task("qa_bot", qa_task)
    assert qa_result and qa_result["success"] is True
    lead._drain_agent_results()

    summary = lead._build_user_chapter_summary(chapter_id)
    assert summary["qa_ready"] is True
    assert lead.submissions["qa"][chapter_id]["review_type"] == "revision_regression"

    # Check stage events include revision labels
    joined = "\n".join(lead.feedback_events)
    assert "Director 修订分镜提交" in joined
    assert "QA 回归验收完成" in joined

    _cleanup(project_id)


if __name__ == "__main__":
    test_revision_regression_end_to_end_flow()
    print("✅ e2e revision regression flow test passed")
