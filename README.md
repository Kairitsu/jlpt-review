# JLPT Review

面向中文日语学习者的句子重组与间隔复习 Web 应用。

根据中文提示，将打乱的日语词块重新排列成完整句子。系统会使用本地日语分词器生成词块和假名注音，并通过官方 FSRS 算法安排后续复习。

- 本地分词，不依赖 LLM 或外部 API
- 官方 FSRS 6 间隔复习调度
- 支持手机、平板和桌面浏览器
- 数据保存在自己的服务器中
- Docker Compose 一键部署

## 项目简介

单纯阅读例句，很容易产生“看得懂，但想不起来”的错觉。

JLPT Review 将日语句子转换为主动回忆练习：

1. 页面展示中文翻译。
2. 日语原句被拆分为若干词块。
3. 用户通过点选或拖拽重新排列词块。
4. 系统根据完整作答过程生成 FSRS 评分。
5. FSRS 自动决定下一次复习时间。

项目适合用于积累 JLPT 例句、教材句子、语法例句、错题句子以及个人日语表达库。

> 本项目不提供预置题库。句子内容由用户自行添加和管理。

## 核心功能

### 句子重组练习

- 根据中文翻译还原日语原句
- 点选词块完成排列
- 拖拽已经选择的词块调整顺序
- 支持重置当前排列
- 核对后显示正确句子和假名注音
- 答错后可立即重新练习当前题
- 首次答对后若上一轮也是首次答对，自动记为“轻松掌握”
- 使用“上一题”和“下一题”自由切换题目
- 切换未作答题目时不会自动显示答案

### 句集与题库管理

- 创建多个句集
- 重命名句集
- 删除句集及其全部句子
- 在句集之间批量转移句子
- 添加、编辑和删除句子
- 按中文或日语内容搜索
- 按创建时间、最近练习时间或难度排序
- 勾选指定句子进行专项练习
- 从整个句集中随机抽取指定数量的句子

移动句子不会改变其 FSRS 状态、复习时间或历史记录。

删除句子或级联删除句集会永久删除相关作答记录、FSRS 状态和复习历史。

### 本地日语分词

输入中文翻译和完整日语原句后，系统会在服务器本地完成分析：

- 使用 SudachiPy
- 使用 SudachiDict-full 完整词典
- 综合 SplitMode A、B、C 三种粒度
- 自动识别词形变化、助动词、复合词和常见语法结构
- 自动生成汉字假名注音
- 保证全部词块按顺序拼接后能够无损还原原句

自动分块后仍可手动：

- 拆分词块
- 合并相邻词块
- 修改词块文字
- 重新执行自动分块

句子内容不会被发送到外部模型、云端分词服务或第三方 API。

### FSRS 间隔复习

项目使用官方 Python `fsrs` 包，不自行复制或改写 FSRS 公式。

当前默认配置：

| 配置 | 当前值 |
| --- | --- |
| FSRS 版本 | 6.3.1 |
| 目标保持率 | 90% |
| 最长复习间隔 | 36500 天 |
| Learning Steps | 关闭 |
| Relearning Steps | 关闭 |
| 生产环境间隔扰动 | 开启 |
| 测试环境间隔扰动 | 关闭 |

关闭分钟级 Learning Steps 和 Relearning Steps 后：

- 新句子完成一次有效评分后直接进入 Review 状态
- 答错的旧句子不会额外进入固定的分钟级重学步骤
- 后续间隔由 FSRS 根据稳定度、难度、评分和目标保持率计算
- 不会在 FSRS 结果之外叠加“固定减半”“固定增加若干天”等自定义规则

### 作答与 FSRS 评分

点击“核对答案”只会保存一次原始作答。

提交本轮练习时，系统会综合同一道题的完整作答过程，生成一次最终 FSRS 评分：

