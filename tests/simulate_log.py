"""Extreme Break-the-System Stress Test — 120s / 200+ entries"""

import json
import random

random.seed(42)

OBJECTS_POOL = ["行人", "汽车", "自行车", "公交车", "摩托车"]
DIRECTIONS = ["左侧", "前方", "右侧"]
DISTANCES = ["较远", "较近", "很近"]
VLM_QUESTIONS = [
    "前面有什么？", "这是什么？", "能帮我看看吗？", "有什么危险吗？",
    "左边那个是什么？", "右边有什么？", "能描述一下环境吗？", "我前面还有多远？",
    "那边是什么颜色？", "有人在我前面吗？", "有没有障碍物？", "帮我读一下那个标志",
    "现在安全吗？", "能走吗？", "到路口还有多远？",
    "这个路口怎么过？", "前面有台阶吗？", "红绿灯什么颜色？", "有没有斑马线？",
    "帮我找一下公交站", "附近有地铁站吗？",
]

_id = [0]


def uid():
    _id[0] += 1
    return _id[0]


def make_entry(t, source, text, queued, played, queue_size=0, vlm_queue_size=0,
               drop_reason=None, priority=None, objects=None, spoke=None,
               vlm_request_time=None, vlm_response_time=None, vlm_played_time=None,
               is_spike=False):
    e = {
        "time": t,
        "source": source,
        "text": text,
        "queued": queued,
        "played": played,
        "queue_size": queue_size,
        "vlm_queue_size": vlm_queue_size,
    }
    if drop_reason:
        e["drop_reason"] = drop_reason
    if priority is not None:
        e["priority"] = priority
    if objects is not None:
        e["objects"] = objects
    if spoke is not None:
        e["spoke"] = spoke
    if vlm_request_time is not None:
        e["vlm_request_time"] = vlm_request_time
    if vlm_response_time is not None:
        e["vlm_response_time"] = vlm_response_time
    if vlm_played_time is not None:
        e["vlm_played_time"] = vlm_played_time
    if is_spike:
        e["is_spike"] = True
    return e


def simulate_arbitration(entries):
    """
    多队列调度器模拟：
    WARNING_QUEUE(max=3) | VLM_QUEUE(max=5) | ENV_QUEUE(max=3)
    调度器: WARNING优先 → VLM保活(>5s) → 加权轮询[VLM,ENV,ENV]
    """
    warn_q = []     # priority=1, max=3, 满则替换最旧
    vlm_q = []      # priority=2, max=5, FIFO丢最旧
    env_q = []      # priority=3, max=3, 满则拒新

    playing_until = 0.0
    last_vlm_play_time = 0.0
    last_accept_time = 0.0
    consecutive_warnings = 0   # WARNING 连续播放计数

    CYCLE = ["VLM", "ENV", "ENV"]
    cycle_idx = 0
    PLAY_DURATION = 2.5

    queue_snapshot = []

    def _pick_next(t_now):
        nonlocal cycle_idx, last_vlm_play_time, consecutive_warnings
        # 1. VLM 保活（最优先非 WARNING 路径）
        if t_now - last_vlm_play_time > 4.0 and vlm_q:
            consecutive_warnings = 0
            return vlm_q.pop()
        # 2. 硬性交错：每 2 个连续 WARNING 后强制 VLM
        if consecutive_warnings >= 2 and vlm_q:
            consecutive_warnings = 0
            return vlm_q.pop()
        # 3. WARNING 优先
        if warn_q:
            consecutive_warnings += 1
            return warn_q.pop(0)
        # 4. ENV 降级
        env_blocked = len(vlm_q) > 3
        if env_blocked and vlm_q:
            consecutive_warnings = 0
            return vlm_q.pop()
        # 5. 加权轮询
        for _ in range(len(CYCLE)):
            target = CYCLE[cycle_idx]
            cycle_idx = (cycle_idx + 1) % len(CYCLE)
            if target == "VLM" and vlm_q:
                consecutive_warnings = 0
                return vlm_q.pop()
            if target == "ENV" and env_q and not env_blocked:
                return env_q.pop(0)
        # 6. 兜底
        if vlm_q:
            consecutive_warnings = 0
            return vlm_q.pop()
        if env_q and not env_blocked:
            return env_q.pop(0)
        return None

    def _total_q():
        return len(warn_q) + len(vlm_q) + len(env_q)

    for e in sorted(entries, key=lambda x: x["time"]):
        t = e["time"]
        src = e["source"]
        prio = e.get("priority", 3)
        req_t = e.get("_request_time", t)
        resp_t = e.get("_response_time", t)
        is_vlm = src == "vlm"

        if is_vlm:
            e["vlm_request_time"] = req_t
            e["vlm_response_time"] = resp_t

        # VLM 8s 超时
        if is_vlm and t - req_t > 8.0:
            e["queued"] = False
            e["played"] = False
            e["drop_reason"] = "expired"
            e["queue_size"] = _total_q()
            queue_snapshot.append((t, _total_q()))
            continue

        # ENV 速率限制
        if prio == 3:
            if t - last_accept_time < 1.5:
                e["queued"] = False
                e["played"] = False
                e["drop_reason"] = "rejected_backpressure"
                e["queue_size"] = _total_q()
                queue_snapshot.append((t, _total_q()))
                continue
            last_accept_time = t

        e["source_queue"] = {1: "WARNING", 2: "VLM", 3: "ENV"}.get(prio, "ENV")

        if t < playing_until:
            # 正在播放中 → 入队
            if prio == 1:
                if len(warn_q) >= 3:
                    warn_q.pop(0)  # 替换最旧
                warn_q.append(e)
                e["queued"] = True
                e["played"] = False
                e["queue_size"] = _total_q()
            elif prio == 2:
                if len(vlm_q) >= 5:
                    vlm_q.pop(0)  # FIFO丢最旧
                vlm_q.append(e)
                e["queued"] = True
                e["played"] = False
                e["queue_size"] = _total_q()
            else:  # prio == 3
                if len(env_q) >= 3:
                    e["queued"] = False
                    e["played"] = False
                    e["drop_reason"] = "rejected_backpressure"
                    e["queue_size"] = _total_q()
                else:
                    env_q.append(e)
                    e["queued"] = True
                    e["played"] = False
                    e["queue_size"] = _total_q()
        else:
            # 可直接播放
            e["queued"] = False
            e["played"] = True
            e["_play_time"] = t
            if is_vlm:
                e["vlm_played_time"] = t
                last_vlm_play_time = t
            e["queue_size"] = _total_q()
            playing_until = t + PLAY_DURATION

            # 调度器 drain：依次取出队列条目播放
            while True:
                qe = _pick_next(playing_until)
                if qe is None:
                    break
                qe["queued"] = True
                qe["played"] = True
                qe["_play_time"] = playing_until
                qe["queue_size"] = _total_q()
                if qe["source"] == "vlm":
                    qe["vlm_played_time"] = playing_until
                    last_vlm_play_time = playing_until
                    qe["vlm_request_time"] = qe.get("_request_time", qe["time"])
                    qe["vlm_response_time"] = qe.get("_response_time", qe["time"])
                playing_until += PLAY_DURATION

        queue_snapshot.append((t, _total_q()))

    for e in entries:
        for k in ("_arrived", "_request_time", "_response_time"):
            e.pop(k, None)

    max_q = max((q for _, q in queue_snapshot), default=0)
    return entries, max_q, queue_snapshot


