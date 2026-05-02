"""自动评估流水线：simulate → evaluate → compare → archive"""

import json
import os
import shutil
import subprocess
import sys
import time


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(BASE_DIR)
HISTORY_DIR = os.path.join(BASE_DIR, "history")
os.makedirs(HISTORY_DIR, exist_ok=True)


def run_simulate():
    """Step 1: 运行 simulate_log 生成日志"""
    sim = os.path.join(PROJECT_DIR, "tests", "simulate_log.py")
    out = os.path.join(PROJECT_DIR, "analysis", "full_run.jsonl")

    print("[PIPE] Running simulate_log.py ...")
    result = subprocess.run(
        [sys.executable, sim],
        cwd=PROJECT_DIR,
        capture_output=True, text=True, timeout=60,
    )
    if result.returncode != 0:
        print("[PIPE] ERROR: simulate_log failed")
        print(result.stderr[:500])
        sys.exit(1)

    # Copy run_log.json to analysis/full_run.jsonl
    src = os.path.join(PROJECT_DIR, "run_log.json")
    if os.path.exists(src):
        with open(src, "r", encoding="utf-8") as f:
            data = json.load(f)
        with open(out, "w", encoding="utf-8") as f:
            for entry in data:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        print(f"[PIPE] Saved {len(data)} entries to full_run.jsonl")
    else:
        print("[PIPE] ERROR: run_log.json not found")
        sys.exit(1)

    return out


def run_evaluate(log_path):
    """Step 3: 评估日志并生成 report.json 和 summary.txt"""
    evaluate_path = os.path.join(BASE_DIR, "evaluate_scheduler.py")
    if not os.path.exists(evaluate_path):
        print("[PIPE] ERROR: evaluate_scheduler.py not found")
        sys.exit(1)

    result = subprocess.run(
        [sys.executable, evaluate_path, log_path],
        cwd=PROJECT_DIR,
        capture_output=True, text=True, timeout=60,
    )
    print(result.stdout[-500:] if len(result.stdout) > 500 else result.stdout)
    if result.returncode != 0:
        print("[PIPE] evaluate_scheduler returned non-zero")


def archive_run():
    """Step 4: 保存本次评估结果到 history/"""
    ts = time.strftime("EVAL_%Y%m%d_%H%M%S")
    run_dir = os.path.join(HISTORY_DIR, ts)
    os.makedirs(run_dir, exist_ok=True)

    for fname in ["report.json", "summary.txt"]:
        src = os.path.join(BASE_DIR, fname)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(run_dir, fname))

    # meta.json
    git_commit = "unknown"
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=PROJECT_DIR, capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0:
            git_commit = r.stdout.strip()
    except Exception:
        pass

    meta = {
        "timestamp": ts,
        "git_commit": git_commit,
        "notes": "auto run",
    }
    with open(os.path.join(run_dir, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)

    print(f"[PIPE] Archived to {run_dir}")
    return run_dir


def compare_with_previous(current_dir):
    """Step 5: 与上一次结果对比"""
    runs = sorted(
        [d for d in os.listdir(HISTORY_DIR) if d.startswith("EVAL_") and d != os.path.basename(current_dir)],
        reverse=True,
    )
    if not runs:
        print("[PIPE] No previous run to compare")
        return

    prev_dir = os.path.join(HISTORY_DIR, runs[0])
    old_report = os.path.join(prev_dir, "report.json")
    new_report = os.path.join(current_dir, "report.json")

    if not os.path.exists(old_report) or not os.path.exists(new_report):
        print("[PIPE] report.json missing in one run, skip compare")
        return

    compare_path = os.path.join(BASE_DIR, "compare_reports.py")
    diff_out = os.path.join(current_dir, "diff.txt")

    result = subprocess.run(
        [sys.executable, compare_path, old_report, new_report, diff_out],
        cwd=PROJECT_DIR,
        capture_output=True, text=True, timeout=30,
    )
    print(result.stdout[-500:] if len(result.stdout) > 500 else result.stdout)


def print_summary(report_path):
    """Step 6: 打印终端摘要"""
    try:
        with open(report_path, "r", encoding="utf-8") as f:
            report = json.load(f)
    except Exception:
        return

    print("\n=== EVALUATION SUMMARY ===")
    for k in ["max_wait_time", "deadlock_count", "vlm_play_rate", "output_rate", "vlm_starved"]:
        if k in report:
            print(f"  {k}: {report[k]}")


def print_diff(current_dir):
    diff_path = os.path.join(current_dir, "diff.txt")
    if os.path.exists(diff_path):
        print("\n=== DIFF VS LAST ===")
        with open(diff_path, "r", encoding="utf-8") as f:
            print(f.read())


def main():
    print("[PIPE] Starting evaluation pipeline ...")

    # Step 1+3: simulate + evaluate
    log_path = run_simulate()
    run_evaluate(log_path)

    # Step 4: archive
    run_dir = archive_run()

    # Step 5: compare
    compare_with_previous(run_dir)

    # Step 6: summary
    report_path = os.path.join(run_dir, "report.json")
    print_summary(report_path)
    print_diff(run_dir)

    print("\n[PIPE] Done.")


if __name__ == "__main__":
    main()
