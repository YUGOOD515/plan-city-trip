#!/usr/bin/env python3
"""MapLibre 行程地图生成工具。

用法：
    python3 build_map.py sh/plans.json sz/plans.json -o out.html

读取一个或多个符合 references/plans-schema.md 的 plans.json，
为每段交通计算路线（OSRM / Overpass，带文件缓存），做数据检查，
输出一个内嵌数据的单文件 HTML（MapLibre GL JS + OpenFreeMap 矢量瓦片）。
"""
from __future__ import annotations

import argparse
import hashlib
import heapq
import json
import math
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
UA = "plan-city-trip-maptool/1.0 (personal trip planner)"
MAPLIBRE_VER = "5.24.0"

MODES = {"walk", "taxi", "car", "bus", "metro", "tram", "train", "flight", "boat", "cable"}
ROAD_ROUTED = {"walk": "foot", "taxi": "driving", "car": "driving", "bus": "driving"}
RAIL_ROUTED = {"metro", "tram"}
SCHEMATIC = {"train", "flight", "boat", "cable"}
COST_KEYS = ["往返交通", "住宿", "当地交通", "门票项目", "餐饮"]
OSRM = {
    "foot": "https://routing.openstreetmap.de/routed-foot/route/v1/foot/",
    "driving": "https://routing.openstreetmap.de/routed-car/route/v1/driving/",
}
OVERPASS = ["https://overpass-api.de/api/interpreter", "https://overpass.private.coffee/api/interpreter"]


# ---------------------------------------------------------------- geometry
def hav_km(a, b):
    lng1, lat1, lng2, lat2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lng2 - lng1) / 2) ** 2
    return 6371.0088 * 2 * math.asin(math.sqrt(h))


def simplify(coords, tol=0.00001):
    """Douglas–Peucker（经纬度平面近似，tol≈1 m），保留 5 位小数。"""
    if len(coords) < 3:
        return [[round(c[0], 5), round(c[1], 5)] for c in coords]
    keep = [False] * len(coords)
    keep[0] = keep[-1] = True
    stack = [(0, len(coords) - 1)]
    while stack:
        s, e = stack.pop()
        ax, ay = coords[s]
        bx, by = coords[e]
        dx, dy = bx - ax, by - ay
        nn = dx * dx + dy * dy
        best, bi = -1.0, -1
        for i in range(s + 1, e):
            px, py = coords[i]
            if nn == 0:
                d = math.hypot(px - ax, py - ay)
            else:
                t = max(0, min(1, ((px - ax) * dx + (py - ay) * dy) / nn))
                d = math.hypot(px - (ax + t * dx), py - (ay + t * dy))
            if d > best:
                best, bi = d, i
        if best > tol:
            keep[bi] = True
            stack += [(s, bi), (bi, e)]
    return [[round(c[0], 5), round(c[1], 5)] for c, k in zip(coords, keep) if k]


def arc(a, b, bend=0.18, n=32):
    """示意弧线（flight / train），让示意段和真实路线一眼可分。"""
    mx, my = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
    dx, dy = b[0] - a[0], b[1] - a[1]
    cx, cy = mx - dy * bend, my + dx * bend
    pts = []
    for i in range(n + 1):
        t = i / n
        x = (1 - t) ** 2 * a[0] + 2 * (1 - t) * t * cx + t * t * b[0]
        y = (1 - t) ** 2 * a[1] + 2 * (1 - t) * t * cy + t * t * b[1]
        pts.append([round(x, 5), round(y, 5)])
    return pts


# ---------------------------------------------------------------- cache + http
class Cache:
    def __init__(self, path):
        self.path = path
        self.data = {}
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    self.data = json.load(f)
            except Exception:  # noqa: BLE001
                self.data = {}
        self.dirty = False

    def get(self, k):
        return self.data.get(k)

    def put(self, k, v):
        self.data[k] = v
        self.dirty = True
        self.n = getattr(self, "n", 0) + 1
        if self.n % 5 == 0:  # 增量落盘：中途被杀也不丢已算好的路线
            self.save()

    def save(self):
        if not self.dirty:
            return
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        # 合并磁盘上的最新内容，防止多个进程同时跑时互相覆盖
        if os.path.exists(self.path):
            try:
                with open(self.path, encoding="utf-8") as f:
                    disk = json.load(f)
                disk.update(self.data)
                self.data = disk
            except Exception:  # noqa: BLE001
                pass
        tmp = f"{self.path}.{os.getpid()}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, self.path)
        self.dirty = False


