"""业务规则：训练记录解析、加重决策、营养缺口、督导提醒。

原则：数值与判定由代码算，大模型只用于把结论说成人话（没有 API Key 时自动降级）。
"""

from __future__ import annotations

import json
import os
import re
import urllib.request
import base64
import io
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import db
import posture_rules

CONFIG_DIR = db.ROOT_DIR / "配置"
CONFIG_PATH = CONFIG_DIR / "ai配置.txt"

PROVIDERS: Dict[str, Dict[str, str]] = {
    "deepseek": {
        "label": "DeepSeek",
        "url": "https://api.deepseek.com/v1/chat/completions",
        "default_model": "deepseek-chat",
        "key_field": "deepseek_key",
        "key_env": "DEEPSEEK_API_KEY",
        "console": "https://platform.deepseek.com/api_keys",
        "vision": False,
    },
    "dashscope": {
        "label": "通义千问",
        "url": "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
        "default_model": "qwen-plus",
        "key_field": "dashscope_key",
        "key_env": "DASHSCOPE_API_KEY",
        "console": "https://bailian.console.aliyun.com/",
        "vision": True,
        "default_vision_model": "qwen-vl-plus",
    },
    "zhipu": {
        "label": "智谱 GLM",
        "url": "https://open.bigmodel.cn/api/paas/v4/chat/completions",
        "default_model": "glm-4-flash",
        "key_field": "zhipu_key",
        "key_env": "ZHIPU_API_KEY",
        "console": "https://open.bigmodel.cn/usercenter/apikeys",
        "vision": True,
        "default_vision_model": "glm-4v-flash",
    },
    "moonshot": {
        "label": "月之暗面 Kimi",
        "url": "https://api.moonshot.cn/v1/chat/completions",
        "default_model": "moonshot-v1-8k",
        "key_field": "moonshot_key",
        "key_env": "MOONSHOT_API_KEY",
        "console": "https://platform.moonshot.cn/console/api-keys",
        "vision": True,
        "default_vision_model": "moonshot-v1-8k-vision-preview",
    },
    "ark": {
        "label": "火山方舟（豆包）",
        "url": "https://ark.cn-beijing.volces.com/api/v3/chat/completions",
        "default_model": "doubao-seed-1-6",
        "key_field": "ark_key",
        "key_env": "ARK_API_KEY",
        "console": "https://console.volcengine.com/ark",
        "vision": True,
        "default_vision_model": "doubao-seed-1-6",
    },
}

CONFIG_TEMPLATE = """# U炼ai · 大模型配置
# 在下面任意一个 Key 后面填入你的密钥即可，也可以直接在网页的「我的与计划」页面里填。
# 五个平台选一个就行，推荐 DeepSeek（便宜、中文好）。
# 修改后如果服务正在运行，请在网页上点一次「保存并测试」，或重启服务。

provider=deepseek

deepseek_key=
dashscope_key=
zhipu_key=
moonshot_key=
ark_key=

deepseek_model=deepseek-chat
dashscope_model=qwen-plus
zhipu_model=glm-4-flash
moonshot_model=moonshot-v1-8k
ark_model=doubao-seed-1-6

# 拍照识别用的视觉模型（DeepSeek 不支持看图，需要另外填一个支持视觉的平台）
dashscope_vision_model=qwen-vl-plus
zhipu_vision_model=glm-4v-flash
moonshot_vision_model=moonshot-v1-8k-vision-preview
ark_vision_model=doubao-seed-1-6
"""


def read_config() -> Dict[str, str]:
    config: Dict[str, str] = {}
    if CONFIG_PATH.exists():
        for raw_line in CONFIG_PATH.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            config[key.strip()] = value.strip()
    return config


def ensure_config_file() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if not CONFIG_PATH.exists():
        CONFIG_PATH.write_text(CONFIG_TEMPLATE, encoding="utf-8")


def save_config(provider: str, key: str, model: str = "") -> Dict[str, str]:
    """把网页上填的 Key 写进配置文件，保留其它平台的设置。"""
    ensure_config_file()
    config = read_config()
    if provider not in PROVIDERS:
        raise ValueError(f"不支持的平台：{provider}")
    config["provider"] = provider
    config[PROVIDERS[provider]["key_field"]] = key.strip()
    if model.strip():
        config[f"{provider}_model"] = model.strip()
    lines = [
        "# U炼ai · 大模型配置（由网页保存）",
        f"provider={config.get('provider', provider)}",
        "",
    ]
    for pid, meta in PROVIDERS.items():
        value = config.get(meta["key_field"], "")
        lines.append(f"{meta['key_field']}={value}")
    lines.append("")
    for pid, meta in PROVIDERS.items():
        value = config.get(f"{pid}_model", meta["default_model"])
        lines.append(f"{pid}_model={value}")
    lines.append("")
    lines.append("# 拍照识别用的视觉模型（DeepSeek 不支持看图，需要另外填一个支持视觉的平台）")
    for pid, meta in PROVIDERS.items():
        if not meta.get("vision"):
            continue
        value = config.get(f"{pid}_vision_model", meta.get("default_vision_model", ""))
        lines.append(f"{pid}_vision_model={value}")
    lines.append("")
    CONFIG_PATH.write_text("\n".join(lines), encoding="utf-8")
    return config
RPE_HINTS = [
    (("极限", "拼了", "力竭"), 10.0),
    (("很吃力", "非常吃力", "快不行"), 9.0),
    (("吃力", "有点累", "费劲"), 8.0),
    (("还行", "一般"), 7.0),
    (("轻松", "很轻松", "没感觉"), 6.0),
]

WEEKDAY_NAMES = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}


