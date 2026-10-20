"""数据源注册表。新平台照 sources/bilibili 的结构实现同样的函数，再在这里登记即可。

每个数据源模块需要提供：
  NAME                      显示名
  login_status() -> str|None 已登录时返回用户名
  qr_generate() -> dict      {"url": 二维码内容, "key": 轮询用 key}
  qr_poll(key) -> dict       {"state": "need_scan|scanned|ok|expired|error", "uname"?: str}
  save_cookie(text)          手动粘贴的登录信息
  search(keywords, n, require) -> list[dict]   候选内容（统一字段见 references/sources.md）
  fetch(ids, outdir, on_progress) -> list[dict] 下载正文，返回清单
  documents(outdir) -> list[dict]              统一格式的攻略文档
"""
from importlib import import_module

REGISTRY = {"bilibili": "sources.bilibili"}


def get(source_id: str):
    if source_id not in REGISTRY:
        raise KeyError(f"数据源 {source_id} 还不支持，目前可用：{', '.join(REGISTRY)}")
    return import_module(REGISTRY[source_id])