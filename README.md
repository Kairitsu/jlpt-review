# 句子重组 · jlpt-review

面向中文母语者的**日语句子重组与间隔复习** Web 应用（GitHub：`jlpt-review`）。

用中文提示回忆日语原句，通过点选 / 拖拽词块还原语序；本机 **SudachiPy（full 词典）** 完成多粒度分词、词块合并与假名注音，**无需外接 LLM**。数据默认保存在本机 SQLite。

| 项目 | 说明 |
|------|------|
| 技术栈 | Flask · Gunicorn · SQLite · SudachiPy + SudachiDict-full |
| 前端 | 单页静态资源（`static/`）；中日文为自托管 Noto **内容子集**（UI + 已导入句子） |
| 部署 | Docker Compose，默认仅监听 `127.0.0.1:3220` |
| 许可证 | [AGPL-3.0](LICENSE) |

## 功能概览

### 首页

- 当前句集进度（已学习 / 总数）、**待复习** 与 **今日学习**（按设置的时区自然日统计）
- 选择句集与本轮数量后，从到期最早的句子开始复习

### 题库（句集）

- 按创建时间 / 错误率 / 最近练习排序；搜索中文或日语
- **句集随机练习** 或 **勾选句子专项练习**
- **批量转移**句子到其他句集
- **管理句集**：重命名；删除时可选级联清空其中全部句子及记忆 / 练习历史（至少保留一个句集）

### 添加 / 编辑句子

- 输入中文翻译 + 完整日语原句 → 本机一键分块
- 预览词块：拆分、合并相邻、修改文字；保存前校验拼接无损
- 自动生成汉字 **假名注音**（`<ruby>`），练习与预览中展示
- 编辑已有句子时默认使用**已保存词块**；再次「自动分块」才会按当前规则重新生成

### 练习

- 词块乱序展示：点选加入答案区，答案区内可**拖拽重排**
- 核对 / 跳过；按**词块文字**判题（同形不同 id 可互换，例如两个「し」）
- 记录作答时长（用于统计，**不参与**认知分级）
- 练习报告中可查看会话明细，并可**重练本轮错题**（`retryWrong`：该会话中首次答对也记为「模糊」）

### 统计

底栏「统计」入口，图表库为本地 `static/vendor/chart.umd.min.js`（无 CDN）：

- **遗忘曲线**：理论参考曲线 + 用户实测保持率（样本不足时向理论先验靠拢）
- **学习情况**：按日 / 周 / 月分桶的认识 / 模糊 / 忘记与新学 / 复习
- **记忆持久度**：稳定度对应持有 ≥10 / 30 / 60 / 90 天的句子占比

### 报告

- 已完成会话的正确 / 错误 / 跳过与句子快照
- 可删除单条报告（**不影响**句子的记忆进度与 SRS 字段）

### 设置

- **访问认证**：用户名 + PBKDF2 密码哈希；可关闭认证
- **时区**（IANA）：决定「今日学习」与统计页自然日分界；未设置则用服务器本地时区
- **复习调度**：界面统一说明**动态间隔**原理（见下文）
- 退出登录

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
| `TRUST_PROXY_COUNT` | 前置反向代理层数（Caddy / Nginx 通常为 `1`） |
| `DATA_DIR` | 数据目录（Compose 内默认 `/app/data`） |

**切勿**将真实的 `secrets/app.env`、数据库文件或 Basic Auth 哈希提交到 Git。仓库只包含 `secrets/app.env.example`。

### 生产反向代理

应用在 Compose 中绑定 **`127.0.0.1:3220 → 容器 8000`**，不直接暴露公网。由本机 Caddy / Nginx 终止 TLS 并反代，例如：

```caddy
your-domain.example {
    reverse_proxy 127.0.0.1:3220
}
```

## 本机分块与假名（摘要）

1. 同时跑 Sudachi **SplitMode A / B / C**，并校验每种结果可逐字还原原句  
2. 以 **B** 为底边界；**C** 保护复合 / 专有等长词  
3. 用 **A** 的词性、辞书形与活用边界识别接尾、サ变、助动词、否定等  
4. `chunk_rules.py` 集中维护合并规则  
5. 标点独立成块；每次分块生成唯一词块 ID  
6. 保存前校验：ID 唯一、拼接无损、正确顺序与词块顺序一致  
7. 用 A 模式读音生成 `furigana_json`（仅汉字带 ruby）；失败时退化为纯文本段，不影响保存  