def resolve_date(text: str, today: Optional[date] = None) -> Optional[str]:
    """把"昨天""上周三""10 月 1 日"这类说法解析成具体日期。识别不出就返回 None。"""
    today = today or date.today()
    text = text or ""

    for keyword, delta in (("大前天", 3), ("前天", 2), ("昨天", 1), ("昨儿", 1), ("今日", 0), ("今天", 0), ("今儿", 0), ("刚刚", 0), ("刚才", 0)):
        if keyword in text:
            return (today - timedelta(days=delta)).isoformat()

    days_ago = re.search(r"(\d+)\s*天前", text)
    if days_ago:
        return (today - timedelta(days=int(days_ago.group(1)))).isoformat()

    month_day = re.search(r"(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]", text)
    if month_day:
        month, day = int(month_day.group(1)), int(month_day.group(2))
        try:
            candidate = date(today.year, month, day)
        except ValueError:
            return None
        # 未来的日期当作去年（避免"12月"被理解成还没到的那次）
        return (candidate if candidate <= today else date(today.year - 1, month, day)).isoformat()

    weekday = re.search(r"(上上周|上周|这周|本周|周|星期|礼拜)\s*([一二三四五六日天])", text)
    if weekday:
        prefix, name = weekday.group(1), weekday.group(2)
        target = WEEKDAY_NAMES[name]
        base = today - timedelta(days=(today.weekday() - target) % 7)
        if prefix in ("上周", "上上周"):
            base -= timedelta(days=7 if prefix == "上周" else 14)
        return base.isoformat()

    return None

# ---------------------------------------------------------------- 记录解析


def parse_workout_text(text: str) -> Dict[str, Any]:
    """把"今天深蹲 60 公斤 5 组 5 次，最后一组有点吃力"解析成结构化记录。"""
    text = (text or "").strip()
    result: Dict[str, Any] = {
        "raw_text": text,
        "exercise": None,
        "weight_kg": None,
        "sets": None,
        "reps": None,
        "rpe": None,
        "completed": True,
        "feeling": text,
        "missing": [],
    }

    for exercise in db.list_exercises():
        if exercise["name"] in text:
            if result["exercise"] is None or len(exercise["name"]) > len(result["exercise"]):
                result["exercise"] = exercise["name"]

    weight = re.search(r"(\d+(?:\.\d+)?)\s*(?:kg|KG|公斤|千克)", text)
    if weight:
        result["weight_kg"] = float(weight.group(1))

    sets = re.search(r"(\d+)\s*组", text)
    if sets:
        result["sets"] = int(sets.group(1))

    reps = re.search(r"(\d+)\s*(?:次|个|下)", text)
    if reps:
        result["reps"] = int(reps.group(1))

    for keywords, value in RPE_HINTS:
        if any(keyword in text for keyword in keywords):
            result["rpe"] = value
            break

    if any(keyword in text for keyword in ("没完成", "没做完", "失败", "做不动")):
        result["completed"] = False

    if result["exercise"] is None:
        result["missing"].append("动作名称")
    if result["sets"] is None:
        result["missing"].append("组数")
    if result["reps"] is None:
        result["missing"].append("次数")
    if result["weight_kg"] is None:
        exercise = db.find_exercise(result["exercise"] or "")
        if exercise and exercise["equipment"] in ("自重",):
            result["weight_kg"] = 0.0
        else:
            result["missing"].append("重量")

    result["confidence"] = "高" if not result["missing"] else "中"
    result["trained_on"] = resolve_date(text)
    result["parser"] = "规则"
    return result


PARSE_SYSTEM_PROMPT = """你是健身训练记录解析器。把用户的一句话解析成 JSON，不要输出任何解释。
字段要求：
- exercise：动作名称，用中文，尽量对应常见力量动作（深蹲、硬拉、卧推、引体向上、俯卧撑、推举、划船等）
- weight_kg：杠铃或哑铃重量，数字；自重动作填 0
- sets：组数，整数
- reps：每组次数，整数
- rpe：主观强度 6 到 10 的数字，用户说"轻松"填 6，"有点吃力"填 8，"很吃力"填 9，"极限"填 10；没提到填 null
- completed：是否完成计划，布尔值
- trained_on：训练日期，格式 YYYY-MM-DD。今天是 {today}。用户说"昨天"就减一天，"前天"减两天，"上周三"指上一个周三，没提到日期就填今天
- feeling：原话里的主观描述，简短

只输出 JSON 对象。"""


def llm_parse_record(text: str) -> Optional[Dict[str, Any]]:
    """让大模型把口语解析成结构化记录（没配 Key 时返回 None）。"""
    if not llm_settings():
        return None
    content = llm_chat(PARSE_SYSTEM_PROMPT.format(today=date.today().isoformat()), text, timeout=30)
    if not content:
        return None
    cleaned = content.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\s*", "", cleaned)
        cleaned = re.sub(r"```\s*$", "", cleaned).strip()
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, re.S)
        if not match:
            return None
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    return parsed if isinstance(parsed, dict) else None


def parse_record(text: str) -> Dict[str, Any]:
    """解析入口：优先用大模型，失败或没配 Key 时退回规则解析，两者结果做校验合并。"""
    rules = parse_workout_text(text)
    parsed = llm_parse_record(text)
    if not parsed:
        return rules

    result = dict(rules)
    result["parser"] = "大模型"

    exercise_name = str(parsed.get("exercise") or "").strip()
    exercise = None
    if exercise_name:
        exercise = db.find_exercise(exercise_name)
        if exercise:
            result["exercise"] = exercise["name"]
    if not result.get("exercise"):
        result["exercise"] = rules["exercise"]

    def number(value):
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    weight = number(parsed.get("weight_kg"))
    if weight is not None and weight >= 0:
        result["weight_kg"] = weight
    for key in ("sets", "reps"):
        value = number(parsed.get(key))
        if value is not None and value > 0:
            result[key] = int(value)
    rpe = number(parsed.get("rpe"))
    if rpe is not None and 1 <= rpe <= 10:
        result["rpe"] = rpe
    if isinstance(parsed.get("completed"), bool):
        result["completed"] = parsed["completed"]

    trained_on = str(parsed.get("trained_on") or "").strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", trained_on):
        result["trained_on"] = trained_on
    elif rules.get("trained_on"):
        result["trained_on"] = rules["trained_on"]
    if parsed.get("feeling"):
        result["feeling"] = str(parsed["feeling"])[:120]

    # 合并后重新检查缺什么
    missing = []
    if not result.get("exercise"):
        missing.append("动作名称")
    if result.get("sets") is None:
        missing.append("组数")
    if result.get("reps") is None:
        missing.append("次数")
    if result.get("weight_kg") is None:
        missing.append("重量")
    result["missing"] = missing
    result["confidence"] = "高" if not missing else "中"
    return result


# ---------------------------------------------------------------- 加重决策


