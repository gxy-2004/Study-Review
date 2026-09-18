# -*- coding: utf-8 -*-
"""LLM 调用（默认智谱AI GLM）与本地模板生成。

- 配置了 LLM_API_KEY 时走 OpenAI 兼容接口（智谱AI：https://open.bigmodel.cn/api/paas/v4）。
- 未配置或调用失败时自动降级为本地模板，保证系统离线可用。
"""
import math
import os
import re

import requests


# ---------------------------------------------------------------- 配置

def _env(key, default=""):
    return os.environ.get(key, default).strip()


def llm_configured():
    return bool(_env("LLM_API_KEY"))


# ---------------------------------------------------------------- LLM 调用

def llm_chat(messages, temperature=0.7, max_tokens=3000):
    """调用 OpenAI 兼容 chat/completions 接口。成功返回 (文本, None)，失败返回 (None, 错误信息)。"""
    key = _env("LLM_API_KEY")
    if not key:
        return None, "未配置 LLM_API_KEY，已使用本地模板生成。"

    base = _env("LLM_BASE_URL", "https://open.bigmodel.cn/api/paas/v4").rstrip("/")
    url = base + "/chat/completions"
    model = _env("LLM_MODEL", "glm-4-flash")

    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    # 提速：DeepSeek v4 默认关闭“思考”（thinking），单次生成从 2~6 分钟降到 10~40 秒；
    # 若希望模型深度推理，可在 .env 设置 LLM_THINKING=on
    if model.lower().startswith("deepseek") and _env("LLM_THINKING", "off").lower() != "on":
        payload["thinking"] = {"type": "disabled"}
    headers = {
        "Authorization": "Bearer " + key,
        "Content-Type": "application/json",
    }

    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=180)
        if resp.status_code != 200:
            return None, "AI 接口返回 {}：{}".format(resp.status_code, resp.text[:300])
        data = resp.json()
        message = data["choices"][0].get("message") or {}
        content = (message.get("content") or "").strip()
        if not content:
            return None, "AI 返回了空内容（可能是 max_tokens 不足或模型未完成回答）"
        return content, None
    except Exception as exc:  # noqa: BLE001
        return None, "AI 调用失败：{}".format(exc)


# ---------------------------------------------------------------- 周复盘 Prompt

WEEKLY_SYSTEM = (
    "你是「像素学伴」AI 学习复盘秘书，擅长把零散记录整理成结构化、可执行的复盘文本。"
    "使用简体中文输出，语言克制、具体，不编造信息。"
)

WEEKLY_SECTION_TITLES = {
    "01": "本周主要事件回顾。",
    "02": "工作经验总结。",
    "03": "对标反思。",
    "04": "价值观梳理。",
    "05": "每周反省。",
    "06": "检视年度计划落地情况。",
    "07": "下周计划。",
}


def weekly_prompt(fields, week_label, year_goal, month_goal):
    lines = [
        "请根据用户填写的本周信息，严格按照下面 7 个小节生成《周复盘》。",
        "每节 80~150 字；用户未填写的内容要温和提示补充或留白，不要编造事实。",
        "",
        "输出格式（保留编号与句号）：",
        "01 本周主要事件回顾。",
        "02 工作经验总结。",
        "03 对标反思。",
        "04 价值观梳理。",
        "05 每周反省。",
        "06 检视年度计划落地情况。",
        "07 下周计划。",
        "",
        "本周时间：{}".format(week_label),
        "年度目标：{}".format(year_goal or "（未设置）"),
        "本月目标：{}".format(month_goal or "（未设置）"),
        "",
        "用户填写的本周信息：",
    ]
    for key, title in WEEKLY_SECTION_TITLES.items():
        lines.append("- {} {}：{}".format(key, title.rstrip("。"), fields.get(key) or "（未填写）"))
    lines.append("")
    lines.append("请直接输出完整的周复盘正文，不要额外解释。")
    return [
        {"role": "system", "content": WEEKLY_SYSTEM},
        {"role": "user", "content": "\n".join(lines)},
    ]


