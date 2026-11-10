#!/usr/bin/env python3
"""WorkBuddy「Buddy加油站」每日签到领积分（幂等，无 UI 自动化）。

实现方式
--------
WorkBuddy 客户端在 `~/.workbuddy/wbipc/endpoint.json` 暴露一个本地命名管道，
其中 `wb.request / http.fetch` 会把请求转发到官方后端，并自动注入当前登录态
（Authorization: Bearer <accessToken>、X-User-Id）。因此脚本**不读取、不解密、
不保存任何 token**，只使用客户端自己的会话。

签到接口（与客户端 DesktopAccountRepo 一致）：
    POST /v2/billing/meter/checkin-activity-status   查询今日状态
    POST /v2/billing/meter/daily-checkin             领取今日积分

前置条件：WorkBuddy 客户端正在运行且已登录（客户端默认开机自启）。

用法
----
    python checkin.py            # 查状态；未领则领取
    python checkin.py --status   # 只查状态
    python checkin.py --json     # 机器可读输出（定时任务用）
    python checkin.py --quiet    # 不打印日志行

退出码：0 成功/今日已领；2 客户端未运行或登录态不可用；3 其他失败
"""

import argparse
import base64
import ctypes
import hashlib
import hmac
import json
import os
import struct
import sys
import time
from datetime import datetime
from ctypes import wintypes

