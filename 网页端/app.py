"""校园健身智能体 · 网站后端。

启动：
    ..\\.venv\\Scripts\\python.exe -m uvicorn app:app --host 0.0.0.0 --port 8000
浏览器打开 http://127.0.0.1:8000
"""

from __future__ import annotations

import secrets
import sys
import threading
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import Cookie, Depends, FastAPI, File, Form, HTTPException, UploadFile, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

WEB_DIR = Path(__file__).resolve().parent
ROOT_DIR = WEB_DIR.parent
STATIC_DIR = WEB_DIR / "static"
ANALYZER_DIR = ROOT_DIR / "动作自检"
RESULT_DIR = ROOT_DIR / "输出结果"
UPLOAD_DIR = RESULT_DIR / "上传视频"
MODEL_PATH = ROOT_DIR / "模型" / "pose_landmarker_lite.task"
FRONTEND_DIST = ROOT_DIR / "前端界面"

sys.path.insert(0, str(ANALYZER_DIR))
sys.path.insert(0, str(WEB_DIR))

import db  # noqa: E402
import logic  # noqa: E402
import posture_rules  # noqa: E402

sys.path.insert(0, str(ANALYZER_DIR))
from pose_rules import EXERCISES  # noqa: E402

EXERCISE_IDS = list(EXERCISES.keys())

RESULT_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
db.init_db()
logic.ensure_config_file()

app = FastAPI(title="校园健身智能体")

JOBS: Dict[str, Dict[str, Any]] = {}
JOBS_LOCK = threading.Lock()

SESSIONS: Dict[str, int] = {}
SESSIONS_LOCK = threading.Lock()


class ProfilePayload(BaseModel):
    name: Optional[str] = None
    gender: Optional[str] = None
    age: Optional[int] = None
    height_cm: Optional[float] = None
    weight_kg: Optional[float] = None
    goal: Optional[str] = None
    weekly_days: Optional[int] = None
    minutes_per_session: Optional[int] = None
    meal_budget: Optional[float] = None


class RecordPayload(BaseModel):
    exercise: str
    weight_kg: float = 0
    sets: int
    reps: int
    rpe: Optional[float] = None
    completed: bool = True
    feeling: str = ""
    trained_on: Optional[str] = None


class MealPayload(BaseModel):
    food_id: int
    servings: float = 1
    meal_type: str = "午餐"


class ParsePayload(BaseModel):
    text: str


class SuggestPayload(BaseModel):
    budget: Optional[float] = None


class LlmConfigPayload(BaseModel):
    provider: str
    key: str
    model: Optional[str] = None


class RestoreRecordPayload(BaseModel):
    exercise_id: Optional[int] = None
    weight_kg: float = 0
    sets: int = 0
    reps: int = 0
    rpe: Optional[float] = None
    completed: bool = True
    feeling: str = ""
    trained_on: Optional[str] = None
    created_at: Optional[str] = None


# ------------------------------------------------------------------ 认证


class AuthPayload(BaseModel):
    username: str
    password: str