def weekly_commentary_prompt(current_text, prev_reviews, year_goal, month_goal):
    prev_lines = []
    for i, review in enumerate(prev_reviews, 1):
        prev_lines.append(
            "【前 {} 周 · {}】\n{}".format(
                i, review.get("week_label", ""), (review.get("generated") or "")[:1200]
            )
        )
    if not prev_lines:
        prev_lines.append("（暂无前两周复盘存档）")

    user = (
        "你是复盘教练。请结合本周复盘与前两周复盘进行对比点评，输出以下四部分：\n"
        "【本周亮点】\n"
        "【与前两周对比】\n"
        "【综合评价】\n"
        "【调整建议与实施建议】\n"
        "\n"
        "要求：调整建议和实施建议要具体可执行；结合年度目标「{}」与本月目标「{}」给出方向性建议。\n"
        "\n"
        "本周复盘：\n{}\n"
        "\n"
        "前两周复盘：\n{}\n"
        "\n"
        "请直接输出点评正文。"
    ).format(
        year_goal or "未设置",
        month_goal or "未设置",
        (current_text or "")[:2500],
        "\n".join(prev_lines),
    )
    return [
        {"role": "system", "content": "你是「像素学伴」的复盘教练，点评客观、具体、可执行。使用简体中文。"},
        {"role": "user", "content": user},
    ]


def fmt_hours(minutes):
    """把分钟格式化为小时文本，例如 90 -> 1.5 小时、120 -> 2 小时。"""
    hours = minutes / 60.0
    if abs(hours - round(hours)) < 0.05:
        return "{} 小时".format(int(round(hours)))
    return "{} 小时".format(("%.1f" % hours).rstrip("0").rstrip("."))


# ---------------------------------------------------------------- 下周计划 Prompt

