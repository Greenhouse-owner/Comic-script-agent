# src/config.py
"""配置加载（优先 .env，其次 config.json）"""

import json
import os
from pathlib import Path

CONFIG_PATH = Path(__file__).parent.parent / "config.json"
ENV_PATH = Path(__file__).parent.parent / ".env"


def _load_dotenv(path: Path) -> dict:
    """读取 .env 文件（不依赖 python-dotenv）"""
    if not path.exists():
        return {}

    env_vars = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        env_vars[key] = value
    return env_vars


def load_config() -> dict:
    """
    加载配置，优先级：
    1) 进程环境变量
    2) 项目根目录 .env
    3) config.json
    """
    file_cfg = {}
    if CONFIG_PATH.exists():
        file_cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))

    dotenv_cfg = _load_dotenv(ENV_PATH)

    api_key = os.getenv("API_KEY") or dotenv_cfg.get("API_KEY") or file_cfg.get("api_key")
    api_base_url = os.getenv("BASE_URL") or dotenv_cfg.get("BASE_URL") or file_cfg.get("api_base_url")
    model = os.getenv("MODEL_NAME") or dotenv_cfg.get("MODEL_NAME") or file_cfg.get("model")

    missing = []
    if not api_key:
        missing.append("API_KEY")
    if not api_base_url:
        missing.append("BASE_URL")
    if not model:
        missing.append("MODEL_NAME")

    if missing:
        raise FileNotFoundError(
            "缺少配置项: "
            + ", ".join(missing)
            + "。请在环境变量或 .env 中提供，或在 config.json 中提供兼容字段。"
        )

    return {
        "api_key": api_key,
        "api_base_url": api_base_url,
        "model": model,
    }
