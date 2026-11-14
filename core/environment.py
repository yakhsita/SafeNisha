"""Refreshed OSM activity/lighting proxies, never live crowd or lamp telemetry."""
import json
import math
import os
import re
import time
from datetime import datetime, timedelta, timezone
from threading import Lock, Thread
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from core.osm_router import USER_AGENT

TTL = 600
DAY_NAMES = ("Mo", "Tu", "We", "Th", "Fr", "Sa", "Su")
IST = timezone(timedelta(hours=5, minutes=30), "Asia/Kolkata")
AMENITIES = "restaurant|cafe|fast_food|food_court|pub|bar|pharmacy|hospital|marketplace|courthouse|bank|library|cinema|community_centre"


def opening_state(hours, local_time):
    """Conservative weekly-hours subset; unsupported/holiday rules stay unknown."""
    if not isinstance(hours, str) or not hours.strip() or local_time is None:
        return "unknown"
    if hours.strip() == "24/7":
        return "open"
    rules = []
    for rule in hours.split(";"):
        match = re.fullmatch(r"\s*(?:(?P<days>(?:Mo|Tu|We|Th|Fr|Sa|Su)(?:-(?:Mo|Tu|We|Th|Fr|Sa|Su))?(?:,(?:Mo|Tu|We|Th|Fr|Sa|Su)(?:-(?:Mo|Tu|We|Th|Fr|Sa|Su))?)*)\s+)?(?P<times>.+?)\s*", rule)
        if not match:
            return "unknown"
        days = set(range(7))
        if match["days"]:
            days = set()
            for part in match["days"].split(","):
                ends = part.split("-")
                first, last = DAY_NAMES.index(ends[0]), DAY_NAMES.index(ends[-1])
                days.update((first + i) % 7 for i in range((last-first) % 7 + 1))
        value = match["times"]
        if value in ("off", "closed"):
            windows = []
        elif value in ("open", "24/7"):
            windows = [(0, 1440)]
        else:
            windows = []
            for span in value.split(","):
                m = re.fullmatch(r"\s*(\d{2}):(\d{2})-(\d{2}):(\d{2})\s*", span)
                if not m:
                    return "unknown"
                a, b, c, d = map(int, m.groups())
                if a > 23 or b > 59 or c > 24 or d > 59 or (c == 24 and d):
                    return "unknown"
                start, end = a*60+b, c*60+d
                if start == end:
                    return "unknown"
                windows.append((start, end))
        rules.append((days, windows))
    weekday, minute = local_time.weekday(), local_time.hour*60+local_time.minute
    current = next((windows for days, windows in reversed(rules) if weekday in days), None)
    previous = next((windows for days, windows in reversed(rules) if (weekday-1) % 7 in days), [])
    if current == []:
        return "closed"
    if any((start <= minute < end if end > start else minute >= start) for start, end in (current or [])):
        return "open"
    if any(end < start and minute < end for start, end in previous):
        return "open"
    return "closed"


def bounds_for(routes):
    points = [p for route in routes for p in route["geometry"]["coordinates"]]
    lat = sum(p[1] for p in points)/len(points)
    dy, dx = 250/110574, 250/(111320*max(.1, math.cos(math.radians(lat))))
    # Quantisation reuses a snapshot for neighbouring GPS fixes.
    w = math.floor((min(p[0] for p in points)-dx)*100)/100
    s = math.floor((min(p[1] for p in points)-dy)*100)/100
    e = math.ceil((max(p[0] for p in points)+dx)*100)/100
    n = math.ceil((max(p[1] for p in points)+dy)*100)/100
    return w, s, e, n


def fetch_osm(bounds):
    w, s, e, n = bounds
    bbox = "{},{},{},{}".format(s, w, n, e)
    query = ('[out:json][timeout:6];('
             'nwr[shop]({b});nwr[amenity~"^({a})$"]({b});'
             'nwr[leisure~"^(pitch|sports_centre|stadium|park|fitness_centre)$"]({b});'
             ');out center tags;way[highway][lit]({b});out geom tags;'
             'node[highway=street_lamp]({b});out tags;').format(b=bbox, a=AMENITIES)
    custom = os.environ.get("SAFENISHA_OVERPASS_URL")
    endpoints = [custom] if custom else ["https://overpass-api.de/api/interpreter", "https://overpass.private.coffee/api/interpreter"]
    for index, endpoint in enumerate(endpoints):
        request = Request(endpoint, data=urlencode({"data": query}).encode(),
                          headers={"User-Agent": USER_AGENT, "Content-Type": "application/x-www-form-urlencoded"})
        try:
            with urlopen(request, timeout=8) as response:
                raw = response.read(4_000_001)
            break
        except OSError:
            if index == len(endpoints)-1:
                raise
    if len(raw) > 4_000_000:
        raise ValueError("Map response exceeds the bounded query size.")
    payload = json.loads(raw)
    if not isinstance(payload, dict) or payload.get("remark") or not isinstance(payload.get("elements"), list):
        raise ValueError("Incomplete map response.")
    if len(payload["elements"]) > 8000:
        raise ValueError("Too many features for this bounded query.")
    return payload