def schedule_prompt(plan):
    total_hours_display = fmt_hours(int(plan.get("total_minutes") or 0))
    study_days = max(1, int(plan.get("study_days") or 6))
    avg_hours = (int(plan.get("total_minutes") or 0) / 60.0) / study_days
    avg_desc = "{}".format(("%.1f" % avg_hours).rstrip("0").rstrip(".") or "0")
    if plan.get("mode") == "count":
        load_desc = "课程总数 {} 节，每节 {} 小时，合计约 {}".format(
            plan.get("course_count", 0),
            plan.get("hours_per_course", 1.5),
            total_hours_display,
        )
    else:
        load_desc = "课程总时长 {}".format(total_hours_display)

    user = (
        "你是学习规划师。请把用户的下周目标落实到周一到周日每天的详细任务安排。\n"
        "\n"
        "【硬性约束 · 范围仅限于补充信息中明确写出的内容】\n"
        "用户补充信息（原话）：\n"
        "{}\n"
        "\n"
        "判定规则：\n"
        "1. 只有补充信息里明确写出的内容才算硬性约束（哪一天/哪个时段不可用、原因是什么）。\n"
        "2. 补充信息未提到的内容一律不得当作约束：补充信息没说休息就不休息、没说某天有事就按普通学习日正常排课；不得自行添加补充信息没有的受限时段。\n"
        "3. 若补充信息没有内容，则 7 天 × 上午/下午/晚上 共 21 个时段全部可用，按课程量均衡分配。\n"
        "4. 时段对应关系（务必按此映射补充信息里的时间词）：「早上/上午」→ 上午时段（08:00-11:30）；「中午/下午/午后」→ 下午时段（14:00-17:30）；「晚上/晚间」→ 晚上时段（19:30-22:00）。\n"
        "\n"
        "【基础输入】\n"
        "- 周目标：{}\n"
        "- 课程量：{}（这就是一周内必须全部排完的课程总时长，安排完后一小时都不能少）\n"
        "- 可安排学习天数：{} 天（其余为休息/机动日）\n"
        "- 日均基准：约 {} 小时/天 = 课程量 ÷ 可安排学习天数（仅作分配参考，不是每天都要刚好这么多）\n"
        "\n"
        "【最重要原则 · 课程量守恒】\n"
        "补充信息占用的时段不能排课程，但这些时段原本应承担的课程时长必须全部转移分摊到其他天的「可用」时段里；把 7 天安排的课程时长相加，必须等于上面给出的课程量（误差不超过 0.5 小时）。\n"
        "宁可把其他天排得满一点、把机动日变成学习日，也不允许一周课程总量缩水。\n"
        "按日均基准排课时，多数学习日当天合计应接近或超过日均基准，把受限日少排的份额补到其他天；不要为了计划看起来轻松而把每天压到 3~4 小时——那样总量必然不达标。\n"
        "时段容量参考：上午（08:00-11:30）和下午（14:00-17:30）每个时段约可排 2.5~3 小时，晚上（19:30-22:00）约可排 2~2.5 小时；每个可用时段请尽量成块排到接近上限（如「视频 1.5 小时 + 笔记 0.5 小时 + 例题 0.5 小时」），不要每个时段只排 1~1.5 小时。\n"
        "\n"
        "【输出步骤 · 必须严格按三步走】\n"
        "\n"
        "第 1 步：输出「## 时段可用性表」。\n"
        "把补充信息逐条翻译到每一天：周一~周日各写一行，每行列出 上午 / 下午 / 晚上 三个时段的可用状态。\n"
        "- 未被补充信息影响的时段写「可用」。\n"
        "- 被补充信息占用的时段必须写「不可用：具体原因」，原因直接引用补充信息。\n"
        "- 参考格式（仅演示写法，请按你的补充信息完整判断 7 天，不可只写被影响的天）：\n"
        "  周一 上午:可用 | 下午:可用 | 晚上:可用\n"
        "  周三 上午:可用 | 下午:不可用（周三下午有课） | 晚上:可用\n"
        "  周日 上午:不可用（周日休息一天） | 下午:不可用（周日休息一天） | 晚上:不可用（周日休息一天）\n"
        "\n"
        "第 2 步：输出「## 周一」……「## 周日」的每日安排。\n"
        "- 每天只能在第 1 步标记为「可用」的时段安排课程任务；标记「不可用」的时段绝不允许出现任何学习任务，只能写「休息/不可用」。\n"
        "- 反过来也一样：凡是第 1 步标记为「可用」的时段，必须写出具体课程任务，禁止写成休息/不可用——不要因为当天本来要休息就顺带把可用时段也写成休息。\n"
        "- 每个可用时段写清：时段名称、课程时长（一律以小时为单位，如 1.5 小时、2 小时）、具体任务（如「完成《xxx》第2章视频 + 笔记」），不要只写「学习」。\n"
        "- 被补充信息挤掉的课程时长必须在此步就补到其他天的可用时段里（例如周五下午晚上休息，就把周五的课程挪到周六上午、周日等可用时段），保证总量守恒。\n"
        "- 总量守恒优先于“把时段排满”：全周合计必须正好接近课程量（如 35 小时），不要为了用满所有可用时段而把每天都排到 7~9 小时；每天合计应以日均基准（约 5 小时）为圆心，补课日最多 +2 小时，总量一够就不要再往时段里硬加任务。\n"
        "\n"
        "第 3 步：最后输出「## 时长核算」。\n"
        "注意：第 2 步的每日安排必须已经按目标课程量排版好（核算表只是把上面的安排汇总），严禁先排一个超额/不足的版本再附一段“需压缩/需补足”的说明；直接让核算合计等于目标课程量（误差不超过 0.5 小时）。\n"
        "用表格把第 2 步实际安排的课程时长按天合计（请按你的实际安排填写）：\n"
        "| 天 | 上午(小时) | 下午(小时) | 晚上(小时) | 当天合计 |\n"
        "|---|---|---|---|---|\n"
        "| 周一 | 1.5 | 1.5 | 1 | 4 |\n"
        "| 周二 | 1.5 | 0 | 1.5 | 3 |\n"
        "...（补齐周一到周日全部 7 行）\n"
        "| 合计 |  |  |  | 35 |\n"
        "表格后写判定结论（二选一）：\n"
        "- 合计达到课程量：写「一周课程总时长：xx 小时（已达标）」。\n"
        "- 合计不足：写「一周课程总时长：xx 小时，仍差 y 小时。本周可用时段容量不足以容纳全部课程，建议把课程量下调到约 xx 小时，或将部分课程顺延到下周」，此时不要重写全部计划，更绝不允许在受限时段里硬塞任务来凑时长。\n"
        "\n"
        "【输出前自查（必须执行）】\n"
        "- 可用性表里每个受限时段都标「不可用」了吗？\n"
        "- 每个「不可用」时段都没有排学习任务吗？\n"
        "- 核算合计等于课程量吗？若不足，是否已在核算结论中如实写出缺口与建议？（不得占用受限时段凑时长）\n"
        "\n"
        "结尾可附「## 一周弹性调度提示」，全部为建议性质。\n"
        "请直接输出完整内容。"
    ).format(
        plan.get("note") or "（无）",
        plan.get("week_goal", ""),
        load_desc,
        plan.get("study_days", 6),
        avg_desc,
    )
    return [
        {"role": "system", "content": "你是「像素学伴」的学习规划师。两条铁律必须同时满足：① 补充信息明确写出的受限日期/时段是唯一硬约束，这些时段不排任何学习任务，未提及时段正常排课；② 课程量守恒——被占用时段空出的课程时长必须转移到其他天，一周课程总时长核算必须等于用户输入的课程量。使用简体中文。"},
        {"role": "user", "content": user},
    ]


