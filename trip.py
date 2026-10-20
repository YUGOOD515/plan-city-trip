#!/usr/bin/env python3
"""travel-agent 命令行：AI 通过它完成整套流程，网页同步显示。

所有命令都向标准输出打印一行 JSON，方便 AI 解析；用 `python3 trip.py <命令> -h` 看参数。
只依赖 Python 3.9+ 标准库。完整流程见 SKILL.md。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
import webbrowser

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core import state, tools  # noqa: E402
import sources  # noqa: E402

PY = sys.executable
SELF = os.path.abspath(__file__)
DEFAULT_MAX_WAIT = 90  # 多数 Harness 单条命令 2 分钟超时，留出余量


def out(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False), flush=True)


def die(msg, **extra):
    out({"status": "error", "error": msg, **extra})
    sys.exit(1)


def touch(cid, fn=None):
    def wrap(c):
        state.touch_ai(c)
        if fn:
            fn(c)
    return state.update(cid, wrap)


def log(cid, text, kind="ai"):
    touch(cid, lambda c: state._push_log(c, text, kind))


def set_focus(cid):
    info = state.server_info()
    info.update({"focus": cid, "focus_t": state.now(), "ai_seen": state.now()})
    state.write_json(state.SERVER, info)


# ---------------------------------------------------------------- 网页服务
def ping(port, timeout=1.0):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/ping", timeout=timeout) as r:
            return json.loads(r.read()).get("home") == state.HOME
    except Exception:  # noqa: BLE001
        return False


def spawn(args, logfile):
    os.makedirs(os.path.dirname(logfile), exist_ok=True)
    kw = {"stdout": open(logfile, "a", encoding="utf-8"), "stderr": subprocess.STDOUT, "stdin": subprocess.DEVNULL,
          "env": dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1"), "cwd": state.ROOT}
    if os.name == "nt":
        kw["creationflags"] = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    else:
        kw["start_new_session"] = True
    return subprocess.Popen([PY, SELF, *args], **kw)


def cmd_serve(a):
    from core import server
    if a.foreground:
        server.serve(a.port)
        return
    info = state.server_info()
    if a.stop:
        pid = info.get("pid")
        if not (pid and info.get("port") and ping(info["port"])):
            out({"status": "ok", "hint": "网页服务本来就没在运行"})
            return
        import signal
        os.kill(pid, signal.SIGTERM)
        out({"status": "ok", "stopped": pid})
        return
    if info.get("port") and ping(info["port"]):
        port = info["port"]
    else:
        port = None
        for p in range(a.port, a.port + 10):
            import socket
            with socket.socket() as s:
                if s.connect_ex(("127.0.0.1", p)) != 0:
                    port = p
                    break
        if port is None:
            die("8765–8774 端口都被占用了，用 --port 换一个")
        spawn(["serve", "--foreground", "--port", str(port)], os.path.join(state.HOME, "server.log"))
        for _ in range(50):
            if ping(port, 0.3):
                break
            time.sleep(0.1)
        else:
            die("网页服务没能启动，看 data/server.log")
    url = f"http://127.0.0.1:{port}/"
    opened = False
    if a.open:
        try:
            opened = webbrowser.open(url)
        except Exception:  # noqa: BLE001
            opened = False
    out({"status": "ok", "url": url, "opened": opened,
         "hint": "把这个地址告诉用户，在浏览器里打开" if not opened else "已在浏览器打开"})


# ---------------------------------------------------------------- 案例
def parse_sets(pairs):
    vals = {}
    for p in pairs or []:
        if "=" not in p:
            die(f"--set 需要 key=value 形式：{p}")
        k, v = p.split("=", 1)
        vals[k.strip()] = v.strip()
    return vals


def apply_sets(cid, vals, by):
    applied, skipped = [], []

    def fn(c):
        for k, v in vals.items():
            try:
                (applied if state.set_field(c, k, v, by) else skipped).append(k)
            except (KeyError, ValueError) as e:
                die(str(e))
        if applied:
            labels = {f["key"]: f["label"] for f in state.FIELDS}
            state._push_log(c, "已预填：" + "、".join(labels[k] for k in applied), "ai")
    touch(cid, fn)
    return applied, skipped


def cmd_new(a):
    c = state.new_case(a.city, created_by="ai")
    applied, skipped = apply_sets(c["id"], parse_sets(a.set), "ai")
    set_focus(c["id"])
    out({"status": "ok", "case": c["id"], "url": state.server_url(c["id"]), "prefilled": applied, "dir": state.case_dir(c["id"])})


def cmd_next(a):
    """等用户在网页首页输入城市。"""
    t0 = time.time()
    info = state.server_info()
    info["ai_seen"] = state.now()
    state.write_json(state.SERVER, info)
    while True:
        for x in state.list_cases():
            if not x.get("claimed"):
                def claim(c):
                    c["claimed"] = True
                    state._push_log(c, "AI 已接手，开始准备", "ai")
                touch(x["id"], claim)
                set_focus(x["id"])
                out({"status": "ok", "case": x["id"], "city": x["city"], "url": state.server_url(x["id"])})
                return
        if time.time() - t0 > a.max_wait:
            out({"status": "pending", "hint": "用户还没在网页输入城市；再次运行 `trip.py next` 继续等"})
            return
        time.sleep(1)
        if int(time.time() - t0) % 10 == 0:
            info = state.server_info()
            info["ai_seen"] = state.now()
            state.write_json(state.SERVER, info)


def cmd_open(a):
    state.load(a.case)
    set_focus(a.case)
    url = state.server_url(a.case)
    opened = False
    if a.browser:
        try:
            opened = webbrowser.open(url)
        except Exception:  # noqa: BLE001
            pass
    out({"status": "ok", "url": url, "opened": opened})


def cmd_set(a):
    applied, skipped = apply_sets(a.case, parse_sets(a.pairs), a.by)
    out({"status": "ok", "applied": applied, "kept_user_value": skipped})


def cmd_list(a):
    out({"status": "ok", "cases": [{k: x[k] for k in ("id", "city", "status", "stage", "date_start", "date_end")}
                                   for x in state.list_cases()]})


def cmd_status(a):
    c = state.load(a.case)
    out({"status": "ok", "case": c["id"], "city": c["city"], "case_status": c["status"], "stage": c["stage"],
         "stages": {k: v["status"] for k, v in c["stages"].items()}, "form": state.form_values(c),
         "form_by": {k: v.get("by") for k, v in c["form"].items()}, "missing": state.missing_required(c),
         "ask": ({k: c["ask"].get(k) for k in ("id", "kind", "title", "answer", "by", "paused")} if c.get("ask") else None),
         "jobs": c.get("jobs", {}), "login": {k: v.get("state") for k, v in c.get("login", {}).items()},
         "candidates": {"total": len(c.get("candidates", [])),
                        "selected": sum(1 for x in c.get("candidates", []) if x.get("selected"))},
         "dir": state.case_dir(c["id"]), "url": state.server_url(c["id"])})


def cmd_log(a):
    log(a.case, a.text, a.kind)
    out({"status": "ok"})


def cmd_stage(a):
    touch(a.case, lambda c: state.set_stage(c, a.key, a.status, a.note))
    out({"status": "ok", "stage": a.key, "stage_status": a.status})


def cmd_progress(a):
    touch(a.case, lambda c: c.update(progress={"done": a.done, "total": a.total, "text": a.text or ""}))
    out({"status": "ok"})


# ---------------------------------------------------------------- 等待用户（60 秒无操作自动继续）
def cmd_ask(a):
    cid, kind = a.case, a.kind
    options = None
    if kind == "videos":
        c = state.load(cid)
        options = [{"id": x["bvid"], "title": x.get("title", x["bvid"]), "author": x.get("author", ""),
                    "views": x.get("views"), "duration": x.get("duration", ""), "pubdate": x.get("pubdate", ""),
                    "url": x.get("url", ""), "cover": x.get("cover", ""), "reason": x.get("reason", ""),
                    "selected": bool(x.get("selected"))} for x in c.get("candidates", [])]
        if not options:
            die("还没有候选视频，先运行 search")
    elif a.options_file:
        with open(a.options_file, encoding="utf-8") as f:
            options = json.load(f)
        any_sel = any("selected" in o for o in options)
        for i, o in enumerate(options):
            o.setdefault("id", str(i + 1))
            o.setdefault("selected", not any_sel)  # 都没写 selected 时默认全选
    elif kind == "pick":
        die("pick 需要 --options-file")
    titles = {"prepare": "确认出行条件和数据源", "videos": "确认要读的攻略视频", "pick": "请选择", "confirm": "请确认"}
    t0 = time.time()

    def create(c):
        cur = c.get("ask")
        if cur and cur.get("tag") == kind and not a.new:
            return  # 同一个问题还在等（或已答待取），继续，不重置倒计时
        if cur:
            c.setdefault("asks", []).append(cur)
        c["ask"] = {"id": f"q{len(c.get('asks', [])) + 1}", "tag": kind,
                    "kind": "pick" if kind == "videos" else kind, "title": a.title or titles[kind],
                    "desc": a.desc or "", "options": options, "multi": True, "timeout": a.timeout,
                    "created": state.now(), "last_activity": state.now(), "paused": False,
                    "blocked": None, "answer": None, "by": None}
        if kind == "prepare":
            state.set_stage(c, "prepare", "active")
            if c.pop("prepare_ready", False) and not state.missing_required(c):
                c["ask"].update(answer=state.form_values(c), by="user", answered_at=state.now())
        c["status"] = "waiting"
        state._push_log(c, f"等待确认：{c['ask']['title']}（{a.timeout} 秒无操作自动继续）", "ai")
    touch(cid, create)

    last_touch = 0.0
    while True:
        result = {}

        def check(c):
            q = c.get("ask")
            if not q:
                result["gone"] = True
                return
            if q.get("answer") is None and not q.get("paused"):
                remain = max(q["created"], q.get("last_activity", 0)) + q["timeout"] - state.now()
                if remain <= 0:
                    miss = state.missing_required(c) if q["kind"] == "prepare" else []
                    if miss:
                        if q.get("blocked") != miss:
                            q["blocked"] = miss
                            state._push_log(c, "还差 " + "、".join(miss) + "，填好后才能继续", "warn")
                    else:
                        if q["kind"] == "prepare":
                            q["answer"] = state.form_values(c)
                            state.save_profile(c)
                        elif q["kind"] == "pick":
                            q["answer"] = {"ids": [o["id"] for o in q["options"] if o.get("selected")], "note": ""}
                        else:
                            q["answer"] = {"ok": True}
                        q["by"], q["answered_at"] = "timeout", state.now()
                        state._push_log(c, f"{q['timeout']} 秒没有操作，已按默认继续：{q['title']}", "ai")
            if q.get("answer") is not None:
                result.update(answer=q["answer"], by=q["by"], kind=q["tag"])
                if q["tag"] == "videos":
                    chosen = set(q["answer"]["ids"])
                    for x in c.get("candidates", []):
                        x["selected"] = x["bvid"] in chosen
                if q["tag"] == "prepare":
                    result["form_by"] = {k: v.get("by") for k, v in c["form"].items()}
                c.setdefault("asks", []).append(q)
                c["ask"] = None
                c["status"] = "running"
            else:
                result["pending"] = {"paused": q.get("paused"), "blocked": q.get("blocked"),
                                     "remaining": round(max(q["created"], q.get("last_activity", 0))
                                                        + q["timeout"] - state.now())}
            if time.time() - last_touch > 5:
                state.touch_ai(c)
        state.update(cid, check)
        if time.time() - last_touch > 5:
            last_touch = time.time()
        if result.get("gone"):
            die("这个问题已被取消")
        if "answer" in result:
            out({"status": "answered", **result})
            return
        if time.time() - t0 > a.max_wait:
            p = result["pending"]
            reason = "用户暂停了自动继续" if p["paused"] else (
                "还差必填项：" + "、".join(p["blocked"]) if p["blocked"] else f"等待用户确认，约 {max(p['remaining'], 0)} 秒后按默认继续")
            out({"status": "pending", "reason": reason, **p,
                 "hint": f"再次运行同一条命令继续等待（不会重置倒计时）：trip.py ask {cid} {kind}"})
            return
        time.sleep(0.5)


def cmd_answer(a):
    """用户在聊天里回答时，由 AI 代为提交。"""
    payload = json.loads(a.json) if a.json else None
    err = []

    def fn(c):
        q = c.get("ask")
        if (not q or q.get("answer") is not None) and c.get("stage") == "prepare":
            # 还没发出准备提问，用户就在聊天里确认了：先记下，ask prepare 时直接通过
            for k, v in (payload or {}).items():
                state.set_field(c, k, v, "chat", force=True)
            miss = state.missing_required(c)
            if miss:
                err.append("还差：" + "、".join(miss))
                return
            c["prepare_ready"] = True
            state._push_log(c, "按聊天里的回复确认出行条件", "user")
            return
        if not q or q.get("answer") is not None:
            err.append("当前没有等待中的问题")
            return
        if q["kind"] == "prepare":
            if payload:
                for k, v in payload.items():
                    state.set_field(c, k, v, "chat", force=True)
            miss = state.missing_required(c)
            if miss:
                err.append("还差：" + "、".join(miss))
                return
            q["answer"] = state.form_values(c)
            state.save_profile(c)
        elif q["kind"] == "pick":
            ids = {o["id"] for o in q["options"]}
            p = payload or {}
            q["answer"] = {"ids": [x for x in p.get("ids", []) if x in ids], "note": p.get("note", "")}
        else:
            q["answer"] = payload or {"ok": True}
        q["by"], q["answered_at"] = "chat", state.now()
        state._push_log(c, f"按聊天里的回复确认：{q['title']}", "user")
    touch(a.case, fn)
    if err:
        die(err[0])
    out({"status": "ok", "hint": "再运行一次 ask 命令取回答案"})


# ---------------------------------------------------------------- 登录
def cmd_login(a):
    cid, sid = a.case, a.source
    src = sources.get(sid)
    qr_file = os.path.join(state.SECRETS, f"{sid}_qr.json")

    def set_login(st, **kw):
        def fn(c):
            c["login"][sid] = {"state": st, "t": state.now(), **kw}
        touch(cid, fn)

    c = state.load(cid)
    if c["stages"]["login"]["status"] != "active":
        touch(cid, lambda c: state.set_stage(c, "login", "active"))
        log(cid, f"检查{src.NAME}登录状态", "tool")
    uname = src.login_status()
    if uname:
        set_login("ok", uname=uname)
        log(cid, f"{src.NAME}已登录：{uname}", "ok")
        out({"status": "ok", "source": sid, "uname": uname})
        return
    t0, regen = time.time(), 0
    qr = state.read_json(qr_file)
    last_state = None
    while True:
        if not qr or time.time() - qr.get("t", 0) > 170:
            if regen >= 6:
                set_login("error", message="二维码多次过期")
                die("二维码连续过期 6 次，用户可能不在电脑前；稍后再运行 login")
            first = qr is None
            qr = {**src.qr_generate(), "t": time.time()}
            state.write_json(qr_file, qr)
            regen += 1
            set_login("need_scan", qr=qr["url"])
            log(cid, f"请用{src.NAME} App 扫码登录（网页上有二维码）" if first else "二维码已过期，已自动换新的", "warn")
        c = state.load(cid)
        if (c["login"].get(sid) or {}).get("state") == "ok":  # 用户在网页上手动导入了 cookie
            out({"status": "ok", "source": sid, "uname": c["login"][sid].get("uname")})
            return
        try:
            r = src.qr_poll(qr["key"])
        except Exception as e:  # noqa: BLE001
            r = {"state": "error", "message": str(e)}
        if r["state"] == "ok":
            os.remove(qr_file)
            set_login("ok", uname=r.get("uname", ""))
            log(cid, f"{src.NAME}登录成功：{r.get('uname', '')}", "ok")
            out({"status": "ok", "source": sid, "uname": r.get("uname", "")})
            return
        if r["state"] == "expired":
            qr = None
            continue
        if r["state"] != last_state and r["state"] in ("scanned", "need_scan"):
            set_login(r["state"], qr=qr["url"])
            if r["state"] == "scanned":
                log(cid, "已扫码，请在手机上点「确认登录」", "ai")
            last_state = r["state"]
        if time.time() - t0 > a.max_wait:
            out({"status": "pending", "login_state": last_state or "need_scan",
                 "hint": f"用户还没扫码；再次运行 trip.py login {cid} 继续等待（二维码会自动续期）"})
            return
        time.sleep(2)


# ---------------------------------------------------------------- 收集
def cmd_search(a):
    cid = a.case
    src = sources.get(a.source)
    c = state.load(cid)
    if c["stages"]["collect"]["status"] != "active":
        touch(cid, lambda c: state.set_stage(c, "collect", "active"))
    n = a.n or state.form_values(c).get("per_keyword") or 8
    log(cid, f"在{src.NAME}搜索 {len(a.kw)} 个关键词：" + "、".join(a.kw), "tool")

    def on_line(line):
        if line.startswith("搜索"):
            log(cid, line, "tool")
    try:
        found = src.search(a.kw, n, a.require, on_line)
    except Exception as e:  # noqa: BLE001
        log(cid, f"搜索失败：{e}", "err")
        die(str(e))
    added = []

    def fn(c):
        have = {x["bvid"] for x in c["candidates"]}
        for x in found:
            if x["bvid"] not in have:
                x["selected"] = True
                c["candidates"].append(x)
                added.append(x)
        state._push_log(c, f"候选视频 {len(c['candidates'])} 个（本次新增 {len(added)}）", "ok")
    touch(cid, fn)
    out({"status": "ok", "added": len(added), "total": len(state.load(cid)["candidates"]),
         "candidates": [{k: x.get(k) for k in ("bvid", "title", "author", "views", "duration", "pubdate")}
                        for x in added]})


def cmd_pick(a):
    off, on = set(a.off.split(",")) if a.off else set(), set(a.on.split(",")) if a.on else set()

    def fn(c):
        for x in c["candidates"]:
            if x["bvid"] in off:
                x["selected"] = False
                if a.reason:
                    x["reason"] = a.reason
            if x["bvid"] in on:
                x["selected"] = True
        state._push_log(c, f"筛选视频：保留 {sum(1 for x in c['candidates'] if x['selected'])} 个", "ai")
    c = touch(a.case, fn)
    out({"status": "ok", "selected": [x["bvid"] for x in c["candidates"] if x["selected"]]})


def run_job(a, name, fn):
    """--bg 时在后台跑同一条命令，立刻返回；AI 用 `trip.py wait` 查结果。"""
    cid = a.case
    if a.bg:
        args = [x for x in sys.argv[1:] if x != "--bg"]
        p = spawn(args + ["--as-job"], os.path.join(state.case_dir(cid), "jobs", f"{name}.log"))
        touch(cid, lambda c: c["jobs"].update({name: {"state": "running", "pid": p.pid, "started": state.now()}}))
        out({"status": "started", "job": name, "pid": p.pid,
             "hint": f"用 trip.py wait {cid} {name} 等待结果"})
        return
    if a.as_job:
        touch(cid, lambda c: c["jobs"].update({name: {"state": "running", "pid": os.getpid(), "started": state.now()}}))
    try:
        summary = fn()
    except Exception as e:  # noqa: BLE001
        touch(cid, lambda c: (c["jobs"].update({name: {"state": "error", "error": str(e), "ended": state.now()}}),
                              state._push_log(c, f"{name} 失败：{e}", "err")))
        die(str(e), job=name)
    touch(cid, lambda c: c["jobs"].update({name: {"state": "done", "summary": summary, "ended": state.now()}}))
    out({"status": "ok", "job": name, **summary})


def pid_alive(pid):
    if not pid:
        return False
    if os.name == "nt":
        out_ = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"], capture_output=True, text=True).stdout
        return str(pid) in out_
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    try:  # 僵尸进程也算结束
        with open(f"/proc/{pid}/stat") as f:
            return f.read().split()[2] != "Z"
    except OSError:
        return True


def cmd_wait(a):
    t0 = time.time()
    while True:
        c = touch(a.case) if int(time.time() - t0) % 5 == 0 else state.load(a.case)
        j = c.get("jobs", {}).get(a.job)
        if not j:
            die(f"没有名为 {a.job} 的任务")
        if j["state"] != "running":
            out({"status": j["state"], "job": a.job, **(j.get("summary") or {}),
                 **({"error": j["error"]} if j.get("error") else {})})
            return
        if not pid_alive(j.get("pid")) and time.time() - j.get("started", 0) > 3:
            touch(a.case, lambda c: c["jobs"][a.job].update(state="error", error="进程意外退出"))
            die("任务进程意外退出，看 jobs/ 下的日志", log=os.path.join(state.case_dir(a.case), "jobs", f"{a.job}.log"))
        if time.time() - t0 > a.max_wait:
            p = c.get("progress") or {}
            out({"status": "running", "job": a.job, "progress": p,
                 "hint": f"还在跑；再次运行 trip.py wait {a.case} {a.job}"})
            return
        time.sleep(1)


def cmd_fetch(a):
    cid = a.case
    src = sources.get(a.source)
    outdir = os.path.join(state.case_dir(cid), "sources", a.source)

    def work():
        c = state.load(cid)
        ids = [x["bvid"] for x in c["candidates"] if x.get("selected")]
        if not ids:
            raise RuntimeError("没有选中的视频")
        done = {"n": 0, "ok": 0}
        touch(cid, lambda c: (c.update(progress={"done": 0, "total": len(ids), "text": "下载字幕"}),
                              state._push_log(c, f"开始下载 {len(ids)} 个视频的字幕", "tool")))

        def on_line(line):
            p = src.parse_progress(line)
            if not p:
                if line.startswith("登录状态"):
                    log(cid, line, "tool")
                return
            done["n"] += 1
            done["ok"] += p["status"] == "成功"

            def fn(c):
                c["progress"] = {"done": done["n"], "total": len(ids), "text": f"下载字幕（{done['ok']} 个成功）"}
                for x in c["candidates"]:
                    if x["bvid"] == p["bvid"]:
                        x["sub_status"] = p["status"]
                kind = "ok" if p["status"] == "成功" else "warn"
                state._push_log(c, f"[{p['status']}] {p['title'] or p['bvid']}", kind)
            touch(cid, fn)
        manifest = src.fetch(ids, outdir, on_line)
        ok = [m for m in manifest if m.get("status") == "成功"]
        chars = sum(m.get("chars", 0) for m in ok)
        res = {"ok": len(ok), "total": len(manifest), "chars": chars, "dir": outdir}
        if not ok:
            log(cid, f"{len(manifest)} 个视频都没拿到字幕", "warn")
            res["hint"] = ("一个字幕都没拿到。多半是没登录（AI 字幕要登录才能拿）：先 trip.py login，"
                           "或换一批视频；实在不行就基于标题和公开信息规划，并在方案里注明")
        else:
            log(cid, f"字幕下载完成：{len(ok)}/{len(manifest)} 成功，共 {chars} 字", "ok")
        return res
    run_job(a, "fetch", work)


def cmd_digest(a):
    cid = a.case
    c = state.load(cid)
    docs = []
    for sid in state.form_values(c).get("sources") or ["bilibili"]:
        docs += sources.get(sid).documents(os.path.join(state.case_dir(cid), "sources", sid))
    if not docs:
        die("没有可用的字幕，先运行 fetch")
    info = tools.digest(docs, os.path.join(state.case_dir(cid), "sources", "digest.md"))
    videos = [{"bvid": d["id"], "title": d["title"], "author": d["author"], "views": d.get("views"),
               "url": d["url"]} for d in docs]
    state.write_json(os.path.join(state.case_dir(cid), "sources", "videos.json"), videos)
    touch(cid, lambda c: (state.set_stage(c, "digest", "active"),
                          state._push_log(c, f"整理好 {info['docs']} 篇攻略原文（{info['chars']} 字），开始提炼要点", "ai")))
    out({"status": "ok", **info, "hint": "读 digest.md，提炼热门地点、避坑、美食，写进 plans.json 的 mentions / pits"})


# ---------------------------------------------------------------- 规划工具
def cmd_geocode(a):
    c = state.load(a.case)
    city = a.city or c["city"]
    log(a.case, f"查 {len(a.names)} 个地点的坐标", "tool")
    res = tools.geocode(a.names, city)
    miss = [r["name"] for r in res if not r["found"]]
    if miss:
        log(a.case, "没找到坐标：" + "、".join(miss) + "（换个写法或用出入口名再查）", "warn")
    out({"status": "ok", "results": res, "missing": miss})


def cmd_weather(a):
    c = state.load(a.case)
    v = state.form_values(c)
    log(a.case, f"查 {c['city']} {v['date_start']}–{v['date_end']} 天气", "tool")
    try:
        w = tools.weather(c["city"], v["date_start"], v["date_end"])
    except Exception as e:  # noqa: BLE001
        log(a.case, f"天气查询失败：{e}", "warn")
        die(str(e))
    touch(a.case, lambda c: c.update(weather=w))
    log(a.case, "天气：" + ("；".join(f"{d['date'][5:]} {d['text']}" for d in w["days"]) or w.get("note", "")), "ok")
    out({"status": "ok", **w})


def cmd_build(a):
    cid = a.case
    cdir = state.case_dir(cid)
    plans = os.path.join(cdir, "plans.json")
    if not os.path.exists(plans):
        die(f"还没有 {plans}，格式见 references/plans-schema.md")

    def work():
        c = state.load(cid)
        touch(cid, lambda c: (state.set_stage(c, "build", "active"),
                              c.update(progress={"done": 0, "total": 1, "text": "计算路线、生成地图"}),
                              state._push_log(c, "开始出图：计算步行 / 打车 / 地铁路线", "tool")))
        data = state.read_json(plans)
        if data is None:
            raise RuntimeError("plans.json 不是合法 JSON")
        # 自动补上 AI 没写的通用信息：视频列表、天气、人数、天数
        v = state.form_values(c)
        vids = state.read_json(os.path.join(cdir, "sources", "videos.json"), [])
        data.setdefault("videos", vids)
        if c.get("weather", {}).get("days"):
            data.setdefault("weather", c["weather"]["days"])
            data.setdefault("sources", []).append(c["weather"]["source"])
        if v.get("people"):
            data.setdefault("pax", [str(v["people"])])
        os.makedirs(os.path.join(cdir, "out"), exist_ok=True)
        merged = os.path.join(cdir, "out", "plans.build.json")
        state.write_json(merged, data)
        cmd = [PY, os.path.join(state.ROOT, "maptool", "build_map.py"), merged,
               "-o", os.path.join(cdir, "out", "map.html"), "--cache", os.path.join(state.CACHE, "maptool"),
               "--title", a.title or data.get("title") or f"{c['city']}怎么玩",
               "--subtitle", a.subtitle or data.get("subtitle") or f"{v['date_start']} – {v['date_end']} · {v['origin']} 出发"]
        if not a.no_inline:
            cmd.append("--inline-lib")
        if a.no_overpass:
            cmd.append("--no-overpass")
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                             env=dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1"))
        tail = []
        for line in p.stdout:
            line = line.rstrip()
            tail = (tail + [line])[-30:]
            if line.startswith("→") or "Overpass" in line or "失败" in line:
                log(cid, line.lstrip("→ ").strip(), "tool")
        p.wait()
        report = os.path.join(cdir, "out", "map.report.md")
        rep = open(report, encoding="utf-8").read() if os.path.exists(report) else ""
        errors = len(re.findall(r"^- \*\*error\*\*", rep, re.M))
        warns = len(re.findall(r"^- \*\*warn\*\*", rep, re.M))
        if p.returncode != 0 or not os.path.exists(os.path.join(cdir, "out", "map.html")):
            raise RuntimeError("build_map.py 失败：" + " / ".join(tail[-4:]))
        pax = (data.get("pax") or ["2"])[0]
        totals = [{"code": pl.get("code"), "name": pl.get("name"),
                   "total": sum(int((pl.get("cost") or {}).get(pax, {}).get(k) or 0)
                                for k in ("往返交通", "住宿", "当地交通", "门票项目", "餐饮"))}
                  for pl in data.get("plans", [])]
        result = {"map": "out/map.html", "report": "out/map.report.md", "errors": errors, "warns": warns,
                  "pax": pax, "plans": totals}
        touch(cid, lambda c: (c.update(result=result, progress=None),
                              state._push_log(c, f"地图已生成：{errors} 个错误，{warns} 个提醒", "ok" if not errors else "warn")))
        return {**result, "report_path": report}
    run_job(a, "build", work)


def cmd_done(a):
    def fn(c):
        for k in state.STAGE_KEYS:
            if c["stages"][k]["status"] in ("active", "pending"):
                c["stages"][k]["status"] = "done" if c["stages"][k]["status"] == "active" else "skipped"
        c["status"], c["ask"], c["progress"] = "done", None, None
        if a.summary:
            c.setdefault("result", {}) or c.update(result={})
            c["result"]["summary"] = a.summary
        state._push_log(c, "规划完成", "ok")
    touch(a.case, fn)
    set_focus(a.case)
    out({"status": "ok", "url": state.server_url(a.case)})


def cmd_fail(a):
    def fn(c):
        c["status"] = "failed"
        c["stages"][c["stage"]]["status"] = "error"
        c["stages"][c["stage"]]["note"] = a.reason
        state._push_log(c, f"中止：{a.reason}", "err")
    touch(a.case, fn)
    out({"status": "ok"})


# ---------------------------------------------------------------- 入口
def main():
    ap = argparse.ArgumentParser(prog="trip.py", description="旅游 Agent 命令行（AI 用）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add(name, fn, help_, case=True):
        p = sub.add_parser(name, help=help_)
        if case:
            p.add_argument("case", help="案例 id")
        p.set_defaults(fn=fn)
        return p

    p = add("serve", cmd_serve, "启动网页服务（已在运行则直接返回地址）", case=False)
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--open", action="store_true", help="尝试自动打开浏览器")
    p.add_argument("--foreground", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--stop", action="store_true", help="关闭网页服务")
    p = add("new", cmd_new, "新建案例（用户在聊天里说了城市时用）", case=False)
    p.add_argument("city")
    p.add_argument("--set", action="append", metavar="KEY=VALUE", help="预填已知条件，可重复")
    p = add("next", cmd_next, "等用户在网页首页输入城市", case=False)
    p.add_argument("--max-wait", type=int, default=DEFAULT_MAX_WAIT)
    add("list", cmd_list, "列出历史案例", case=False)
    p = add("open", cmd_open, "让网页切到这个案例")
    p.add_argument("--browser", action="store_true")
    p = add("set", cmd_set, "预填出行条件 key=value（不会覆盖用户改过的值）")
    p.add_argument("pairs", nargs="+")
    p.add_argument("--by", default="ai", choices=["ai", "chat"])
    add("status", cmd_status, "查看案例状态")
    p = add("log", cmd_log, "在网页的动态区写一行")
    p.add_argument("text")
    p.add_argument("--kind", default="ai", choices=["ai", "tool", "ok", "warn", "err"])
    p = add("stage", cmd_stage, "切换阶段")
    p.add_argument("key", choices=state.STAGE_KEYS)
    p.add_argument("--status", default="active", choices=["active", "done", "skipped", "error"])
    p.add_argument("--note")
    p = add("progress", cmd_progress, "更新进度条")
    p.add_argument("done", type=int)
    p.add_argument("total", type=int)
    p.add_argument("text", nargs="?")
    p = add("ask", cmd_ask, "在网页上等用户确认；无操作 timeout 秒后按默认继续")
    p.add_argument("kind", choices=["prepare", "videos", "pick", "confirm"])
    p.add_argument("--title")
    p.add_argument("--desc")
    p.add_argument("--options-file", help="pick 用：JSON 数组 [{id,title,desc,selected}]")
    p.add_argument("--timeout", type=int, default=60)
    p.add_argument("--max-wait", type=int, default=DEFAULT_MAX_WAIT)
    p.add_argument("--new", action="store_true", help="强制新开一个问题")
    p = add("answer", cmd_answer, "用户在聊天里回答时代为提交")
    p.add_argument("--json", help="prepare: {字段: 值}；pick: {ids: [...], note}；confirm: 任意")
    p = add("login", cmd_login, "检查登录，必要时在网页上显示二维码并等待扫码")
    p.add_argument("--source", default="bilibili")
    p.add_argument("--max-wait", type=int, default=DEFAULT_MAX_WAIT)
    p = add("search", cmd_search, "按关键词搜索候选内容")
    p.add_argument("--kw", action="append", required=True)
    p.add_argument("-n", type=int)
    p.add_argument("--require", help="标题须含其中一个词，逗号分隔")
    p.add_argument("--source", default="bilibili")
    p = add("pick", cmd_pick, "调整候选视频的勾选")
    p.add_argument("--off")
    p.add_argument("--on")
    p.add_argument("--reason")
    for name, fn, help_ in [("fetch", cmd_fetch, "下载选中视频的字幕"), ("build", cmd_build, "生成地图")]:
        p = add(name, fn, help_)
        p.add_argument("--bg", action="store_true", help="后台运行，立刻返回")
        p.add_argument("--as-job", action="store_true", help=argparse.SUPPRESS)
        if name == "fetch":
            p.add_argument("--source", default="bilibili")
        else:
            p.add_argument("--title")
            p.add_argument("--subtitle")
            p.add_argument("--no-inline", action="store_true", help="不内联 MapLibre（文件更小，但依赖 CDN）")
            p.add_argument("--no-overpass", action="store_true")
    p = add("wait", cmd_wait, "等待后台任务")
    p.add_argument("job", choices=["fetch", "build"])
    p.add_argument("--max-wait", type=int, default=DEFAULT_MAX_WAIT)
    add("digest", cmd_digest, "把字幕整理成 digest.md")
    p = add("geocode", cmd_geocode, "查地点坐标（WGS84）")
    p.add_argument("names", nargs="+")
    p.add_argument("--city")
    add("weather", cmd_weather, "查出行日期的天气")
    p = add("done", cmd_done, "标记完成")
    p.add_argument("--summary")
    p = add("fail", cmd_fail, "标记失败")
    p.add_argument("reason")
    a = ap.parse_args()
    try:
        a.fn(a)
    except FileNotFoundError as e:
        die(str(e))


if __name__ == "__main__":
    main()