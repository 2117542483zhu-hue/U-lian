"""判定规则的自检脚本。

不依赖视频和模型，用构造出来的关键点验证几何计算与阈值判断是否正确。
运行方式：python test_rules.py
"""

from __future__ import annotations

from pose_rules import LM, analyze_metrics_sequence, frame_metrics

PASSED = 0
FAILED = 0


def build_landmarks(points: dict) -> list:
    """构造 33 个关键点，未指定的位置填默认值。"""
    landmarks = [{"x": 0.5, "y": 0.5, "z": 0.0, "visibility": 0.9} for _ in range(33)]
    for name, (x, y, visibility) in points.items():
        landmarks[LM[name]] = {"x": x, "y": y, "z": 0.0, "visibility": visibility}
    return landmarks


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if condition:
        PASSED += 1
        print(f"[通过] {name}")
    else:
        FAILED += 1
        print(f"[失败] {name} {detail}")


def test_frame_metrics() -> None:
    standing = build_landmarks(
        {
            "left_shoulder": (0.48, 0.20, 0.95),
            "right_shoulder": (0.52, 0.20, 0.95),
            "left_hip": (0.48, 0.45, 0.95),
            "right_hip": (0.52, 0.45, 0.95),
            "left_knee": (0.49, 0.62, 0.95),
            "right_knee": (0.53, 0.62, 0.95),
            "left_ankle": (0.48, 0.80, 0.95),
            "right_ankle": (0.52, 0.80, 0.95),
        }
    )
    metrics = frame_metrics(standing)
    check(
        "站立时膝关节接近伸直",
        metrics["knee_angle"] is not None and metrics["knee_angle"] > 150,
        f"实际 {metrics['knee_angle']}",
    )
    check(
        "站立时躯干基本竖直",
        metrics["torso_lean"] is not None and metrics["torso_lean"] < 10,
        f"实际 {metrics['torso_lean']}",
    )

    deep = build_landmarks(
        {
            "left_shoulder": (0.42, 0.30, 0.95),
            "right_shoulder": (0.46, 0.30, 0.95),
            "left_hip": (0.40, 0.60, 0.95),
            "right_hip": (0.44, 0.60, 0.95),
            "left_knee": (0.55, 0.62, 0.95),
            "right_knee": (0.59, 0.62, 0.95),
            "left_ankle": (0.55, 0.80, 0.95),
            "right_ankle": (0.59, 0.80, 0.95),
        }
    )
    deep_metrics = frame_metrics(deep)
    check(
        "深蹲最低点膝关节角度偏小",
        deep_metrics["knee_angle"] is not None and deep_metrics["knee_angle"] < 110,
        f"实际 {deep_metrics['knee_angle']}",
    )

    valgus = build_landmarks(
        {
            "left_shoulder": (0.35, 0.20, 0.95),
            "right_shoulder": (0.65, 0.20, 0.95),
            "left_hip": (0.44, 0.45, 0.95),
            "right_hip": (0.56, 0.45, 0.95),
            "left_knee": (0.46, 0.62, 0.95),
            "right_knee": (0.54, 0.62, 0.95),
            "left_ankle": (0.40, 0.80, 0.95),
            "right_ankle": (0.60, 0.80, 0.95),
        }
    )
    valgus_metrics = frame_metrics(valgus)
    check(
        "膝盖内移能被量化为比例",
        valgus_metrics["valgus_ratio"] is not None and valgus_metrics["valgus_ratio"] > 0.15,
        f"实际 {valgus_metrics['valgus_ratio']}",
    )

    aligned = build_landmarks(
        {
            "left_shoulder": (0.35, 0.20, 0.95),
            "right_shoulder": (0.65, 0.20, 0.95),
            "left_hip": (0.44, 0.45, 0.95),
            "right_hip": (0.56, 0.45, 0.95),
            "left_knee": (0.40, 0.62, 0.95),
            "right_knee": (0.60, 0.62, 0.95),
            "left_ankle": (0.40, 0.80, 0.95),
            "right_ankle": (0.60, 0.80, 0.95),
        }
    )
    aligned_metrics = frame_metrics(aligned)
    check(
        "膝盖对准脚尖时不报膝内扣",
        aligned_metrics["valgus_ratio"] is not None and aligned_metrics["valgus_ratio"] < 0.05,
        f"实际 {aligned_metrics['valgus_ratio']}",
    )

    # 侧面拍摄时远侧肢体被身体挡住，可见度会降到 0.3 以下，这是正常现象
    occluded = build_landmarks(
        {
            "left_shoulder": (0.48, 0.20, 0.95),
            "right_shoulder": (0.50, 0.20, 0.20),
            "left_hip": (0.48, 0.45, 0.95),
            "right_hip": (0.50, 0.45, 0.20),
            "left_knee": (0.55, 0.62, 0.90),
            "right_knee": (0.20, 0.30, 0.10),
            "left_ankle": (0.55, 0.80, 0.88),
            "right_ankle": (0.20, 0.30, 0.10),
        }
    )
    occluded_metrics = frame_metrics(occluded)
    check(
        "远侧被遮挡时采用近侧关键点",
        occluded_metrics["visibility"] > 0.5
        and occluded_metrics["knee_angle"] is not None
        and abs(occluded_metrics["knee_angle"] - occluded_metrics["knee_angle_left"]) < 1e-6
        and occluded_metrics["knee_angle_right"] is None,
        f"可见度 {occluded_metrics['visibility']}，膝角 {occluded_metrics['knee_angle']}",
    )
    check(
        "远侧被遮挡时不做膝内扣判断",
        occluded_metrics["valgus_ratio"] is None,
        f"实际 {occluded_metrics['valgus_ratio']}",
    )


