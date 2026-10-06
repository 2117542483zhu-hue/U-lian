"""数据库：建表、初始化数据、常用读写。"""

from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from seed_data import EXERCISES, FOODS

ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "数据"
DB_PATH = DATA_DIR / "fitness.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'user',
    name TEXT NOT NULL DEFAULT '同学',
    gender TEXT NOT NULL DEFAULT '男',
    age INTEGER NOT NULL DEFAULT 20,
    height_cm REAL NOT NULL DEFAULT 175,
    weight_kg REAL NOT NULL DEFAULT 70,
    goal TEXT NOT NULL DEFAULT '增肌',
    weekly_days INTEGER NOT NULL DEFAULT 3,
    minutes_per_session INTEGER NOT NULL DEFAULT 60,
    meal_budget REAL NOT NULL DEFAULT 15,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS exercises (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    muscle TEXT,
    equipment TEXT,
    cues TEXT
);

CREATE TABLE IF NOT EXISTS set_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    exercise_id INTEGER NOT NULL,
    weight_kg REAL NOT NULL,
    sets INTEGER NOT NULL,
    reps INTEGER NOT NULL,
    rpe REAL,
    completed INTEGER NOT NULL DEFAULT 1,
    feeling TEXT,
    trained_on TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pose_analyses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    video_name TEXT,
    exercise TEXT DEFAULT 'squat',
    view TEXT,
    status TEXT,
    summary TEXT,
    issues_json TEXT,
    metrics_json TEXT,
    overlay_path TEXT,
    card_path TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS foods (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    canteen TEXT,
    window TEXT,
    price REAL NOT NULL DEFAULT 0,
    kcal REAL NOT NULL DEFAULT 0,
    protein REAL NOT NULL DEFAULT 0,
    carb REAL NOT NULL DEFAULT 0,
    fat REAL NOT NULL DEFAULT 0,
    serving TEXT,
    source TEXT DEFAULT '估算值'
);

