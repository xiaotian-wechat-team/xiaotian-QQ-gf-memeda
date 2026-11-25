"""Bounded, cached geographic tools. Coordinates always come from map providers.

Persona facts: data/persona_dossier.json. Only public place queries leave the
process; no chat, home-room details, credentials, or complete schedules.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
import threading
import time
import urllib.parse
import urllib.request

from location_coordinates import bd09mc_to_wgs84, valid_coordinate

_network_lock = threading.Lock()
_last_request = 0.0
USER_AGENT = "XiaotianLocation/1.0 (local persona map; cached public geography)"


def osm_precision(properties):
    """Classify geography, never an activity-to-venue mapping."""
    key, value = properties.get("osm_key"), properties.get("osm_value")
    layer = properties.get("type") or properties.get("layer")
    if value in ("city", "town", "village", "state", "country", "administrative"):
        return "city"
    if value in ("station", "halt", "bus_stop", "stop_position", "platform", "aerodrome", "terminal"):
        return "station"
    if key == "highway" or layer == "street":
        return "street"
    if key in ("place", "boundary", "landuse", "natural") or layer in ("locality", "district", "county"):
        return "area"
    if key in ("amenity", "shop", "tourism", "leisure", "office", "craft", "historic"):
        return "venue"
    if key == "building" or layer == "house":
        return "address"
    return "unknown"


def baidu_precision(item):
    tags = str(item.get("std_tag") or "") + ";" + str(item.get("di_tag") or "")
    if any(tag in tags for tag in ("地铁站", "公交站", "公交车站", "火车站", "机场", "站点")):
        return "station"
    if any(tag in tags for tag in ("道路", "路口", "街道")):
        return "street"
    if any(tag in tags for tag in ("行政地标", "行政区划", "商圈", "地名地址")):
        return "area"
    # Require an actual tagged POI, not a city suggestion or arbitrary point.
    if item.get("poiType") == 0 and item.get("detail") == 1 and item.get("addr") and any(
            tag in tags for tag in ("休闲娱乐", "购物", "旅游景点", "文化传媒", "教育培训", "运动健身",
                                    "美食", "生活服务", "酒店", "医疗", "公司企业", "金融", "政府机构")):
        return "venue"
    return "unknown"


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=path.parent, encoding="utf-8", delete=False) as out:
        json.dump(value, out, ensure_ascii=False)
    os.replace(out.name, path)


def distance(a, b):
    lon1, lat1, lon2, lat2 = map(math.radians, [*a, *b])
    h = math.sin((lat2-lat1)/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin((lon2-lon1)/2)**2
    return 6371000 * 2 * math.asin(min(1, math.sqrt(h)))


class Geography:
    def __init__(self, cache, settings=None, *, refresh_search=False):
        self.cache, self.settings = Path(cache), settings or {}
        self.places = {}
        self.refresh_search = refresh_search
        self.last_search = []

    def _json(self, url, ttl=30*86400, *, refresh=False, negative_ttl=600):
        key = hashlib.sha256(url.encode()).hexdigest()
        path = self.cache / (key + ".json")
        try:
            saved = json.loads(path.read_text())
            value = saved["value"]
            empty = isinstance(value, dict) and (value.get("features") == [] or value.get("content") == [])
            if not refresh and time.time() - saved["at"] < (min(ttl, negative_ttl) if empty else ttl):
                return value
        except (OSError, ValueError, KeyError):
            pass
        global _last_request
        # Demo services: one request per second, single process, persistent cache.
        with _network_lock:
            time.sleep(max(0, 1.05 - (time.monotonic() - _last_request)))
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            try:
                with urllib.request.urlopen(request, timeout=18) as response:
                    raw = response.read(5_000_001)
                if len(raw) > 5_000_000:
                    raise ValueError("geography response too large")
                value = json.loads(raw)
            finally:
                _last_request = time.monotonic()
        atomic_json(path, {"at": time.time(), "value": value})
        return value

    def _photon(self, query, near=None, regions=()):
        params = {"q": query, "limit": 6}
        if near:
            params.update(lon=near[0], lat=near[1], location_bias_scale=.4)
        if regions and regions[0].get("extent"):
            west, north, east, south = regions[0]["extent"]
            params["bbox"] = f"{west},{south},{east},{north}"
        base = self.settings.get("search_url", "https://photon.komoot.io/api/")
        data = self._json(base + "?" + urllib.parse.urlencode(params), refresh=self.refresh_search)
        if not isinstance(data, dict) or not isinstance(data.get("features"), list):
            raise ValueError("invalid Photon response")
        results = []
        for feature in data["features"][:6]:
            p, coordinate = feature.get("properties", {}), feature.get("geometry", {}).get("coordinates")
            if not valid_coordinate(coordinate) or not p.get("osm_id"):
                continue
            kind = {"N": "node", "W": "way", "R": "relation"}.get(p.get("osm_type"))
            if not kind:
                continue
            pid, precision = f"osm:{kind}:{p['osm_id']}", osm_precision(p)
            place = dict(id=pid, label=p.get("name") or p.get("street") or query,
                coordinates=coordinate, city=(p.get("state") if str(p.get("state", "")).endswith("市") else p.get("city")) or p.get("state") or "",
                address=" · ".join(str(p[k]) for k in ("country", "city", "district", "street", "housenumber") if p.get(k)),
                precision=precision, place_type=precision, category=p.get("osm_value", ""),
                osm_key=p.get("osm_key"), osm_value=p.get("osm_value"), provider="photon",
                source=f"https://www.openstreetmap.org/{kind}/{p['osm_id']}")
            if p.get("extent"):
                place["extent"] = p["extent"]
            if not place["city"] and precision == "city":
                place["city"] = place["label"]
            results.append(place)
        return results

    def _baidu(self, query, near=None, city=None):
        # Independent POI catalogue; fixed read-only endpoint, no model URLs or city-code table.
        term = f"{city} {query}" if city and city.removesuffix("市") not in query else query
        params = {"qt": "s", "wd": term, "rn": 6, "nn": 0, "ie": "utf-8"}
        base = self.settings.get("poi_search_url", "https://map.baidu.com/")
        data = self._json(base + "?" + urllib.parse.urlencode(params), ttl=86400, refresh=self.refresh_search)
        if not isinstance(data, dict) or not isinstance(data.get("content"), list):
            raise ValueError("invalid Baidu POI response")
        results = []
        for item in data["content"][:6]:
            if not isinstance(item, dict) or not re.fullmatch(r"[a-fA-F0-9]{24}", str(item.get("uid", ""))):
                continue
            # Only point geometry. Lines/areas must never be reinterpreted as a venue entrance.
            match = re.match(r"^1\|(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?);", str(item.get("geo", "")))
            if not match or not item.get("name"):
                continue
            try:
                coordinate = bd09mc_to_wgs84(*map(float, match.groups()))
            except ValueError:
                continue
            precision = baidu_precision(item)
            results.append(dict(id="baidu:" + item["uid"], label=item["name"], coordinates=coordinate,
                city=item.get("city_name") or (item.get("admin_info") or {}).get("city_name") or "",
                address=item.get("addr") or "", category=item.get("di_tag") or item.get("std_tag") or "",
                precision=precision, place_type=precision, provider="baidu", coordinate_system="WGS84",
                original_coordinate_system="BD09MC", note="百度地图地点参考，坐标经近似转换",
                source="https://map.baidu.com/poi/" + item["uid"]))
        return results

    def search(self, query, near=None, city=None, kind=None):
        if not isinstance(query, str) or not 1 <= len(query.strip()) <= 160:
            raise ValueError("query must contain 1–160 characters")
        if city is not None and (not isinstance(city, str) or not 1 <= len(city.strip()) <= 80):
            raise ValueError("invalid city")
        if near is not None and not valid_coordinate(near):
            raise ValueError("invalid nearby coordinate")
        if kind not in (None, "venue", "street", "area", "city", "station"):
            raise ValueError("invalid place kind")
        query = query.strip()
        normalize = lambda value: str(value).strip().removesuffix("市")
        self.last_search = []
        regions = []
        if city:
            try:
                regions = [p for p in self._photon(city) if p["precision"] == "city" and normalize(p["label"]) == normalize(city)]
            except (OSError, ValueError, KeyError, TypeError):
                self.last_search.append({"provider": "photon_region", "status": "error"})
        results = []
        providers = [("photon", lambda: self._photon(query, near, regions))]
        if kind != "city":
            providers.append(("baidu", lambda: self._baidu(query, near, city)))
        for name, lookup in providers:
            try:
                found = lookup()
                results.extend(found)
                self.last_search.append({"provider": name, "status": "ok" if found else "empty", "count": len(found)})
            except (OSError, ValueError, KeyError, TypeError):
                self.last_search.append({"provider": name, "status": "error", "count": 0})
        if city:
            # Provider administrative hierarchies are inconsistent (e.g. a
            # municipality's district in its city field). Verify the requested
            # city geometrically instead of maintaining city-name exceptions.
            for place in results:
                lon, lat = place["coordinates"]
                for region in regions:
                    extent = region.get("extent")
                    inside = (extent[0]-.005 <= lon <= extent[2]+.005 and extent[3]-.005 <= lat <= extent[1]+.005) if extent else distance(place["coordinates"],region["coordinates"]) < 50000
                    # Baidu supplies an explicit municipality name. A rectangular
                    # OSM extent cannot overrule a different authoritative city.
                    if inside and (place.get("provider") != "baidu" or normalize(place["city"]) == normalize(city)):
                        place.update(city=normalize(city), city_source=region["source"])
                        break
        # Deduplicate identical names at the same point; keep distinct venues/branches.
        unique = []
        for place in results:
            if not any(normalize(p["label"]) == normalize(place["label"]) and distance(p["coordinates"], place["coordinates"]) < 80 for p in unique):
                unique.append(place)
        unique.sort(key=lambda p: (bool(kind) and p["precision"] != kind,
                                  distance(near, p["coordinates"]) if near else 0))
        self.places.update({p["id"]: p for p in unique})
        return unique

    def route(self, source, target, mode):
        a, b = source["coordinates"], target["coordinates"]
        meters = distance(a, b)
        if meters < 20:
            return None
        # Rail/air have no road navigation claim; their line is a labelled schematic.
        walking = mode == "步行"
        drive = mode in ("驾车", "出租车", "公交")
        if (walking or drive) and meters < 60000:
            profile = "foot" if walking else "car"
            base = self.settings.get("routing_url", "https://routing.openstreetmap.de")
            path = f"/routed-{profile}/route/v1/{'foot' if walking else 'driving'}/{a[0]},{a[1]};{b[0]},{b[1]}"
            try:
                data = self._json(base + path + "?overview=simplified&geometries=geojson&steps=false")
                route = data["routes"][0]
                points = route["geometry"]["coordinates"]
                # Distant snaps cannot locate a venue entrance.
                if len(points) >= 2 and distance(a, points[0]) < 500 and distance(b, points[-1]) < 500:
                    return dict(points=points, minutes=max(1, math.ceil(route["duration"]/60)),
                                distance_m=round(route["distance"]), kind="road", source="https://routing.openstreetmap.de/about.html")
            except (OSError, ValueError, KeyError, IndexError, TypeError):
                pass
        return dict(points=[a, b], minutes=None, distance_m=round(meters), kind="schematic", source=None)

    def background(self, places):
        coordinates = [p["coordinates"] for p in places]
        if not coordinates:
            return []
        west, east = min(p[0] for p in coordinates), max(p[0] for p in coordinates)
        south, north = min(p[1] for p in coordinates), max(p[1] for p in coordinates)
        if east-west > .14 or north-south > .14:
            return []
        bbox = f"{south-.004:.4f},{west-.004:.4f},{north+.004:.4f},{east+.004:.4f}"
        query = f'[out:json][timeout:12];(way[highway]({bbox});way[building]({bbox});way[waterway]({bbox});way[leisure=park]({bbox}););out geom 250;'
        base = self.settings.get("map_url", "https://overpass-api.de/api/interpreter")
        try:
            value = self._json(base + "?" + urllib.parse.urlencode({"data": query}), ttl=90*86400)
            features = []
            for way in value.get("elements", [])[:250]:
                coords = [[p["lon"], p["lat"]] for p in way.get("geometry", []) if "lon" in p and "lat" in p]
                tags = way.get("tags", {})
                if len(coords) > 1:
                    features.append(dict(kind="road" if tags.get("highway") else "water" if tags.get("waterway") else "park" if tags.get("leisure") else "building", points=coords, label=tags.get("name", "")))
            return features
        except (OSError, ValueError, KeyError, TypeError):
            # A small OSM map extract is an independent read-only fallback when
            # Overpass is busy. Keep the bound small instead of bulk downloading.
            if east-west > .035 or north-south > .035:
                return []
            try:
                data = self._json("https://api.openstreetmap.org/api/0.6/map.json?" + urllib.parse.urlencode({"bbox": f"{west-.002:.5f},{south-.002:.5f},{east+.002:.5f},{north+.002:.5f}"}), ttl=90*86400)
                elements = data.get("elements", [])
                nodes = {n["id"]: [n["lon"], n["lat"]] for n in elements if n.get("type") == "node"}
                features = []
                for way in elements:
                    tags = way.get("tags", {})
                    kind = "road" if tags.get("highway") else "water" if tags.get("waterway") else "park" if tags.get("leisure") == "park" else "building" if tags.get("building") else None
                    points = [nodes[nid] for nid in way.get("nodes", []) if nid in nodes]
                    if kind and len(points) > 1:
                        features.append(dict(kind=kind, points=points, label=tags.get("name", "")))
                # Roads/water first so a dense building block cannot hide all roads.
                return sorted(features, key=lambda f: f["kind"] in ("building", "park"))[:400]
            except (OSError, ValueError, KeyError, TypeError):
                return []