def http_get_json(url, timeout=25, data=None):
    req = urllib.request.Request(url, data=data, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def normalize_line(line):
    s = str(line or "").strip()
    m = re.search(r"([A-Za-z]*\d+[A-Za-z]*)", s)
    if m:
        return m.group(1).upper()
    return re.sub(r"(地铁|号线|線|线)$", "", s) or s


def _decode_poly6(enc):
    pts, idx, lat, lng = [], 0, 0, 0
    while idx < len(enc):
        for which in (0, 1):
            shift = res = 0
            while True:
                c = ord(enc[idx]) - 63; idx += 1
                res |= (c & 0x1F) << shift; shift += 5
                if c < 0x20:
                    break
            d = ~(res >> 1) if res & 1 else res >> 1
            if which == 0: lat += d
            else: lng += d
        pts.append([round(lng / 1e6, 6), round(lat / 1e6, 6)])
    return pts


class Router:
    def __init__(self, cache_dir, offline=False, use_overpass=True, log=print):
        self.cache = Cache(os.path.join(cache_dir, "routes.json"))
        self.ovp_dir = os.path.join(cache_dir, "overpass")
        self.offline = offline
        self.use_overpass = use_overpass and not offline
        self.ovp_dead = False
        self.ovp_fail = 0
        self.last_req = 0.0
        self.log = log
        self.stats = {"cache": 0, "osrm": 0, "overpass": 0, "fallback": 0}

    def _throttle(self, gap=0.35):
        dt = time.time() - self.last_req
        if dt < gap:
            time.sleep(gap - dt)
        self.last_req = time.time()

    # ---- OSRM（步行 / 驾车）
    def road(self, profile, a, b):
        key = f"osrm:{profile}:{a[0]:.6f},{a[1]:.6f};{b[0]:.6f},{b[1]:.6f}"
        hit = self.cache.get(key)
        if hit:
            self.stats["cache"] += 1
            return hit
        if self.offline:
            return None
        url = OSRM[profile] + f"{a[0]:.6f},{a[1]:.6f};{b[0]:.6f},{b[1]:.6f}?overview=full&geometries=geojson"
        err = None
        for attempt in range(1 if getattr(self, 'osrm_dead', False) else 2):
            try:
                self._throttle()
                j = http_get_json(url)
                if j.get("code") != "Ok" or not j.get("routes"):
                    raise RuntimeError(j.get("code"))
                r = j["routes"][0]
                snap = max((w.get("distance", 0) for w in j.get("waypoints", [])), default=0)
                res = {
                    "geom": simplify([list(a)] + r["geometry"]["coordinates"] + [list(b)]),
                    "km": round(r["distance"] / 1000, 2),
                    "min": round(r["duration"] / 60),
                    "snap_m": round(snap),
                    "src": "osrm-" + profile,
                }
                self.cache.put(key, res)
                self.stats["osrm"] += 1
                return res
            except Exception as e:  # noqa: BLE001
                err = e
                if "SSL" in str(e) or "429" in str(e):
                    self.osrm_dead = True
                    break
                time.sleep(1.5 * (attempt + 1))
        self.log(f"  ! OSRM {profile} 失败：{err}，改用 Valhalla")
        res = self.valhalla(profile, a, b)
        if res:
            self.cache.put(key, res)
            self.stats["osrm"] += 1
        return res

    # ---- Valhalla（OSRM 被限流/封禁时的备用路由，同属 FOSSGIS）
    def valhalla(self, profile, a, b):
        costing = "pedestrian" if profile == "foot" else "auto"
        q = {"locations": [{"lon": a[0], "lat": a[1]}, {"lon": b[0], "lat": b[1]}], "costing": costing}
        url = "https://valhalla1.openstreetmap.de/route?json=" + urllib.parse.quote(json.dumps(q, separators=(",", ":")))
        for attempt in range(3):
            try:
                self._throttle(1.1)
                j = http_get_json(url)
                leg = j["trip"]["legs"][0]
                coords = _decode_poly6(leg["shape"])
                sm = j["trip"]["summary"]
                return {
                    "geom": simplify([list(a)] + coords + [list(b)]),
                    "km": round(sm["length"], 2),
                    "min": round(sm["time"] / 60),
                    "snap_m": 0,
                    "src": "valhalla-" + costing,
                }
            except Exception as e:  # noqa: BLE001
                err = e
                time.sleep(2 * (attempt + 1))
        self.log(f"  ! Valhalla {costing} 也失败：{err}")
        return None

    # ---- Overpass（地铁 / 有轨电车沿轨道）
    def _overpass(self, mode, line, bbox):
        s, w, n, e = bbox
        ref = normalize_line(line)
        routes = "subway|light_rail|monorail|train" if mode == "metro" else "tram|light_rail"
        sel = f'["ref"="{ref}"]' if ref.isascii() else f'["name"~"{ref}"]'
        q = (
            f'[out:json][timeout:90];'
            f'rel["type"="route"]["route"~"^({routes})$"]{sel}({s},{w},{n},{e})->.r;'
            f'.r out tags;'
            f'way(r.r)["railway"~"^(subway|light_rail|rail|tram|monorail|narrow_gauge|construction)$"];'
            f'out geom;'
        )
        key = hashlib.sha1(q.encode()).hexdigest()[:16]
        path = os.path.join(self.ovp_dir, key + ".json")
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        if not self.use_overpass or self.ovp_dead:
            return None
        self.log(f"  · Overpass 查询 {mode} {ref} 号线轨道（可能要 30–90 秒）…")
        for url in OVERPASS:
            for attempt in range(2):
                try:
                    self._throttle(1.0)
                    j = http_get_json(url, timeout=150, data=urllib.parse.urlencode({"data": q}).encode())
                    if not any(el.get("type") == "way" for el in j.get("elements", [])):
                        self.log(f"  ! Overpass 没找到 {ref} 号线轨道（检查 line 是否为 OSM 的 ref）")
                    os.makedirs(self.ovp_dir, exist_ok=True)
                    with open(path, "w", encoding="utf-8") as f:
                        json.dump(j, f, ensure_ascii=False, separators=(",", ":"))
                    self.stats["overpass"] += 1
                    self.ovp_fail = 0
                    return j
                except Exception as e:  # noqa: BLE001
                    self.log(f"  ! Overpass {url.split('/')[2]} 失败（第 {attempt + 1} 次）：{e}")
                    time.sleep(8 * (attempt + 1))
        self.ovp_fail += 1
        if self.ovp_fail >= 2:  # 连续两条线都失败：本次运行不再尝试，避免每段都等超时
            self.ovp_dead = True
            self.log("  ! Overpass 连续失败，本次其余地铁段直接画直线示意；稍后重跑会自动补上")
        return None

    def rail(self, mode, line, a, b, bbox):
        key = f"rail:{mode}:{normalize_line(line)}:{a[0]:.6f},{a[1]:.6f};{b[0]:.6f},{b[1]:.6f}"
        hit = self.cache.get(key)
        if hit:
            self.stats["cache"] += 1
            return hit
        if not line or not bbox:
            return None
        j = self._overpass(mode, line, bbox)
        if not j:
            return None
        res = rail_path(j, a, b)
        if res:
            self.cache.put(key, res)
        return res

    def save(self):
        self.cache.save()


def rail_path(j, a, b, max_snap_m=800):
    """在 Overpass 返回的轨道 way 上做最短路，返回 a→b 的轨道几何。"""
    colour = None
    adj = {}
    for el in j.get("elements", []):
        if el["type"] == "relation":
            colour = colour or el.get("tags", {}).get("colour")
        if el["type"] != "way" or "geometry" not in el:
            continue
        g = [(round(p["lon"], 6), round(p["lat"], 6)) for p in el["geometry"]]
        for u, v in zip(g, g[1:]):
            d = hav_km(u, v)
            adj.setdefault(u, []).append((v, d))
            adj.setdefault(v, []).append((u, d))
    if not adj:
        return None

    def nearest(p):
        bk = min(adj, key=lambda k: (k[0] - p[0]) ** 2 + ((k[1] - p[1]) * 1.1) ** 2)
        return bk, hav_km(p, bk) * 1000

    sa, da = nearest(a)
    sb, db = nearest(b)
    if max(da, db) > max_snap_m:
        return None
    dist = {sa: 0.0}
    prev = {}
    pq = [(0.0, sa)]
    while pq:
        d, u = heapq.heappop(pq)
        if u == sb:
            break
        if d > dist.get(u, 1e18):
            continue
        for v, w in adj[u]:
            nd = d + w
            if nd < dist.get(v, 1e18):
                dist[v] = nd
                prev[v] = u
                heapq.heappush(pq, (nd, v))
    if sb not in dist:
        return None
    path = [sb]
    while path[-1] != sa:
        path.append(prev[path[-1]])
    path.reverse()
    coords = [list(a)] + [list(p) for p in path] + [list(b)]
    return {
        "geom": simplify(coords, 0.00002),
        "km": round(dist[sb] + (da + db) / 1000, 2),
        "snap_m": round(max(da, db)),
        "src": "osm-rail",
        "osm_colour": colour,
    }


# ---------------------------------------------------------------- checks
class Issues:
    def __init__(self):
        self.items = []

    def add(self, level, where, msg):
        self.items.append({"level": level, "where": where, "msg": msg})

    def count(self, level):
        return sum(1 for i in self.items if i["level"] == level)


TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})$")


