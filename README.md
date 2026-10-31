# 小度反向接入 · XiaoDu Reverse for Home Assistant

[![Release](https://img.shields.io/github/v/release/helloArmy/ha-xiaodu-reverse?label=release)](https://github.com/helloArmy/ha-xiaodu-reverse/releases)
[![HACS](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/helloArmy/ha-xiaodu-reverse)
[![HA](https://img.shields.io/badge/Home%20Assistant-2024.1%2B-18BCEE.svg)](https://www.home-assistant.io/)

把**小度（百度 DuerOS）智能家居 App 里已有的设备**反向接入 Home Assistant —— 你可以继续在小度 App 里管理设备，同时在 HA 里做自动化、仪表盘和语音联动。

> **「反向」的含义**：通常是小度去控制 HA 里的设备；这个集成走反方向 —— 从百度小度云把小度生态下的设备「反向拉回」HA。

## 功能

- **两种登录**：百度账号密码（推荐，短信验证后可自动换取 BDUSS）／直接粘贴 BDUSS Cookie
- **短信验证**：百度要求二次验证时，点「发送验证码」→ 填 6 位码 → 自动换取 BDUSS，60 秒内不可重发
- **多家庭 / 多设备**：选择家庭后多选要接入的设备
- **全平台实体**：`light` `switch` `cover` `climate` `fan` `lock` `button` `sensor` `vacuum`
- **面板拆分**：一台多功能设备按 `panels.功能控制` 拆成多个开关实体，每个功能独立可控、独立回读状态
- **传感器**：温度、湿度、PM2.5、CO₂、滤芯寿命、电量等按 `stateSetting` 自动生成
- **凭据自愈**：轮询发现 `not login` 时用保存的账号密码自动重登，失败才要求重新认证
- **批量轮询**：优先用 `GET_APPLIANCES_BY_ID` 一次拉全部设备状态，缺数据再逐台回退

## 环境要求

| 项 | 要求 |
|---|---|
| Home Assistant | **2024.1.0 或更高** |
| 安装形态 | HAOS / Supervised / Container / Core 均可 |
| Python | 随 HA 自带（2024.1 为 3.11），无需额外安装 |
| 网络 | 需能访问百度小度云（`passport.baidu.com`、`xiaodu.baidu.com`） |
| 账号 | 小度 App 中已绑定设备的小度（百度）账号 |
| 额外依赖 | 无。只用 HA 自带的 `aiohttp` 与 `cryptography` |

**跨版本兼容说明**：HA 2024.1 到当前版本之间有若干核心 API 发生变更，集成内已按版本自适应，无需你手动处理：

| 能力 | 2024.1 ~ 2024.5 | 2024.6 ~ 2024.10 | 2024.11+ | 2025.1+ |
|---|---|---|---|---|
| `ConfigEntry` 泛型 | 不支持，退回非泛型 | 支持 | 支持 | 支持 |
| 吸尘器状态 | `state` 字符串 | `state` 字符串 | `state` 字符串 | `activity` + `VacuumActivity` 枚举 |
| 选项流程的 config entry | 集成自行注入 | 集成自行注入 | 框架自动注入 | 框架自动注入 |

其余用到的接口（`CoverEntityFeature`、`ClimateEntityFeature`、`ColorMode`、`SensorDeviceClass` 等）在 2024.1 时已存在，且均采用 `getattr` 容错构造，不会因后续版本增删成员而导入失败。

## 安装

### 方式一：HACS 一键打开（推荐）

[![在 HACS 中打开](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=helloArmy&repository=ha-xiaodu-reverse&category=integration)

> 本集成尚未进入 HACS 默认仓库，所以**首次**仍要先按「方式二」添加一次自定义存储库；
> 添加之后这个按钮就能直接在 HACS 里打开本集成页面，后续安装 / 更新都走它。

### 方式二：HACS 添加自定义存储库

1. HACS → 集成 → 右上角 ⋮ → 自定义存储库
2. 填入 `https://github.com/helloArmy/ha-xiaodu-reverse`，类别选「集成」
3. 搜索「小度反向接入」→ 安装 → 重启 Home Assistant

### 方式三：手动复制文件（最快，适合调试）

把 `custom_components/xiaodu_reverse` 整个目录复制到 HA 配置目录：

```
<HA 配置目录>/custom_components/xiaodu_reverse/
```

然后**重启 Home Assistant**。

## 配置

**只需要百度账号和密码，再加一次短信验证码。** `BAIDUID` 与 `BDUSS` 都由集成自动获取，不需要打开开发者工具、也不需要复制任何 Cookie。

1. **设置 → 设备与服务 → 添加集成 → 搜索「小度反向接入」**
2. 登录方式选「百度账号密码登录」，填小度 App 绑定的百度账号与密码 → 提交
3. 百度会要求短信验证，界面连续出现两步：
   - **发送验证码** —— 点提交即向百度请求下发，60 秒后可重发
   - **输入验证码** —— 填入手机收到的 6 位码并提交
4. 提交后集成自动换取 BDUSS 并完成登录 → 选择家庭 → 多选设备 → 完成

> 整个过程里 `BAIDUID`、`dv` 设备指纹、`BDUSS` 都是集成内部处理的，用户只需要提供账号密码和收到的验证码。

### 备用：手动粘贴 BDUSS

只有在这两种情况下才需要：

- 该账号收不到短信验证码（例如未绑定手机）
- 想跳过验证步骤，直接用浏览器里已有的登录态

1. 浏览器打开 <https://xiaodu.baidu.com/saiya/smarthome/index.html> 并登录
2. 开发者工具 → Application → Cookies → 找到 `BDUSS`，复制其值
3. 在集成里选「直接填写 BDUSS Cookie」粘贴

Cookie 有效期通常数月，失效后集成会提示重新认证。

## 选项

集成条目 → 配置，可进行：

- **更新 BDUSS Cookie**
- **使用账号密码重新登录**（推荐；会要求一次短信验证码，之后自动换取 BDUSS）
- **重新选择设备**
- **修改状态刷新间隔**（默认 300 秒，最小 30 秒）

> 小度云是轮询型 API，间隔建议不低于 120 秒，避免触发限流。

## 支持的设备类型

| 平台 | 覆盖的设备类型（节选） |
|---|---|
| `light` | LIGHT / LAMP / LIGHT_STRIP / DESK_LAMP / CEILING_LIGHT |
| `switch` | SOCKET / SWITCH / OUTLET / PLUG / 洗衣机 / 热水器 / 空气净化器 / 加湿器 / 晾衣架 / 电饭煲 / 微波炉 / 冰箱 … |
| `cover` | CURTAIN / CURTAIN_SWITCH / WINDOW / SHADE / BLIND / GARAGE_DOOR / AWNING |
| `climate` | AIR_CONDITION / AIRCONDITION / AC |
| `fan` | FAN / CEILING_FAN / TOWER_FAN / VENTILATOR / EXHAUST_FAN |
| `lock` | DOOR_LOCK / SMART_LOCK / FINGERPRINT_LOCK / SMART_GATING |
| `vacuum` | SWEEPING_ROBOT / ROBOT_VACUUM / SWEEPER |
| `button` | SCENE_TRIGGER / SCENE，以及设备面板里的单向动作按钮 |
| `sensor` | 温度 / 湿度 / PM2.5 / PM10 / CO₂ / CO / TVOC / 甲醛 / AQI / 滤芯寿命 / 电量 / 功率 … |

未识别的设备仍会以通用开关形式接入，不会丢失。

## 工作原理

```
HA 集成  ──账密登录──▶  passport.baidu.com/v2/api/?login        45 参数 + dv 设备指纹
         ──二次验证──▶  passport.baidu.com/v3/api/auth/widget    action=get / send / check
         ──换取凭据──▶  passport.baidu.com/v2/?loginproxy&apiver=v3&tt=…   → BDUSS
         ──轮询────▶  /saiya/smarthome/appliance          设备列表
                    /saiya/smarthome/appliancedetails   单设备详情 + stateSetting + panels
         ──控制──▶  /saiya/smarthome/directivesend      DuerOS.ConnectedHome.Control 指令
```

登录链路有一个容易踩的坑：**百度在 120021 响应里会下发会话 cookie（`UBI`、`logTraceID`、`pplogid`）**，
只有把它们完整带回去，验证控件才认这个会话。任何手写的 `Cookie:` 头都会覆盖整个 cookie jar，
导致后续请求恒被拒（`400021` / `810010` / `110001`）。

设备请求都带 `Cookie: BDUSS=<值>;BDUSS_BFESS=<值>` 与 `HOUSE_ID` Cookie。

状态读取统一走 `appliance.stateSetting.<属性>.value`；面板开关的「开」判定为
`stateSetting[switchType].value == typeValue`。

## 已知限制

- **仅云控**：完全依赖小度云，HA 断网即失效，本地局域网不生效
- **到位有延迟**：窗帘、纱窗是蓝牙 mesh 设备，经小度智能屏代理转发，下发指令后要 15~30 秒才真正到位并回传状态。集成在 10s / 25s / 45s 各补一次刷新，但滑块位置短时间内仍会停在旧值，这是设备本身的延迟，不是没执行
- **窗帘定位走私有指令**：小度窗帘 / 纱窗的定位指令是 `TurnOnPercentRequest`（`SetPositionRequest` / `SetDegreeRequest` / `SetPercentageRequest` 都会被云端的白名单拒为 `21098 header name is not vaild`）。该指令名是从官方网页版拖动窗帘图标的真实请求里抓到的，属于未公开接口，未来可能变化
- **空调关机时拒绝调节**：多数小度空调在关机状态会拒绝温度与风速指令（`控制失败，请检查该设备的电源和网络是否正常`），请先开机
- **室温不可读**：部分空调设备的 `stateSetting` 里没有 `temperature` 字段，`current_temperature` 会一直是空值，这是设备不上报
- **不支持的设备**：某些厂商设备的小度技能是「只读」的，会出现实体但控制被云端拒绝（日志有 `控制失败 (xxx)` 提示）
- **风控验证**：账号密码登录时百度通常要求短信二次验证（`120021`）。验证通过后可自动换取 BDUSS；若该账号收不到短信，仍可改用 BDUSS Cookie 手动更新

## 实测验证

以下能力在 Home Assistant 2026.10.4（Python 3.14）真实环境验证通过：

| 能力 | 状态 |
|---|---|
| BDUSS Cookie 登录 | 可用 |
| 账号密码登录 | 可用（2026-10-31 实机跑通） |
| 短信验证 → 自动换取 BDUSS | 可用（登录 → 发码 → 填码 → BDUSS，无需浏览器） |
| 设备发现 / 实体生成 | 可用 |
| 批量状态轮询 | 可用 |
| 空调：开关 / 模式 / 调温 | 可用 |
| 窗帘 / 纱窗：开 / 关 | 可用（约 30 秒到位） |
| 窗帘 / 纱窗：位置反馈 | 可用（`degree`） |
| 窗帘 / 纱窗：设定位置 | 可用（`cover.set_cover_position`，约 20-30 秒到位） |
| `panels` 多功能设备拆分 | 未验证：代码已实现，测试环境设备无 `panels` |

## 目录结构

```
custom_components/xiaodu_reverse/
├── __init__.py          入口：建 session / API / 协调器，转发平台
├── const.py             常量与端点
├── login.py             登录签名（moonshadV3 / RSA / 请求构造）
├── relogin.py           BDUSS 自动续期：账密登录 → 短信验证 → loginproxy 换票
├── api.py               小度云控制 API（来自 hass_xiaodu）
├── coordinator.py       批量轮询 + 凭据自动续期
├── entity.py            实体基类与运行时取用
├── appliance_types.py   设备类型分类 + panels 解析
├── config_flow.py       配置流 / 选项流 / 重新认证
├── light.py switch.py cover.py climate.py fan.py
├── lock.py button.py sensor.py vacuum.py
├── strings.json  translations/{zh-Hans,en}.json
└── manifest.json
```

## 更新日志

完整说明见 [Releases](https://github.com/helloArmy/ha-xiaodu-reverse/releases)。

### v1.3.5（2026-10-31）· 实测支持 Home Assistant 2024.1.0

- **`ConfigEntry` 泛型降级** — `ConfigEntry[XiaoDuCoordinator]` 是 2024.6 才支持的写法，在 2024.1~2024.5 会抛 `TypeError: type 'ConfigEntry' is not subscriptable`，且发生在模块顶层，导致**整个集成导入失败、条目完全无法 setup**。这才是原先版本号写 `2024.6.0` 的真正原因。改为 `try/except` 探测后降级为非泛型
- **在真实 2024.1.0 容器（Python 3.11.6）上端到端实测** — 17/17 模块导入、条目 setup 成功、读到真实状态（窗帘 `degree=50 ON`）、控制 50%→30% 16 秒到位，无任何 Traceback；同时复测 HA 2026.1.3 全部通过
- **品牌图标换成小度官方图标** — 由此前的自制紫底字标改为小度官方 App 图标（红蓝气泡），并新增 `dark_icon.png` 深色主题版本

<details>
<summary>v1.3.4（2026-10-31）· 支持 Home Assistant 2024.1.0 及以上</summary>

- **最低版本定为 `2024.1.0`** — `hacs.json` 同步；HAOS / Supervised / Container / Core 四种安装形态均可用
- **吸尘器状态跨版本自适应** — HA 2025.1 起改用 `activity` 属性 + `VacuumActivity` 枚举，2024.x 只认 `state` 字符串。原来在模块级直接 `from ... import VacuumActivity`，在 2024.x 上会 ImportError 并连带整个条目 setup 失败。改为 `try/except` 探测后按版本挂载对应属性（两个属性语义互斥，只挂其一）
- **选项流程跨版本自适应** — `OptionsFlow.config_entry` 是 2024.11 才由框架注入的只读属性；更早版本拿不到。改为运行时探测该 property 是否存在，不存在时在 `__init__` 里自行注入
- **`ConfigEntry` 泛型降级** — `ConfigEntry[XiaoDuCoordinator]` 是 2024.6 才支持的写法，在 2024.1~2024.5 会 `TypeError: type 'ConfigEntry' is not subscriptable`，导致**整个集成导入失败**（这也是原先版本号写 2024.6.0 的真正原因）。改为 `try/except` 探测后降级为非泛型

</details>

<details>
<summary>v1.3.3（2026-10-31）· 修复验证控件 810010 / 110001</summary>

- **登录终于稳定了** — 现象是输完账号密码后，验证方式查询恒返回 `810010（系统繁忙）`、`data` 为空，发码则返回 `110001（参数错误）`。用 Chrome DevTools 在真实浏览器里跑一遍登录、逐字段对比请求后定位到三个原因：
  - **缺 `ppfuid` cookie**（浏览器侧生成的 32 位设备指纹 uid）。没有它，验证控件直接判「繁忙」。真实浏览器请求头里一直带着，我们从未发送过。补齐后 `code` 立刻从 `810010` 变成 `110000`，并带出脱敏手机号
  - **验证控件请求体少了 4 个字段**：`tpl` 应为 `pp`（不是 `mn`）、需要 `lang=zh_CN`、`staticPage=…/v3Jump.html`、`charset=UTF-8`，`authParams` 还要带 `nfczid`。少任何一个都会被拒
  - **响应不是 JSON 而是跳转页**：带上 `staticPage` 后服务端返回的是 `v3Jump.html` 的 HTML，里面用 `?code=…&data=<URL编码JSON>` 携带结果。新增解析器处理这种格式
- **不再预先调 `fetch_baiduid()`** — 留空交给客户端内部获取。内部那次首页请求会顺带带回 `BIDUPSID` / `PSTM` / `BD_HOME` / `BDSVRTM`，而 `fetch_baiduid()` 因为请求头不同拿不到，这几个 cookie 缺失同样会触发 `810010`
- **BAIDUID 输入框不再预填** — 标签写着「留空即可」却预填了旧值，容易让人误以为必填
- **发送验证码前就显示手机号** — 进页面即查询验证方式，显示「验证码将发送至177********」

</details>

<details>
<summary>v1.3.2（2026-10-31）· 窗帘支持设定百分比</summary>

- **窗帘 / 纱窗支持设定开合百分比** — HA 里现在有位置滑块，`cover.set_cover_position` 可用。关键是找对了指令名：`TurnOnPercentRequest`（`SetPositionRequest`/`SetDegreeRequest`/`SetPercentageRequest` 全被云端 `21098 header name is not vaild` 拒绝）。做法是打开小度 App 的窗帘详情页（本质是网页版 `xiaodu.baidu.com/saiya/smarthome`），通过 WebView 调试通道拖动窗帘图标，抓到真实请求：
  ```json
  {"header":{"namespace":"DuerOS.ConnectedHome.Control","name":"TurnOnPercentRequest","payloadVersion":3},
   "payload":{"applianceId":"…","parameters":{"attribute":"degree","attributeValue":"45","proxyConnectStatus":false},
              "appliance":{"applianceId":["…"]},"degree":"45"}}
  ```
  注意 `degree` 要同时出现在 `parameters.attributeValue` 和 `payload.degree`，且都是**字符串**；`degree` 属于可调属性，传裸值而不是 `{"value": 45}`
- **到位延迟按实测调整** — 补刷新从 8/20/35 秒改为 10/25/45 秒。实测 70% → 0% 往返接近 40 秒，原来的时间点会漏掉最终状态
- **验证码页显示真实手机号** — 进入「发送验证码」时若还没取到手机号，会再查一次验证方式接口；失败也不再静默，日志里会写明原因

</details>

<details>
<summary>v1.3.1（2026-10-31）· 账号密码登录全链路打通</summary>

- **账号密码登录全链路打通** — 现在整条登录都可以在服务端完成：账密登录 → 短信验证 → 换取 BDUSS。不需要浏览器，也不引入任何新增依赖。三个关键修复：
  - **会话 cookie 被自己顶掉**（决定性）：请求头里手写 `Cookie: BAIDUID=…` 会覆盖整个 cookie jar，把百度在 `120021` 时下发的 `UBI` / `logTraceID` / `pplogid` 全部丢掉，验证控件因此恒返回 `400021` / `810010` / `110001`。改为把 BAIDUID 注入 jar 后，`get` 当次即返回 `110000`
  - **`loginproxy` 是双层 URL 编码**：只解一层会得到 `%3Floginproxy%26…` 并 302 到 `error.html`，改为循环解码直到出现 `?loginproxy`
  - **签名耗时与设备指纹**：`elapsed` 改为 moonshad 签名循环的真实毫秒耗时（原来硬编码 `6`）；`rinfo` 在没有真实设备指纹时不再伪造（原来是随机 UUID 的 md5）
- **短信验证拆成两步** — 「发送验证码」与「输入验证码」分开，发码后 60 秒内不可重发；验证方式查询会显示真实脱敏手机号（如 `177********`）
- **移除浏览器方案** — 删除探测用的 Chromium 相关模块。`dv` 设备指纹与官方 `g.min.js` 输出逐字节一致（解码回环 2/2 通过）

</details>

<details>
<summary>v1.2.1（2026-10-31）· 修正 HACS 页面上的链接</summary>

- **修正 HACS 页面上的文档与问题反馈链接** — 此前指向占位地址 `https://github.com/`，点了没反应；现指向真实仓库

</details>

<details>
<summary>v1.2.0（2026-10-31）· 窗帘控制与延迟刷新</summary>

- **控制后补刷新** — 开 / 关 / 停后调度 8s、20s、35s 三次刷新。小度蓝牙 mesh 窗帘 / 纱窗（`botId=__ble_mesh__`，经小度智能屏代理）状态回传延迟约 30 秒，而默认轮询间隔 300 秒，原实现会让用户在 5 分钟内看到旧状态，像是没反应
- **移除 `SET_POSITION`** — `SetPositionRequest` 实测返回 `header name is not vaild`，设备不支持设定任意位置，暴露滑块会误导；`current_position` 保留为只读，仍随 `degree` 变化

</details>

<details>
<summary>v1.1.0（2026-10-31）· 修复空调温度控制</summary>

- **温度控制改为增减优先** — 实机诊断（海信爱家空调）发现 `SetTargetTemperatureRequest` 返回 `header name is not vaild`（该设备族无此指令），而 `IncrementTemperatureRequest` 指令有效、仅在关机时被设备拒绝。改为：增减逼近 → 直接设温兜底 → 仍失败抛异常
- **不再静默成功** — 原实现两种指令都失败后仍返回成功，界面上操作无反应也无报错；现在关机状态会明确提示「空调当前处于关机状态，请先开机」
- **部分完成算成功** — 避免把已经发生的温度变化谎报为失败

</details>

<details>
<summary>v1.0.0（2026-10-31）· 首个可用版本</summary>

- **登录** — 百度 wappass 账号密码登录（RSA + moonshadV3 风控签名 + 短信验证码）与 BDUSS Cookie 登录；凭据失效自动重新登录；批量轮询降低请求数
- **九类平台实体** — light / switch / cover / climate / fan / lock / button / sensor / vacuum，多功能设备按 `panels` 拆分为独立功能开关并原样透传厂商 payload
- **修复 HA 2026.10（Python 3.14）兼容** — `VacuumEntityFeature.BATTERY` 已被移除，类体硬引用导致 vacuum 平台 ImportError、整条 setup 失败；改用 `getattr` 容错构造
- **修复 `__repr__` 无限递归爆栈** — `XiaoDuEntity.state()` 覆盖了 HA 的 `Entity.state` property，`str(self.state)` 得到 bound method，其 repr 又引用实体，触发 `RecursionError: Stack overflow`，所有实体 `Error adding entity` 且恒为 unavailable；改名为 `attr_value()`
- **窗帘位置字段补充 `degree`** — 小度窗帘实际使用 `degree` 而非 `position`

</details>

## 免责声明

本项目通过非官方接口访问小度云，仅用于个人学习与自用。接口结构由百度控制，可能随时变更。
请勿用于商业用途，使用风险自负。

## 致谢

本集成参考了以下开源项目的实现思路：

- [RockJesus/ha-xiaodu](https://github.com/RockJesus/ha-xiaodu)
- [2331892928/hass_xiaodu](https://github.com/2331892928/hass_xiaodu)

## License

MIT
