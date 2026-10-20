# 数据源

`sources/` 下每个目录是一个数据源，`sources/__init__.py` 里注册。目前：

| id | 名称 | 状态 | 内容 |
| --- | --- | --- | --- |
| `bilibili` | 哔哩哔哩 | 可用 | 攻略视频字幕（含 AI 字幕，需登录） |
| `xiaohongshu` | 小红书 | 即将支持 | 图文笔记 |
| `douyin` | 抖音 | 即将支持 | 短视频 |
| `mafengwo` | 马蜂窝 | 即将支持 | 游记 |

## 接一个新数据源

在 `sources/<id>/__init__.py` 实现下面这些函数（同步、只用标准库），然后：

1. 在 `sources/__init__.py` 的 `REGISTRY` 里加上 `"<id>": "sources.<id>"`；
2. 在 `core/state.py` 的 `SOURCES` 里把它的 `available` 改成 `True`（网页准备页会变成可勾选）。


```python
NAME = "哔哩哔哩"
def login_status() -> str | None          # 已登录返回用户名，否则 None
def qr_generate() -> dict                 # {"url": 二维码内容, "key": 轮询用}
def qr_poll(key) -> dict                  # {"state": need_scan|scanned|expired|ok|error, "uname"?}
def save_cookie(text) -> None             # 手动导入 cookie，格式不对时抛异常
def search(keywords, n, require, on_line) -> list[dict]   # 候选：bvid(=id)、title、author、views、duration、pubdate、url、cover
def fetch(ids, outdir, on_line) -> list[dict]            # 下载正文到 outdir，返回清单（status=成功/…）
def parse_progress(line) -> dict | None   # 把 fetch 的一行日志解析成 {status, bvid, title}
def documents(outdir) -> list[dict]       # 统一格式：source、id、title、author、url、views、pubdate、text
```

登录信息只能写在 `state.SECRETS`（`data/.secrets/`，权限 600）里，不要上传、不要打印到日志。

## B 站说明

- 搜索走 WBI 签名接口；未登录时首个请求偶尔被风控返回空，脚本会自动重试 2 次。
- AI 字幕（`ai-zh`）需要登录才能拿到；很多攻略视频只有 AI 字幕。
- 频繁请求会触发 412，脚本每个视频间隔 1.5 秒；遇到 412 等 1 分钟再试。
- `sources/bilibili/bili_sub.py` 也可以单独用：`python3 bili_sub.py --search 大连旅游攻略 -n 5 --format json,txt`。
