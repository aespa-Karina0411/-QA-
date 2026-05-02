"""对比两份 report.json → 输出 diff.txt"""

import json
import sys


THRESHOLDS = {
    "max_wait_time": 4.0,
    "vlm_play_rate": 30.0,
    "warnings_dropped": 0,
}


def compare(old_path, new_path, out_path):
    with open(old_path, "r", encoding="utf-8") as f:
        old = json.load(f)
    with open(new_path, "r", encoding="utf-8") as f:
        new = json.load(f)

    keys = sorted(set(list(old.keys()) + list(new.keys())))
    lines = []

    for k in keys:
        ov = old.get(k, "N/A")
        nv = new.get(k, "N/A")
        if ov == nv:
            lines.append(f"{k}: {ov} -> {nv} (OK)")
            continue

        try:
            diff = round(nv - ov, 2) if isinstance(ov, (int, float)) and isinstance(nv, (int, float)) else 0
            threshold = THRESHOLDS.get(k)
            if isinstance(nv, (int, float)) and isinstance(ov, (int, float)):
                if diff > 0:
                    if threshold and nv > threshold:
                        lines.append(f"{k}: {ov} -> {nv} (+{diff}) !!WARNING!!")
                    else:
                        lines.append(f"{k}: {ov} -> {nv} (+{diff})")
                else:
                    lines.append(f"{k}: {ov} -> {nv} ({diff})")
            else:
                lines.append(f"{k}: {ov} -> {nv}")
        except Exception:
            lines.append(f"{k}: {ov} -> {nv}")

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    print("\n[COMPARE] diff saved to", out_path)
    print("\n".join(lines))


if __name__ == "__main__":
    if len(sys.argv) < 4:
        print("Usage: compare_reports.py <old.json> <new.json> <out.txt>")
        sys.exit(1)
    compare(sys.argv[1], sys.argv[2], sys.argv[3])
