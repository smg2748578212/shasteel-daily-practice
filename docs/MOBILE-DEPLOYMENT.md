# 手机后台与网站管理页

`management/` 提供人员、执行计划、任务队列和历史记录 API；`frontend/` 是同源的管理员页面。管理页复用已有站点的 `/assets/site.css`、`/assets/site.js` 和管理员登录会话。它不是独立登录系统。

## 部署结构

```text
浏览器 → 公网 Nginx → SSH 反向隧道 → 手机 127.0.0.1:5011
                                             ├─ 管理 API
                                             ├─ 单进程调度器
                                             └─ Chromium / 每人独立会话
```

在 Debian/Termux proot 的独立虚拟环境安装：

```bash
python3 -m venv /opt/daily-practice-venv
/opt/daily-practice-venv/bin/pip install -r requirements-management.txt
apt-get install --no-install-recommends chromium tesseract-ocr tesseract-ocr-chi-sim
```

ARM64 可以使用系统 Chromium，并设置 `DAILY_PRACTICE_BROWSER_EXECUTABLE=/usr/bin/chromium`，不必下载 x86 浏览器。

管理服务通过 `management/start.py` 读取 `DAILY_MANAGER_CONFIG` 指定的 JSON 文件，默认 `/var/lib/daily-practice/runtime.json`。该文件是字符串映射，除 `.env.example` 中的应用及模型设置外，需要：

| 参数 | 说明 |
| --- | --- |
| `DATABASE_URL` | 现有站点会话数据库，只用于验证站点用户与权限 |
| `SITE_SHARED_ROOT` | 现有站点 `shared/` 包的父目录，默认 `/opt/webapp` |
| `SHASTEEL_COMPLETION_API_URL` | 门户当日完成状态接口 |
| `DAILY_MANAGER_DATA` | 受保护的运行数据目录 |
| `DAILY_MANAGER_PORT` | 回环监听端口，默认 `5011` |
| `DAILY_MANAGER_PAUSED` | 迁移时设为 `1`，仅允许手动检查，暂不扫描每日计划 |

使用一个 Gunicorn worker、四个 HTTP 线程。调度器还持有文件锁，避免多个进程重复执行。应通过 runit 或其他服务管理器运行 `management/start.py`，包括停机时的子进程清理。

将 `frontend/daily-practice.html` 和两个资源文件部署到站点根目录及 `assets/`，登记页面 ID `daily-practice`，设为仅管理员可见。管理 API 还会独立检查管理员角色，前端隐藏入口不能替代后端权限验证。

公网 Nginx 将 `/api/daily-practice/` 原样转发到专用隧道的回环端口，并透传 `Host`、Cookie、Authorization 和 `X-Forwarded-Proto`。API 限制请求体大小，修改接口拒绝跨站请求。

## 运行语义

- 每人配置姓名、工号、班组、门户密码、每日时间和执行星期，时间统一使用 `Asia/Shanghai`。
- 全局串行执行；同一个人不能同时排队和运行多个任务。每人每天最多自动创建一次计划任务，可手动重试。
- 服务当天重启后，会补排已经到点但尚未确认完成的计划；已核实完成的人员不会重复答题。
- 暂停人员会取消尚未开始的排队任务，不中断已开始的任务。
- “检查状态”只查询门户完成标志，不生成题目、不提交答案、不发送通知。
- “立即执行”先检查是否已完成；未完成才答题，结束后再次调用门户接口验证。页面结果和脚本退出码均不能单独证明已完成。
- 失败、超时、异常重启和没有题目均不会写为完成。浏览器子进程最多运行 20 分钟。
- 移除人员会清除其已保存密码，并保留历史记录。原工号不会分配给其他人员。

## 数据与迁移

密码以 Fernet 加密后保存到 `manager.sqlite3`，API 仅返回 `password_set`。密钥文件 `credentials.key` 和运行配置均需权限 `0600`，数据目录需 `0700`。备份应同时保存数据库、密钥和运行配置，均不得提交 Git。

每人浏览器会话、令牌、截图和本地日志在运行数据目录下隔离。实际人员、门户地址、应用标识、题库、通知密钥和运行数据均不随源码分发。

切换顺序：备份旧计划；复制必要的私有配置和会话；在手机上完成离线测试、浏览器启动、两个只读状态检查；验证新 API 与网站权限；停用旧定时任务；启用手机调度。旧机器保留备份以便回退，不能让两个调度器同时运行。
