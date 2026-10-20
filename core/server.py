"""本地网页服务：只监听 127.0.0.1。网页读 case.json 显示进度，用户操作写回 case.json。"""
from __future__ import annotations

import json
import mimetypes
import os
import posixpath
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from core import state

WEB = os.path.join(state.ROOT, "web")
VERSION = "0.1.0"
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("text/css", ".css")


class ApiError(Exception):
    def __init__(self, code, msg, extra=None):
        super().__init__(msg)
        self.code, self.msg, self.extra = code, msg, extra or {}


def ask_view(c: dict) -> dict | None:
    a = c.get("ask")
    if not a:
        return None
    a = dict(a)
    if not a.get("paused") and a.get("timeout"):
        a["remaining"] = max(0, round(max(a["created"], a.get("last_activity", 0)) + a["timeout"] - state.now(), 1))
    return a


def public_case(c: dict) -> dict:
    c = dict(c)
    c["ask"] = ask_view(c)
    c.pop("asks", None)
    c["now"] = state.now()
    c["missing"] = state.missing_required(c)
    return c


def bump_activity(c: dict) -> None:
    if c.get("ask"):
        c["ask"]["last_activity"] = state.now()


# ---------------------------------------------------------------- API
def api(method: str, parts: list, body: dict):
    if parts == ["ping"]:
        return {"ok": True, "version": VERSION, "home": state.HOME}
    if parts == ["meta"]:
        return {"fields": state.FIELDS, "sources": state.SOURCES, "stages": state.STAGES}
    if parts == ["home"]:
        info = state.server_info()
        cases = state.list_cases()
        return {"cases": cases, "focus": info.get("focus"), "focus_t": info.get("focus_t", 0),
                "ai_seen": max([0] + [state.read_json(state.case_path(x["id"]), {}).get("ai_seen", 0)
                                      for x in cases[:5]] + [info.get("ai_seen", 0)]),
                "now": state.now()}
    if parts == ["cases"] and method == "POST":
        city = (body.get("city") or "").strip()
        if not city:
            raise ApiError(400, "请输入一个城市")
        c = state.new_case(city, created_by="web")
        src = body.get("copy_from")
        if src:
            old = state.load(src)
            state.update(c["id"], lambda x: x["form"].update({
                k: {"value": v["value"], "by": "user" if v.get("by") == "user" else v.get("by", "default")}
                for k, v in old["form"].items()}))
        return {"id": c["id"]}
    if len(parts) >= 2 and parts[0] == "case":
        cid, action = parts[1], (parts[2] if len(parts) > 2 else "")
        if method == "GET" and not action:
            return public_case(state.load(cid))
        if method != "POST":
            raise ApiError(405, "method not allowed")
        if action == "activity":
            return {"ok": True, "ask": ask_view(state.update(cid, bump_activity))}
        if action == "pause":
            def fn(c):
                if c.get("ask"):
                    c["ask"]["paused"] = bool(body.get("paused", True))
                    c["ask"]["last_activity"] = state.now()
                    state._push_log(c, "已暂停自动继续" if c["ask"]["paused"] else "恢复自动继续", "user")
            return {"ok": True, "ask": ask_view(state.update(cid, fn))}
        if action == "form":
            key, value = body.get("key"), body.get("value")
            err = []

            def fn(c):
                try:
                    state.set_field(c, key, value, "user")
                except (KeyError, ValueError) as e:
                    err.append(str(e))
                bump_activity(c)
            c = state.update(cid, fn)
            if err:
                raise ApiError(400, err[0])
            return {"ok": True, "field": c["form"][key], "missing": state.missing_required(c)}
        if action == "answer":
            err = []

            def fn(c):
                a = c.get("ask")
                if (not a or a.get("answer") is not None) and c.get("stage") == "prepare":
                    # AI 还没来得及问，用户已经点了「开始规划」：记下来，AI 一问就直接通过
                    miss = state.missing_required(c)
                    if miss:
                        err.append("还差：" + "、".join(miss))
                        return
                    c["prepare_ready"] = True
                    state.save_profile(c)
                    state._push_log(c, "条件已确认，等 AI 接手后马上开始", "user")
                    return
                if not a or a.get("answer") is not None:
                    err.append("当前没有需要回答的问题")
                    return
                if a["kind"] == "prepare":
                    miss = state.missing_required(c)
                    if miss:
                        err.append("还差：" + "、".join(miss))
                        return
                    a["answer"] = state.form_values(c)
                    state.save_profile(c)
                elif a["kind"] == "pick":
                    ids = {o["id"] for o in a.get("options", [])}
                    ans = body.get("answer") or {}
                    a["answer"] = {"ids": [x for x in ans.get("ids", []) if x in ids], "note": str(ans.get("note") or "")}
                else:
                    a["answer"] = body.get("answer") if body.get("answer") is not None else {"ok": True}
                a["by"], a["answered_at"] = "user", state.now()
                state._push_log(c, f"已确认：{a.get('title', '')}", "user")
            state.update(cid, fn)
            if err:
                raise ApiError(400, err[0])
            return {"ok": True}
        if action == "cookie":
            from sources import get as get_source
            src = get_source(body.get("source") or "bilibili")
            try:
                src.save_cookie(body.get("text") or "")
            except ValueError as e:
                raise ApiError(400, str(e)) from None
            uname = src.login_status()
            if not uname:
                raise ApiError(400, "这份 cookie 登录不上，可能已过期，换一份或改用扫码")

            def fn(c):
                c["login"][body.get("source") or "bilibili"] = {"state": "ok", "uname": uname, "t": state.now()}
                state._push_log(c, f"{src.NAME}已登录：{uname}（手动导入）", "ok")
            state.update(cid, fn)
            return {"ok": True, "uname": uname}
    raise ApiError(404, "not found")