SudachiDict-full 在**镜像构建**时经 `requirements.txt` 安装；容器运行期不需要访问外网下载词典。

保存句子后会在后台按 UI + 句库字形重建 Noto 字体子集（`font_active.py`），浏览器通过 `/api/fonts/faces.css` 加载。

## 记忆模型与认知分级

### 认知三档（+ 跳过）

作答映射见 `memory.py` 的 `grade_attempt`。**作答时长不参与分级**，仅写入统计。

| 条件 | 结果（UI） |
|------|------------|
| 跳过 | `skipped`（跳过） |
| 答错 | `forgotten`（忘记） |
| 答对，且本会话第 2+ 次核对，或错题重练会话 | `fuzzy`（模糊） |
| 答对，且本会话第一次核对 | `known`（认识） |

历史数据中的 `mastered`（旧「熟知」）在启动时会迁移为 `known`。

### 稳定度与下次复习

模型：`R(t) = exp(-t / S)`，目标保持率约 90%，下次间隔  
`t = -S · ln(0.9)`。

| 结果 | 对 S 的影响 | 到期 |
|------|-------------|------|
| `known` | S × 2 | 按新 S 计算间隔 |
| `fuzzy` | S × 1.2 | 按新 S 计算间隔 |
| `forgotten` | 重置为初始值 `1.0` | **立即到期** |
| `skipped` | 不变 | 不变 |

S 限制在 `0.3`～`365` 天。

应用界面统一使用上述**动态调度**。后端仍保留 `GET/PUT /api/settings/scheduler` 的 `fixed` 模式（固定阶梯 1 → 3 → 7 → 14 → 30 天），供测试或高级调用；设置页不提供切换。

### 主要统计 API（只读 JSON）

| 接口 | 内容 |
|------|------|
| `GET /api/stats/forgetting-curve` | 理论曲线 + 用户按间隔天数分桶的实测保持率 |
| `GET /api/stats/learning?granularity=day\|week\|month` | 时间桶内认识 / 模糊 / 忘记与新学 / 复习 + 今日汇总 |
| `GET /api/stats/retention?granularity=day\|week\|month` | 记忆持久度 ≥10 / 30 / 60 / 90 天的累计与占比 |

## 数据与备份

- 数据库路径：`data/japanese_sentence_review.sqlite3`（Compose 挂载 `./data`）
- 备份：

```bash
./scripts/backup-db.sh
# 输出到 backups/japanese_sentence_review-YYYYMMDD-HHMMSS.sqlite3
```

- **删除句子**或**级联删除句集**时，会硬删相关 `attempts` / `review_events` 等历史，避免孤儿统计  
- 启动时 `init_db` 幂等迁移：补列、旧 attempts 回填 `review_events`、`mastered` → `known`、清理孤儿历史等  
- `data/` 与 `backups/` 已在 `.gitignore` 中忽略

## 项目结构

```text
app.py                 Flask 路由：句集 / 句子 / 练习 / 报告 / 设置 / 统计 / 字体
auth.py                登录锁定与会话鉴权
security.py            密码哈希 / 校验（PBKDF2）
db.py                  SQLite schema、幂等迁移与设置读写
memory.py              遗忘模型、认知分级、调度间隔、时区自然日
tokenizer.py           Sudachi 分词、分块校验、假名段
chunk_rules.py         词块合并规则
font_active.py         按 UI+句库生成预置字体子集
static/                前端 HTML / CSS / JS（app.js、stats.js、vendor/chart.js）
font-sources/          Noto Sans SC/JP 源 OTF（仅供 subset，不直出浏览器）
scripts/backup-db.sh   SQLite 一致性备份
scripts/build_font_subsets.py  （可选）离线 unicode-range 全量分片工具
secrets/app.env.example
tests/                 pytest
docker-compose.yml
Dockerfile
```

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
- 登录失败超过阈值会锁定（默认 5 次失败，锁定 15 分钟，见 `auth.py`）  
- 密码以 PBKDF2-SHA256 加盐哈希存储，不明文落盘  
- 响应默认带安全头（CSP、HTTPS 时的 HSTS、禁缓存等）  

若你发现安全问题，请勿在公开 issue 中粘贴真实凭据或用户句子数据。

## 许可

本项目以 **GNU Affero General Public License v3.0（AGPL-3.0）** 发布。完整条款见 [LICENSE](LICENSE)。

若你在服务器上修改并对外提供本软件的网络服务，AGPL 要求你向用户提供对应修改后的完整源代码。
