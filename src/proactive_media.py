"""Successful proactive rounds, not image requests or bubbles, determine the 60% floor.
Persona SSOT: data/persona_dossier.json. Sleep/rate limits always take precedence.
"""
import math


def counts(state, now):
    day = now.date().isoformat()
    total = int(state.get('proactive_count', 0)) if state.get('proactive_day') == day else 0
    routine = state.get('proactive_routine') or {}
    if routine.get('day') == day:
        total += int(routine.get('count', 0))
    media = state.get('proactive_media') or {}
    images = int(media.get('images', 0)) if media.get('day') == day else 0
    return total, images


def required(state, now, cfg, beat=None):
    from proactive_limiter import is_routine
    if is_routine(beat) or (beat or {}).get("id") == "idle_contact":
        return False
    ratio = min(1., max(0., float(cfg.get('image_share_min_ratio', 0))))
    total, images = counts(state or {}, now)
    return ratio > 0 and images < math.ceil((total + 1) * ratio - 1e-9)


def photo_slot(beat, now):
    slot = dict(beat.get('ended') or beat.get('slot') or {})
    from schedule_execution import blocked
    if blocked(slot):
        return None
    if beat.get('ended') and str(slot.get('outing_activity_id', '')).startswith('movie_'):
        if now.strftime("%H:%M") < str(slot.get("end") or "24:00"):
            return None
        # Once the film has ended, a lobby photo is plausible; never photograph the screen.
        slot.update(activity='电影散场后在大厅歇脚', photo_ok=True)
    if not slot or slot.get('bucket') == 'sleep' or slot.get('photo_ok') is False:
        return None
    if slot.get('leg') in ('outbound', 'return'):
        return None
    return slot


def prepare(beat, state, now, cfg):
    """Attach a real schedule photo plan when the next round needs an image."""
    beat = dict(beat)
    from proactive_limiter import is_routine
    if is_routine(beat):
        beat.pop('image_required', None)
        return beat
    if not required(state, now, cfg, beat) and beat.get("id") != "life_photo":
        return beat
    beat['image_required'] = True
    if (beat.get('material') or {}).get('image_path'):
        return beat
    slot = photo_slot(beat, now)
    if slot is None:
        return None
    import schedule_shot
    beat['image'] = schedule_shot.image_hint_for_beat(beat['id'], slot, beat.get('today'))
    beat['image_slot'] = slot
    return beat


def receipt_patch(state, item, now):
    day = now.date().isoformat()
    old = state.get('proactive_media') or {}
    if str(old.get('day') or '') > day:
        return {}
    images = int(old.get('images', 0)) if old.get('day') == day else 0
    # Sender sets has_image only after a local file exists. This runs only on
    # complete transport success, including the primary outbox retry receipt.
    images += bool((item.get('meta') or {}).get('has_image'))
    return {'proactive_media': {'day': day, 'images': images}}