def metrics(knee: float, hip: float, lean: float, valgus: float, visibility: float = 0.9) -> dict:
    return {
        "knee_angle": knee,
        "hip_angle": hip,
        "torso_lean": lean,
        "valgus_ratio": valgus,
        "visibility": visibility,
    }


def normal_sequence() -> list:
    """动作标准的深蹲：下蹲到位，起身髋膝同步。"""
    sequence = []
    for step in range(10):
        ratio = step / 9
        sequence.append(metrics(170 - 72 * ratio, 170 - 95 * ratio, 8 + 12 * ratio, 0.02))
    for step in range(10):
        ratio = step / 9
        sequence.append(metrics(98 + 72 * ratio, 75 + 95 * ratio, 20 - 12 * ratio, 0.02))
    return sequence


def test_sequence_judgement() -> None:
    normal = analyze_metrics_sequence(normal_sequence(), view="side")
    check("标准深蹲不报问题", normal["status"] == "ok" and not normal["issues"], str(normal.get("summary")))

    shallow = analyze_metrics_sequence(
        [metrics(170 - 40 * (step / 9), 170 - 60 * (step / 9), 12, 0.02) for step in range(10)]
        + [metrics(130 + 40 * (step / 9), 110 + 60 * (step / 9), 12, 0.02) for step in range(10)],
        view="side",
    )
    shallow_keys = [item["key"] for item in shallow["issues"]]
    check("下蹲深度不足能被检出", "shallow_depth" in shallow_keys, str(shallow_keys))

    valgus_sequence = analyze_metrics_sequence(
        [metrics(170 - 72 * (step / 9), 170 - 95 * (step / 9), 15, 0.05 + 0.20 * (step / 9)) for step in range(10)]
        + [metrics(98 + 72 * (step / 9), 75 + 95 * (step / 9), 18, 0.25 - 0.20 * (step / 9)) for step in range(10)],
        view="front",
    )
    valgus_keys = [item["key"] for item in valgus_sequence["issues"]]
    check("正面视角能检出膝内扣", "knee_valgus" in valgus_keys, str(valgus_keys))

    same_but_side = analyze_metrics_sequence(
        [metrics(170 - 72 * (step / 9), 170 - 95 * (step / 9), 15, 0.05 + 0.20 * (step / 9)) for step in range(10)]
        + [metrics(98 + 72 * (step / 9), 75 + 95 * (step / 9), 18, 0.25 - 0.20 * (step / 9)) for step in range(10)],
        view="side",
    )
    check(
        "侧面视角不判断膝内扣",
        "knee_valgus" not in [item["key"] for item in same_but_side["issues"]],
        str([item["key"] for item in same_but_side["issues"]]),
    )

    lean_sequence = analyze_metrics_sequence(
        [metrics(170 - 72 * (step / 9), 170 - 95 * (step / 9), 30 + 25 * (step / 9), 0.02) for step in range(10)]
        + [metrics(98 + 72 * (step / 9), 75 + 95 * (step / 9), 55 - 25 * (step / 9), 0.02) for step in range(10)],
        view="side",
    )
    check(
        "躯干过度前倾能被检出",
        "excessive_lean" in [item["key"] for item in lean_sequence["issues"]],
        str([item["key"] for item in lean_sequence["issues"]]),
    )

    hip_first_sequence = [metrics(170, 170, 12, 0.02)]
    for _ in range(8):
        hip_first_sequence.append(metrics(100, 70, 20, 0.02))
    hip_first_sequence.append(metrics(105, 165, 12, 0.02))
    hip_first_sequence.append(metrics(120, 168, 10, 0.02))
    hip_first_sequence.append(metrics(140, 170, 9, 0.02))
    hip_first_sequence.append(metrics(165, 170, 8, 0.02))
    hip_first_result = analyze_metrics_sequence(hip_first_sequence, view="side")
    check(
        "起身髋部先动能被检出",
        "hip_first" in [item["key"] for item in hip_first_result["issues"]],
        str(hip_first_result["summary"]),
    )

    low_visibility = analyze_metrics_sequence(
        [metrics(150, 150, 12, 0.02, visibility=0.2) for _ in range(20)],
        view="side",
    )
    check(
        "可信度低时不出结论",
        low_visibility["status"] == "insufficient_frames",
        str(low_visibility["status"]),
    )


