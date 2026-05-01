"""Phase E0 分布级验证 — 分析 DROP_CANDIDATE 模式"""

import re
import sys
from collections import Counter

pattern = re.compile(r"DROP_CANDIDATE.*semantic=(\w+).*queue_size=(\d+)")


def analyze(filepath):
    semantic_cnt = Counter()
    queue_cnt = Counter()
    total = 0

    try:
        content = None
        for enc in ["utf-8", "utf-16-le", "gbk"]:
            try:
                with open(filepath, "r", encoding=enc) as f:
                    content = f.read()
                break
            except (UnicodeDecodeError, UnicodeError):
                continue
        if content is None:
            print(f"\n=== {filepath} ===")
            print("CANNOT DECODE")
            return

        total = 0
        semantic_cnt = Counter()
        queue_cnt = Counter()
        for line in content.splitlines():
            m = pattern.search(line)
            if m:
                semantic = m.group(1)
                q = int(m.group(2))
                semantic_cnt[semantic] += 1
                queue_cnt[q] += 1
                total += 1
    except FileNotFoundError:
        print(f"\n=== {filepath} ===")
        print("FILE NOT FOUND")
        return

    print(f"\n=== {filepath} ===")
    print(f"total:       {total}")
    print(f"semantic:    {dict(semantic_cnt)}")
    print(f"queue_size:  {dict(sorted(queue_cnt.items()))}")

    if total > 0:
        low_ratio = semantic_cnt.get("LOW", 0) / total
        print(f"LOW ratio:   {low_ratio:.1%}")

        q_max = max(queue_cnt.keys()) if queue_cnt else 0
        critical_q = sum(queue_cnt.get(q, 0) for q in (q_max - 1, q_max))
        q_ratio = critical_q / total
        print(f"critical Q ratio: {q_ratio:.1%} (queue at max-1 or max)")
    print()


if __name__ == "__main__":
    files = sys.argv[1:] if len(sys.argv) > 1 else ["log_normal.txt", "log_stress.txt"]
    for f in files:
        analyze(f)
