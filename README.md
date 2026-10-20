# 旅游 Agent · Skill

把「看 B 站攻略 → 提炼热门地点和避坑 → 排 3 套方案 → 出交互地图」做成一个可以接进任何 AI 工具（Claude Code、Codex、DeepSeek 等能跑命令的 Agent）的 Skill。

- AI 照 [SKILL.md](SKILL.md) 一步步执行，用 `trip.py` 调脚本；
- 你在本地网页（`http://127.0.0.1:8765`）里看 AI 在干什么、改出行条件、扫码登录 B 站、勾选视频；
- 60 秒不操作就按默认继续，基本全自动；
- 结果是一个单文件 HTML 地图（3 套方案、每天路线、人均分项、车次、酒店、攻略热度、避坑），可以直接发给同行的人。

## 安装

只需要 Python 3.9+（标准库，无第三方依赖）和能联网的电脑。

- **Claude Code**：把整个目录放到 `~/.claude/skills/travel-agent/`。
- **Codex / 其他 Agent**：放在任意位置，在项目说明（如 `AGENTS.md`）里写一句「做旅游规划时，读 `<路径>/SKILL.md` 并照做」。

然后对 AI 说：「帮我规划去大连玩，10/23–10/25，两个人，从北京出发」。

## 手动用

```bash
python3 trip.py serve --open          # 打开网页（--stop 关闭）
python3 trip.py --help                # 所有命令
```

两个工具也能单独用：`sources/bilibili/bili_sub.py`（B 站字幕下载）、`maptool/build_map.py`（plans.json → 地图），说明见各自目录。

## 隐私

- 网页只监听 `127.0.0.1`，跨站请求会被拒绝。
- B 站登录信息只存在本机 `data/.secrets/`（权限 600），不会出现在日志和网页里。
- `data/` 已加入 `.gitignore`。

## 已知限制

- 只支持单城市；数据源目前只有 B 站（小红书、抖音、马蜂窝在准备页上标「即将支持」）。
- 天气只有 16 天内的预报。
- 车票、酒店价格需要 AI 有网页搜索能力，否则是估算（方案里会标出来）。
- 地图底图来自 OpenFreeMap，国内能打开但可能偏慢；地图库已内联，不依赖 CDN。
