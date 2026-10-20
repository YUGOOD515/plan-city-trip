#!/usr/bin/env python3
"""B 站字幕下载工具 v2：输入 BV 号 / 视频链接 / 搜索关键词，一步下载字幕。

用法:
  python3 bili_sub.py BV1p3GA61EFp                         # 单个视频
  python3 bili_sub.py BV1xxx https://b23.tv/... BV2xxx      # 多个视频
  python3 bili_sub.py --search "大连旅游攻略" -n 5           # 搜索后批量下载前 5 个
  python3 bili_sub.py --search 大连攻略 --search 大连自由行 -n 10 --require 大连
                                                            # 多关键词合并去重，只留标题含「大连」的
  python3 bili_sub.py BV1xxx --all-pages                    # 多 P 视频下载全部分 P

登录态（AI 字幕必须登录才返回），任选其一:
  环境变量 BILI_COOKIE="SESSDATA=...; buvid3=..."
  或 --cookie-file 路径（内容为 "k=v; k=v" 一行），默认 .secrets/bili_cookie.txt

输出（默认 ./subs/）:
  <BV号>_<标题>.json（默认只输出 JSON，--format json,srt,txt 可加其他格式），以及汇总清单 manifest.json（标题、UP 主、播放量、时长、字幕语言、条数）
只依赖 Python 标准库。
"""
import argparse, hashlib, json, os, re, sys, time, urllib.error, urllib.parse, urllib.request
from functools import lru_cache

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
MIXIN = [46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49,
         33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40, 61,
         26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36,
         20, 34, 44, 52]
LANG_PREF = ["zh-CN", "zh-Hans", "zh", "ai-zh", "zh-Hant", "zh-TW", "zh-HK"]
COOKIE = ""
META = {}  # 搜索结果元数据，--list-only 时输出


# ---------- HTTP ----------
def get(url, params=None, raw=False, retries=3):
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    headers = {"User-Agent": UA, "Referer": "https://www.bilibili.com/"}
    if COOKIE and "bilibili.com" in url:
        headers["Cookie"] = COOKIE
    for i in range(retries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=20) as r:
                data = r.read()
                return data if raw else json.loads(data)
        except urllib.error.HTTPError as e:
            if e.code == 412:
                raise RuntimeError("被 B 站风控拦截(412)：请确认登录 cookie 有效，或调大 --delay") from None
            if i == retries - 1:
                raise
            time.sleep(1.5 * (i + 1))
        except Exception:
            if i == retries - 1:
                raise
            time.sleep(1.5 * (i + 1))


def api(path, params=None, wbi=False):
    params = dict(params or {})
    if wbi:
        params = sign_wbi(params)
    j = get("https://api.bilibili.com" + path, params)
    if j.get("code") != 0:
        raise RuntimeError(f"{path} 返回 code={j.get('code')} {j.get('message')}")
    return j["data"]


# ---------- WBI 签名 ----------
@lru_cache(maxsize=1)
def mixin_key():
    img = get("https://api.bilibili.com/x/web-interface/nav")["data"]["wbi_img"]
    k = lambda u: u.rsplit("/", 1)[1].split(".")[0]
    orig = k(img["img_url"]) + k(img["sub_url"])
    return "".join(orig[i] for i in MIXIN)[:32]


def sign_wbi(params):
    params["wts"] = int(time.time())
    params = {k: re.sub(r"[!'()*]", "", str(v)) for k, v in sorted(params.items())}
    q = urllib.parse.urlencode(params)
    params["w_rid"] = hashlib.md5((q + mixin_key()).encode()).hexdigest()
    return params


# ---------- 业务 ----------
def login_status():
    d = get("https://api.bilibili.com/x/web-interface/nav")["data"]
    return d.get("uname") if d.get("isLogin") else None


def parse_bvid(s):
    if "b23.tv" in s:  # 短链跳转
        req = urllib.request.Request(s if s.startswith("http") else "https://" + s, headers={"User-Agent": UA})
        s = urllib.request.urlopen(req, timeout=20).geturl()
    m = re.search(r"BV[0-9A-Za-z]{10}", s)
    if not m:
        raise ValueError(f"无法识别 BV 号: {s}")
    return m.group(0)


