"""Phase C Step 1A FINAL CHECK — Config Architecture Audit"""
import sys, os

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

passed = True
warnings = []

def chk(label, ok, crit=True):
    global passed
    state = "PASS" if ok else ("FAIL" if crit else "WARN")
    print(f"  [{state}] {label}")
    if not ok and crit:
        passed = False

print("=" * 60)
print("  Phase C Step 1A — FINAL CONFIG AUDIT")
print("=" * 60)

# ── 1. File Structure ──
print("\n--- 1. File Structure ---")
for f in ["config/config.yaml", "config/config_pc.yaml", "config/config_pi.yaml",
          "core/config_loader.py", "core/global_config.py"]:
    ok = os.path.exists(os.path.join(BASE, f))
    chk(f, ok)

# ── 2. ConfigLoader Code Audit ──
print("\n--- 2. ConfigLoader Code Audit ---")
import core.config_loader as cl_mod

src = open(os.path.join(BASE, "core/config_loader.py"), "r", encoding="utf-8").read()

# A. Load order: _load(base) then _merge_profile
has_load_base_first = "_load(self._base_path)" in src
has_merge_profile_after = "_merge_profile(profile)" in src
load_idx = src.index("_load(self._base_path)") if "_load(self._base_path)" in src else 0
merge_idx = src.index("_merge_profile(profile)") if "_merge_profile(profile)" in src else 0
chk("A1: base loaded before profile", has_load_base_first and merge_idx > load_idx)

# A2. Profile resolution priority
has_explicit = "profile = (" in src
has_env = 'os.environ.get("EDGE_VISION_PROFILE")' in src
has_yaml_fallback = 'self._data.get("profile"' in src
env_idx = src.index('os.environ.get("EDGE_VISION_PROFILE")') if 'os.environ.get("EDGE_VISION_PROFILE")' in src else 0
data_idx = src.index('self._data.get("profile"') if 'self._data.get("profile"' in src else 0
chk("A2: priority = explicit > env > yaml", has_explicit and has_env and has_yaml_fallback and env_idx < data_idx)

# B. Deep merge
has_deep_merge = 'isinstance(base[key], dict)' in src
chk("B: recursive deep merge", has_deep_merge)

# C. get() interface
has_get_dot = 'key.split(".")' in src
has_default = 'return default' in src
chk("C: get() supports dot-notation + default", has_get_dot and has_default)

# D. Path robustness
has_base_dir = 'base_dir' in src and 'os.path.join(base_dir, "config.yaml")' in src
chk("D: uses config/ dir via base_dir", has_base_dir)

# ── 3. global_config Audit ──
print("\n--- 3. global_config Audit ---")
gsrc = open(os.path.join(BASE, "core/global_config.py"), "r", encoding="utf-8").read()
is_singleton = "CONFIG =" in gsrc and "ConfigLoader(" in gsrc
no_print = "print(" not in gsrc
chk("CONFIG singleton, no side-effects", is_singleton and no_print)

# ── 4. YAML Structure ──
print("\n--- 4. YAML Structure ---")
import yaml
base_yaml = yaml.safe_load(open(os.path.join(BASE, "config/config.yaml"), "r", encoding="utf-8"))
chk("config.yaml has profile key", "profile" in base_yaml)

pc = yaml.safe_load(open(os.path.join(BASE, "config/config_pc.yaml"), "r", encoding="utf-8"))
pi = yaml.safe_load(open(os.path.join(BASE, "config/config_pi.yaml"), "r", encoding="utf-8"))

# Count overlapping keys with base
def count_duplicates(base, override):
    dups = 0
    for k, v in override.items():
        if k in base:
            if isinstance(v, dict) and isinstance(base[k], dict):
                dups += count_duplicates(base[k], v)
            elif v == base[k]:
                dups += 1
    return dups

pc_dups = count_duplicates(base_yaml, pc)
pi_dups = count_duplicates(base_yaml, pi)
chk(f"config_pc.yaml duplicates: {pc_dups} (0 expected)", pc_dups == 0, crit=False)
chk(f"config_pi.yaml duplicates: {pi_dups} (0 expected)", pi_dups == 0, crit=False)

# ── 5. Runtime Tests ──
print("\n--- 5. Runtime Tests ---")

# Test 1: Basic load
from core.global_config import CONFIG
profile = CONFIG.get("profile")
chk(f"Test1: basic load → profile={profile}", isinstance(profile, str) and profile in ("pc", "pi"))

# Test 2: Env override
os.environ["EDGE_VISION_PROFILE"] = "pi"
import importlib
import core.global_config as gc_mod
importlib.reload(gc_mod)
CONFIG2 = gc_mod.CONFIG
min_interval = CONFIG2.get("speech.min_interval")
chk(f"Test2: env=pi → speech.min_interval={min_interval} (expect 2.0)", min_interval == 2.0)

# Test 3: Fallback
del os.environ["EDGE_VISION_PROFILE"]
importlib.reload(gc_mod)
CONFIG3 = gc_mod.CONFIG
min_interval3 = CONFIG3.get("speech.min_interval")
chk(f"Test3: no env → speech.min_interval={min_interval3} (expect 1.0 from pc yaml)", min_interval3 == 1.0)

# Test 4: System integrity — can CONFIG.get be called from all consumers?
from perception.speech_arbitrator import SpeechArbitrator
sa = SpeechArbitrator()
chk("Test4a: SpeechArbitrator imports CONFIG (survives init)", True)

from vlm.vlm_manager import VLMManager
chk("Test4b: VLMManager imports CONFIG (survives import)", True)

# ── 6. Configuration Consistency ──
print("\n--- 6. Configuration Consistency ---")

# Check for multiple ConfigLoader instances
multi_instance = src.count("ConfigLoader(") > 1
chk("Single ConfigLoader instance", not multi_instance)

# Check config.py compat layer
cfg_src = open(os.path.join(BASE, "config.py"), "r", encoding="utf-8").read()
is_compat = "from core.global_config import CONFIG" in cfg_src
chk("config.py is compat layer (bridges CONFIG)", is_compat)

# ── FINAL ──
print("\n" + "=" * 60)
if passed:
    print("  FINAL RESULT: PASS")
    print("  CONFIDENCE: HIGH")
else:
    print("  FINAL RESULT: FAIL")
    print("  CONFIDENCE: LOW")
print("=" * 60)