def decide_progression(exercise: Dict[str, Any], history: List[Dict[str, Any]], current: Dict[str, Any]) -> Dict[str, Any]:
    """根据历史记录与本次表现给出下次训练的建议。"""
    weight = float(current.get("weight_kg") or 0)
    is_bodyweight = exercise.get("equipment") == "自重"
    step = 2.5 if weight >= 20 else 1.0

    completed = bool(current.get("completed", True))
    rpe = current.get("rpe")

    if not completed:
        previous_failed = len(history) >= 1 and not int(history[0].get("completed", 1))
        if previous_failed:
            next_weight = max(0.0, round(weight * 0.9 / step) * step)
            return {
                "action": "deload",
                "next_weight_kg": next_weight,
                "delta": round(next_weight - weight, 2),
                "reason": f"同一个重量连续两次没有完成，先减到 {next_weight} 公斤，把动作做标准再往上加。",
            }
        return {
            "action": "hold",
            "next_weight_kg": weight,
            "delta": 0.0,
            "reason": "这次没有完成计划次数，重量保持不变，下一次先把同样的重量做完整。",
        }

    if is_bodyweight:
        reps = int(current.get("reps") or 0)
        return {
            "action": "increase_reps",
            "next_weight_kg": weight,
            "delta": 0.0,
            "next_reps": reps + 2,
            "reason": f"自重动作没有加重，下次每组做到 {reps + 2} 次。",
        }

    if rpe is not None and rpe >= 9:
        return {
            "action": "hold",
            "next_weight_kg": weight,
            "delta": 0.0,
            "reason": f"这次主观强度到了 {rpe:.0f}，属于接近极限，先不要加重，把同样的重量再练一次。",
        }

    next_weight = round((weight + step) / step) * step if weight > 0 else weight
    if rpe is not None:
        reason = (
            f"计划次数全部完成，主观强度 {rpe:.0f} 属于还有余量的区间，"
            f"下次加到 {next_weight} 公斤。"
        )
    else:
        reason = f"计划次数全部完成，下次加到 {next_weight} 公斤。"
    return {
        "action": "increase",
        "next_weight_kg": next_weight,
        "delta": round(next_weight - weight, 2),
        "reason": reason,
    }


# ---------------------------------------------------------------- 营养计算


def nutrition_targets(profile: Dict[str, Any]) -> Dict[str, float]:
    weight = float(profile.get("weight_kg") or 70)
    height = float(profile.get("height_cm") or 175)
    age = int(profile.get("age") or 20)
    gender = profile.get("gender") or "男"
    goal = profile.get("goal") or "增肌"
    weekly = int(profile.get("weekly_days") or 3)

    base = 10 * weight + 6.25 * height - 5 * age + (5 if gender == "男" else -161)
    factor = 1.375 if weekly <= 2 else (1.55 if weekly <= 4 else 1.725)
    kcal = base * factor
    if goal == "增肌":
        kcal += 300
        protein_per_kg = 1.8
    elif goal == "减脂":
        kcal -= 400
        protein_per_kg = 2.0
    else:
        protein_per_kg = 1.6

    return {
        "kcal": round(kcal),
        "protein": round(weight * protein_per_kg),
        "carb": round(kcal * 0.45 / 4),
        "fat": round(kcal * 0.25 / 9),
    }


def nutrition_summary(profile: Dict[str, Any], meals: List[Dict[str, Any]]) -> Dict[str, Any]:
    targets = nutrition_targets(profile)
    totals = {
        "kcal": round(sum(item["kcal"] for item in meals)),
        "protein": round(sum(item["protein"] for item in meals), 1),
        "carb": round(sum(item["carb"] for item in meals), 1),
        "fat": round(sum(item["fat"] for item in meals), 1),
        "price": round(sum(item["price"] for item in meals), 1),
    }
    return {
        "targets": targets,
        "totals": totals,
        "gap": {
            "kcal": round(targets["kcal"] - totals["kcal"]),
            "protein": round(targets["protein"] - totals["protein"], 1),
        },
        "meals": meals,
    }


def suggest_meals(protein_gap: float, kcal_gap: float, budget: float) -> Dict[str, Any]:
    """在预算与热量允许的范围内，优先补齐蛋白质。"""
    if protein_gap <= 0:
        return {"items": [], "total_price": 0.0, "total_protein": 0.0, "message": "今天的蛋白质已经达标，不需要额外补。"}

    foods = db.search_foods("", limit=200)
    # 只看蛋白质密度高的食物，避免为了凑蛋白质而推荐一堆米饭馒头
    candidates = []
    for food in foods:
        if food["protein"] <= 3 or food["canteen"] == "自备":
            continue
        density = food["protein"] / max(food["kcal"], 1) * 100
        if density < 5:
            continue
        if food["kcal"] > max(kcal_gap, 0) + 150:
            continue
        candidates.append({**food, "density": round(density, 1)})
    candidates.sort(key=lambda item: (item["protein"] / max(item["price"], 0.5)), reverse=True)

    picked: List[Dict[str, Any]] = []
    remaining_protein = protein_gap
    remaining_budget = max(budget, 0)
    remaining_kcal = max(kcal_gap, 0) + 200

    for food in candidates:
        if remaining_protein <= 0 or remaining_budget <= 0:
            break
        if food["price"] > remaining_budget and picked:
            continue
        if food["kcal"] > remaining_kcal and picked:
            continue
        if any(item["name"] == food["name"] for item in picked):
            continue
        picked.append(food)
        remaining_protein -= food["protein"]
        remaining_budget -= food["price"]
        remaining_kcal -= food["kcal"]
        if len(picked) >= 4:
            break

    total_price = round(sum(item["price"] for item in picked), 1)
    total_protein = round(sum(item["protein"] for item in picked), 1)
    windows = sorted({f"{item['canteen']}{item['window']}" for item in picked})
    if picked:
        message = (
            f"还差 {protein_gap:.0f} 克蛋白质。建议去{('、'.join(windows))}，"
            f"点上面这几样，合计约 {total_price} 元，能补 {total_protein} 克蛋白质。"
        )
        if protein_gap - total_protein > 5:
            message += " 如果还没补够，再加一勺蛋白粉或两盒牛奶。"
    else:
        message = "在当前预算与热量范围内没有合适的组合，建议先补一个鸡蛋或一盒牛奶。"

    return {
        "items": picked,
        "total_price": total_price,
        "total_protein": total_protein,
        "message": message,
    }