class EnvironmentProvider:
    """Background fetching with a ten-minute cache and one-minute failure backoff."""
    def __init__(self, fetch=fetch_osm):
        self.fetch = fetch
        self.lock = Lock()
        self.cache = {}
        self.pending = set()
        self.last_request = 0.0
        self.fetch_lock = Lock()

    def get(self, routes):
        if os.environ.get("SAFENISHA_CONTEXT_ENABLED", "1") == "0":
            return {"status": "disabled"}
        bounds = bounds_for(routes)
        w, s, e, n = bounds
        area = (e-w)*111320*math.cos(math.radians((n+s)/2))*(n-s)*110574
        if area > 60_000_000 or e-w > .15 or n-s > .15 or not (-180 <= w < e <= 180 and -85 <= s < n <= 85):
            return {"status": "unavailable", "reason": "Route extent exceeds nearby-data query limits."}
        with self.lock:
            cached = self.cache.get(bounds)
            if cached and time.monotonic() < cached[0]:
                return cached[1]
            if bounds not in self.pending:
                if len(self.pending) >= 4:
                    return {"status":"unavailable", "reason":"Nearby-data refresh is busy."}
                self.pending.add(bounds)
                Thread(target=self._load, args=(bounds,), daemon=True).start()
        return {"status": "pending", "source": "OpenStreetMap / Overpass", "refresh_seconds": TTL}

    def _load(self, bounds):
        try:
            with self.fetch_lock:
                delay = 1-(time.monotonic()-self.last_request)
                if delay > 0:
                    time.sleep(delay)
                self.last_request = time.monotonic()
                payload = self.fetch(bounds)
            result = {"status": "available", "source": "OpenStreetMap / Overpass", "bounds": bounds,
                      "fetched_at": datetime.now(timezone.utc).isoformat(),
                      "osm_timestamp": payload.get("osm3s", {}).get("timestamp_osm_base"),
                      "elements": payload["elements"], "refresh_seconds": TTL}
            duration = TTL
        except Exception:
            result = {"status": "unavailable", "source": "OpenStreetMap / Overpass",
                      "reason": "Nearby map data could not be refreshed. Synthetic fallback retained.", "refresh_seconds": 60}
            duration = 60
        with self.lock:
            if len(self.cache) >= 32:
                self.cache.pop(next(iter(self.cache)))
            self.cache[bounds] = (time.monotonic()+duration, result)
            self.pending.discard(bounds)


