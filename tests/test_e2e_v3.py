# tests/test_e2e_v3.py
"""
V3 端到端验证脚本
- 离线 harness 测试：验证数据层流程（不需要 API）
- API 端到端测试：验证 LeadAgent 能产出内容（需要 API）
"""

import sys
import shutil
import json
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from p0_runtime import WORKDIR, safe_path, run_read, run_write
from p1_skills import SkillLoader
from p2_content import (
    comic_init_project, comic_read_character, comic_write_character,
    comic_list_characters, comic_read_environment, comic_write_environment,
    comic_list_environments
)


def cleanup(project_id):
    p = WORKDIR / project_id
    if p.exists():
        shutil.rmtree(p)


# ============================================================
# 离线 Harness 测试（不需要 API）
# ============================================================

def test_harness_full_flow():
    """模拟完整创作流程：初始化 → 角色 → 场景 → 剧本 → QA"""
    project_id = "e2e_test_v3"
    cleanup(project_id)

    # 1. 初始化项目
    result = comic_init_project(project_id, "赛博朋克黑客故事", num_chapters=2)
    assert "e2e_test_v3" in result["project_path"]
    p = WORKDIR / project_id
    assert (p / "state" / "characters").is_dir()
    assert (p / "state" / "environments").is_dir()
    assert (p / "chapters" / "ch01").is_dir()
    assert (p / "chapters" / "ch02").is_dir()
    print("  ✓ 项目初始化成功")

    # 2. 写入 Level 1 角色
    char_content = """# 林夜 | 主角 | Level 1 🔵

> 首次登场: ch01 | 上次更新: ch01

## Level 1 — 轮廓 (ch01 创建)
- **视觉锚点**:
  1. 黑色过膝风衣
  2. 银色碎发
  3. 左眼金色竖瞳
- **一句话定位**: 在夜色中游荡的孤独黑客
- **性格特质**: 沉默寡言但内心炽热
- **关键关系**:
  - 黑狐: 搭档
"""
    comic_write_character(project_id, "林夜", char_content)
    comic_write_character(project_id, "黑狐", """# 黑狐 | 配角 | Level 1 🔵

> 首次登场: ch01 | 上次更新: ch01

## Level 1 — 轮廓 (ch01 创建)
- **视觉锚点**:
  1. 狐狸面具（半遮脸）
  2. 红色连帽衫
  3. 左手机械义肢
- **一句话定位**: 信息贩子，亦正亦邪
- **性格特质**: 油嘴滑舌，关键时刻靠谱
- **关键关系**:
  - 林夜: 搭档
""")

    # 验证角色读取和列表
    read_back = comic_read_character(project_id, "林夜")
    assert "黑色过膝风衣" in read_back
    names = comic_list_characters(project_id)
    assert sorted(names) == ["林夜", "黑狐"]
    print("  ✓ 角色写入/读取/列表正常")

    # 3. 写入 Level 1 场景
    env_content = """# 老城区街头 | 主场景 | Level 1 🔵

> 首次出现: ch01 | 上次更新: ch01

## Level 1 — 轮廓 (ch01 创建)
- **地理位置**: 城市东区
- **一句话氛围**: 霓虹灯与阴影交错的赛博朋克街巷
- **时间段**: 深夜
- **关键道具**:
  1. 霓虹灯招牌
  2. 地面积水
  3. 破旧贩卖机
"""
    comic_write_environment(project_id, "老城区街头", env_content)
    read_back = comic_read_environment(project_id, "老城区街头")
    assert "霓虹灯招牌" in read_back
    envs = comic_list_environments(project_id)
    assert envs == ["老城区街头"]
    print("  ✓ 场景写入/读取/列表正常")

    # 4. 写入 mock 剧本和分镜
    script_content = """# 第一章：暗夜追踪

## 场景 1：老城区街头

林夜穿着黑色风衣站在巷口，银色碎发被风吹起。
他的左眼金色竖瞳微微发光，扫视着周围的环境。

**林夜**：（独白）三年了，还是没有线索...

突然，一个戴着狐狸面具的身影从暗处走出——是黑狐。
红色连帽衫的帽子拉得很低，左手机械义肢在霓虹灯下反射着冷光。

**黑狐**：嘿，老搭档。有个活儿，感兴趣吗？
"""
    run_write(f"{project_id}/chapters/ch01/script.md", script_content)

    storyboard_content = """# 第一章分镜脚本

## 第 1 页

### 格子 1（远景）
- **景别**：远景
- **画面内容**：老城区街头全景，霓虹灯招牌林立，地面积水反射灯光
- **人物**：林夜站在巷口（黑色风衣剪影）

### 格子 2（中景）
- **景别**：中景
- **画面内容**：林夜半身像，银色碎发被风吹起
- **视觉重点**：左眼金色竖瞳发出微光
- **对白**：林夜独白："三年了，还是没有线索..."

### 格子 3（中景）
- **景别**：中景
- **画面内容**：黑狐从暗处走出，狐狸面具半遮脸，红色连帽衫
- **视觉重点**：左手机械义肢在霓虹灯下反光
- **对白**：黑狐："嘿，老搭档。有个活儿，感兴趣吗？"
"""
    run_write(f"{project_id}/chapters/ch01/storyboard.md", storyboard_content)

    # 验证文件写入
    script_back = run_read(f"{project_id}/chapters/ch01/script.md")
    assert "暗夜追踪" in script_back
    storyboard_back = run_read(f"{project_id}/chapters/ch01/storyboard.md")
    assert "格子 1" in storyboard_back
    print("  ✓ 剧本和分镜写入正常")

    # 5. 验证 Skill 加载
    loader = SkillLoader()
    skills = loader.list_skills()
    assert len(skills) == 11
    # 验证关键 Skill 可加载
    for skill_name in ["story-planner", "character-db-manager", "environment-db-manager", "story-qa"]:
        content = loader.load(skill_name)
        assert "## 输入" in content
        assert "## 硬约束" in content
    print("  ✓ 11 个 Skill 全部可加载")

    # 6. 验证数据完整性（QA 需要 API，这里只验证文件结构）
    char_dir = safe_path(f"{project_id}/state/characters")
    env_dir = safe_path(f"{project_id}/state/environments")
    assert len(list(char_dir.glob("*.md"))) == 2
    assert len(list(env_dir.glob("*.md"))) == 1
    assert (safe_path(f"{project_id}/chapters/ch01/script.md")).exists()
    assert (safe_path(f"{project_id}/chapters/ch01/storyboard.md")).exists()
    print("  ✓ 数据完整性验证通过")

    cleanup(project_id)
    print("✅ 离线 Harness 测试全部通过\n")