def valid_coord(it):
    lng, lat = it.get("lng"), it.get("lat")
    if isinstance(lng, bool) or isinstance(lat, bool):
        return False
    if not isinstance(lng, (int, float)) or not isinstance(lat, (int, float)):
        return False
    return not (lng == 0 or lat == 0) and abs(lat) <= 90 and abs(lng) <= 180


def check_city(c, issues, radius_km):
    cid = c.get("city", "?")
    for k in ["city", "name", "center", "plans"]:
        if k not in c:
            issues.add("error", cid, f"缺少顶层字段 {k}")
    center = c.get("center") or [0, 0]
    bounds = c.get("bounds")  # 可选 [[w,s],[e,n]]，比 radius 更精确

    def in_city(p):
        if bounds:
            (w, s), (e, n) = bounds
            return w <= p[0] <= e and s <= p[1] <= n
        return hav_km(center, p) <= radius_km

    def coord_check(where, it):
        lng, lat = it.get("lng"), it.get("lat")
        nums = all(isinstance(v, (int, float)) and not isinstance(v, bool) and v for v in (lng, lat))
        if nums and (abs(lat) > 90 or (abs(lat) > abs(lng) and abs(center[0]) > abs(center[1]))):
            issues.add("error", where, f"「{it.get('name', '')}」疑似经纬度颠倒（lng={lng}, lat={lat}）")
            return False
        if not valid_coord(it):
            issues.add("error", where, f"「{it.get('name', '')}」坐标为空、为 0 或超出范围")
            return False
        if not in_city((lng, lat)):
            issues.add("warn", where, f"「{it.get('name', '')}」离城市中心 {hav_km(center, (lng, lat)):.0f} km，超出城市范围")
        return True

    codes = {p.get("code") for p in c.get("plans", [])}
    for h in c.get("hotels", []):
        if not valid_coord(h):
            issues.add("warn", f"{cid}/酒店", f"「{h.get('name')}」没有坐标，地图上不显示")
        else:
            coord_check(f"{cid}/酒店", h)
        for u in h.get("used_by", []):
            if u not in codes:
                issues.add("warn", f"{cid}/酒店", f"「{h.get('name')}」used_by 引用了不存在的方案 {u}")
    for side in ("go", "back"):
        for t in (c.get("transport") or {}).get(side, []):
            for u in t.get("default_for", []):
                if u not in codes:
                    issues.add("warn", f"{cid}/交通", f"{t.get('no')} default_for 引用了不存在的方案 {u}")

    for p in c.get("plans", []):
        pid = p.get("id") or f"{cid}-{p.get('code')}"
        for k in ["id", "code", "name", "days", "cost"]:
            if k not in p:
                issues.add("error", pid, f"方案缺少字段 {k}")
        for pax in [str(x) for x in (c.get("pax") or ["2", "4"])]:  # 人均按哪些人数算，默认 2 / 4 人
            cc = (p.get("cost") or {}).get(pax)
            if not cc:
                issues.add("error", pid, f"cost 缺少 {pax} 人人均")
                continue
            for k in COST_KEYS:
                v = cc.get(k)
                if not isinstance(v, int) or isinstance(v, bool):
                    issues.add("warn", pid, f"cost.{pax}.{k} 应为人民币整数（现在是 {v!r}）")
        for di, d in enumerate(p.get("days", [])):
            where = f"{pid}/D{di + 1} {d.get('date', '')}"
            items = d.get("items", [])
            if not items:
                issues.add("warn", where, "当天没有行程")
                continue
            if items[0].get("type") != "stop" or items[-1].get("type") != "stop":
                issues.add("error", where, "items 必须以 stop 开头并以 stop 结尾")
            for i in range(1, len(items)):
                if items[i].get("type") == items[i - 1].get("type"):
                    issues.add("error", where, f"第 {i + 1} 项与前一项同为 {items[i].get('type')}，必须站点/交通交替")
            last_t = None
            for i, it in enumerate(items):
                if it.get("type") == "stop":
                    coord_check(where, it)
                    t = it.get("time")
                    if t:
                        m = TIME_RE.match(str(t))
                        if not m:
                            issues.add("info", where, f"「{it.get('name')}」时间格式不是 HH:MM：{t}")
                        else:
                            mins = int(m.group(1)) * 60 + int(m.group(2))
                            if last_t is not None and mins < last_t:
                                issues.add("warn", where, f"「{it.get('name')}」时间 {t} 早于上一站")
                            last_t = mins
                elif it.get("type") == "leg":
                    mode = it.get("mode")
                    if mode not in MODES:
                        issues.add("error", where, f"第 {i + 1} 项交通方式 {mode!r} 不在允许值内")
                    if mode in RAIL_ROUTED and not it.get("line"):
                        issues.add("warn", where, f"第 {i + 1} 项 {mode} 没写 line 线路号")
                else:
                    issues.add("error", where, f"第 {i + 1} 项 type={it.get('type')!r} 无效")


