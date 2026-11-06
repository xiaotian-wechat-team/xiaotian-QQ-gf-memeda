# 小甜学业日历硬约束（北京舞蹈学院）

人设：北京舞蹈学院创意学院编导系（舞蹈编导）大三；**不是**北京大学。燕园仅为用户侧天气默认城市。

## 来源

1. [北京舞蹈学院 · 学校校历](https://office.bda.edu.cn/xwgk/xxxl/) — 《北京舞蹈学院校历（2025-2026…）》2025-10-20 挂网；本环境抓取正文被 WAF/挑战页拦截。
2. [京教办〔2026〕4号](https://jw.beijing.gov.cn/xxgk/2024zcwj/2024qtwj/202604/t20260417_4588623.html) 北京市教委 2026—2027 学年度校历 — **非义务教育**：寒假 **2027-01-25～02-21**，春季开学 **2027-02-22**（普通高校可自定；本表寒假旅弧自考完 **01-19** 对齐至 **02-21**）。
3. [京教办〔2025〕14号](https://jw.beijing.gov.cn/xxgk/2024zcwj/2024qtwj/202507/t20250704_4141331.html) — 对照学年结构。
4. 人设课表（`AGENTS.md`）：编导技法 / 导演基础 / 中外舞蹈史 / 剧目 — 大三考试偏少，只插轻量几门。

## 落盘

| 字段 | 位置 |
|------|------|
| 日行程 | `data/life_schedule_2026q4.json` |
| 考试+复习硬块 | `data/study_blocks.json` |
| 寒假 kind | `src/life_calendar.py::_winter_kind` |
| 拒出门 | `src/schedule_pipeline.py::study_priority_block` |

## 拒绝强度

- 考试日 / 近考（`refuse_strength ≥ 0.7`）：`blocks_override=true`，用户出门后门也拒。
- 复习窗远端（约 2–4 周前段，strength 更低）：仍是拒绝**理由**，但边界模糊，`user_override_still_wins=true`，更容易答应。
