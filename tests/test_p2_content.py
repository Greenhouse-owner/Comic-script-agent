# tests/test_p2_content.py

import sys
import shutil
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from p0_runtime import WORKDIR
from p2_content import (
    comic_init_project, comic_read_character, comic_write_character,
    comic_list_characters, comic_read_environment, comic_write_environment,
    comic_list_environments
)


def cleanup(project_id):
    p = WORKDIR / project_id
    if p.exists():
        shutil.rmtree(p)


def test_project_init():
    cleanup("test_v3")
    result = comic_init_project("test_v3", "V3测试故事", num_chapters=3)
    assert "test_v3" in result["project_path"]
    p = WORKDIR / "test_v3"
    assert (p / "brief.md").exists()
    assert (p / "state" / "characters").is_dir()
    assert (p / "state" / "environments").is_dir()
    assert (p / "chapters" / "ch01").is_dir()
    assert (p / "chapters" / "ch02").is_dir()
    assert (p / "chapters" / "ch03").is_dir()
    # 不应该有 JSON 文件
    assert not (p / "state" / "characters.json").exists()
    assert not (p / "state" / "environments.json").exists()
    # 重复创建应报错
    try:
        comic_init_project("test_v3", "重复")
        assert False, "应该抛出 ValueError"
    except ValueError:
        pass
    cleanup("test_v3")
    print("✅ project_init (V3)")


def test_character_file_ops():
    cleanup("test_char_v3")
    comic_init_project("test_char_v3", "测试", num_chapters=1)

    # 写入角色文件
    content = """# 林夜 | 主角 | Level 1 🔵

> 首次登场: ch01 | 上次更新: ch01

## Level 1 — 轮廓 (ch01 创建)
- **视觉锚点**:
  1. 黑色过膝风衣
  2. 银色碎发
  3. 左眼金色竖瞳
- **一句话定位**: 在夜色中游荡的孤独黑客
- **性格特质**: 沉默寡言但内心炽热
- **关键关系**:
  - 李雨: 青梅竹马
"""
    result = comic_write_character("test_char_v3", "林夜", content)
    assert result["success"]

    # 读取
    read_back = comic_read_character("test_char_v3", "林夜")
    assert "黑色过膝风衣" in read_back
    assert "Level 1" in read_back

    # 列表
    names = comic_list_characters("test_char_v3")
    assert names == ["林夜"]

    # 写入第二个角色
    comic_write_character("test_char_v3", "黑狐", "# 黑狐 | 配角 | Level 1 🔵\n")
    names = comic_list_characters("test_char_v3")
    assert "林夜" in names and "黑狐" in names

    # 不存在的角色应报错
    try:
        comic_read_character("test_char_v3", "不存在")
        assert False, "应该抛出 FileNotFoundError"
    except FileNotFoundError:
        pass

    cleanup("test_char_v3")
    print("✅ character_file_ops (V3)")


def test_environment_file_ops():
    cleanup("test_env_v3")
    comic_init_project("test_env_v3", "测试", num_chapters=1)

    content = """# 老城区街头 | 主场景 | Level 1 🔵

> 首次出现: ch01 | 上次更新: ch01

## Level 1 — 轮廓 (ch01 创建)
- **地理位置**: 城市东区
- **一句话氛围**: 霓虹灯与阴影交错的赛博朋克街巷
- **时间段**: 深夜
- **关键道具**:
  1. 霓虹灯招牌
  2. 地面积水
  3. 破旧贩卖机
- **详细描述**: 曾经繁华的商业街，霓虹招牌大多半坏。
"""
    result = comic_write_environment("test_env_v3", "老城区街头", content)
    assert result["success"]

    read_back = comic_read_environment("test_env_v3", "老城区街头")
    assert "霓虹灯招牌" in read_back

    names = comic_list_environments("test_env_v3")
    assert names == ["老城区街头"]

    # 不存在的场景应报错
    try:
        comic_read_environment("test_env_v3", "不存在")
        assert False, "应该抛出 FileNotFoundError"
    except FileNotFoundError:
        pass

    cleanup("test_env_v3")
    print("✅ environment_file_ops (V3)")


if __name__ == "__main__":
    test_project_init()
    test_character_file_ops()
    test_environment_file_ops()
    print("\n🎉 P2 层测试全部通过 (V3)")