def _init_console():
    """交互窗口沿用控制台本地编码（GBK），管道/重定向输出统一 UTF-8。

    否则 Windows 下被 bash、计划任务或父进程捕获时，中文会变成乱码。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            if stream is not None and not stream.isatty():
                stream.reconfigure(encoding="utf-8", errors="replace")
            elif stream is not None:
                stream.reconfigure(errors="replace")
        except Exception:
            pass


_init_console()

PROTOCOL_VERSION = 1
STATUS_EP = "/v2/billing/meter/checkin-activity-status"
CLAIM_EP = "/v2/billing/meter/daily-checkin"
DISCOVERY_RELPATH = ("wbipc", "endpoint.json")
HANDSHAKE_TIMEOUT_S = 5.0
RPC_TIMEOUT_S = 55.0

GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
OPEN_EXISTING = 3
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
ERROR_PIPE_BUSY = 231
ERROR_BROKEN_PIPE = 109
ERROR_NO_DATA = 232

if sys.platform != "win32":
    raise SystemExit("checkin.py 目前仅支持 Windows（wbipc 使用命名管道）。")

_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_k32.CreateFileW.restype = wintypes.HANDLE
_k32.CreateFileW.argtypes = [
    wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
    wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
]
_k32.ReadFile.argtypes = [
    wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID,
]
_k32.ReadFile.restype = wintypes.BOOL
_k32.WriteFile.argtypes = [
    wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID,
]
_k32.WriteFile.restype = wintypes.BOOL
_k32.CloseHandle.argtypes = [wintypes.HANDLE]
_k32.CloseHandle.restype = wintypes.BOOL
_k32.PeekNamedPipe.argtypes = [
    wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD,
    wintypes.LPVOID, wintypes.LPVOID, wintypes.LPVOID,
]
_k32.PeekNamedPipe.restype = wintypes.BOOL


class WbipcError(Exception):
    def __init__(self, code, message):
        super().__init__("%s: %s" % (code, message))
        self.code = code
        self.message = message


class NotRunning(WbipcError):
    def __init__(self, message="WorkBuddy 未运行或 IPC 端点不可用"):
        WbipcError.__init__(self, "E_NOT_WORKBUDDY", message)


class _Pipe:
    def __init__(self, handle):
        self._h = handle
        self._buf = b""

    def _available(self):
        total = wintypes.DWORD(0)
        left = wintypes.DWORD(0)
        if not _k32.PeekNamedPipe(self._h, None, 0, None,
                                  ctypes.byref(total), ctypes.byref(left)):
            err = ctypes.get_last_error()
            return -1 if err in (ERROR_BROKEN_PIPE, ERROR_NO_DATA) else 0
        return total.value

    def read(self, timeout):
        deadline = time.monotonic() + timeout
        while True:
            avail = self._available()
            if avail < 0:
                raise WbipcError("E_REVOKED", "WorkBuddy IPC 连接已断开")
            if avail > 0:
                n = max(avail, 4096)
                buf = ctypes.create_string_buffer(n)
                got = wintypes.DWORD(0)
                if not _k32.ReadFile(self._h, buf, n, ctypes.byref(got), None):
                    raise WbipcError("E_REVOKED", "IPC 读取失败")
                return buf.raw[:got.value]
            if time.monotonic() >= deadline:
                raise WbipcError("E_TIMEOUT", "等待 WorkBuddy 响应超时")
            time.sleep(0.01)

    def recv_frame(self, timeout):
        while b"\n" not in self._buf:
            chunk = self.read(timeout)
            if not chunk:
                raise WbipcError("E_REVOKED", "WorkBuddy IPC 连接已断开")
            self._buf += chunk
        line, self._buf = self._buf.split(b"\n", 1)
        try:
            return json.loads(line.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise WbipcError("E_PROTOCOL", "IPC 返回了非法 JSON 帧")

    def send_frame(self, obj):
        data = (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")
        sent = 0
        while sent < len(data):
            chunk = data[sent:]
            written = wintypes.DWORD(0)
            if not _k32.WriteFile(self._h, chunk, len(chunk),
                                  ctypes.byref(written), None):
                raise WbipcError("E_REVOKED", "IPC 写入失败")
            sent += written.value

    def close(self):
        if self._h:
            _k32.CloseHandle(self._h)
            self._h = None


def _b64url(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _proof(ticket, role, endpoint, client_nonce, server_nonce):
    domain = b"wbipc-s" if role == "server" else b"wbipc-c"
    parts = [domain, str(PROTOCOL_VERSION).encode(), endpoint.encode(),
             client_nonce.encode(), server_nonce.encode()]
    transcript = b"".join(struct.pack(">I", len(p)) + p for p in parts)
    mac = hmac.new(ticket.encode(), transcript, hashlib.sha256).digest()
    return _b64url(mac)


def _discovery_path():
    config_dir = os.environ.get("WORKBUDDY_CONFIG_DIR", "").strip()
    if not config_dir:
        config_dir = os.path.join(os.path.expanduser("~"), ".workbuddy")
    return os.path.join(config_dir, *DISCOVERY_RELPATH)


def _connect():
    path = _discovery_path()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            disc = json.load(fh)
        endpoint = (disc.get("endpoint") or "").strip()
        ticket = (disc.get("ticket") or "").strip()
    except (OSError, ValueError):
        raise NotRunning("未找到 WorkBuddy IPC 端点（客户端未运行）")
    if not endpoint or not ticket:
        raise NotRunning("WorkBuddy IPC 端点信息不完整")

    handle = _k32.CreateFileW(endpoint, GENERIC_READ | GENERIC_WRITE, 0, None,
                              OPEN_EXISTING, 0, None)
    if handle == INVALID_HANDLE_VALUE:
        err = ctypes.get_last_error()
        reason = "端点被占用" if err == ERROR_PIPE_BUSY else "无法连接（客户端未运行）"
        raise NotRunning("WorkBuddy IPC %s (error %d)" % (reason, err))

    pipe = _Pipe(handle)
    try:
        client_nonce = _b64url(os.urandom(16))
        pipe.send_frame({
            "type": "session_hello",
            "protocol_min": PROTOCOL_VERSION,
            "protocol_max": PROTOCOL_VERSION,
            "client_nonce": client_nonce,
            "ticket_id": hashlib.sha256(ticket.encode()).hexdigest()[:16],
            "client": {"kind": "skill", "id": "buddy-daily-checkin"},
        })
        challenge = pipe.recv_frame(HANDSHAKE_TIMEOUT_S)
        if challenge.get("type") == "session_hello_error":
            raise NotRunning("WorkBuddy IPC 握手被拒绝：%s" % challenge.get("code"))
        if challenge.get("type") != "session_challenge":
            raise NotRunning("WorkBuddy IPC 握手响应异常")
        server_nonce = challenge.get("server_nonce") or ""
        expected = _proof(ticket, "server", endpoint, client_nonce, server_nonce)
        if not hmac.compare_digest(expected, challenge.get("server_proof") or ""):
            raise NotRunning("WorkBuddy IPC 端点校验失败（端点可能被抢占）")
        pipe.send_frame({
            "type": "session_prove",
            "client_proof": _proof(ticket, "client", endpoint,
                                   client_nonce, server_nonce),
        })
        ack = pipe.recv_frame(HANDSHAKE_TIMEOUT_S)
        if ack.get("type") != "session_hello_ack":
            raise NotRunning("WorkBuddy IPC 登录态不可用：%s" % ack.get("code"))
        return pipe, ack
    except Exception:
        pipe.close()
        raise


class Client:
    def __init__(self, pipe):
        self._pipe = pipe
        self._next_id = 1
        self._channel = None

    def rpc(self, method, params):
        request_id = self._next_id
        self._next_id += 1
        self._pipe.send_frame({
            "jsonrpc": "2.0", "id": request_id, "method": method,
            "params": params, "mode": "call",
        })
        deadline = time.monotonic() + RPC_TIMEOUT_S
        while True:
            frame = self._pipe.recv_frame(max(0.1, deadline - time.monotonic()))
            if frame.get("id") != request_id:
                continue
            if "error" in frame:
                err = frame["error"] or {}
                code = str(err.get("code") or "E_INTERNAL")
                if code == "E_NOT_CONNECTED":
                    raise NotRunning("WorkBuddy 当前未登录")
                raise WbipcError(code, str(err.get("message") or "IPC 调用失败"))
            return frame.get("result")

    def http_post(self, path, body=None):
        if self._channel is None:
            got = self.rpc("broker/GetPipe", {"pipe": "wb.request"})
            methods = got.get("methods") or []
            if "http.fetch" not in methods:
                raise WbipcError("E_UNSUPPORTED", "客户端未开放 http.fetch 通道，请升级 WorkBuddy")
            self._channel = got["channel"]
        payload = json.dumps(body if body is not None else {},
                             ensure_ascii=False).encode("utf-8")
        result = self.rpc(self._channel + "/http.fetch", {
            "method": "POST",
            "path": path,
            "headers": {"accept": "application/json",
                        "content-type": "application/json"},
            "body_b64": base64.b64encode(payload).decode("ascii"),
        })
        raw = base64.b64decode(result.get("body_b64") or "")
        try:
            data = json.loads(raw.decode("utf-8")) if raw else {}
        except (ValueError, UnicodeDecodeError):
            data = {"raw": raw.decode("utf-8", "replace")}
        return int(result.get("status") or 0), data


def _pick(obj, *keys):
    if isinstance(obj, dict):
        for key in keys:
            value = obj.get(key)
            if value not in (None, "", []):
                return value
    return None


def _as_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _describe(payload):
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    return {
        "checked_in": bool(_pick(data, "today_checked_in") or False),
        "code": _pick(payload, "code"),
        "msg": str(_pick(payload, "msg", "message") or ""),
        "today_credit": _as_int(_pick(data, "today_credit", "daily_credit", "credit")),
        "streak_days": _as_int(_pick(data, "streak_days")),
        "total_credits": _as_int(_pick(data, "total_credits")),
        "is_streak_day": bool(_pick(data, "is_streak_day") or False),
        "theme": str(_pick(data, "theme_name") or ""),
        "activity": str(_pick(data, "activity_name") or ""),
        "end_time": str(_pick(data, "end_time") or ""),
    }


def _log_path():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "buddy-checkin.log")


_CONSOLE_QUIET = False


def _log(line, quiet):
    if not quiet and not _CONSOLE_QUIET:
        try:
            print(line)
        except UnicodeEncodeError:
            enc = getattr(sys.stdout, "encoding", None) or "utf-8"
            print(line.encode(enc, "replace").decode(enc, "replace"))
    try:
        with open(_log_path(), "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def main():
    parser = argparse.ArgumentParser(description="WorkBuddy 每日签到领积分（幂等）")
    parser.add_argument("--status", action="store_true", help="只查状态，不领取")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    parser.add_argument("--quiet", action="store_true", help="不打印日志行")
    parser.add_argument("--force", action="store_true",
                        help=argparse.SUPPRESS)  # 诊断用：已领也调用一次领取接口
    args = parser.parse_args()
    global _CONSOLE_QUIET
    _CONSOLE_QUIET = args.quiet or args.json

    result = {
        "ok": False, "action": "none", "message": "",
        "today_credit": None, "streak_days": None, "total_credits": None,
        "theme": "", "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }

    def finish(code):
        if args.json:
            print(json.dumps(result, ensure_ascii=False))
        return code

    try:
        pipe, ack = _connect()
        client = Client(pipe)
    except NotRunning as exc:
        result["message"] = str(exc) + "。请启动 WorkBuddy 客户端并确认已登录。"
        _log("[!] %s" % result["message"], args.quiet)
        return finish(2)
    except WbipcError as exc:
        result["message"] = "IPC 连接失败：%s" % exc
        _log("[!] %s" % result["message"], args.quiet)
        return finish(3)

    try:
        http_status, status_payload = client.http_post(STATUS_EP)
        info = _describe(status_payload)
        result.update(theme=info["theme"], streak_days=info["streak_days"],
                      total_credits=info["total_credits"])

        if http_status == 401 or info["code"] in (401, 10002):
            result["message"] = "登录态已过期，请打开 WorkBuddy 客户端刷新后重试。"
            _log("[!] %s" % result["message"], args.quiet)
            return finish(2)

        if http_status != 200 or info["code"] not in (0, "0", None):
            result["message"] = "状态查询失败：%s" % (info["msg"] or status_payload)
            _log("[!] %s" % result["message"], args.quiet)
            return finish(3)

        if info["checked_in"] and not args.force:
            result.update(ok=True, action="skip", message="今天已领取，跳过",
                          today_credit=info["today_credit"])
            _log("-> %s（连续 %s 天，累计 %s 积分）"
                 % (result["message"], info["streak_days"], info["total_credits"]),
                 args.quiet)
            return finish(0)

        if args.status and not args.force:
            result.update(ok=True, action="status-only", message="今天还未领取",
                          today_credit=info["today_credit"])
            _log("-> 今天还未领取（连续 %s 天，可领 %s 积分）"
                 % (info["streak_days"], info["today_credit"]), args.quiet)
            return finish(0)

        claim_status, claim_payload = client.http_post(CLAIM_EP)
        claim = _describe(claim_payload)

        if claim["code"] in (10001, 1001) or "已签到" in claim["msg"]:
            result.update(ok=True, action="skip", message="今天已领取（接口幂等）")
            _log("-> 今天已领取（接口幂等）", args.quiet)
            return finish(0)

        if claim_status == 401:
            result["message"] = "登录态已过期，请打开 WorkBuddy 客户端刷新后重试。"
            _log("[!] %s" % result["message"], args.quiet)
            return finish(2)

        if claim_status != 200 or claim["code"] not in (0, "0", None):
            result["message"] = "领取失败：%s" % (claim["msg"] or claim_payload)
            _log("[!] %s" % result["message"], args.quiet)
            return finish(3)

        gained = claim["today_credit"]
        if gained is None:
            gained = info["today_credit"]
        result.update(ok=True, action="claimed", message="领取成功",
                      today_credit=gained,
                      streak_days=claim["streak_days"] or info["streak_days"])
        _log("[+] 领取成功：+%s 积分（连续 %s 天）"
             % (gained if gained is not None else "?", result["streak_days"]),
             args.quiet)

        # 二次核对，顺便刷新累计积分
        try:
            _, verify_payload = client.http_post(STATUS_EP)
            verify = _describe(verify_payload)
            if verify["checked_in"]:
                result["total_credits"] = verify["total_credits"]
                _log("  二次核对通过，累计 %s 积分" % verify["total_credits"],
                     args.quiet)
        except Exception:
            pass
        return finish(0)
    except NotRunning as exc:
        result["message"] = str(exc) + "。请在客户端内确认登录态。"
        _log("[!] %s" % result["message"], args.quiet)
        return finish(2)
    except Exception as exc:
        result["message"] = "签到失败：%s" % exc
        _log("[!] %s" % result["message"], args.quiet)
        return finish(3)
    finally:
        try:
            pipe.close()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())