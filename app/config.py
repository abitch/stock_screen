"""配置加载"""

from pathlib import Path
import yaml

_DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.yaml"


def load_config(path: str = None) -> dict:
    """加载 YAML 配置文件。

    Args:
        path: 配置文件路径,默认项目根目录的 config.yaml

    Returns:
        配置字典
    """
    config_path = Path(path) if path else _DEFAULT_CONFIG_PATH
    if not config_path.exists():
        raise FileNotFoundError(f"配置文件不存在: {config_path}")

    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)