# ---------------------------------------------------------------- build
def route_city(c, router, issues, walk_max_km):
    cid = c.get("city", "?")
    # 预先算每条地铁线涉及的范围，一条线只查一次 Overpass
    boxes = {}
    for p in c.get("plans", []):
        for d in p.get("days", []):
            its = d.get("items", [])
            for i, it in enumerate(its):
                if it.get("type") == "leg" and it.get("mode") in RAIL_ROUTED and 0 < i < len(its) - 1:
                    a, b = its[i - 1], its[i + 1]
                    if valid_coord(a) and valid_coord(b):
                        bx = boxes.setdefault((it["mode"], normalize_line(it.get("line"))), [90, 180, -90, -180])
                        for q in (a, b):
                            bx[0], bx[1] = min(bx[0], q["lat"]), min(bx[1], q["lng"])
                            bx[2], bx[3] = max(bx[2], q["lat"]), max(bx[3], q["lng"])
    for k, bx in boxes.items():
        boxes[k] = [round(bx[0] - 0.02, 2), round(bx[1] - 0.02, 2), round(bx[2] + 0.02, 2), round(bx[3] + 0.02, 2)]

    for p in c.get("plans", []):
        pid = p.get("id") or f"{cid}-{p.get('code')}"
        for di, d in enumerate(p.get("days", [])):
            where = f"{pid}/D{di + 1} {d.get('date', '')}"
            its = d.get("items", [])
            for i, it in enumerate(its):
                if it.get("type") != "leg" or not (0 < i < len(its) - 1):
                    continue
                a, b = its[i - 1], its[i + 1]
                if not (valid_coord(a) and valid_coord(b)):
                    it["geom"] = None
                    continue
                A, B = (a["lng"], a["lat"]), (b["lng"], b["lat"])
                mode = it.get("mode")
                straight = hav_km(A, B)
                res = None
                if straight < 0.01:
                    res = {"geom": [list(A), list(B)], "km": 0, "src": "same"}
                elif mode in ROAD_ROUTED:
                    res = router.road(ROAD_ROUTED[mode], A, B)
                elif mode in RAIL_ROUTED:
                    res = router.rail(mode, it.get("line"), A, B, boxes.get((mode, normalize_line(it.get("line")))))
                for k in ("approx", "min"):
                    it.pop(k, None)
                if res:
                    it["geom"] = res["geom"]
                    it["km"] = res.get("km")
                    if res.get("min") is not None:
                        it["min"] = res["min"]
                    it["src"] = res["src"]
                    if mode in RAIL_ROUTED and not it.get("color") and res.get("osm_colour"):
                        it["color"] = res["osm_colour"]
                    if res.get("snap_m", 0) > 500:
                        issues.add("warn", where, f"「{a.get('name')}」→「{b.get('name')}」端点离路网/轨道 {res['snap_m']} m，坐标可能不准")
                    if mode in ROAD_ROUTED and straight > 0.3 and res["km"] > straight * 3.2:
                        issues.add("warn", where, f"「{a.get('name')}」→「{b.get('name')}」路线 {res['km']} km，是直线距离的 {res['km'] / straight:.1f} 倍，检查坐标")
                else:
                    if mode in ("flight", "train"):
                        it["geom"] = arc(A, B, 0.12 if mode == "flight" else 0.06)
                    else:
                        it["geom"] = [[round(A[0], 5), round(A[1], 5)], [round(B[0], 5), round(B[1], 5)]]
                    it["km"] = round(straight, 2)
                    it["src"] = "schematic"
                    if mode not in MODES:
                        it["approx"] = True
                    elif mode not in SCHEMATIC:
                        it["approx"] = True
                        router.stats["fallback"] += 1
                        issues.add("warn", where, f"「{a.get('name')}」→「{b.get('name')}」{mode} 路由失败，已退回直线（地图上标「示意」）")
                if mode == "walk" and (it.get("km") or 0) > walk_max_km:
                    issues.add("warn", where, f"「{a.get('name')}」→「{b.get('name')}」单段步行 {it['km']:.1f} km，超过 {walk_max_km:g} km")