def schedule_revision_prompt(plan, first_text, target_hours, issues):
    """二次修订 prompt：给出机器检测到的具体问题（时长不足/可用时段被误写休息），要求 AI 逐条修正。"""
    note = plan.get("note") or "（无）"
    issue_lines = "\n".join("- " + item for item in issues)
    target_desc = "{}".format(("%.1f" % target_hours).rstrip("0").rstrip(".") or "0")
    user = (
        "你是学习规划师。你生成的下周计划存在以下问题，请逐条全部修正后重新输出完整计划：\n"
        "{}\n"
        "\n"
        "用户补充信息（原话，仍是最高优先级硬约束，绝不允许违背）：\n"
        "{}\n"
        "\n"
        "修正规则：\n"
        "1. 保持「## 时段可用性表」与上次一致，不可用时段不增、不减、不改。\n"
        "2. 若上面指出某个时段在可用性表里是「可用」却被写成了休息/不可用，必须把它改成具体课程任务（如「数据结构树与二叉树视频 1.5 小时 + 笔记 0.5 小时」）。\n"
        "3. 若上面指出总时长不足或超出，都按指示调整：不足就在未受限的可用时段补足；超出就削减部分时段的冗余任务/时长；两者最终都要使核算合计等于目标课程量，且不占用受限时段、不要把每天排得过满。\n"
        "4. 可用性表里标记「不可用」的时段，依然绝不允许出现任何学习任务。\n"
        "\n"
        "你上一次输出的全文（在此基础上修正，不要推倒重来）：\n"
        "-----\n"
        "{}\n"
        "-----\n"
        "\n"
        "请重新输出完整结果（## 时段可用性表 → 每日安排 → ## 时长核算 → 判定结论），最终核算合计必须达到 {} 小时（误差不超过 0.5 小时）。"
    ).format(
        issue_lines,
        note,
        first_text,
        target_desc,
    )
    return [
        {"role": "system", "content": "你是「像素学伴」的学习规划师。按用户列出的问题清单逐条修正：可用时段必须排课、不可用时段绝不能排课、总时长核算必须等于用户目标。使用简体中文。"},
        {"role": "user", "content": user},
    ]


# ---------------------------------------------------------------- 月复盘 Prompt