def search(keyword, n, require=None):
    words = [w for w in (require or "").split(",") if w]
    out, page, retry = [], 1, 0
    while len(out) < n and page <= 5:
        d = api("/x/web-interface/wbi/search/type",
                {"search_type": "video", "keyword": keyword, "page": page}, wbi=True)
        if page == 1 and not d.get("result") and retry < 2:
            retry += 1  # 未登录时首个请求偶尔被风控返回空结果，稍等重试
            time.sleep(2)
            continue
        for r in d.get("result") or []:
            title = re.sub(r"<[^>]+>", "", r.get("title", ""))
            if r.get("bvid") and (not words or any(w in title for w in words)):
                out.append(r["bvid"])
                META.setdefault(r["bvid"], {
                    "bvid": r["bvid"], "title": title, "author": r.get("author", ""),
                    "views": r.get("play") if isinstance(r.get("play"), int) else None,
                    "duration": ":".join(x.zfill(2) if i else x for i, x in enumerate(str(r.get("duration", "")).split(":"))),
                    "keyword": keyword,
                    "pubdate": time.strftime("%Y-%m-%d", time.localtime(r["pubdate"])) if r.get("pubdate") else "",
                    "cover": ("https:" + r["pic"]) if str(r.get("pic", "")).startswith("//") else r.get("pic", ""),
                    "url": f"https://www.bilibili.com/video/{r['bvid']}"})
        if not d.get("result"):
            break
        page += 1
    return list(dict.fromkeys(out))[:n]


def pick_sub(subs, lang=None):
    if not subs:
        return None
    if lang:
        for s in subs:
            if s["lan"] == lang:
                return s
    rank = {l: i for i, l in enumerate(LANG_PREF)}
    return sorted(subs, key=lambda s: rank.get(s["lan"], 99))[0]


