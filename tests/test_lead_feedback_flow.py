import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from lead import LeadAgent
from p0_runtime import WORKDIR
from p2_content import comic_init_project, comic_write_character


class _StubAuxSubagent:
    def run(self, task_name, payload, context=None):
        if task_name == "input_classifier":
            text = payload.get("user_text", "")
            if "模糊" in text or "不太确定" in text:
                input_type = "vague_demand"
            elif any(k in text for k in ["修改", "调整", "改"]):
                input_type = "write_chapter"
            else:
                input_type = "new_story"
            return {
                "success": True,
                "data": {
                    "input_type": input_type,
                    "confidence": 0.88,
                    "key_info": {"raw": text},
                    "suggestion": "先澄清" if input_type == "vague_demand" else "进入修订",
                },
            }
        if task_name == "choice_designer":
            mode = payload.get("mode")
            if mode == "clarification":
                return {
                    "success": True,
                    "data": {
                        "mode": "clarification",
                        "result": {
                            "questions": [
                                {"id": "q1", "question": "主角定位？", "options": ["少年", "成年", "自定义"], "allow_custom": True},
                                {"id": "q2", "question": "故事类型？", "options": ["奇幻", "科幻", "都市"], "allow_custom": True},
                                {"id": "q3", "question": "节奏偏好？", "options": ["慢热", "均衡", "快节奏"], "allow_custom": True},
                            ]
                        },
                    },
                }
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
        return {"success": False, "error": "unknown task"}


def _cleanup(project_id: str):
    p = WORKDIR / project_id
    if p.exists():
        shutil.rmtree(p)


def test_lead_aux_tools_and_feedback_instruction_apply():
    project_id = "lead_feedback_apply_demo"
    _cleanup(project_id)
    comic_init_project(project_id, "lead feedback apply", num_chapters=1)
    comic_write_character(project_id, "林夜", "# 林夜 | Level 1\n\n- 视觉锚点：黑色风衣\n")

    lead = LeadAgent(enable_teammates=False)
    lead.aux_subagent = _StubAuxSubagent()

    classify = lead._execute_lead_tool("classify_input", {"user_text": "我的需求很模糊，不太确定"})
    assert classify["success"] is True
    assert classify["data"]["input_type"] == "vague_demand"

    design = lead._execute_lead_tool(
        "design_choice",
        {"mode": "modification", "user_text": "给主角加一个伤疤", "project_id": project_id},
    )
    assert design["success"] is True

    instruction = design["data"]["result"]["instruction"]
    applied = lead._execute_lead_tool(
        "submit_feedback_instruction",
        {
            "project_id": project_id,
            "instruction": instruction,
            "target_type": "character",
            "target_name": "林夜",
        },
    )
    assert applied["success"] is True

    updated = (WORKDIR / project_id / "state" / "characters" / "林夜.md").read_text(encoding="utf-8")
    assert "修改指令应用记录" in updated
    assert "右手旧伤疤" in updated

    _cleanup(project_id)


def test_protocol_stage_delivery_events_are_recorded():
    lead = LeadAgent(enable_teammates=False)
    chapter_id = "ch01"
    project_id = "protocol_feedback_event_demo"

    lead._route_protocol_message(
        "architect_bot",
        {
            "type": "handoff",
            "project_id": project_id,
            "chapter_id": chapter_id,
            "deliverables": [f"{project_id}/chapters/{chapter_id}/script.md"],
            "summary": "architect handoff done",
            "state_inputs": {},
        },
    )
    assert any("Architect 剧本交付" in e for e in lead.feedback_events)

    lead._route_protocol_message(
        "director_bot",
        {
            "type": "submission",
            "project_id": project_id,
            "chapter_id": chapter_id,
            "deliverables": [f"{project_id}/chapters/{chapter_id}/storyboard.md"],
            "summary": "director submitted",
        },
    )
    assert any("Director 分镜提交" in e for e in lead.feedback_events)

    lead._route_protocol_message(
        "qa_bot",
        {
            "type": "verdict",
            "project_id": project_id,
            "chapter_id": chapter_id,
            "issues": [],
            "report_file": f"{project_id}/qa/{chapter_id}_report.md",
            "final_verdict": "PASS",
            "summary": "qa done",
        },
    )
    assert any("QA 验收完成" in e for e in lead.feedback_events)


def test_auto_route_returns_clarification_json_for_vague_input():
    lead = LeadAgent(enable_teammates=False)
    lead.aux_subagent = _StubAuxSubagent()

    output = lead._auto_route_user_input("我的需求很模糊，不太确定")
    assert output is not None
    data = json.loads(output)
    assert data["type"] == "clarification_questions"
    assert "questions" in data and data["questions"]["mode"] == "clarification"


def test_auto_route_feedback_creates_architect_follow_up_task_and_message():
    project_id = "lead_feedback_auto_route_demo"
    _cleanup(project_id)
    comic_init_project(project_id, "auto route feedback", num_chapters=1)
    comic_write_character(project_id, "林夜", "# 林夜 | Level 1\n\n- 视觉锚点：黑色风衣\n")

    lead = LeadAgent(enable_teammates=False)
    lead.aux_subagent = _StubAuxSubagent()
    lead.task_manager.create(
        title="seed chapter task",
        assignee="architect_bot",
        metadata={"project_id": project_id, "chapter_id": "ch01"},
    )

    output = lead._auto_route_user_input("请修改林夜设定，调整视觉锚点")
    assert output is not None
    data = json.loads(output)
    assert data["type"] == "feedback_instruction_applied"
    assert data["architect_follow_up"]["success"] is True

    task_id = data["architect_follow_up"]["task_id"]
    task = lead.task_manager.get(task_id)
    assert task is not None
    assert task["assignee"] == "architect_bot"
    assert task["metadata"]["task_type"] == "architect_feedback_revision"
    assert task["metadata"]["submission_target"] == "lead"

    inbox = lead.message_bus.read_inbox("architect_bot", mark_read=True)
    assert inbox
    assert inbox[-1]["message"]["type"] == "feedback_instruction"

    updated = (WORKDIR / project_id / "state" / "characters" / "林夜.md").read_text(encoding="utf-8")
    assert "修改指令应用记录" in updated

    _cleanup(project_id)


if __name__ == "__main__":
    test_lead_aux_tools_and_feedback_instruction_apply()
    test_protocol_stage_delivery_events_are_recorded()
    test_auto_route_returns_clarification_json_for_vague_input()
    test_auto_route_feedback_creates_architect_follow_up_task_and_message()
    print("✅ lead feedback flow tests passed")
