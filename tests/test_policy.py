import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from policy import PolicyViolation, ToolPolicy


def test_policy_allows_architect_script_write():
    policy = ToolPolicy()
    policy.authorize_path(
        "architect_bot",
        "policy_demo/chapters/ch01/script.md",
        "write",
        {"project_id": "policy_demo", "chapter_id": "ch01"},
    )


def test_policy_blocks_director_character_write():
    policy = ToolPolicy()
    try:
        policy.authorize_path(
            "director_bot",
            "policy_demo/state/characters/林夜.md",
            "write",
            {"project_id": "policy_demo", "chapter_id": "ch01"},
        )
    except PolicyViolation:
        return
    raise AssertionError("director_bot should not write character state")


def test_policy_blocks_qa_script_write():
    policy = ToolPolicy()
    try:
        policy.authorize_path(
            "qa_bot",
            "policy_demo/chapters/ch01/script.md",
            "write",
            {"project_id": "policy_demo", "chapter_id": "ch01"},
        )
    except PolicyViolation:
        return
    raise AssertionError("qa_bot should not write script.md")


def test_policy_blocks_aux_write_tool():
    policy = ToolPolicy()
    try:
        policy.authorize_tool("aux_subagent", "write_file")
    except PolicyViolation:
        return
    raise AssertionError("aux_subagent should not use write_file")


def test_policy_blocks_workspace_escape():
    policy = ToolPolicy()
    try:
        policy.authorize_path(
            "lead",
            "../../outside.txt",
            "read",
            {"project_id": "policy_demo", "chapter_id": "ch01"},
        )
    except Exception:
        return
    raise AssertionError("path escape should be blocked")


if __name__ == "__main__":
    test_policy_allows_architect_script_write()
    test_policy_blocks_director_character_write()
    test_policy_blocks_qa_script_write()
    test_policy_blocks_aux_write_tool()
    test_policy_blocks_workspace_escape()
    print("✅ policy tests passed")
