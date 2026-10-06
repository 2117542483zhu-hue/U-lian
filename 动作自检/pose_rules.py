"""动作自检的判定规则，支持深蹲、卧推、硬拉、俯卧撑。

设计原则：角度、偏移、时序这些必须准确的数值，全部在本文件里用几何运算算出来；
大模型只负责把结论翻译成人话。这样既稳定，答辩时也能解释清楚。

作者：AI 编写，人工校验。使用前请用 test_rules.py 跑一遍自检。
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence, Tuple

# MediaPipe Pose 共 33 个关键点，这里列出本文件用到的下标
LM = {
    "nose": 0,
    "left_shoulder": 11,
    "right_shoulder": 12,
    "left_elbow": 13,
    "right_elbow": 14,
    "left_wrist": 15,
    "right_wrist": 16,
    "left_hip": 23,
    "right_hip": 24,
    "left_knee": 25,
    "right_knee": 26,
    "left_ankle": 27,
    "right_ankle": 28,
    "left_foot_index": 31,
    "right_foot_index": 32,
}

EXERCISES: Dict[str, Dict[str, object]] = {
    "squat": {
        "label": "深蹲",
        "views": ["side", "front"],
        "default_view": "side",
        "hint": "全身入镜，侧面拍摄时镜头与髋部同高；要判断膝内扣就再录一段正面。",
    },
    "deadlift": {
        "label": "硬拉",
        "views": ["side"],
        "default_view": "side",
        "hint": "从侧面拍，确保起始位置与锁定位置都在画面里，杠铃全程可见。",
    },
    "bench_press": {
        "label": "卧推",
        "views": ["side", "front"],
        "default_view": "side",
        "hint": "从侧面或斜前方拍，保证肩膀、手肘、手腕与髋部都能看见。",
    },
    "pushup": {
        "label": "俯卧撑",
        "views": ["side"],
        "default_view": "side",
        "hint": "手机放在地面高度从侧面拍，让肩、髋、踝形成一条线。",
    },
    "pullup": {
        "label": "引体向上",
        "views": ["front", "side"],
        "default_view": "front",
        "hint": "正面或侧面拍都行，要能看见双手、下巴和髋部；俯拍会看不准下巴有没有过杠。",
    },
}

DEFAULT_THRESHOLDS: Dict[str, float] = {
    # 侧面拍摄时远侧肢体必然被遮挡，可见度会掉到 0.3 左右，
    # 所以这里判断的是"较好一侧"的可见度，而不是八个点的最小值。
    "min_visibility": 0.50,
    "valgus_min_visibility": 0.50,
    "min_valid_frames": 8,
    # 通用：解剖学上的合理范围，越界说明关键点识别失败，直接剔除该帧
    "min_plausible_knee_angle": 25.0,
    "min_plausible_hip_angle": 20.0,
    "min_plausible_elbow_angle": 20.0,
    "max_plausible_lean": 85.0,
    "smooth_window": 3,
    # 次数统计：一次动作的最低点要比两侧峰低出这个度数才算数，用来过滤站立时的细小抖动
    "min_rep_depth_deg": 25.0,
    # 深蹲
    "depth_max_knee_angle": 100.0,
    "torso_lean_max_deg": 45.0,
    "valgus_ratio_max": 0.10,
    "hip_first_ratio": 1.40,
    "ascent_ratio_window": 0.30,
    "bottom_tolerance_deg": 3.0,
    # 硬拉
    "deadlift_start_lean_max": 60.0,
    "deadlift_lockout_lean_min": -8.0,
    "deadlift_max_knee_angle_min": 165.0,
    "deadlift_hip_first_ratio": 1.60,
    # 卧推
    "bench_bottom_elbow_max": 110.0,
    "bench_top_elbow_min": 150.0,
    "bench_hip_lift_max_deg": 20.0,
    "bench_elbow_asymmetry_max": 15.0,
    # 俯卧撑
    "pushup_bottom_elbow_max": 110.0,
    "pushup_body_line_min": 155.0,
    # 引体向上
    "pullup_chin_margin_min": 0.0,
    "pullup_hang_elbow_min": 150.0,
    "pullup_swing_max": 0.60,
}

ISSUE_LIBRARY: Dict[str, Dict[str, object]] = {
    # ---------------------------------------------------------- 深蹲
    "shallow_depth": {
        "name": "下蹲深度不足",
        "why": "最低点膝关节角度偏大，大腿没有到接近平行的位置，股四头肌和臀大肌的刺激会明显不足。",
        "fixes": [
            {"name": "箱式深蹲", "how": "找一个高度约等于膝盖下方一拳的凳子，每次下蹲都轻触凳面再起身，做 3 组 8 次。"},
            {"name": "踝关节活动度练习", "how": "靠墙做膝触墙练习，脚跟不离地，每侧 15 次，训练前做 1 组。"},
        ],
    },
    "knee_valgus": {
        "name": "膝内扣",
        "why": "下蹲或起身时膝盖向身体中线塌陷，膝关节与内侧韧带承受的剪切力会增大，是常见的受伤原因。",
        "fixes": [
            {"name": "弹力带深蹲", "how": "把弹力带套在膝盖上方并向外撑住，做 3 组 10 次，全程保持膝盖对准脚尖。"},
            {"name": "臀中肌激活", "how": "侧卧抬腿或贝壳式各 2 组 15 次，训练前先做，让髋外展肌参与稳定。"},
        ],
    },
    "excessive_lean": {
        "name": "躯干过度前倾",
        "why": "上身趴得太低会把负荷转移到下背部，容易出现腰部疲劳甚至不适。",
        "fixes": [
            {"name": "高脚杯深蹲", "how": "胸前抱一只哑铃或矿泉水瓶，做 3 组 10 次，全程想着胸口朝前上方。"},
            {"name": "呼吸与核心控制", "how": "下蹲前吸气收紧腹部，起身时缓慢呼气，避免含胸弓背。"},
        ],
    },
    "hip_first": {
        "name": "起身时髋部先动",
        "why": "起身瞬间臀部先抬高、膝盖还没伸直，俗称早安式起身，会让下背部承担额外负荷。",
        "fixes": [
            {"name": "暂停深蹲", "how": "在最低点停留 2 秒再起身，做 3 组 6 次，强化起身时的控制。"},
            {"name": "轻重量节奏训练", "how": "用平时重量的六成，按 3 秒下蹲、2 秒停顿、1 秒起身的节奏做 3 组 8 次。"},
        ],
    },
    "low_confidence": {
        "name": "画面可信度不足",
        "why": "关键点可信度偏低，通常是距离太远、光线太暗、身体被遮挡或者只拍到半个身体。",
        "fixes": [
            {"name": "按拍摄要求重拍", "how": "全身入镜，距离 2 至 3 米，侧面拍摄时镜头与髋部同高，光线均匀。"},
        ],
    },
    # ---------------------------------------------------------- 硬拉
    "deadlift_start_lean": {
        "name": "起始位置背部过度前倾",
        "why": "起始时躯干趴得太低，腰椎承受的剪切力明显增大，是硬拉最常见的受伤原因。",
        "fixes": [
            {"name": "降低起始臀位", "how": "起始前先屈髋屈膝，让臀部更低、胸口更挺，用平时的六成重量做 3 组 5 次。"},
            {"name": "罗马尼亚硬拉找感觉", "how": "用轻重量做 3 组 8 次，全程保持背部与地面夹角稳定，体会髋铰链而不是弯腰。"},
        ],
    },
    "deadlift_hip_first": {
        "name": "起身时臀部抬得过快",
        "why": "臀部先抬起而膝盖还没伸直，躯干会瞬间变得更水平，负荷集中到下背部。",
        "fixes": [
            {"name": "暂停硬拉", "how": "杠铃离地约 5 厘米处停 2 秒再继续，做 3 组 5 次，训练髋膝同步发力。"},
            {"name": "垫高起始位置", "how": "把杠铃垫高到膝盖下方一点，减轻起始难度，做 3 组 6 次。"},
        ],
    },
    "deadlift_over_extension": {
        "name": "锁定时过度后仰",
        "why": "站直后继续向后挺腰，会让腰椎在末端承受挤压，长期容易造成不适。",
        "fixes": [
            {"name": "锁定位置练习", "how": "站直时想着臀部夹紧、肋骨下沉，不刻意后仰，用六成重量做 3 组 6 次。"},
            {"name": "平板支撑强化核心", "how": "每次 40 秒做 3 组，训练躯干在负重下保持中立的能力。"},
        ],
    },
    "deadlift_not_locked": {
        "name": "结束时未完全站直",
        "why": "膝盖没有伸直就放下杠铃，说明本次动作没做完，训练效果会打折。",
        "fixes": [
            {"name": "顶部停顿", "how": "每次站直后停 1 秒再放下，用六成重量做 3 组 5 次。"},
            {"name": "降低重量", "how": "把重量降到能轻松完成锁定为止，再逐次加回去。"},
        ],
    },
    # ---------------------------------------------------------- 卧推
    "bench_shallow": {
        "name": "下放幅度不足",
        "why": "手肘没有弯到足够角度，说明杠铃没有接近胸部，胸大肌的刺激范围明显不够。",
        "fixes": [
            {"name": "触碰式卧推", "how": "用六成重量，每次杠铃轻触胸部再推起，做 3 组 8 次。"},
            {"name": "肩胛收紧", "how": "躺下后先把肩胛骨向中间收拢并下沉，保持这个位置再开始动作。"},
        ],
    },
    "bench_not_pressed": {
        "name": "顶部没有推直",
        "why": "手肘在最高点仍然弯曲，说明每次动作没做完整，长期会限制力量增长。",
        "fixes": [
            {"name": "半程锁定练习", "how": "在前半程用较大重量做 3 组 5 次，专门练顶部锁定。"},
            {"name": "降低重量完成全程", "how": "把重量降到能完全推直，再逐步加重。"},
        ],
    },
    "bench_hip_lift": {
        "name": "臀部离凳",
        "why": "推起时臀部抬起变成桥式，会让动作变成上斜卧推，胸部刺激减少，腰部压力增大。",
        "fixes": [
            {"name": "脚掌踩实", "how": "双脚踩稳地面，把力量传导到脚上，臀部始终贴在凳面。"},
            {"name": "降低重量", "how": "重量降到能保持臀部贴凳的程度，做 3 组 8 次。"},
        ],
    },
    "bench_asymmetric": {
        "name": "两侧发力不均",
        "why": "左右手肘角度差异较大，一侧推得更多，长期会造成力量不平衡。",
        "fixes": [
            {"name": "哑铃卧推", "how": "改用哑铃做 3 组 8 次，强迫两侧独立发力。"},
            {"name": "单侧辅助训练", "how": "弱侧先做，强侧按弱侧的次数完成，缩小差距。"},
        ],
    },
    # ---------------------------------------------------------- 俯卧撑
    "pushup_shallow": {
        "name": "下放幅度不足",
        "why": "手肘没有弯到 90 度左右，胸部没有接近地面，训练效果会明显打折。",
        "fixes": [
            {"name": "高位俯卧撑", "how": "手撑在椅子或台阶上做 3 组 10 次，先保证幅度再降低高度。"},
            {"name": "离心俯卧撑", "how": "用 3 秒缓慢下放、撑起时膝盖着地借力，做 3 组 6 次。"},
        ],
    },
    "pushup_body_line": {
        "name": "身体不成一条直线",
        "why": "臀部下塌或抬高，说明核心没有收紧，负荷会转到腰部或让幅度变浅。",
        "fixes": [
            {"name": "跪姿俯卧撑", "how": "先减少难度，专注保持肩、髋、踝在一条线上，做 3 组 8 次。"},
            {"name": "平板支撑", "how": "每次 40 秒做 3 组，练核心稳定后再回到标准俯卧撑。"},
        ],
    },
    # ---------------------------------------------------------- 引体向上
    "pullup_chin_not_over_bar": {
        "name": "顶部下巴没有过杠",
        "why": "下巴没有超过单杠，说明动作幅度不足，背阔肌和肱二头肌没有完全收缩。",
        "fixes": [
            {"name": "弹力带辅助引体", "how": "用弹力带减轻体重，做 3 组 6 次，每次都要把下巴拉过杠。"},
            {"name": "高位下拉补充", "how": "做 3 组 10 次，拉到手柄到锁骨附近，建立过杠的发力感受。"},
        ],
    },
    "pullup_not_full_hang": {
        "name": "下放不完全",
        "why": "手臂没有伸直就再拉上去，说明每次只做了半程，肌肉在较窄的范围内反复收缩。",
        "fixes": [
            {"name": "控制下放", "how": "用 3 秒缓慢下放，手臂完全伸直再开始下一次，做 3 组 5 次。"},
            {"name": "悬垂练习", "how": "先练被动悬垂 20 至 30 秒，感受手臂完全伸直的起始位置。"},
        ],
    },
    "pullup_swing": {
        "name": "身体明显摆动",
        "why": "髋部前后晃动说明借用了惯性，背部发力变少，也容易让肩膀受力不稳。",
        "fixes": [
            {"name": "屈膝收腹", "how": "双腿交叉向后屈起并收紧腹部，减小摆动，做 3 组 5 次。"},
            {"name": "慢速引体", "how": "用 2 秒拉起、2 秒下放的节奏做 3 组 4 次，靠控制而不是惯性。"},
        ],
    },
}


# 问题 -> 通俗短标签（逐次点评、出错帧标红、视频侧栏用）
SHORT_LABELS: Dict[str, str] = {
    "shallow_depth": "下蹲过浅",
    "knee_valgus": "膝盖内扣",
    "excessive_lean": "躯干前倾",
    "hip_first": "起身弯腰",
    "low_confidence": "画面不清",
    "deadlift_start_lean": "起始前倾",
    "deadlift_hip_first": "起身髋先动",
    "deadlift_over_extension": "锁定后仰",
    "deadlift_not_locked": "未站直",
    "bench_shallow": "下放不足",
    "bench_not_pressed": "没推直",
    "bench_hip_lift": "臀部离凳",
    "bench_asymmetric": "两侧不均",
    "pushup_shallow": "下放不足",
    "pushup_body_line": "身体不成线",
    "pullup_chin_not_over_bar": "下巴未过杠",
    "pullup_not_full_hang": "下放不全",
    "pullup_swing": "身体摆动",
}

# 问题 -> 需要在视频里橙色高亮的关节点（LM 名称）
ERROR_JOINTS: Dict[str, List[str]] = {
    "shallow_depth": ["left_knee", "right_knee"],
    "knee_valgus": ["left_knee", "right_knee"],
    "excessive_lean": ["left_shoulder", "right_shoulder", "left_hip", "right_hip"],
    "hip_first": ["left_hip", "right_hip", "left_knee", "right_knee"],
    "deadlift_start_lean": ["left_shoulder", "right_shoulder", "left_hip", "right_hip"],
    "deadlift_hip_first": ["left_hip", "right_hip", "left_knee", "right_knee"],
    "deadlift_over_extension": ["left_shoulder", "right_shoulder", "left_hip", "right_hip"],
    "deadlift_not_locked": ["left_knee", "right_knee"],
    "bench_shallow": ["left_elbow", "right_elbow"],
    "bench_not_pressed": ["left_elbow", "right_elbow"],
    "bench_hip_lift": ["left_hip", "right_hip"],
    "bench_asymmetric": ["left_elbow", "right_elbow"],
    "pushup_shallow": ["left_elbow", "right_elbow"],
    "pushup_body_line": ["left_shoulder", "right_shoulder", "left_hip", "right_hip", "left_ankle", "right_ankle"],
    "pullup_chin_not_over_bar": ["nose", "left_wrist", "right_wrist"],
    "pullup_not_full_hang": ["left_elbow", "right_elbow"],
    "pullup_swing": ["left_hip", "right_hip"],
}


def _angle(a: Sequence[float], b: Sequence[float], c: Sequence[float]) -> Optional[float]:
    """返回三点夹角（度），b 为顶点。任何两点重合时返回 None。"""
    ax, ay = a[0] - b[0], a[1] - b[1]
    cx, cy = c[0] - b[0], c[1] - b[1]
    n1 = math.hypot(ax, ay)
    n2 = math.hypot(cx, cy)
    if n1 < 1e-6 or n2 < 1e-6:
        return None
    cosine = (ax * cx + ay * cy) / (n1 * n2)
    cosine = max(-1.0, min(1.0, cosine))
    return math.degrees(math.acos(cosine))


def _xy(landmarks: Sequence[Dict[str, float]], name: str) -> Sequence[float]:
    point = landmarks[LM[name]]
    return (point["x"], point["y"])


def _visible(landmarks: Sequence[Dict[str, float]], name: str) -> float:
    return float(landmarks[LM[name]].get("visibility", 1.0))


def _used_joints(exercise: str) -> List[object]:
    """每个动作做判定时真正用到的关节。成对关节用元组表示，便于取左右中较清晰的一侧。"""
    shoulders = ("left_shoulder", "right_shoulder")
    hips = ("left_hip", "right_hip")
    knees = ("left_knee", "right_knee")
    ankles = ("left_ankle", "right_ankle")
    elbows = ("left_elbow", "right_elbow")
    wrists = ("left_wrist", "right_wrist")
    if exercise == "custom":
        # 自由动作：只要求躯干可见，肢体是否出画交给具体动作决定，避免误杀
        return [shoulders, hips]
    if exercise in ("squat", "deadlift"):
        return [shoulders, hips, knees, ankles]
    if exercise == "bench_press":
        return [shoulders, hips, elbows, wrists]
    if exercise == "pushup":
        return [shoulders, hips, elbows, wrists, ankles]
    # pullup：允许腿在画面外
    return ["nose", shoulders, hips, elbows, wrists]


def _frame_visibility(landmarks: Sequence[Dict[str, float]], exercise: str) -> float:
    """一帧的置信度：成对关节取左右中较清晰的一侧，再对所有用到的最小值。"""
    values = []
    for slot in _used_joints(exercise):
        if isinstance(slot, tuple):
            values.append(max(_visible(landmarks, slot[0]), _visible(landmarks, slot[1])))
        else:
            values.append(_visible(landmarks, slot))
    return min(values)


def frame_metrics(
    landmarks: Sequence[Dict[str, float]],
    thresholds: Optional[Dict[str, float]] = None,
    exercise: str = "squat",
) -> Dict[str, Optional[float]]:
    """把一帧关键点换算成指标。landmarks 为长度 33 的序列。"""
    thresholds = thresholds or DEFAULT_THRESHOLDS
    left_chain = ["left_shoulder", "left_hip", "left_knee", "left_ankle"]
    right_chain = ["right_shoulder", "right_hip", "right_knee", "right_ankle"]
    visibility_left = min(_visible(landmarks, name) for name in left_chain)
    visibility_right = min(_visible(landmarks, name) for name in right_chain)
    # 该帧的置信度只统计当前动作真正用到的关节，避免引体向上、卧推这种腿可能出画的场景被误杀
    visibility = _frame_visibility(landmarks, exercise)
    best_left = visibility_left >= visibility_right

    knee_l = _angle(_xy(landmarks, "left_hip"), _xy(landmarks, "left_knee"), _xy(landmarks, "left_ankle"))
    knee_r = _angle(_xy(landmarks, "right_hip"), _xy(landmarks, "right_knee"), _xy(landmarks, "right_ankle"))
    hip_l = _angle(_xy(landmarks, "left_shoulder"), _xy(landmarks, "left_hip"), _xy(landmarks, "left_knee"))
    hip_r = _angle(_xy(landmarks, "right_shoulder"), _xy(landmarks, "right_hip"), _xy(landmarks, "right_knee"))
    elbow_l = _angle(_xy(landmarks, "left_shoulder"), _xy(landmarks, "left_elbow"), _xy(landmarks, "left_wrist"))
    elbow_r = _angle(_xy(landmarks, "right_shoulder"), _xy(landmarks, "right_elbow"), _xy(landmarks, "right_wrist"))
    body_line_l = _angle(_xy(landmarks, "left_shoulder"), _xy(landmarks, "left_hip"), _xy(landmarks, "left_ankle"))
    body_line_r = _angle(_xy(landmarks, "right_shoulder"), _xy(landmarks, "right_hip"), _xy(landmarks, "right_ankle"))

    if best_left:
        knee_angle, hip_angle = knee_l, hip_l
        elbow_angle, body_line = elbow_l, body_line_l
        elbow_visible = _visible(landmarks, "left_elbow"), _visible(landmarks, "left_wrist")
    else:
        knee_angle, hip_angle = knee_r, hip_r
        elbow_angle, body_line = elbow_r, body_line_r
        elbow_visible = _visible(landmarks, "right_elbow"), _visible(landmarks, "right_wrist")

    shoulder_mid_x = (_xy(landmarks, "left_shoulder")[0] + _xy(landmarks, "right_shoulder")[0]) / 2
    shoulder_mid_y = (_xy(landmarks, "left_shoulder")[1] + _xy(landmarks, "right_shoulder")[1]) / 2
    hip_mid_x = (_xy(landmarks, "left_hip")[0] + _xy(landmarks, "right_hip")[0]) / 2
    hip_mid_y = (_xy(landmarks, "left_hip")[1] + _xy(landmarks, "right_hip")[1]) / 2
    torso_dx = shoulder_mid_x - hip_mid_x
    torso_dy = shoulder_mid_y - hip_mid_y
    torso_lean = math.degrees(math.atan2(abs(torso_dx), abs(torso_dy))) if torso_dy else None
    # 带符号的躯干倾角：先用鼻子相对肩膀的位置判断人朝向哪边，正数表示前倾，负数表示后仰
    signed_lean = None
    if torso_dy and abs(torso_dy) > 1e-6:
        nose_x = _xy(landmarks, "nose")[0]
        facing = 1.0 if nose_x >= shoulder_mid_x else -1.0
        signed_lean = math.degrees(math.atan2((shoulder_mid_x - hip_mid_x) * facing, abs(torso_dy)))

    shoulder_width = abs(_xy(landmarks, "left_shoulder")[0] - _xy(landmarks, "right_shoulder")[0])
    valgus_ratio: Optional[float] = None
    if shoulder_width > 1e-6 and min(visibility_left, visibility_right) >= thresholds["valgus_min_visibility"]:
        ratios = []
        for hip, knee, ankle in (
            ("left_hip", "left_knee", "left_ankle"),
            ("right_hip", "right_knee", "right_ankle"),
        ):
            inward = -1.0 if _xy(landmarks, hip)[0] > hip_mid_x else 1.0
            ratios.append((_xy(landmarks, knee)[0] - _xy(landmarks, ankle)[0]) * inward / shoulder_width)
        valgus_ratio = max(ratios)

    # 肩髋连线与水平方向的夹角，用来判断卧推时臀部是否离凳
    shoulder_hip_distance = math.hypot(shoulder_mid_x - hip_mid_x, shoulder_mid_y - hip_mid_y)
    hip_lift_angle = None
    if shoulder_hip_distance > 1e-6:
        hip_lift_angle = math.degrees(math.asin(max(-1.0, min(1.0, abs(shoulder_mid_y - hip_mid_y) / shoulder_hip_distance))))

    elbow_asymmetry = None
    # 只有两侧手肘都清晰时才比较左右差异，否则远侧被遮挡会算出一个假的"发力不均"
    elbow_visible_all = min(
        _visible(landmarks, name)
        for name in ("left_elbow", "left_wrist", "right_elbow", "right_wrist")
    )
    if elbow_l is not None and elbow_r is not None and elbow_visible_all >= thresholds["valgus_min_visibility"]:
        elbow_asymmetry = abs(elbow_l - elbow_r)

    # 引体向上：下巴相对双手（单杠所在位置）的高度，正值表示下巴过杠
    nose_y = _xy(landmarks, "nose")[1]
    wrist_min_y = min(_xy(landmarks, "left_wrist")[1], _xy(landmarks, "right_wrist")[1])
    chin_margin = None
    if shoulder_hip_distance > 1e-6:
        chin_margin = (wrist_min_y - nose_y) / shoulder_hip_distance

    metrics: Dict[str, Optional[float]] = {
        "knee_angle": knee_angle,
        "knee_angle_left": knee_l,
        "knee_angle_right": knee_r,
        "hip_angle": hip_angle,
        "elbow_angle": elbow_angle,
        "elbow_angle_left": elbow_l,
        "elbow_angle_right": elbow_r,
        "elbow_asymmetry": elbow_asymmetry,
        "body_line_angle": body_line,
        "torso_lean": torso_lean,
        "signed_lean": signed_lean,
        "hip_lift_angle": hip_lift_angle,
        "valgus_ratio": valgus_ratio,
        "visibility": visibility,
        "visibility_left": visibility_left,
        "visibility_right": visibility_right,
        "elbow_visibility": min(elbow_visible),
        "chin_margin": chin_margin,
        "hip_mid_x": hip_mid_x,
        "shoulder_width": shoulder_width,
    }
    metrics["plausible"] = _plausible(landmarks, metrics, thresholds, exercise)
    return metrics


def _plausible(
    landmarks: Sequence[Dict[str, float]],
    metrics: Dict[str, Optional[float]],
    thresholds: Dict[str, float],
    exercise: str,
) -> bool:
    """判断这一帧的关键点是否还原出了人体结构，用来剔除识别失败的帧。"""
    if metrics.get("visibility", 0) < thresholds["min_visibility"]:
        return False
    torso_lean = metrics.get("torso_lean")
    if torso_lean is not None and torso_lean > thresholds["max_plausible_lean"]:
        return False

    if exercise == "custom":
        # 自由动作：通过可见度与躯干合理范围即可，不要求具体关节角度
        return True

    if exercise in ("bench_press", "pullup"):
        # 躺姿/悬垂视频里腿不一定在画面内，重点看手肘
        elbow = metrics.get("elbow_angle")
        if elbow is None or elbow < thresholds["min_plausible_elbow_angle"]:
            return False
        if metrics.get("elbow_visibility", 0) < thresholds["min_visibility"]:
            return False
        return True

    knee_angle = metrics.get("knee_angle")
    if knee_angle is None or knee_angle < thresholds["min_plausible_knee_angle"]:
        return False
    hip_angle = metrics.get("hip_angle")
    if hip_angle is not None and hip_angle < thresholds["min_plausible_hip_angle"]:
        return False
    if exercise == "pushup":
        elbow = metrics.get("elbow_angle")
        if elbow is None or elbow < thresholds["min_plausible_elbow_angle"]:
            return False
        return True
    hip_y = (_xy(landmarks, "left_hip")[1] + _xy(landmarks, "right_hip")[1]) / 2
    ankle_y = (_xy(landmarks, "left_ankle")[1] + _xy(landmarks, "right_ankle")[1]) / 2
    return hip_y < ankle_y


def _median_filter(values: List[Optional[float]], window: int) -> List[Optional[float]]:
    """中值平滑，专门用来削掉单帧跳变。"""
    if window <= 1:
        return list(values)
    half = window // 2
    smoothed: List[Optional[float]] = []
    for index in range(len(values)):
        window_values = [
            values[position]
            for position in range(max(0, index - half), min(len(values), index + half + 1))
            if values[position] is not None
        ]
        if not window_values:
            smoothed.append(None)
            continue
        window_values.sort()
        smoothed.append(window_values[len(window_values) // 2])
    return smoothed


def _find_valleys(values: List[Optional[float]], distance: int) -> List[int]:
    """在一维序列里找谷底位置（局部最小），相邻谷底至少间隔 distance 帧。"""
    n = len(values)
    valleys: List[int] = []
    for i in range(1, n - 1):
        current = values[i]
        if current is None:
            continue
        left = values[i - 1]
        right = values[i + 1]
        if (left is None or current <= left) and (right is None or current <= right):
            if not valleys or i - valleys[-1] >= distance:
                valleys.append(i)
    return valleys


def _nearest_peak(values: List[Optional[float]], center: int, direction: int, limit: int) -> int:
    """从 center 沿 direction（-1 向左 / +1 向右）在 limit 范围内找最近的峰值位置。"""
    n = len(values)
    best = center
    for i in range(center + direction, center + direction * (limit + 1), direction):
        if i < 0 or i >= n:
            break
        if values[i] is None:
            continue
        if values[best] is None or values[i] > values[best]:
            best = i
    return best


def _dominant_angle_key(metrics: Sequence[Dict[str, Optional[float]]]) -> str:
    """自由动作：自动判断哪条角度序列变化最大，用它来数次数。

    下肢动作看膝关节、上肢推拉看肘关节，核心/体态动作看身体直线角度。
    若所有序列都基本不动（如平板支撑这类静态动作），退回膝关节，结果自然是 0 次。
    """
    best_key = "knee_angle"
    best_range = 0.0
    for key in ("knee_angle", "elbow_angle", "body_line_angle", "hip_angle"):
        values = [item.get(key) for item in metrics if item and item.get(key) is not None]
        if len(values) < 4:
            continue
        span = max(values) - min(values)
        if span > best_range:
            best_range = span
            best_key = key
    return best_key


def detect_reps(
    metrics: Sequence[Dict[str, Optional[float]]],
    thresholds: Optional[Dict[str, float]] = None,
    exercise: str = "squat",
    sample_fps: float = 4.0,
) -> List[Dict[str, object]]:
    """从逐帧指标里切分每一次动作（次数）。

    深蹲/硬拉看膝关节角度，卧推/俯卧撑/引体向上看肘关节角度：
    角度下降 → 谷底 → 回升，记作一次。返回每一次的谷底角度、下放用时、
    起身用时和全程用时（用采样帧数除以 sample_fps 换算成秒）。
    """
    thresholds = thresholds or DEFAULT_THRESHOLDS
    if exercise == "custom":
        angle_key = _dominant_angle_key(metrics)
    else:
        angle_key = "knee_angle" if exercise in ("squat", "deadlift") else "elbow_angle"
    window = max(1, int(thresholds.get("smooth_window", 3)))
    sample_fps = max(sample_fps, 0.5)

    values: List[Optional[float]] = [item.get(angle_key) if item else None for item in metrics]
    if sum(1 for value in values if value is not None) < 4:
        return []

    smoothed = _median_filter(values, window)
    # 相邻两次动作至少间隔 0.6 秒，避免一次深蹲被噪声切成两个谷底
    min_gap = max(2, int(round(0.6 * sample_fps)))
    valleys = _find_valleys(smoothed, min_gap)
    min_depth = float(thresholds.get("min_rep_depth_deg", 25.0))

    reps: List[Dict[str, object]] = []
    for position in valleys:
        bottom_angle = smoothed[position]
        if bottom_angle is None:
            continue
        left_peak = _nearest_peak(smoothed, position, -1, min_gap * 2)
        right_peak = _nearest_peak(smoothed, position, +1, min_gap * 2)
        # 两侧都要有峰、且谷底要比峰低出足够多，才算一次真正的动作（滤掉站立抖动）
        if left_peak >= position or right_peak <= position:
            continue
        prominence = min(smoothed[left_peak], smoothed[right_peak]) - bottom_angle
        if prominence < min_depth:
            continue
        descent = max(0.0, (position - left_peak) / sample_fps)
        ascent = max(0.0, (right_peak - position) / sample_fps)
        duration = max(0.0, (right_peak - left_peak) / sample_fps)
        reps.append(
            {
                "rep": len(reps) + 1,
                "start_index": left_peak,
                "bottom_index": position,
                "end_index": right_peak,
                "bottom_angle": round(bottom_angle, 1),
                "descent_seconds": round(descent, 2),
                "ascent_seconds": round(ascent, 2),
                "duration_seconds": round(duration, 2),
            }
        )
    return reps


def _issue(key: str, severity: str, evidence: str, value: Optional[float]) -> Dict[str, object]:
    base = dict(ISSUE_LIBRARY[key])
    base.update(
        {
            "key": key,
            "severity": severity,
            "evidence": evidence,
            "value": value,
            "short": SHORT_LABELS.get(key, base.get("name", key)),
        }
    )
    return base


def _prepare(
    metrics: List[Dict[str, Optional[float]]],
    thresholds: Dict[str, float],
    exercise: str,
) -> Optional[Dict[str, object]]:
    """过滤无效帧并做平滑，返回各条序列。"""
    valid = [
        (index, item)
        for index, item in enumerate(metrics)
        if item
        and item.get("knee_angle") is not None
        and item.get("plausible", True)
        and (item.get("visibility") or 0) >= thresholds["min_visibility"]
    ]
    if exercise == "custom":
        valid = [
            (index, item)
            for index, item in enumerate(metrics)
            if item and item.get("plausible", True)
        ]
    elif exercise in ("bench_press", "pullup"):
        valid = [
            (index, item)
            for index, item in enumerate(metrics)
            if item
            and item.get("elbow_angle") is not None
            and item.get("plausible", True)
            and (item.get("visibility") or 0) >= thresholds["min_visibility"]
        ]
    if len(valid) < int(thresholds["min_valid_frames"]):
        return None

    window = int(thresholds["smooth_window"])
    series = {}
    for key in ("knee_angle", "hip_angle", "elbow_angle", "body_line_angle", "torso_lean", "signed_lean", "valgus_ratio", "hip_lift_angle", "elbow_asymmetry", "visibility", "chin_margin", "hip_mid_x", "shoulder_width"):
        series[key] = _median_filter([item.get(key) for _, item in valid], window)
    series["indices"] = [index for index, _ in valid]
    series["count"] = len(valid)
    series["raw_count"] = len(metrics)
    return series


def _insufficient_result() -> Dict[str, object]:
    return {
        "status": "insufficient_frames",
        "confidence": 0.0,
        "issues": [_issue("low_confidence", "high", "有效帧不足，无法给出动作结论。", None)],
        "metrics": {},
        "summary": "视频里能用于判断的画面太少，请按拍摄要求重新录制。",
    }


def _pack(
    status: str,
    issues: List[Dict[str, object]],
    metrics: Dict[str, object],
    summary_parts: List[str],
    series: Optional[Dict[str, object]] = None,
) -> Dict[str, object]:
    angle_summary = ("；".join(summary_parts) + "。") if summary_parts else ""
    if issues:
        names = "、".join(str(item["name"]) for item in issues)
        first_fixes = issues[0].get("fixes") or []
        fix_hint = f"建议先练：{first_fixes[0]['name']}。" if first_fixes else ""
        summary = "检出的问题：" + names + "。" + fix_hint + angle_summary
    else:
        summary = "未检出明显问题。" + angle_summary
    confidence = 0.0
    if series and series.get("visibility"):
        values = [value for value in series["visibility"] if value is not None]
        confidence = sum(values) / len(values) if values else 0.0
    return {
        "status": status,
        "confidence": confidence,
        "issues": issues,
        "metrics": metrics,
        "summary": summary,
    }


def _analyze_squat(series: Dict[str, object], view: str, thresholds: Dict[str, float]) -> Dict[str, object]:
    knee = series["knee_angle"]
    hip = series["hip_angle"]
    lean = series["torso_lean"]
    valgus = series["valgus_ratio"]
    indices = series["indices"]

    min_pos = min(range(len(knee)), key=lambda position: knee[position])
    min_knee = knee[min_pos]
    tolerance = thresholds["bottom_tolerance_deg"]
    deepest = min_pos
    while deepest + 1 < len(knee) and knee[deepest + 1] is not None and knee[deepest + 1] <= min_knee + tolerance:
        deepest += 1

    lean_at_bottom = lean[deepest]
    window_start = max(0, deepest - 2)
    window_end = min(len(valgus), deepest + 3)
    valgus_at_bottom = max(
        (value for value in valgus[window_start:window_end] if value is not None),
        default=None,
    )

    issues: List[Dict[str, object]] = []
    if min_knee > thresholds["depth_max_knee_angle"]:
        issues.append(_issue("shallow_depth", "medium",
                             f"最低点膝关节角度约 {min_knee:.0f} 度，超过 {thresholds['depth_max_knee_angle']:.0f} 度的参考线。", min_knee))
    if view == "front" and valgus_at_bottom is not None and valgus_at_bottom > thresholds["valgus_ratio_max"]:
        issues.append(_issue("knee_valgus", "high",
                             f"最低点两侧膝盖相对脚踝向内偏移约肩宽的 {valgus_at_bottom * 100:.0f} 百分比，"
                             f"超过 {thresholds['valgus_ratio_max'] * 100:.0f} 百分比的参考线。", valgus_at_bottom))
    if lean_at_bottom is not None and lean_at_bottom > thresholds["torso_lean_max_deg"]:
        issues.append(_issue("excessive_lean", "medium",
                             f"最低点躯干前倾约 {lean_at_bottom:.0f} 度，超过 {thresholds['torso_lean_max_deg']:.0f} 度的参考线。", lean_at_bottom))

    ascent_end = deepest
    for position in range(deepest, len(knee)):
        if knee[position] is not None and knee[position] >= knee[ascent_end]:
            ascent_end = position
    hip_first_ratio = None
    ascent_length = ascent_end - deepest
    if ascent_length >= 4:
        span = max(2, int(ascent_length * thresholds["ascent_ratio_window"]))
        hip_bottom, hip_peak = hip[deepest], hip[ascent_end]
        knee_bottom, knee_peak = knee[deepest], knee[ascent_end]
        hip_range = (hip_peak - hip_bottom) if None not in (hip_bottom, hip_peak) else 0.0
        knee_range = knee_peak - knee_bottom
        if hip_range > 0 and knee_range > 0:
            hip_progress = (hip[deepest + span] - hip_bottom) / hip_range
            knee_progress = (knee[deepest + span] - knee_bottom) / knee_range
            if knee_progress > 1e-6:
                hip_first_ratio = hip_progress / knee_progress
                if hip_first_ratio > thresholds["hip_first_ratio"]:
                    issues.append(_issue("hip_first", "medium",
                                         f"起身前段髋部完成自身活动范围的 {hip_progress * 100:.0f} 百分比，"
                                         f"膝部只完成 {knee_progress * 100:.0f} 百分比，比值 {hip_first_ratio:.2f} "
                                         f"超过 {thresholds['hip_first_ratio']:.2f} 的参考线。", hip_first_ratio))

    parts = [f"有效帧 {series['count']} 帧，最低点膝关节角度约 {min_knee:.0f} 度"]
    if lean_at_bottom is not None:
        parts.append(f"躯干前倾约 {lean_at_bottom:.0f} 度")
    if view == "front" and valgus_at_bottom is not None:
        parts.append(f"膝盖内移约肩宽的 {valgus_at_bottom * 100:.0f} 百分比")
    if hip_first_ratio is not None:
        parts.append(f"起身髋膝速率比 {hip_first_ratio:.2f}")

    metrics = {
        "min_knee_angle": min_knee,
        "torso_lean_at_bottom": lean_at_bottom,
        "hip_first_ratio": hip_first_ratio,
        "valid_frames": series["count"],
        "total_frames": series.get("raw_count", series["count"]),
        "deepest_frame_index": indices[deepest],
    }
    if view == "front":
        metrics["valgus_ratio_at_bottom"] = valgus_at_bottom
    return _pack("ok", issues, metrics, parts, series)


def _analyze_deadlift(series: Dict[str, object], thresholds: Dict[str, float]) -> Dict[str, object]:
    knee = series["knee_angle"]
    hip = series["hip_angle"]
    lean = series["signed_lean"]
    indices = series["indices"]

    issues: List[Dict[str, object]] = []
    valid_lean = [value for value in lean if value is not None]
    # 起始位置：膝角最小的那一帧（杠铃在最低点）
    start_pos = min(range(len(knee)), key=lambda position: knee[position])
    start_lean = lean[start_pos]
    max_knee = max(value for value in knee if value is not None)
    # 锁定位置取"接近完全站直的最后几帧"，而不是第一个达到最大角度的帧
    near_lockout = [
        position for position, value in enumerate(knee)
        if value is not None and value >= max_knee - 5.0
    ]
    end_pos = near_lockout[-1] if near_lockout else len(knee) - 1
    lockout_lean = lean[end_pos]

    if start_lean is not None and start_lean > thresholds["deadlift_start_lean_max"]:
        issues.append(_issue("deadlift_start_lean", "high",
                             f"起始位置躯干前倾约 {start_lean:.0f} 度，超过 {thresholds['deadlift_start_lean_max']:.0f} 度的参考线。", start_lean))

    if max_knee < thresholds["deadlift_max_knee_angle_min"]:
        issues.append(_issue("deadlift_not_locked", "medium",
                             f"全程最大膝关节角度约 {max_knee:.0f} 度，小于 {thresholds['deadlift_max_knee_angle_min']:.0f} 度，说明结束时没有完全站直。", max_knee))

    if lockout_lean is not None and lockout_lean < thresholds["deadlift_lockout_lean_min"]:
        issues.append(_issue("deadlift_over_extension", "medium",
                             f"锁定位置躯干后仰约 {abs(lockout_lean):.0f} 度，超过 {abs(thresholds['deadlift_lockout_lean_min']):.0f} 度的参考线。", lockout_lean))

    hip_first_ratio = None
    ascent_length = end_pos - start_pos
    if ascent_length >= 4:
        span = max(2, int(ascent_length * thresholds["ascent_ratio_window"]))
        hip_bottom, hip_peak = hip[start_pos], hip[end_pos]
        knee_bottom, knee_peak = knee[start_pos], knee[end_pos]
        hip_range = (hip_peak - hip_bottom) if None not in (hip_bottom, hip_peak) else 0.0
        knee_range = (knee_peak - knee_bottom) if None not in (knee_bottom, knee_peak) else 0.0
        if hip_range > 0 and knee_range > 0:
            hip_progress = (hip[start_pos + span] - hip_bottom) / hip_range
            knee_progress = (knee[start_pos + span] - knee_bottom) / knee_range
            if knee_progress > 1e-6:
                hip_first_ratio = hip_progress / knee_progress
                if hip_first_ratio > thresholds["deadlift_hip_first_ratio"]:
                    issues.append(_issue("deadlift_hip_first", "medium",
                                         f"起身前段髋部完成自身活动范围的 {hip_progress * 100:.0f} 百分比，"
                                         f"膝部只完成 {knee_progress * 100:.0f} 百分比，比值 {hip_first_ratio:.2f} "
                                         f"超过 {thresholds['deadlift_hip_first_ratio']:.2f} 的参考线。", hip_first_ratio))

    parts = [f"有效帧 {series['count']} 帧，全程最大膝角约 {max_knee:.0f} 度"]
    if start_lean is not None:
        parts.append(f"起始躯干前倾约 {start_lean:.0f} 度")
    if lockout_lean is not None:
        parts.append(f"锁定位置躯干角度 {lockout_lean:.0f} 度")
    metrics = {
        "start_lean": start_lean,
        "lockout_lean": lockout_lean,
        "max_knee_angle": max_knee,
        "hip_first_ratio": hip_first_ratio,
        "valid_frames": series["count"],
        "start_frame_index": indices[start_pos],
    }
    return _pack("ok", issues, metrics, parts, series)


def _analyze_bench(series: Dict[str, object], thresholds: Dict[str, float], view: str) -> Dict[str, object]:
    elbow = [value for value in series["elbow_angle"]]
    lift = series["hip_lift_angle"]
    asymmetry = series["elbow_asymmetry"]
    values = [value for value in elbow if value is not None]
    if not values:
        return _insufficient_result()
    issues: List[Dict[str, object]] = []
    bottom_pos = min(range(len(elbow)), key=lambda position: elbow[position] if elbow[position] is not None else 999)
    min_elbow = min(values)
    max_elbow = max(values)

    if min_elbow > thresholds["bench_bottom_elbow_max"]:
        issues.append(_issue("bench_shallow", "medium",
                             f"最低点肘角约 {min_elbow:.0f} 度，大于 {thresholds['bench_bottom_elbow_max']:.0f} 度，说明杠铃没有接近胸部。", min_elbow))
    if max_elbow < thresholds["bench_top_elbow_min"]:
        issues.append(_issue("bench_not_pressed", "medium",
                             f"最高点肘角约 {max_elbow:.0f} 度，小于 {thresholds['bench_top_elbow_min']:.0f} 度，说明顶部没有推直。", max_elbow))

    lift_values = [value for value in lift if value is not None]
    max_lift = max(lift_values) if lift_values else None
    if max_lift is not None and max_lift > thresholds["bench_hip_lift_max_deg"]:
        issues.append(_issue("bench_hip_lift", "medium",
                             f"肩髋连线最大偏离水平约 {max_lift:.0f} 度，超过 {thresholds['bench_hip_lift_max_deg']:.0f} 度的参考线，说明推起时臀部离凳。", max_lift))

    asym_values = [value for value in asymmetry if value is not None]
    max_asym = max(asym_values) if asym_values else None
    if max_asym is not None and max_asym > thresholds["bench_elbow_asymmetry_max"]:
        issues.append(_issue("bench_asymmetric", "medium",
                             f"两侧肘角最大差异约 {max_asym:.0f} 度，超过 {thresholds['bench_elbow_asymmetry_max']:.0f} 度的参考线。", max_asym))

    parts = [f"有效帧 {series['count']} 帧，最低点肘角约 {min_elbow:.0f} 度", f"最高点肘角约 {max_elbow:.0f} 度"]
    metrics = {
        "min_elbow_angle": min_elbow,
        "max_elbow_angle": max_elbow,
        "hip_lift_angle_max": max_lift,
        "elbow_asymmetry_max": max_asym,
        "valid_frames": series["count"],
        "bottom_frame_index": series["indices"][bottom_pos],
    }
    return _pack("ok", issues, metrics, parts, series)


def _analyze_pushup(series: Dict[str, object], thresholds: Dict[str, float]) -> Dict[str, object]:
    elbow = series["elbow_angle"]
    body = series["body_line_angle"]
    values = [value for value in elbow if value is not None]
    if not values:
        return _insufficient_result()
    issues: List[Dict[str, object]] = []
    min_elbow = min(values)
    body_values = [value for value in body if value is not None]
    min_body = min(body_values) if body_values else None

    if min_elbow > thresholds["pushup_bottom_elbow_max"]:
        issues.append(_issue("pushup_shallow", "medium",
                             f"最低点肘角约 {min_elbow:.0f} 度，大于 {thresholds['pushup_bottom_elbow_max']:.0f} 度，说明下放幅度不够。", min_elbow))
    if min_body is not None and min_body < thresholds["pushup_body_line_min"]:
        issues.append(_issue("pushup_body_line", "medium",
                             f"肩髋踝最小夹角约 {min_body:.0f} 度，小于 {thresholds['pushup_body_line_min']:.0f} 度的参考线，说明身体没有保持一条直线。", min_body))

    parts = [f"有效帧 {series['count']} 帧，最低点肘角约 {min_elbow:.0f} 度"]
    if min_body is not None:
        parts.append(f"肩髋踝最小夹角约 {min_body:.0f} 度")
    metrics = {
        "min_elbow_angle": min_elbow,
        "min_body_line_angle": min_body,
        "valid_frames": series["count"],
    }
    return _pack("ok", issues, metrics, parts, series)


def _analyze_pullup(series: Dict[str, object], thresholds: Dict[str, float], view: str) -> Dict[str, object]:
    elbow = series["elbow_angle"]
    chin = series["chin_margin"]
    hip_x = series["hip_mid_x"]
    shoulder_width = series["shoulder_width"]

    elbow_values = [value for value in elbow if value is not None]
    if not elbow_values:
        return _insufficient_result()
    issues: List[Dict[str, object]] = []

    # 顶部：手肘最弯的一帧，下巴应高于双手
    top_pos = min(range(len(elbow)), key=lambda position: elbow[position] if elbow[position] is not None else 999)
    chin_at_top = chin[top_pos]
    min_elbow = min(elbow_values)
    max_elbow = max(elbow_values)

    if chin_at_top is not None and chin_at_top <= thresholds["pullup_chin_margin_min"]:
        issues.append(_issue("pullup_chin_not_over_bar", "medium",
                             f"顶部下巴相对单杠的高度约 {chin_at_top:.2f}（以躯干长度为尺度，大于 0 表示过杠），没有达到过杠位置。", chin_at_top))

    if max_elbow < thresholds["pullup_hang_elbow_min"]:
        issues.append(_issue("pullup_not_full_hang", "medium",
                             f"全程最大肘角约 {max_elbow:.0f} 度，小于 {thresholds['pullup_hang_elbow_min']:.0f} 度，说明下放时手臂没有伸直。", max_elbow))

    swing_ratio = None
    # 身体前后摆动只在侧面视角下才能从二维画面里看出来
    if view == "side":
        width_values = [value for value in shoulder_width if value and value > 1e-6]
        hip_values = [value for value in hip_x if value is not None]
        if width_values and hip_values:
            width_ref = sum(width_values) / len(width_values)
            swing_ratio = (max(hip_values) - min(hip_values)) / width_ref
            if swing_ratio > thresholds["pullup_swing_max"]:
                issues.append(_issue("pullup_swing", "medium",
                                     f"髋部水平摆动幅度约肩宽的 {swing_ratio * 100:.0f} 百分比，"
                                     f"超过 {thresholds['pullup_swing_max'] * 100:.0f} 百分比的参考线，说明借用了惯性。", swing_ratio))

    parts = [f"有效帧 {series['count']} 帧，最低点肘角约 {min_elbow:.0f} 度"]
    if chin_at_top is not None:
        parts.append("顶部下巴" + ("已过杠" if chin_at_top > thresholds["pullup_chin_margin_min"] else "未过杠"))
    if view == "side" and swing_ratio is not None:
        parts.append(f"身体摆动约肩宽的 {swing_ratio * 100:.0f} 百分比")

    metrics = {
        "min_elbow_angle": min_elbow,
        "max_elbow_angle": max_elbow,
        "chin_margin_at_top": chin_at_top,
        "swing_ratio": swing_ratio,
        "valid_frames": series["count"],
        "top_frame_index": series["indices"][top_pos],
    }
    return _pack("ok", issues, metrics, parts, series)


def _minmax(values: List[Optional[float]]) -> Optional[Tuple[float, float]]:
    """返回非空序列的 (最小值, 最大值)，全为空时返回 None。"""
    vals = [value for value in values if value is not None]
    if not vals:
        return None
    return round(min(vals), 1), round(max(vals), 1)


def _analyze_custom(series: Dict[str, object], thresholds: Dict[str, float]) -> Dict[str, object]:
    """自由动作：不套用手写阈值，只汇总各角度范围，具体纠错交给大模型。"""
    metrics: Dict[str, object] = {
        "valid_frames": series["count"],
        "raw_frames": series["raw_count"],
    }
    for key in (
        "knee_angle",
        "elbow_angle",
        "hip_angle",
        "body_line_angle",
        "torso_lean",
        "valgus_ratio",
        "elbow_asymmetry",
    ):
        span = _minmax(series.get(key) or [])
        if span is not None:
            metrics[f"{key}_min"] = span[0]
            metrics[f"{key}_max"] = span[1]
    return _pack("ok", [], metrics, [], series)


def analyze_metrics_sequence(
    metrics: List[Dict[str, Optional[float]]],
    view: str = "side",
    thresholds: Optional[Dict[str, float]] = None,
    exercise: str = "squat",
) -> Dict[str, object]:
    """根据逐帧指标给出动作结论。metrics 由 frame_metrics 生成。"""
    thresholds = thresholds or DEFAULT_THRESHOLDS
    if exercise == "custom":
        series = _prepare(metrics, thresholds, exercise)
        if series is None:
            return _insufficient_result()
        return _analyze_custom(series, thresholds)
    if exercise not in EXERCISES:
        exercise = "squat"
    series = _prepare(metrics, thresholds, exercise)
    if series is None:
        return _insufficient_result()

    if exercise == "squat":
        return _analyze_squat(series, view, thresholds)
    if exercise == "deadlift":
        return _analyze_deadlift(series, thresholds)
    if exercise == "bench_press":
        return _analyze_bench(series, thresholds, view)
    if exercise == "pushup":
        return _analyze_pushup(series, thresholds)
    return _analyze_pullup(series, thresholds, view)


def analyze_landmark_frames(
    frames: List[Sequence[Dict[str, float]]],
    view: str = "side",
    thresholds: Optional[Dict[str, float]] = None,
    exercise: str = "squat",
) -> Dict[str, object]:
    """便捷入口：直接传入若干帧的关键点。"""
    per_frame = [frame_metrics(item, thresholds, exercise) if item else {} for item in frames]
    return analyze_metrics_sequence(per_frame, view=view, thresholds=thresholds, exercise=exercise)


def per_rep_issues(
    reps: Sequence[Dict[str, object]],
    metrics: Sequence[Dict[str, Optional[float]]],
    thresholds: Optional[Dict[str, float]] = None,
    exercise: str = "squat",
    view: str = "side",
) -> List[Dict[str, object]]:
    """给每一次动作单独判定它的问题，供逐次点评与出错帧标红。

    直接复用 analyze_metrics_sequence：把某一次动作的帧切片（start_index 到 end_index）
    单独跑一遍判定，取回这次动作自己的 issues。片段太短判不了时，返回空 issues 而不是
    "画面可信度不足"，避免把短动作误报成拍摄问题。
    """
    thresholds = thresholds or DEFAULT_THRESHOLDS
    results: List[Dict[str, object]] = []
    for rep in reps:
        start = int(rep.get("start_index", 0))
        end = int(rep.get("end_index", start))
        bottom = int(rep.get("bottom_index", start))
        slice_metrics = list(metrics[start:end + 1]) if end >= start else []
        issues: List[Dict[str, object]] = []
        if len(slice_metrics) >= 2:
            verdict = analyze_metrics_sequence(
                slice_metrics, view=view, thresholds=thresholds, exercise=exercise
            )
            if verdict.get("status") == "ok":
                issues = verdict.get("issues", [])
        results.append(
            {
                "rep": int(rep.get("rep", len(results) + 1)),
                "start_index": start,
                "bottom_index": bottom,
                "end_index": end,
                "issues": issues,
            }
        )
    return results