def monthly_prompt(month, week_reviews, month_goal, year_goal, self_eval):
    week_text = "\n\n".join(
        "### {}\n{}".format(r.get("week_label", ""), (r.get("generated") or "")[:1500])
        for r in week_reviews
    )
    se = self_eval or {}

    user = (
        "你是复盘教练。请根据以下材料生成 {} 的《月复盘》，严格按照 10 个小节输出：\n"
        "01 梳理月度大事记。\n"
        "02 找出做得最好的一件事以及最待改进的一件事。\n"
        "03 检视各类习惯的推进情况。\n"
        "04 检视自己的情绪状态和内在状态。\n"
        "05 检视年度目标落地进度。\n"
        "06 找出收获最大的一个点。\n"
        "07 梳理问题和风险。\n"
        "08 寻找发力点，界定自己的能力圈。\n"
        "09 寻求反馈。\n"
        "10 制定下个月的行动计划。\n"
        "\n"
        "输入材料：\n"
        "- 本月目标：{}\n"
        "- 年度目标：{}\n"
        "- 月自评：\n"
        "  · 本月目标完成度：{}%\n"
        "  · 年度目标完成度：{}%\n"
        "  · 自评分数：{}/10\n"
        "  · 做得最好的事：{}\n"
        "  · 最待改进的事：{}\n"
        "  · 习惯推进：{}\n"
        "  · 情绪与内在状态：{}\n"
        "  · 最大收获：{}\n"
        "  · 问题与风险：{}\n"
        "  · 下月计划重点：{}\n"
        "\n"
        "四周周复盘：\n{}\n"
        "\n"
        "要求：每节 100~180 字；第 05 节必须结合年度目标完成度评估；"
        "第 10 节给出具体可执行的下月行动；不要编造用户未提供的信息。请直接输出完整月复盘正文。"
    ).format(
        month,
        month_goal or "（未设置）",
        year_goal or "（未设置）",
        se.get("month_progress", "未填"),
        se.get("year_progress", "未填"),
        se.get("score", "未填"),
        se.get("best", "未填"),
        se.get("worst", "未填"),
        se.get("habits", "未填"),
        se.get("emotion", "未填"),
        se.get("gain", "未填"),
        se.get("risks", "未填"),
        se.get("next_plan", "未填"),
        week_text,
    )
    return [
        {"role": "system", "content": "你是「像素学伴」的复盘教练，月复盘结构化、客观、可执行。使用简体中文。"},
        {"role": "user", "content": user},
    ]


# ---------------------------------------------------------------- 本地模板降级方案

def fallback_weekly_review(fields, week_label, year_goal, month_goal):
    lines = ["# 周复盘 · {}".format(week_label), ""]
    for key, title in WEEKLY_SECTION_TITLES.items():
        lines.append("{} {}".format(key, title))
        content = str(fields.get(key) or "").strip()
        lines.append(content or "（本节未填写，建议补充）")
        lines.append("")
    if year_goal:
        lines.append("年度目标提醒：{}".format(year_goal))
    if month_goal:
        lines.append("本月目标提醒：{}".format(month_goal))
    return "\n".join(lines).strip()


def fallback_weekly_commentary(current_text, prev_reviews, year_goal, month_goal):
    lines = ["# 本周复盘点评（本地模板）", ""]
    lines.append("【本周亮点】")
    lines.append("本周复盘已归档。请重点回顾「01 本周主要事件回顾」中的关键事件，确认是否有可复用的经验。")
    lines.append("")
    lines.append("【与前两周对比】")
    if prev_reviews:
        for i, review in enumerate(prev_reviews, 1):
            lines.append(
                "- 前 {} 周（{}）：已归档，可对照该周「07 下周计划」检查本周执行情况。".format(
                    i, review.get("week_label", "")
                )
            )
        lines.append("- 建议：把本周「03 对标反思」与前两周复盘并排查看，找出反复出现的问题。")
    else:
        lines.append("（暂无前两周复盘存档，完成更多周复盘后会自动对比。）")
    lines.append("")
    lines.append("【综合评价】")
    lines.append("本地模板无法做深度语义评价；请配置 LLM_API_KEY 以获得结合前两周内容的综合评价。")
    lines.append("")
    lines.append("【调整建议与实施建议】")
    lines.append("1. 保持每周复盘连续性，4 周后即可生成月复盘。")
    lines.append("2. 下周计划要具体到每天早中晚时段，并在周中安排一次 15 分钟检查点。")
    if year_goal:
        lines.append("3. 围绕年度目标「{}」校准本周投入。".format(year_goal))
    if month_goal:
        lines.append("4. 对照本月目标「{}」检查进度，必要时调整下周任务。".format(month_goal))
    return "\n".join(lines).strip()


