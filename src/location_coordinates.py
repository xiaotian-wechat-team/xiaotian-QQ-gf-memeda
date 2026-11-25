"""Public map coordinates -> WGS84 for the cartoon map (approximate, not GPS).

BD09 Mercator polynomial coefficients are also documented by Maptalks:
https://github.com/maptalks/maptalks.js/blob/master/src/geo/projection/Projection.Baidu.ts
Coordinate systems: https://lbsyun.baidu.com/docs/jsapi?title=jspopularGL/guide/coorinfo
No coordinates are produced by the language model. Persona SSOT: data/persona_dossier.json.
"""
import math

_BANDS = (12890594.86, 8362377.87, 5591021, 3481989.83, 1678043.12, 0)
_MC2LL = (
    (1.410526172116255e-8, .00000898305509648872, -1.9939833816331, 200.9824383106796, -187.2403703815547, 91.6087516669843, -23.38765649603339, 2.57121317296198, -.03801003308653, 17337981.2),
    (-7.435856389565537e-9, .000008983055097726239, -.78625201886289, 96.32687599759846, -1.85204757529826, -59.36935905485877, 47.40033549296737, -16.50741931063887, 2.28786674699375, 10260144.86),
    (-3.030883460898826e-8, .00000898305509983578, .30071316287616, 59.74293618442277, 7.357984074871, -25.38371002664745, 13.45380521110908, -3.29883767235584, .32710905363475, 6856817.37),
    (-1.981981304930552e-8, .000008983055099779535, .03278182852591, 40.31678527705744, .65659298677277, -4.44255534477492, .85341911805263, .12923347998204, -.04625736007561, 4482777.06),
    (3.09191371068437e-9, .000008983055096812155, .00006995724062, 23.10934304144901, -.00023663490511, -.6321817810242, -.00663494467273, .03430082397953, -.00466043876332, 2555164.4),
    (2.890871144776878e-9, .000008983055095805407, -3.068298e-8, 7.47137025468032, -.00000353937994, -.02145144861037, -.00001234426596, .00010322952773, -.00000323890364, 826088.5),
)


def valid_coordinate(value):
    return (isinstance(value, (list, tuple)) and len(value) == 2
            and all(type(v) in (int, float) and math.isfinite(v) for v in value)
            and -180 <= value[0] <= 180 and -90 <= value[1] <= 90)


def bd09_to_wgs84(lon, lat):
    if not valid_coordinate([lon, lat]):
        raise ValueError("invalid BD09 coordinate")
    x, y = lon - .0065, lat - .006
    z = math.hypot(x, y) - .00002 * math.sin(y * math.pi * 3000 / 180)
    theta = math.atan2(y, x) - .000003 * math.cos(x * math.pi * 3000 / 180)
    lon, lat = z * math.cos(theta), z * math.sin(theta)
    if not (72.004 <= lon <= 137.8347 and .8293 <= lat <= 55.8271):
        return [lon, lat]
    # Invert the GCJ offset iteratively instead of treating BD09 as GPS.
    target, guess = (lon, lat), [lon, lat]
    for _ in range(5):
        x, y = guess[0] - 105, guess[1] - 35
        dlat = -100 + 2*x + 3*y + .2*y*y + .1*x*y + .2*math.sqrt(abs(x))
        dlon = 300 + x + 2*y + .1*x*x + .1*x*y + .1*math.sqrt(abs(x))
        common = (20*math.sin(6*x*math.pi) + 20*math.sin(2*x*math.pi))*2/3
        dlat += common + (20*math.sin(y*math.pi) + 40*math.sin(y*math.pi/3))*2/3 + (160*math.sin(y*math.pi/12) + 320*math.sin(y*math.pi/30))*2/3
        dlon += common + (20*math.sin(x*math.pi) + 40*math.sin(x*math.pi/3))*2/3 + (150*math.sin(x*math.pi/12) + 300*math.sin(x*math.pi/30))*2/3
        rad = math.radians(guess[1])
        magic = 1 - .00669342162296594323 * math.sin(rad)**2
        offsets = (dlon*180/(6378245/math.sqrt(magic)*math.cos(rad)*math.pi),
                   dlat*180/((6378245*(1-.00669342162296594323))/(magic**1.5)*math.pi))
        guess = [target[i] - offsets[i] for i in (0, 1)]
    return [round(v, 7) for v in guess]


def bd09mc_to_wgs84(x, y):
    if (any(type(v) not in (int, float) or not math.isfinite(v) for v in (x, y))
            or abs(x) > 20040000 or abs(y) > 12500000):
        raise ValueError("invalid BD09 Mercator coordinate")
    coeff = next(c for band, c in zip(_BANDS, _MC2LL) if abs(y) >= band)
    t = abs(y) / coeff[9]
    lon = math.copysign(coeff[0] + coeff[1]*abs(x), x)
    lat = math.copysign(sum(coeff[i+2]*t**i for i in range(7)), y)
    return bd09_to_wgs84(lon, lat)
