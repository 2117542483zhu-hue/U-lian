"""给网站用的接口：上传视频，后台分析，轮询拿结果。

启动：
    安装网页端依赖：pip install -r ../requirements-网页端.txt
    运行：python -m uvicorn api_pose:app --host 0.0.0.0 --port 8000

接口：
    POST /api/pose/analyze   上传视频（form-data，字段名 file，可选参数 view=side|front）
    GET  /api/pose/jobs/{id} 查询进度与结果
"""

from __future__ import annotations

import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.staticfiles import StaticFiles

from squat_analyzer import analyze_video

BASE_DIR = Path(__file__).resolve().parent
ROOT_DIR = BASE_DIR.parent
UPLOAD_DIR = ROOT_DIR / "输出结果" / "上传视频"
RESULT_DIR = ROOT_DIR / "输出结果"
MODEL_PATH = ROOT_DIR / "模型" / "pose_landmarker_lite.task"

UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
RESULT_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="动作自检接口")
app.mount("/outputs", StaticFiles(directory=str(RESULT_DIR)), name="outputs")

JOBS: Dict[str, Dict[str, object]] = {}
LOCK = threading.Lock()


def run_job(job_id: str, video_path: Path, view: str) -> None:
    with LOCK:
        JOBS[job_id]["status"] = "running"
    try:
        report = analyze_video(
            video_path=video_path,
            model_path=MODEL_PATH,
            view=view,
            out_dir=RESULT_DIR,
            sample_fps=4.0,
            max_seconds=20.0,
        )
        verdict = report["verdict"]
        with LOCK:
            JOBS[job_id].update(
                {
                    "status": "done",
                    "finished_at": datetime.now().isoformat(timespec="seconds"),
                    "summary": verdict.get("summary"),
                    "issues": verdict.get("issues", []),
                    "metrics": verdict.get("metrics", {}),
                    "artifacts": {
                        "overlay_video": f"/outputs/{Path(str(report['artifacts']['overlay_video'])).name}",
                        "report_card": f"/outputs/{Path(str(report['artifacts']['report_card'])).name}",
                    },
                }
            )
    except Exception as exc:  # noqa: BLE001
        with LOCK:
            JOBS[job_id].update({"status": "failed", "error": f"{type(exc).__name__}: {exc}"})


@app.post("/api/pose/analyze")
async def analyze(file: UploadFile = File(...), view: str = Form("side")):
    if view not in ("side", "front"):
        raise HTTPException(status_code=400, detail="view 只能是 side 或 front")
    suffix = Path(file.filename or "video.mp4").suffix or ".mp4"
    job_id = uuid.uuid4().hex[:12]
    video_path = UPLOAD_DIR / f"{job_id}{suffix}"
    video_path.write_bytes(await file.read())

    with LOCK:
        JOBS[job_id] = {
            "job_id": job_id,
            "status": "queued",
            "view": view,
            "video": video_path.name,
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }
    threading.Thread(target=run_job, args=(job_id, video_path, view), daemon=True).start()
    return {"job_id": job_id, "status": "queued"}


@app.get("/api/pose/jobs/{job_id}")
async def get_job(job_id: str):
    with LOCK:
        job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="没有这个任务")
    return job


@app.get("/api/health")
async def health():
    return {"ok": True, "model": MODEL_PATH.exists()}
