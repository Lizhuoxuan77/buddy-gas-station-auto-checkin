# 协议与实现说明

本文用于排查问题和做客户端升级适配。**不要把里面的任何凭据、ticket 或 token 外发。**

## 0. 文档约定（重要）

本文只记录**跨版本稳定的结构**：接口路径、请求形状、字段名、返回码语义、IPC 帧格式。
以下三类内容**一律不写入**，需要用的时候现抓现看：

| 不写入 | 原因 | 正确做法 |
|---|---|---|
| 具体积分、连续天数、活动期时间 | 随账号和活动期变化，写下来第二天就错 | 运行时读接口返回值 |
| 域名、channel 名、pipe 名 | 由客户端宿主注入或协商产生 | 从 `endpoint.json` / `broker/GetPipe` 读 |
| 大段原始响应快照 | 会过期，且容易被误当断言 | 只记录字段含义和类型 |

字段表里的“依赖”列标明脚本是否真的读取该字段：标 **是** 的改名会导致解析失败，
标 否 的仅供排查参考，缺失不影响运行。

## 1. 适用环境

- 平台：Windows（命名管道 + ctypes；macOS 走 AF_UNIX socket，脚本未适配）
- 客户端：WorkBuddy Desktop，核对基线为 `5.9.3`
- 判断是否需要重读本文：脚本报 `http.fetch` 通道不可用、接口 404、握手被拒

客户端升级后如果上述任一项失败，按下文顺序重新核对一遍即可，不要凭记忆改代码。

## 2. 签到接口

| 用途 | 方法与路径 | 请求体 |
|---|---|---|
| 查今日状态 | `POST /v2/billing/meter/checkin-activity-status` | `{}` |
| 领取今日积分 | `POST /v2/billing/meter/daily-checkin` | `{}` |

客户端内部还保留一套更老的调用（`/billing/meter/...` 不带 `/v2`，见 `main/account.js`
的 `BackendProvider`）。以带 `/v2` 的 activity 版本为准。

### 鉴权（由客户端宿主注入，脚本不接触）

- `Authorization: Bearer <accessToken>`
- `X-User-Id: <account.uid>`
- 企业账号额外带 `X-Enterprise-Id` / `X-Tenant-Id`
- 域名不硬编码，取登录态中的 `auth.domain`
- 客户端桌面版自身请求还会带 `X-Device-Token`（腾讯天御 TuringShield 设备指纹，
  见 `native/turing-sdk`、`qimei.dll`、`qm-helper.exe`）。**wbipc 转发路径不带该 header，
  当前两个接口也不要求它**；若后续服务端收紧，症状会是 4xx 或风控拒绝，
  届时需改走客户端自身的签到入口。

## 3. 响应结构

外层为 `{code, msg, data}`，`data` 内是 snake_case 字段。

| 字段 | 类型 | 含义 | 脚本依赖 |
|---|---|---|---|
| `today_checked_in` | bool | 今天是否已领取，幂等判断的唯一依据 | 是 |
| `today_credit` | int | 本次可领/已领积分 | 是 |
| `streak_days` | int | 活动内累计签到天数 | 是 |
| `total_credits` | int | 累计积分 | 是 |
| `theme_name` | str | 活动主题名，用于向用户确认领的是哪个活动 | 是 |
| `active` | bool | 活动是否在有效期内 | 否 |
| `daily_credit` | int | 每日基础积分 | 否 |
| `is_streak_day` | bool | 今天是否落在连击奖励日 | 否 |
| `next_streak_day` | int | 下一个奖励日 | 否 |
| `streak_bonus_days` / `streak_bonus_credit` | int | 连击奖励条件与额度 | 否 |
| `week_checkin_days` / `week_progress` | int / bool[] | 本周签到天数与逐日进度 | 否 |
| `start_time` / `end_time` | str | 活动起止时间，仅用于展示 | 否 |
| `season` | int | 活动赛季号 | 否 |
| `activity_name` | str | 活动名 | 否 |
| `claim_button_text` / `action_button` | str | UI 按钮文案 | 否 |
| `checkin_dates` | str[] | 历史签到日期，可能断档 | 否 |

