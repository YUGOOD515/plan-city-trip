"""案例状态：所有进程（AI 调用的 CLI、网页服务）都通过这里读写 case.json。

case.json 是唯一的状态来源：网页只读它来显示，用户在网页上的操作也写回它。
写入用文件锁 + 原子替换，CLI 和服务同时写也不会互相覆盖。
"""
from __future__ import annotations

import contextlib
import datetime as dt
import json
import os
import random
import string
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOME = os.path.abspath(os.environ.get("TRIP_HOME") or os.path.join(ROOT, "data"))
CASES = os.path.join(HOME, "cases")
CACHE = os.path.join(HOME, "cache")
SECRETS = os.path.join(HOME, ".secrets")
PROFILE = os.path.join(HOME, "profile.json")
SERVER = os.path.join(HOME, "server.json")

STAGES = [
    ("prepare", "准备"),
    ("login", "登录"),
    ("collect", "收集"),
    ("digest", "提炼"),
    ("plan", "规划"),
    ("build", "出图"),
]
STAGE_KEYS = [k for k, _ in STAGES]
MAX_LOG = 600

# ---------------------------------------------------------------- 出行条件
SOURCES = [
    {"id": "bilibili", "name": "哔哩哔哩", "desc": "攻略视频字幕", "available": True},
    {"id": "xiaohongshu", "name": "小红书", "desc": "图文笔记", "available": False},
    {"id": "douyin", "name": "抖音", "desc": "短视频", "available": False},
    {"id": "mafengwo", "name": "马蜂窝", "desc": "游记", "available": False},
]
INTERESTS = ["美食", "自然风光", "城市漫步", "历史人文", "博物馆展馆", "拍照出片", "夜景", "海边", "购物"]
FIELDS = [
    {"key": "origin", "label": "出发地", "type": "text", "required": True,
     "placeholder": "如：武汉 · 南湖校区", "profile": True},
    {"key": "date_start", "label": "出发日期", "type": "date", "required": True},
    {"key": "date_end", "label": "返回日期", "type": "date", "required": True},
    {"key": "people", "label": "人数", "type": "number", "required": True, "min": 1, "max": 12, "profile": True},
    {"key": "budget", "label": "人均预算", "type": "number", "placeholder": "不限", "unit": "元", "min": 0},
    {"key": "pace", "label": "节奏", "type": "select", "profile": True,
     "options": ["三种都看看", "轻松", "适中", "较累"]},
    {"key": "transport", "label": "大交通", "type": "select", "profile": True,
     "options": ["都比较", "高铁优先", "飞机优先", "自驾"]},
    {"key": "interests", "label": "偏好", "type": "tags", "options": INTERESTS},
    {"key": "note", "label": "其他要求", "type": "textarea",
     "placeholder": "如：不想爬山；周六晚上要看灯光秀；必须 18:00 前回到学校"},
    {"key": "sources", "label": "数据源", "type": "sources", "required": True, "profile": True},
    {"key": "per_keyword", "label": "每个关键词取几个视频", "type": "number", "min": 3, "max": 20, "profile": True},
]
FIELD_KEYS = [f["key"] for f in FIELDS]
BY_RANK = {"default": 0, "profile": 1, "ai": 2, "chat": 3, "user": 4}


def now() -> float:
    return time.time()


def default_form() -> dict:
    today = dt.date.today()
    fri = today + dt.timedelta(days=(4 - today.weekday()) % 7 or 7)  # 下一个周五
    base = {
        "origin": "", "date_start": fri.isoformat(), "date_end": (fri + dt.timedelta(days=2)).isoformat(),
        "people": 2, "budget": None, "pace": "三种都看看", "transport": "都比较", "interests": [],
        "note": "", "sources": ["bilibili"], "per_keyword": 8,
    }
    form = {k: {"value": v, "by": "default"} for k, v in base.items()}
    for k, v in (read_json(PROFILE, {}) or {}).items():
        if k in form and v not in (None, "", []):
            form[k] = {"value": v, "by": "profile"}
    return form


def coerce(key: str, value):
    f = next((x for x in FIELDS if x["key"] == key), None)
    if f is None:
        raise KeyError(f"未知字段 {key}，可用：{', '.join(FIELD_KEYS)}")
    t = f["type"]
    if value in (None, ""):
        return [] if t in ("tags", "sources") else (None if t == "number" else "")
    if t == "number":
        return int(float(value))
    if t in ("tags", "sources"):
        if isinstance(value, str):
            value = [x.strip() for x in value.replace("，", ",").split(",") if x.strip()]
        if t == "sources":
            ok = {s["id"] for s in SOURCES if s["available"]}
            value = [x for x in value if x in ok]
        return list(dict.fromkeys(value))
    if t == "date":
        dt.date.fromisoformat(str(value))
    return str(value)


def set_field(case: dict, key: str, value, by: str, force: bool = False) -> bool:
    """按来源优先级写字段：用户改过的值不会被 AI 预填覆盖（除非 force）。"""
    cur = case["form"].get(key) or {"value": None, "by": "default"}
    if not force and BY_RANK.get(by, 0) < BY_RANK.get(cur.get("by"), 0) and by != "user":
        return False
    case["form"][key] = {"value": coerce(key, value), "by": by}
    return True


def form_values(case: dict) -> dict:
    return {k: v.get("value") for k, v in case.get("form", {}).items()}