# ---------------------------------------------------------------- 督导提醒


def nudge_message(days: Optional[int], last_date: Optional[str]) -> Dict[str, Any]:
    if days is None:
        return {
            "kind": "no_record",
            "message": "还没有训练记录。第一次训练可以先只做一次动作自检，看看现在的动作有没有问题。",
            "downgrade": None,
        }
    if days >= 3:
        return {
            "kind": "missed_days",
            "message": f"已经 {days} 天没有训练了，是没时间、太累，还是不想练了？告诉我原因，我把计划改小一点。",
            "downgrade": "把今天的训练改成 15 分钟宿舍版：深蹲 3 组 12 次、俯卧撑 3 组力竭、平板支撑 3 组 40 秒，组间休息 60 秒。",
            "reason": f"距离上次训练（{last_date}）已经 {days} 天，按恢复情况把当天的总量下调，先恢复训练习惯。",
        }
    return {
        "kind": "on_track",
        "message": f"距离上次训练 {days} 天，节奏正常，按原计划继续。",
        "downgrade": None,
    }


# ---------------------------------------------------------------- 大模型


def llm_status() -> Dict[str, Any]:
    """给页面用的状态：是否已配置、用的哪家、模型名、脱敏后的 Key。"""
    settings = llm_settings()
    config = read_config()
    preferred = config.get("provider", "deepseek")
    masked = ""
    if settings:
        key = settings["key"]
        masked = key[:4] + "*" * max(0, len(key) - 8) + key[-4:] if len(key) > 8 else "****"
    return {
        "configured": bool(settings),
        "provider": settings["provider"] if settings else preferred,
        "label": settings["label"] if settings else PROVIDERS.get(preferred, PROVIDERS["deepseek"])["label"],
        "model": settings["model"] if settings else PROVIDERS.get(preferred, {}).get("default_model", ""),
        "masked_key": masked,
        "config_path": str(CONFIG_PATH),
        "providers": [
            {
                "id": pid,
                "label": meta["label"],
                "default_model": meta["default_model"],
                "console": meta["console"],
                "vision": bool(meta.get("vision")),
                "default_vision_model": meta.get("default_vision_model", ""),
            }
            for pid, meta in PROVIDERS.items()
        ],
    }


def llm_settings() -> Optional[Dict[str, str]]:
    """按优先级找一个可用的平台：配置文件里指定的优先，其次是任意填了 Key 的。"""
    config = read_config()
    order = [config.get("provider", "deepseek")] + [pid for pid in PROVIDERS if pid != config.get("provider")]
    for provider_id in order:
        meta = PROVIDERS.get(provider_id)
        if not meta:
            continue
        key = config.get(meta["key_field"], "").strip() or os.environ.get(meta["key_env"], "").strip()
        if not key:
            continue
        model = config.get(f"{provider_id}_model", "").strip() or meta["default_model"]
        return {
            "provider": provider_id,
            "name": meta["label"],
            "label": meta["label"],
            "url": meta["url"],
            "model": model,
            "key": key,
        }
    return None


def llm_test() -> Dict[str, Any]:
    """在页面上点「测试」时调用：真的发一次请求，返回模型原话或错误原因。"""
    settings = llm_settings()
    if not settings:
        return {"ok": False, "error": "还没有填任何平台的 API Key"}
    payload = json.dumps(
        {
            "model": settings["model"],
            "messages": [
                {"role": "system", "content": "你是校园健身教练，回答控制在一句话以内。"},
                {"role": "user", "content": "用一句话说明深蹲时膝盖内扣为什么要纠正。"},
            ],
            "temperature": 0.3,
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        settings["url"],
        data=payload,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {settings['key']}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=40) as response:
            data = json.loads(response.read().decode("utf-8"))
        reply = data["choices"][0]["message"]["content"].strip()
        return {"ok": True, "provider": settings["name"], "model": settings["model"], "reply": reply}
    except Exception as exc:  # noqa: BLE001
        detail = ""
        if hasattr(exc, "read"):
            try:
                detail = exc.read().decode("utf-8", "ignore")[:200]
            except Exception:  # noqa: BLE001
                detail = ""
        return {"ok": False, "provider": settings["name"], "model": settings["model"], "error": f"{type(exc).__name__}: {exc} {detail}".strip()}


# ---------------------------------------------------------------- 视觉模型（拍照识别菜品）


def vision_settings() -> Optional[Dict[str, str]]:
    """挑一个既填了 Key、又支持看图的平台。DeepSeek 只有文字模型，会跳过。"""
    config = read_config()
    order = [config.get("provider", "deepseek")] + [pid for pid in PROVIDERS if pid != config.get("provider")]
    for provider_id in order:
        meta = PROVIDERS.get(provider_id)
        if not meta or not meta.get("vision"):
            continue
        key = config.get(meta["key_field"], "").strip() or os.environ.get(meta["key_env"], "").strip()
        if not key:
            continue
        model = config.get(f"{provider_id}_vision_model", "").strip() or meta.get("default_vision_model", "")
        if not model:
            continue
        return {
            "provider": provider_id,
            "label": meta["label"],
            "url": meta["url"],
            "model": model,
            "key": key,
        }
    return None


def vision_status() -> Dict[str, Any]:
    settings = vision_settings()
    text_only = llm_settings()
    hint = ""
    if not settings:
        if text_only and not PROVIDERS.get(text_only["provider"], {}).get("vision"):
            hint = f"当前只配置了 {text_only['name']}，它只有文字模型、不能看图；再填一个支持视觉的平台即可启用拍照识别。"
        else:
            hint = "还没有配置支持看图的模型，拍照识别暂不可用，可以先用下面的手动选菜。"
    return {
        "available": bool(settings),
        "provider": settings["label"] if settings else "",
        "model": settings["model"] if settings else "",
        "hint": hint,
        "candidates": [
            {"id": pid, "label": meta["label"], "model": meta.get("default_vision_model", ""), "console": meta["console"]}
            for pid, meta in PROVIDERS.items()
            if meta.get("vision")
        ],
    }


def image_to_data_url(raw: bytes, max_side: int = 1024) -> str:
    """把上传的图片压到合适尺寸再编码成 data URL，避免几 MB 的原图拖慢识别。"""
    try:
        from PIL import Image

        image = Image.open(io.BytesIO(raw)).convert("RGB")
        image.thumbnail((max_side, max_side))
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=85)
        return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode()
    except Exception:  # noqa: BLE001
        return "data:image/jpeg;base64," + base64.b64encode(raw).decode()


