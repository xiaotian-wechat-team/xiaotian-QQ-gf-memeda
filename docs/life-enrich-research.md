# 人设日程丰富调研（小红书 + 抖音）

日期：2026-10-24（Asia/Shanghai）  
范围：丰富 `life_schedule` 城市娱乐 + 寒假少宅多旅；不涉及账号/cookie。

## 方法
- 平台：Chrome 已登录标签页，经 `src/collectors` DOM 只读采集（`--force` 手动调研，无 OCR）
- Topic：寒假雪山、新疆禾木、北京周末探店、三亚寒假、城市娱乐剧本杀/Livehouse、崇礼滑雪
- 场景库：`data/outfit_scenes.json`（579）娱乐/旅游/景点/约会类名称作活动灵感
- 产出：`data/collect_runs/life_enrich_research.json`、日程草稿、`life_calendar` 池

## 调研主题摘要（标题级，非背书）
- **雪山/滑雪**：国内入门雪山、梅里日照金山、崇礼太舞/富龙/云顶/翠云山寒假滑雪攻略热度高
- **新疆禾木**：冬日禾木/阿勒泰雪村、自驾与木屋雪景内容集中
- **三亚寒假**：海棠湾、天涯/镇海海岸、女大寒假海边 vlog 常见
- **北京日常**：citywalk（东四/三里屯等）、文创杂货、古堡剧本杀/密室、Livehouse、大学生「100 件小事」娱乐清单

## 如何加权进日程
- 窗内寒假（2027-01-19～01-24）：崇礼滑雪 → 新疆禾木 → 三亚暖冬特种兵弧线；`weekend_home` 刷剧去掉
- 平时晚上：从加宽 `WEEKDAY_EVE_POOL`（剧本杀/脱口秀/798/citywalk/猫咖等）抽样，降低散步重复
- 窗外生成：`WINTER_TRAVEL_POOL` + material_store 对 XHS/Douyin 检索 hints；寒假宅家概率约 8%

## 复用
见 skill `.cursor/skills/xiaotian-life-enrich/SKILL.md` 与 `tools/enrich_life_from_research.py`。


## 合理度（2026-10-24 续）
活动池见 `data/activity_pool.json`；闸门见 `docs/reasonableness.md`。
寒假落盘弧：崇礼 4 天 → 交通 → 禾木 7 天 → 交通 → 三亚 12 天（日程窗延至 2027-02-12）。
出国短途已进 `WINTER_TRAVEL_POOL`（东京/首尔/清迈/巴厘/北海道/新加坡），写入前须过合理度。