| 作答情况 | FSRS 评分 | 页面显示 |
| --- | --- | --- |
| 从未核对答案 | 不更新 | 未回答 |
| 第一次核对即答对，上一轮可靠复习不是首次答对或不存在 | Good | 认识 |
| 第一次核对即答对，上一轮可靠复习也是首次答对 | Easy | 轻松掌握 |
| 第一次答错、第二次答对 | Hard | 模糊 |
| 第一次答错后未再次核对，或第二次仍然答错 | Again | 忘记 |
| 第三次或更多次才答对 | Again | 忘记 |

第二次答错后评分锁定为 Again；第一次即答对后，后续可选重练不会改变本轮评分。答题耗时只用于统计，不参与评分。Easy 完全由连续两轮首次核对即答对自动产生，前端不能指定评分。

每个句子在同一轮练习中最多产生一次 FSRS 更新。重复提交不会导致重复调度。核对请求使用客户端 `attemptId` 幂等写入，不会覆盖历史核对记录。

### 未回答题目处理

用户可以在练习中直接切换到上一题或下一题。

提交本轮时，如果仍有题目从未核对，系统会弹出确认提示，并明确说明：

- 未回答题目不会写入 FSRS 复习记录
- 不会被判定为 Again
- Stability 和 Difficulty 不会改变
- 下次复习时间不会改变
- 原本已经到期的题目仍会保持待复习状态

用户可以返回继续作答，也可以确认提交。

练习报告会保留未回答题目及其临时排列结果。

在报告页选择“再练一轮”时，系统会优先加入本轮未回答的句子，再按照当前到期顺序补充其他句子。

### 学习状态

首页会按句集显示：

- 已学习句子数
- 总句子数
- 当前待复习数量
- 今日学习数量

“待复习”和“今日学习”均可点击查看具体句子。

待复习列表按照以下顺序排列：

1. 下次复习时间
2. 句子创建时间
3. 句子 ID

“今日学习”按照设置的自然日边界统计，并显示每个句子当天最后一次学习时间。

### 练习报告与历史记录

每轮正式提交的练习都会生成报告，包括：

- 本轮句子总数
- Again、Hard、Good、Easy 数量
- 未回答数量
- 每道题的中文提示
- 用户最终排列
- 正确日语原句
- 对应的 FSRS 评分

历史报告可以随时重新打开，也可以从报告中开始下一轮练习。

删除报告只会隐藏这条练习历史：

- 不会回滚已经完成的 FSRS 更新
- 不会改变句子的 Stability 或 Difficulty
- 不会删除用于统计的 FSRS 复习事件

### 学习概览

统计页面提供：

- 以前天、昨天、今天、明天、后天组成的固定 5 日自然日时间轴
- 过去两天和今天按正式 `review_events` 汇总的完成、新学、复习和练习时长
- 忘记、模糊、认识、轻松掌握的有效评分数量与占比
- 当前待复习数量，以及明天、后天各自自然日内预计到期的数量
- 由官方 FSRS 逐句计算当前回忆概率后形成的记忆掌握度分组

未来日期尚未发生的实际学习数据返回空值，不伪造为 0；过去日期不根据当前
`next_review_at` 反推历史到期数量。尚未形成有效复习记录的句子单独统计，不进入
有效掌握度比例的分母。

### 时区设置

FSRS 调度时间统一使用 UTC 保存和计算。

用户设置的 IANA 时区只影响：

- “今日学习”的自然日边界
- 今日统计
- 页面中的日期和时间显示

时区不会改变 FSRS 本身的调度结果。

如果未设置用户时区，应用将使用服务器时区划分自然日。

### 访问认证

应用内置单用户访问认证：

- 用户名和密码可以通过环境变量初始化
- 密码仅保存 PBKDF2-SHA256 哈希
- 登录会话有效期为 7 天
- Cookie 使用 `HttpOnly` 和 `SameSite=Lax`
- HTTPS 环境支持 Secure Cookie
- 连续登录失败会触发临时锁定
- 可以在设置页面修改或关闭应用认证

