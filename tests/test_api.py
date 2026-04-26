# tests/test_api.py
"""API 连通性测试"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from openai import OpenAI
from config import load_config


def test_basic_chat():
    """测试基本对话"""
    config = load_config()
    client = OpenAI(api_key=config["api_key"], base_url=config["api_base_url"])

    print(f"📡 测试连接: {config['api_base_url']}")
    print(f"🤖 模型: {config['model']}")

    response = client.chat.completions.create(
        model=config["model"],
        messages=[{"role": "user", "content": "用一句话介绍你自己"}]
    )

    content = response.choices[0].message.content
    print(f"✅ 基本对话成功: {content[:80]}...")
    return True


def test_tool_calling():
    """测试 function calling"""
    config = load_config()
    client = OpenAI(api_key=config["api_key"], base_url=config["api_base_url"])

    tools = [{
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "获取天气",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {"type": "string", "description": "城市名"}
                },
                "required": ["city"]
            }
        }
    }]

    response = client.chat.completions.create(
        model=config["model"],
        messages=[{"role": "user", "content": "北京今天天气怎么样？"}],
        tools=tools
    )

    choice = response.choices[0]
    if choice.finish_reason == "tool_calls":
        tc = choice.message.tool_calls[0]
        print(f"✅ Tool calling 成功: {tc.function.name}({tc.function.arguments})")
    else:
        print(f"⚠️ 模型未调用工具 (finish_reason={choice.finish_reason})")
        print(f"   回复: {choice.message.content[:80]}")
    return True


if __name__ == "__main__":
    try:
        test_basic_chat()
        print()
        test_tool_calling()
        print("\n🎉 API 测试完成")
    except Exception as e:
        print(f"\n❌ 测试失败: {e}")
        sys.exit(1)