class Handler(BaseHTTPRequestHandler):
    server_version = "plan-city-trip/" + VERSION

    def log_message(self, fmt, *args):  # 安静一点，只记错误
        if args and str(args[1] if len(args) > 1 else "").startswith(("4", "5")):
            sys.stderr.write("[server] " + fmt % args + "\n")

    def _send(self, code, body: bytes, ctype="application/json; charset=utf-8", cache=False):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "max-age=300" if cache else "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, code, obj):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def _file(self, base, rel):
        rel = posixpath.normpath(urllib.parse.unquote(rel)).lstrip("/")
        if rel.startswith("..") or any(p.startswith(".") for p in rel.split("/")):
            return self._json(403, {"error": "forbidden"})
        path = os.path.join(base, *rel.split("/"))
        if not os.path.isfile(path):
            return self._json(404, {"error": "not found"})
        with open(path, "rb") as f:
            data = f.read()
        ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype.endswith(("javascript", "json")):
            ctype += "; charset=utf-8"
        self._send(200, data, ctype)

    def _route(self, method):
        u = urllib.parse.urlparse(self.path)
        path = u.path
        if path.startswith("/api/"):
            if method == "POST" and self.headers.get("Origin") not in (None, f"http://{self.headers.get('Host')}"):
                return self._json(403, {"error": "cross-origin"})  # 防止别的网站往本地服务发请求
            body = {}
            n = int(self.headers.get("Content-Length") or 0)
            if n:
                try:
                    body = json.loads(self.rfile.read(n) or b"{}")
                except json.JSONDecodeError:
                    return self._json(400, {"error": "bad json"})
            try:
                return self._json(200, api(method, [p for p in path[5:].split("/") if p], body))
            except ApiError as e:
                return self._json(e.code, {"error": e.msg, **e.extra})
            except FileNotFoundError as e:
                return self._json(404, {"error": str(e)})
            except Exception as e:  # noqa: BLE001
                return self._json(500, {"error": f"{type(e).__name__}: {e}"})
        if path.startswith("/files/"):
            cid, _, rel = path[7:].partition("/")
            try:
                return self._file(state.case_dir(cid), rel)
            except ValueError:
                return self._json(403, {"error": "forbidden"})
        return self._file(WEB, "index.html" if path in ("", "/") else path)

    def do_GET(self):
        self._route("GET")

    def do_HEAD(self):
        self._route("GET")

    def do_POST(self):
        self._route("POST")


def serve(port: int) -> None:
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    httpd.daemon_threads = True
    info = state.server_info()
    info.update({"pid": os.getpid(), "port": port})
    state.write_json(state.SERVER, info)
    print(f"plan-city-trip 网页：http://127.0.0.1:{port}/", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass