# -*- coding: utf-8 -*-
"""AI 学习复盘秘书 · 纯后端 API 版本（app.py 的 API-only 副本，不覆盖原文件）。

与原 app.py 的区别：
- 移除了网页渲染（不再需要 templates / static），只提供 JSON API；
- 根路径 / 返回接口清单；
- 默认端口 5001（可用环境变量 PORT 修改）；
- 已开启跨域支持（CORS），便于前端单独部署时调用。

运行：
    python app1.py
"""
import copy
import datetime
import json
import os
import threading
import uuid

from flask import Flask, jsonify, request

from services.ai import (
    fallback_monthly_review,
    fallback_schedule,
    fallback_weekly_commentary,
    fallback_weekly_review,
    llm_chat,
    llm_configured,
    monthly_prompt,
    schedule_prompt,
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
        content, error2 = llm_chat(messages, temperature=temperature, max_tokens=max_tokens)
        if content:
            return content, ""
        return fallback_fn(*args), error2 or error or "AI 生成失败，已使用本地模板。"
    return fallback_fn(*args), "未配置 LLM_API_KEY，已使用本地模板生成。"


def remove_by_id(items, item_id):
    return [item for item in items if item.get("id") != item_id]


# ---------------------------------------------------------------- 根路径（纯 API）

@app.route("/", methods=["GET", "OPTIONS"])
def api_index():
    """纯后端 API：根路径返回接口清单。"""
    if request.method == "OPTIONS":
        return ("", 204)
    endpoints = [
        {"method": "GET", "path": "/api/state", "desc": "读取全部状态（年/月目标、周复盘、计划、月复盘等）"},
        {"method": "POST", "path": "/api/year_goal", "desc": "保存年目标 {year, content}"},
        {"method": "POST", "path": "/api/month_goal", "desc": "保存月目标 {month, content}"},
        {"method": "POST", "path": "/api/week_reviews", "desc": "生成并存档周复盘 {week_start, fields}"},
        {"method": "DELETE", "path": "/api/week_reviews/<id>", "desc": "删除周复盘"},
        {"method": "POST", "path": "/api/schedules", "desc": "生成并存档下周计划（week_goal / mode / course_count / hours_per_course / total_hours / study_days / note）"},
        {"method": "DELETE", "path": "/api/schedules/<id>", "desc": "删除下周计划"},
        {"method": "POST", "path": "/api/month_reviews", "desc": "生成并存档月复盘 {month, self_eval}（需该月已满 4 周周复盘）"},
        {"method": "DELETE", "path": "/api/month_reviews/<id>", "desc": "删除月复盘"},
    ]
    return jsonify({"name": "像素学伴 · 纯后端 API", "version": "1.0", "endpoints": endpoints})


# CORS：允许任意来源调用，方便前后端分离部署
@app.after_request
def add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, DELETE, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    return response


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
        temperature=0.7, max_tokens=3000,
    )

    prev_keys = [add_weeks(week_key, -1), add_weeks(week_key, -2)]
    reviews_by_key = {r.get("week_key"): r for r in store.get("weekly_reviews", [])}
    prev_reviews = [reviews_by_key[k] for k in prev_keys if k in reviews_by_key]

    comment, note2 = generate_with_fallback(
        weekly_commentary_prompt, fallback_weekly_commentary,
        text, prev_reviews, year_goal, month_goal,
        temperature=0.6, max_tokens=2000,
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
        temperature=0.7, max_tokens=4000,
    )

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
        temperature=0.6, max_tokens=4000,
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
    # 纯后端 API 默认 5001 端口，避免与 app.py（5000）冲突；可用环境变量 PORT 覆盖
    port = int(os.environ.get("PORT", 5001))
    app.run(host="127.0.0.1", port=port, debug=False)
