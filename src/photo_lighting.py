"""Offline solar-position estimate, from NOAA solareqns.PDF (no weather claim).
https://gml.noaa.gov/grad/solcalc/solareqns.PDF
Persona location comes from the recorded slot, never the user's location.
"""
import calendar
import datetime as dt
import math
from zoneinfo import ZoneInfo


def resolve(now, slot, cfg=None):
    if isinstance(now, str):
        now = dt.datetime.fromisoformat(now)
    now = now.replace(tzinfo=ZoneInfo('Asia/Shanghai')) if now.tzinfo is None else now.astimezone(ZoneInfo('Asia/Shanghai'))
    lat, lon = slot.get('lat'), slot.get('lon')
    city = slot.get('city')
    from photo_schedule import location
    place = location(slot) or ''
    if lat is None or lon is None:
        if city == '北京' or (not city and any(x in place for x in ('学校', '北舞', '宿舍', '食堂', '教室', '排练厅', '练功房'))):
            loc = ((cfg or {}).get('weather', {}).get('places', {}).get('bda') or {})
            lat, lon = loc.get('lat', 39.9459), loc.get('lon', 116.3130)
        else:
            return {'period':'unknown', 'source':'coordinates_missing', 'text':
                    f'光照约束：拍摄时间为{now.isoformat()}；地点坐标未确认，不指定窗外天空或直射日光，采用场所正常照明。'}
    lat, lon = float(lat), float(lon)
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError('invalid scheduled coordinates')
    rad = math.pi / 180
    hour = now.hour + now.minute / 60 + now.second / 3600
    gamma = 2 * math.pi / (366 if calendar.isleap(now.year) else 365) * (now.timetuple().tm_yday - 1 + (hour - 12) / 24)
    eq = 229.18 * (.000075 + .001868*math.cos(gamma) - .032077*math.sin(gamma) - .014615*math.cos(2*gamma) - .040849*math.sin(2*gamma))
    dec = (.006918 - .399912*math.cos(gamma) + .070257*math.sin(gamma) - .006758*math.cos(2*gamma) + .000907*math.sin(2*gamma) - .002697*math.cos(3*gamma) + .00148*math.sin(3*gamma))
    solar_minutes = hour * 60 + eq + 4 * lon - now.utcoffset().total_seconds() / 60
    angle = (solar_minutes / 4 - 180) * rad
    elevation = math.asin(max(-1, min(1, math.sin(lat*rad)*math.sin(dec) + math.cos(lat*rad)*math.cos(dec)*math.cos(angle)))) / rad
    period = 'day' if elevation >= -.833 else ('twilight' if elevation >= -6 else 'night')
    text = {'day':'白昼；允许自然光但不臆断晴天，室内服从场所照明。',
            'twilight':'晨昏弱光；禁止正午阳光或明亮午后天空，室内使用正常照明。',
            'night':'夜间；窗外和室外为夜色，禁止白天日光，室内使用场所灯光。'}[period]
    return {'period':period, 'source':'noaa_solar_estimate', 'lat':lat, 'lon':lon,
            'elevation':round(elevation,3), 'text':'光照硬约束：'+text+'参考图光照不得覆盖此约束。'}
