"""规划用的小工具：查坐标、查天气、把字幕整理成便于阅读的摘要文件。"""
from __future__ import annotations

import datetime as dt
import json
import os
import time
import urllib.parse
import urllib.request

from core import state

UA = "travel-agent-skill/1.0 (personal trip planner)"
WMO = {0: "晴", 1: "晴间多云", 2: "多云", 3: "阴", 45: "雾", 48: "雾", 51: "毛毛雨", 53: "毛毛雨", 55: "毛毛雨",
       61: "小雨", 63: "中雨", 65: "大雨", 66: "冻雨", 67: "冻雨", 71: "小雪", 73: "中雪", 75: "大雪", 77: "雪粒",
       80: "阵雨", 81: "阵雨", 82: "强阵雨", 85: "阵雪", 86: "阵雪", 95: "雷阵雨", 96: "雷阵雨伴冰雹", 99: "雷阵雨伴冰雹"}


def _json(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "zh-CN,zh"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


# ---------------------------------------------------------------- 坐标（WGS84，和地图底图一致）
_last = [0.0]


def geocode(names: list, city: str) -> list:
    path = os.path.join(state.CACHE, "geocode.json")
    cache = state.read_json(path, {}) or {}
    out = []
    for name in names:
        key = f"{city}|{name}"
        if key not in cache:
            hit = None
            for q in (f"{name} {city}", name):
                wait = 1.1 - (time.time() - _last[0])  # Nominatim 要求每秒不超过 1 次
                if wait > 0:
                    time.sleep(wait)
                _last[0] = time.time()
                try:
                    res = _json("https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode(
                        {"q": q, "format": "jsonv2", "limit": 3, "accept-language": "zh-CN", "countrycodes": "cn"}))
                except Exception as e:  # noqa: BLE001
                    res, hit = [], {"error": str(e)}
                if res:
                    r = res[0]
                    hit = {"lng": round(float(r["lon"]), 6), "lat": round(float(r["lat"]), 6),
                           "display": r.get("display_name", ""), "type": r.get("type", "")}
                    break
            if hit and "error" not in hit:
                cache[key] = hit
            else:
                out.append({"name": name, "found": False, **(hit or {})})
                continue
        out.append({"name": name, "found": True, **cache[key]})
    state.write_json(path, cache)
    return out


# ---------------------------------------------------------------- 天气（Open-Meteo，最多 16 天内）
def weather(city: str, start: str, end: str) -> dict:
    res = []
    for name in dict.fromkeys([city, city.rstrip("市") + "市", city.rstrip("市")]):
        g = _json("https://geocoding-api.open-meteo.com/v1/search?" + urllib.parse.urlencode(
            {"name": name, "count": 10, "language": "zh", "format": "json"}))
        res += g.get("results") or []
    if not res:
        raise RuntimeError(f"找不到城市 {city}")
    # 同名小地方很多（如福建也有「大连」村），优先中国境内、行政级别高、人口多的
    rank = {"PPLC": 4, "PPLA": 3, "PPLA2": 2, "PPLA3": 1}
    loc = max(res, key=lambda r: (r.get("country_code") == "CN", rank.get(r.get("feature_code"), 0),
                                  r.get("population") or 0))
    days = (dt.date.fromisoformat(start) - dt.date.today()).days
    if days > 15:
        return {"city": city, "lng": loc["longitude"], "lat": loc["latitude"], "days": [],
                "note": f"出发还有 {days} 天，超出 16 天预报范围，临近出发再查"}
    j = _json("https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode({
        "latitude": loc["latitude"], "longitude": loc["longitude"], "timezone": "auto",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
        "start_date": max(start, dt.date.today().isoformat()), "end_date": end}))
    d = j["daily"]
    out = []
    for i, date in enumerate(d["time"]):
        p = d["precipitation_probability_max"][i]
        text = (f"{WMO.get(d['weather_code'][i], '—')}，{round(d['temperature_2m_min'][i])}–"
                f"{round(d['temperature_2m_max'][i])}°C" + (f"，降水概率 {p}%" if p is not None else ""))
        out.append({"date": date, "text": text, "code": d["weather_code"][i], "rain": p})
    return {"city": city, "lng": loc["longitude"], "lat": loc["latitude"], "days": out,
            "source": {"name": "Open-Meteo 逐日预报", "url": "https://open-meteo.com/"}}


# ---------------------------------------------------------------- 字幕摘要
def digest(docs: list, path: str) -> dict:
    """把所有文档拼成一个 Markdown，按播放量排序，AI 读这一份就够了。"""
    docs = sorted(docs, key=lambda d: d.get("views") or 0, reverse=True)
    lines = [f"# 攻略原文（{len(docs)} 篇）", ""]
    for i, d in enumerate(docs, 1):
        views = f"{d['views'] / 10000:.1f} 万播放" if d.get("views") else ""
        lines += [f"## {i}. {d['title']}",
                  f"{d['author']} · {views} · {d.get('pubdate', '')} · {d['url']}", "", d["text"], ""]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return {"path": path, "docs": len(docs), "chars": sum(len(d["text"]) for d in docs)}