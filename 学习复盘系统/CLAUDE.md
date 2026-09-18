# CLAUDE.md

「像素学伴」AI 学习复盘秘书 —— 面向大四学生的周复盘 / 下周任务安排 / 月复盘系统。

## 常用命令

```bash
pip install -r requirements.txt   # 安装依赖（仅 flask + requests）
python app.py                     # 启动主应用（带页面）→ http://127.0.0.1:5000
python app1.py                    # 启动 API-only 副本（CORS 已开，端口 5001，可用 PORT 环境变量改）
```

- 无测试、无 linter 配置；验证改动的方式是启动应用后手工调用接口。
- 运行环境为 Windows，shell 是 bash（用 Unix 语法）。

## 架构

- `app.py` — Flask 后端：全部路由 + JSON 文件存储。单文件为主，无数据库。
- `app1.py` — `app.py` 的 API-only 副本（无页面渲染），改后端逻辑时两个文件可能需要同步。
- `services/ai.py` — LLM 调用（OpenAI 兼容接口）+ 各功能 Prompt + 本地模板降级实现。
- `templates/index.html` + `static/css/style.css` + `static/js/app.js` — 原生前端，金色+蓝色像素风。
- `data/store.json` — 运行时数据存档（**用户真实数据，勿随意删改**）。
- `.env` / `.env.example` — LLM 配置（`LLM_API_KEY`、`LLM_BASE_URL`、`LLM_MODEL`、`LLM_THINKING`、可选 `DATA_DIR`）。`.env` 含密钥，绝不提交或外传。

## 核心设计（改代码前必读）

### AI 生成 + 降级
- `generate_with_fallback()`（app.py）：有 Key 走 LLM；空内容时用更大的 `max_tokens` 重试一次；仍失败或未配 Key 则降级到 `services/ai.py` 里的本地模板函数（`fallback_*`），保证离线可用。每个 prompt 构造函数与同名 fallback 函数签名一致，改一处要同步另一处。
- `llm_chat()`（services/ai.py）：DeepSeek 模型默认关闭 thinking（`LLM_THINKING=on` 可开启）以提速。
- 所有生成结果的 `ai_mode` 字段记录 `"ai"` / `"local"`，`ai_note` 记录降级原因。

### 下周计划的"二次修订"机制（app.py `create_schedule`）
生成后程序自动检测三类问题：总时长不足/超出目标（±0.5 小时容差）、"可用性表标记可用但每日安排写了休息"的时段冲突（`schedule_conflicts()` 用正则解析生成文本）。有问题则调 `schedule_revision_prompt` 让 AI 修正；只要更接近目标就采纳修订版。改动输出格式相关逻辑时，注意正则解析（`extract_hours`、`_parse_availability`、`_parse_daily_rest`）依赖 AI 输出的固定格式（`## 时段可用性表`、`## 周X`、`## 时长核算`）。

### 数据模型
- 存储为单个 `data/store.json`，结构：`year_goal` / `month_goals` / `weekly_reviews` / `schedules` / `monthly_reviews` / `self_evals`。
- 周键为 ISO 格式 `{年}-W{周:02d}`（如 `2026-W36`），月键 `{年}-{月:02d}`。
- 同一周/同一月的记录是覆盖式更新（先删旧再追加），按 key 排序。
- 写入用原子操作：先写 `.tmp` 再 `os.replace`，并有 `threading.Lock` 保护。
- 项目自带极简 `load_dotenv()`，避免引入 python-dotenv 依赖。

### 业务规则
- 周复盘按 7 段固定格式（01~07）生成，并自动结合前两周存档生成点评（亮点/对比/评价/建议）。
- 月复盘硬性要求：该月已有 ≥4 篇周复盘，否则接口报错。
- 下周任务安排支持两种计量：按课程节数 × 每节时长，或直接给总时长。

## 注意事项

- 全项目（代码注释、Prompt、UI、错误信息）使用简体中文，保持一致。
- 根目录的 `.docx` / `.pptx` 是演示文档，`~$*` 开头的是 Office 锁文件，均与代码无关。
- 此项目不是 git 仓库。