def fallback_schedule(plan):
    total = int(plan.get("total_minutes") or 0)
    days = max(1, min(7, int(plan.get("study_days") or 6)))
    course_count = int(plan.get("course_count") or 0)

    names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    periods = ["上午", "下午", "晚上"]
    note = str(plan.get("note") or "").strip()

    # ---------- 解析补充信息：只把“明确受限的时段”置为不可用 ----------
    rest_slots = {}      # {(day_idx, period_idx): reason}
    reduce_minutes = {}  # {day_idx: 该天需额外预留的分钟数（未指明时段，如“周二空出2小时洗澡”）}
    day_map = {name: i for i, name in enumerate(names)}

    def _norm_period(word):
        return {"早上": 0, "上午": 0, "中午": 1, "下午": 1, "晚间": 2, "晚上": 2}.get(word, None)

    for raw_line in re.split(r"[。；;\n]+", note):
        line = raw_line.strip()
        if not line:
            continue
        rest_words = ["休息", "不安排", "不排", "无任务", "有课", "上课", "开会", "实习", "有事", "外出"]
        busy_words = ["空出", "留出", "预留", "洗澡", "健身", "锻炼"]
        day_ids = []
        for idx, name in enumerate(names):
            if name in line:
                day_ids.append(idx)
        if not day_ids and "周末" in line:
            day_ids = [5, 6]
        if not day_ids:
            continue

        period_matches = []
        for word in ["早上", "上午", "中午", "下午", "晚间", "晚上"]:
            for m in re.finditer(word, line):
                period_matches.append((m.start(), _norm_period(word)))
        period_matches.sort()
        period_ids = [p for _, p in period_matches]
        # 处理“X 至/到 Y”的范围
        if len(period_ids) >= 2 and re.search(r"(?:至|到)", line):
            p_start, p_end = period_ids[0], period_ids[-1]
            if p_start <= p_end:
                period_ids = list(range(p_start, p_end + 1))
            else:
                period_ids = list(range(p_end, p_start + 1))

        is_rest = any(w in line for w in rest_words)
        busy_match = [w for w in busy_words if w in line]

        if busy_match and not is_rest:
            hour_m = re.search(r"(\d+(?:\.\d+)?)\s*小时", line)
            minutes = int(float(hour_m.group(1)) * 60) if hour_m else 120
            if period_ids:
                # 指明时段的占用（如“周二晚上洗澡”）：该时段不再排课
                for p in period_ids:
                    for d in day_ids:
                        rest_slots[(d, p)] = busy_match[0]
            else:
                for d in day_ids:
                    reduce_minutes[d] = reduce_minutes.get(d, 0) + minutes
        elif is_rest:
            for d in day_ids:
                if period_ids:
                    for p in period_ids:
                        rest_slots[(d, p)] = "休息"
                else:
                    # 未指明时段（如“周六休息”“周五休息一天”）→ 整天三个时段都受限
                    for p in range(3):
                        rest_slots[(d, p)] = "休息"

    # ---------- 可用时段与均摊 ----------
    avail_slots = [(i, p) for i in range(days) for p in range(3) if (i, p) not in rest_slots]
    avail_slots.sort()
    per_slot = math.ceil(total / len(avail_slots)) if avail_slots else 0
    per_slot_course = math.ceil(course_count / len(avail_slots)) if (avail_slots and course_count) else 0

    def slot_minutes(i):
        """单日某个可用时段的分钟数（含该天预留占用时扣减后的均摊）。"""
        base = per_slot
        if i in reduce_minutes:
            reduced = max(0, per_slot * 3 - reduce_minutes[i])
            base = math.ceil(reduced / 3)
        return base

    lines = [
        "# 下周任务安排 · {}".format(plan.get("week_label", "")),
        "",
        "周目标：{}".format(plan.get("week_goal", "")),
        "总课程量：约 {} / 可用学习时段 {} 个，每时段约 {}".format(
            fmt_hours(total), len(avail_slots), fmt_hours(per_slot)
        ),
        "",
    ]
    if note:
        lines.append("## 补充信息落实说明")
        lines.append("- 补充信息：{}".format(note))
        rest_desc = []
        for (d, p), reason in sorted(rest_slots.items()):
            rest_desc.append("{} {}（{}）".format(names[d], periods[p], reason))
        if rest_desc:
            lines.append("- 仅以下时段不安排学习：{}".format("；".join(rest_desc)))
        else:
            lines.append("- 补充信息未导致任何整时段停课，已按需预留时间。")
        if reduce_minutes:
            for d, mins in sorted(reduce_minutes.items()):
                lines.append("- {} 需额外预留约 {}（洗澡/事务），该日课程量已相应减少。".format(names[d], fmt_hours(mins)))
        lines.append("")

    for i, name in enumerate(names):
        lines.append("## {}".format(name))
        if i >= days:
            lines.append("- 休息/机动日：只做 15 分钟回顾，不安排硬性任务；若前几日有积压，优先补最紧急的一项。")
        else:
            day_reduce = reduce_minutes.get(i, 0)
            if day_reduce:
                lines.append("- ⚠ 补充信息：该日需额外预留约 {}（洗澡/事务），已相应减少课程量。".format(fmt_hours(day_reduce)))
            for p, period in enumerate(periods):
                if (i, p) in rest_slots:
                    lines.append("- {}：休息/不可用（按补充信息不安排学习任务）".format(period))
                    continue
                minutes = slot_minutes(i)
                if p == 0:
                    if per_slot_course:
                        lines.append(
                            "- 上午（08:00-11:30）：主课学习 {}（约 {} 节，含笔记整理）".format(
                                fmt_hours(minutes), per_slot_course
                            )
                        )
                    else:
                        lines.append("- 上午（08:00-11:30）：主课学习 {}".format(fmt_hours(minutes)))
                elif p == 1:
                    lines.append("- 下午（14:00-17:30）：练习与复盘 {}（配套习题/错题整理）".format(fmt_hours(minutes)))
                else:
                    lines.append("- 晚上（19:30-22:00）：预习与总结 {}（预习次日内容 + 当日小结）".format(fmt_hours(minutes)))
        lines.append("")
    lines.append("## 一周弹性调度提示")
    lines.append("1. 每天留出 10~20% 缓冲时间；某天完不成时，把未完成任务移到最近的机动日。")
    lines.append("2. 每周日晚上做 20 分钟周复盘，衔接下周计划。")
    if note:
        lines.append("3. 已根据补充信息调整：{}".format(note))
    return "\n".join(lines).strip()


