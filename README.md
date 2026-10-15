# 沙钢每日一练自动化

使用 Playwright 操作每日一练 H5 页面，结合本地 Excel 题库、OCR 和兼容 OpenAI SDK 的模型服务完成答题。保留原项目的多账号串行执行、会话复用、错误重试与可选 AstrBot 通知。

这是从现有部署整理的脱敏版本。仓库不包含真实工号、姓名、密码、密钥、应用实例参数、题库、浏览器会话、截图或运行日志。

新增网站管理后台：管理员可维护自动执行人员、班组、每日时间与执行星期，按人员选择“仅查看进度”或“完成每日一练”，查看进度和历史。新增人员默认仅查看进度，升级前已有人员保留自动完成模式。支持 ARM64 手机上的系统 Chromium，部署方式见 [手机后台与网站管理页](docs/MOBILE-DEPLOYMENT.md)。

新增自助接入页 `/daily-practice-public.html`：无需网站登录，仅填写本人门户工号与密码；验证后接入同一后台，每天北京时间 16:00 仅查询进度。门户自动获取姓名，无需第三个输入框。QQ 在每天 16:00 查询结束后统一提醒：已完成显示人数，未完成列姓名，并附完整名单链接，不再逐人发消息。公开名单页 `/daily-practice-roster.html` 展示所有姓名、班组和查询状态，通知日期链接保留当日快照。接口与重复账号处理见部署文档。

## 环境与安装

建议在 Linux 上使用 Python 3.10 或更新版本。进程锁依赖 `fcntl`，暂不原生支持 Windows。

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
python -m playwright install --with-deps chromium
```

部署网站管理服务或运行包含管理服务的全部离线测试时，改用 `requirements-management.txt`。

OCR 兜底需要安装 Tesseract 以及简体中文、英文语言包，例如 Debian/Ubuntu：

```bash
sudo apt-get install tesseract-ocr tesseract-ocr-chi-sim tesseract-ocr-eng
```

## 配置

```bash
cp .env.example .env
chmod 600 .env
# 编辑 .env，填入你自己的配置。
set -a
. ./.env
set +a
```

`.env` 是 shell 格式，不会由 Python 自动读取。定时任务也需要显式加载它。

| 配置 | 用途 |
| --- | --- |
| `SHASTEEL_EMPLOYEE_ID`、`SHASTEEL_PASSWORD` | 已获授权的门户账号 |
| `SHASTEEL_EMPLOYEE_NAME` | 可选显示名称 |
| `SHASTEEL_LOGIN_URL`、`SHASTEEL_SSO_REDIRECT_URL` | 门户登录及单点登录接口 |
| `SHASTEEL_APP_ID`、`SHASTEEL_APP_ID_STR`、`SHASTEEL_H5_BASE` | 当前部署的应用标识与 H5 地址 |
| `SHASTEEL_SESSION_API_URL`、`SHASTEEL_COMPLETION_API_URL` | 门户查询令牌交换及当日完成状态接口 |
| `SHASTEEL_GENERATE_API_URL`、`SHASTEEL_SUBMIT_API_URL` | 可选取题与整卷提交接口；同时配置时自动完成走 HTTP |
| `SHASTEEL_MY_APPS_URL` | 可选门户应用列表地址，当前答题流程不依赖它 |
| `QUESTION_BANK_PATH` | 本地 Excel 题库路径 |
| `DEEPSEEK_API_KEY` 或 `DEEPSEEK_TOKEN_FILE` | 模型服务密钥，或密钥文件路径 |
| `DEEPSEEK_BASE_URL` | 模型服务地址 |
| `DEEPSEEK_MODEL_FAST`、`DEEPSEEK_MODEL_PRO` | 优先及回退模型；回退名称留空时使用优先模型 |
| `ASTROBOT_API_URL`、`ASTROBOT_API_KEY`、`ASTROBOT_TARGETS` | 可选通知配置，多个接收人以逗号分隔；留空时关闭通知 |
| `DAILY_PRACTICE_STATE_DIR` | 可选运行数据目录，默认是项目目录 |

应用地址和标识应从自己的授权部署获取，仓库不提供生产配置。模型仅在题库未匹配等情况下调用；题目文本与选项会发送到所配置的模型服务。

## 本地题库

题库文件需自行准备，默认位置为 `data/question_bank.xlsx`。可先创建不含实际题目的格式模板：

```bash
python scripts/create_bank_template.py
```

模板中的列顺序与原项目一致：

- `单选题样题`：第 3 列题干，第 4–7 列选项，第 8 列答案字母。
- `判断题样题`：第 3 列题干，第 4 列答案；`1` 代表对，`2` 代表错。

自动答题要求题库文件存在，空模板可用于只依赖模型的流程。已验证的新答案会追加到本地题库文件。实际题库及题目内容不随仓库分发。

## 运行

仅验证登录与 SSO，不答题、不发送通知，也不要求题库文件存在：

```bash
python run.py --dry-run
```

执行答题（会向目标应用提交答案）：

```bash
python run.py
```

多账号时，将示例文件复制为本地配置并填写各账号授权信息：

```bash
cp extra_accounts.example.json extra_accounts.json
chmod 600 extra_accounts.json
python run.py --dry-run
```

主账号来自环境变量，其他账号来自 `extra_accounts.json`。同一账号不能重复配置。其他账号的会话和日志分别写入 `accounts/<工号>/`，批处理按顺序运行。

`--dry-run` 仍会访问门户并写入本地登录缓存。它验证账号与 SSO，不验证目标 H5 页面的全部行为。

## 状态与能力边界

已验证的 HTTP 流程通过门户账号密码验证身份、获取查询会话令牌，再读取当日完成标志。网站管理服务的“仅查看进度”全程使用 HTTP，优先复用本人令牌，过期或被拒绝时刷新，不启动浏览器；自动完成也先通过 HTTP 检查，已完成则直接结束，未完成时使用取题和整卷提交接口，直接使用题目响应中对应的答案；接口未配置的旧部署保留浏览器流程。仓库没有全班组汇总或任意历史日期查询接口；配置多个账号只是逐个运行，不会自动获得其他员工的访问权限。

新管理页汇总已获授权并录入的人员，由后台逐人检查当日状态并保存今后的执行历史。它不会补出门户未提供的过去日期记录。

门户页面、鉴权或题目格式变化时，可能需要调整浏览器选择器。此脱敏副本的验证使用离线测试，未触发生产环境答题或消息发送。

## 项目结构

```text
run.py                 单账号执行入口
account_runner.py      多账号校验、串行调度及进程锁
auth.py                门户登录、SSO 与登录缓存
browser_manager.py     Playwright 浏览器与会话
daily_practice.py      页面识别、答题及结果验证
question_bank.py       本地题库匹配与追加
llm_engine.py          模型调用与答案解析
ocr_engine.py          OCR 兜底
notify.py              可选 AstrBot 通知
logger.py              本地结构化日志与截图
```

## 离线检查

```bash
python -m unittest discover -s tests -v
python -m compileall -q .
```

`.gitignore` 排除了 `.env`、真实账号配置、会话令牌、运行记录、截图和题库。提交前仍应检查自己新增的文件。

管理页“全部名单”旁提供“一键查询未完成人员”：将今日未完成、尚未检查或查询失败的启用人员加入状态查询队列，跳过今日已完成、已暂停及执行或排队中的人员。此操作固定仅查询，不触发答题。管理接口为 `POST /api/daily-practice/check-uncompleted`，请求体 `{}`，沿用管理员登录验证；返回 `queued`、`completed`、`paused`、`active` 人数。
