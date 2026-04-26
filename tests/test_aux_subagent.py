import importlib.util
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def _load_aux_subagent_module():
    mod_path = Path(__file__).parent.parent / "src" / "p3_team" / "aux_subagent.py"
    spec = importlib.util.spec_from_file_location("aux_subagent_module", mod_path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    return module


class _FakeMessage:
    def __init__(self, content: str):
        self.content = content


class _FakeChoice:
    def __init__(self, content: str):
        self.message = _FakeMessage(content)


class _FakeResponse:
    def __init__(self, content: str):
        self.choices = [_FakeChoice(content)]


class _FakeCompletions:
    def __init__(self, content: str):
        self.content = content

    def create(self, **kwargs):
        return _FakeResponse(self.content)


class _FakeChat:
    def __init__(self, content: str):
        self.completions = _FakeCompletions(content)


class _FakeClient:
    def __init__(self, content: str):
        self.chat = _FakeChat(content)


def test_input_classifier_schema():
    mod = _load_aux_subagent_module()
    payload = {
        "input_type": "new_story",
        "confidence": 0.92,
        "key_info": {"topic": "科幻"},
        "suggestion": "建议初始化项目",
    }
    manager = mod.AuxSubagentManager(
        openai_client=_FakeClient(json.dumps(payload, ensure_ascii=False)),
        model="fake-model",
    )
    result = manager.run_input_classifier({"user_text": "我想写赛博朋克故事"})
    assert result["success"] is True
    assert result["data"]["input_type"] == "new_story"


def test_choice_designer_modification_schema():
    mod = _load_aux_subagent_module()
    payload = {
        "mode": "modification",
        "result": {
            "instruction": {
                "target": "林夜.md 的 Level1 视觉锚点",
                "content": "新增：右手旧伤疤",
                "constraints": ["Level 只升不降", "视觉锚点不删"],
            }
        },
    }
    manager = mod.AuxSubagentManager(
        openai_client=_FakeClient(json.dumps(payload, ensure_ascii=False)),
        model="fake-model",
    )
    result = manager.run_choice_designer({"mode": "modification", "feedback_text": "给主角加一个标记"})
    assert result["success"] is True
    assert "instruction" in result["data"]["result"]


if __name__ == "__main__":
    test_input_classifier_schema()
    test_choice_designer_modification_schema()
    print("✅ aux_subagent tests passed")
