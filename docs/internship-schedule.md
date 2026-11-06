# 实习活动树与专门时间审查

人设 SSOT：`data/persona_dossier.json#work.internship`。

`data/internship_tree.json` 分为案头/制片协助、本市外出、室外踩点、外景跟组，以及出差目的地分支。每类下有任务、候选地点、场景词和适用时段。白天轮换任务和地点；晚段只选案头工作，避免夜里随机做日照踩点。本市外出槽含60分钟往返接驳预算。候选地点不是场地预约或已经发生的经历。

出差目的地：天津（海河/五大道，一天短途），杭州（运河/钱塘江）、上海（杨浦/徐汇滨江）、青岛（老城/海滨），后三者至少三天。北京往返单程预算分别为3/6/6/6小时，均含接驳候车，是保守排程预算，不是车次信息。长途分支不自动挤进半天或普通周末，须先有连续空闲窗口和完整经审查行程。

当前落盘更新：保留2026-10-27及以前的记录；替换未来已有实习槽的任务/地点，原时间不变；2026-11-14原自动休闲安排定向改为天津海河取景一天，含往返各3小时、午休和返京休息。用户本次定向修改覆盖该日冻结，其他冻结及既有旅行保持原样。未来缺省生成最多每月第二个周六安排一次天津短途，考试/复习/假期不生成。

## 审查入口

配置：`config/schedule_review.json`，执行器：`src/schedule_travel_review.py`。

```
.venv/bin/python tools/review_internship_schedule.py --report data/devtest/internship_review.json
```

检查所有时段时间、重叠/缺口、城市连续性、独立交通槽及最短交通预算、最短行程天数、回京闭环、睡眠、课程考试冲突；缺字段或未知线路按错误返回。上午杭州、下午上海、晚上北京会被拒绝。新出差槽需要trip_id、city/location、phase；交通槽需要from_city/to_city，不能只改活动名糊过审查。

此审查已接入`reasonableness.score_schedule`和`ensure_month_days`写入前闸门；月更审查失败不再冻结。现有专门的 Mac mini 本地 Cursor review 继续负责第二遍审查，输入已从每天前四个活动名改为完整时段和地点，并附活动树及review配置；无需新建Bot审查员。Grok只作远端操作转发，联系「小甜发言模型」，不修改代码。自动review不越过已有冻结/用户约定。

## 素材

穿搭与参考场景新增`work-001`至`work-009`，源表在`tools/gen_outfit_scenes.py`，重建不会丢失。分别覆盖工作间、外出对接、室外踩点、外景跟组、出差交通及四地取景环境。图片回填使用既有`scene_refs`流程，每场景目标4张；缺图任务保留在`data/scene_refs_queue.json`，不伪称已有素材。图是环境参考，不是她实地拍摄的证据。

本地已目视筛选28张参考图（9类）；排除明显无关、前景大人物、产品白底、示意/拼图。审核清单含原始URL和SHA256：`data/internship_scene_refs_manifest.json`。部署时运行 `.venv/bin/python tools/sync_internship_refs.py` 只下载校验匹配的已审原图，源图变化则拒收。未达到4张的场景仍在补齐队列；不能把有队列等同已收齐。