# ------------------------------------------------ 硬拉


def deadlift_metrics(knee: float, hip: float, lean: float) -> dict:
    return {
        "knee_angle": knee,
        "hip_angle": hip,
        "signed_lean": lean,
        "torso_lean": abs(lean),
        "visibility": 0.9,
        "plausible": True,
    }


def test_deadlift_rules() -> None:
    def build(start_lean: float):
        frames = []
        for step in range(14):
            ratio = step / 13
            frames.append(deadlift_metrics(100 + 75 * ratio, 90 + 80 * ratio, start_lean - (start_lean - 4) * ratio))
        return frames

    normal = analyze_metrics_sequence(build(45), view="side", exercise="deadlift")
    check("硬拉起始角度正常时不报问题", not normal["issues"], str(normal.get("summary")))

    bad_start = analyze_metrics_sequence(build(72), view="side", exercise="deadlift")
    keys = [item["key"] for item in bad_start["issues"]]
    check("硬拉起始过度前倾能被检出", "deadlift_start_lean" in keys, str(keys))

    half = analyze_metrics_sequence(
        [deadlift_metrics(100 + 50 * (step / 13), 90 + 60 * (step / 13), 30) for step in range(14)],
        view="side",
        exercise="deadlift",
    )
    keys = [item["key"] for item in half["issues"]]
    check("硬拉没有站直能被检出", "deadlift_not_locked" in keys, str(keys))

    over = analyze_metrics_sequence(
        [deadlift_metrics(100 + 75 * (step / 9), 90 + 80 * (step / 9), 30) for step in range(10)]
        + [deadlift_metrics(175, 170 + step, -14) for step in range(5)],
        view="side",
        exercise="deadlift",
    )
    keys = [item["key"] for item in over["issues"]]
    check("硬拉锁定过度后仰能被检出", "deadlift_over_extension" in keys, str(keys))


# ------------------------------------------------ 卧推


def bench_metrics(elbow: float, hip_lift: float = 8, asymmetry: float = 6) -> dict:
    return {
        "elbow_angle": elbow,
        "elbow_asymmetry": asymmetry,
        "hip_lift_angle": hip_lift,
        "knee_angle": 150,
        "hip_angle": 150,
        "torso_lean": 10,
        "visibility": 0.9,
        "plausible": True,
    }


def test_bench_rules() -> None:
    def build(bottom: float, top: float = 172, hip_lift: float = 8, asymmetry: float = 6):
        frames = []
        for step in range(10):
            frames.append(bench_metrics(top - (top - bottom) * (step / 9), hip_lift, asymmetry))
        for step in range(10):
            frames.append(bench_metrics(bottom + (top - bottom) * (step / 9), hip_lift, asymmetry))
        return frames

    normal = analyze_metrics_sequence(build(85), view="side", exercise="bench_press")
    check("卧推动作完整时不报问题", not normal["issues"], str(normal.get("summary")))

    shallow = analyze_metrics_sequence(build(125), view="side", exercise="bench_press")
    keys = [item["key"] for item in shallow["issues"]]
    check("卧推下放不足能被检出", "bench_shallow" in keys, str(keys))

    not_pressed = analyze_metrics_sequence(build(85, top=140), view="side", exercise="bench_press")
    keys = [item["key"] for item in not_pressed["issues"]]
    check("卧推顶部没推直能被检出", "bench_not_pressed" in keys, str(keys))

    hip = analyze_metrics_sequence(build(85, hip_lift=30), view="side", exercise="bench_press")
    keys = [item["key"] for item in hip["issues"]]
    check("卧推臀部离凳能被检出", "bench_hip_lift" in keys, str(keys))

    uneven = analyze_metrics_sequence(build(85, asymmetry=22), view="side", exercise="bench_press")
    keys = [item["key"] for item in uneven["issues"]]
    check("卧推两侧发力不均能被检出", "bench_asymmetric" in keys, str(keys))