def _safe_user(user: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    return {key: user.get(key) for key in ("id", "username", "role", "name")}


def current_user(session: Optional[str] = Cookie(None)) -> Dict[str, Any]:
    with SESSIONS_LOCK:
        user_id = SESSIONS.get(session) if session else None
    user = db.get_user(user_id) if user_id else None
    if user is None:
        raise HTTPException(status_code=401, detail="未登录")
    return user


def current_admin(user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="仅管理员可用")
    return user


def _set_session(response: Response, user_id: int) -> None:
    token = secrets.token_urlsafe(32)
    with SESSIONS_LOCK:
        SESSIONS[token] = user_id
    response.set_cookie("session", token, httponly=True, samesite="lax", max_age=30 * 86400)


@app.post("/api/auth/register")
async def auth_register(payload: AuthPayload, response: Response) -> Dict[str, Any]:
    username = payload.username.strip()
    if not username or not payload.password:
        raise HTTPException(status_code=400, detail="用户名和密码不能为空")
    try:
        user_id = db.create_user(username, payload.password)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    _set_session(response, user_id)
    return {"user": _safe_user(db.get_user(user_id))}


@app.post("/api/auth/login")
async def auth_login(payload: AuthPayload, response: Response) -> Dict[str, Any]:
    user = db.authenticate(payload.username.strip(), payload.password)
    if user is None:
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    _set_session(response, user["id"])
    return {"user": _safe_user(user)}


@app.post("/api/auth/logout")
async def auth_logout(response: Response, session: Optional[str] = Cookie(None)) -> Dict[str, Any]:
    if session:
        with SESSIONS_LOCK:
            SESSIONS.pop(session, None)
    response.delete_cookie("session")
    return {"ok": True}


@app.get("/api/auth/me")
async def auth_me(user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    return {"user": _safe_user(user)}


# ------------------------------------------------------------------ 健康检查


@app.get("/api/health")
async def health() -> Dict[str, Any]:
    settings = logic.llm_settings()
    return {
        "ok": True,
        "model": MODEL_PATH.exists(),
        "llm": bool(settings),
        "llm_provider": (settings or {}).get("name"),
        "llm_model": (settings or {}).get("model"),
        "database": str(db.DB_PATH),
    }


# ------------------------------------------------------------------ 大模型接入


@app.get("/api/llm/config")
async def llm_config(admin: Dict[str, Any] = Depends(current_admin)) -> Dict[str, Any]:
    return logic.llm_status()


@app.post("/api/llm/config")
async def llm_config_save(payload: LlmConfigPayload, admin: Dict[str, Any] = Depends(current_admin)) -> Dict[str, Any]:
    try:
        logic.save_config(payload.provider, payload.key, payload.model or "")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return logic.llm_status()


@app.post("/api/llm/test")
async def llm_test(admin: Dict[str, Any] = Depends(current_admin)) -> Dict[str, Any]:
    return logic.llm_test()


# ------------------------------------------------------------------ 档案


@app.get("/api/profile")
async def get_profile(user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    return db.get_profile(user["id"])


@app.post("/api/profile")
async def post_profile(payload: ProfilePayload, user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    data = {key: value for key, value in payload.model_dump().items() if value is not None}
    profile = db.update_profile(data, user["id"])
    db.add_plan_change(
        user_id=user["id"],
        reason="身体数据或目标发生变化，重新计算热量与蛋白质目标",
        before_text="按旧的体重与目标计算",
        after_text=f"按 {profile['weight_kg']} 公斤、目标{profile['goal']}重新计算",
        trigger="profile",
    )
    return {"profile": profile, "targets": logic.nutrition_targets(profile)}


# ------------------------------------------------------------------ 训练记录


@app.get("/api/exercises")
async def get_exercises() -> List[Dict[str, Any]]:
    return db.list_exercises()


@app.get("/api/pose/exercises")
async def get_pose_exercises() -> List[Dict[str, Any]]:
    """动作自检支持的动作清单，供页面渲染下拉菜单。"""
    items = [{"id": pid, **meta} for pid, meta in EXERCISES.items()]
    items.append(
        {
            "id": "custom",
            "label": "自由动作",
            "name": "自由动作",
            "desc": "输入任意动作名称，由大模型结合几何量程做通用判定",
            "custom": True,
        }
    )
    return items


@app.post("/api/records/parse")
async def parse_record(payload: ParsePayload, user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    """解析训练记录：优先交给大模型，没配 Key 或解析失败时退回规则。"""
    return logic.parse_record(payload.text)


@app.post("/api/records")
async def create_record(payload: RecordPayload, user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    exercise = db.find_exercise(payload.exercise)
    if exercise is None:
        raise HTTPException(status_code=400, detail=f"动作库里没有「{payload.exercise}」")
    history = db.history_for_exercise(user["id"], exercise["id"], limit=3)
    decision = logic.decide_progression(
        exercise,
        history,
        {
            "weight_kg": payload.weight_kg,
            "sets": payload.sets,
            "reps": payload.reps,
            "rpe": payload.rpe,
            "completed": payload.completed,
        },
    )
    db.add_set_record(
        user_id=user["id"],
        exercise_id=exercise["id"],
        weight_kg=payload.weight_kg,
        sets=payload.sets,
        reps=payload.reps,
        rpe=payload.rpe,
        completed=payload.completed,
        feeling=payload.feeling,
        trained_on=payload.trained_on,
    )
    if decision["action"] in ("increase", "deload"):
        db.add_plan_change(
            user_id=user["id"],
            reason=decision["reason"],
            before_text=f"{exercise['name']} {payload.weight_kg} 公斤 {payload.sets} 组 {payload.reps} 次",
            after_text=f"{exercise['name']} {decision['next_weight_kg']} 公斤 {payload.sets} 组 {payload.reps} 次",
            trigger="set_record",
        )
    return {"decision": decision, "exercise": exercise, "history": history}


@app.get("/api/records")
async def list_records(user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    return {"records": db.recent_records(user["id"], 20), "days_since_last": db.days_since_last_training(user["id"])}


@app.delete("/api/records/{record_id}")
async def delete_record(record_id: int, user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    """删除一条训练记录，返回被删的数据供撤销使用。"""
    deleted = db.delete_set_record(record_id, user["id"])
    if deleted is None:
        raise HTTPException(status_code=404, detail="找不到这条记录")
    return {"deleted": deleted, "records": db.recent_records(user["id"], 20)}


@app.post("/api/records/restore")
async def restore_record(payload: RestoreRecordPayload, user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    record_id = db.restore_set_record(payload.model_dump(), user["id"])
    return {"restored_id": record_id, "records": db.recent_records(user["id"], 20)}


@app.get("/api/records/calendar")
async def records_calendar(year: int, month: int, user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    """某个月的训练日历：每天的动作数、组数、次数与容量。"""
    if not 1 <= month <= 12:
        raise HTTPException(status_code=400, detail="月份要在 1 到 12 之间")
    start = date(year, month, 1)
    end = date(year + (month == 12), (month % 12) + 1, 1) - timedelta(days=1)
    records = db.records_between(user["id"], start.isoformat(), end.isoformat())
    days = logic.build_calendar(records)
    return {
        "year": year,
        "month": month,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "days": days,
        "train_days": len(days),
        "month_volume": round(sum(item["volume"] for item in days.values()), 1),
        "today": date.today().isoformat(),
    }


@app.get("/api/records/day")
async def records_of_day(day: str, user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    """某一天的训练明细，点日历上的某天时调用。"""
    try:
        target = date.fromisoformat(day)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="日期格式应为 YYYY-MM-DD") from exc
    records = db.records_between(user["id"], target.isoformat(), target.isoformat())
    for item in records:
        item["volume"] = logic.record_volume(item)
        item["total_reps"] = logic.record_reps(item)
    return {"date": target.isoformat(), "summary": logic.summarize_day(records), "records": records}


@app.post("/api/coach/advice")
async def coach_advice(user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    """根据最近一个月的训练记录生成新的训练建议。"""
    today = date.today()
    start = today - timedelta(days=29)
    records = db.records_between(user["id"], start.isoformat(), today.isoformat())
    result = logic.training_advice(records, today)
    result["range"] = {"start": start.isoformat(), "end": today.isoformat(), "records": len(records)}
    return result


# ------------------------------------------------------------------ 食堂


@app.get("/api/foods")
async def list_foods(q: str = "") -> List[Dict[str, Any]]:
    return db.search_foods(q, limit=80)


@app.get("/api/nutrition")
async def nutrition(user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    profile = db.get_profile(user["id"])
    meals = db.meals_on(user["id"])
    summary = logic.nutrition_summary(profile, meals)
    return summary


@app.post("/api/meals")
async def create_meal(payload: MealPayload, user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    food = db.get_food(payload.food_id)
    if food is None:
        raise HTTPException(status_code=404, detail="没有这个菜品")
    db.add_meal(user["id"], food, payload.servings, payload.meal_type)
    profile = db.get_profile(user["id"])
    return logic.nutrition_summary(profile, db.meals_on(user["id"]))


@app.delete("/api/meals/{meal_id}")
async def remove_meal(meal_id: int, user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    db.delete_meal(meal_id, user["id"])
    profile = db.get_profile(user["id"])
    return logic.nutrition_summary(profile, db.meals_on(user["id"]))


@app.post("/api/nutrition/suggest")
async def nutrition_suggest(payload: SuggestPayload, user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    profile = db.get_profile(user["id"])
    summary = logic.nutrition_summary(profile, db.meals_on(user["id"]))
    budget = payload.budget if payload.budget is not None else float(profile.get("meal_budget") or 15)
    result = logic.suggest_meals(summary["gap"]["protein"], summary["gap"]["kcal"], budget)
    result["gap"] = summary["gap"]
    return result


@app.get("/api/nutrition/vision")
async def nutrition_vision_status() -> Dict[str, Any]:
    """拍照识别是否可用（由服务器统一接入的视觉模型决定，用户不需要自己配置）。"""
    return logic.vision_status()


@app.post("/api/nutrition/photo")
async def nutrition_photo(file: UploadFile = File(...), user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    """拍照识别菜品：视觉模型只负责认菜与估份量，热量和蛋白质由菜品库计算。"""
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="没有收到图片")
    data_url = logic.image_to_data_url(raw)
    foods = db.search_foods("", limit=200)
    result = logic.analyze_food_photo(data_url, foods)
    if not result.get("ok"):
        raise HTTPException(status_code=503, detail=result.get("error") or "识别失败")
    return result


# ------------------------------------------------------------------ 动作自检


def run_pose_job(
    job_id: str,
    video_path: Path,
    view: str,
    user_id: int,
    display_name: str = "",
    exercise: str = "squat",
    custom_name: str = "",
) -> None:
    from pose_rules import EXERCISES
    from squat_analyzer import analyze_video

    with JOBS_LOCK:
        JOBS[job_id]["status"] = "running"
    try:
        # 先查体态档案，生成体态联动提示，随语音播报一起给到分析流程
        latest_posture = db.list_posture_assessments(user_id, limit=1)
        posture_note = logic.posture_pose_note(
            latest_posture[0]["issues"] if latest_posture else [], exercise
        )
        # 自由动作：把几何量程交给大模型做通用动作质量判定
        custom_judge = None
        if exercise == "custom":
            custom_judge = logic.judge_free_exercise
        report = analyze_video(
            video_path=video_path,
            model_path=MODEL_PATH,
            view=view,
            out_dir=RESULT_DIR,
            sample_fps=4.0,
            max_seconds=20.0,
            display_name=display_name,
            exercise=exercise,
            posture_note=posture_note,
            custom_name=custom_name,
            custom_judge=custom_judge,
        )
        verdict = report["verdict"]
        advice, advice_source = logic.coach_explain_for_pose(verdict)
        db.save_pose_analysis(
            user_id=user_id,
            video_name=video_path.name,
            view=view,
            verdict=verdict,
            overlay_path=str(report["artifacts"]["overlay_video"]),
            card_path=str(report["artifacts"]["report_card"]),
            exercise=exercise,
        )
        if verdict.get("issues"):
            first = verdict["issues"][0]
            db.add_plan_change(
                user_id=user_id,
                reason=f"动作自检检出{first['name']}：{first['evidence']}",
                before_text="原计划：按原重量继续加量",
                after_text=f"调整计划：下一次训练先加入{first.get('fixes', [{}])[0].get('name', '纠正练习')}，并降低该动作重量",
                trigger="pose_analysis",
            )
        with JOBS_LOCK:
            JOBS[job_id].update(
                {
                    "status": "done",
                    "summary": verdict.get("summary"),
                    "issues": verdict.get("issues", []),
                    "metrics": verdict.get("metrics", {}),
                    "advice": advice,
                    "advice_source": advice_source,
                    "exercise": exercise,
                    "exercise_label": report.get("exercise_label") or EXERCISES.get(exercise, {}).get("label", exercise),
                    "per_rep_issues": report.get("per_rep_issues", []),
                    "error_frames": report.get("error_frames", []),
                    "rep_count": report.get("rep_count", 0),
                    "posture_note": posture_note,
                    "overlay_video": f"/outputs/{Path(report['artifacts']['overlay_video']).name}",
                    "report_card": f"/outputs/{Path(report['artifacts']['report_card']).name}",
                    "report_json": f"/outputs/{Path(report['artifacts']['report_json']).name}",
                }
            )
    except Exception as exc:  # noqa: BLE001
        with JOBS_LOCK:
            JOBS[job_id].update({"status": "failed", "error": f"{type(exc).__name__}: {exc}"})


@app.post("/api/pose/analyze")
async def pose_analyze(
    file: UploadFile = File(...),
    view: str = Form("side"),
    exercise: str = Form("squat"),
    custom_name: str = Form(""),
    user: Dict[str, Any] = Depends(current_user),
) -> Dict[str, Any]:
    if view not in ("side", "front"):
        raise HTTPException(status_code=400, detail="view 只能是 side 或 front")
    if exercise not in EXERCISE_IDS and exercise != "custom":
        raise HTTPException(status_code=400, detail=f"暂不支持的动作：{exercise}")
    custom_name = custom_name.strip()
    if exercise == "custom":
        if not custom_name:
            raise HTTPException(status_code=400, detail="自由动作需要填写动作名称")
        if len(custom_name) > 24:
            custom_name = custom_name[:24]
    suffix = Path(file.filename or "video.mp4").suffix or ".mp4"
    job_id = uuid.uuid4().hex[:12]
    video_path = UPLOAD_DIR / f"{job_id}{suffix}"
    video_path.write_bytes(await file.read())
    with JOBS_LOCK:
        JOBS[job_id] = {
            "job_id": job_id,
            "user_id": user["id"],
            "status": "queued",
            "view": view,
            "exercise": exercise,
            "custom_name": custom_name,
            "video": video_path.name,
            "display_name": file.filename,
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }
    threading.Thread(
        target=run_pose_job,
        args=(job_id, video_path, view, user["id"], file.filename or "", exercise, custom_name),
        daemon=True,
    ).start()
    return {"job_id": job_id, "status": "queued"}


@app.get("/api/pose/jobs/{job_id}")
async def pose_job(job_id: str, user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if job is None or job.get("user_id") != user["id"]:
        raise HTTPException(status_code=404, detail="没有这个任务")
    return job


@app.get("/api/pose/history")
async def pose_history(user: Dict[str, Any] = Depends(current_user)) -> List[Dict[str, Any]]:
    items = db.list_pose_analyses(user["id"], limit=5)
    for item in items:
        for key in ("issues_json", "metrics_json"):
            if item.get(key):
                try:
                    import json

                    item[key.replace("_json", "")] = json.loads(item.pop(key))
                except Exception:  # noqa: BLE001
                    pass
    return items


# ------------------------------------------------------------------ 体态评估


@app.post("/api/posture/analyze")
async def posture_analyze(
    front: UploadFile = File(...),
    side: UploadFile = File(...),
    back: UploadFile = File(...),
    user: Dict[str, Any] = Depends(current_user),
) -> Dict[str, Any]:
    """上传正/侧/背三张照片，视觉模型评估体态，规则引擎生成矫正方案。"""
    raw_front = await front.read()
    raw_side = await side.read()
    raw_back = await back.read()
    if not raw_front or not raw_side or not raw_back:
        raise HTTPException(status_code=400, detail="正/侧/背三张照片都要上传")

    result = logic.analyze_posture_photos(
        logic.image_to_data_url(raw_front),
        logic.image_to_data_url(raw_side),
        logic.image_to_data_url(raw_back),
    )
    if not result.get("ok"):
        raise HTTPException(status_code=503, detail=result.get("error") or "体态识别失败")

    issues = result["issues"]
    angles = result["angles"]
    risk_score = result["risk_score"]
    plan = posture_rules.build_plan(issues)
    report_id = db.save_posture_assessment(user["id"], issues, angles, risk_score)

    return {
        "report_id": report_id,
        "issues": issues,
        "angles": angles,
        "risk_score": risk_score,
        "provider": result.get("provider"),
        "model": result.get("model"),
        "plan": plan,
    }


@app.get("/api/posture/history")
async def posture_history(user: Dict[str, Any] = Depends(current_user)) -> List[Dict[str, Any]]:
    return db.list_posture_assessments(user["id"], limit=5)


# ------------------------------------------------------------------ 督导与计划


@app.post("/api/nudge/run")
async def run_nudge(user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    days = db.days_since_last_training(user["id"])
    last = db.last_training_date(user["id"])
    result = logic.nudge_message(days, last)
    db.add_nudge(user["id"], result["kind"], result["message"])
    if result.get("downgrade"):
        db.add_plan_change(
            user_id=user["id"],
            reason=result.get("reason", result["message"]),
            before_text="原计划：正常强度训练",
            after_text=result["downgrade"],
            trigger="nudge",
        )
    return result


@app.get("/api/nudges")
async def list_nudges(user: Dict[str, Any] = Depends(current_user)) -> List[Dict[str, Any]]:
    return db.list_nudges(user["id"], limit=5)


@app.get("/api/plan/changes")
async def plan_changes(user: Dict[str, Any] = Depends(current_user)) -> List[Dict[str, Any]]:
    return db.list_plan_changes(user["id"], limit=10)


@app.get("/api/plan/today")
async def plan_today(user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    profile = db.get_profile(user["id"])
    records = db.recent_records(user["id"], limit=30)
    trained_days = {item["trained_on"] for item in records}
    today = date.today().isoformat()
    weeks_target = int(profile.get("weekly_days") or 3)
    return {
        "date": today,
        "weekly_days": weeks_target,
        "trained_today": today in trained_days,
        "trained_days_last_7": len({d for d in trained_days if (date.today() - date.fromisoformat(d)).days < 7}),
        "minutes_per_session": profile.get("minutes_per_session"),
        "suggestion": "今天还没有训练安排。可以先做一次动作自检，再按计划训练。",
    }


# ------------------------------------------------------------------ 静态资源

app.mount("/outputs", StaticFiles(directory=str(RESULT_DIR)), name="outputs")

if STATIC_DIR.exists():
    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
elif FRONTEND_DIST.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIST), html=True), name="frontend")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