FOOD_VISION_PROMPT = """你是食堂营养识别助手。用户会给你一张食堂餐盘或菜单的照片，请判断里面有哪些菜。

只能从下面这份菜品库中选择，用编号回答，不要自己编菜名：
{food_list}

输出 JSON，不要任何解释：
{{"items": [{{"index": 编号, "portions": 份数, "confidence": 0到1之间的小数, "reason": "判断依据，10 字以内"}}],
  "scene": "一句话描述画面，20 字以内"}}

份数判断标准：照片里明显只有一点点记 0.5，正常一份记 1，明显比别人多或双份记 1.5 到 2。
如果照片里没有任何食物，items 返回空数组。
如果某道菜在库里找不到对应项，选最接近的那一项，并在 reason 里说明。"""


def analyze_food_photo(data_url: str, foods: List[Dict[str, Any]]) -> Dict[str, Any]:
    """用视觉模型识别照片里的菜品，再把结果映射回菜品库计算热量与蛋白质。"""
    settings = vision_settings()
    if not settings:
        return {"ok": False, "error": vision_status()["hint"]}

    food_list = "\n".join(
        f"{index + 1}. {item['name']}（{item['serving']}，{item['kcal']} 千卡，蛋白 {item['protein']} 克）"
        for index, item in enumerate(foods)
    )
    payload = json.dumps(
        {
            "model": settings["model"],
            "messages": [
                {"role": "system", "content": "你只输出 JSON。"},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": FOOD_VISION_PROMPT.format(food_list=food_list)},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                },
            ],
            "temperature": 0.2,
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        settings["url"],
        data=payload,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {settings['key']}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            data = json.loads(response.read().decode("utf-8"))
        content = data["choices"][0]["message"]["content"].strip()
    except Exception as exc:  # noqa: BLE001
        detail = ""
        if hasattr(exc, "read"):
            try:
                detail = exc.read().decode("utf-8", "ignore")[:200]
            except Exception:  # noqa: BLE001
                detail = ""
        return {"ok": False, "error": f"{type(exc).__name__}: {exc} {detail}".strip()}

    cleaned = re.sub(r"^```[a-zA-Z]*\s*", "", content)
    cleaned = re.sub(r"```\s*$", "", cleaned).strip()
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, re.S)
        if not match:
            return {"ok": False, "error": f"模型返回的内容无法解析：{content[:120]}"}
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError:
            return {"ok": False, "error": f"模型返回的内容无法解析：{content[:120]}"}

    items: List[Dict[str, Any]] = []
    total_kcal = total_protein = total_price = 0.0
    for entry in (parsed.get("items") or [])[:8]:
        try:
            index = int(entry.get("index")) - 1
        except (TypeError, ValueError):
            continue
        if not 0 <= index < len(foods):
            continue
        food = foods[index]
        try:
            portions = float(entry.get("portions") or 1)
        except (TypeError, ValueError):
            portions = 1.0
        portions = max(0.5, min(2.0, portions))
        try:
            confidence = float(entry.get("confidence") or 0.6)
        except (TypeError, ValueError):
            confidence = 0.6
        kcal = round(food["kcal"] * portions, 1)
        protein = round(food["protein"] * portions, 1)
        price = round((food.get("price") or 0) * portions, 1)
        total_kcal += kcal
        total_protein += protein
        total_price += price
        items.append(
            {
                "food_id": food["id"],
                "name": food["name"],
                "canteen": food.get("canteen"),
                "window": food.get("window"),
                "portions": portions,
                "confidence": round(min(1.0, max(0.0, confidence)), 2),
                "reason": str(entry.get("reason") or "")[:40],
                "kcal": kcal,
                "protein": protein,
                "price": price,
            }
        )

    return {
        "ok": True,
        "provider": settings["label"],
        "model": settings["model"],
        "scene": str(parsed.get("scene") or "")[:60],
        "items": items,
        "totals": {
            "kcal": round(total_kcal, 1),
            "protein": round(total_protein, 1),
            "price": round(total_price, 1),
        },
        "note": "热量与蛋白质由菜品库的标准值乘以份数算出，识别结果可以在加入前修改。",
    }


# ---------------------------------------------------------------- 体态评估（视觉模型看三张照片）


def _extract_json_object(content: str) -> Optional[Dict[str, Any]]:
    """把模型返回的内容清理成 JSON 对象：去掉代码围栏，抓取首个 {...}。"""
    cleaned = re.sub(r"^```[a-zA-Z]*\s*", "", content)
    cleaned = re.sub(r"```\s*$", "", cleaned).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, re.S)
        if not match:
            return None
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return None


POSTURE_VISION_PROMPT = """你是体态评估助手。用户会给你三张全身照，顺序是：正面、侧面、背面。请根据三张照片综合评估体态问题。

需要检查的问题，type 只能从下面这些词里选，不要自造：
头前伸、圆肩、圆肩驼背、高低肩、骨盆前倾、骨盆后倾、XO型腿

评估要点：
- 头前伸：侧面看耳垂是否明显在肩峰前方
- 圆肩/圆肩驼背：侧面看肩是否前扣含胸；圆肩驼背是圆肩叠加驼背（上背部后凸）
- 高低肩：正面或背面看双肩是否一高一低
- 骨盆前倾/后倾：侧面看腰椎曲度是否过大（前倾）或过直（后倾）
- XO型腿：正面或背面看腿型

输出 JSON，不要任何解释，格式：
{"issues":[{"type":"头前伸","level":"轻度","angle":32.5,"normalRange":"0~15°","advice":"上斜方肌拉伸 + 下巴后缩训练"}],
 "angles":{"耳肩对齐":{"value":32.5,"normal":"0~15°"}},
 "riskScore":58}

规则：
- level 只能取 轻度 / 中度 / 重度
- angle 是偏离正常范围的角度（纯数字，不带单位）
- normalRange 是正常范围，例如 "0~15°"
- advice 给出一条 20 字以内的建议
- angles 列出你实际测量的关键角度，每项含 value（数字）和 normal（正常范围）
- riskScore 是 0~100 的整数，综合所有问题的严重程度
- 没检测到的问题不要列；如果照片里没有人或看不清，issues 返回空数组，riskScore 给低分"""


