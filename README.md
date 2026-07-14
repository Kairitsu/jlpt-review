# JLPT AI Tutor / Japanese Sentence Review

面向中文母语者的**日语句子重组与间隔复习** Web 应用。

用中文提示回忆日语原句，通过拖拽词块还原语序；本机 **SudachiPy（full 词典）** 完成多粒度分词与词块合并，无需外接 LLM。数据默认保存在本机 SQLite。

| 项目 | 说明 |
|------|------|
| 技术栈 | Flask · Gunicorn · SQLite · SudachiPy + SudachiDict-full |
| 前端 | 单页静态资源（`static/`）；中日文为自托管 Noto **内容子集**（UI+已导入句子） |
| 部署 | Docker Compose，默认仅监听 `127.0.0.1:3220` |
| 许可证 | [AGPL-3.0](LICENSE) |

## 功能概览

- **句集管理**：创建 / 重命名 / 删除句集（删除前需清空句子）
- **句子导入**：中文 + 日语；一键本机分块；可编辑词块后保存
- **练习模式**：待复习 / 句集练习；拖拽排序作答；跳过与对错统计
- **间隔复习（默认动态）**：指数遗忘模型 `R(t)=exp(-t/S)`，下次复习落在目标保持率（约 90%）附近；设置中可回退到固定间隔 1 → 3 → 7 → 14 → 30 天
- **数据统计**：遗忘曲线 / 学习情况 / 记忆持久度（底栏「统计」入口）
- **练习报告**：会话正确 / 错误 / 跳过记录与句子快照
- **访问控制**：会话登录、密码 PBKDF2 哈希、失败锁定；可在设置页修改账号密码
- **安全头**：CSP、HSTS（HTTPS 时）、禁缓存等默认开启

## 快速开始

### 要求

- Docker 与 Docker Compose
- （可选）本机 Python 3.12+，仅用于跑测试或不经 Docker 开发

### 用 Docker 运行

```bash
git clone https://github.com/Kairitsu/jlpt-review.git
cd jlpt-review

cp secrets/app.env.example secrets/app.env
# 编辑 secrets/app.env：设置强随机 APP_SECRET、初始用户名与密码

docker compose up -d --build
curl -s http://127.0.0.1:3220/api/health
```

浏览器打开：<http://127.0.0.1:3220>

首次启动若配置了 `INIT_USERNAME` / `INIT_PASSWORD` 且库中尚无账号，将写入初始登录凭据。之后请在应用**设置**中修改密码；生产环境不要使用示例密码。

### 环境变量（`secrets/app.env`）

| 变量 | 说明 |
|------|------|
| `APP_SECRET` | Flask 会话密钥原料（请使用足够长的随机串） |
| `INIT_USERNAME` | 仅在尚未配置账号时生效的初始用户名 |
| `INIT_PASSWORD` | 仅在尚未配置账号时生效的初始密码 |
| `SESSION_COOKIE_SECURE` | 生产 HTTPS 下保持 `true`；纯 HTTP 本地调试可设 `false` |
| `TRUST_PROXY_COUNT` | 前置反向代理层数（Caddy/Nginx 通常为 `1`） |
| `DATA_DIR` | 数据目录（Compose 内默认 `/app/data`） |

**切勿**将真实的 `secrets/app.env`、数据库文件或 Basic Auth 哈希提交到 Git。仓库只包含 `secrets/app.env.example`。

### 生产反向代理

应用在 Compose 中绑定 **`127.0.0.1:3220 → 容器 8000`**，不直接暴露公网。由本机 Caddy / Nginx 终止 TLS 并反代，例如：

```caddy
your-domain.example {
    reverse_proxy 127.0.0.1:3220
}
```

## 本机分块逻辑（摘要）

1. 同时跑 Sudachi **SplitMode A / B / C**，并校验每种结果可逐字还原原句  
2. 以 **B** 为底边界；**C** 保护复合 / 专有等长词  
3. 用 **A** 的词性、辞书形与活用边界识别接尾、サ变、助动词、否定等  
4. `chunk_rules.py` 集中维护合并规则  
5. 标点独立成块；每次分块生成唯一词块 ID  
6. 保存前校验：ID 唯一、拼接无损、正确顺序与词块顺序一致  

编辑已有句子时默认使用**已保存词块**；点击「重新分块」才按当前规则重新生成。

SudachiDict-full 在**镜像构建**时经 `requirements.txt` 安装；容器运行期不需要访问外网下载词典。