注意：

- `streak_days` 是**活动内累计**天数，不是严格连续天数；`checkin_dates` 可能存在断档。
- `start_time` / `end_time` / `active` 只作展示与参考，不要据此判断能否领取；
  能否领取以 `today_checked_in` 和接口返回码为准。
- 连击奖励额度以接口返回的 `streak_bonus_*` 为准，客户端 UI 有兜底默认值（7 天 / 1000 积分），
  服务端可能返回 0，此时表示当期没有额外奖励。
- 字段可能随活动期增删。新增字段不影响脚本；改名会命中上表“依赖=是”的行，需同步改代码。

示例响应的**结构**（`<...>` 为占位符，不是真实数据）：

```jsonc
{
  "code": 0,
  "msg": "",
  "data": {
    "active": true,
    "today_checked_in": "<bool>",
    "streak_days": "<int>",
    "today_credit": "<int>",
    "total_credits": "<int>",
    "theme_name": "<活动主题>",
    "start_time": "<YYYY-MM-DD HH:MM:SS>",
    "end_time": "<YYYY-MM-DD HH:MM:SS>",
    "week_progress": ["<bool>", "<bool>", "<bool>", "<bool>", "<bool>", "<bool>", "<bool>"]
  }
}
```

实际值一律运行时读取，不要写入文档或代码常量。

## 4. 返回码

| code | 含义 | 脚本处理 |
|---|---|---|
| `0` | 成功 | 读 `today_credit` / `streak_days` |
| `10001` | 今天已签到，请明天再来 | 视为已领，退出码 0 |
| `1001` | 客户端映射：AlreadyClaimed | 视为已领，退出码 0 |
| `1002` | NotEligible，不满足领取条件 | 退出码 3 |
| `1003` | EventEnded，活动已结束 | 退出码 3 |
| `401` / `10002` | 登录态失效 | 退出码 2，提示重新登录 |

HTTP 非 200 且 code 不在上表时，按退出码 3 处理并原样转述 `msg`。
领取接口在已领时不会重复发分，`10001` 是正常幂等响应，不是错误。

## 5. 登录态存储（为什么不该读 token）

登录态文件：

```
%LOCALAPPDATA%\CodeBuddyExtension\Data\Public\auth\workbuddy-desktop.info
```

`auth.accessToken` / `refreshToken` 以 `$wbEncrypted` 信封形式存储：

```json
{"$wbEncrypted":1,"envelope":"{\"suite\":1,\"keyId\":\"...\",\"nonce\":\"...\",\"authTag\":\"...\",\"ciphertext\":\"...\"}"}
```

- 算法：AES-256-GCM，`keyId = sha256(key)[:16]`
- 字段 AAD 绑定用途与字段路径
- 主密钥由 Electron 原生绑定 `electron.workbuddyStorage.loggerGet()` 提供
- `~/.workbuddy/keyblob` 保存 keyblob（`static-v1` slot）
- 构建期 key payload 形如 `{version:1, atRestSecretKey:<base64 32B>, atRestDeveloperPublicKey:...}`

**不要实现 token 解密。** 这条路会随客户端版本变化，且属于绕过客户端静态加密保护，
不适合作为 skill 的依赖。读取该文件只会拿到加密信封，把信封当明文用会得到空 token 和 401。

## 6. wbipc：复用登录态的正确路径

客户端向本机暴露一个带票据校验的 IPC，由宿主代为注入登录态。脚本只需表达
“POST 这个 path、body 是这个”，完全不需要接触 token。

### 6.1 发现

```
<配置目录>/wbipc/endpoint.json
{"endpoint":"\\\\.\\pipe\\wbipc-<id>","ticket":"<random>"}
```

配置目录默认 `~/.workbuddy`，可被环境变量 `WORKBUDDY_CONFIG_DIR` 覆盖（沙箱会注入）。
`endpoint` 与 `ticket` 都可能随客户端重启变化，每次运行都要重新读取，不要缓存。

### 6.2 握手（协议版本 1）

1. 打开命名管道（Windows `CreateFileW`；macOS 为 AF_UNIX socket）
2. 发 `session_hello`：`protocol_min` / `protocol_max` / `client_nonce` /
   `ticket_id = sha256(ticket)[:16]` / `client:{kind,id}`
