"""Recorded calendar contract for generation and delivery. Persona: data/persona_dossier.json."""
import datetime as dt
import hashlib
import json


class SchedulePhotoError(ValueError):
    pass


def location(slot):
    value = str(slot.get('location') or '').strip()
    if value in ('未知', '待定', '未确定', '不详'):
        return None
    if value:
        return value
    # Only explicit venue words in recorded activity/hints; eating is not a venue.
    import re
    evidence = ' '.join([str(slot.get('activity') or ''), *map(str, slot.get('scene_hints') or [])])
    found = set(re.findall(r'食堂|宿舍|图书馆|教室|排练厅|练功房|公司工作间', evidence))
    return next(iter(found)) if len(found) == 1 else None


def signature(slot):
    keys = ('start', 'end', 'activity', 'location', 'scene_hints', 'scene_id',
            'photo_ok', 'bucket', 'execution_status', 'leg', 'city', 'lat', 'lon')
    return {k: slot[k] for k in keys if k in slot}


def local_time(value):
    from zoneinfo import ZoneInfo
    if isinstance(value, str):
        value = dt.datetime.fromisoformat(value)
    if value.tzinfo is not None:
        value = value.astimezone(ZoneInfo('Asia/Shanghai')).replace(tzinfo=None)
    return value


def check(snapshot, now=None):
    """Recheck recorded facts after generation / before delivery; never invent a day."""
    import life_calendar as lc
    from schedule_execution import blocked
    now = local_time(now or lc.now_local())
    day = dt.date.fromisoformat(snapshot['date'])
    doc = lc.recorded_day(day)
    matches = [s for s in (doc or {}).get('slots', []) if signature(s) == snapshot['slot']]
    if len(matches) != 1 or blocked(matches[0]) or matches[0].get('photo_ok') is False:
        raise SchedulePhotoError('recorded photo slot changed or unavailable')
    depicted = local_time(snapshot['depiction_time'])
    if (depicted.date() != day or depicted > now
            or not matches[0]['start'] <= depicted.strftime('%H:%M') < matches[0]['end']):
        raise SchedulePhotoError('invalid recorded photo timestamp')
    if not location(matches[0]):
        raise SchedulePhotoError('recorded photo location missing')
    if not snapshot['historical']:
        if day != now.date() or not matches[0]['start'] <= now.strftime('%H:%M') < matches[0]['end']:
            raise SchedulePhotoError('current photo slot expired')
    return matches[0]


def bind(scene, context=None, *, now=None):
    import life_calendar as lc
    import schedule_fidelity as sf
    ctx = dict(context or {})
    wall = local_time(now or lc.now_local())
    if ctx.get('imagined'):
        raise SchedulePhotoError('imagined photos have no recorded schedule')
    previous = ctx.get('photo_schedule')
    if previous:
        slot = check(previous, wall)
        depicted = local_time(previous['depiction_time'])
        date, historical = previous['date'], previous['historical']
    else:
        verified, reason = sf.resolve_photo_schedule(ctx.get('user_text') or '', scene, {}, wall)
        if reason:
            raise SchedulePhotoError(reason)
        slot, depicted = verified['slot'], verified['now']
        date, historical = verified['date'], verified['historical']
        supplied = ctx.get('slot')
        if supplied and any(supplied.get(k) != slot.get(k) for k in
                            ('start', 'end', 'activity', 'location')):
            raise SchedulePhotoError('supplied slot conflicts with recorded calendar')
        if ctx.get('date') and ctx['date'] != date:
            raise SchedulePhotoError('supplied date conflicts with photo request')
    place = location(slot)
    if not place:
        raise SchedulePhotoError('recorded photo location missing or ambiguous')
    if sf.image_request_block_reason('', scene, {**slot, 'date':date}, depicted):
        raise SchedulePhotoError('scene conflicts with recorded calendar')
    snap = dict(date=date, historical=historical, depiction_time=depicted.isoformat(), slot=signature(slot))
    doc = lc.recorded_day(dt.date.fromisoformat(date))
    snap['day_sha256'] = hashlib.sha256(json.dumps(doc, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    # Confirm the selected slot still exists, including execution status.
    check(snap, wall)
    ctx.update(slot={**slot, 'date':date}, today={'date':date,'slots':[slot]}, date=date,
               now=depicted, photo_schedule=snap, activity=f"{place} {slot['activity']}")
    return ctx


def prompt(snapshot):
    slot = snapshot['slot']
    return (f"日程硬约束：拍摄时间 {snapshot['depiction_time']}（Asia/Shanghai）；"
            f"地点 {location(slot)}；活动 {slot['activity']}。所有图片均服从此时间地点，"
            "参考图只辅助外观和空间细节，不得覆盖日程；不支持的地点、室内外环境不得补造。")


def check_file(path, now=None):
    """Generated artifacts carry their schedule to retries and direct sends."""
    from pathlib import Path
    value = str(path)
    if value.startswith(('http://', 'https://', 'base64://')):
        return
    target = Path(value.removeprefix('file://')).expanduser()
    meta = target.parent / 'run.json'
    if not meta.is_file():
        return  # Ordinary collected/shared media is not a generated life photo.
    record = json.loads(meta.read_text())
    if not record.get('photo_schedule'):
        raise SchedulePhotoError('generated photo has no recorded schedule evidence')
    check(record['photo_schedule'], now)
