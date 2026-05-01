"""配置加载器：YAML 加载 + base/profile 合并 + 点号访问"""

import os
import yaml


class ConfigLoader:
    def __init__(self, base_dir="config", profile=None):
        self._data = {}
        self._base_path = os.path.join(base_dir, "config.yaml")
        self._base_dir = base_dir

        self._load(self._base_path)

        profile = (
            profile
            or os.environ.get("EDGE_VISION_PROFILE")
            or self._data.get("profile", "pc")
        )
        self._merge_profile(profile)

    def _load(self, path):
        if not os.path.exists(path):
            return
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        self._deep_merge(self._data, data or {})

    def _merge_profile(self, profile):
        profile_path = os.path.join(self._base_dir, f"config_{profile}.yaml")
        if os.path.exists(profile_path):
            self._load(profile_path)

    @staticmethod
    def _deep_merge(base, override):
        for key, value in override.items():
            if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                ConfigLoader._deep_merge(base[key], value)
            else:
                base[key] = value

    def get(self, key, default=None):
        """
        点号访问：get("speech.min_interval") → 2.0
        """
        parts = key.split(".")
        node = self._data
        for part in parts:
            if isinstance(node, dict) and part in node:
                node = node[part]
            else:
                return default
        return node

    def all(self):
        return dict(self._data)