# ------------------------------------------------ 俯卧撑


def pushup_metrics(elbow: float, body_line: float) -> dict:
    return {
        "elbow_angle": elbow,
        "body_line_angle": body_line,
        "knee_angle": 170,
        "hip_angle": body_line,
        "torso_lean": 10,
        "visibility": 0.9,
        "plausible": True,
    }


def test_pushup_rules() -> None:
    def build(bottom: float, body_min: float = 168):
        frames = []
        for step in range(10):
            ratio = step / 9
            frames.append(pushup_metrics(170 - (170 - bottom) * ratio, 170 - (170 - body_min) * ratio))
        for step in range(10):
            ratio = step / 9
            frames.append(pushup_metrics(bottom + (170 - bottom) * ratio, body_min + (170 - body_min) * ratio))
        return frames

    normal = analyze_metrics_sequence(build(85), view="side", exercise="pushup")
    check("俯卧撑动作标准时不报问题", not normal["issues"], str(normal.get("summary")))

    shallow = analyze_metrics_sequence(build(125), view="side", exercise="pushup")
    keys = [item["key"] for item in shallow["issues"]]
    check("俯卧撑下放不足能被检出", "pushup_shallow" in keys, str(keys))

    sagging = analyze_metrics_sequence(build(85, body_min=140), view="side", exercise="pushup")
    keys = [item["key"] for item in sagging["issues"]]
    check("俯卧撑身体不成直线能被检出", "pushup_body_line" in keys, str(keys))


# ------------------------------------------------ 引体向上


def pullup_metrics(elbow: float, chin: float, hip_x: float = 0.5, shoulder_width: float = 0.3) -> dict:
    return {
        "elbow_angle": elbow,
        "chin_margin": chin,
        "hip_mid_x": hip_x,
        "shoulder_width": shoulder_width,
        "knee_angle": 160,
        "hip_angle": 150,
        "torso_lean": 8,
        "visibility": 0.9,
        "plausible": True,
    }


def test_pullup_rules() -> None:
    def build(chin_top: float = 0.15, hang_elbow: float = 175, hip_fn=None):
        frames = []
        for step in range(10):
            ratio = step / 9
            frames.append(pullup_metrics(175 - (175 - 40) * ratio, 0 + chin_top * ratio, hip_fn(ratio) if hip_fn else 0.5))
        for step in range(10):
            ratio = step / 9
            frames.append(pullup_metrics(40 + (175 - 40) * ratio, chin_top - chin_top * ratio, hip_fn(1 - ratio) if hip_fn else 0.5))
        return frames

    normal = analyze_metrics_sequence(build(), view="front", exercise="pullup")
    check("引体向上动作标准时不报问题", not normal["issues"], str(normal.get("summary")))

    low = analyze_metrics_sequence(build(chin_top=-0.2), view="front", exercise="pullup")
    keys = [item["key"] for item in low["issues"]]
    check("引体向上下巴未过杠能被检出", "pullup_chin_not_over_bar" in keys, str(keys))

    half = analyze_metrics_sequence(
        [pullup_metrics(40 + (120 - 40) * (step / 9), 0.15 * (1 - step / 9)) for step in range(10)]
        + [pullup_metrics(120 - (120 - 40) * (step / 9), 0.15 * (step / 9)) for step in range(10)],
        view="front",
        exercise="pullup",
    )
    keys = [item["key"] for item in half["issues"]]
    check("引体向上未完全伸直能被检出", "pullup_not_full_hang" in keys, str(keys))

    swing = analyze_metrics_sequence(
        build(hip_fn=lambda r: 0.5 + 0.25 * round(__import__("math").sin(r * __import__("math").pi), 2)),
        view="side",
        exercise="pullup",
    )
    keys = [item["key"] for item in swing["issues"]]
    check("引体向上身体摆动能被检出", "pullup_swing" in keys, str(keys))


if __name__ == "__main__":
    test_frame_metrics()
    test_sequence_judgement()
    test_deadlift_rules()
    test_bench_rules()
    test_pushup_rules()
    test_pullup_rules()
    print(f"\n通过 {PASSED} 项，失败 {FAILED} 项")
