---
name: plan-city-trip
description: 单城市旅游规划。读 B 站攻略视频字幕，提炼热门地点和避坑，结合车票、酒店、天气排出 3 套方案，生成带路线的交互地图。用户说「去 X 玩 / 帮我规划 X 旅游 / X 怎么玩」或在网页里输入城市时使用。
---

# 单城市行程规划（plan-city-trip）

你负责「想」和「写方案」，脚本负责「抓数据、等用户、出图」。用户在本地网页里看你工作、改条件、扫码、确认。

所有命令都在本目录下运行：`python3 trip.py <命令>`（只用 Python 3.9+ 标准库，Windows / macOS / Linux 通用）。
每条命令只输出**一行 JSON**；先看 `status`，再看 `hint`。`status` 可能是：

| status | 含义 | 你要做什么 |
| --- | --- | --- |
| `ok` / `answered` / `done` | 成功 | 继续下一步 |
| `pending` | 还在等用户或后台任务 | **原样再运行同一条命令**（倒计时不会重置）。不要改参数、不要跳过 |
| `started` | 后台任务已启动 | 用 `trip.py wait <case> <job>` 等它 |
| `error` | 失败 | 读 `error`，按下文「出错时」处理 |

## 0. 铁律

1. **一开始就启动网页**，把地址告诉用户，之后每一步都写 `log`，让用户在网页上看得到你在干什么。
2. **你知道的就先填，填齐了再问**：聊天里出现过的出发地、日期、人数、预算、偏好、特殊要求，**全部**在 `new --set` 里一次填好（或在 `ask prepare` 之前用 `set --by chat` 补齐），再运行 `ask`。网页会标「来自对话」，用户只需要补。别再在聊天里重复问网页上能填的东西。
3. **基本全自动**：`ask` 节点用户 60 秒没操作就按默认继续，你不用催。只有两种情况会一直等：必填项（出发地、日期）缺失、用户点了「先别继续」。登录**从不**自动跳过。
4. **用户的修改优先**：`set` 不会覆盖用户在网页上改过的值（输出里的 `kept_user_value`）。
5. 慢的事情放后台：`fetch --bg`、`build --bg`，然后 `wait`。
6. 价格：有网页搜索工具就查实时价（12306、航司、携程/飞猪/美团等）并在 `cost_notes` 写来源；查不到就按经验估，并在 note 里写「估算」。**班次号 / 航班号只写查到过的**（时刻表来源放进 `sources`）；没查过就写「高铁（约 4.5 小时）」这类描述。
8. 不要在案例目录里写自己的脚本：查字幕用 `mentions` / `grep`（见第 5 步）。
7. 用户在聊天里回复了网页上的问题（比如「就这样吧」「去掉第 3 个视频」），用 `trip.py answer` 代为提交，再运行原来的 `ask` 取回结果。

## 1. 启动

```bash
python3 trip.py serve            # 已在运行会直接返回地址；--open 尝试自动打开浏览器；--stop 关闭
```
把 `url` 发给用户：「在浏览器打开这个地址，可以看到我每一步在做什么」。

- 用户在聊天里说了城市 → `python3 trip.py new 大连 --set origin=武汉 --set people=2 --set interests=美食,海边`
  （字段见下表；能从对话推出来的都填上，日期写成 `YYYY-MM-DD`）
- 用户没说城市 → `python3 trip.py next`，等用户在网页首页输入（`pending` 就再运行）。返回的 case 里已带上用户在网页填的条件。
- 继续以前的规划 → `python3 trip.py list` 找到 case，`python3 trip.py open <case>`。

