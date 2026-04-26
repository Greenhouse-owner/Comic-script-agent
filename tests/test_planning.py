import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from p0_runtime import WORKDIR
from planning import PlanManager
from policy import PolicyViolation, ToolPolicy


def _cleanup(project_id: str):
    p = WORKDIR / project_id
    if p.exists():
        shutil.rmtree(p)


def _task(task_type: str, project_id: str = "planning_demo", chapter_id: str = "ch01"):
    return {
        "id": f"task_{task_type}",
        "metadata": {
            "task_type": task_type,
            "project_id": project_id,
            "chapter_id": chapter_id,
        },
    }


def test_architect_delivery_plan_written():
    project_id = "planning_demo_architect"
    _cleanup(project_id)
    manager = PlanManager()
    result = manager.require_plan("architect_bot", _task("architect_delivery", project_id))
    assert result["success"] is True
    assert result["plan"]["status"] == "approved"
    plan_path = WORKDIR / result["plan_path"]
    assert plan_path.exists()
    _cleanup(project_id)


def test_architect_feedback_revision_plan_has_v3_risks():
    project_id = "planning_demo_revision"
    _cleanup(project_id)
    manager = PlanManager()
    result = manager.require_plan("architect_bot", _task("architect_feedback_revision", project_id))
    risks = result["plan"]["risk_checklist"]
    assert "Level 只升不降" in risks
    assert "视觉锚点不删" in risks
    _cleanup(project_id)


def test_director_delivery_plan_targets_storyboard():
    project_id = "planning_demo_director"
    _cleanup(project_id)
    manager = PlanManager()
    result = manager.require_plan("director_bot", _task("director_delivery", project_id))
    paths = [step["inputs"].get("file_path") for step in result["plan"]["steps"]]
    assert f"{project_id}/chapters/ch01/storyboard.md" in paths
    _cleanup(project_id)


def test_qa_review_plan_targets_report():
    project_id = "planning_demo_qa"
    _cleanup(project_id)
    manager = PlanManager()
    result = manager.require_plan("qa_bot", _task("qa_review", project_id))
    paths = [step["inputs"].get("file_path") for step in result["plan"]["steps"]]
    assert f"{project_id}/qa/ch01_report.md" in paths
    _cleanup(project_id)


def test_illegal_plan_is_blocked_by_policy():
    policy = ToolPolicy()
    manager = PlanManager(policy=policy)
    plan = manager._build_plan("qa_bot", _task("architect_delivery", "planning_demo_illegal"))
    try:
        manager.validate_plan(plan)
    except PolicyViolation:
        return
    raise AssertionError("qa_bot should not be allowed to execute architect_delivery plan")


if __name__ == "__main__":
    test_architect_delivery_plan_written()
    test_architect_feedback_revision_plan_has_v3_risks()
    test_director_delivery_plan_targets_storyboard()
    test_qa_review_plan_targets_report()
    test_illegal_plan_is_blocked_by_policy()
    print("✅ planning tests passed")
