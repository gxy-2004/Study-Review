# 像素学伴 · AI 学习复盘秘书

面向大四学生的周复盘 / 下周任务安排 / 月复盘系统。后端 Python（Flask），前端原生 HTML/CSS/JS，金色 + 蓝色像素风，带霓虹闪烁刷新特效。大模型接入智谱AI GLM（预留 API Key 位置），未配置 Key 时自动降级为本地模板，系统离线也可用。

## 功能

1. **周复盘**：按 7 段指定格式填写当周信息，AI 生成周复盘文本并存档；自动结合前两周复盘生成点评（本周亮点 / 与前两周对比 / 综合评价 / 调整建议与实施建议）。
2. **下周任务安排**：输入周目标 + 课程总数（或课程总时长）+ 可学习天数 + 补充信息，AI 把周计划落实到周一至周日每天的早 / 中 / 晚详细任务（时长以小时为单位），兼顾可行性与灵活性，并存档。
3. **月复盘**：年初设立年目标、月初设立月目标；完成该月 4 篇周复盘后，填写月自评信息（含本月 / 年度目标完成度），AI 按 10 段指定格式生成月复盘总结并存档。
4. **档案库**：所有周复盘、下周计划、月复盘自动存档，可查看 / 复制 / 删除。

## 目录结构

```
学习复盘系统/
├── app.py                  # Flask 后端：路由 + JSON 数据存储
├── requirements.txt        # 依赖
├── .env.example            # 智谱AI API Key 配置模板（复制为 .env 使用）
├── services/
│   ├── __init__.py
│   └── ai.py               # LLM 调用 + Prompt + 本地模板降级
├── templates/
│   └── index.html          # 前端页面
├── static/
│   ├── css/style.css       # 金色 + 蓝色像素风 + 霓虹特效
│   └── js/app.js           # 前端交互
└── data/                   # 运行时自动创建，存档 store.json
```

## 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 配置 API Key（把 .env.example 复制为 .env 并填入你的 Key）
cp .env.example .env

# 3. 启动
python app.py

# 4. 浏览器打开
# http://127.0.0.1:5000
```

## 智谱AI (GLM) 配置说明

`.env` 中已预留 Key 位置：

```
LLM_API_KEY=请在这里填入你的智谱AI_API_Key
LLM_BASE_URL=https://open.bigmodel.cn/api/paas/v4
LLM_MODEL=glm-4-flash
```

- 默认使用免费轻量的 `glm-4-flash`；如需更强效果，可把 `LLM_MODEL` 改为你账号可用的其他模型（如 `glm-4-air`、`glm-4-plus`）。
- 不配置 Key 时系统会自动使用本地模板生成，所有功能仍可体验。
