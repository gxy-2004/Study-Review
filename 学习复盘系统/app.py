# -*- coding: utf-8 -*-
"""AI 学习复盘秘书 · Flask 后端。

运行：
    python app.py
"""
import copy
import datetime
import json
import os
import re
import threading
import uuid

from flask import Flask, jsonify, render_template, request

from services.ai import (
    fallback_monthly_review,
    fallback_schedule,
    fallback_weekly_commentary,
    fallback_weekly_review,
    llm_chat,
    llm_configured,
    monthly_prompt,
    schedule_prompt,
    schedule_revision_prompt,
    weekly_commentary_prompt,
    weekly_prompt,
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def load_dotenv(path):
    """极简 .env 读取，避免额外依赖。"""
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


load_dotenv(os.path.join(BASE_DIR, ".env"))

DATA_DIR = os.path.join(BASE_DIR, os.environ.get("DATA_DIR", "data"))
STORE_PATH = os.path.join(DATA_DIR, "store.json")

app = Flask(__name__)
app.config["JSON_AS_ASCII"] = False

DEFAULT_STORE = {
    "year_goal": None,
    "month_goals": {},
    "weekly_reviews": [],
    "schedules": [],
    "monthly_reviews": [],
    "self_evals": {},
}

_lock = threading.Lock()


# ---------------------------------------------------------------- 基础工具

def now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def ok(data=None, **extra):
    resp = {"ok": True}
    if isinstance(data, dict):
        resp.update(data)
    resp.update(extra)
    return jsonify(resp)


def err(message, code=400):
    return jsonify({"ok": False, "error": message}), code


def load_store():
    os.makedirs(DATA_DIR, exist_ok=True)
    if not os.path.exists(STORE_PATH):
        return copy.deepcopy(DEFAULT_STORE)
    try:
        with open(STORE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    store = copy.deepcopy(DEFAULT_STORE)
    store.update(data)
    return store


def save_store(data):
    os.makedirs(DATA_DIR, exist_ok=True)
    with _lock:
        tmp = STORE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, STORE_PATH)


def parse_date(value):
    return datetime.date.fromisoformat(value)


def week_key_from_date(value):
    d = parse_date(value)
    iso = d.isocalendar()
    return "{}-W{:02d}".format(iso[0], iso[1])


def week_label_from_key(key):
    year, week = key.split("-W")
    return "{}年 第{:02d}周".format(year, int(week))


def month_key_from_date(value):
    d = parse_date(value)
    return "{}-{:02d}".format(d.year, d.month)


def add_weeks(key, delta):
    year, week = key.split("-W")
    d = datetime.date.fromisocalendar(int(year), int(week), 1) + datetime.timedelta(weeks=delta)
    iso = d.isocalendar()
    return "{}-W{:02d}".format(iso[0], iso[1])


def generate_with_fallback(build_prompt, fallback_fn, *args, temperature=0.7, max_tokens=3000):
    """返回 (内容, ai_note)。ai_note 为空表示 AI 生成成功。

    失败时自动重试一次，仍失败才降级到本地模板。
    """
    if llm_configured():
        messages = build_prompt(*args)
        content, error = llm_chat(messages, temperature=temperature, max_tokens=max_tokens)
        if content:
            return content, ""
        # 推理模型（deepseek-v4-*）思考会消耗大量预算：空内容时用更大的 max_tokens 重试一次
        retry_tokens = min(int(max_tokens) + 8000, 32000)
        content, error2 = llm_chat(messages, temperature=temperature, max_tokens=retry_tokens)
        if content:
            return content, ""
        return fallback_fn(*args), error2 or error or "AI 生成失败，已使用本地模板。"
    return fallback_fn(*args), "未配置 LLM_API_KEY，已使用本地模板生成。"


def remove_by_id(items, item_id):
    return [item for item in items if item.get("id") != item_id]


def extract_hours(text):
    """从 AI 生成文本中解析其自报的一周课程总时长（小时）；解析失败返回 None。"""
    text = text or ""
    m = re.search(r"一周课程总时长[：:]\s*([\d.]+)", text)
    if m:
        return float(m.group(1))
    # 兼容核算表合计行：| 合计 | | | | 22.5 |
    m = re.search(r"\|\s*合计\s*\|[^|]*\|[^|]*\|[^|]*\|\s*([\d.]+)\s*\|", text)
    if m:
        return float(m.group(1))
    return None


DAY_NAMES = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
PERIOD_NAMES = ["上午", "下午", "晚上"]


def _parse_availability(text):
    """解析「时段可用性表」区块，返回 {(周X, 时段): '可用'/'不可用'}。"""
    m = re.search(r"##\s*时段可用性表(.*?)(?=\n##\s*周一)", text, re.S)
    block = m.group(1) if m else ""
    status = {}
    for line in block.splitlines():
        mm = re.search(r"^\s*(周[一二三四五六日])\s*(上午|下午|晚上)\s*[：:]\s*(可用|不可用)", line)
        if mm:
            status[(mm.group(1), mm.group(2))] = mm.group(3)
    return status


def _parse_daily_rest(text):
    """解析每日安排区块中写有「休息/不可用」的时段，返回 {(周X, 时段)} 集合。"""
    rests = set()
    parts = re.split(r"##\s*(周[一二三四五六日])", text)
    for i in range(1, len(parts) - 1, 2):
        day, content = parts[i], parts[i + 1]
        content = re.split(r"\n##\s*", content)[0]
        for line in content.splitlines():
            mm = re.search(r"^\s*(?:周[一二三四五六日]\s*|-\s*)?(上午|下午|晚上)\s*[：:]\s*(.+)$", line)
            if mm and ("休息" in mm.group(2) or "不可用" in mm.group(2)):
                rests.add((day, mm.group(1)))
    return rests


def schedule_conflicts(text):
    """检测「可用性表标记可用，但每日安排却写了休息」的冲突时段。"""
    avail = _parse_availability(text)
    rests = _parse_daily_rest(text)
    return sorted(
        [(day, period) for (day, period) in rests if avail.get((day, period)) == "可用"],
        key=lambda x: (DAY_NAMES.index(x[0]) if x[0] in DAY_NAMES else 9, PERIOD_NAMES.index(x[1]) if x[1] in PERIOD_NAMES else 9),
    )


# ---------------------------------------------------------------- 页面

@app.route("/")
def index():
    return render_template("index.html")


@app.get("/api/state")
def get_state():
    store = load_store()
    store["ai_configured"] = llm_configured()
    return jsonify(store)


# ---------------------------------------------------------------- 年目标 / 月目标

@app.post("/api/year_goal")
def set_year_goal():
    payload = request.get_json(silent=True) or {}
    year = str(payload.get("year", "")).strip()
    content = str(payload.get("content", "")).strip()
    if not year or not content:
        return err("年份和年目标内容不能为空")
    store = load_store()
    store["year_goal"] = {"year": year, "content": content, "updated_at": now()}
    save_store(store)
    return ok({"year_goal": store["year_goal"]})


@app.post("/api/month_goal")
def set_month_goal():
    payload = request.get_json(silent=True) or {}
    month = str(payload.get("month", "")).strip()
    content = str(payload.get("content", "")).strip()
    if not month or not content:
        return err("月份和月目标内容不能为空")
    try:
        parse_date(month + "-01")
    except ValueError:
        return err("月份格式不正确，应为 YYYY-MM")
    store = load_store()
    store["month_goals"][month] = {"month": month, "content": content, "updated_at": now()}
    save_store(store)
    return ok({"month_goals": store["month_goals"]})


# ---------------------------------------------------------------- 周复盘

@app.post("/api/week_reviews")
def create_week_review():
    payload = request.get_json(silent=True) or {}
    week_start = str(payload.get("week_start", "")).strip()
    fields = payload.get("fields") or {}
    if not isinstance(fields, dict):
        fields = {}
    if not week_start:
        return err("请选择本周的日期")
    try:
        week_key = week_key_from_date(week_start)
        month = month_key_from_date(week_start)
    except ValueError:
        return err("日期格式不正确，应为 YYYY-MM-DD")
    week_label = week_label_from_key(week_key)

    store = load_store()
    year_goal = (store.get("year_goal") or {}).get("content", "")
    month_goal = (store.get("month_goals") or {}).get(month, {}).get("content", "")

    text, note1 = generate_with_fallback(
        weekly_prompt, fallback_weekly_review,
        fields, week_label, year_goal, month_goal,
        temperature=0.7, max_tokens=12000,
    )

    prev_keys = [add_weeks(week_key, -1), add_weeks(week_key, -2)]
    reviews_by_key = {r.get("week_key"): r for r in store.get("weekly_reviews", [])}
    prev_reviews = [reviews_by_key[k] for k in prev_keys if k in reviews_by_key]

    comment, note2 = generate_with_fallback(
        weekly_commentary_prompt, fallback_weekly_commentary,
        text, prev_reviews, year_goal, month_goal,
        temperature=0.6, max_tokens=12000,
    )

    ai_note = note1 or note2
    review = {
        "id": uuid.uuid4().hex[:12],
        "week_key": week_key,
        "month": month,
        "week_label": week_label,
        "week_start": week_start,
        "fields": fields,
        "generated": text,
        "commentary": comment,
        "ai_mode": "ai" if not ai_note else "local",
        "ai_note": ai_note,
        "created_at": now(),
    }

    store["weekly_reviews"] = [r for r in store["weekly_reviews"] if r.get("week_key") != week_key]
    store["weekly_reviews"].append(review)
    store["weekly_reviews"].sort(key=lambda r: r.get("week_key", ""))
    save_store(store)
    return ok({"review": review, "reviews": store["weekly_reviews"]})


@app.delete("/api/week_reviews/<item_id>")
def delete_week_review(item_id):
    store = load_store()
    store["weekly_reviews"] = remove_by_id(store["weekly_reviews"], item_id)
    save_store(store)
    return ok({"reviews": store["weekly_reviews"]})


# ---------------------------------------------------------------- 下周任务安排

@app.post("/api/schedules")
def create_schedule():
    payload = request.get_json(silent=True) or {}
    week_start = str(payload.get("week_start", "")).strip()
    week_goal = str(payload.get("week_goal", "")).strip()
    mode = str(payload.get("mode", "count")).strip()
    study_days = int(payload.get("study_days") or 6)
    note = str(payload.get("note", "")).strip()

    if not week_start or not week_goal:
        return err("请填写日期和周目标")
    try:
        week_key = week_key_from_date(week_start)
        month = month_key_from_date(week_start)
    except ValueError:
        return err("日期格式不正确，应为 YYYY-MM-DD")

    course_count = int(payload.get("course_count") or 0)
    hours_per_course = float(payload.get("hours_per_course") or 1.5)
    total_hours = float(payload.get("total_hours") or 0)

    if mode == "count":
        if course_count <= 0:
            return err("请填写课程总数（节）")
        if hours_per_course <= 0:
            return err("请填写每节课程时长（小时）")
        total_minutes = int(course_count * hours_per_course * 60)
    elif mode == "duration":
        if total_hours <= 0:
            return err("请填写课程总时长（小时）")
        total_minutes = int(total_hours * 60)
    else:
        return err("课程计量方式不正确")

    if not 1 <= study_days <= 7:
        return err("学习天数应在 1~7 之间")

    plan = {
        "week_label": week_label_from_key(week_key),
        "week_goal": week_goal,
        "mode": mode,
        "course_count": course_count,
        "hours_per_course": hours_per_course,
        "total_hours": total_hours,
        "total_minutes": total_minutes,
        "study_days": study_days,
        "note": note,
    }
    text, ai_note = generate_with_fallback(
        schedule_prompt, fallback_schedule, plan,
        temperature=0.3, max_tokens=20000,
    )

    # 二次修订：程序检测三类问题（总时长不足/超出、可用时段被误写休息），让 AI 逐条修正后重排
    if not ai_note and text and total_minutes > 0:
        target_hours = total_minutes / 60.0
        issues = []
        found_hours = extract_hours(text)
        if found_hours is not None and found_hours + 0.5 < target_hours:
            issues.append(
                "总课程时长不足：目标 {} 小时，本次只安排了 {} 小时，还差约 {} 小时，请把差额补到未被限制的可用时段（可把单个可用时段扩到 2~3 小时），最终核算必须达到 {} 小时。".format(
                    "%.1f" % target_hours,
                    "%.1f" % found_hours,
                    "%.1f" % max(0.0, target_hours - found_hours),
                    "%.1f" % target_hours,
                )
            )
        elif found_hours is not None and found_hours > target_hours + 0.5:
            issues.append(
                "总课程时长超出：目标 {} 小时，本次安排了 {} 小时，多出约 {} 小时。请在保持「时段可用性表」与受限时段不变的前提下，削减部分时段的冗余时长或删减重复性任务，总量必须回落到 {} 小时（误差不超过 0.5 小时）；不要把每天的时段都排满，总量一够就停止加任务。".format(
                    "%.1f" % target_hours,
                    "%.1f" % found_hours,
                    "%.1f" % (found_hours - target_hours),
                    "%.1f" % target_hours,
                )
            )
        for day, period in schedule_conflicts(text):
            issues.append(
                "时段冲突：可用性表里「{} {}」是可用，但每日安排却写成了休息/不可用；补充信息没有限制该时段，请把它改成具体课程任务。".format(
                    day, period
                )
            )
        if issues:
            revision_messages = schedule_revision_prompt(plan, text, target_hours, issues)
            revised, _rev_err = llm_chat(revision_messages, temperature=0.3, max_tokens=20000)
            if revised:
                revised_ok = not schedule_conflicts(revised)
                revised_hours = extract_hours(revised)
                hours_ok = revised_hours is not None and abs(revised_hours - target_hours) <= 0.5
                original_conflicts = bool(schedule_conflicts(text))
                # 记录是否达标（不足或超出都在 0.5h 内）
                if hours_ok:
                    # 时长达标即采用
                    text = revised
                elif revised_hours is not None and found_hours is not None:
                    # 没到 0.5h 精度，但只要更接近目标也采用
                    if abs(revised_hours - target_hours) < abs(found_hours - target_hours) or (revised_ok and not original_conflicts and found_hours is not None):
                        text = revised

    schedule = {
        "id": uuid.uuid4().hex[:12],
        "week_key": week_key,
        "month": month,
        "week_label": plan["week_label"],
        "week_start": week_start,
        "plan": plan,
        "generated": text,
        "ai_mode": "ai" if not ai_note else "local",
        "ai_note": ai_note,
        "created_at": now(),
    }
    store = load_store()
    store["schedules"] = [s for s in store["schedules"] if s.get("week_key") != week_key]
    store["schedules"].append(schedule)
    store["schedules"].sort(key=lambda s: s.get("week_key", ""))
    save_store(store)
    return ok({"schedule": schedule, "schedules": store["schedules"]})


@app.delete("/api/schedules/<item_id>")
def delete_schedule(item_id):
    store = load_store()
    store["schedules"] = remove_by_id(store["schedules"], item_id)
    save_store(store)
    return ok({"schedules": store["schedules"]})


# ---------------------------------------------------------------- 月复盘

@app.post("/api/month_reviews")
def create_month_review():
    payload = request.get_json(silent=True) or {}
    month = str(payload.get("month", "")).strip()
    self_eval = payload.get("self_eval") or {}
    if not isinstance(self_eval, dict):
        self_eval = {}
    if not month:
        return err("请选择复盘月份")
    try:
        parse_date(month + "-01")
    except ValueError:
        return err("月份格式不正确，应为 YYYY-MM")

    store = load_store()
    week_reviews = [r for r in store.get("weekly_reviews", []) if r.get("month") == month]
    week_reviews.sort(key=lambda r: r.get("week_key", ""))
    if len(week_reviews) < 4:
        return err("{} 只有 {} 篇周复盘，请先完成 4 周周复盘后再生成月复盘。".format(month, len(week_reviews)))

    store["self_evals"][month] = {**self_eval, "updated_at": now()}
    year_goal = (store.get("year_goal") or {}).get("content", "")
    month_goal = (store.get("month_goals") or {}).get(month, {}).get("content", "")

    text, ai_note = generate_with_fallback(
        monthly_prompt, fallback_monthly_review,
        month, week_reviews, month_goal, year_goal, self_eval,
        temperature=0.6, max_tokens=20000,
    )

    review = {
        "id": uuid.uuid4().hex[:12],
        "month": month,
        "self_eval": self_eval,
        "week_keys": [r.get("week_key") for r in week_reviews],
        "generated": text,
        "ai_mode": "ai" if not ai_note else "local",
        "ai_note": ai_note,
        "created_at": now(),
    }
    store["monthly_reviews"] = [m for m in store["monthly_reviews"] if m.get("month") != month]
    store["monthly_reviews"].append(review)
    store["monthly_reviews"].sort(key=lambda m: m.get("month", ""))
    save_store(store)
    return ok({"review": review, "monthly_reviews": store["monthly_reviews"]})


@app.delete("/api/month_reviews/<item_id>")
def delete_month_review(item_id):
    store = load_store()
    store["monthly_reviews"] = remove_by_id(store["monthly_reviews"], item_id)
    save_store(store)
    return ok({"monthly_reviews": store["monthly_reviews"]})


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
