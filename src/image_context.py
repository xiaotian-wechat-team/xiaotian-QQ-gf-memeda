"""Resolve depiction time once, independently of the time a job finishes."""
import datetime as dt
import re
from zoneinfo import ZoneInfo

TZ = ZoneInfo('Asia/Shanghai')


def resolve(scene, context=None):
    ctx = dict(context or {})
    now = ctx.get('now')
    if isinstance(now, str):
        now = dt.datetime.fromisoformat(now)
    now = now or dt.datetime.now(TZ)
    if now.tzinfo:
        now = now.astimezone(TZ).replace(tzinfo=None)
    slot = ctx.get('slot') or {}
    today = ctx.get('today') or {}
    date = ctx.get('date') or slot.get('date') or (today.get('date') if isinstance(today, dict) else today)
    day = dt.date.fromisoformat(str(date)) if date else now.date()
    source = 'clock'
    if ctx.get('photo_schedule'):
        stamp = dt.datetime.fromisoformat(ctx['photo_schedule']['depiction_time'])
        if stamp.date().isoformat() != ctx['photo_schedule']['date']:
            raise ValueError('photo date mismatch')
        ctx.update(now=stamp, date=stamp.date().isoformat(), depiction_time_source='recorded_schedule')
        return ctx
    if not ctx.get('imagined') and slot:
        if 'activity' not in ctx:
            ctx['activity'] = ' '.join(str(slot.get(k) or '') for k in ('activity', 'location'))
        start, end = slot.get('start'), slot.get('end')
        if start:
            def minutes(value):
                h, m = map(int, value.split(':'))
                if not (0 <= h <= 24 and 0 <= m < 60 and (h < 24 or m == 0)):
                    raise ValueError('invalid schedule time')
                return h * 60 + m
            a = minutes(start)
            b = minutes(end) if end else a + 1
            if b <= a:
                b += 1440
            current = now.hour * 60 + now.minute
            if day == now.date() and not a <= current < b and not ctx.get('historical'):
                raise ValueError('depiction time outside supplied schedule slot')
            if day != now.date() or not a <= current < b:
                now = dt.datetime.combine(day, dt.time()) + dt.timedelta(minutes=a)
            source = 'schedule_slot'
    if source == 'clock':
        # Explicit depicted time (including a past lunch) is not render wall time.
        match = re.search(r'(?<!\d)([01]?\d|2[0-3]):([0-5]\d)(?!\d)', scene or '')
        if match:
            now = dt.datetime.combine(day, dt.time(int(match[1]), int(match[2])))
            source = 'scene_time'
        else:
            periods = [(r'凌晨|午夜|深夜', 1), (r'夜晚|晚上|夜景|星空|月光|night', 21),
                       (r'午饭|午餐|中午|正午', 12), (r'早饭|早餐|早上|早晨|清晨', 8),
                       (r'下午|午后', 15), (r'白天|日光|阳光|daytime|daylight', 12)]
            for pattern, hour in periods:
                if re.search(pattern, scene or '', re.I):
                    now = dt.datetime.combine(day, dt.time(hour))
                    source = 'scene_period'
                    break
    ctx.update(now=now, date=day.isoformat(), depiction_time_source=source)
    return ctx


# RPC data only. No identity, config, host paths or executable fields.
CONTEXT_KEYS = {'slot', 'date', 'now', 'imagined', 'shot_type', 'shot_types', 'n_images'}
SLOT_KEYS = {'activity', 'location', 'start', 'end', 'date', 'bucket', 'scene_hints', 'scene_id'}


def wire_context(ctx):
    result = {k: v for k, v in ctx.items() if k in CONTEXT_KEYS and v is not None}
    if isinstance(result.get('now'), dt.datetime):
        result['now'] = result['now'].isoformat()
    if isinstance(result.get('slot'), dict):
        result['slot'] = {k: v for k, v in result['slot'].items() if k in SLOT_KEYS and v is not None}
    validate_context(result)
    return result


def validate_context(ctx):
    if not isinstance(ctx, dict) or set(ctx) - CONTEXT_KEYS:
        raise ValueError('unexpected image context fields')
    for key in ('date', 'now', 'shot_type'):
        if key in ctx and (not isinstance(ctx[key], str) or not 1 <= len(ctx[key]) <= 80):
            raise ValueError('invalid image context ' + key)
    if 'date' in ctx:
        dt.date.fromisoformat(ctx['date'])
    if 'now' in ctx:
        dt.datetime.fromisoformat(ctx['now'])
    if 'imagined' in ctx and type(ctx['imagined']) is not bool:
        raise ValueError('invalid imagined flag')
    n = ctx.get('n_images', 1)
    if type(n) is not int or not 1 <= n <= 5:
        raise ValueError('invalid image count')
    from shot_modes import normalize_shot
    if 'shot_type' in ctx and not normalize_shot(ctx['shot_type']):
        raise ValueError('invalid shot type')
    if 'shot_types' in ctx:
        shots = ctx['shot_types']
        if not isinstance(shots, list) or len(shots) != n or any(not isinstance(s, str) or not normalize_shot(s) for s in shots):
            raise ValueError('invalid shot types')
    if 'slot' in ctx:
        slot = ctx['slot']
        if not isinstance(slot, dict) or set(slot) - SLOT_KEYS:
            raise ValueError('invalid image slot')
        for key, value in slot.items():
            if key == 'scene_hints':
                if not isinstance(value, list) or len(value) > 20 or any(not isinstance(s, str) or len(s) > 200 for s in value):
                    raise ValueError('invalid scene hints')
            elif not isinstance(value, str) or len(value) > 500:
                raise ValueError('invalid slot field')
        if 'date' in slot:
            dt.date.fromisoformat(slot['date'])
        for key in ('start', 'end'):
            if key in slot and not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d|24:00', slot[key]):
                raise ValueError('invalid slot time')
