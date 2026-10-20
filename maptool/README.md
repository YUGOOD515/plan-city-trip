# maptool · MapLibre 行程地图生成工具

把一个或多个 `plans.json`（契约见 `../SPEC.md`）生成**单文件 HTML** 行程地图：MapLibre GL JS（CDN）+ OpenFreeMap 矢量底图（无需 key），数据内嵌，可直接作为 Notion 嵌入 / 附件。

## 用法

```bash
cd /data/trip_shsz
python3 maptool/build_map.py sh/plans.json sz/plans.json -o out/map.html \
  --title "上海还是深圳" --subtitle "10/22 周四午后出发，10/25 周日晚回武汉。"
```

运行结束会打印路线统计和检查结果，并写一份 `<out>.report.md`。

| 参数 | 说明 |
| --- | --- |
| `-o/--out` | 输出 HTML（默认 `map.html`） |
| `--title` / `--subtitle` | 大标题 / 副标题；默认标题由城市名拼出，两城时为「A还是B」 |
| `--cache DIR` | 路线缓存目录，默认 `maptool/cache`（`routes.json` + `overpass/*.json`） |
| `--offline` | 不发任何网络请求，只用缓存，缺的段退回直线 |
| `--no-overpass` | 地铁 / 电车不查 OSM 轨道，直接直线示意（Overpass 很慢时用） |
| `--radius-km 60` | 站点离 `center` 超过多少 km 报「超出城市范围」；`plans.json` 里可选 `bounds: [[w,s],[e,n]]` 更精确 |
| `--walk-max-km 3` | 单段步行超过多少 km 报警 |
| `--inline-lib` | 把 MapLibre JS/CSS 内联进 HTML（约 +1 MB），不再依赖 CDN |
| `--strict` | 有 error 级问题时退出码为 1 |

截图自检（无头 Chromium + SwiftShader WebGL）：

```bash
cd /data && node trip_shsz/maptool/shot.js trip_shsz/out/map.html trip_shsz/out/shot
```

依次输出 8 张图：桌面首屏、点站点 flyTo、第 2 天、全部、切第 2 个城市 + 4 人、整页、375 手机、深色模式；并打印高亮状态和页面报错（`errors` 应为空）。

## 路线怎么画

| mode | 路线来源 | 样式 |
| --- | --- | --- |
| walk | routing.openstreetmap.de foot（OSRM） | 城市强调色圆点线 |
| taxi / car / bus | routing.openstreetmap.de driving | 灰实线；bus 为灰虚线 |
| metro / tram | Overpass 取该线路（`line` = OSM `ref`）的轨道，图上最短路 | 线路色（`color`，缺省时用 OSM `colour`） |
| train / flight / boat / cable | 不路由 | 示意线（火车 / 飞机为弧线），图例和时间线标「示意」 |

- 所有结果缓存到 `cache/`，重复运行零请求（样例第二次运行 0.0 s）。
- OSRM 失败（含 429 限流、SSL 断开）自动改用 Valhalla（valhalla1.openstreetmap.de）按路网算；路线缓存每 5 条写一次盘，中途被杀也不丢。
- 不要同时跑两个构建：公共 OSRM 会限流甚至临时封 IP。
- Overpass 每个端点试 2 次，连续两条线失败后本次不再查。失败的段**退回直线、淡色显示、时间线标「示意」**，报告里有 warn；稍后重跑会自动补上。
- 每条地铁线只查一次 Overpass（覆盖该线所有段的范围）。

## 自动检查（报告 `<out>.report.md`）

- error：坐标为空 / 为 0、经纬度颠倒、items 不是「站点→交通→站点」交替、首尾不是 stop、mode 非法、缺 cost.2 / cost.4、缺必填字段
- warn：站点超出城市范围、单段步行 > 3 km、端点离路网 / 轨道 > 500 m（坐标可能不准，常见于机场、大公园质心）、路线长度 > 直线 3.2 倍、路由失败退回直线、metro 没写 line、时间倒序、cost 不是整数、酒店无坐标、`default_for` / `used_by` 引用不存在的方案
- info：时间不是 HH:MM

`sample/bad_plans.json` 故意包含上述错误，可用来回归测试：`python3 build_map.py sample/bad_plans.json -o /tmp/bad.html --offline`。

## 页面交互

- 顶部：切换城市、2 人 / 4 人人均。
- 地图 + 右侧面板：切方案（显示人均，最省方案金色「最省」标）→ 切日期（含「全部」）→ 时间线。
- 点时间线站点：地图 flyTo 并高亮该站；点交通段：缩放到该段并高亮；悬停交通段：地图同步高亮；点地图上的站点：时间线滚动到该站（必要时自动切到那一天）。
- 同一地点多次到访合并为一个标记（如「3·5」）；方案住宿若不在站点里，显示「宿」标记。标签自动避让。
- 下方：所有方案人均分项对比（点一行打开该方案）、去程 / 回程班次表（高铁和飞机）、酒店表、攻略提及热度（含各方案是否去了）和避坑、视频列表、来源与署名。
- 视觉：apply-jiy-design-style tokens（浅 / 深色自动），城市 1 用 blue、城市 2 用 green，gold 只用于「最省」；4pt 间距；中文字体栈含 PingFang SC / Noto Sans SC；数字 tabular-nums；尊重 prefers-reduced-motion。
- 地图滚轮需按 Ctrl / ⌘（cooperative gestures），嵌入 Notion 时不会抢页面滚动。

## 已知限制

- **国内访问**：OpenFreeMap 瓦片走 Cloudflare，国内通常能打开但可能慢；MapLibre JS 依次尝试 jsDelivr → npmmirror → unpkg（每个 9 s 超时）。网络差时用 `--inline-lib` 去掉 CDN 依赖。底图样式 12 s 内拿不到会退到纯色背景，路线和站点照常显示；MapLibre 本身加载失败时只剩时间线和表格。
- Overpass 很慢（单条线 30–90 s，偶发 502 / 超时）。第一次跑会久一些，之后走缓存。
- 地铁轨道按 OSM `ref` 匹配（上海 / 深圳是纯数字线路号）；`line` 写成「浦江线」这类名字时按 name 模糊匹配，可能找不到，会退回直线。
- 地铁最短路按轨道几何走，不区分上下行和换乘；跨线路的一段请拆成两段 leg（中间加换乘站）。
- 中文地名用浏览器本地字体渲染（`localIdeographFontFamily`），不同系统字形略有差异。
- 底图数据和路线均为 WGS84；高德 / 百度坐标会偏几百米，检查不出来（只会体现为「端点离路网远」）。
