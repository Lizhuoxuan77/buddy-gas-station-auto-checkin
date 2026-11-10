# Buddy加油站自动签到

WorkBuddy skill：自动领取「Buddy加油站 / 今日礼包」每日积分。

通过客户端本地的 `wbipc` 通道转发官方签到接口，复用当前登录态。脚本不读取、不解密、
不保存任何 token，也不模拟点击。

## 功能

- **自动签到**：先查今日状态，未领取才调用领取接口，今天领过直接跳过。
- **幂等**：重复运行不会重复发分，已领取时接口返回 `10001`，脚本视为成功。
- **结果可读**：输出今日积分、累计签到天数、总积分，支持 `--json` 供自动化消费。
- **可定时**：附带计划任务安装脚本，每天固定时间自动领取，错过开机可补跑。

## 使用

```
python -I scripts/checkin.py --json      # 查状态，未领则领取
python -I scripts/checkin.py --status    # 只查不领
```

退出码：`0` 成功或今日已领 · `2` 客户端未运行/未登录 · `3` 其他失败。

```
{"ok": true, "action": "skip", "today_credit": 100,
 "streak_days": 24, "total_credits": 2400, "theme": "Buddy加油站"}
```

（数值随账号与活动期变化，仅为格式示例。）

## 前置条件

- Windows，WorkBuddy 客户端正在运行且已登录。
- Python 3.7+，只用标准库，无需安装依赖。

macOS / Linux 不支持：脚本依赖 Windows 命名管道。

## 定时领取

```powershell
powershell -ExecutionPolicy Bypass -File scripts/install_schedule.ps1            # 默认每天 10:00、20:00
powershell -ExecutionPolicy Bypass -File scripts/install_schedule.ps1 -Times 09:30,21:00
powershell -ExecutionPolicy Bypass -File scripts/uninstall_schedule.ps1          # 删除
```

## 目录

```
SKILL.md                 技能说明（给 agent 读）
references/protocol.md   接口、字段、返回码、wbipc 握手
scripts/checkin.py       主脚本
scripts/*.ps1            计划任务的安装与删除
```

## 边界

只操作当前登录账号，不做多账号批量领取、不绕过风控、不修改客户端文件。

## License

MIT