CREATE TABLE IF NOT EXISTS meals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    eaten_on TEXT NOT NULL,
    meal_type TEXT,
    food_id INTEGER,
    food_name TEXT,
    servings REAL NOT NULL DEFAULT 1,
    kcal REAL NOT NULL DEFAULT 0,
    protein REAL NOT NULL DEFAULT 0,
    carb REAL NOT NULL DEFAULT 0,
    fat REAL NOT NULL DEFAULT 0,
    price REAL NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS plan_changes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    plan_version INTEGER NOT NULL,
    before_text TEXT,
    after_text TEXT,
    reason TEXT,
    trigger TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS nudges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    kind TEXT,
    message TEXT,
    status TEXT DEFAULT 'new',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS posture_assessments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    risk_score INTEGER NOT NULL DEFAULT 0,
    issues_json TEXT,
    angles_json TEXT,
    created_at TEXT NOT NULL
);
"""


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def init_db() -> None:
    with connect() as connection:
        connection.executescript(SCHEMA)
        # 老版本数据库补上动作类型字段
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(pose_analyses)")}
        if "exercise" not in columns:
            connection.execute("ALTER TABLE pose_analyses ADD COLUMN exercise TEXT DEFAULT 'squat'")
        # 一次性迁移：老版 users 表没有用户名/密码/角色，且数据挂在单一用户下 → 重建并清空
        user_columns = {row["name"] for row in connection.execute("PRAGMA table_info(users)")}
        if "username" not in user_columns:
            connection.execute("DROP TABLE users")
            connection.executescript(SCHEMA)
            for table in ("set_records", "pose_analyses", "meals", "plan_changes", "nudges", "posture_assessments"):
                connection.execute(f"DELETE FROM {table}")
        # 确保至少存在一个管理员账号
        admin = connection.execute("SELECT id FROM users WHERE role = 'admin' LIMIT 1").fetchone()
        if admin is None:
            connection.execute(
                "INSERT INTO users (username, password_hash, role, name, gender, age, height_cm, weight_kg, goal, weekly_days, minutes_per_session, meal_budget, updated_at)"
                " VALUES (?, ?, 'admin', '管理员', '男', 20, 175, 70, '增肌', 3, 60, 15, ?)",
                ("admin", hash_password("admin123"), datetime.now().isoformat(timespec="seconds")),
            )
        food_count = connection.execute("SELECT COUNT(*) AS n FROM foods").fetchone()["n"]
        if food_count == 0:
            connection.executemany(
                "INSERT INTO foods (name, canteen, window, price, kcal, protein, carb, fat, serving, source)"
                " VALUES (:name, :canteen, :window, :price, :kcal, :protein, :carb, :fat, :serving, '估算值')",
                FOODS,
            )
        exercise_count = connection.execute("SELECT COUNT(*) AS n FROM exercises").fetchone()["n"]
        if exercise_count == 0:
            connection.executemany(
                "INSERT INTO exercises (name, muscle, equipment, cues) VALUES (:name, :muscle, :equipment, :cues)",
                EXERCISES,
            )
        connection.commit()


def rows_to_dicts(rows: Iterable[sqlite3.Row]) -> List[Dict[str, Any]]:
    return [dict(row) for row in rows]


# ------------------------------------------------------------------ 账号与认证


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), 120_000).hex()
    return f"{salt}${digest}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt, digest = stored.split("$", 1)
    except ValueError:
        return False
    candidate = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), 120_000).hex()
    return secrets.compare_digest(candidate, digest)


def _safe_user(row: Optional[sqlite3.Row]) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    data = dict(row)
    data.pop("password_hash", None)
    return data


def create_user(username: str, password: str, role: str = "user", name: str = "同学") -> int:
    with connect() as connection:
        exists = connection.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
        if exists is not None:
            raise ValueError("用户名已被注册")
        cursor = connection.execute(
            "INSERT INTO users (username, password_hash, role, name, gender, age, height_cm, weight_kg, goal, weekly_days, minutes_per_session, meal_budget, updated_at)"
            " VALUES (?, ?, ?, ?, '男', 20, 175, 70, '增肌', 3, 60, 15, ?)",
            (username, hash_password(password), role, name, datetime.now().isoformat(timespec="seconds")),
        )
        connection.commit()
        return int(cursor.lastrowid)


def get_user_by_username(username: str) -> Optional[Dict[str, Any]]:
    with connect() as connection:
        row = connection.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    return _safe_user(row)


def get_user(user_id: int) -> Optional[Dict[str, Any]]:
    with connect() as connection:
        row = connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return _safe_user(row)


def authenticate(username: str, password: str) -> Optional[Dict[str, Any]]:
    with connect() as connection:
        row = connection.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    if row is None or not verify_password(password, row["password_hash"]):
        return None
    return _safe_user(row)


def is_admin(user_id: int) -> bool:
    with connect() as connection:
        row = connection.execute("SELECT role FROM users WHERE id = ?", (user_id,)).fetchone()
    return bool(row and row["role"] == "admin")


def get_profile(user_id: int = 1) -> Dict[str, Any]:
    with connect() as connection:
        row = connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    data = dict(row) if row else {}
    data.pop("password_hash", None)
    return data


def update_profile(payload: Dict[str, Any], user_id: int = 1) -> Dict[str, Any]:
    fields = ["name", "gender", "age", "height_cm", "weight_kg", "goal", "weekly_days", "minutes_per_session", "meal_budget"]
    updates = {key: payload[key] for key in fields if key in payload and payload[key] is not None}
    if updates:
        assignments = ", ".join(f"{key} = :{key}" for key in updates)
        updates["user_id"] = user_id
        updates["updated_at"] = datetime.now().isoformat(timespec="seconds")
        with connect() as connection:
            connection.execute(
                f"UPDATE users SET {assignments}, updated_at = :updated_at WHERE id = :user_id",
                updates,
            )
            connection.commit()
    return get_profile(user_id)


def list_exercises() -> List[Dict[str, Any]]:
    with connect() as connection:
        rows = connection.execute("SELECT * FROM exercises ORDER BY id").fetchall()
    return rows_to_dicts(rows)


def find_exercise(name: str) -> Optional[Dict[str, Any]]:
    with connect() as connection:
        row = connection.execute("SELECT * FROM exercises WHERE name = ?", (name,)).fetchone()
        if row is None:
            row = connection.execute("SELECT * FROM exercises WHERE ? LIKE '%' || name || '%' LIMIT 1", (name,)).fetchone()
    return dict(row) if row else None


def add_set_record(user_id: int, exercise_id: int, weight_kg: float, sets: int, reps: int, rpe: Optional[float], completed: bool, feeling: str, trained_on: Optional[str] = None) -> int:
    trained_on = trained_on or date.today().isoformat()
    with connect() as connection:
        cursor = connection.execute(
            "INSERT INTO set_records (user_id, exercise_id, weight_kg, sets, reps, rpe, completed, feeling, trained_on, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (user_id, exercise_id, weight_kg, sets, reps, rpe, 1 if completed else 0, feeling, trained_on, datetime.now().isoformat(timespec="seconds")),
        )
        connection.commit()
        return int(cursor.lastrowid)


def history_for_exercise(user_id: int, exercise_id: int, limit: int = 12) -> List[Dict[str, Any]]:
    with connect() as connection:
        rows = connection.execute(
            "SELECT * FROM set_records WHERE user_id = ? AND exercise_id = ? ORDER BY id DESC LIMIT ?",
            (user_id, exercise_id, limit),
        ).fetchall()
    return rows_to_dicts(rows)


def recent_records(user_id: int, limit: int = 20) -> List[Dict[str, Any]]:
    with connect() as connection:
        rows = connection.execute(
            "SELECT r.*, e.name AS exercise_name FROM set_records r JOIN exercises e ON e.id = r.exercise_id"
            " WHERE r.user_id = ? ORDER BY r.id DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
    return rows_to_dicts(rows)


def records_between(user_id: int, start_on: str, end_on: str) -> List[Dict[str, Any]]:
    """取某个日期区间内的训练记录，供日历与训练分析使用。"""
    with connect() as connection:
        rows = connection.execute(
            "SELECT r.*, e.name AS exercise_name, e.muscle AS muscle FROM set_records r"
            " JOIN exercises e ON e.id = r.exercise_id"
            " WHERE r.user_id = ? AND r.trained_on BETWEEN ? AND ?"
            " ORDER BY r.trained_on, r.id",
            (user_id, start_on, end_on),
        ).fetchall()
    return rows_to_dicts(rows)


def get_set_record(record_id: int, user_id: int = 1) -> Optional[Dict[str, Any]]:
    with connect() as connection:
        row = connection.execute(
            "SELECT * FROM set_records WHERE id = ? AND user_id = ?",
            (record_id, user_id),
        ).fetchone()
    return dict(row) if row else None


def delete_set_record(record_id: int, user_id: int = 1) -> Optional[Dict[str, Any]]:
    """删除一条训练记录，并把被删掉的数据返回，方便前端提供撤销。"""
    record = get_set_record(record_id, user_id)
    if record is None:
        return None
    with connect() as connection:
        connection.execute("DELETE FROM set_records WHERE id = ? AND user_id = ?", (record_id, user_id))
        connection.commit()
    return record


def restore_set_record(record: Dict[str, Any], user_id: int = 1) -> int:
    """把撤销的训练记录原样写回，不产生新的计划调整记录。"""
    exercise_id = record.get("exercise_id")
    if exercise_id is not None:
        with connect() as connection:
            exists = connection.execute("SELECT id FROM exercises WHERE id = ?", (exercise_id,)).fetchone()
        if exists is None:
            exercise_id = None

    with connect() as connection:
        cursor = connection.execute(
            "INSERT INTO set_records (user_id, exercise_id, weight_kg, sets, reps, rpe, completed, feeling, trained_on, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                user_id,
                exercise_id,
                record.get("weight_kg", 0),
                record.get("sets", 0),
                record.get("reps", 0),
                record.get("rpe"),
                1 if record.get("completed") else 0,
                record.get("feeling", ""),
                record.get("trained_on"),
                record.get("created_at") or datetime.now().isoformat(timespec="seconds"),
            ),
        )
        connection.commit()
        return int(cursor.lastrowid)


def last_training_date(user_id: int) -> Optional[str]:
    with connect() as connection:
        row = connection.execute("SELECT MAX(trained_on) AS d FROM set_records WHERE user_id = ?", (user_id,)).fetchone()
    return row["d"] if row and row["d"] else None


def save_pose_analysis(
    user_id: int,
    video_name: str,
    view: str,
    verdict: Dict[str, Any],
    overlay_path: str,
    card_path: str,
    exercise: str = "squat",
) -> int:
    with connect() as connection:
        cursor = connection.execute(
            "INSERT INTO pose_analyses (user_id, video_name, exercise, view, status, summary, issues_json, metrics_json, overlay_path, card_path, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                user_id,
                video_name,
                exercise,
                view,
                verdict.get("status"),
                verdict.get("summary"),
                json.dumps(verdict.get("issues", []), ensure_ascii=False),
                json.dumps(verdict.get("metrics", {}), ensure_ascii=False),
                overlay_path,
                card_path,
                datetime.now().isoformat(timespec="seconds"),
            ),
        )
        connection.commit()
        return int(cursor.lastrowid)


def list_pose_analyses(user_id: int, limit: int = 5) -> List[Dict[str, Any]]:
    with connect() as connection:
        rows = connection.execute(
            "SELECT * FROM pose_analyses WHERE user_id = ? ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
    return rows_to_dicts(rows)


def save_posture_assessment(
    user_id: int,
    issues: List[Dict[str, Any]],
    angles: Dict[str, Any],
    risk_score: int,
) -> int:
    with connect() as connection:
        cursor = connection.execute(
            "INSERT INTO posture_assessments (user_id, risk_score, issues_json, angles_json, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (
                user_id,
                risk_score,
                json.dumps(issues, ensure_ascii=False),
                json.dumps(angles, ensure_ascii=False),
                datetime.now().isoformat(timespec="seconds"),
            ),
        )
        connection.commit()
        return int(cursor.lastrowid)


def list_posture_assessments(user_id: int, limit: int = 5) -> List[Dict[str, Any]]:
    with connect() as connection:
        rows = connection.execute(
            "SELECT * FROM posture_assessments WHERE user_id = ? ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
    items = rows_to_dicts(rows)
    for item in items:
        for key in ("issues_json", "angles_json"):
            if item.get(key):
                try:
                    item[key.replace("_json", "")] = json.loads(item.pop(key))
                except Exception:  # noqa: BLE001
                    pass
    return items


def search_foods(keyword: str = "", limit: int = 60) -> List[Dict[str, Any]]:
    with connect() as connection:
        if keyword:
            rows = connection.execute(
                "SELECT * FROM foods WHERE name LIKE ? OR window LIKE ? OR canteen LIKE ? ORDER BY price LIMIT ?",
                (f"%{keyword}%", f"%{keyword}%", f"%{keyword}%", limit),
            ).fetchall()
        else:
            rows = connection.execute("SELECT * FROM foods ORDER BY kcal LIMIT ?", (limit,)).fetchall()
    return rows_to_dicts(rows)


def get_food(food_id: int) -> Optional[Dict[str, Any]]:
    with connect() as connection:
        row = connection.execute("SELECT * FROM foods WHERE id = ?", (food_id,)).fetchone()
    return dict(row) if row else None


def add_meal(user_id: int, food: Dict[str, Any], servings: float, meal_type: str, eaten_on: Optional[str] = None) -> int:
    eaten_on = eaten_on or date.today().isoformat()
    with connect() as connection:
        cursor = connection.execute(
            "INSERT INTO meals (user_id, eaten_on, meal_type, food_id, food_name, servings, kcal, protein, carb, fat, price, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                user_id,
                eaten_on,
                meal_type,
                food["id"],
                food["name"],
                servings,
                food["kcal"] * servings,
                food["protein"] * servings,
                food["carb"] * servings,
                food["fat"] * servings,
                food["price"] * servings,
                datetime.now().isoformat(timespec="seconds"),
            ),
        )
        connection.commit()
        return int(cursor.lastrowid)


def meals_on(user_id: int, day: Optional[str] = None) -> List[Dict[str, Any]]:
    day = day or date.today().isoformat()
    with connect() as connection:
        rows = connection.execute(
            "SELECT * FROM meals WHERE user_id = ? AND eaten_on = ? ORDER BY id DESC",
            (user_id, day),
        ).fetchall()
    return rows_to_dicts(rows)


def delete_meal(meal_id: int, user_id: int) -> None:
    with connect() as connection:
        connection.execute("DELETE FROM meals WHERE id = ? AND user_id = ?", (meal_id, user_id))
        connection.commit()


def add_plan_change(user_id: int, reason: str, before_text: str, after_text: str, trigger: str) -> int:
    with connect() as connection:
        version = connection.execute("SELECT COUNT(*) AS n FROM plan_changes WHERE user_id = ?", (user_id,)).fetchone()["n"] + 1
        cursor = connection.execute(
            "INSERT INTO plan_changes (user_id, plan_version, before_text, after_text, reason, trigger, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (user_id, version, before_text, after_text, reason, trigger, datetime.now().isoformat(timespec="seconds")),
        )
        connection.commit()
        return int(cursor.lastrowid)


def list_plan_changes(user_id: int, limit: int = 10) -> List[Dict[str, Any]]:
    with connect() as connection:
        rows = connection.execute(
            "SELECT * FROM plan_changes WHERE user_id = ? ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
    return rows_to_dicts(rows)


def add_nudge(user_id: int, kind: str, message: str) -> int:
    with connect() as connection:
        cursor = connection.execute(
            "INSERT INTO nudges (user_id, kind, message, status, created_at) VALUES (?, ?, ?, 'new', ?)",
            (user_id, kind, message, datetime.now().isoformat(timespec="seconds")),
        )
        connection.commit()
        return int(cursor.lastrowid)


def list_nudges(user_id: int, limit: int = 5) -> List[Dict[str, Any]]:
    with connect() as connection:
        rows = connection.execute(
            "SELECT * FROM nudges WHERE user_id = ? ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
    return rows_to_dicts(rows)


def days_since_last_training(user_id: int) -> Optional[int]:
    last = last_training_date(user_id)
    if not last:
        return None
    return (date.today() - date.fromisoformat(last)).days