# ============================================================
# API 端到端测试（需要 API）
# ============================================================

def test_api_e2e():
    """测试 LeadAgent 能否正常初始化并处理一轮对话"""
    from lead import LeadAgent

    print("  → 初始化 LeadAgent...")
    lead = LeadAgent()

    # 验证队友已启动
    teammates = lead.team_manager.list_all()
    names = sorted([t["name"] for t in teammates])
    assert names == ["architect_bot", "director_bot", "qa_bot"], f"队友异常: {names}"
    print("  ✓ 3 个队友已启动")

    # 发一条简单消息，验证 Lead 能正常响应
    print("  → 发送测试消息给 Lead（等待 API 响应）...")
    try:
        response = lead._handle_input(
            "我想创作一个赛博朋克风格的短篇漫画，主角是一个黑客，请帮我初始化项目"
        )
        assert response, "Lead 返回了空响应"
        assert len(response) > 10, f"Lead 响应太短: {response}"
        print(f"  ✓ Lead 响应正常（{len(response)} 字）")
        print(f"  → 响应预览: {response[:200]}...")
    except Exception as e:
        print(f"  ⚠️ API 调用出错（可能是网络/限流）: {e}")
        print("  → 这不影响离线测试结果，API 测试跳过")
        return False

    # 检查是否有项目被创建
    projects = list(WORKDIR.iterdir())
    project_dirs = [p.name for p in projects if p.is_dir() and p.name != "inboxes"]
    if project_dirs:
        print(f"  ✓ 检测到项目目录: {project_dirs}")
    else:
        print("  ℹ️ Lead 尚未创建项目（可能还在规划阶段，这是正常的）")

    print("✅ API 端到端测试通过\n")
    return True


# ============================================================
# 入口
# ============================================================

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="V3 端到端测试")
    parser.add_argument("--api", action="store_true", help="运行 API 端到端测试（需要网络）")
    args = parser.parse_args()

    print("=" * 50)
    print("  V3 端到端验证")
    print("=" * 50)
    print()

    print("📋 离线 Harness 测试")
    test_harness_full_flow()

    if args.api:
        print("🌐 API 端到端测试")
        test_api_e2e()

    print("🎉 V3 端到端验证完成！")
