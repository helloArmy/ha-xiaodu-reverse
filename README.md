# 小度反向接入 · XiaoDu Reverse for Home Assistant

[![Release](https://img.shields.io/github/v/release/helloArmy/ha-xiaodu-reverse?label=release)](https://github.com/helloArmy/ha-xiaodu-reverse/releases)
[![HACS](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/helloArmy/ha-xiaodu-reverse)
[![HA](https://img.shields.io/badge/Home%20Assistant-2024.6%2B-18BCEE.svg)](https://www.home-assistant.io/)

把**小度（百度 DuerOS）智能家居 App 里已有的设备**反向接入 Home Assistant —— 你可以继续在小度 App 里管理设备，同时在 HA 里做自动化、仪表盘和语音联动。

> **「反向」的含义**：通常是小度去控制 HA 里的设备；这个集成走反方向 —— 从百度小度云把小度生态下的设备「反向拉回」HA。

## 设计来源

本集成的两个关键能力分别取自两个开源项目，取长补短：

| 能力 | 来源 | 说明 |
|---|---|---|
| **登录方式** | [RockJesus/ha-xiaodu](https://github.com/RockJesus/ha-xiaodu) | 完整的百度 wappass 账号密码登录：RSA 加密 + moonshadV3 风控签名 + 短信验证码 + 凭据自动续期 |
| **控制方式** | [2331892928/hass_xiaodu](https://github.com/2331892928/hass_xiaodu) | 小度云 `directivesend` 指令通道，以及 **panels 面板驱动**的多功能设备拆分（晾衣架、空调扇等），可原样透传厂商 payload |

两者底层是同一套小度云 API（`DuerOS.ConnectedHome.Control`），差异主要在指令覆盖度与面板能力，本项目做了合并：

- 保留 hass_xiaodu 的面板控制签名语义（`switchType` / `typeValue` / `headerNameOn` / `headerNameOff` / `payloadObject`）；
- 补上 ha-xiaodu 的「直接设定温度 / 风速 / 摆风」指令（失败时自动回退到「加一度 / 减一度」逼近）；
- 用 ha-xiaodu 的 `LoginExpired` 异常体系，让 Cookie 失效能触发自动重登而不是静默失败。

## 功能

- **两种登录**：百度账号密码（推荐，可自动续期）／直接粘贴 BDUSS Cookie
- **短信验证**：命中百度风控（400023）时自动下发验证码并引导输入
- **多家庭 / 多设备**：选择家庭后多选要接入的设备
- **全平台实体**：`light` `switch` `cover` `climate` `fan` `lock` `button` `sensor` `vacuum`
- **面板拆分**：一台多功能设备按 `panels.功能控制` 拆成多个开关实体，每个功能独立可控、独立回读状态
- **传感器**：温度、湿度、PM2.5、CO₂、滤芯寿命、电量等按 `stateSetting` 自动生成
- **凭据自愈**：轮询发现 `not login` 时用保存的账号密码自动重登，失败才要求重新认证
- **批量轮询**：优先用 `GET_APPLIANCES_BY_ID` 一次拉全部设备状态，缺数据再逐台回退

## 安装

### 方式一：手动（推荐，最快）

把 `custom_components/xiaodu_reverse` 整个目录复制到 HA 配置目录：

```
<HA 配置目录>/custom_components/xiaodu_reverse/
```

然后**重启 Home Assistant**。

### 方式二：HACS 自定义仓库

1. HACS → 集成 → 右上角 ⋮ → 自定义存储库
2. 填入本仓库地址，类别选「集成」
3. 搜索「小度反向接入」安装，重启 HA

## 配置

1. **设置 → 设备与服务 → 添加集成 → 搜索「小度反向接入」**
2. 选择登录方式：
   - **账号密码**：填小度 App 绑定的百度账号与密码
   - 建议同时填 **BAIDUID**：浏览器打开 <https://xiaodu.baidu.com/saiya/smarthome/index.html> 登录后，从开发者工具 → Application → Cookies 复制 `BAIDUID`（形如 `XXXX...:FG=1`）。**填了能显著降低被风控要求短信验证的概率**
3. 如提示短信验证，输入收到的验证码
4. 选择家庭 → 多选设备 → 完成

### 关于 BDUSS Cookie

只有账号密码登录被风控死锁时才需要：

1. 浏览器打开 <https://xiaodu.baidu.com/saiya/smarthome/index.html> 并登录
2. 开发者工具 → Application → Cookies → 找到 `BDUSS`，复制其值
3. 在集成里选「直接填写 BDUSS Cookie」粘贴

Cookie 有效期通常数月，失效后集成会提示重新认证。

## 选项

集成条目 → 配置，可进行：

- **更新 BDUSS Cookie**
- **使用账号密码重新登录**（推荐，一条命令刷新凭据）
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
HA 集成  ──登录──▶  wappass.baidu.com     （账号密码 → BDUSS）
         ──轮询──▶  /saiya/smarthome/appliance          设备列表
                    /saiya/smarthome/appliancedetails   单设备详情 + stateSetting + panels
         ──控制──▶  /saiya/smarthome/directivesend      DuerOS.ConnectedHome.Control 指令
```

所有请求都带 `Cookie: BDUSS=<值>;BDUSS_BFESS=<值>` 与 `HOUSE_ID` Cookie。

状态读取统一走 `appliance.stateSetting.<属性>.value`；面板开关的「开」判定为
`stateSetting[switchType].value == typeValue`。

## 已知限制

- **仅云控**：完全依赖小度云，HA 断网即失效，本地局域网不生效
- **轮询延迟**：状态最长滞后一个轮询周期（默认 300 秒）。窗帘、纱窗这类蓝牙 mesh 设备本身状态回传就要约 30 秒，控制后请稍等再判断结果
- **窗帘不支持定位**：实测小度窗帘 / 纱窗只接受开 / 关 / 停，`SetPositionRequest` 返回 `header name is not vaild`，因此不提供位置滑块，`current_position` 仅作只读显示
- **空调关机时拒绝调节**：多数小度空调在关机状态会拒绝温度与风速指令（`控制失败，请检查该设备的电源和网络是否正常`），请先开机
- **室温不可读**：部分空调设备的 `stateSetting` 里没有 `temperature` 字段，`current_temperature` 会一直是空值，这是设备不上报
- **不支持的设备**：某些厂商设备的小度技能是「只读」的，会出现实体但控制被云端拒绝（日志有 `控制失败 (xxx)` 提示）
- **风险控制**：新设备上使用账号密码登录，百度可能要求二次验证（`400023`）。若该账号拿不到短信验证入口，请改用 BDUSS Cookie 登录——此时无法自动续期

## 实测验证

以下能力在 Home Assistant 2026.10.4（Python 3.14）真实环境验证通过：

| 能力 | 状态 |
|---|---|
| BDUSS Cookie 登录 | ✅ |
| 账号密码登录 | ⚠️ 取决于百度风控，新设备可能触发 400023 |
| 设备发现 / 实体生成 | ✅ |
| 批量状态轮询 | ✅ |
| 空调：开关 / 模式 / 调温 | ✅ |
| 窗帘 / 纱窗：开 / 关 | ✅（约 30 秒到位） |
| 窗帘 / 纱窗：位置反馈 | ✅（只读，`degree`） |
| 窗帘 / 纱窗：设定位置 | ❌ 设备不支持 |
| `panels` 多功能设备拆分 | ⚠️ 代码已实现，测试环境设备无 `panels`，未实机验证 |

## 目录结构

```
custom_components/xiaodu_reverse/
├── __init__.py          入口：建 session / API / 协调器，转发平台
├── const.py             常量与端点
├── login.py             百度 wappass 登录（来自 ha-xiaodu）
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

### v1.2.0 — 窗帘控制与延迟刷新

实测小度蓝牙 mesh 窗帘 / 纱窗（`botId=__ble_mesh__`，经小度智能屏代理）后调整：

- **控制后补刷新**：开 / 关 / 停后调度 8s、20s、35s 三次刷新。这类设备状态回传延迟约 30 秒，而默认轮询间隔 300 秒，原实现会让用户在 5 分钟内看到旧状态，像是没反应。
- **移除 `SET_POSITION`**：`SetPositionRequest` 实测返回 `header name is not vaild`，设备不支持设定任意位置，暴露滑块会误导。`current_position` 保留为只读，仍随 `degree` 变化。
- 清理未使用导入。

### v1.1.0 — 修复空调温度控制

实机诊断（海信爱家空调）得到两个与常见假设相反的结论：

| 指令 | 云端返回 | 结论 |
|---|---|---|
| `SetTargetTemperatureRequest` | `header name is not vaild` | 该设备族不存在此指令 |
| `IncrementTemperatureRequest` | `控制失败，请检查该设备的电源和网络是否正常` | 指令有效，关机时设备拒绝 |

原实现先试无效指令、失败后走增减、再失败**静默返回成功**，用户在界面上操作无反应也无报错。改为：

1. 优先用增减指令逼近目标温度
2. 失败再退到直接设温（部分设备支持该指令）
3. 两者皆失败抛 `HomeAssistantError`；空调关机时提示先开机
4. 部分完成算成功，避免把已发生的温度变化谎报为失败

### v1.0.0 — 首个可用版本

- 百度 wappass 账号密码登录（RSA + moonshadV3 风控签名 + 短信验证码）与 BDUSS Cookie 登录
- 凭据失效自动重登、批量轮询、九类平台实体、`panels` 多功能设备拆分
- 修复 HA 2026.10（Python 3.14）下 `VacuumEntityFeature.BATTERY` 被移除导致的平台导入失败
- 修复 `XiaoDuEntity.state()` 覆盖 `Entity.state` property 导致的 `__repr__` 无限递归爆栈
- 窗帘位置字段补充 `degree`

## 免责声明

本项目通过非官方接口访问小度云，仅用于个人学习与自用。接口结构由百度控制，可能随时变更。
请勿用于商业用途，使用风险自负。

## 致谢

- [RockJesus/ha-xiaodu](https://github.com/RockJesus/ha-xiaodu) —— 登录协议与风控签名实现
- [2331892928/hass_xiaodu](https://github.com/2331892928/hass_xiaodu) —— 控制通道与面板驱动思路

## License

MIT
