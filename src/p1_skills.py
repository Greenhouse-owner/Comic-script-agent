# src/p1_skills.py
"""
P1 层 — Skills 技能层
SkillLoader：管理所有 Skill 手册的加载与校验
"""

import re
from pathlib import Path
from typing import List, Dict, Optional

SKILLS_DIR = Path(__file__).parent.parent / "skills"


class SkillLoader:
    """技能加载器：管理所有 Skill 手册"""

    def __init__(self):
        self.skills_dir = SKILLS_DIR
        self._cache: Dict[str, str] = {}

    def list_skills(self) -> List[Dict[str, str]]:
        """列出所有可用技能"""
        skills = []
        for skill_dir in sorted(self.skills_dir.iterdir()):
            if not skill_dir.is_dir():
                continue
            skill_file = skill_dir / "SKILL.md"
            if not skill_file.exists():
                continue
            content = skill_file.read_text(encoding="utf-8")
            frontmatter = self._parse_frontmatter(content)
            skills.append({
                "name": skill_dir.name,
                "description": frontmatter.get("description", ""),
                "tags": frontmatter.get("tags", [])
            })
        return skills

    def load(self, skill_name: str) -> str:
        """加载指定技能的完整内容"""
        if skill_name in self._cache:
            return self._cache[skill_name]
        skill_file = self.skills_dir / skill_name / "SKILL.md"
        if not skill_file.exists():
            raise FileNotFoundError(f"⚠️ Skill not found: {skill_name}")
        content = skill_file.read_text(encoding="utf-8")
        self._cache[skill_name] = content
        return content

    def validate_contracts(self) -> Dict[str, List[str]]:
        """校验所有技能是否包含必要章节"""
        required_sections = ["输入", "输出格式", "硬约束", "失败处理"]
        issues = {}
        for skill_info in self.list_skills():
            skill_name = skill_info["name"]
            content = self.load(skill_name)
            missing = []
            for section in required_sections:
                if f"## {section}" not in content:
                    missing.append(section)
            if missing:
                issues[skill_name] = missing
        return issues

    def _parse_frontmatter(self, content: str) -> dict:
        """解析 YAML frontmatter（使用 regex，无需 PyYAML）"""
        match = re.match(r'^---\n(.*?)\n---', content, re.DOTALL)
        if not match:
            return {}
        frontmatter_text = match.group(1)
        result = {}
        for line in frontmatter_text.split('\n'):
            if ':' in line:
                key, value = line.split(':', 1)
                key = key.strip()
                value = value.strip()
                if value.startswith('['):
                    value = [v.strip().strip('"\'') for v in value.strip('[]').split(',')]
                result[key] = value
        return result