| 字段 | 说明 | 默认 |
| --- | --- | --- |
| `origin` | 出发城市（必填，会记住给下次用） | 上次的值 |
| `date_start` / `date_end` | 出发 / 返回日期（必填） | 下周五–周日 |
| `people` | 人数 | 2 |
| `budget` | 人均预算（元，不含购物） | 不限 |
| `pace` | 三种都看看 / 轻松 / 适中 / 较累 | 三种都看看 |
| `transport` | 都比较 / 高铁优先 / 飞机优先 / 自驾 | 都比较 |
| `interests` | 逗号分隔：美食,自然风光,城市漫步,历史人文,博物馆展馆,拍照出片,夜景,海边,购物 | 空 |
| `note` | 上面字段装不下的额外要求（如「有老人，少爬山」「想看一场演出」），原话照抄；**不要**重复出发地、日期、人数、预算 | 空 |
| `per_keyword` | 每个关键词取几个视频 | 8 |

## 2. 准备（prepare）

```bash
python3 trip.py ask <case> prepare
```
网页上显示出行条件和数据源，用户确认或 60 秒无操作后返回 `answered`，`answer` 就是最终条件（以它为准，不要用你自己记的）。
- 必填项缺失时会一直等；补齐那一刻倒计时重新开始，用户有 60 秒看一眼。你在等待期间 `set` 了新值，倒计时也会重置。
- `--timeout` 一般不用改；任何 `ask` 都别调到 60 秒以下。

## 3. 登录（login）

```bash
python3 trip.py stage <case> login
python3 trip.py login <case>
```
- 已登录直接返回 `ok`。
- 没登录时网页会显示二维码（自动续期），返回 `pending` 就**再运行 login**。告诉用户「请用 B 站 App 扫网页上的二维码」，一次就够，别刷屏。
- 用户也可以在网页上粘贴 cookie。登录信息只存在本机 `data/.secrets/`。
- 登录成功脚本会自己写日志，你不用再 `log` 一遍。
- 用户明确说不登录 → `trip.py stage <case> login --status skipped`，继续。没登录时大多数视频拿不到 AI 字幕，结果会差很多，提醒用户一次。

## 4. 收集（collect）

```bash
python3 trip.py search <case> --kw "大连旅游攻略" --kw "大连三天两夜" --kw "大连避坑" --require 大连,旅顺
python3 trip.py pick <case> --off BV1xx,BV1yy --reason "和大连无关 / 广告 / 太旧"
python3 trip.py ask <case> videos
python3 trip.py fetch <case> --bg && python3 trip.py wait <case> fetch
```
- 关键词 3–5 个：`{城市}旅游攻略`、`{城市}{N}天{N-1}夜`、`{城市}避坑`、`{城市}美食`，再按用户偏好加（如 `{城市}拍照`、`{城市}博物馆`）。`--require` 填城市名和主要区县，过滤跑题视频。
- 先自己看一遍候选，用 `pick --off` 去掉跑题、纯广告、5 年前的、时长 < 1 分钟的，再 `ask videos` 让用户确认。`--reason` 按视频分别写具体原因（「讲武汉楼市」「2019 年的」），会显示在网页上；不要一批都写「无关 / 太旧」。
- `fetch` 返回 `ok: 0` 时看 `hint`：多半是没登录。

## 5. 提炼（digest）

```bash
python3 trip.py stage <case> digest
python3 trip.py digest <case>      # 生成 sources/digest.md（按播放量排序的全部字幕）
python3 trip.py weather <case>     # 16 天内才有预报
```
字幕多（`hint` 会提示，通常 > 3 万字）时不必通读 `digest.md`，用这两个命令：
```bash
python3 trip.py mentions <case> 黄鹤楼 湖北省博物馆=省博 武汉大学=武大 东湖   # 每个地点被几个视频提到，可直接填 mentions
python3 trip.py grep <case> --pits            # 避坑句（避雷、别去、智商税、排队、预约…），带前后文
python3 trip.py grep <case> 热干面 过早 --width 60   # 任意关键词的上下文
```
先扫一眼 `digest.md` 的标题和开头列出候选地点，再用 `mentions` 一次统计完。

整理：
- **热门地点**：每个地点被几个视频提到（`mentions`），按次数排序；提得多却没进任何方案的（比如被 7 个视频提到的地方），在方案 note 或 tips 里说明为什么没排；
- **避坑**（`pits`）：具体、可执行（「海鲜代加工先问清称重方式」，不要写「注意安全」）；
- 美食、交通、住哪一片的建议。
每整理出一类就 `log` 一行（如 `log <case> "热门地点：星海广场 10 次、老虎滩 11 次…"`）。