def missing_required(case: dict) -> list:
    vals = form_values(case)
    return [f["label"] for f in FIELDS if f.get("required") and vals.get(f["key"]) in (None, "", [])]


def save_profile(case: dict) -> None:
    prof = read_json(PROFILE, {}) or {}
    for f in FIELDS:
        if f.get("profile"):
            v = case["form"].get(f["key"], {}).get("value")
            if v not in (None, "", []):
                prof[f["key"]] = v
    write_json(PROFILE, prof)


# ---------------------------------------------------------------- 文件读写
def read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def write_json(path, obj) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".tmp-", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    for i in range(20):  # Windows 上目标文件被读时 replace 可能短暂失败
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            time.sleep(0.05 * (i + 1))
    os.replace(tmp, path)


@contextlib.contextmanager
def file_lock(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fh = open(path, "a+")
    try:
        try:
            import fcntl
            fcntl.flock(fh, fcntl.LOCK_EX)
        except ImportError:  # Windows
            import msvcrt
            fh.seek(0)
            while True:
                try:
                    msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)
                    break
                except OSError:
                    time.sleep(0.05)
        yield
    finally:
        try:
            try:
                import fcntl
                fcntl.flock(fh, fcntl.LOCK_UN)
            except ImportError:
                import msvcrt
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        finally:
            fh.close()


# ---------------------------------------------------------------- 案例
def case_dir(cid: str) -> str:
    if not cid or "/" in cid or "\\" in cid or cid.startswith("."):
        raise ValueError(f"非法案例 id：{cid!r}")
    return os.path.join(CASES, cid)


def case_path(cid: str) -> str:
    return os.path.join(case_dir(cid), "case.json")


def load(cid: str) -> dict:
    c = read_json(case_path(cid))
    if c is None:
        raise FileNotFoundError(f"找不到案例 {cid}")
    return c


def update(cid: str, fn=None) -> dict:
    """加锁读-改-写。fn(case) 原地修改；返回修改后的 case。"""
    with file_lock(os.path.join(case_dir(cid), ".lock")):
        c = load(cid)
        if fn:
            fn(c)
        c["updated"] = now()
        write_json(case_path(cid), c)
        return c


def new_case(city: str, created_by: str = "ai") -> dict:
    city = city.strip()
    if not city:
        raise ValueError("城市不能为空")
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    cid = f"{stamp}-{''.join(random.choices(string.ascii_lowercase + string.digits, k=4))}"
    t = now()
    c = {
        "id": cid, "city": city, "created": t, "updated": t, "created_by": created_by,
        "claimed": created_by != "web", "status": "running", "stage": "prepare",
        "stages": {k: {"status": "active" if k == "prepare" else "pending", "note": ""} for k in STAGE_KEYS},
        "form": default_form(), "ask": None, "asks": [], "login": {}, "progress": None,
        "jobs": {}, "candidates": [], "result": None, "log": [], "seq": 0, "ai_seen": t if created_by != "web" else 0,
    }
    os.makedirs(case_dir(cid), exist_ok=True)
    write_json(case_path(cid), c)
    add_log(cid, f"新建案例：{city}", "user" if created_by == "web" else "ai")
    return load(cid)


def list_cases() -> list:
    out = []
    if not os.path.isdir(CASES):
        return out
    for name in os.listdir(CASES):
        c = read_json(os.path.join(CASES, name, "case.json"))
        if not c:
            continue
        v = form_values(c)
        out.append({
            "id": c["id"], "city": c["city"], "created": c["created"], "updated": c["updated"],
            "status": c["status"], "stage": c["stage"], "date_start": v.get("date_start"),
            "date_end": v.get("date_end"), "people": v.get("people"), "result": c.get("result"),
            "claimed": c.get("claimed", True),
        })
    out.sort(key=lambda x: x["created"], reverse=True)
    return out


def _push_log(c: dict, text: str, kind: str = "ai") -> None:
    c["seq"] = c.get("seq", 0) + 1
    c["log"].append({"seq": c["seq"], "t": now(), "kind": kind, "text": text})
    if len(c["log"]) > MAX_LOG:
        c["log"] = c["log"][-MAX_LOG:]


def add_log(cid: str, text: str, kind: str = "ai") -> dict:
    return update(cid, lambda c: _push_log(c, text, kind))


def touch_ai(c: dict) -> None:
    c["ai_seen"] = now()


def set_stage(c: dict, key: str, status: str = "active", note: str | None = None) -> None:
    if key not in STAGE_KEYS:
        raise KeyError(f"未知阶段 {key}，可用：{', '.join(STAGE_KEYS)}")
    if status == "active":
        # 之前的阶段若还在进行中，自动标记完成
        for k in STAGE_KEYS[:STAGE_KEYS.index(key)]:
            if c["stages"][k]["status"] in ("active", "pending"):
                c["stages"][k]["status"] = "done" if c["stages"][k]["status"] == "active" else "skipped"
        c["stage"] = key
        c["progress"] = None
    c["stages"][key]["status"] = status
    if note is not None:
        c["stages"][key]["note"] = note


def server_info() -> dict:
    return read_json(SERVER, {}) or {}


def server_url(cid: str | None = None) -> str:
    info = server_info()
    base = f"http://127.0.0.1:{info.get('port', 8765)}/"
    return base + (f"#/case/{cid}" if cid else "")