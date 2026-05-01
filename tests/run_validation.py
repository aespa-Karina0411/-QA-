"""工业级统一验证框架 — 单一入口"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from validation.validator_core import run_all_validations


def main():
    print("=" * 60)
    print("    edge-visionQA  Unified Validation Framework")
    print("=" * 60)

    results = run_all_validations()

    all_pass = True

    for r in results:
        print()
        print(f"  [{r.name}]")
        for k, v in r.metrics.items():
            print(f"    {k}: {v}")

        if r.passed:
            print(f"    RESULT: PASS")
        else:
            print(f"    RESULT: FAIL")
            for e in r.errors:
                print(f"      - {e}")
            all_pass = False

    print()
    print("=" * 60)
    if all_pass:
        print("  === FINAL RESULT ===")
        print("         PASS")
    else:
        print("  === FINAL RESULT ===")
        failed = [r.name for r in results if not r.passed]
        print(f"         FAIL  ({', '.join(failed)})")
    print("=" * 60)


if __name__ == "__main__":
    main()