## 项目结构

```text
app.py                 Flask 路由、练习 / 复习、统计 API
auth.py                登录锁定与会话鉴权
security.py            密码哈希 / 校验（PBKDF2）
db.py                  SQLite schema、幂等迁移与设置读写
memory.py              遗忘模型、四档认知映射、调度间隔
tokenizer.py           Sudachi 分词封装
chunk_rules.py         词块合并规则
static/                前端 HTML / CSS / JS（含 stats.js、vendor/chart.js）
font_active.py         按 UI+句库生成预置字体子集（保存句子后后台重建）
font-sources/          Noto Sans SC/JP 源 OTF（仅供 subset，不直出浏览器）
scripts/backup-db.sh   SQLite 一致性备份
scripts/build_font_subsets.py  （可选）离线 unicode-range 全量分片工具
secrets/app.env.example
tests/                 pytest
docker-compose.yml
Dockerfile
```

## 统计与动态复习

### 数据库（`init_db` 幂等）

| 变更 | 说明 |
|------|------|
| `sentences.stability` | 记忆稳定度 S（天），默认 `1.0` |
| `sentences.review_count` / `lapse_count` | 有效复习次数 / 遗忘次数 |
| `review_events` | 每次最终答题明细（result、duration_ms、S 前后、间隔等） |
| `attempts.duration_ms` / `attempt_n` / `grade` | 作答时长、本会话核对次数、四档认知结果 |

旧库启动时自动 `ALTER` 加列；若 `review_events` 为空且已有 `attempts`，会按 `correct→known / wrong→forgotten / skipped→skipped` 回填一次。

### 认知四档映射

| 条件 | 结果 |
|------|------|
| 跳过 | skipped |
| 答错 | forgotten（忘记） |
| 答对且本会话第 2+ 次核对 | fuzzy（模糊） |
| 答对、首次、时长 ≤15s | mastered（熟知） |
| 答对、首次、更慢或无时长 | known（认识） |

### 调度模式（设置页）

- `GET/PUT /api/settings/scheduler` → `{ "mode": "dynamic" | "fixed" }`
- **dynamic**（默认）：`t = -S · ln(0.9)`；答对增大 S，答错重置 S 并立即到期
- **fixed**：沿用 `correct_streak` 与 1/3/7/14/30 天阶梯

### 统计 API（只读 JSON）

| 接口 | 内容 |
|------|------|
| `GET /api/stats/forgetting-curve` | 艾宾浩斯理论曲线 + 用户实测保持率（按距上次复习天数分桶） |
| `GET /api/stats/learning?granularity=day\|week\|month` | 时间桶内熟知/认识/模糊/忘记与新学/复习计数 + 今日汇总 |
| `GET /api/stats/retention?granularity=day\|week\|month` | 记忆持久度 ≥10/30/60/90 天的累计句子数与占比 |

图表库为本地 `static/vendor/chart.umd.min.js`（无 CDN）。

## 数据与备份

- 数据库路径：`data/japanese_sentence_review.sqlite3`（Compose 挂载 `./data`）
- 备份：

```bash
./scripts/backup-db.sh
# 输出到 backups/japanese_sentence_review-YYYYMMDD-HHMMSS.sqlite3
```

`data/` 与 `backups/` 已在 `.gitignore` 中忽略。

## 开发与测试

不经 Docker 时（需本机安装依赖，含 SudachiDict-full）：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt pytest
export DATA_DIR=./data APP_SECRET=dev-secret SESSION_COOKIE_SECURE=false TRUST_PROXY_COUNT=0
pytest -q
```

Docker 镜像内默认**不**拷贝 `tests/`（见 `.dockerignore`）；测试请在宿主或 CI 中运行。

## 安全说明

- 真实密钥、登录口令、SQLite 用户数据**不得**入库  
- 默认仅监听环回地址；公网访问应走 HTTPS 反代  
- 登录失败超过阈值会锁定（见 `auth.py`）  
- 密码以 PBKDF2-SHA256 加盐哈希存储，不明文落盘  

若你发现安全问题，请勿在公开 issue 中粘贴真实凭据或用户句子数据。

## 许可

本项目以 **GNU Affero General Public License v3.0（AGPL-3.0）** 发布。完整条款见 [LICENSE](LICENSE)。

若你在服务器上修改并对外提供本软件的网络服务，AGPL 要求你向用户提供对应修改后的完整源代码。
