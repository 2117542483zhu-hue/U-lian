"""把动作自检的 JSON 报告交给大模型，生成给学生的训练建议。

这一步才是作品里"大模型与智能体"的部分：数值由规则算出，模型只负责解释与安排训练。

使用前先设置 API Key，例如：
    set DEEPSEEK_API_KEY=你的key
    python llm_explain.py "../输出结果/深蹲_报告.json"
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

PROVIDERS = [
    {
        "name": "DeepSeek",
        "env": "DEEPSEEK_API_KEY",
        "url": "https://api.deepseek.com/v1/chat/completions",
        "model": "deepseek-chat",
    },
    {
        "name": "通义千问（DashScope 兼容模式）",
        "env": "DASHSCOPE_API_KEY",
        "url": "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
        "model": "qwen-plus",
    },
    {
        "name": "自定义",
        "env": "LLM_API_KEY",
        "url": os.environ.get("LLM_BASE_URL", ""),
        "model": os.environ.get("LLM_MODEL", ""),
    },
]

CONFIG_PATH = Path(__file__).resolve().parent.parent / "配置" / "ai配置.txt"


def key_from_config(env_name: str) -> str:
    """读取 配置\ai配置.txt 里填的 Key（网页上填的 Key 也保存在这里）。"""
    if not CONFIG_PATH.exists():
        return ""
    field = {
        "DEEPSEEK_API_KEY": "deepseek_key",
        "DASHSCOPE_API_KEY": "dashscope_key",
        "LLM_API_KEY": "",
    }.get(env_name, "")
    if not field:
        return ""
    for line in CONFIG_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith(field + "="):
            return line.split("=", 1)[1].strip()
    return ""

SYSTEM_PROMPT = """你是一名面向大学生的校园健身教练，说话直接、具体、不夸张。
你会收到一份由姿态识别系统算出的深蹲动作报告，报告里的数字是准确的，你必须以它为依据。
请按下面的结构回答，总量控制在 200 字以内：
1. 一句话结论：这次动作最需要改的是哪一点。
2. 解释原因：为什么这一点重要，用学生能听懂的话。
3. 接下来两次训练怎么练：给出具体动作、组数次数和一个判断标准。
要求：不要使用医疗术语，不要下诊断结论；如果报告提示画面可信度不足，就只说明需要重新拍摄并给出拍摄要点。"""


def build_user_prompt(report: dict) -> str:
    verdict = report.get("verdict", {})
    return json.dumps(
        {
            "视频": Path(report.get("video", "")).name,
            "拍摄角度": report.get("view"),
            "结论状态": verdict.get("status"),
            "结论摘要": verdict.get("summary"),
            "问题列表": [
                {"名称": item.get("name"), "严重度": item.get("severity"), "依据": item.get("evidence")}
                for item in verdict.get("issues", [])
            ],
            "关键指标": verdict.get("metrics", {}),
        },
        ensure_ascii=False,
    )


def call_llm(provider: dict, api_key: str, user_prompt: str, timeout: int = 60) -> str:
    payload = json.dumps(
        {
            "model": provider["model"],
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.4,
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        provider["url"],
        data=payload,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = json.loads(response.read().decode("utf-8"))
    return data["choices"][0]["message"]["content"].strip()


def main() -> int:
    if len(sys.argv) < 2:
        print("用法：python llm_explain.py 报告.json")
        return 2
    report_path = Path(sys.argv[1]).expanduser().resolve()
    if not report_path.exists():
        print(f"找不到报告文件：{report_path}")
        return 2

    report = json.loads(report_path.read_text(encoding="utf-8"))
    user_prompt = build_user_prompt(report)

    for provider in PROVIDERS:
        api_key = os.environ.get(provider["env"], "").strip() or key_from_config(provider["env"])
        if not api_key or not provider["url"] or not provider["model"]:
            continue
        print(f"使用模型：{provider['name']} / {provider['model']}\n")
        try:
            print(call_llm(provider, api_key, user_prompt))
            return 0
        except urllib.error.HTTPError as exc:
            print(f"调用失败，HTTP {exc.code}：{exc.read().decode('utf-8', 'ignore')[:200]}")
        except Exception as exc:  # noqa: BLE001
            print(f"调用失败：{type(exc).__name__} {exc}")

    print("没有检测到可用的 API Key，下面先给出规则生成的建议：\n")
    verdict = report.get("verdict", {})
    print(verdict.get("summary", ""))
    for item in verdict.get("issues", []):
        print(f"\n问题：{item['name']}")
        print(f"依据：{item['evidence']}")
        for fix in item.get("fixes", []):
            print(f"建议：{fix['name']}。{fix['how']}")
    print("\n设置环境变量后再次运行即可让大模型改写这段内容，例如：set DEEPSEEK_API_KEY=你的key")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