3. 收 `session_challenge`：含 `server_nonce`、`server_proof`
4. 校验 `server_proof`，确认对端确实是客户端而不是抢占端点的进程
5. 发 `session_prove`：client 侧同算法
6. 收 `session_hello_ack`

证明值算法：

```
domain   = "wbipc-s"（server 侧） / "wbipc-c"（client 侧）
parts    = [domain, "1", endpoint, client_nonce, server_nonce]
transcript = concat( uint32_be(len(p)) + p for p in parts )
proof    = base64url_nopad( HMAC-SHA256(key=ticket, msg=transcript) )
```

失败语义（分类处理，不要一律当成“客户端没开”）：

| 错误 | 含义 | 脚本归类 | 处理 |
|---|---|---|---|
| 端点文件不存在 | 客户端没跑 | `NotRunning` → 2 | 提示用户启动客户端 |
| `server_proof` 校验失败 | 对端证明不了自己 | `NotRunning` → 2 | 硬失败，不要降级到其他方案 |
| 握手被拒（`session_hello_error`） | 票据失效或需要授权 | `NotRunning` → 2 | 提示重开客户端；需授权时引导用户在客户端内确认 |
| `E_NOT_CONNECTED` | 客户端未登录 | `NotRunning` → 2 | 提示登录 |
| `E_TIMEOUT` | 宿主未在超时内响应 | `WbipcError` → 3 | 重试一次，仍失败按退出码 3 |
| `E_UNSUPPORTED` | 客户端未开放 `http.fetch` | `WbipcError` → 3 | 提示升级 WorkBuddy |

### 6.3 调用

```jsonc
{"jsonrpc":"2.0","id":3,"method":"<channel>/http.fetch","params":{
  "method":"POST",
  "path":"/v2/billing/meter/checkin-activity-status",
  "headers":{"accept":"application/json","content-type":"application/json"},
  "body_b64":"e30="
},"mode":"call"}
```

`channel` 不写死：先 `broker/GetPipe {"pipe":"wb.request"}`，从返回的 `channel` 取。
同时检查返回的 `methods` 含 `http.fetch`，不含说明客户端版本不支持，应提示升级。

`http.fetch` 的宿主侧约束：

- 方法仅 `GET/HEAD/POST/PUT/PATCH/DELETE`
- `path` 必须以 `/` 开头，不能带 query / fragment，不能含 `..`
- 自定义 header 只允许 `content-type` / `accept`
- 响应内联上限约 640KB，上游超时约 50s（脚本取 55s 作为 RPC 超时）
- 宿主用 `resolveBackendAuthFromHost()` 注入 `baseUrl`、`Authorization`、`X-User-Id`
  及企业账号的 `X-Enterprise-Id` / `X-Tenant-Id`

响应体为 `{status, body_b64}`，`body_b64` 需 base64 解码后再按 JSON 解析。

## 7. 验证方法

只读验证（不会发放积分）：

```bat
python -I "{SKILL_DIR}\scripts\checkin.py" --status --json
```

期望：`ok=true`，`action` 为 `status-only` 或 `skip`。
`theme` 为空不算故障，只说明接口这一期没返回 `theme_name`；
若 `ok=false`，按第 4 节的返回码排查。

写接口验证（已领时返回 `10001`，不会多发分）：

```bat
python -I "{SKILL_DIR}\scripts\checkin.py" --force --json
```

`--force` 是隐藏参数，仅用于诊断，不要写进常规流程。

## 8. 已知边界

- 仅 Windows。必须由当前登录用户运行，且 WorkBuddy 处于已登录状态。
- 客户端升级若改动 pipe 名、方法名或接口路径，脚本会报 `http.fetch` 不可用或 404；
  按本文第 6 节重新核对，不要猜测参数。
- 服务端目前不校验 `X-Device-Token`。若以后收紧，wbipc 转发路径无法补该 header，
  需改走客户端自身签到入口，或等客户端提供带设备指纹的技能通道。
- 不支持多账号、不支持绕过频率限制、不改动客户端文件。