def srt_time(t):
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3600_000); m, ms = divmod(ms, 60_000); s, ms = divmod(ms, 1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"


def safe(name, n=40, max_bytes=90):
    """文件名里的标题部分：去掉非法字符，限 n 个字、max_bytes 字节（中文 3 字节/字），避免 Windows 260 路径上限。"""
    s = re.sub(r'[\\/:*?"<>|\s]+', "_", name).strip("_")[:n]
    while len(s.encode("utf-8")) > max_bytes:
        s = s[:-1]
    return s.rstrip("_.") or "untitled"


def fetch_video(bvid, outdir, lang=None, all_pages=False, formats=("json",)):
    v = api("/x/web-interface/view", {"bvid": bvid})
    pages = v["pages"] if all_pages else v["pages"][:1]
    results = []
    for p in pages:
        info = {"bvid": bvid, "title": v["title"], "author": v["owner"]["name"],
                "views": v["stat"]["view"], "pubdate": time.strftime("%Y-%m-%d", time.localtime(v["pubdate"])),
                "url": f"https://www.bilibili.com/video/{bvid}" + (f"?p={p['page']}" if len(v["pages"]) > 1 else ""),
                "page": p["page"], "part": p["part"], "duration": p["duration"]}
        player = api("/x/player/wbi/v2", {"bvid": bvid, "cid": p["cid"]}, wbi=True)
        subs = (player.get("subtitle") or {}).get("subtitles") or []
        s = pick_sub(subs, lang)
        if not s or not s.get("subtitle_url"):
            info.update(status="无字幕", available=[x["lan"] for x in subs])
            results.append(info); continue
        url = s["subtitle_url"]
        body = get("https:" + url if url.startswith("//") else url)["body"]
        # 校验：字幕时长明显超过视频时长时，可能拿到了错配字幕
        if body and body[-1]["to"] > p["duration"] + 30:
            info.update(status="字幕疑似错配，已跳过", lang=s["lan"])
            results.append(info); continue
        base = os.path.join(outdir, f"{bvid}{'_p'+str(p['page']) if len(v['pages'])>1 else ''}_{safe(v['title'])}")
        if "json" in formats:
            with open(base + ".json", "w", encoding="utf-8") as f:
                json.dump({**info, "lang": s["lan"], "desc": v.get("desc", ""), "body": body},
                          f, ensure_ascii=False, indent=1)
        if "srt" in formats:
            with open(base + ".srt", "w", encoding="utf-8") as f:
                for i, l in enumerate(body, 1):
                    f.write(f"{i}\n{srt_time(l['from'])} --> {srt_time(l['to'])}\n{l['content']}\n\n")
        if "txt" in formats:
            with open(base + ".txt", "w", encoding="utf-8") as f:
                f.write(f"# {v['title']}\n# {info['author']} · {info['url']}\n\n")
                f.write("\n".join(l["content"] for l in body))
        info.update(status="成功", lang=s["lan"], lines=len(body),
                    chars=sum(len(l["content"]) for l in body), file=os.path.basename(base))
        results.append(info)
    return results


def main():
    global COOKIE
    ap = argparse.ArgumentParser(description="B 站字幕下载")
    ap.add_argument("videos", nargs="*", help="BV 号或视频链接")
    ap.add_argument("--search", action="append", default=[], help="搜索关键词，可重复多次")
    ap.add_argument("--require", help="只保留标题含这些词之一的视频，逗号分隔，如 大连,旅顺")
    ap.add_argument("--format", default="json", help="输出格式，逗号分隔：json,srt,txt（默认只输出 json）")
    ap.add_argument("-n", type=int, default=5, help="搜索时下载前 n 个（默认 5）")
    ap.add_argument("-o", "--out", default="subs", help="输出目录（默认 subs）")
    ap.add_argument("--lang", help="指定字幕语言，如 zh-CN / ai-zh / en-US")
    ap.add_argument("--all-pages", action="store_true", help="多 P 视频下载全部分 P")
    ap.add_argument("--cookie-file", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), ".secrets", "bili_cookie.txt"))
    ap.add_argument("--list-only", action="store_true", help="只搜索、输出候选视频列表（JSON），不下载字幕")
    ap.add_argument("--delay", type=float, default=1.5, help="视频间隔秒数，防风控（默认 1.5）")
    a = ap.parse_args()

    COOKIE = os.environ.get("BILI_COOKIE", "")
    if not COOKIE and os.path.exists(a.cookie_file):
        COOKIE = open(a.cookie_file, encoding="utf-8").read().strip()
    if "buvid3=" not in COOKIE:  # 未登录时补一个设备标识，否则接口返回 412
        try:
            d = get("https://api.bilibili.com/x/frontend/finger/spi")["data"]
            COOKIE = "; ".join(x for x in [COOKIE, f"buvid3={d['b_3']}", f"buvid4={d['b_4']}"] if x)
        except Exception:
            pass
    user = login_status()
    print(f"登录状态: {'已登录 ' + user if user else '未登录（AI 字幕将拿不到）'}", file=sys.stderr)

    bvids = [parse_bvid(x) for x in a.videos]
    for kw in a.search:
        found = search(kw, a.n, a.require)
        print(f"搜索「{kw}」得到 {len(found)} 个视频", file=sys.stderr)
        bvids += found
        time.sleep(a.delay)
    if a.list_only:
        json.dump([META.get(b, {"bvid": b}) for b in dict.fromkeys(bvids)], sys.stdout, ensure_ascii=False)
        print()
        return
    if not bvids:
        ap.error("请提供 BV 号/链接，或使用 --search")

    os.makedirs(a.out, exist_ok=True)
    manifest = []
    for i, bv in enumerate(dict.fromkeys(bvids)):
        if i:
            time.sleep(a.delay)
        try:
            rs = fetch_video(bv, a.out, a.lang, a.all_pages, tuple(a.format.split(",")))
        except Exception as e:
            rs = [{"bvid": bv, "status": f"失败: {e}"}]
        for r in rs:
            print(f"[{r['status']}] {bv} {r.get('title', '')[:30]} {r.get('lines', '')}", file=sys.stderr)
        manifest += rs

    mpath = os.path.join(a.out, "manifest.json")
    old = json.load(open(mpath, encoding="utf-8")) if os.path.exists(mpath) else []
    keep = {(m["bvid"], m.get("page")) for m in manifest}
    merged = [m for m in old if (m["bvid"], m.get("page")) not in keep] + manifest
    with open(mpath, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=1)
    ok = sum(r["status"] == "成功" for r in manifest)
    print(f"完成：{ok}/{len(manifest)} 成功，清单见 {mpath}", file=sys.stderr)
    json.dump(manifest, sys.stdout, ensure_ascii=False)  # 供 Agent 解析
    print()


if __name__ == "__main__":
    main()
