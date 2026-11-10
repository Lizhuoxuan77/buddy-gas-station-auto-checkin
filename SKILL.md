---
name: buddy-gas-station-auto-checkin
description: "自动领取 WorkBuddy「Buddy加油站 / 今日礼包」每日积分。当用户说“帮我签到”“领一下今日积分”“今天签到了吗”“以后每天自动领积分”“设置自动签到”，或提到 WorkBuddy 积分不够、断签、连续签到天数时使用本技能。通过客户端本地 wbipc 通道转发官方签到接口，复用当前登录态；幂等、不模拟点击、不读取或保存 token。用户要的是实际领取到账（不只是提醒）时优先使用本技能。"
description_en: "Automatically claim WorkBuddy Buddy Gas Station daily credits. Use this skill when the user says 'check me in', 'claim today's points', 'did I check in today', 'set up automatic daily check-in', or mentions low credits, a broken streak, or consecutive check-in days. Forwards the official check-in API through the client's local wbipc channel using the live session; idempotent, no UI automation, no token reading or storage. Use it when the user wants the credits actually claimed, not just a reminder."
license: "MIT"
agent_created: true
---

# Buddy加油站自动签到

复用 WorkBuddy 客户端的当前登录态，领取「Buddy加油站」每日积分。脚本幂等：先查后领，
今天已领则直接跳过，不会重复发分。

## 前置条件

1. WorkBuddy 客户端正在运行，且当前账号已登录（客户端默认开机自启）。
2. 平台为 Windows。脚本通过客户端本地命名管道 `wbipc` 转发请求，macOS / Linux 不支持。
3. 不向用户索要 token、Cookie、验证码、密码；脚本不读取、不解密、不保存这些凭据。

第 1 条不满足时不要尝试绕过：直接请用户打开 WorkBuddy、确认登录后重跑。

## 执行

下文命令中的 `{SKILL_DIR}` 指本 `SKILL.md` 所在目录，执行时替换成绝对路径，
例如 `C:\Users\<用户名>\.workbuddy\skills\buddy-gas-station-auto-checkin`。
脚本只依赖标准库，Python 3.7+ 即可，无需安装任何包。

```bat
python -I "{SKILL_DIR}\scripts\checkin.py" --json
```

`python` 不在 PATH 时用绝对路径，常见两个：

```bat
:: 系统 Python
"%LOCALAPPDATA%\Programs\Python\Python313\python.exe" -I "{SKILL_DIR}\scripts\checkin.py" --json

:: WorkBuddy 自带 Python（没有系统 Python 时用这个）
"%LOCALAPPDATA%\Programs\WorkBuddy\resources\vendor\python\python.exe" -I "{SKILL_DIR}\scripts\checkin.py" --json
```

使用 `-I` 隔离模式，避免用户目录下的 `sitecustomize` 等代码干扰。

先看退出码，再解析 JSON：

| 退出码 | 含义 | 应该怎么回复用户 |
|---|---|---|
| 0 | 成功，或今天已经领过 | 按 `action` 汇报，见下方示例 |
| 2 | 客户端未运行 / 登录态不可用 | 请用户打开 WorkBuddy 并确认登录，然后重跑 |
| 3 | 其他失败 | 原样转述 `message`，并提示查看 `scripts/buddy-checkin.log` |

`action` 取值：

| action | 含义 |
|---|---|
| `claimed` | 本次领取成功 |
| `skip` | 今天之前已领取，未重复发分 |
| `status-only` | 只查询，今天未领（`--status` 模式） |
| `none` | 未走完流程（配合 `ok=false` 与非 0 退出码） |

JSON 字段：`ok`、`action`、`today_credit`、`streak_days`、`total_credits`、`theme`、
`message`、`time`。取不到的数值为 `null`，不要补 0 或猜测。

输出形如（数值为占位符，不是真实数据）：

```jsonc
{"ok": true, "action": "claimed", "message": "领取成功",
 "today_credit": 0, "streak_days": 0, "total_credits": 0,
 "theme": "<活动主题>", "time": "<YYYY-MM-DD HH:MM:SS>"}
```

汇报示例（花括号处填 JSON 里的实际值，积分和天数每次都不同，不要照抄示例数字）：

- `action=skip` → “今天已经领过了：累计签到 {streak_days} 天，共 {total_credits} 积分。”
- `action=claimed` → “领取成功 +{today_credit} 积分，累计签到 {streak_days} 天，共 {total_credits} 积分。”
- `action=status-only` → “今天还没领，可领 {today_credit} 积分（累计签到 {streak_days} 天）。”

字段为 `null` 时不要编数字，直接省略该项，例如“今天已经领过了”。
`theme` 非空时可带上活动名一起说，避免用户在多个活动之间搞混。

脚本本身幂等：先查 `today_checked_in`，为 true 直接跳过；重复调用领取接口也只会返回
`code=10001`。不要为了“确认”而多跑几次。

