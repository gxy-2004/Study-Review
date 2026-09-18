/* 像素学伴 · 前端交互 */
(function () {
  "use strict";

  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => Array.from(document.querySelectorAll(sel));

  const FIELDS_01_07 = ["01", "02", "03", "04", "05", "06", "07"];

  let state = {
    year_goal: null,
    month_goals: {},
    weekly_reviews: [],
    schedules: [],
    monthly_reviews: [],
    self_evals: {},
    ai_configured: false,
  };

  /* ---------------- 基础工具 ---------------- */

  async function api(url, options = {}) {
    const res = await fetch(url, {
      headers: { "Content-Type": "application/json" },
      ...options,
    });
    const data = await res.json().catch(() => ({ ok: false, error: "响应解析失败" }));
    if (!res.ok || data.ok === false) {
      throw new Error(data.error || "请求失败 " + res.status);
    }
    return data;
  }

  function esc(value) {
    return String(value ?? "").replace(/[&<>"']/g, (c) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    }[c]));
  }

  let toastTimer = null;
  function toast(message, isError = false) {
    const el = $("#toast");
    el.textContent = message;
    el.classList.toggle("error", isError);
    el.classList.remove("hidden");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.classList.add("hidden"), 3200);
  }

  function todayStr() {
    const d = new Date();
    const mm = String(d.getMonth() + 1).padStart(2, "0");
    const dd = String(d.getDate()).padStart(2, "0");
    return d.getFullYear() + "-" + mm + "-" + dd;
  }

  function currentMonthStr() {
    return todayStr().slice(0, 7);
  }

  function isoWeek(dateStr) {
    const [y, m, d] = dateStr.split("-").map(Number);
    const date = new Date(Date.UTC(y, m - 1, d));
    const dayNum = date.getUTCDay() || 7;
    date.setUTCDate(date.getUTCDate() + 4 - dayNum);
    const yearStart = new Date(Date.UTC(date.getUTCFullYear(), 0, 1));
    const week = Math.ceil(((date - yearStart) / 86400000 + 1) / 7);
    return { year: date.getUTCFullYear(), week };
  }

  function weekLabelFromDate(dateStr) {
    if (!dateStr) return "";
    const { year, week } = isoWeek(dateStr);
    return year + "年 第" + String(week).padStart(2, "0") + "周";
  }

  /* ---------------- 霓虹闪烁刷新特效 ---------------- */

  function neonRefresh() {
    const body = document.body;
    const sweep = $("#scanSweep");
    body.classList.remove("neon-flicker");
    if (sweep) {
      sweep.classList.remove("run");
      void sweep.offsetWidth;
      sweep.classList.add("run");
    }
    void body.offsetWidth;
    body.classList.add("neon-flicker");
    setTimeout(() => body.classList.remove("neon-flicker"), 1500);
  }

  /* ---------------- 生成进度条 ---------------- */

  let progressTimer = null;
  let progressStart = 0;

  function setProgress(percent, suffix) {
    const bar = $("#progressBar");
    const text = $("#progressText");
    if (bar) {
      bar.style.width = Math.floor(percent) + "%";
      if (percent >= 100) {
        bar.classList.remove("waiting");
      }
    }
    if (text) text.textContent = Math.floor(percent) + "%" + (suffix || "");
  }

  function showProgress(title) {
    const overlay = $("#progressOverlay");
    if (!overlay) return;
    const titleEl = $("#progressTitle");
    if (titleEl) titleEl.textContent = title;
    overlay.classList.remove("hidden");
    progressStart = Date.now();
    setProgress(0, " · 准备中…");
    clearInterval(progressTimer);
    let p = 0;
    progressTimer = setInterval(() => {
      const elapsed = Math.floor((Date.now() - progressStart) / 1000);
      // 缓慢爬升到 90% 后进入等待动画，避免过早“卡住”的观感
      p = Math.min(p + 0.6 + Math.random() * 1.2, 90);
      const bar = $("#progressBar");
      if (bar) {
        if (p >= 90) {
          bar.classList.add("waiting");
        }
      }
      setProgress(p, " · 已等待 " + elapsed + "s");
    }, 600);
  }

  function finishProgress() {
    clearInterval(progressTimer);
    setProgress(100, " · 完成");
    setTimeout(() => {
      const overlay = $("#progressOverlay");
      if (overlay) overlay.classList.add("hidden");
    }, 550);
  }

  /* ---------------- 选项卡 ---------------- */

  function activateTab(tabId) {
    $$(".tab-btn").forEach((btn) => btn.classList.toggle("active", btn.dataset.tab === tabId));
    $$(".panel").forEach((panel) => panel.classList.toggle("active", panel.id === tabId));
    neonRefresh();
  }

  /* ---------------- 渲染 ---------------- */

  function renderYearGoal() {
    const box = $("#yearCurrent");
    if (!state.year_goal) {
      box.innerHTML = '<p class="muted">尚未设置年目标。</p>';
      return;
    }
    box.innerHTML =
      '<div class="goal-item"><span class="goal-key">' + esc(state.year_goal.year) + "年目标</span>" +
      esc(state.year_goal.content) +
      '<p class="muted">更新于 ' + esc(state.year_goal.updated_at || "") + "</p></div>";
  }

  function renderMonthGoals() {
    const box = $("#monthGoalList");
    const goals = Object.values(state.month_goals || {}).sort((a, b) => String(a.month).localeCompare(String(b.month)));
    if (!goals.length) {
      box.innerHTML = '<p class="muted">尚未设置月目标。</p>';
      return;
    }
    box.innerHTML = goals
      .map(
        (g) =>
          '<div class="goal-item"><span class="goal-key">' + esc(g.month) + "</span>" +
          esc(g.content) +
          '<p class="muted">更新于 ' + esc(g.updated_at || "") + "</p></div>"
      )
      .join("");
  }

  /* ---------------- 年度归档 · 工具 ---------------- */

  let archiveYearState = null; // 当前正在查看的年份
  let archiveMonthState = "all"; // "all" 或 "2026-09" 这样的月份键

  function pad2(n) {
    return String(n).padStart(2, "0");
  }

  function currentYearStr() {
    return String(new Date().getFullYear());
  }

  function isoMonday(year, week) {
    // ISO 8601：第 1 周的周一由 1 月 4 日所在周的周一决定
    const jan4 = new Date(Date.UTC(year, 0, 4));
    const dow = (jan4.getUTCDay() + 6) % 7; // 0 = 周一
    const firstMonday = new Date(Date.UTC(year, 0, 4 - dow));
    return new Date(firstMonday.getTime() + (week - 1) * 7 * 86400000);
  }

  function weekRangeOf(weekKey) {
    const m = /^(\d{4})-W(\d{2})$/.exec(weekKey || "");
    if (!m) return null;
    const year = Number(m[1]);
    const week = Number(m[2]);
    const mon = isoMonday(year, week);
    const sun = new Date(mon.getTime() + 6 * 86400000);
    const fmt = (d) => pad2(d.getUTCMonth() + 1) + "-" + pad2(d.getUTCDate());
    return { week: week, start: fmt(mon), end: fmt(sun) };
  }

  function monthRangeText(year, month) {
    const last = new Date(Date.UTC(year, month, 0)).getUTCDate();
    return year + "-" + pad2(month) + "-01 ~ " + year + "-" + pad2(month) + "-" + pad2(last);
  }

  function availableArchiveYears() {
    const years = new Set([currentYearStr()]);
    const add = (s) => {
      const y = String(s || "").slice(0, 4);
      if (/^\d{4}$/.test(y)) years.add(y);
    };
    if (state.year_goal) add(state.year_goal.year);
    Object.keys(state.month_goals || {}).forEach((k) => add(k));
    state.weekly_reviews.forEach((r) => add(r.month));
    state.schedules.forEach((s) => add(s.month));
    state.monthly_reviews.forEach((m) => add(m.month));
    return Array.from(years).sort();
  }

  function yearCounts(year) {
    const c = { monthGoal: 0, week: 0, plan: 0, monthRev: 0 };
    Object.keys(state.month_goals || {}).forEach((k) => {
      if (k.slice(0, 4) === year) c.monthGoal++;
    });
    state.weekly_reviews.forEach((r) => {
      if ((r.month || "").slice(0, 4) === year) c.week++;
    });
    state.schedules.forEach((s) => {
      if ((s.month || "").slice(0, 4) === year) c.plan++;
    });
    state.monthly_reviews.forEach((m) => {
      if ((m.month || "").slice(0, 4) === year) c.monthRev++;
    });
    return c;
  }

  /* ---------------- 年度归档 · 年目标（直接展示） ---------------- */

  function yearGoalBannerHtml() {
    const g = state.year_goal;
    if (!g) {
      return (
        '<div class="goal-banner"><span class="slot-tag">🎯 年度目标</span>' +
        '<p class="muted">尚未设置年目标。</p>' +
        '<button class="pixel-btn small" data-goto="panel-year" type="button">去设置年目标</button></div>'
      );
    }
    const note =
      g.year !== archiveYearState
        ? '<p class="muted">当前查看 ' + esc(archiveYearState) + " 年；下方为最近设置（" + esc(g.year) + " 年）的年目标</p>"
        : "";
    return (
      '<div class="goal-banner"><span class="slot-tag">🎯 年度目标 · ' + esc(g.year) + "年</span>" +
      '<p class="slot-content">' + esc(g.content) + "</p>" +
      '<p class="muted">更新于 ' + esc(g.updated_at || "") + "</p>" + note +
      '<button class="pixel-btn small" data-goto="panel-year" type="button">修改年目标</button></div>'
    );
  }

  /* ---------------- 年度归档 · 月度卡片 ---------------- */

  function monthGoalSlotHtml(year, month) {
    const mKey = year + "-" + pad2(month);
    const g = (state.month_goals || {})[mKey];
    const tag = '<span class="slot-tag">📅 月初 · 月目标</span>';
    const btn =
      '<button class="pixel-btn small" data-goto="panel-month-goal" data-goto-month="' +
      mKey + '" type="button">' + (g ? "修改" : "去设置") + "</button>";
    if (!g) {
      return '<div class="month-slot start">' + tag + '<p class="muted">本月尚未设置月目标</p>' + btn + "</div>";
    }
    return (
      '<div class="month-slot start">' + tag +
      '<p class="slot-content">' + esc(g.content) + "</p>" +
      '<p class="muted">更新于 ' + esc(g.updated_at || "") + "</p>" + btn + "</div>"
    );
  }

  function monthReviewSlotHtml(year, month) {
    const mKey = year + "-" + pad2(month);
    const m = state.monthly_reviews.find((x) => x.month === mKey) || null;
    const tag = '<span class="slot-tag">🌙 月末 · 月复盘</span>';
    if (!m) {
      return (
        '<div class="month-slot end">' + tag +
        '<p class="muted">月末尚未生成月复盘（需先完成本月 4 周周复盘）</p>' +
        '<button class="pixel-btn small" data-goto="panel-month" data-goto-month="' + mKey + '" type="button">去生成月复盘</button></div>'
      );
    }
    return (
      '<div class="month-slot end">' + tag +
      (m.ai_note ? '<p class="muted">' + esc(m.ai_note) + "</p>" : "") +
      "<details><summary>查看月复盘全文</summary>" +
      '<pre id="mr-' + m.id + '" class="result-pre">' + esc(m.generated || "") + "</pre>" +
      '<button class="pixel-btn small" data-copy="mr-' + m.id + '" type="button">复制</button> ' +
      '<button class="pixel-btn small" data-delete-month="' + m.id + '" type="button">删除</button>' +
      "</details>" +
      '<button class="pixel-btn small" data-goto="panel-month" data-goto-month="' + mKey + '" type="button">重新生成</button></div>'
    );
  }

  function weekUnitHtml(weekKey, review, plan) {
    const range = weekRangeOf(weekKey);
    const num = range ? "第" + pad2(range.week) + "周" : weekKey;
    const rangeText = range ? range.start + " ~ " + range.end : "";
    let flags = "";
    if (review) flags += '<span class="has-doc">周复盘</span>';
    if (plan) flags += '<span class="has-doc">周计划</span>';

    let docs = "";
    if (plan) {
      const goal = (plan.plan && plan.plan.week_goal) || "";
      docs +=
        '<div class="week-doc">' +
        '<div class="week-doc-title">📌 下周计划 / 周目标</div>' +
        (goal ? '<p class="week-goal">周目标：' + esc(goal) + "</p>" : "") +
        (plan.ai_note ? '<p class="muted">' + esc(plan.ai_note) + "</p>" : "") +
        "<details><summary>查看完整计划</summary>" +
        '<pre id="p-' + plan.id + '" class="result-pre">' + esc(plan.generated || "") + "</pre>" +
        '<button class="pixel-btn small" data-copy="p-' + plan.id + '" type="button">复制</button> ' +
        '<button class="pixel-btn small" data-delete-plan="' + plan.id + '" type="button">删除</button>' +
        "</details></div>";
    }
    if (review) {
      docs +=
        '<div class="week-doc">' +
        '<div class="week-doc-title">📝 周复盘（含 07 下周计划）</div>' +
        (review.ai_note ? '<p class="muted">' + esc(review.ai_note) + "</p>" : "") +
        "<details><summary>查看周复盘与点评</summary>" +
        '<pre id="w-' + review.id + '" class="result-pre">【周复盘】\n' + esc(review.generated || "") +
        "\n\n【复盘点评】\n" + esc(review.commentary || "") + "</pre>" +
        '<button class="pixel-btn small" data-copy="w-' + review.id + '" type="button">复制</button> ' +
        '<button class="pixel-btn small" data-delete-week="' + review.id + '" type="button">删除</button>' +
        "</details></div>";
    }
    return (
      '<details class="week-card" data-week="' + weekKey + '">' +
      "<summary>" +
      '<span class="week-num">' + num + "</span>" +
      (rangeText ? '<span class="week-range">' + rangeText + "</span>" : "") +
      flags +
      "</summary>" +
      '<div class="week-docs">' + docs + "</div></details>"
    );
  }

  function weekDocsAreaHtml(year, month) {
    const mKey = year + "-" + pad2(month);
    const reviewsByWeek = {};
    state.weekly_reviews
      .filter((r) => r.month === mKey)
      .sort((a, b) => String(a.week_key).localeCompare(String(b.week_key)))
      .forEach((r) => (reviewsByWeek[r.week_key] = r));
    const plansByWeek = {};
    state.schedules
      .filter((s) => s.month === mKey)
      .sort((a, b) => String(a.week_key).localeCompare(String(b.week_key)))
      .forEach((s) => (plansByWeek[s.week_key] = s));

    const keys = Array.from(new Set(Object.keys(reviewsByWeek).concat(Object.keys(plansByWeek)))).sort();
    if (!keys.length) {
      return '<p class="muted week-empty">本月暂无周复盘 / 下周计划存档</p>';
    }
    return (
      '<div class="month-weeks">' +
      keys
        .map((k) => weekUnitHtml(k, reviewsByWeek[k] || null, plansByWeek[k] || null))
        .join("") +
      "</div>"
    );
  }

  function monthCardHtml(year, month, openByDefault) {
    const mKey = year + "-" + pad2(month);
    const reviews = state.weekly_reviews.filter((r) => r.month === mKey);
    const plans = state.schedules.filter((s) => s.month === mKey);
    const hasMonthRev = state.monthly_reviews.some((x) => x.month === mKey);
    const header =
      "<h3>" + year + "年" + month + "月</h3>" +
      '<span class="month-range">' + monthRangeText(year, month) + "</span>" +
      '<span class="month-count">周复盘 ' + reviews.length + " · 周计划 " + plans.length + " · 月复盘 " + (hasMonthRev ? 1 : 0) + "</span>";
    const body =
      monthGoalSlotHtml(year, month) +
      weekDocsAreaHtml(year, month) +
      monthReviewSlotHtml(year, month);
    return (
      '<details class="month-card"' + (openByDefault ? " open" : "") + ' data-month="' + mKey + '">' +
      '<summary class="month-head">' + header + "</summary>" +
      body +
      "</details>"
    );
  }

  function renderYearArchive() {
    const container = $("#yearArchive");
    const selYear = $("#archiveYear");
    const selMonth = $("#archiveMonth");
    const banner = $("#yearGoalBanner");
    const statsEl = $("#archiveStats");
    if (!container) return;

    const years = availableArchiveYears();
    if (selYear) {
      const keep =
        archiveYearState && years.indexOf(archiveYearState) !== -1 ? archiveYearState : currentYearStr();
      selYear.innerHTML = years
        .map(
          (y) =>
            '<option value="' + y + '"' + (y === keep ? " selected" : "") + ">" + y + "年</option>"
        )
        .join("");
      archiveYearState = keep;
    }
    const year = archiveYearState || currentYearStr();

    // 年份切换后，若之前选择的月份不属于新年份则回到全年总览
    if (archiveMonthState !== "all" && archiveMonthState.indexOf(year) !== 0) {
      archiveMonthState = "all";
    }
    if (selMonth) {
      let monthOptions = '<option value="all">全年总览（可折叠）</option>';
      for (let m = 1; m <= 12; m++) {
        const val = year + "-" + pad2(m);
        monthOptions +=
          '<option value="' + val + '"' + (archiveMonthState === val ? " selected" : "") + ">" + m + "月</option>";
      }
      selMonth.innerHTML = monthOptions;
    }

    if (banner) banner.innerHTML = yearGoalBannerHtml();
    if (statsEl) {
      const c = yearCounts(year);
      statsEl.textContent =
        year + "年 · 月目标 " + c.monthGoal + " / 周复盘 " + c.week + " / 周计划 " + c.plan + " / 月复盘 " + c.monthRev;
    }

    let html = "";
    if (archiveMonthState === "all") {
      for (let m = 1; m <= 12; m++) {
        html += monthCardHtml(year, m, false);
      }
    } else {
      const m = Number(archiveMonthState.slice(5, 7));
      html += monthCardHtml(year, m, true);
    }
    container.innerHTML = html;
  }

  function renderAll() {
    renderYearGoal();
    renderMonthGoals();
    renderYearArchive();
    const badge = $("#aiStatus");
    badge.textContent = state.ai_configured ? "AI 已配置 ✓" : "未配置 API Key · 本地模板模式";
    badge.style.borderColor = state.ai_configured ? "var(--neon-blue)" : "var(--gold)";
  }

  function fillSelfEval(month) {
    const se = (state.self_evals && state.self_evals[month]) || {};
    const mapping = {
      se_month_progress: "month_progress",
      se_year_progress: "year_progress",
      se_score: "score",
      se_best: "best",
      se_worst: "worst",
      se_habits: "habits",
      se_emotion: "emotion",
      se_gain: "gain",
      se_risks: "risks",
      se_next_plan: "next_plan",
    };
    Object.entries(mapping).forEach(([id, key]) => {
      const el = $("#" + id);
      if (el) el.value = se[key] ?? "";
    });
  }

  function updateMonthReviewHint() {
    const month = $("#monthReviewMonth").value;
    const hint = $("#monthReviewHint");
    if (!month) {
      hint.textContent = "";
      return;
    }
    const count = state.weekly_reviews.filter((r) => r.month === month).length;
    hint.textContent = "该月已完成 " + count + " 篇周复盘（需 ≥ 4 篇才能生成月复盘）。";
    fillSelfEval(month);
  }

  function collectSelfEval() {
    return {
      month_progress: $("#se_month_progress").value.trim(),
      year_progress: $("#se_year_progress").value.trim(),
      score: $("#se_score").value.trim(),
      best: $("#se_best").value.trim(),
      worst: $("#se_worst").value.trim(),
      habits: $("#se_habits").value.trim(),
      emotion: $("#se_emotion").value.trim(),
      gain: $("#se_gain").value.trim(),
      risks: $("#se_risks").value.trim(),
      next_plan: $("#se_next_plan").value.trim(),
    };
  }

  /* ---------------- 数据加载 ---------------- */

  async function loadState() {
    state = await api("/api/state");
    renderAll();
    updateMonthReviewHint();
  }

  /* ---------------- 事件绑定 ---------------- */

  function bindEvents() {
    $$(".tab-btn").forEach((btn) =>
      btn.addEventListener("click", () => activateTab(btn.dataset.tab))
    );

    $("#refreshBtn").addEventListener("click", neonRefresh);

    const archiveSel = $("#archiveYear");
    if (archiveSel) {
      archiveSel.addEventListener("change", (e) => {
        archiveYearState = e.target.value;
        renderYearArchive();
      });
    }
    const archiveMonthSel = $("#archiveMonth");
    if (archiveMonthSel) {
      archiveMonthSel.addEventListener("change", (e) => {
        archiveMonthState = e.target.value;
        renderYearArchive();
      });
    }

    $("#weekDate").addEventListener("change", () => {
      $("#weekLabel").textContent = "识别为：" + weekLabelFromDate($("#weekDate").value);
    });
    $("#planMode").addEventListener("change", () => {
      const isCount = $("#planMode").value === "count";
      $("#planCountBox").classList.toggle("hidden", !isCount);
      $("#planDurationBox").classList.toggle("hidden", isCount);
    });

    $("#yearSubmit").addEventListener("click", async () => {
      try {
        const data = await api("/api/year_goal", {
          method: "POST",
          body: JSON.stringify({
            year: $("#yearInput").value.trim(),
            content: $("#yearContent").value.trim(),
          }),
        });
        state.year_goal = data.year_goal;
        renderYearGoal();
        toast("年目标已保存 ⚡");
      } catch (e) {
        toast(e.message, true);
      }
    });

    $("#monthGoalSubmit").addEventListener("click", async () => {
      try {
        const data = await api("/api/month_goal", {
          method: "POST",
          body: JSON.stringify({
            month: $("#monthGoalMonth").value,
            content: $("#monthGoalContent").value.trim(),
          }),
        });
        state.month_goals = data.month_goals;
        renderMonthGoals();
        toast("月目标已保存 ⚡");
      } catch (e) {
        toast(e.message, true);
      }
    });

    $("#weekSubmit").addEventListener("click", async () => {
      const btn = $("#weekSubmit");
      try {
        const fields = {};
        FIELDS_01_07.forEach((key, index) => {
          fields[key] = $("#f" + String(index + 1).padStart(2, "0")).value.trim();
        });
        btn.disabled = true;
        btn.textContent = "生成中…";
        showProgress("AI 正在生成周复盘与点评…");
        const data = await api("/api/week_reviews", {
          method: "POST",
          body: JSON.stringify({
            week_start: $("#weekDate").value,
            fields: fields,
          }),
        });
        state.weekly_reviews = data.reviews;
        $("#weekText").textContent = data.review.generated || "";
        $("#weekComment").textContent = data.review.commentary || "";
        $("#weekResult").classList.remove("hidden");
        renderYearArchive();
        updateMonthReviewHint();
        toast(data.review.ai_note ? "已生成（" + data.review.ai_note + "）" : "周复盘已生成并存档 ⚡");
        neonRefresh();
      } catch (e) {
        toast(e.message, true);
      } finally {
        finishProgress();
        btn.disabled = false;
        btn.textContent = "保存并生成周复盘（AI）";
      }
    });

    $("#planSubmit").addEventListener("click", async () => {
      const btn = $("#planSubmit");
      try {
        btn.disabled = true;
        btn.textContent = "生成中…";
        showProgress("AI 正在安排下周每日任务…");
        const mode = $("#planMode").value;
        const payload = {
          week_start: $("#planDate").value,
          week_goal: $("#planGoal").value.trim(),
          mode: mode,
          study_days: parseInt($("#planDays").value, 10),
          note: $("#planNote").value.trim(),
        };
        if (mode === "count") {
          payload.course_count = parseInt($("#planCount").value, 10) || 0;
          payload.hours_per_course = parseFloat($("#planHoursPerCourse").value) || 1.5;
        } else {
          payload.total_hours = parseFloat($("#planHours").value) || 0;
        }
        const data = await api("/api/schedules", {
          method: "POST",
          body: JSON.stringify(payload),
        });
        state.schedules = data.schedules;
        $("#planText").textContent = data.schedule.generated || "";
        $("#planResult").classList.remove("hidden");
        renderYearArchive();
        toast(data.schedule.ai_note ? "已生成（" + data.schedule.ai_note + "）" : "下周计划已生成并存档 ⚡");
        neonRefresh();
      } catch (e) {
        toast(e.message, true);
      } finally {
        finishProgress();
        btn.disabled = false;
        btn.textContent = "生成下周计划（AI）";
      }
    });

    $("#monthReviewMonth").addEventListener("change", updateMonthReviewHint);

    $("#monthReviewSubmit").addEventListener("click", async () => {
      const btn = $("#monthReviewSubmit");
      try {
        btn.disabled = true;
        btn.textContent = "生成中…";
        showProgress("AI 正在生成月复盘总结…");
        const data = await api("/api/month_reviews", {
          method: "POST",
          body: JSON.stringify({
            month: $("#monthReviewMonth").value,
            self_eval: collectSelfEval(),
          }),
        });
        state.monthly_reviews = data.monthly_reviews;
        $("#monthText").textContent = data.review.generated || "";
        $("#monthReviewResult").classList.remove("hidden");
        renderYearArchive();
        toast(data.review.ai_note ? "已生成（" + data.review.ai_note + "）" : "月复盘已生成并存档 ⚡");
        neonRefresh();
      } catch (e) {
        toast(e.message, true);
      } finally {
        finishProgress();
        btn.disabled = false;
        btn.textContent = "保存自评并生成月复盘（AI）";
      }
    });

    /* 复制 */
    document.addEventListener("click", async (e) => {
      const copyBtn = e.target.closest("[data-copy]");
      if (!copyBtn) return;
      const target = document.getElementById(copyBtn.dataset.copy);
      if (!target) return;
      try {
        await navigator.clipboard.writeText(target.textContent || "");
        toast("已复制到剪贴板 ✂");
      } catch (err) {
        toast("复制失败，请手动选择复制", true);
      }
    });

    /* 删除 / 页面跳转 */
    document.addEventListener("click", async (e) => {
      const gotoBtn = e.target.closest("[data-goto]");
      if (gotoBtn) {
        const month = gotoBtn.dataset.gotoMonth;
        if (month) {
          const goalInp = $("#monthGoalMonth");
          const revInp = $("#monthReviewMonth");
          if (goalInp) goalInp.value = month;
          if (revInp) revInp.value = month;
          updateMonthReviewHint();
          fillSelfEval(month);
        }
        activateTab(gotoBtn.dataset.goto);
        return;
      }
      const weekBtn = e.target.closest("[data-delete-week]");
      const planBtn = e.target.closest("[data-delete-plan]");
      const monthBtn = e.target.closest("[data-delete-month]");
      try {
        if (weekBtn) {
          if (!confirm("确定删除这篇周复盘？")) return;
          const data = await api("/api/week_reviews/" + weekBtn.dataset.deleteWeek, { method: "DELETE" });
          state.weekly_reviews = data.reviews;
          renderYearArchive();
          updateMonthReviewHint();
          toast("已删除");
        } else if (planBtn) {
          if (!confirm("确定删除这份下周计划？")) return;
          const data = await api("/api/schedules/" + planBtn.dataset.deletePlan, { method: "DELETE" });
          state.schedules = data.schedules;
          renderYearArchive();
          toast("已删除");
        } else if (monthBtn) {
          if (!confirm("确定删除这份月复盘？")) return;
          const data = await api("/api/month_reviews/" + monthBtn.dataset.deleteMonth, { method: "DELETE" });
          state.monthly_reviews = data.monthly_reviews;
          renderYearArchive();
          toast("已删除");
        }
      } catch (err) {
        toast(err.message, true);
      }
    });
  }

  /* ---------------- 初始化 ---------------- */

  function init() {
    const today = todayStr();
    $("#weekDate").value = today;
    $("#planDate").value = today;
    $("#yearInput").value = new Date().getFullYear();
    $("#monthGoalMonth").value = currentMonthStr();
    $("#monthReviewMonth").value = currentMonthStr();
    $("#weekLabel").textContent = "识别为：" + weekLabelFromDate(today);

    bindEvents();
    loadState().catch((e) => toast("加载数据失败：" + e.message, true));

    setTimeout(neonRefresh, 350);
  }

  document.addEventListener("DOMContentLoaded", init);
})();