def generate():
    entries = []
    t = 0.0

    # ==================================================================
    # 背景洪水：decision 每 0.8~1.2s（全程）
    # ==================================================================
    while t <= 120.0:
        is_warning = random.random() < 0.20
        prio = 1 if is_warning else 3
        d1 = random.choice(DIRECTIONS)
        c1 = random.choice(OBJECTS_POOL)
        ds1 = random.choice(DISTANCES)
        txt = f"D#{uid()}: {d1}{c1}{ds1}" + (" WARN" if is_warning else "")
        e = make_entry(t, "decision", txt, False, False,
                       priority=prio, spoke=is_warning)
        entries.append(e)
        t += 0.8 + random.random() * 0.4

    # ==================================================================
    # VLM 高频请求：每 3s 一次（全程）
    # ==================================================================
    for vt in [i * 3.0 for i in range(41)]:
        if vt > 120:
            break
        cat = random.choices([0, 1, 2, 3], weights=[35, 30, 20, 15])[0]
        if cat == 0:
            delay = 0.3 + random.random() * 0.5
        elif cat == 1:
            delay = 1.0 + random.random() * 1.0
        elif cat == 2:
            delay = 3.0 + random.random() * 2.0
        else:
            delay = 6.0 + random.random() * 2.0

        resp_t = vt + delay
        txt = f"V#{uid()}: {random.choice(VLM_QUESTIONS)}"
        e = make_entry(resp_t, "vlm", txt, False, False, priority=2)
        e["_request_time"] = vt
        e["_response_time"] = resp_t
        entries.append(e)

    # ==================================================================
    # 突发冲击 Spike：每 20s 一次（0,20,40,60,80,100,115）
    # 在 2s 内生成 15~25 条混合事件
    # ==================================================================
    for spike_start in [0.0, 20.0, 40.0, 60.0, 80.0, 100.0, 115.0]:
        spike_end = spike_start + 2.0
        count = random.randint(15, 25)
        spike_t = spike_start
        for si in range(count):
            spike_t = spike_start + (si / count) * 2.0
            if spike_t > 120:
                break

            r = random.random()
            if r < 0.15:
                prio = 1
                txt = f"SPW#{uid()}: {random.choice(DIRECTIONS)}{random.choice(OBJECTS_POOL)}危险！"
                e = make_entry(spike_t, "decision", txt, False, False,
                               priority=prio, spoke=True, is_spike=True)
            elif r < 0.40:
                txt = f"SPV#{uid()}: {random.choice(VLM_QUESTIONS)}"
                e = make_entry(spike_t, "vlm", txt, False, False, priority=2, is_spike=True)
                e["_request_time"] = spike_t
                e["_response_time"] = spike_t
            else:
                prio = 3
                txt = f"SPE#{uid()}: {random.choice(DIRECTIONS)}{random.choice(OBJECTS_POOL)}{random.choice(DISTANCES)}"
                e = make_entry(spike_t, "decision", txt, False, False,
                               priority=prio, is_spike=True)
            entries.append(e)

    entries, max_q, snapshots = simulate_arbitration(entries)

    with open("run_log.json", "w", encoding="utf-8") as f:
        json.dump(entries, f, indent=2, ensure_ascii=False, default=str)

    d_count = sum(1 for e in entries if e["source"] == "decision")
    v_count = sum(1 for e in entries if e["source"] == "vlm")
    played = sum(1 for e in entries if e["played"])
    dropped = sum(1 for e in entries if not e.get("played"))
    spike_count = sum(1 for e in entries if e.get("is_spike"))
    from collections import Counter
    drop_counts = Counter(e.get("drop_reason") for e in entries if e.get("drop_reason"))
    print(f"[SIMULATE] {len(entries)} entries (spike={spike_count}), decision={d_count}, vlm={v_count}, played={played}")
    print(f"  dropped={dropped}, max_queue={max_q}")
    for reason, count in sorted(drop_counts.items()):
        print(f"    {reason}: {count}")


if __name__ == "__main__":
    generate()