对于公网部署，仍建议同时使用 HTTPS 和必要的网络访问控制。

## 技术栈

| 模块 | 技术 |
| --- | --- |
| 后端 | Python 3.12、Flask、Gunicorn |
| 数据库 | SQLite、WAL |
| 复习调度 | Py-FSRS 6.3.1 |
| 日语分词 | SudachiPy、SudachiDict-full |
| 前端 | 原生 HTML、CSS、JavaScript |
| 图表 | Chart.js |
| 字体处理 | FontTools、Brotli |
| 部署 | Docker、Docker Compose |

项目采用轻量级单体架构，不需要 Node.js、Redis、PostgreSQL 或外部模型服务。

## 快速部署

### 1. 克隆仓库

```bash
git clone https://github.com/Kairitsu/jlpt-review.git
cd jlpt-review
```

### 2. 创建环境变量文件

```bash
cp secrets/app.env.example secrets/app.env
```

编辑 `secrets/app.env`：

```env
APP_SECRET=replace-with-a-long-random-secret
INIT_USERNAME=admin
INIT_PASSWORD=replace-with-a-long-random-password
SESSION_COOKIE_SECURE=true
TRUST_PROXY_COUNT=1
```

可以使用以下命令生成随机密钥：

```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

主要变量说明：

| 变量 | 说明 |
| --- | --- |
| `APP_SECRET` | Flask 会话签名密钥，必须使用随机长字符串 |
| `INIT_USERNAME` | 数据库尚未配置认证时创建的初始用户名 |
| `INIT_PASSWORD` | 数据库尚未配置认证时创建的初始密码 |
| `SESSION_COOKIE_SECURE` | 使用 HTTPS 时设为 `true` |
| `TRUST_PROXY_COUNT` | 应用前方可信反向代理的层数 |
| `DATA_DIR` | 数据库、迁移备份和活动字体目录 |
| `FSRS_ENABLE_FUZZING` | 是否启用 FSRS 间隔扰动，生产环境默认开启 |
| `FONT_SOURCES_DIR` | 可选，自定义 Noto 字体源文件目录 |

如果直接通过本地 HTTP 访问，而没有 HTTPS 反向代理，应设置：

```env
SESSION_COOKIE_SECURE=false
TRUST_PROXY_COUNT=0
```

### 3. 启动应用

```bash
docker compose up -d --build
```

默认监听：

```text
127.0.0.1:3220
```

应用不会直接绑定所有公网网卡。

### 4. 检查运行状态

```bash
docker compose ps
curl -s http://127.0.0.1:3220/api/health
```

正常情况下会返回类似结果：

```json
{
  "ok": true,
  "time": 1780000000,
  "tokenizer": "sudachipy-full-abc"
}
```

### 5. 配置 HTTPS 反向代理

生产环境建议使用 Caddy、Nginx 或其他反向代理，将 HTTPS 请求转发至：

```text
http://127.0.0.1:3220
```

使用一层反向代理时：

```env
SESSION_COOKIE_SECURE=true
TRUST_PROXY_COUNT=1
```

修改环境变量后重新创建容器：

```bash
docker compose up -d --build
```

## 更新应用

拉取最新代码并重新构建：

```bash
git pull
docker compose up -d --build
```

更新前建议先备份数据库。

## 数据存储

Docker Compose 默认将宿主机的 `./data` 挂载到容器内的 `/app/data`。

主要数据位置：

```text
data/
├── japanese_sentence_review.sqlite3
├── backups/
└── fonts/
    └── active/