def fallback_monthly_review(month, week_reviews, month_goal, year_goal, self_eval):
    se = self_eval or {}

    def g(key):
        value = str(se.get(key) or "").strip()
        return value or "（未填写）"

    events = []
    for review in week_reviews:
        first = ((review.get("fields") or {}).get("01") or "").strip()
        if first:
            events.append("- {}：{}".format(review.get("week_label", ""), first[:80]))
    if not events:
        events.append("（暂无周复盘事件记录）")

    lines = [
        "# 月复盘 · {}（本地模板）".format(month),
        "",
        "01 梳理月度大事记。",
        "\n".join(events),
        "",
        "02 找出做得最好的一件事以及最待改进的一件事。",
        "- 做得最好：{}".format(g("best")),
        "- 最待改进：{}".format(g("worst")),
        "",
        "03 检视各类习惯的推进情况。",
        g("habits"),
        "",
        "04 检视自己的情绪状态和内在状态。",
        g("emotion"),
        "",
        "05 检视年度目标落地进度。",
        "年度目标：{}；年度目标完成度：{}%；本月目标完成度：{}%。".format(
            year_goal or "（未设置）", g("year_progress"), g("month_progress")
        ),
        "",
        "06 找出收获最大的一个点。",
        g("gain"),
        "",
        "07 梳理问题和风险。",
        g("risks"),
        "",
        "08 寻找发力点，界定自己的能力圈。",
        "建议结合本周复盘中反复出现的薄弱点，把下月发力点收敛到 1~2 项。",
        "",
        "09 寻求反馈。",
        "建议把本月复盘发给信任的同学/导师，重点请对方就「问题与风险」和「下月计划」给反馈。",
        "",
        "10 制定下个月的行动计划。",
        g("next_plan"),
        "",
        "（本地模板生成；配置 LLM_API_KEY 可获得更深入的语义点评。）",
    ]
    return "\n".join(lines).strip()