class EnvironmentField:
    """Spatially indexed, time-evaluated proxies shared by all route candidates."""
    def __init__(self, snapshot, now=None):
        self.snapshot = snapshot
        self.now = now or datetime.now(timezone.utc)
        self.places, self.lamps, self.roads = {}, {}, {}
        w, s, e, n = snapshot["bounds"]
        self.lon0, self.lat0 = (w+e)/2, (s+n)/2
        self.scale = 111320*math.cos(math.radians(self.lat0))
        # Supported demo region is southern India; do not guess time zones elsewhere.
        self.local_time = self.now.astimezone(IST) if 11 <= self.lat0 <= 15 and 74 <= self.lon0 <= 80 else None
        seen = set()
        for item in snapshot.get("elements", []):
            if not isinstance(item,dict) or not isinstance(item.get("tags",{}),dict):
                continue
            ident = "{}:{}".format(item.get("type"), item.get("id"))
            if ident in seen:
                continue
            seen.add(ident)
            tags = item.get("tags", {})
            if tags.get("access") in ("private", "no") or tags.get("disused") == "yes":
                continue
            location = item if "lat" in item else item.get("center", {})
            if tags.get("highway") == "street_lamp" and "lat" in location:
                x, y = self.xy(location["lat"], location["lon"])
                self.lamps.setdefault(self.cell(x, y), []).append((x, y))
            if tags.get("shop") or tags.get("amenity") in AMENITIES.split("|") or tags.get("leisure") in ("pitch", "sports_centre", "stadium", "park", "fitness_centre"):
                if "lat" in location and tags.get("shop") not in ("vacant", "no"):
                    x, y = self.xy(location["lat"], location["lon"])
                    value = (ident, x, y, opening_state(tags.get("opening_hours"), self.local_time))
                    self.places.setdefault(self.cell(x, y), []).append(value)
            lighting = tags.get("lit")
            if tags.get("highway") and lighting in ("yes", "24/7", "no", "disused"):
                geometry = item.get("geometry", [])
                for a, b in zip(geometry, geometry[1:]):
                    ax, ay = self.xy(a["lat"], a["lon"])
                    bx, by = self.xy(b["lat"], b["lon"])
                    # Avoid indexing unexpectedly long ways over an enormous area.
                    if math.hypot(bx-ax, by-ay) > 1500:
                        continue
                    value = (ax, ay, bx, by, .2 if lighting in ("yes", "24/7") else .95)
                    for i in range(math.floor((min(ax,bx)-25)/150), math.floor((max(ax,bx)+25)/150)+1):
                        for j in range(math.floor((min(ay,by)-25)/150), math.floor((max(ay,by)+25)/150)+1):
                            self.roads.setdefault((i,j), []).append(value)

    def xy(self, lat, lon):
        return (lon-self.lon0)*self.scale, (lat-self.lat0)*110574

    @staticmethod
    def cell(x, y):
        return math.floor(x/150), math.floor(y/150)

    def nearby(self, grid, x, y):
        i, j = self.cell(x, y)
        return [value for a in range(i-1,i+2) for b in range(j-1,j+2) for value in grid.get((a,b), [])]

    def at(self, lat, lon):
        x, y = self.xy(lat, lon)
        places = [p for p in self.nearby(self.places,x,y) if math.hypot(p[1]-x,p[2]-y) <= 150]
        activity = sum(1-math.hypot(p[1]-x,p[2]-y)/150 for p in places if p[3] == "open")
        isolation = math.exp(-activity/2) if any(p[3] != "unknown" for p in places) else None
        roads = []
        for ax, ay, bx, by, value in self.roads.get(self.cell(x,y), []):
            dx, dy = bx-ax, by-ay
            t = max(0,min(1,((x-ax)*dx+(y-ay)*dy)/(dx*dx+dy*dy))) if dx or dy else 0
            distance = math.hypot(x-ax-t*dx,y-ay-t*dy)
            if distance <= 25:
                roads.append((distance, -value, value))
        if roads:
            lighting, confidence = min(roads)[2], .7
        elif any(math.hypot(a-x,b-y) <= 30 for a,b in self.nearby(self.lamps,x,y)):
            lighting, confidence = .35, .4
        else:
            lighting, confidence = None, 0
        return isolation, lighting, confidence, places

    def route_factors(self, route, factors, zones):
        from core.synthetic_routing import kernel_factors, field_contains
        deltas = {"isolation": 0.0, "lighting_deficiency": 0.0}
        coverage = {"isolation": 0.0, "lighting_deficiency": 0.0}
        places, total = {}, 0.0
        coords = route["geometry"]["coordinates"]
        for a, b in zip(coords, coords[1:]):
            ax, ay = self.xy(a[1],a[0]); bx, by = self.xy(b[1],b[0])
            length = math.hypot(bx-ax,by-ay)
            count = max(1,math.ceil(length/50))
            for i in range(count):
                t = (i+.5)/count; weight = length/count
                lat, lon = a[1]+(b[1]-a[1])*t, a[0]+(b[0]-a[0])*t
                baseline = (kernel_factors(lat,lon,zones) if field_contains(lat,lon,zones) else
                            {"isolation":1.0,"lighting_deficiency":1.0}) if zones else factors
                isolation, lighting, confidence, nearby = self.at(lat,lon)
                places.update((p[0],p[3]) for p in nearby)
                for key,value,blend in (("isolation",isolation,.65),("lighting_deficiency",lighting,confidence)):
                    if value is not None:
                        deltas[key] += blend*(value-baseline[key])*weight
                        coverage[key] += weight
                total += weight
        values = {key:max(0,min(1,factors[key]+delta/total)) for key,delta in deltas.items() if total and coverage[key]}
        return values, {"open_places": sum(v == "open" for v in places.values()),
                        "closed_places": sum(v == "closed" for v in places.values()),
                        "unknown_hours": sum(v == "unknown" for v in places.values()),
                        "activity_coverage": coverage["isolation"]/total if total else 0,
                        "lighting_coverage": coverage["lighting_deficiency"]/total if total else 0,
                        "applied": sorted(values)}

    def metadata(self):
        return {**{k:v for k,v in self.snapshot.items() if k not in ("elements", "bounds")},
                "evaluated_at": self.now.isoformat(), "timezone": "Asia/Kolkata" if self.local_time else None,
                "activity_type": "opening-hours proxy", "lighting_type": "mapped infrastructure proxy"}
