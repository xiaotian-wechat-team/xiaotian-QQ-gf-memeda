"""Resolve unconfirmed/cancelled plans before they can become claims or images.
Persona SSOT: data/persona_dossier.json. Pure function, no network or state writes.
"""
import copy


def blocked(slot):
    return (slot or {}).get('execution_status') in ('pending', 'cancelled')


def beat_blocked(beat, now, state=None):
    """Recheck queued snapshots against the current day's authoritative plan."""
    slots = [(beat or {}).get(k) for k in ('slot', 'ended', 'image_slot')]
    slots = [s for s in slots if isinstance(s, dict)
             and not (s.get('original_plan_status') in ('pending','cancelled')
                      and s.get('bucket')=='weekend_home')]
    if any(blocked(s) for s in slots):
        return True
    if not slots:
        return False
    import life_calendar
    day = life_calendar.get_day(now.date())
    if not day:
        day = (state or {}).get('life_today') or {}
    if day.get('date') != now.date().isoformat():
        return False
    for old in slots:
        for live in day.get('slots', []):
            same = (old.get('outing_id') and old.get('outing_id') == live.get('outing_id'))
            same = same or (old.get('start') == live.get('start') and old.get('end') == live.get('end'))
            if same and blocked(live):
                return True
    return False


def effective(slot):
    if not slot or not blocked(slot):
        return slot
    # Retain time bounds so freshness/sleep checks still work, but remove the
    # original venue, props and scene identity from the actual activity context.
    s = {k:copy.deepcopy(v) for k,v in slot.items() if k in
         ('date','kind','start','end','delay','energy')}
    s.update(activity='宿舍休息，原外出计划尚未落实', bucket='weekend_home',
             location='学校宿舍', city='北京', tone='轻松自然', photo_ok=True,
             scene_hints=['宿舍','休息'], original_plan_status=slot.get('execution_status'))
    return s