def analyze_posture_photos(front_data_url: str, side_data_url: str, back_data_url: str) -> Dict[str, Any]:
    """用视觉模型分析正/侧/背三张照片，返回统一的 {issues, angles, risk_score}。"""
    settings = vision_settings()
    if not settings:
        return {"ok": False, "error": vision_status()["hint"]}

    content = [
        {"type": "text", "text": POSTURE_VISION_PROMPT},
        {"type": "image_url", "image_url": {"url": front_data_url}},
        {"type": "image_url", "image_url": {"url": side_data_url}},
        {"type": "image_url", "image_url": {"url": back_data_url}},
    ]
    payload = json.dumps(
        {
            "model": settings["model"],
            "messages": [
                {"role": "system", "content": "你只输出 JSON。"},
                {"role": "user", "content": content},
            ],
            "temperature": 0.2,
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        settings["url"],
        data=payload,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {settings['key']}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            data = json.loads(response.read().decode("utf-8"))
        content_text = data["choices"][0]["message"]["content"].strip()
    except Exception as exc:  # noqa: BLE001
        detail = ""
        if hasattr(exc, "read"):
            try:
                detail = exc.read().decode("utf-8", "ignore")[:200]
            except Exception:  # noqa: BLE001
                detail = ""
        return {"ok": False, "error": f"{type(exc).__name__}: {exc} {detail}".strip()}

    parsed = _extract_json_object(content_text)
    if parsed is None:
        return {"ok": False, "error": f"模型返回的内容无法解析：{content_text[:120]}"}

    allowed_levels = {"轻度", "中度", "重度"}
    issues: List[Dict[str, Any]] = []
    for item in (parsed.get("issues") or [])[:12]:
        if not isinstance(item, dict) or not item.get("type"):
            continue
        level = str(item.get("level") or "轻度")
        if level not in allowed_levels:
            level = "轻度"
        try:
            angle = float(item.get("angle") or 0)
        except (TypeError, ValueError):
            angle = 0.0
        issues.append(
            {
                "type": str(item["type"])[:20],
                "level": level,
                "angle": round(angle, 1),
                "normalRange": str(item.get("normalRange") or "—")[:20],
                "advice": str(item.get("advice") or "")[:60],
            }
        )

    angles: Dict[str, Dict[str, Any]] = {}
    for name, entry in (parsed.get("angles") or {}).items():
        if not isinstance(entry, dict):
            continue
        try:
            value = float(entry.get("value") or 0)
        except (TypeError, ValueError):
            value = 0.0
        angles[str(name)[:20]] = {"value": round(value, 1), "normal": str(entry.get("normal") or "—")[:20]}

    try:
        risk_score = int(round(float(parsed.get("riskScore") or 0)))
    except (TypeError, ValueError):
        risk_score = 0
    risk_score = max(0, min(100, risk_score))

    return {
        "ok": True,
        "provider": settings["label"],
        "model": settings["model"],
        "issues": issues,
        "angles": angles,
        "risk_score": risk_score,
    }


def llm_chat(system_prompt: str, user_prompt: str, timeout: int = 40) -> Optional[str]:
    settings = llm_settings()
    if not settings:
        return None
    payload = json.dumps(
        {
            "model": settings["model"],
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.4,
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        settings["url"],
        data=payload,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {settings['key']}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
        return data["choices"][0]["message"]["content"].strip()
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------- 训练统计与建议


def record_volume(record: Dict[str, Any]) -> float:
    """容量 = 重量 × 组数 × 每组次数。自重动作重量为 0，容量记 0，但次数照常统计。"""
    weight = float(record.get("weight_kg") or 0)
    sets = int(record.get("sets") or 0)
    reps = int(record.get("reps") or 0)
    return round(weight * sets * reps, 1)


def record_reps(record: Dict[str, Any]) -> int:
    return int(record.get("sets") or 0) * int(record.get("reps") or 0)


def summarize_day(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    """把一天的多条记录汇总成动作数、组数、次数与容量。"""
    if not records:
        return {"count": 0, "sets": 0, "reps": 0, "volume": 0.0, "exercises": []}
    per_exercise: Dict[str, Dict[str, Any]] = {}
    total_sets = total_reps = 0
    total_volume = 0.0
    for item in records:
        name = item.get("exercise_name") or "未命名动作"
        entry = per_exercise.setdefault(name, {"name": name, "sets": 0, "reps": 0, "volume": 0.0, "top_weight": 0.0})
        entry["sets"] += int(item.get("sets") or 0)
        entry["reps"] += record_reps(item)
        entry["volume"] = round(entry["volume"] + record_volume(item), 1)
        entry["top_weight"] = max(entry["top_weight"], float(item.get("weight_kg") or 0))
        total_sets += int(item.get("sets") or 0)
        total_reps += record_reps(item)
        total_volume += record_volume(item)
    return {
        "count": len(records),
        "sets": total_sets,
        "reps": total_reps,
        "volume": round(total_volume, 1),
        "exercises": list(per_exercise.values()),
    }


def build_calendar(records: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """按日期归档，供日历上的圆点与数字使用。"""
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for item in records:
        grouped.setdefault(str(item.get("trained_on")), []).append(item)
    return {day: summarize_day(items) for day, items in grouped.items()}


def training_insight(records: List[Dict[str, Any]], today: Optional[date] = None) -> Dict[str, Any]:
    """把记录整理成一份可直接交给大模型的训练画像。"""
    today = today or date.today()
    week_start = today - timedelta(days=6)
    prev_start = today - timedelta(days=13)
    prev_end = today - timedelta(days=7)

    def between(start: date, end: date) -> List[Dict[str, Any]]:
        return [r for r in records if start.isoformat() <= str(r.get("trained_on")) <= end.isoformat()]

    this_week = between(week_start, today)
    last_week = between(prev_start, prev_end)

    def volume_of(items):
        return round(sum(record_volume(r) for r in items), 1)

    def reps_of(items):
        return sum(record_reps(r) for r in items)

    # 每个动作的重量变化，用来看渐进超负荷有没有发生
    per_exercise: Dict[str, Dict[str, Any]] = {}
    muscle_volume: Dict[str, float] = {}
    for item in records:
        name = item.get("exercise_name") or "未命名动作"
        entry = per_exercise.setdefault(name, {"name": name, "times": 0, "first_weight": None, "last_weight": None, "volume": 0.0})
        weight = float(item.get("weight_kg") or 0)
        entry["times"] += 1
        entry["volume"] = round(entry["volume"] + record_volume(item), 1)
        if weight > 0:
            if entry["first_weight"] is None:
                entry["first_weight"] = weight
            entry["last_weight"] = weight
        muscle = item.get("muscle") or "其他"
        muscle_volume[muscle] = round(muscle_volume.get(muscle, 0.0) + record_volume(item), 1)

    trend = None
    last_volume = volume_of(last_week)
    if last_volume > 0:
        trend = round((volume_of(this_week) - last_volume) / last_volume * 100, 1)

    return {
        "today": today.isoformat(),
        "recent_days": 14,
        "train_days_last_7": len({str(r.get("trained_on")) for r in this_week}),
        "train_days_prev_7": len({str(r.get("trained_on")) for r in last_week}),
        "volume_last_7": volume_of(this_week),
        "volume_prev_7": last_volume,
        "volume_trend_percent": trend,
        "reps_last_7": reps_of(this_week),
        "exercise_stats": sorted(per_exercise.values(), key=lambda item: item["times"], reverse=True)[:8],
        "muscle_volume": sorted(muscle_volume.items(), key=lambda pair: pair[1], reverse=True),
    }


ADVICE_SYSTEM_PROMPT = """你是一名面向大学生的健身教练。你会收到一份近两周的训练统计数据，请给出新的训练建议。
要求：
1. 先用一句话总评训练频率与容量的变化趋势。
2. 指出一个最需要改进的地方（频率、容量增长、肌群是否偏科、某个动作是否停滞）。
3. 给出下周的具体安排：练几天、每天练什么动作、重量或组数怎么调整。
4. 总量控制在 200 字以内，口吻直接具体，不使用医疗术语。
以纯文本输出，不要用 Markdown 标题和代码块，可以用短横线分点。"""


def training_advice(records: List[Dict[str, Any]], today: Optional[date] = None) -> Dict[str, Any]:
    """基于训练记录生成新建议：优先让大模型分析，没配 Key 时用规则给结论。"""
    insight = training_insight(records, today)
    text = llm_chat(ADVICE_SYSTEM_PROMPT, json.dumps(insight, ensure_ascii=False), timeout=45)
    if text:
        return {"advice": text, "source": "大模型", "insight": insight}
    return {"advice": rule_based_advice(insight), "source": "规则", "insight": insight}


def rule_based_advice(insight: Dict[str, Any]) -> str:
    lines: List[str] = []
    days = insight["train_days_last_7"]
    prev_days = insight["train_days_prev_7"]
    lines.append(f"最近 7 天练了 {days} 天，前 7 天练了 {prev_days} 天。")

    trend = insight["volume_trend_percent"]
    if trend is None:
        lines.append("上周还没有容量数据，先按固定节奏练起来，把每次的重量和组数都记下来。")
    elif trend >= 10:
        lines.append(f"总容量比前一周上升了 {trend}%，进度正常，注意别一次加太多。")
    elif trend <= -10:
        lines.append(f"总容量比前一周下降了 {abs(trend)}%，如果不是刻意减量，建议把训练频率或组数补回来。")
    else:
        lines.append(f"总容量比前一周变化 {trend}%，基本持平，可以考虑给主要动作加 2.5 公斤。")

    muscles = insight.get("muscle_volume") or []
    if len(muscles) >= 2:
        top, top_volume = muscles[0]
        total = sum(value for _, value in muscles) or 1
        share = round(top_volume / total * 100)
        if share >= 55:
            lines.append(f"{top}占了总容量的 {share}%，偏得有点多，下周把其中一天换成其他部位。")

    stats = insight.get("exercise_stats") or []
    for item in stats:
        if item["first_weight"] and item["last_weight"] and item["last_weight"] <= item["first_weight"] and item["times"] >= 3:
            lines.append(f"{item['name']}最近 {item['times']} 次都在 {item['last_weight']} 公斤，没有加重，建议下次加 2.5 公斤。")
            break

    lines.append("下周建议：保持每周 3 次，每次先做主要动作 4 组 6 至 8 次，再加两组辅助动作，训练后把数据记进来。")
    return "\n".join(lines)


def coach_explain_for_pose(verdict: Dict[str, Any]) -> Tuple[str, str]:
    """返回（建议文本, 来源），来源为"大模型"或"规则"。

    纠错式输出：不报角度数字，重点讲「错在哪 → 为什么 → 怎么改 → 下次怎么练」。
    """
    issues = verdict.get("issues") or []
    if not issues:
        return "这次动作没有检出明显问题，保持现在的节奏，下次训练再录一段对比，看角度有没有更标准。", "规则"

    # 高危问题优先讲，其次中等，最后低危
    order = {"high": 0, "medium": 1, "low": 2}
    issues = sorted(issues, key=lambda item: order.get(str(item.get("severity")), 3))

    system_prompt = (
        "你是一名面向大学生的校园健身教练，说话直接具体。你会收到姿态识别系统算出的动作报告，"
        "报告里已经给出每个问题的原因（为什么）和候选纠正练习（纠正练习）。请逐条输出纠正建议："
        "每个问题一段，先一句话说清错在哪，再给具体的纠正方法（可直接采用报告里的纠正练习，也可补充），"
        "最后一句点明下次训练怎么调整。不要复述角度数字，数字只在解释原因时一句话带过。"
        "总长控制在 250 字以内，用短横线分点，不要用 Markdown 标题和代码块，不要下诊断结论。"
    )
    user_prompt = json.dumps(
        {
            "结论": verdict.get("summary"),
            "问题": [
                {
                    "名称": item["name"],
                    "依据": item["evidence"],
                    "为什么": item.get("why", ""),
                    "纠正练习": [
                        {"名称": fix["name"], "做法": fix["how"]}
                        for fix in (item.get("fixes") or [])
                    ],
                }
                for item in issues
            ],
        },
        ensure_ascii=False,
    )
    text = llm_chat(system_prompt, user_prompt)
    if text:
        return text, "大模型"

    lines: List[str] = []
    for item in issues:
        lines.append(f"【{item['name']}】{item.get('why', '')}")
        fixes = item.get("fixes") or []
        if fixes:
            how = "；".join(f"{fix['name']}：{fix['how']}" for fix in fixes[:2])
            lines.append(f"→ 怎么改：{how}")
    if not lines:
        return "这次动作没有检出明显问题，保持现在的节奏。", "规则"
    return "\n".join(lines), "规则"


def _extract_json(text: str) -> Optional[Any]:
    """从大模型回复里提取 JSON 对象：去掉代码块围栏，失败再正则抓首个 {…}。"""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\s*", "", cleaned)
        cleaned = re.sub(r"```\s*$", "", cleaned).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, re.S)
        if not match:
            return None
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return None


def judge_free_exercise(exercise_name: str, summary_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """自由动作的通用质量判定：几何层给次数与量程，大模型判动作质量。

    返回 {"issues": [...], "summary": "..."}，或 None（没配 Key / 调用失败）。
    issues 每项含 name / short / severity / why / fixes / evidence，key 恒为空字符串。
    """
    if not llm_settings():
        return None
    system_prompt = (
        "你是面向大学生的校园健身教练。你会收到一段健身动作视频的几何量程数据"
        "（主关节角度的最小/最大/均值、完成次数等），请据此判断动作质量并给纠错建议。"
        "只输出 JSON，不要输出任何解释、Markdown 或代码块围栏。JSON 结构必须是："
        '{"summary": "一句话总评", "issues": [{"name": "问题名", "short": "短标签", '
        '"severity": "high|medium|low", "why": "为什么这是问题", '
        '"fixes": [{"name": "纠正方法", "how": "具体做法"}], "evidence": "数据依据"}]}。'
        "没有明显问题就输出空 issues。问题最多 3 条，按严重程度从高到低排列，"
        "总结与每条建议都要具体、口语化、能直接照做，总长控制在 200 字以内。"
    )
    user_prompt = json.dumps({"动作": exercise_name, "数据": summary_data}, ensure_ascii=False)
    text = llm_chat(system_prompt, user_prompt, timeout=60)
    if not text:
        return None
    parsed = _extract_json(text)
    if not isinstance(parsed, dict):
        return None

    issues: List[Dict[str, Any]] = []
    for item in parsed.get("issues") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        severity = str(item.get("severity") or "medium").strip().lower()
        if severity not in ("high", "medium", "low"):
            severity = "medium"
        fixes = [
            {"name": str(fix.get("name") or "纠正方法"), "how": str(fix.get("how") or "")}
            for fix in (item.get("fixes") or [])
            if isinstance(fix, dict)
        ]
        issues.append(
            {
                "key": "",
                "name": name,
                "short": str(item.get("short") or name),
                "severity": severity,
                "why": str(item.get("why") or ""),
                "fixes": fixes,
                "evidence": str(item.get("evidence") or ""),
            }
        )

    result: Dict[str, Any] = {"issues": issues[:3]}
    summary = str(parsed.get("summary") or "").strip()
    if summary:
        result["summary"] = summary
    return result


# 体态问题类型 -> 与它相关的动作类别（用于"静态体态 + 动态纠错"联动）
_POSTURE_POSE_RULES: Dict[str, Dict[str, Any]] = {
    "骨盆前倾": {
        "exercises": {"squat", "deadlift"},
        "risk": "深蹲/硬拉时容易用腰部代偿、弓腰，加重骨盆前倾",
        "hint": "检测到你存在{type}，做{label}时容易腰部代偿，建议先做 3 组针对性矫正热身再开始。",
    },
    "骨盆后倾": {
        "exercises": {"squat", "deadlift"},
        "risk": "深蹲/硬拉时下背容易过度弯曲，蹲不到位",
        "hint": "检测到你存在{type}，做{label}时下背容易弯曲，建议先做 3 组针对性矫正热身再开始。",
    },
    "圆肩": {
        "exercises": {"bench_press", "pushup", "pullup"},
        "risk": "推/拉类动作里肩胛不稳、容易耸肩",
        "hint": "检测到你存在{type}，做{label}时肩胛容易不稳，建议先做 3 组肩胛激活热身再开始。",
    },
    "圆肩驼背": {
        "exercises": {"bench_press", "pushup", "pullup"},
        "risk": "推/拉类动作里肩胛不稳、容易耸肩",
        "hint": "检测到你存在{type}，做{label}时肩胛容易不稳，建议先做 3 组肩胛激活热身再开始。",
    },
}

_EXERCISE_LABELS = {
    "squat": "深蹲",
    "deadlift": "硬拉",
    "bench_press": "卧推",
    "pushup": "俯卧撑",
    "pullup": "引体向上",
}

# 体态问题 -> 播报里的一句话保护建议（口语化）
_VOICE_ADVICE = {
    "骨盆前倾": "请注意保护腰椎，收紧核心",
    "骨盆后倾": "请注意保护腰椎，保持背部中立",
    "圆肩": "注意肩胛收紧，别耸肩",
    "圆肩驼背": "注意挺胸收肩胛，别耸肩",
}


def posture_pose_note(posture_issues: List[Dict[str, Any]], exercise: str) -> Optional[Dict[str, Any]]:
    """把静态体态报告和动态动作自检打通。

    若用户最近的体态问题与当前动作相关（如骨盆前倾 × 深蹲），返回一句联动提示 +
    推荐的热身动作；不相关则返回 None。
    """
    if not posture_issues or exercise not in _EXERCISE_LABELS:
        return None
    label = _EXERCISE_LABELS[exercise]

    for issue in posture_issues:
        issue_type = str(issue.get("type") or "")
        rule = _POSTURE_POSE_RULES.get(issue_type)
        if rule is None or exercise not in rule["exercises"]:
            continue
        warmup = posture_rules.ISSUE_MAP.get(issue_type, [])[:2]
        return {
            "text": rule["hint"].format(type=issue_type, label=label),
            "risk": rule["risk"],
            "warmup": warmup,
            "type": issue_type,
            "voice": f"结合你的体态档案，你存在{issue_type}，{_VOICE_ADVICE.get(issue_type, '请多加注意')}。",
        }
    return None
