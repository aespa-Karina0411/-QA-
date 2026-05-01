"""全局配置单例"""

import os
from core.config_loader import ConfigLoader

CONFIG = ConfigLoader(
    base_dir=os.path.join(os.path.dirname(os.path.dirname(__file__)), "config"),
)