```

其中：

- `japanese_sentence_review.sqlite3`：主数据库
- `backups/`：部分自动迁移创建的数据库快照
- `fonts/active/`：根据当前界面和句库内容生成的字体子集

数据库使用 SQLite WAL 模式。

真实数据、密码和密钥均已通过 `.gitignore` 排除，不应提交到 Git。

## 数据库备份

仓库提供 SQLite Backup API 备份脚本：

```bash
./scripts/backup-db.sh
```

备份文件会写入仓库根目录的：

```text
backups/
```

也可以手动复制整个 `data` 目录，但建议在容器停止或数据库没有写入时进行。

停止后备份：

```bash
docker compose stop
cp -a data "data-backup-$(date +%Y%m%d-%H%M%S)"
docker compose start
```

## 重要升级说明

### 从旧版非 FSRS 数据库升级

从早期自定义记忆模型版本首次升级到 FSRS 版本时，会执行一次不可逆迁移。

迁移会保留：

- 句集
- 中文翻译
- 日语原句
- 词块
- 假名注音
- 句子所属句集

迁移会永久删除：

- 旧作答记录
- 旧练习会话
- 旧练习报告
- 旧复习事件
- 旧记忆状态
- 旧调度设置

全部现有句子会被初始化为立即到期的 FSRS 新卡。

升级前务必执行：

```bash
./scripts/backup-db.sh
```

该迁移由 `schema_migrations` 表中的 `fsrs_v1_reset` 标记保护，只会执行一次。

### 从已有 FSRS 数据升级到无短学习步骤版本

如果数据库已经使用 FSRS，但仍来自启用 Learning Steps 或 Relearning Steps 的版本，应用会执行一次历史重排迁移。

迁移过程会：

1. 使用 SQLite Backup API 自动创建完整数据库快照
2. 按时间顺序读取每个句子的历史复习事件
3. 转换为官方 FSRS `ReviewLog`
4. 调用 `Scheduler.reschedule_card()` 重放历史
5. 更新当前 FSRS 状态和下次复习时间

迁移不会：

- 删除或修改历史复习事件
- 删除原始作答
- 删除练习会话
- 删除练习报告
- 补造不存在的复习记录
- 清空句子内容

迁移在单个写事务中执行，失败时会完整回滚。

自动备份保存在：

```text
data/backups/
```

迁移标记为：

```text
fsrs_no_short_steps_v1
```

成功后不会在后续启动时重复执行。

## 本地开发

### 创建虚拟环境

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Windows PowerShell：

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

### 安装依赖

```bash
pip install -r requirements.txt pytest
```

### 配置开发环境

Linux 或 macOS：

```bash
export DATA_DIR=./data
export APP_SECRET=dev-secret
export SESSION_COOKIE_SECURE=false
export TRUST_PROXY_COUNT=0
```

Windows PowerShell：

```powershell
$env:DATA_DIR="./data"
$env:APP_SECRET="dev-secret"
$env:SESSION_COOKIE_SECURE="false"
$env:TRUST_PROXY_COUNT="0"
```

### 运行测试

```bash
pytest -q
```

Flask `TESTING` 模式会关闭 FSRS interval fuzzing，使调度结果稳定且可重复测试。

### 启动开发服务

```bash
flask --app app run --host 127.0.0.1 --port 3220
```

## 项目结构

```text
.
├── app.py
├── auth.py
├── chunk_rules.py
├── db.py
├── font_active.py
├── fsrs_service.py
├── memory.py
├── security.py
├── tokenizer.py
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
├── font-sources/
├── scripts/
│   ├── backup-db.sh
│   └── build_font_subsets.py
├── static/
│   ├── index.html
│   ├── app.js
│   ├── stats.js
│   ├── styles.css
│   └── vendor/
└── tests/
```

主要模块：

| 文件 | 职责 |
| --- | --- |
| `app.py` | Flask 路由、练习流程、报告、设置和统计接口 |
| `db.py` | SQLite Schema、索引、迁移和自动备份 |
| `fsrs_service.py` | 官方 FSRS 包的唯一集成边界 |
| `tokenizer.py` | Sudachi A/B/C 分析、分块和假名注音 |
| `chunk_rules.py` | 日语词块边界合并规则 |
| `memory.py` | UTC 时间解析和自然日时区工具 |
| `auth.py` | 登录状态、失败次数和临时锁定 |
| `security.py` | 密码哈希与验证 |
| `font_active.py` | 基于当前句库内容生成字体子集 |
| `static/app.js` | 单页应用、题库和练习交互 |
| `static/stats.js` | 学习概览页面、图表交互和无障碍摘要 |

## 主要 API

| 方法与路径 | 用途 |
| --- | --- |
| `GET /api/health` | 健康检查 |
| `GET /api/dashboard` | 首页句集和学习状态 |
| `GET /api/collections/:id/study-status/due` | 查看待复习句子 |
| `GET /api/collections/:id/study-status/today` | 查看今日学习句子 |
| `POST /api/collections` | 创建句集 |
| `PATCH /api/collections/:id` | 重命名句集 |
| `DELETE /api/collections/:id` | 删除句集 |
| `POST /api/sentences/organize` | 本地分析日语原句 |
| `POST /api/sentences` | 添加句子 |
| `PUT /api/sentences/:id` | 编辑句子 |
| `POST /api/sentences/move` | 批量转移句子 |
| `POST /api/practice/sessions` | 创建练习会话 |
| `POST /api/practice/sessions/:id/attempts` | 使用必填 `attemptId` 幂等追加一次原始核对记录，不更新 FSRS |
| `POST /api/practice/sessions/:id/sentences/:sentenceId/complete` | 幂等地完成一题并更新一次 FSRS |
| `POST /api/practice/sessions/:id/complete` | 正常或提前完成会话，并通过统一规则结算已有核对的题目 |
| `GET /api/reports` | 获取练习历史 |
| `GET /api/reports/:id` | 获取报告明细 |
| `DELETE /api/reports/:id` | 隐藏练习报告 |
| `GET /api/stats/summary` | 获取 FSRS 汇总统计 |
| `GET /api/settings/fsrs` | 获取当前 FSRS 配置 |
| `GET /api/settings/timezone` | 获取时区设置 |
| `PUT /api/settings/timezone` | 保存时区设置 |

## 字体子集

项目可以根据以下内容生成自托管的 Noto Sans 字体子集：

- 应用界面文字
- 句集名称
- 中文翻译
- 日语原句
- 假名注音

新增、编辑、删除或移动句子后，应用会异步检查是否需要重新构建字体。

浏览器只需下载当前内容实际使用到的字符，而不需要加载完整的中日韩字体文件。

生成的 WOFF2 文件采用内容哈希命名，并使用长期缓存。

## 安全说明

应用设置了以下响应头：

- Content Security Policy
- `X-Content-Type-Options: nosniff`
- `X-Frame-Options: DENY`
- `Referrer-Policy: no-referrer`
- Permissions Policy
- HTTPS 环境下的 HSTS

同时：

- API 请求体最大为 2 MiB
- 密码不会以明文保存
- 数据库、密钥和备份默认不会进入 Git
- 应用默认仅监听 `127.0.0.1`
- 登录失败达到限制后会临时锁定用户名和来源 IP

这些措施不能替代服务器系统更新、防火墙、HTTPS 和正确的反向代理配置。

## 依赖项目

本项目主要使用：

- [Py-FSRS](https://github.com/open-spaced-repetition/py-fsrs)
- [SudachiPy](https://github.com/WorksApplications/SudachiPy)
- [SudachiDict](https://github.com/WorksApplications/SudachiDict)
- [Flask](https://github.com/pallets/flask)
- [Chart.js](https://github.com/chartjs/Chart.js)
- [FontTools](https://github.com/fonttools/fonttools)
- [Noto Fonts](https://github.com/notofonts)

## 许可证

本项目采用 [GNU Affero General Public License v3.0](LICENSE)。

如果修改本项目并通过网络向用户提供服务，需要按照 AGPL-3.0 的要求向该服务的用户提供对应源代码。