## 6. 规划（plan）

```bash
python3 trip.py stage <case> plan
python3 trip.py geocode <case> 星海广场 老虎滩海洋公园 东港 --city 大连
```
按 [references/planning.md](references/planning.md) 排 3 套方案（A 经典 / B 主题 / C 省钱），查车票、酒店价格，然后**写 `<case 目录>/plans.json`**（`dir` 在 `new` / `status` 的输出里），格式见 [references/plans-schema.md](references/plans-schema.md)，完整样例 [references/example.plans.json](references/example.plans.json)。

- 坐标必须是 WGS84：用 `geocode` 查，查不到的用你的知识给近似值，并在 note 写「坐标约略」。不要用高德 / 百度坐标。
- `pace` 不是「三种都看看」时，3 套方案都按这个强度排。
- **地铁换乘必须拆段**：`4 号线 → 洪山广场换 2 号线` 要写成「leg metro line 4 → stop 洪山广场（换乘）→ leg metro line 2」，一段只写一条线，否则路线会退回直线。
- **前后一致**：每套方案第一天的起点、最后一天的终点要和它默认的去返交通一致（默认飞机就从机场开始 / 结束，不要写火车站）；`tips` 只提这套方案真的会去的地方。

可选：方案写好后让用户挑一下再出图（不需要就跳过）：
```bash
python3 trip.py ask <case> confirm --title "3 套方案排好了，开始出图？" --desc "A 经典 ¥2,110 / B 旅顺 ¥2,070 / C 省钱 ¥1,240（人均）"
```

## 7. 出图（build）

```bash
python3 trip.py build <case> --bg && python3 trip.py wait <case> build
```
- 返回里有 `errors` / `warns` 和 `report_path`。**有 error 必须修 plans.json 再 build**；warn 逐条看：
  - 「单段步行超过 3 km」→ 改成 taxi / metro；
  - 「端点离路网远 / 路线是直线的 N 倍」→ 坐标多半错了，重新 geocode；
  - 「metro 路由失败，已退回直线」→ 可以接受（地图上会标「示意」），或加 `--no-overpass` 直接示意。
- 第一次算路线可能要几分钟（结果有缓存）。别同时跑两个 build。

## 8. 完成

```bash
python3 trip.py done <case> --summary "3 套方案，人均 ¥1,240–2,110；最省是 C，最推荐 A。"
```
在聊天里给用户：一句话结论 + 三套方案人均 + 网页地址（结果页里有可交互地图，`out/map.html` 也可以单独发给别人，双击就能打开）。

## 出错时

- 先 `trip.py log <case> "…" --kind err` 让用户知道，再判断能否绕过：B 站 412 / 风控 → 等 1 分钟重试一次；网络问题 → 重试一次；仍失败 → `trip.py fail <case> "原因"`，在聊天里说明，用户之后说「继续规划」时用 `status` 看停在哪一步，从那里接着做。
- 不要删除 `data/` 里的东西；每个案例自成一个目录（`data/cases/<id>/`）。

## 并行（可选）

有子 Agent 能力时，可以把「读 digest 整理热门地点 / 避坑」和「查车票、酒店价格」并行。所有 `trip.py` 写入都有文件锁，可以同时调用；但 `ask` / `login` 同一时间只能有一个。没有子 Agent 就按上面的顺序做。

## 文件

```
trip.py                AI 用的命令行（入口）
core/                  网页服务、案例状态、天气 / 坐标 / 摘要工具
sources/bilibili/      B 站：扫码登录、搜索、字幕下载（bili_sub.py 可单独用）
maptool/               plans.json → 单文件交互地图（build_map.py 可单独用）
web/                   本地网页
references/            方案规则、数据格式、样例
data/                  运行时生成：案例、缓存、登录信息（已 .gitignore）
```
