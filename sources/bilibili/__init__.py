"""哔哩哔哩数据源：扫码登录、搜索候选视频、下载字幕。底层复用 bili_sub.py。"""
from __future__ import annotations

import glob
import json
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request

from core import state

NAME = "哔哩哔哩"
HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "bili_sub.py")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
COOKIE_FILE = os.path.join(state.SECRETS, "bili_cookie.txt")


def _get(url, cookie=""):
    h = {"User-Agent": UA, "Referer": "https://www.bilibili.com/"}
    if cookie:
        h["Cookie"] = cookie
    with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=15) as r:
        return json.loads(r.read()), r.headers.get_all("Set-Cookie") or []


def read_cookie() -> str:
    if os.environ.get("BILI_COOKIE"):
        return os.environ["BILI_COOKIE"].strip()
    try:
        with open(COOKIE_FILE, encoding="utf-8") as f:
            return f.read().strip()
    except FileNotFoundError:
        return ""


def save_cookie(text: str) -> None:
    text = text.strip().replace("\n", "; ")
    if "SESSDATA=" not in text:
        raise ValueError("cookie 里没有 SESSDATA，可能复制得不完整")
    os.makedirs(state.SECRETS, exist_ok=True)
    with open(COOKIE_FILE, "w", encoding="utf-8") as f:
        f.write(text)
    try:
        os.chmod(COOKIE_FILE, 0o600)
    except OSError:
        pass


def login_status() -> str | None:
    cookie = read_cookie()
    if "SESSDATA=" not in cookie:
        return None
    try:
        d, _ = _get("https://api.bilibili.com/x/web-interface/nav", cookie)
        return d["data"].get("uname") if d.get("data", {}).get("isLogin") else None
    except Exception:  # noqa: BLE001
        return None


def qr_generate() -> dict:
    d, _ = _get("https://passport.bilibili.com/x/passport-login/web/qrcode/generate")
    if d.get("code") != 0:
        raise RuntimeError(f"生成二维码失败：{d.get('message')}")
    return {"url": d["data"]["url"], "key": d["data"]["qrcode_key"]}


def qr_poll(key: str) -> dict:
    d, set_cookies = _get("https://passport.bilibili.com/x/passport-login/web/qrcode/poll?qrcode_key="
                          + urllib.parse.quote(key))
    code = (d.get("data") or {}).get("code")
    if code == 86101:
        return {"state": "need_scan"}
    if code == 86090:
        return {"state": "scanned"}
    if code == 86038:
        return {"state": "expired"}
    if code != 0:
        return {"state": "error", "message": (d.get("data") or {}).get("message") or d.get("message")}
    jar = {}
    for sc in set_cookies:  # Set-Cookie: SESSDATA=...; Path=/; ...
        k, _, v = sc.split(";", 1)[0].partition("=")
        if k.strip():
            jar[k.strip()] = v.strip()
    if "SESSDATA" not in jar:  # 兜底：从回调 url 的参数里取
        q = urllib.parse.parse_qs(urllib.parse.urlparse(d["data"].get("url", "")).query)
        jar.update({k: v[0] for k, v in q.items() if k in ("SESSDATA", "bili_jct", "DedeUserID", "DedeUserID__ckMd5")})
    try:  # 补设备标识，减少 412 风控
        spi, _ = _get("https://api.bilibili.com/x/frontend/finger/spi")
        jar.setdefault("buvid3", spi["data"]["b_3"])
        jar.setdefault("buvid4", spi["data"]["b_4"])
    except Exception:  # noqa: BLE001
        pass
    save_cookie("; ".join(f"{k}={v}" for k, v in jar.items()))
    return {"state": "ok", "uname": login_status() or ""}


def _run(args, on_line=None):
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
    p = subprocess.Popen([sys.executable, SCRIPT, *args, "--cookie-file", COOKIE_FILE],
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", env=env)
    err_lines = []
    for line in p.stderr:
        line = line.rstrip()
        err_lines.append(line)
        if on_line:
            on_line(line)
    out = p.stdout.read()
    p.wait()
    if p.returncode != 0:
        raise RuntimeError("\n".join(err_lines[-5:]) or f"bili_sub.py 退出码 {p.returncode}")
    return json.loads(out.strip().splitlines()[-1]) if out.strip() else []


def search(keywords: list, n: int = 8, require: str | None = None, on_line=None) -> list:
    args = ["--list-only", "-n", str(n)]
    for k in keywords:
        args += ["--search", k]
    if require:
        args += ["--require", require]
    return _run(args, on_line)


def fetch(bvids: list, outdir: str, on_line=None) -> list:
    os.makedirs(outdir, exist_ok=True)
    return _run([*bvids, "-o", outdir], on_line)


def parse_progress(line: str):
    """bili_sub.py 每个视频打印一行 `[成功] BVxxx 标题 条数`。"""
    m = re.match(r"\[(.+?)\] (BV\w{10}) ?(.*)", line)
    return {"status": m.group(1), "bvid": m.group(2), "title": m.group(3).strip()} if m else None


def documents(outdir: str) -> list:
    docs = []
    for path in sorted(glob.glob(os.path.join(outdir, "BV*.json"))):
        with open(path, encoding="utf-8") as f:
            j = json.load(f)
        docs.append({
            "source": "bilibili", "id": j["bvid"], "title": j["title"], "author": j["author"], "url": j["url"],
            "views": j.get("views"), "pubdate": j.get("pubdate"), "duration": j.get("duration"),
            "text": "".join(x["content"] + ("。" if not re.search(r"[。！？!?，,]$", x["content"]) else "")
                            for x in j.get("body", [])),
        })
    return docs