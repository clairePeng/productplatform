# 企业智能融资匹配平台 · 部署与使用说明

本系统是赛题 05 的本地全栈实现：上传产品说明书 PDF → 上传时抽取入库 → 对话录入需求 → 缺项追问 → 分次调取政务数据 → 按已入库产品预审 → 排序推荐 → 经理确认 → 政府视图与报告。

预审只用已入库的产品表和已授权的政务数据，不是授信审批，不生成放款，不向银行发送。默认全程本地规则运行；配置 `DEEPSEEK_API_KEY` 后，对话会调用 DeepSeek（固定模型 deepseek-v4.1-flash）补全字段并解读结果，仅外送用户原话与结果摘要，不外送信用代码、法定代表人和明细。提示词见 [提示词文档.md](提示词文档.md)。

## 1. 环境

- Python 3.9 及以上（在 3.9 上开发和自测）。
- 依赖见 [requirements.txt](requirements.txt)：FastAPI、uvicorn、python-multipart、pdfplumber、openpyxl（读评测集）、pytest。
- 数据：`数据/融资数据.sqlite`（种子库，只读）、`数据/产品/*.pdf`（11 份说明书）。

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## 2. 启动

一键启动：`./start.sh`（自动建环境、装依赖、入库示例产品、打开浏览器；模型密钥填入 `.env` 的 `DEEPSEEK_API_KEY`）。手动启动：

```bash
.venv/bin/python -m uvicorn finmatch.server:app --host 127.0.0.1 --port 8080
```

浏览器打开 <http://127.0.0.1:8080>。首次启动会把种子库复制成工作库 `runtime/工作数据.sqlite`（含 `table_schema`），产品、会话、方案都写在工作库里，种子库不改动。

页面：产品管理、对话工作台、企业画像、政府视图、报告、方案列表，另有「记忆与技能」。

**验收前先在「产品管理」点「上传数据包内 11 份说明书」**（或逐个上传 PDF）。没有入库的产品不进入预审和推荐。

## 3. 批量跑测评集并导出会话记录

```bash
# 公开测评集 → 会话记录/A.jsonl … M.jsonl
.venv/bin/python scripts/run_eval.py

# 现场加试非公开测评集（会话名 N–AP 与公开集 A–M 不重复；默认输出到会话记录/）
DEEPSEEK_API_KEY='' .venv/bin/python scripts/run_eval.py \
  --input "数据/06_非公开测试集_现场加试/01_非公开测试集_输入_现场提供.xlsx"

# 与公开集标准结果自测比对（标准结果只在这个脚本里读取，不进入系统）
.venv/bin/python scripts/compare_gold.py

# 导出产品表抽取结果
.venv/bin/python scripts/export_products.py --ingest
```

- `run_eval.py` 默认清空工作库、重新上传 11 份 PDF，再按题号顺序逐题提问；同一会话字母的多题写入同一个 `.jsonl`，每行一个 JSON：`role`、`content`，助手行带 `trace`（工具、规则、Skill）。
- 项目会从 `.env` 读取模型密钥；如需保证规则模式，显式设置 `DEEPSEEK_API_KEY=''`，会话记录仍写入 `会话记录/`。
- 只读取「题号、会话、承接、问题」四列，不读「标准结果」列。
- 追加到现有工作库而不清空：加 `--no-reset`。

## 4. 规则落在哪里

| 内容                   | 位置                                                                                              |
| -------------------- | ----------------------------------------------------------------------------------------------- |
| PDF 抽取（只读第三至第六条表格）   | [finmatch/extractor.py](finmatch/extractor.py)、入库 [finmatch/products.py](finmatch/products.py)  |
| 预审规则与原因输出            | [finmatch/precheck.py](finmatch/precheck.py)，规则顺序、文案、来源在 [config/rules.json](config/rules.json) |
| 参与预审列白名单             | [config/rules.json](config/rules.json) 的 `whitelist`；`Facts.get` 读取白名单外的列直接报错                   |
| 授权闸门                 | [finmatch/govdata.py](finmatch/govdata.py)：未授权时税务、社保、司法、历史融资工具拒绝读取                              |
| 排序（利率优先/额度优先，最多 3 个） | [finmatch/ranking.py](finmatch/ranking.py)                                                      |
| 对话解析、缺项追问、确认/放弃      | [finmatch/nlu.py](finmatch/nlu.py)、[finmatch/agent.py](finmatch/agent.py)                       |
| 方案状态、政府视图、报告         | [finmatch/solutions.py](finmatch/solutions.py)                                                  |
| 用语、用途别名、停用词          | [config/lexicon.json](config/lexicon.json)                                                      |
| Skill 清单             | [skills/](skills)                                                                               |

规则都在代码和配置里，没有提示词，也没有写入标准结果。

## 5. 测试

```bash
.venv/bin/python -m pytest tests -q
```

## 6. 目录

```
finmatch/        后端与静态页面
config/          预审口径与词表
skills/          Skill 清单（触发条件、输入输出契约）
scripts/         批量测评、自测比对、产品表导出
tests/           自动化测试与扩展自测题
会话记录/        公开测评集会话（A–M）
产品表/          产品表抽取结果 CSV
自测结果/        逐题比对、政府视图与报告自测、扩展自测会话
runtime/         工作库（运行时生成）
```
