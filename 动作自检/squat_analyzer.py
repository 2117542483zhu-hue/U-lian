"""动作自检主程序：视频进，结论出。

用法示例：
    python squat_analyzer.py ../../样例视频/深蹲_侧面.mp4 --view side
    python squat_analyzer.py 深蹲_正面.mp4 --view front --out ../../输出结果

输出三样东西：
    1. 带骨架和实时指标的 mp4（用于演示视频与截图）
    2. JSON 报告（供网站后端和大模型调用）
    3. PNG 结论卡片（用于作品简介的三张截图）
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Sequence

# matplotlib 会被 mediapipe 间接导入，先指定一个可写目录，避免缓存写入报错
os.environ.setdefault("MPLCONFIGDIR", str(Path(tempfile.gettempdir()) / "mplconfig"))

import cv2
import mediapipe as mp
import numpy as np
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision
from PIL import Image, ImageDraw, ImageFont

from pose_rules import (
    DEFAULT_THRESHOLDS,
    ERROR_JOINTS,
    EXERCISES,
    LM,
    _dominant_angle_key,
    analyze_metrics_sequence,
    detect_reps,
    frame_metrics,
    per_rep_issues,
)

SKELETON_PAIRS = [
    ("left_shoulder", "right_shoulder"),
    ("left_shoulder", "left_hip"),
    ("right_shoulder", "right_hip"),
    ("left_hip", "right_hip"),
    ("left_hip", "left_knee"),
    ("left_knee", "left_ankle"),
    ("right_hip", "right_knee"),
    ("right_knee", "right_ankle"),
]

POINT_NAMES = [
    "nose",
    "left_shoulder",
    "right_shoulder",
    "left_hip",
    "right_hip",
    "left_knee",
    "right_knee",
    "left_ankle",
    "right_ankle",
]

CJK_FONT_CANDIDATES = [
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "C:/Windows/Fonts/simsun.ttc",
]


def find_font(size: int) -> ImageFont.FreeTypeFont:
    for path in CJK_FONT_CANDIDATES:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except Exception:  # noqa: BLE001
                continue
    return ImageFont.load_default()


_font_cache: Dict[int, ImageFont.FreeTypeFont] = {}


def get_font(size: int) -> ImageFont.FreeTypeFont:
    if size not in _font_cache:
        _font_cache[size] = find_font(max(1, size))
    return _font_cache[size]


class OneEuroFilter:
    """一欧元滤波：低通滤掉静止时的高频抖动，又能在快速移动时自动放宽截止频率，减少滞后。"""

    def __init__(self, min_cutoff: float = 1.0, beta: float = 0.3, d_cutoff: float = 1.0) -> None:
        self.min_cutoff = min_cutoff
        self.beta = beta
        self.d_cutoff = d_cutoff
        self._x_prev: Optional[float] = None
        self._dx_prev: float = 0.0

    def _alpha(self, cutoff: float, dt: float) -> float:
        tau = 1.0 / (2.0 * math.pi * cutoff)
        return 1.0 / (1.0 + tau / dt)

    def __call__(self, x: float, dt: float) -> float:
        if dt <= 0:
            return x
        if self._x_prev is None:
            self._x_prev = x
            self._dx_prev = 0.0
            return x
        dx = (x - self._x_prev) / dt
        edx = self._dx_prev + self._alpha(self.d_cutoff, dt) * (dx - self._dx_prev)
        cutoff = self.min_cutoff + self.beta * abs(edx)
        x_hat = self._x_prev + self._alpha(cutoff, dt) * (x - self._x_prev)
        self._x_prev = x_hat
        self._dx_prev = edx
        return x_hat


class LandmarkSmoother:
    """对每一帧的关键点做时序平滑，消除逐帧抖动。"""

    def __init__(self, sample_fps: float = 4.0, num_landmarks: int = 33) -> None:
        self._dt = 1.0 / max(sample_fps, 0.5)
        self._num = num_landmarks
        self._filters = [[OneEuroFilter() for _ in range(2)] for _ in range(num_landmarks)]

    def smooth(self, landmarks: List[Dict[str, float]]) -> List[Dict[str, float]]:
        smoothed: List[Dict[str, float]] = []
        for i, point in enumerate(landmarks[: self._num]):
            x = self._filters[i][0](float(point["x"]), self._dt)
            y = self._filters[i][1](float(point["y"]), self._dt)
            smoothed.append({**point, "x": x, "y": y})
        return smoothed

    def reset(self) -> None:
        self._filters = [[OneEuroFilter() for _ in range(2)] for _ in range(self._num)]


def transcode_to_h264(source: Path, target: Path) -> bool:
    """把 OpenCV 写出的 MPEG-4 文件转成 H.264。

    浏览器（Chrome、Edge）不支持 OpenCV 默认的 mp4v 编码，不转码网页里会黑屏。
    """
    try:
        import imageio_ffmpeg

        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001
        return False
    command = [
        ffmpeg,
        "-y",
        "-loglevel",
        "error",
        "-i",
        str(source),
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "23",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        str(target),
    ]
    try:
        subprocess.run(command, check=True, capture_output=True, timeout=300)
    except Exception:  # noqa: BLE001
        return False
    return target.exists() and target.stat().st_size > 0


def landmarks_to_dicts(landmarks: Sequence) -> List[Dict[str, float]]:
    return [
        {
            "x": float(item.x),
            "y": float(item.y),
            "z": float(getattr(item, "z", 0.0)),
            "visibility": float(getattr(item, "visibility", 0.0) or 0.0),
        }
        for item in landmarks
    ]


def ensure_ascii_model_path(model_path: Path) -> Path:
    """MediaPipe 的底层接口不支持中文路径，必要时把模型复制到临时目录再加载。"""
    try:
        str(model_path).encode("ascii")
        return model_path
    except UnicodeEncodeError:
        pass

    cache_dir = Path(tempfile.gettempdir()) / "pose_model_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    target = cache_dir / model_path.name
    if not target.exists() or target.stat().st_size != model_path.stat().st_size:
        shutil.copy2(model_path, target)
    return target


def _close_async(*objs) -> None:
    """在后台线程关闭 MediaPipe 资源。

    mediapipe 1.0.1 在 Windows 上的 close()（C++ 层 teardown）会阻塞约 40 秒，
    两个识别器就是 80 秒。放到后台守护线程里关闭，主流程不等待；
    每个识别器单独开线程并行关闭，网页场景下资源能更快释放，都不影响结果。
    """

    def _run(obj) -> None:
        try:
            obj.close()
        except Exception:  # noqa: BLE001
            pass

    for obj in objs:
        threading.Thread(target=_run, args=(obj,), daemon=True).start()


def create_landmarker(model_path: Path, running_mode: str = "video") -> "vision.PoseLandmarker":
    """创建姿态识别器。

    running_mode="video"：跟踪模式（逐帧传入递增时间戳），后续帧用上一帧结果跟踪，
    比每帧重新检测快一个数量级，适合连续抽帧。
    running_mode="image"：单帧模式（每帧独立识别），用于裁剪放大后的二次精修，
    无状态、不受跟踪漂移影响。
    """
    if not model_path.exists():
        raise FileNotFoundError(
            f"没有找到姿态模型文件：{model_path}\n"
            "请确认 模型/pose_landmarker_lite.task 是否存在，或重新运行一键安装脚本。"
        )
    model_path = ensure_ascii_model_path(model_path)
    mode = vision.RunningMode.VIDEO if running_mode == "video" else vision.RunningMode.IMAGE
    options = vision.PoseLandmarkerOptions(
        base_options=mp_python.BaseOptions(model_asset_path=str(model_path)),
        running_mode=mode,
        num_poses=1,
        min_pose_detection_confidence=0.5,
        min_pose_presence_confidence=0.5,
        min_tracking_confidence=0.5,
    )
    return vision.PoseLandmarker.create_from_options(options)


class Landmark:
    """裁剪放大后重新映射回来的关键点，字段与 MediaPipe 的输出保持一致。"""

    __slots__ = ("x", "y", "z", "visibility", "presence")

    def __init__(self, x: float, y: float, z: float = 0.0, visibility: float = 1.0, presence: float = 1.0) -> None:
        self.x = x
        self.y = y
        self.z = z
        self.visibility = visibility
        self.presence = presence


def detect_landmarks(
    landmarker: "vision.PoseLandmarker",
    refine_landmarker: Optional["vision.PoseLandmarker"],
    rgb: np.ndarray,
    timestamp_ms: int,
    refine: bool = True,
):
    """两段式识别：先整帧找人（跟踪模式，快），再按人体框裁剪放大后重新识别。

    手机视频里人往往只占画面的一小部分，直接整帧识别的关键点误差较大，
    裁剪放大后关键点会明显贴合身体。整帧用跟踪模式大幅提速，裁剪精修
    用独立的单帧识别器，互不干扰。
    """
    height, width = rgb.shape[:2]
    image = mp.Image(image_format=mp.ImageFormat.SRGB, data=np.ascontiguousarray(rgb))
    result = landmarker.detect_for_video(image, timestamp_ms)
    if not result.pose_landmarks:
        return None

    landmarks = result.pose_landmarks[0]
    if not refine or refine_landmarker is None:
        return landmarks

    xs = [point.x for point in landmarks]
    ys = [point.y for point in landmarks]
    box_width = max(xs) - min(xs)
    box_height = max(ys) - min(ys)
    if box_height <= 0 or box_width <= 0:
        return landmarks
    # 人体已经占满画面时不再裁剪，避免把四肢裁掉
    if box_height > 0.55:
        return landmarks

    margin_x = box_width * 0.25
    margin_y = box_height * 0.15
    x0 = max(0, int((min(xs) - margin_x) * width))
    x1 = min(width, int((max(xs) + margin_x) * width))
    y0 = max(0, int((min(ys) - margin_y) * height))
    y1 = min(height, int((max(ys) + margin_y) * height))
    if x1 - x0 < 32 or y1 - y0 < 64:
        return landmarks

    crop = rgb[y0:y1, x0:x1]
    scale = 720 / crop.shape[0]
    if scale > 1.0:
        crop = cv2.resize(crop, (int(crop.shape[1] * scale), int(crop.shape[0] * scale)), interpolation=cv2.INTER_CUBIC)

    crop_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=np.ascontiguousarray(crop))
    refined = refine_landmarker.detect(crop_image)
    if not refined.pose_landmarks:
        return landmarks

    crop_height, crop_width = crop.shape[:2]
    mapped = []
    for point in refined.pose_landmarks[0]:
        mapped.append(
            Landmark(
                (x0 + point.x * crop_width) / width,
                (y0 + point.y * crop_height) / height,
                point.z,
                point.visibility,
                getattr(point, "presence", 0.0),
            )
        )
    return mapped


# 面板与文字颜色（RGB，供 PIL 使用）
PANEL_BG = (18, 22, 30)
PANEL_CARD = (33, 39, 51)
PANEL_CARD_SOFT = (27, 32, 42)
PANEL_BORDER = (58, 68, 84)
PANEL_ACCENT = (94, 234, 212)   # 青绿：计数与当前次高亮
PANEL_OK = (74, 222, 128)       # 绿：规范
PANEL_WARN = (255, 165, 0)      # 橙：问题标签（呼应错误关节橙色）
TEXT_MAIN = (236, 240, 246)
TEXT_DIM = (150, 158, 172)
TEXT_ANGLE = (255, 225, 80)

# 骨骼配色（BGR，供 cv2 使用）：柔和青 + 橙色错误关节高亮
SKELETON_LINE = (210, 205, 120)
SKELETON_POINT = (185, 175, 90)
ERROR_JOINT = (0, 165, 255)

# 问题 -> 视频侧栏里的一句话通俗纠正
ISSUE_CUE = {
    "shallow_depth": "下蹲过浅：再往下坐，让大腿接近平行。",
    "knee_valgus": "膝盖内扣：膝盖向外撑，对准脚尖。",
    "excessive_lean": "躯干前倾：胸口朝前上方，别趴太低。",
    "hip_first": "起身弯腰：先伸膝再伸髋，别先撅臀。",
    "deadlift_start_lean": "起始前倾：降低臀位，胸口挺起。",
    "deadlift_hip_first": "起身髋先动：髋膝同步发力，别先撅臀。",
    "deadlift_over_extension": "锁定后仰：站直即可，别向后挺腰。",
    "deadlift_not_locked": "未站直：站到膝盖伸直再放杠。",
    "bench_shallow": "下放不足：杠铃再贴近胸口。",
    "bench_not_pressed": "没推直：推到手臂伸直。",
    "bench_hip_lift": "臀部离凳：臀部贴凳，脚踩实。",
    "bench_asymmetric": "两侧不均：两侧同步发力。",
    "pushup_shallow": "下放不足：胸口再贴近地面。",
    "pushup_body_line": "身体不成线：收紧核心，别塌腰撅臀。",
    "pullup_chin_not_over_bar": "下巴未过杠：再拉高，让下巴过杠。",
    "pullup_not_full_hang": "下放不全：下放到手臂伸直。",
    "pullup_swing": "身体摆动：收腹屈膝，减少晃动。",
}


def _panel_width(width: int) -> int:
    """侧边面板宽度，随画面宽度缩放。"""
    return max(280, min(380, int(width * 0.30)))


def _to_pil(bgr: np.ndarray) -> Image.Image:
    return Image.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))


def _from_pil(img: Image.Image) -> np.ndarray:
    return cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2BGR)


def _pil_text(draw: ImageDraw.ImageDraw, text: str, pos: tuple, size: int, fill: tuple) -> None:
    font = get_font(size)
    draw.text(pos, text, font=font, fill=fill, stroke_width=max(1, size // 12), stroke_fill=(0, 0, 0))


def _panel_text(draw: ImageDraw.ImageDraw, text: str, pos: tuple, size: int, fill: tuple) -> None:
    """面板文字（画在纯色面板上，不加描边，更干净）。"""
    draw.text(pos, text, font=get_font(size), fill=fill)


def _draw_skeleton(
    canvas: np.ndarray,
    landmarks: List[Dict[str, float]],
    width: int,
    height: int,
    error_joints: Optional[set] = None,
) -> None:
    """画骨骼连线（柔和青）+ 关节点，错误关节用橙色高亮。"""
    error_joints = error_joints or set()
    for start, end in SKELETON_PAIRS:
        point_a = landmarks[LM[start]]
        point_b = landmarks[LM[end]]
        if point_a["visibility"] < 0.3 or point_b["visibility"] < 0.3:
            continue
        cv2.line(
            canvas,
            (int(point_a["x"] * width), int(point_a["y"] * height)),
            (int(point_b["x"] * width), int(point_b["y"] * height)),
            SKELETON_LINE,
            3,
            cv2.LINE_AA,
        )
    for name in POINT_NAMES:
        point = landmarks[LM[name]]
        if point["visibility"] < 0.3:
            continue
        cv2.circle(
            canvas,
            (int(point["x"] * width), int(point["y"] * height)),
            5,
            SKELETON_POINT,
            -1,
            cv2.LINE_AA,
        )
    for name in error_joints:
        index = LM.get(name)
        if index is None:
            continue
        point = landmarks[index]
        if point["visibility"] < 0.3:
            continue
        px, py = int(point["x"] * width), int(point["y"] * height)
        cv2.circle(canvas, (px, py), 12, ERROR_JOINT, 3, cv2.LINE_AA)
        cv2.circle(canvas, (px, py), 4, ERROR_JOINT, -1, cv2.LINE_AA)


def _draw_angle_fan(canvas: np.ndarray, a: tuple, b: tuple, c: tuple, radius: int = 26) -> Optional[tuple]:
    """在顶点 b 处画一段角度弧（a-b-c），返回数字标注的建议位置。"""
    bx, by = b
    va = (a[0] - bx, a[1] - by)
    vc = (c[0] - bx, c[1] - by)
    na = math.hypot(*va)
    nc = math.hypot(*vc)
    if na < 1e-3 or nc < 1e-3:
        return None
    ua = (va[0] / na, va[1] / na)
    uc = (vc[0] / nc, vc[1] / nc)
    steps = 14
    for i in range(steps + 1):
        t = i / steps
        dx = ua[0] * (1 - t) + uc[0] * t
        dy = ua[1] * (1 - t) + uc[1] * t
        n = math.hypot(dx, dy) or 1.0
        dx, dy = dx / n, dy / n
        cv2.circle(canvas, (int(bx + dx * radius), int(by + dy * radius)), 2, (0, 255, 255), -1, cv2.LINE_AA)
    mid_x = (ua[0] + uc[0]) / 2
    mid_y = (ua[1] + uc[1]) / 2
    mn = math.hypot(mid_x, mid_y) or 1.0
    return (int(bx + mid_x / mn * (radius + 28)), int(by + mid_y / mn * (radius + 28)))


def _draw_angle_fans(
    canvas: np.ndarray,
    landmarks: List[Dict[str, float]],
    metrics: Dict[str, Optional[float]],
    width: int,
    height: int,
    exercise: str,
    dominant_key: Optional[str] = None,
) -> List[tuple]:
    """在关键关节旁画出弯曲角度，返回 [(x, y, 文本), ...]。"""
    labels: List[tuple] = []
    if exercise == "custom":
        if dominant_key == "elbow_angle":
            proximal, vertex, distal = "shoulder", "elbow", "wrist"
            angle_left = metrics.get("elbow_angle_left")
            angle_right = metrics.get("elbow_angle_right")
        else:
            proximal, vertex, distal = "hip", "knee", "ankle"
            angle_left = metrics.get("knee_angle_left")
            angle_right = metrics.get("knee_angle_right")
    elif exercise in ("bench_press", "pullup"):
        proximal, vertex, distal = "shoulder", "elbow", "wrist"
        angle_left = metrics.get("elbow_angle_left")
        angle_right = metrics.get("elbow_angle_right")
    else:
        proximal, vertex, distal = "hip", "knee", "ankle"
        angle_left = metrics.get("knee_angle_left")
        angle_right = metrics.get("knee_angle_right")

    for side, angle in (("left", angle_left), ("right", angle_right)):
        if angle is None:
            continue
        a = landmarks[LM[f"{side}_{proximal}"]]
        b = landmarks[LM[f"{side}_{vertex}"]]
        c = landmarks[LM[f"{side}_{distal}"]]
        if min(a["visibility"], b["visibility"], c["visibility"]) < 0.3:
            continue
        label_pos = _draw_angle_fan(
            canvas,
            (a["x"] * width, a["y"] * height),
            (b["x"] * width, b["y"] * height),
            (c["x"] * width, c["y"] * height),
        )
        if label_pos is not None:
            labels.append((label_pos[0], label_pos[1], f"{angle:.0f}°"))
    return labels


def _top_metric_lines(metrics: Dict[str, Optional[float]], view: str, exercise: str) -> List[str]:
    lines: List[str] = []
    if metrics.get("knee_angle") is not None:
        lines.append(f"膝关节 {metrics['knee_angle']:.0f}°")
    if exercise in ("bench_press", "custom") and metrics.get("elbow_angle") is not None:
        lines.append(f"肘关节 {metrics['elbow_angle']:.0f}°")
    if exercise == "custom" and metrics.get("hip_angle") is not None:
        lines.append(f"髋关节 {metrics['hip_angle']:.0f}°")
    if metrics.get("torso_lean") is not None:
        lines.append(f"躯干前倾 {metrics['torso_lean']:.0f}°")
    # 膝内扣只在正面拍摄时才有意义，侧面画面里两条腿重叠，数值没有参考价值
    if view == "front" and metrics.get("valgus_ratio") is not None:
        lines.append(f"膝内移 {metrics['valgus_ratio'] * 100:.0f}%")
    if metrics.get("visibility") is not None:
        lines.append(f"可信度 {metrics['visibility']:.2f}")
    return lines


def _wrap_text(draw: ImageDraw.ImageDraw, text: str, max_width: int, size: int) -> List[str]:
    """按像素宽度对中文提示折行。"""
    font = get_font(size)
    lines: List[str] = []
    current = ""
    for ch in text:
        if draw.textlength(current + ch, font=font) <= max_width:
            current += ch
        else:
            lines.append(current)
            current = ch
    if current:
        lines.append(current)
    return lines or [""]


def _clip_text(draw: ImageDraw.ImageDraw, text: str, max_width: int, size: int) -> str:
    """超宽时截断并加省略号。"""
    font = get_font(size)
    if draw.textlength(text, font=font) <= max_width:
        return text
    result = ""
    for ch in text:
        if draw.textlength(result + ch + "…", font=font) > max_width:
            break
        result += ch
    return result + "…"


def _current_cue(
    sample_index: int,
    phase: str,
    per_rep_issues: Optional[List[Dict[str, object]]],
    exercise: str = "squat",
) -> str:
    """当前帧的通俗纠正提示：有问题优先讲问题，否则按阶段给一句提示。"""
    current: Optional[Dict[str, object]] = None
    for rep_info in per_rep_issues or []:
        if rep_info["start_index"] <= sample_index <= rep_info["end_index"]:
            current = rep_info
            break
    if current and current.get("issues"):
        key = current["issues"][0]["key"]
        return ISSUE_CUE.get(key, "注意动作规范，保持节奏。")
    if exercise == "custom":
        if phase == "下放中":
            return "放慢速度，控制动作幅度，保持稳定。"
        if phase == "起身中":
            return "发力时保持身体稳定，别借惯性。"
        if phase == "保持":
            return "收紧核心，动作全程在控制中。"
        return "保持自然节奏，全身入镜。"
    if phase == "下放中":
        return "慢慢下蹲，坐向脚跟，膝盖对准脚尖。"
    if phase == "起身中":
        return "脚跟发力站起，收紧核心，别前倾。"
    if phase == "保持":
        return "站直，保持背部中立。"
    return "保持自然节奏，全身入镜。"


def _draw_panel(
    draw: ImageDraw.ImageDraw,
    width: int,
    height: int,
    out_width: int,
    panel_w: int,
    reps: List[Dict[str, object]],
    sample_index: int,
    phase: str,
    view: str,
    exercise: str,
    scale: float,
    per_rep_issues: Optional[List[Dict[str, object]]] = None,
    exercise_label: str = "",
) -> None:
    """右侧信息面板：完成次数、通俗纠正提示，以及每一次的问题点评。"""
    x0 = width
    draw.rectangle([x0, 0, out_width, height], fill=PANEL_BG)
    pad = int(20 * scale)
    cx = x0 + pad
    cw = panel_w - 2 * pad

    label = exercise_label or EXERCISES.get(exercise, {}).get("label", "动作")
    view_label = "正面" if view == "front" else "侧面"

    # 标题：左侧一小段强调竖条 + 标题 / 副标题
    draw.rounded_rectangle(
        [cx, int(22 * scale), cx + int(5 * scale), int(46 * scale)],
        radius=2, fill=PANEL_ACCENT,
    )
    _panel_text(draw, "动作点评", (cx + int(14 * scale), int(20 * scale)), int(24 * scale), TEXT_MAIN)
    _panel_text(draw, f"{label} · {view_label}", (cx + int(14 * scale), int(50 * scale)), int(15 * scale), TEXT_DIM)

    y = int(84 * scale)

    # 完成次数：横向排版，数字大号、单位小号贴底对齐
    done = sum(1 for rep in reps if rep["bottom_index"] <= sample_index)
    box_h = int(92 * scale)
    draw.rounded_rectangle([cx, y, cx + cw, y + box_h], radius=int(16 * scale), fill=PANEL_CARD)
    _panel_text(draw, "完成次数", (cx + int(16 * scale), y + int(14 * scale)), int(14 * scale), TEXT_DIM)
    _panel_text(draw, str(done), (cx + int(16 * scale), y + int(30 * scale)), int(40 * scale), PANEL_ACCENT)
    num_w = draw.textlength(str(done), font=get_font(int(40 * scale)))
    _panel_text(draw, "次", (cx + int(16 * scale) + int(num_w) + int(8 * scale), y + int(54 * scale)), int(15 * scale), TEXT_DIM)

    y += box_h + int(14 * scale)

    # 通俗纠正提示（替代原来的"当前角度 X°"）：左侧强调竖条 + 标题 + 1~2 行提示
    cue = _current_cue(sample_index, phase, per_rep_issues, exercise)
    cue_lines = _wrap_text(draw, cue, cw - int(42 * scale), int(18 * scale))[:2]
    box_h = int(42 * scale) + len(cue_lines) * int(24 * scale) + int(14 * scale)
    draw.rounded_rectangle([cx, y, cx + cw, y + box_h], radius=int(16 * scale), fill=PANEL_CARD)
    draw.rounded_rectangle(
        [cx, y + int(18 * scale), cx + int(4 * scale), y + box_h - int(18 * scale)],
        radius=2, fill=PANEL_ACCENT,
    )
    _panel_text(draw, "怎么改", (cx + int(20 * scale), y + int(12 * scale)), int(14 * scale), TEXT_DIM)
    ty = y + int(36 * scale)
    for line in cue_lines:
        _panel_text(draw, line, (cx + int(20 * scale), ty), int(18 * scale), TEXT_MAIN)
        ty += int(24 * scale)

    y += box_h + int(16 * scale)

    draw.line([cx, y, cx + cw, y], fill=PANEL_BORDER, width=1)
    y += int(14 * scale)

    if not per_rep_issues:
        _panel_text(draw, "尚未记录到完整动作", (cx, y + int(6 * scale)), int(15 * scale), TEXT_DIM)
        return

    # 逐次点评：只显示问题短标签，去掉角度/秒数的密集列表
    row_h = int(34 * scale)
    gap = int(8 * scale)
    visible_items: List[Dict[str, object]] = []
    y_cursor = y
    for rep_info in reversed(per_rep_issues):
        if y_cursor + row_h > height - int(16 * scale):
            break
        visible_items.append(rep_info)
        y_cursor += row_h + gap
    visible_items.reverse()

    for rep_info in visible_items:
        current = rep_info["start_index"] <= sample_index <= rep_info["end_index"]
        row_fill = PANEL_CARD if current else PANEL_CARD_SOFT
        draw.rounded_rectangle([cx, y, cx + cw, y + row_h], radius=int(10 * scale), fill=row_fill)
        if current:
            draw.rounded_rectangle([cx, y, cx + cw, y + row_h], radius=int(10 * scale), outline=PANEL_ACCENT, width=1)
        _panel_text(draw, f"第{rep_info['rep']}次", (cx + int(12 * scale), y + int(8 * scale)), int(16 * scale), TEXT_MAIN)
        short = " · ".join(item.get("short", item.get("name", "")) for item in rep_info.get("issues", [])) or "规范"
        tag_color = PANEL_OK if short == "规范" else PANEL_WARN
        _panel_text(draw, _clip_text(draw, short, cw - int(84 * scale), int(16 * scale)), (cx + int(66 * scale), y + int(8 * scale)), int(16 * scale), tag_color)
        y += row_h + gap


def _render_frame(
    frame: np.ndarray,
    rec: Dict[str, object],
    reps: List[Dict[str, object]],
    sample_index: int,
    phase: str,
    view: str,
    exercise: str,
    panel_w: int,
    per_rep_issues: Optional[List[Dict[str, object]]] = None,
    exercise_label: str = "",
    dominant_key: Optional[str] = None,
) -> np.ndarray:
    """把一帧画面、骨架、角度标注和侧边面板合成到一起。"""
    height, width = frame.shape[:2]
    out_width = width + panel_w
    canvas = np.full((height, out_width, 3), 24, dtype=np.uint8)
    canvas[:, :width] = frame

    landmarks = rec["landmarks"]
    metrics = rec["metrics"]
    angle_labels: List[tuple] = []

    error_joints: set = set()
    if landmarks is not None:
        for rep_info in per_rep_issues or []:
            if rep_info["start_index"] <= sample_index <= rep_info["end_index"]:
                for issue in rep_info.get("issues", []):
                    error_joints.update(ERROR_JOINTS.get(issue["key"], []))
                break
        _draw_skeleton(canvas, landmarks, width, height, error_joints)
        angle_labels = _draw_angle_fans(canvas, landmarks, metrics, width, height, exercise, dominant_key)

    pil = _to_pil(canvas)
    draw = ImageDraw.Draw(pil)
    scale = max(0.8, min(width, height) / 720.0)

    if landmarks is not None:
        for index, text in enumerate(_top_metric_lines(metrics, view, exercise)):
            _pil_text(draw, text, (16, 30 + index * int(30 * scale)), int(20 * scale), TEXT_MAIN)
        for (x, y, text) in angle_labels:
            _pil_text(draw, text, (x, y), int(22 * scale), TEXT_ANGLE)
    else:
        _pil_text(draw, "未检测到人体，请检查拍摄角度与距离", (16, 40), int(22 * scale), (255, 120, 120))

    _draw_panel(draw, width, height, out_width, panel_w, reps, sample_index, phase, view, exercise, scale, per_rep_issues, exercise_label)

    return _from_pil(pil)


def render_report_card(
    result: Dict[str, object],
    video_name: str,
    out_path: Path,
    view: str = "side",
    exercise: str = "squat",
) -> None:
    """生成结论卡片。"""
    width, height = 1200, 760
    card = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(card)

    title_font = find_font(44)
    head_font = find_font(30)
    body_font = find_font(26)
    small_font = find_font(20)

    label = str(EXERCISES.get(exercise, {}).get("label", "动作"))
    view_label = "正面" if view == "front" else "侧面"
    draw.rectangle([0, 0, width, 96], fill=(31, 58, 95))
    draw.text((36, 26), f"动作自检报告 · {label}（{view_label}）", font=title_font, fill="white")

    draw.text((36, 124), f"视频文件：{video_name}", font=body_font, fill=(20, 20, 20))
    draw.text(
        (36, 166),
        f"生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}",
        font=small_font,
        fill=(90, 90, 90),
    )

    issues = result.get("issues") or []
    status_text = "未检出明显问题" if not issues else f"检出 {len(issues)} 个问题"
    draw.text((36, 214), "结论", font=head_font, fill=(31, 58, 95))
    draw.text((160, 214), status_text, font=head_font, fill=(20, 20, 20))

    y = 270
    if issues:
        for item in issues:
            draw.text((36, y), f"- {item['name']}（{item['severity']}）", font=body_font, fill=(180, 40, 40))
            y += 38
            draw.text((60, y), str(item["evidence"]), font=small_font, fill=(60, 60, 60))
            y += 32
            fixes = item.get("fixes") or []
            if fixes:
                draw.text(
                    (60, y),
                    f"建议练：{fixes[0]['name']}。{fixes[0]['how']}",
                    font=small_font,
                    fill=(60, 60, 60),
                )
                y += 38
            y += 6
    else:
        draw.text((60, y), result.get("summary", ""), font=small_font, fill=(60, 60, 60))
        y += 60

    manifest = result.get("metrics") or {}
    draw.text((36, y + 10), "关键指标", font=head_font, fill=(31, 58, 95))
    y += 60
    if exercise == "squat":
        rows = [
            ("最低点膝关节角度", manifest.get("min_knee_angle")),
            ("最低点躯干前倾角", manifest.get("torso_lean_at_bottom")),
            ("起身髋膝速率比", manifest.get("hip_first_ratio")),
        ]
        if view == "front":
            rows.insert(2, ("最低点膝内移比例", manifest.get("valgus_ratio_at_bottom")))
    elif exercise == "deadlift":
        rows = [
            ("起始躯干前倾角", manifest.get("start_lean")),
            ("锁定位置躯干角度", manifest.get("lockout_lean")),
            ("全程最大膝关节角度", manifest.get("max_knee_angle")),
            ("起身髋膝速率比", manifest.get("hip_first_ratio")),
        ]
    elif exercise == "bench_press":
        rows = [
            ("最低点肘角", manifest.get("min_elbow_angle")),
            ("最高点肘角", manifest.get("max_elbow_angle")),
            ("肩髋偏离水平最大角度", manifest.get("hip_lift_angle_max")),
            ("两侧肘角最大差异", manifest.get("elbow_asymmetry_max")),
        ]
    elif exercise == "pullup":
        rows = [
            ("最低点肘角", manifest.get("min_elbow_angle")),
            ("最高点肘角", manifest.get("max_elbow_angle")),
            ("顶部下巴过杠量", manifest.get("chin_margin_at_top")),
            ("身体摆动幅度", manifest.get("swing_ratio")),
        ]
    else:
        rows = [
            ("最低点肘角", manifest.get("min_elbow_angle")),
            ("肩髋踝最小夹角", manifest.get("min_body_line_angle")),
        ]
    for label, value in rows:
        if value is None:
            text = "未计算"
        elif label == "最低点膝内移比例":
            text = f"{value * 100:.0f}%"
        else:
            text = f"{value:.1f}"
        draw.text((60, y), f"{label}：{text}", font=small_font, fill=(40, 40, 40))
        y += 32

    draw.line([(36, height - 70), (width - 36, height - 70)], fill=(210, 210, 210), width=2)
    draw.text(
        (36, height - 56),
        "结果由姿态识别自动生成，仅用于训练动作参考，不构成医疗或康复诊断。",
        font=small_font,
        fill=(120, 120, 120),
    )
    card.save(out_path)


def _narration_text(
    reps: List[Dict[str, object]],
    per_rep_issues: List[Dict[str, object]],
    exercise: str,
    posture_note: Optional[Dict[str, object]] = None,
    label: str = "",
    overall_issues: Optional[List[Dict[str, object]]] = None,
) -> str:
    """拼出「整体汇总播报」文案：完成次数 + 整体评价 + 逐次纠错 + 体态联动提示。"""
    label = label or EXERCISES.get(exercise, {}).get("label", "动作")
    n = len(reps)
    if n == 0:
        return "本次未识别到完整动作，请调整拍摄角度后重试。"

    parts = [f"姿态识别完毕，本次一共完成{n}次{label}。"]

    if exercise == "custom":
        if overall_issues is None:
            parts.append("已为你统计完成次数与关节角度范围。")
        elif not overall_issues:
            parts.append("整体动作较为规范，继续保持。")
        else:
            parts.append("本次动作还需改进，")
            for issue in overall_issues[:2]:
                name = str(issue.get("short") or issue.get("name") or "动作问题")
                fixes = issue.get("fixes") or []
                cue = str(fixes[0].get("how")) if fixes else "注意动作规范。"
                parts.append(f"存在{name}，请{cue}")
    else:
        problem_reps = [r for r in (per_rep_issues or []) if r.get("issues")]
        if not problem_reps:
            parts.append("整体动作较为规范，继续保持。")
        else:
            parts.append("整体动作较为规范，" if len(problem_reps) <= max(1, n // 2) else "本次动作还需改进，")
            for rep_info in problem_reps[:2]:
                issue = rep_info["issues"][0]
                key = str(issue.get("key", ""))
                short = str(issue.get("short") or issue.get("name") or "动作问题")
                cue = ISSUE_CUE.get(key, "注意动作规范。")
                if "：" in cue:
                    cue = cue.split("：", 1)[1]
                parts.append(f"第{rep_info['rep']}次存在{short}，请{cue}")

    if posture_note and posture_note.get("voice"):
        parts.append(str(posture_note["voice"]))

    return "".join(parts)


def _metrics_summary(series: List[Dict[str, Optional[float]]]) -> Dict[str, object]:
    """把整段逐帧指标汇总成给大模型看的量程统计（min/max/mean）。"""
    keys = (
        "knee_angle",
        "elbow_angle",
        "hip_angle",
        "body_line_angle",
        "torso_lean",
        "valgus_ratio",
        "elbow_asymmetry",
    )
    valid = [m for m in series if m]
    summary: Dict[str, object] = {"valid_frames": len(valid), "total_frames": len(series)}
    for key in keys:
        values = [m.get(key) for m in valid if m.get(key) is not None]
        if not values:
            continue
        summary[key] = {
            "min": round(min(values), 1),
            "max": round(max(values), 1),
            "mean": round(sum(values) / len(values), 1),
        }
    return summary


def analyze_video(
    video_path: Path,
    model_path: Path,
    view: str,
    out_dir: Path,
    sample_fps: float,
    max_seconds: float,
    thresholds: Optional[Dict[str, float]] = None,
    display_name: Optional[str] = None,
    exercise: str = "squat",
    voice: bool = True,
    posture_note: Optional[Dict[str, object]] = None,
    custom_name: Optional[str] = None,
    custom_judge=None,
) -> Dict[str, object]:
    thresholds = dict(thresholds or DEFAULT_THRESHOLDS)
    custom_label = (custom_name or "").strip() if exercise == "custom" else ""
    exercise_label = custom_label or EXERCISES.get(exercise, {}).get("label", exercise)
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise RuntimeError(f"无法打开视频：{video_path}")

    source_fps = capture.get(cv2.CAP_PROP_FPS) or 25.0
    width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    step = max(1, int(round(source_fps / max(sample_fps, 0.5))))
    max_frames = int(max_seconds * source_fps)
    writer_fps = max(sample_fps, 1.0)

    out_dir.mkdir(parents=True, exist_ok=True)
    stem = video_path.stem
    overlay_path = out_dir / f"{stem}_骨架.mp4"
    raw_path = out_dir / f"{stem}_骨架_raw.mp4"
    json_path = out_dir / f"{stem}_报告.json"
    card_path = out_dir / f"{stem}_结论卡.png"

    panel_w = _panel_width(width)
    out_width = width + panel_w

    landmarker = create_landmarker(model_path, "video")
    refine_landmarker = create_landmarker(model_path, "image")
    smoother = LandmarkSmoother(sample_fps=writer_fps)
    per_frame_metrics: List[Dict[str, Optional[float]]] = []
    frame_records: List[Dict[str, object]] = []
    sampled_frames: List[Dict[str, object]] = []
    frame_index = 0
    sampled = 0

    # 第一遍：识别 + 平滑 + 算指标，把每帧的关键点与指标缓存下来（第二遍重画用）
    try:
        while True:
            if frame_index % step == 0:
                ok, frame = capture.read()
                if not ok:
                    break
            else:
                # 非抽帧只需推进解码，跳过 BGR 转换，省去大部分解码开销
                if not capture.grab():
                    break
            if frame_index >= max_frames:
                break
            if frame_index % step == 0:
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                detected = detect_landmarks(landmarker, refine_landmarker, rgb, sampled)

                if detected:
                    landmarks = landmarks_to_dicts(detected)
                    landmarks = smoother.smooth(landmarks)
                    metrics = frame_metrics(landmarks, thresholds, exercise)
                else:
                    landmarks = None
                    metrics = {}
                    smoother.reset()

                per_frame_metrics.append(metrics)
                frame_records.append(
                    {
                        "frame_index": frame_index,
                        "time_seconds": round(frame_index / source_fps, 2),
                        "detected": bool(landmarks),
                        "metrics": metrics,
                    }
                )
                sampled_frames.append({"frame_index": frame_index, "landmarks": landmarks, "metrics": metrics})
                sampled += 1
            frame_index += 1
    finally:
        _close_async(landmarker, refine_landmarker)
        capture.release()

    # 从整段指标里切出每一次动作（次数、最低角度、下放/起身/全程用时）
    reps = detect_reps(per_frame_metrics, thresholds, exercise, sample_fps=writer_fps)
    # 给每一次动作单独判定问题（逐次点评 + 出错帧标红 + 错误关节高亮）
    per_rep_issue_list = per_rep_issues(reps, per_frame_metrics, thresholds, exercise, view)
    # 自由动作：按幅度最大的角度自动识别主关节（用于次数切分、阶段与角度弧）
    dominant_angle_key = _dominant_angle_key(per_frame_metrics) if exercise == "custom" else None

    # 第二遍：用缓存的关键点重画，叠加侧边面板，写出带面板的视频
    fourcc = getattr(cv2, "VideoWriter_fourcc", None)
    codec = fourcc(*"mp4v") if fourcc else cv2.VideoWriter.fourcc(*"mp4v")
    writer = cv2.VideoWriter(str(raw_path), codec, writer_fps, (out_width, height))
    capture2 = cv2.VideoCapture(str(video_path))
    try:
        frame_index = 0
        sampled_i = 0
        prev_angle: Optional[float] = None
        while True:
            if frame_index % step == 0:
                ok, frame = capture2.read()
                if not ok:
                    break
            else:
                if not capture2.grab():
                    break
            if frame_index >= max_frames:
                break
            if frame_index % step == 0 and sampled_i < len(sampled_frames):
                rec = sampled_frames[sampled_i]
                metrics = rec["metrics"]
                if exercise == "custom":
                    angle = metrics.get(dominant_angle_key) if dominant_angle_key else metrics.get("knee_angle")
                else:
                    angle = metrics.get("knee_angle") if exercise in ("squat", "deadlift", "pushup") else metrics.get("elbow_angle")
                if angle is not None and prev_angle is not None:
                    diff = angle - prev_angle
                    phase = "下放中" if diff < -0.5 else ("起身中" if diff > 0.5 else "保持")
                else:
                    phase = "—"
                prev_angle = angle
                canvas = _render_frame(
                    frame, rec, reps, sampled_i, phase, view, exercise, panel_w,
                    per_rep_issue_list, exercise_label, dominant_angle_key,
                )
                writer.write(canvas)
                sampled_i += 1
            frame_index += 1
    finally:
        capture2.release()
        writer.release()

    # 转成 H.264，保证在浏览器里能直接播放
    if transcode_to_h264(raw_path, overlay_path):
        try:
            raw_path.unlink()
        except OSError:
            pass
    else:
        raw_path.replace(overlay_path)
        print("提示：没有找到 ffmpeg，骨架视频仍为 MPEG-4 编码，部分浏览器可能无法播放。")

    verdict = analyze_metrics_sequence(per_frame_metrics, view=view, thresholds=thresholds, exercise=exercise)

    # 自由动作：几何层负责次数与量程，大模型负责动作质量判定（覆盖 verdict 的问题与总结）
    overall_issues: Optional[List[Dict[str, object]]] = None
    if exercise == "custom" and callable(custom_judge):
        judge_data = _metrics_summary(per_frame_metrics)
        judge_data["exercise"] = exercise_label
        judge_data["rep_count"] = len(reps)
        try:
            judge = custom_judge(exercise_label, judge_data)
            if judge is None:
                # 没配大模型 / 调用失败：只保留几何量程，不妄下"规范"结论
                overall_issues = None
                verdict["summary"] = "已统计完成次数与关节角度范围，但未配置大模型，暂无法判定动作质量。"
            else:
                overall_issues = judge.get("issues") or []
                if judge.get("summary"):
                    verdict["summary"] = judge["summary"]
                if overall_issues:
                    verdict["issues"] = overall_issues
            verdict["metrics"] = judge_data
        except Exception as exc:  # noqa: BLE001
            print(f"提示：自由动作大模型判定失败，退回几何指标：{exc}")
            overall_issues = None
            verdict["summary"] = "已统计完成次数与关节角度范围，但动作质量判定失败。"
            verdict["metrics"] = judge_data

    # 给视频加语音播报：一句「整体汇总播报」，合成失败会自动退回静音视频
    narration_ok = False
    if voice:
        try:
            from voice import add_narration

            summary_text = _narration_text(
                reps, per_rep_issue_list, exercise, posture_note,
                label=exercise_label, overall_issues=overall_issues,
            )
            narration_ok = add_narration(overlay_path, summary_text, sampled / writer_fps)
        except Exception:  # noqa: BLE001
            narration_ok = False
    if not narration_ok:
        print("提示：语音播报未启用（未检测到可用的语音合成）。")

    error_frames = [
        {
            "rep": item["rep"],
            "time_seconds": round(item["bottom_index"] / writer_fps, 2),
            "label": " · ".join(issue.get("short", issue.get("name", "")) for issue in item["issues"]),
        }
        for item in per_rep_issue_list
        if item.get("issues")
    ]

    report: Dict[str, object] = {
        "video": str(video_path),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "view": view,
        "exercise": exercise,
        "exercise_label": exercise_label,
        "sample_fps": sample_fps,
        "sampled_frames": sampled,
        "source_fps": round(source_fps, 2),
        "resolution": [width, height],
        "output_resolution": [out_width, height],
        "thresholds": thresholds,
        "verdict": verdict,
        "frames": frame_records,
        "reps": reps,
        "rep_count": len(reps),
        "per_rep_issues": per_rep_issue_list,
        "error_frames": error_frames,
        "narration": narration_ok,
        "artifacts": {
            "overlay_video": str(overlay_path),
            "report_json": str(json_path),
            "report_card": str(card_path),
        },
        "disclaimer": "结果仅用于训练动作参考，不构成医疗或康复诊断。",
    }

    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    report["video_display"] = display_name or video_path.name
    render_report_card(verdict, display_name or video_path.name, card_path, view, exercise)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="动作自检（深蹲 / 卧推 / 硬拉 / 俯卧撑）")
    parser.add_argument("video", help="视频文件路径")
    parser.add_argument("--exercise", choices=list(EXERCISES.keys()), default="squat", help="动作类型，默认 squat 深蹲")
    parser.add_argument("--view", choices=["side", "front"], default="side", help="拍摄角度：side 侧面，front 正面")
    parser.add_argument("--out", default=None, help="输出目录，默认是程序目录下的输出结果")
    parser.add_argument("--model", default=None, help="姿态模型路径，默认使用 模型/pose_landmarker_lite.task")
    parser.add_argument("--sample-fps", type=float, default=4.0, help="每秒抽帧数，默认 4")
    parser.add_argument("--max-seconds", type=float, default=20.0, help="最多分析多少秒，默认 20")
    parser.add_argument("--json-only", action="store_true", help="只输出 JSON，不写文件")
    parser.add_argument("--no-voice", action="store_true", help="不加语音播报")
    args = parser.parse_args()

    video_path = Path(args.video).expanduser().resolve()
    if not video_path.exists():
        print(f"找不到视频文件：{video_path}")
        return 2

    base_dir = Path(__file__).resolve().parent
    model_path = Path(args.model).expanduser().resolve() if args.model else base_dir.parent / "模型" / "pose_landmarker_lite.task"
    out_dir = Path(args.out).expanduser().resolve() if args.out else base_dir.parent / "输出结果"

    started = time.time()
    report = analyze_video(
        video_path=video_path,
        model_path=model_path,
        view=args.view,
        out_dir=out_dir,
        sample_fps=args.sample_fps,
        max_seconds=args.max_seconds,
        exercise=args.exercise,
        voice=not args.no_voice,
    )
    elapsed = time.time() - started

    verdict = report["verdict"]
    print("=" * 52)
    print(f"动作：{report['exercise_label']}　拍摄角度：{'正面' if args.view == 'front' else '侧面'}")
    print(f"视频：{video_path.name}")
    print(f"结论：{verdict['summary']}")
    for item in verdict.get("issues", []):
        print(f"  问题：{item['name']}（{item['severity']}）")
        print(f"    依据：{item['evidence']}")
        for fix in item.get("fixes", []):
            print(f"    建议：{fix['name']}，{fix['how']}")
    print("-" * 52)
    if not args.json_only:
        for name, path in report["artifacts"].items():
            print(f"{name}: {path}")
    print(f"分析用时：{elapsed:.1f} 秒")
    print("=" * 52)
    sys.stdout.flush()
    sys.stderr.flush()
    # mediapipe 1.0.1 的 close() 在后台线程会拖约 40 秒，结果文件此刻已全部写完，
    # 直接结束进程，不必等资源回收。
    os._exit(0)


if __name__ == "__main__":
    sys.exit(main())