def fetch_lib(cache_dir):
    out = {}
    for name in ("maplibre-gl.js", "maplibre-gl.css"):
        path = os.path.join(cache_dir, "lib", f"{MAPLIBRE_VER}-{name}")
        if not os.path.exists(path):
            os.makedirs(os.path.dirname(path), exist_ok=True)
            url = f"https://cdn.jsdelivr.net/npm/maplibre-gl@{MAPLIBRE_VER}/dist/{name}"
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=60) as r, open(path, "wb") as f:
                f.write(r.read())
        with open(path, encoding="utf-8") as f:
            out[name] = f.read()
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="把 plans.json 生成 MapLibre 行程地图（单文件 HTML）")
    ap.add_argument("inputs", nargs="+", help="一个或多个 plans.json")
    ap.add_argument("-o", "--out", default="map.html", help="输出 HTML 路径（默认 map.html）")
    ap.add_argument("--title", help="页面大标题（默认由城市名拼出，如「上海还是深圳」）")
    ap.add_argument("--subtitle", help="标题下的一句话说明")
    ap.add_argument("--cache", default=os.path.join(HERE, "cache"), help="路线缓存目录（默认 maptool/cache）")
    ap.add_argument("--offline", action="store_true", help="不发任何网络请求，只用缓存，没有的退回直线")
    ap.add_argument("--no-overpass", action="store_true", help="地铁/电车不查 Overpass，直接画直线示意")
    ap.add_argument("--radius-km", type=float, default=60, help="站点离城市中心超过多少 km 报警（默认 60）")
    ap.add_argument("--walk-max-km", type=float, default=3, help="单段步行超过多少 km 报警（默认 3）")
    ap.add_argument("--inline-lib", action="store_true", help="把 MapLibre JS/CSS 内联进 HTML（约 +1 MB，免 CDN）")
    ap.add_argument("--report", help="检查报告路径（默认 <out>.report.md）")
    ap.add_argument("--strict", action="store_true", help="有 error 级问题时以退出码 1 结束")
    args = ap.parse_args(argv)

    t0 = time.time()
    cities, issues = [], Issues()
    for path in args.inputs:
        with open(path, encoding="utf-8") as f:
            c = json.load(f)
        c["_src"] = os.path.relpath(path)
        cities.append(c)
        check_city(c, issues, args.radius_km)

    router = Router(args.cache, offline=args.offline, use_overpass=not args.no_overpass)
    for c in cities:
        print(f"→ 路线：{c.get('name')}（{c.get('city')}）")
        route_city(c, router, issues, args.walk_max_km)
        router.save()

    names = [c.get("name", c.get("city")) for c in cities]
    title = args.title or ("还是".join(names) if len(names) == 2 else "、".join(names) + " 行程地图")
    payload = {
        "title": title,
        "subtitle": args.subtitle or "",
        "generated": datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M"),
        "cities": [{k: v for k, v in c.items() if not k.startswith("_")} for c in cities],
    }
    with open(os.path.join(HERE, "template.html"), encoding="utf-8") as f:
        html = f.read()
    data_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    if args.inline_lib:
        lib = fetch_lib(args.cache)
        css_tag = "<style>" + lib["maplibre-gl.css"] + "</style>"
        js_tag = "<script>" + lib["maplibre-gl.js"].replace("</script", "<\\/script") + "</script>"
    else:
        css_tag = js_tag = ""
    html = (html.replace("__TITLE__", title)
                .replace("__MAPLIBRE_VER__", MAPLIBRE_VER)
                .replace("<!--INLINE_CSS-->", css_tag)
                .replace("<!--INLINE_JS-->", js_tag)
                .replace("__DATA__", data_json))
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(html)

    rp = args.report or (os.path.splitext(args.out)[0] + ".report.md")
    st = router.stats
    lines = ["# 地图数据检查报告", "",
             f"- 生成：{payload['generated']}；输入：{', '.join(c['_src'] for c in cities)}",
             f"- 路线：缓存命中 {st['cache']}，OSRM 新请求 {st['osrm']}，Overpass 新查询 {st['overpass']}，退回直线 {st['fallback']}",
             f"- 问题：error {issues.count('error')}，warn {issues.count('warn')}，info {issues.count('info')}", ""]
    for lv in ("error", "warn", "info"):
        lines += [f"- **{lv}** `{i['where']}` {i['msg']}" for i in issues.items if i["level"] == lv]
    if not issues.items:
        lines.append("没有发现问题。")
    with open(rp, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    print(f"✓ 输出 {args.out}（{os.path.getsize(args.out) / 1024:.0f} KB），用时 {time.time() - t0:.1f}s")
    print(f"  路线：缓存 {st['cache']} / OSRM {st['osrm']} / Overpass {st['overpass']} / 退回直线 {st['fallback']}")
    print(f"  检查：error {issues.count('error')}，warn {issues.count('warn')}，info {issues.count('info')} → {rp}")
    for i in issues.items:
        if i["level"] != "info":
            print(f"  [{i['level']}] {i['where']}: {i['msg']}")
    if args.strict and issues.count("error"):
        sys.exit(1)


if __name__ == "__main__":
    main()
