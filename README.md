# CROWD — AI硬件链挤压强度监视看板

> **本看板为人眼研究监视器（human-eye research monitor），定位对标SR3看板。**
> 不产生任何机械信号，不接入 ABCD v3.5 / ABCDS v0.5 的任何状态机、信号管线或仓位模块。
> 全部输出仅供人工判断AI硬件链内短线切换参考。
> 禁止未来任何版本在未经独立审计的情况下将本看板数据接入主框架（防 "integrated but never declared" 风险）。

## 看板回答三个问题

1. **钱现在在链内怎么流**（日频，自动）→ 面板一
2. **真值锚最近一次说了什么**（季度，手动）→ 面板三（Phase 2）
3. **下一个能改变判断的信息节点是哪天**（日历，手动）→ 面板四

## 面板说明

### 面板一：链内相对强度矩阵（自动，日频）

- **核心剪刀差**：`MEM/SOXX − OPT/SOXX`——存储vs光模块的日频影子，挤出效应叙事强度计。
- 四篮子比值曲线 vs SOXX（归一化至1.0）+ 20d/60d动量 + z-score。
- z-score ≥ 2 仅视觉高亮，不附带操作建议——高亮只表示"值得人眼注意"。

### 面板四：信息节点日历 + ICS订阅（手动）

- `data/calendar.json` 是唯一真值来源。
- 每个事件必须回答"该节点验证什么假设"——不允许只写事件名。
- 每日 Actions 自动生成 `crowd_calendar.ics`，Google日历通过URL订阅（零凭证）。

### 面板二（Phase 2）：A股-美股时差面板

### 面板三（Phase 2）：TrendForce vintage 表

## 维护手册

### 面板四：日历维护

IR/交易所公布或变更日期 → 修改 `data/calendar.json`：
- 确认日期时将 `date_confirmed` 翻 `true`
- 下次 Actions 运行自动重生成 ICS
- Google 日历约一天内自动同步
- 一季度人工维护成本约十几分钟

### 面板三：TrendForce 录入（Phase 2）

录入时机为 TrendForce press center 每次发布后人工读原文录入。一季度预计 4–8 条记录。**不做爬虫。**

## ICS 日历订阅架构（§7.5）

> calendar.json 是信息节点的唯一真值来源，ICS 及 Google 日历均为其只读投影。
> 禁止在 Google 日历侧直接编辑事件后反向同步。
> 日期变更一律修改 calendar.json，由构建流程重新生成 ICS。
> 禁止改为 Google Calendar API 直写（需OAuth凭证管理，违反零凭证原则）。

**UID 幂等规则**：ICS 中每个事件 UID = `sha1(name)@crowd-monitor`。同一事件改期后 UID 不变，订阅端更新为新日期且不产生重复事件。hash 仅基于 name，不基于 date——改期不改变事件身份。

## 部署

- GitHub Pages：`site/` 目录
- CI：`.github/workflows/daily.yml`（UTC 22:30 工作日）
- validate 失败 → 阻断 commit → 页面保持上次成功状态 + STALE 横幅

## 验收状态

**当前：验收中（2026-07-05 建成，预计 2026-07-11 完成 5 日 soak）**

| 验收项 | 状态 | 说明 |
|---|---|---|
| #1 面板一+四渲染 | ✅ 代码完成 | 待首次 fetch 后实测 |
| #2 N1/N3/N4/N5/N6/N7/N8/N9 | ✅ 代码完成 | 待数据跑通后逐条确认 |
| #3 定位声明 | ✅ | index.html + README 双份 |
| #4 连续5日 Actions 无干预 | ⏳ 验收中 | 最早 2026-07-11 满足 |
| #5 维护手册 | ✅ | README §维护手册 |
| #6 ICS 订阅端实测 | ⚠️ 单元通过 | UID幂等=✅；Google端轮询验证待做 |

**待验证项**：
- ICS 订阅端实测：Google日历通过URL添加 → 等一个轮询周期 → 确认事件出现且 hypothesis 完整 → 改期一个事件 → 等下次 Actions → 确认更新且不重复
- 5日 soak 完成后从"验收中"改为"已验收"
- Panel 3 数据上线后补 N2 类型校验

## 版本

v0.1.1（2026-07-04）· CROWD_v0.1_spec.md
