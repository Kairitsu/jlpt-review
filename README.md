# 句子重组 · jlpt-review

面向中文母语者的日语句子重组与间隔复习 Web 应用。用户根据中文提示，用词块还原日语原句；本机 SudachiPy（full 词典）负责分词和假名注音，官方 Python FSRS 负责全部复习调度。

| 项目 | 说明 |
|---|---|
| 后端 | Flask · Gunicorn · SQLite |
| 调度 | 官方 `fsrs==6.3.1`（FSRS 6） |
| 分词 | SudachiPy + SudachiDict-full |
| 部署 | Docker Compose，默认监听 `127.0.0.1:3220` |
| 许可证 | [AGPL-3.0](LICENSE) |

## 升级前必须备份

首次使用 FSRS 版本启动现有数据库时，会执行一次不可逆的数据库升级：

- 保留句集、中文、日文、词块顺序、假名和句集归属；
- 永久删除旧作答、旧复习事件、旧练习报告和全部旧记忆进度；
- 删除旧的 dynamic/fixed 调度设置与旧记忆字段；
- 把所有现有句子初始化为立即到期的 FSRS 新卡。

升级前务必备份数据库：

```bash
./scripts/backup-db.sh
```

升级由 `schema_migrations` 表中的 `fsrs_v1_reset` 标记保护。重置只执行一次；后续启动不会再次清空已经生成的 FSRS 进度和记录。

## 功能

- 句集创建、重命名、转移与级联删除；
- 中文/日文句子导入、编辑和搜索；
- Sudachi A/B/C 多粒度分词、词块拆分/合并、汉字假名注音；
- 点选或拖拽词块完成句子重组；
- FSRS 到期队列、练习报告和错题重练；
- FSRS 评分统计、未来 7/30/90 天复习预测、stability/difficulty 分布与预计保持率；
- 用户认证和 IANA 时区设置。

## FSRS 调度

项目只保留 FSRS 一套生产调度逻辑。所有 Card 状态读取、官方 Scheduler 调用、下次复习时间和预计保持率计算均集中在 `fsrs_service.py`。项目不实现或复制 FSRS 公式。

默认配置：

| 配置 | 值 |
|---|---|
| FSRS 包版本 | 6.3.1 |
| 目标保持率 | 90% |
| 最长间隔 | 36500 天 |
| 学习步骤 | 官方库默认值 |
| 重学步骤 | 官方库默认值 |
| 生产随机间隔扰动 | 开启 |
| 测试随机间隔扰动 | 关闭 |

FSRS 和数据库中的复习时间始终使用 UTC。用户时区只影响页面显示、“今日”边界和自然日统计，不参与调度计算。

### 作答到 FSRS 评分的映射

“核对答案”只追加一条原始作答记录，不会立刻修改 Card。用户点击“下一题”、完成会话或在已核对状态下退出时，系统才根据当前题的完整作答过程生成一次最终评分：

| 作答过程 | FSRS | 中文界面 |
|---|---|---|
| 跳过并结束当前题 | 不更新 | 跳过 |
| 最终没有答对 | Again | 忘记 |
| 先答错，之后在同一道题中答对 | Hard | 模糊 |
| 第一次就答对 | Good | 认识 |
| 第一次答对并主动选择“太简单” | Easy | 轻松掌握 |

答题耗时只写入记录，不会自动触发 Easy。错题重练不会强制改变评分。

每个练习会话中的每个句子在 `practice_items` 中只有一个主键记录，`review_events` 同时有 `UNIQUE(session_id, sentence_id)` 约束。完成题目时，FSRS Card 更新、复习事件写入和题目完成标记在同一个 SQLite 写事务中完成。重复请求会返回已有结果，不会再次调度。

### 数据库存储

每个句子保存：

- `fsrs_state`、`fsrs_step`；
- `stability`、`difficulty`；
- `last_review_at`、`next_review_at`；
- `fsrs_version`。

`next_review_at` 用于到期队列。新导入的句子直接创建为官方 FSRS 新卡并立即到期。

每个最终复习事件保存会话、句子、评分、耗时、FSRS 版本，以及更新前后的 state、step、stability、difficulty 和到期时间。每次核对产生独立 `attempts` 行，旧记录不会被覆盖。

## 快速开始

```bash
git clone https://github.com/Kairitsu/jlpt-review.git
cd jlpt-review
cp secrets/app.env.example secrets/app.env
# 编辑 APP_SECRET、INIT_USERNAME、INIT_PASSWORD
docker compose up -d --build
curl -s http://127.0.0.1:3220/api/health
```

生产环境建议通过 Caddy/Nginx 把 HTTPS 请求反代到 `127.0.0.1:3220`。

### 环境变量

| 变量 | 说明 |
|---|---|
| `APP_SECRET` | Flask 会话密钥原料 |
| `INIT_USERNAME` / `INIT_PASSWORD` | 数据库尚未配置认证时写入初始账号 |
| `SESSION_COOKIE_SECURE` | HTTPS 生产环境应为 `true` |
| `TRUST_PROXY_COUNT` | 可信反向代理层数 |
| `DATA_DIR` | SQLite 与活动字体数据目录 |
| `FSRS_ENABLE_FUZZING` | 生产默认 `true`；通常无需修改 |

真实密钥、数据库、备份和 Basic Auth 文件不得提交到 Git。

## API 摘要

| 接口 | 内容 |
|---|---|
| `POST /api/practice/sessions` | 创建练习会话和唯一题目项 |
| `POST /api/practice/sessions/:id/attempts` | 追加一次原始核对/跳过记录，不更新 FSRS |
| `POST /api/practice/sessions/:id/sentences/:sentenceId/complete` | 幂等地完成一题并更新一次 FSRS |
| `POST /api/practice/sessions/:id/complete` | 完成会话，并兜底完成已有作答的题目 |
| `GET /api/settings/fsrs` | 只读 FSRS 系统、保持率、最长间隔和版本 |
| `GET /api/stats/summary` | FSRS 今日评分、预测、分布与保持率 |

## 项目结构

```text
app.py              Flask 路由、题目完成事务、报告与统计
db.py               SQLite schema 与一次性 FSRS 重置迁移
fsrs_service.py     官方 Python FSRS 的唯一集成边界
memory.py           UTC 解析与自然日时区工具（不含调度逻辑）
tokenizer.py        Sudachi 分词与假名段
chunk_rules.py      词块合并规则
font_active.py      自托管字体内容子集
static/             单页前端、FSRS 统计和本地 Chart.js
tests/              pytest 测试
```

## 开发与测试

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt pytest
export DATA_DIR=./data APP_SECRET=dev-secret SESSION_COOKIE_SECURE=false TRUST_PROXY_COUNT=0
pytest -q
```

Flask `TESTING` 模式会关闭 FSRS interval fuzzing，确保结果稳定可重复；生产默认开启。

## 删除行为

- 删除句子或级联删除句集会删除相关原始作答、练习项和 FSRS 复习事件；
- 删除报告只隐藏报告，不回滚 Card 状态，也不删除用于 FSRS 统计的复习事件；
- 移动句子不会改变它的 FSRS 状态或历史。

## 许可

本项目采用 GNU Affero General Public License v3.0。
