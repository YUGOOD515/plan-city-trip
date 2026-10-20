# plans.json 数据格式

AI 写到 `<case 目录>/plans.json`，`trip.py build` 读取并生成地图。完整样例：[example.plans.json](example.plans.json)。
`build` 会自动补上 `videos`（视频列表）、`weather`（天气）、`pax`（人数）和天气来源，你不用写。

## 顶层

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `city` | ✓ | 城市 id，如 `"大连"` |
| `name` | ✓ | 显示名 |
| `center` | ✓ | `[lng, lat]`，城市中心（WGS84） |
| `zoom` |  | 初始缩放，市区 11–12 |
| `bounds` |  | `[[西, 南], [东, 北]]`；不写时按离 center 60 km 判断「超出城市范围」 |
| `pax` |  | 人均按几人算，字符串数组，如 `["2"]`；默认取出行人数。写两个（`["2","4"]`）网页会出现人数切换 |
| `nights` |  | 住几晚，默认 3 |
| `room_size` |  | 几人一间，默认 2 |
| `checked_at` |  | 价格查询日期 `YYYY-MM-DD` |
| `plans` | ✓ | 3 套方案，见下 |
| `hotels` |  | 酒店 |
| `transport` |  | 去程 / 回程班次 |
| `mentions` |  | 攻略提及热度 |
| `pits` |  | 避坑，字符串数组 |
| `sources` |  | `[{name, url}]`，你查价格、查资料用到的来源 |
| `title` / `subtitle` |  | 地图大标题 / 副标题，默认「{城市}怎么玩」/「日期 · 出发地」 |

## plans[]

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `id` | ✓ | 全局唯一，如 `"dl-A"` |
| `code` | ✓ | `"A"` / `"B"` / `"C"` |
| `name` | ✓ | 4–6 字，如「经典城区」 |
| `theme` |  | 一句话主题 |
| `intensity` |  | 轻松 / 适中 / 较累 |
| `fit` |  | 适合谁 |
| `go` / `back` / `stay` |  | 去程、回程、住哪一片（一句话） |
| `days` | ✓ | 每天一项，见下 |
| `cost` | ✓ | `{"2": {往返交通, 住宿, 当地交通, 门票项目, 餐饮}}`，**人均、人民币整数**；`pax` 里每个人数都要有 |
| `cost_notes` |  | `{分项: 说明}`，写清怎么算的、价格来源、是否估算 |
| `tips` |  | 这个方案的提醒，字符串数组 |

### days[]

```json
{"date": "2026-10-24", "title": "滨海路一日", "items": [
  {"type": "stop", "name": "老虎滩海洋公园", "lng": 121.6752, "lat": 38.8771, "time": "09:00", "note": "极地馆必看", "mentions": 11},
  {"type": "leg", "mode": "taxi", "note": "打车约 10 分钟"},
  {"type": "stop", "name": "燕窝岭", "lng": 121.7, "lat": 38.876, "time": "14:00"}
]}
```

- `items` **站点和交通交替**，以 stop 开头、以 stop 结尾。
- stop：`name`、`lng`、`lat` 必填；`time` 为 `HH:MM`，同一天内递增；`mentions` 是被几个视频提到（可选，会显示「攻略 N」）。
- leg：`mode` 只能是 `walk` `taxi` `car` `bus` `metro` `tram` `train` `flight` `boat` `cable`。
  - `metro` / `tram` 要写 `line`（线路号，如 `"1"`）和可选 `color`（线路色）；跨线路拆成两段，中间加换乘站。
  - 步行单段别超过 3 km。
  - `train` / `flight` / `boat` / `cable` 画示意弧线，不算路线。
- 第一天从到达站开始，最后一天以离开的车站 / 机场结束。

## hotels[]

```json
{"name": "…", "area": "中山广场", "lng": 121.6455, "lat": 38.9175, "price_total": 900, "rating": 4.6, "note": "含早", "used_by": ["A", "B"]}
```
`price_total` 是 `nights` 晚一间房的总价；`used_by` 是方案 code。

## transport

```json
{"go": [{"type": "高铁", "no": "G8017", "from": "北京南", "to": "大连北", "time": "07:46–12:51", "price": 430, "note": "二等座", "default_for": ["A", "B"]}],
 "back": [ … ]}
```
`price` 是单程每人；`default_for` 是把这一班当作默认的方案。查不到实时班次时，`no` 写「高铁」之类的类型，`note` 写「估算」。

## mentions[]

```json
{"name": "星海广场", "count": 10, "plans": ["A", "B", "C"]}
```
`count` 是提到它的视频数（不是次数），`plans` 是去了这里的方案。

## 检查规则（build 报告）

- **error**（必须修）：坐标为空 / 为 0 / 经纬度颠倒；items 不交替或首尾不是 stop；mode 不合法；缺 `cost.<人数>`；缺必填字段。
- **warn**（逐条判断）：站点超出城市范围；单段步行 > 3 km；端点离路网 > 500 m；路线长度 > 直线 3.2 倍；路由失败退回直线；metro 没写 line；时间倒序；cost 不是整数；酒店无坐标；`used_by` / `default_for` 引用不存在的方案。
