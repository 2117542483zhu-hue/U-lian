"""下载姿态模型文件到 模型 目录。国内网络直连较慢，这里做了重试。"""

import ssl
import sys
import urllib.request
from pathlib import Path

MODELS = [
    (
        "pose_landmarker_lite.task",
        "https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_lite/float16/latest/pose_landmarker_lite.task",
    ),
    (
        "pose_landmarker_full.task",
        "https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_full/float16/latest/pose_landmarker_full.task",
    ),
]

TARGET_DIR = Path(__file__).resolve().parent.parent / "模型"


def download(url: str, target: Path) -> bool:
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    data = urllib.request.urlopen(request, timeout=120, context=context).read()
    if len(data) < 100000:
        raise RuntimeError(f"下载内容过小，可能失败：{len(data)} 字节")
    target.write_bytes(data)
    return True


def main() -> int:
    TARGET_DIR.mkdir(parents=True, exist_ok=True)
    wanted = sys.argv[1:] or ["pose_landmarker_lite.task"]
    for name in wanted:
        url = next((item[1] for item in MODELS if item[0] == name), None)
        if url is None:
            print(f"未知模型名称：{name}")
            continue
        target = TARGET_DIR / name
        if target.exists() and target.stat().st_size > 100000:
            print(f"已存在，跳过：{target}（{target.stat().st_size} 字节）")
            continue
        try:
            download(url, target)
            print(f"下载完成：{target}（{target.stat().st_size} 字节）")
        except Exception as exc:  # noqa: BLE001
            print(f"下载失败：{name}，{type(exc).__name__} {exc}")
            print("可以手动下载后放到 模型 目录，文件名保持一致即可。")
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