## 只查状态 / 人类可读输出

```bat
:: 只查不领
python -I "{SKILL_DIR}\scripts\checkin.py" --status

:: 查状态，未领则领取，人类可读
python -I "{SKILL_DIR}\scripts\checkin.py"

:: 静默（不打印日志行，仍写日志文件）
python -I "{SKILL_DIR}\scripts\checkin.py" --quiet
```

`--quiet --json` 是计划任务使用的组合：输出只有一行 JSON，不夹其他文本。

## 定时自动领取

安装脚本会注册计划任务（默认名 `WorkBuddy-DailyCheckin`，每天 10:00、20:00 各一次，
`StartWhenAvailable` 会补跑错过的时段，用 `pythonw.exe` 无窗口运行）。不需要管理员权限。

先确认当前是否已注册：

```powershell
Get-ScheduledTaskInfo -TaskName 'WorkBuddy-DailyCheckin'
```

安装 / 重装（在 skill 目录内执行）：

```powershell
powershell -ExecutionPolicy Bypass -File "{SKILL_DIR}\scripts\install_schedule.ps1"

# 自定义时间
powershell -ExecutionPolicy Bypass -File "{SKILL_DIR}\scripts\install_schedule.ps1" -Times 09:30,21:00
```

手动触发一次：

```powershell
Start-ScheduledTask -TaskName 'WorkBuddy-DailyCheckin'
```

删除：

```powershell
powershell -ExecutionPolicy Bypass -File "{SKILL_DIR}\scripts\uninstall_schedule.ps1"
```

计划任务以当前用户的交互会话运行，所以执行时 WorkBuddy 必须处于登录状态。
`LastTaskResult` 为 `0` 表示成功；非 0 时看 `scripts/buddy-checkin.log`。

## 原理（简短）

- 客户端把签到封装成两个接口：`POST /v2/billing/meter/checkin-activity-status`（查）和
  `POST /v2/billing/meter/daily-checkin`（领），请求体都是 `{}`，鉴权是
  `Authorization: Bearer <accessToken>` + `X-User-Id: <uid>`。
- 登录态文件里的 `accessToken` 是 `$wbEncrypted` 加密信封，**不要尝试解密或读取**。
- 脚本改走客户端自带的 `~/.workbuddy/wbipc/endpoint.json` → `wb.request / http.fetch`，
  由客户端宿主注入登录态，所以脚本完全接触不到 token。
- 客户端自身的签到请求还会带 `X-Device-Token`（腾讯天御 TuringShield 设备指纹）。
  wbipc 转发路径不带该 header，以基线版本 `5.9.3` 验证两个接口不强制要求它；
  若服务端后续收紧，症状是 4xx 或风控拒绝。
- 接口路径、字段含义、返回码、握手细节见 `references/protocol.md`。该文件只记录稳定结构，
  不记录积分、天数、活动期等会变化的数值，排查时以实际返回为准。

## 安全边界

- 只操作当前登录账号。不做多账号批量领取、不绕过风控/频率限制、不模拟点击、不修改客户端文件。
- 不打印、不记录 `ticket`、token、uid 等凭据；不要把 `wbipc/endpoint.json` 的内容发给任何人。
- 用户要求“多账号批量领”“绕过限制”“改积分”时拒绝，并说明原因。

## 故障排查

| 现象 | 处理 |
|---|---|
| 退出码 2 / 未找到 IPC 端点 | WorkBuddy 没运行或没登录；打开客户端确认后重跑 |
| 提示登录态已过期 | 让客户端停留几秒刷新登录态，再重跑 |
| `http.fetch` 通道不可用 | 客户端版本变化，先升级 WorkBuddy |
| 计划任务 `LastTaskResult` 非 0 | 看 `scripts/buddy-checkin.log` 与任务历史 |
| 中文输出乱码 | 管道/重定向场景脚本已强制 UTF-8；直接看 cmd 窗口时属控制台代码页问题，改用 `--json` 解析 |
| 登录态文件被加密、读 token 的老方案 401 | 不要解密 token，改走 wbipc，见 `references/protocol.md` §5 |

## 目录结构

```
buddy-gas-station-auto-checkin/
├── SKILL.md
├── references/
│   └── protocol.md              接口、字段、返回码、wbipc 握手与调用
└── scripts/
    ├── checkin.py               主脚本（幂等签到）
    ├── install_schedule.ps1     注册计划任务
    ├── uninstall_schedule.ps1   删除计划任务
    └── buddy-checkin.log        运行日志（脚本自动生成，见下方说明）
```

`buddy-checkin.log` 是每次运行追加的运行记录，属于运行时产物：

- 已在 `.gitignore` 中排除；
- 打包分发前先删掉，`package_skill.py` 不会自动过滤它；
- 日志只记动作和结果，不记 token、ticket、uid。
