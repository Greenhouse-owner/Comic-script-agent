# tests/test_p1_skills.py

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from p1_skills import SkillLoader

EXPECTED_SKILLS = [
    "chapter-expander",
    "character-builder",
    "character-db-manager",
    "choice-designer",
    "environment-builder",
    "environment-db-manager",
    "input-classifier",
    "panel-director",
    "reaction-polisher",
    "story-planner",
    "story-qa",
]


def test_list_skills():
    loader = SkillLoader()
    skills = loader.list_skills()
    names = sorted([s["name"] for s in skills])
    assert names == EXPECTED_SKILLS, f"期望 {EXPECTED_SKILLS}，实际 {names}"
    assert len(skills) == 11, f"期望 11 个 Skill，实际 {len(skills)} 个"
    print("✅ list_skills: 11 个 Skill 全部找到")


def test_load_skill():
    loader = SkillLoader()
    content = loader.load("story-planner")
    assert "## 输入" in content
    assert "## 硬约束" in content
    # 测试缓存
    content2 = loader.load("story-planner")
    assert content is content2  # 同一个对象（来自缓存）
    # 测试不存在的 skill
    try:
        loader.load("nonexistent-skill")
        assert False, "应该抛出 FileNotFoundError"
    except FileNotFoundError:
        pass
    print("✅ load_skill")


def test_validate_contracts():
    loader = SkillLoader()
    issues = loader.validate_contracts()
    if issues:
        for name, missing in issues.items():
            print(f"  ❌ {name}: 缺少 {missing}")
        assert False, f"技能手册不符合规范：{issues}"
    print("✅ validate_contracts: 所有 Skill 合约通过")


def test_parse_frontmatter():
    loader = SkillLoader()
    skills = loader.list_skills()
    for s in skills:
        assert s["description"], f"{s['name']} 缺少 description"
        assert s["tags"], f"{s['name']} 缺少 tags"
    print("✅ parse_frontmatter: 所有 Skill 的 frontmatter 正确")


if __name__ == "__main__":
    test_list_skills()
    test_load_skill()
    test_validate_contracts()
    test_parse_frontmatter()
    print("\n🎉 P1 层测试全部通过")
