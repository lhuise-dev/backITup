import json
from pathlib import Path

from core.logger import get_logger

logger = get_logger()

_DATA_FILE = Path(__file__).parent.parent / "data" / "backup_systems.json"


class ConfigManager:
    def __init__(self):
        _DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
        if not _DATA_FILE.exists():
            _DATA_FILE.write_text("[]", encoding="utf-8")

    def load_all(self) -> list:
        try:
            return json.loads(_DATA_FILE.read_text(encoding="utf-8"))
        except Exception as e:
            logger.error(f"Failed to load config: {e}")
            return []

    def _save_all(self, configs: list):
        try:
            _DATA_FILE.write_text(json.dumps(configs, indent=2), encoding="utf-8")
        except Exception as e:
            logger.error(f"Failed to save config: {e}")

    def add(self, config: dict):
        configs = self.load_all()
        configs.append(config)
        self._save_all(configs)

    def update(self, name: str, updated: dict):
        configs = self.load_all()
        for i, c in enumerate(configs):
            if c["name"] == name:
                configs[i] = updated
                break
        self._save_all(configs)

    def remove(self, name: str):
        configs = [c for c in self.load_all() if c["name"] != name]
        self._save_all(configs)

    def get(self, name: str) -> dict | None:
        return next((c for c in self.load_all() if c["name"] == name), None)
