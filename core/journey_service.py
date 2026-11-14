"""Shared application service for the standalone web frontend."""
import json
import math
import time
from functools import lru_cache
from threading import Lock

from core.incident_model import DATA_PATH
from core.osm_router import get_route_alternatives, get_routes
from core.hazard_detour import find_hazard_detour, _clear_of, _hazard_polygons, _has_excursion, _points, _meters
from core.route_scoring import score_route_alternatives, choose_route_pair, _point_to_segment_meters
from core.synthetic_routing import zone_profiles, build_demo_zones, risk_field_definition
from core.risk_alternatives import find_risk_alternatives, unique_routes
from hazards import hazard_service


@lru_cache(maxsize=4)
def _profiles(contents):
    return zone_profiles(json.loads(contents))


def coordinates(value):
    if isinstance(value, str):
        try:
            value = [float(v.strip()) for v in value.split(",")]
        except ValueError:
            raise ValueError("Enter latitude, longitude or choose a point on the map.")
    if isinstance(value, dict):
        value = [value.get("lat"), value.get("lon")]
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError("Both latitude and longitude are required.")
    lat, lon = value
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in value):
        raise ValueError("Coordinates must be finite numbers.")
    if not -90 <= lat <= 90 or not -180 <= lon <= 180:
        raise ValueError("Coordinates are outside valid geographic ranges.")
    return (float(lat), float(lon))


def report_for_web(report):
    return {**report, "lat": report["latitude"], "lon": report["longitude"],
            "label": report["hazard_type"].replace("_", " ").capitalize()}


def avoids_hazards(route, hazards, allow_escape=False):
    """Allow departure from an existing hazard, then reject subsequent re-entry."""
    points=_points(route)
    for hazard in hazards:
        location=(hazard["latitude"],hazard["longitude"])
        if allow_escape and _meters(points[0],location)<=50:
            previous=_meters(points[0],location)
            outside=None
            for i,point in enumerate(points[1:],1):
                if _point_to_segment_meters(location,points[i-1],point)+2<previous:
                    return False
                distance=_meters(point,location)
                if distance+2<previous:
                    return False
                previous=distance
                if distance>50:
                    outside=i
                    break
            if outside is None:
                return False
            if outside==len(points)-1:
                continue
            remaining={"geometry":{"coordinates":[[lon,lat] for lat,lon in points[outside:]]}}
            if not _clear_of(remaining,[hazard]):
                return False
        elif not _clear_of(route,[hazard]):
            return False
    return not _has_excursion(route)


def mark_departure_turn(route, heading, origin):
    """Add a U-turn only if the actual departure opposes the user's heading."""
    if heading is None:
        return route
    points=_points(route)
    target=next((point for point in points[1:] if _meters(points[0],point)>3),None)
    if target is None:
        return route
    dy=target[0]-points[0][0]
    dx=(target[1]-points[0][1])*math.cos(math.radians(points[0][0]))
    departure=math.degrees(math.atan2(dx,dy))%360
    difference=abs((departure-heading+180)%360-180)
    if difference<135:
        return route
    result=dict(route)
    steps=[dict(step) for leg in route.get("legs",[]) for step in leg.get("steps",[])]
    if not steps:
        steps=[{"distance":0,"name":"","maneuver":{}}]
    steps[0]["maneuver"]={**steps[0].get("maneuver",{}),"type":"turn","modifier":"uturn",
                          "location":[origin[1],origin[0]],"instruction":"Make a U-turn"}
    result["legs"]=[{"steps":steps}]
    result["detour"]={**route.get("detour",{}),"turnaround":origin,
                       "divergence":origin,"return_geometry":points[:2]}
    return result


class JourneyService:
    def __init__(self, database_path=None):
        self.database_path = database_path
        self.cache = {}
        self.detours = {}
        self.risk_candidates = {}
        self.lock = Lock()

    def atlas(self, body):
        latitude,longitude=coordinates(body.get("center"))
        radius=body.get("radius_m",2500)
        if isinstance(radius,bool) or not isinstance(radius,(int,float)) or not math.isfinite(radius) or not 500<=radius<=7000:
            raise ValueError("Atlas radius must be between 500 and 7000 metres.")
        dy=radius/110574.0
        dx=radius/(111320.0*max(0.01,math.cos(math.radians(latitude))))
        west,east=max(-180,longitude-dx),min(180,longitude+dx)
        south,north=max(-85,latitude-dy),min(85,latitude+dy)
        profiles=_profiles(DATA_PATH.read_text(encoding="utf-8-sig"))
        zones=build_demo_zones(profiles,[{"geometry":{"coordinates":[[west,south],[east,north]]}}])
        return {"synthetic_zones":zones,"risk_field":risk_field_definition(),"bounds":{"west":west,"east":east,"south":south,"north":north}}

    def reports(self):
        return [report_for_web(r) for r in hazard_service.get_reports(self.database_path)
                if r.get("latitude") is not None and r.get("longitude") is not None]

    def plan(self, body):
        active=body.get("journey_active",False)
        force=body.get("force_reroute",False)
        if not isinstance(active,bool) or not isinstance(force,bool):
            raise ValueError("Invalid journey mode.")
        start=coordinates(body.get("current_location") if active and force else body.get("start"))
        end=coordinates(body.get("destination"))
        heading=body.get("heading")
        if heading is not None and (isinstance(heading,bool) or not isinstance(heading,(int,float)) or not math.isfinite(heading) or not 0<=heading<360):
            raise ValueError("Invalid travel heading.")
        avoid_ids=body.get("avoid_report_ids",[])
        if not isinstance(avoid_ids,list) or len(avoid_ids)>500 or any(type(v) is not int or v<=0 for v in avoid_ids):
            raise ValueError("Invalid report IDs.")
        if start == end:
            raise ValueError("Choose different starting and destination points.")
        beta = body.get("beta", 2.0)
        if isinstance(beta, bool) or not isinstance(beta, (int, float)) or not math.isfinite(beta) or not 0 <= beta <= 5:
            raise ValueError("Safety preference must be between zero and five.")
        key = (start, end)
        with self.lock:
            cached = self.cache.get(key)
        if force or not cached or time.monotonic() - cached[0] > 300:
            routes = get_route_alternatives(start, end, timeout=12)
            with self.lock:
                if len(self.cache) >= 64:
                    self.cache.pop(next(iter(self.cache)))
                self.cache[key] = (time.monotonic(), routes)
        else:
            routes = cached[1]
        profiles = _profiles(DATA_PATH.read_text(encoding="utf-8-sig"))
        zones = build_demo_zones(profiles, routes)
        layers = {"synthetic_zones": zones}
        # Explore hotspot bypasses even when the default router returns one path.
        # Keep the field anchored to the original query while ranking every path.
        if beta>0:
            risk_key=(key,json.dumps(zones,sort_keys=True))
            with self.lock:risk_cached=self.risk_candidates.get(risk_key)
            if not risk_cached or time.monotonic()-risk_cached[0]>300:
                base_shortest=min(routes,key=lambda r:r["distance"])
                extra=find_risk_alternatives(base_shortest,zones,start,end)
                with self.lock:
                    if len(self.risk_candidates)>=64:self.risk_candidates.pop(next(iter(self.risk_candidates)))
                    self.risk_candidates[risk_key]=(time.monotonic(),extra)
            else:extra=risk_cached[1]
            routes=unique_routes(routes+extra)

        confirmed = hazard_service.get_confirmed_reports(self.database_path)
        base_scored = score_route_alternatives(routes, layers, confirmed, beta)
        shortest, base = choose_route_pair(base_scored)
        base_route = routes[base_scored.index(base)]
        hazards = list(confirmed)
        known_ids={r["id"] for r in hazards}
        for report in self.reports():
            if report["id"] in avoid_ids and report["id"] not in known_ids and report["status"]!="resolved":
                hazards.append({**report,"preview":True})
                known_ids.add(report["id"])
        preview = body.get("preview_hazard")
        if preview is not None:
            lat, lon = coordinates(preview)
            hazards.append({"latitude":lat, "longitude":lon, "preview":True})
        scored = score_route_alternatives(routes, layers, hazards, beta)
        shortest = min(scored, key=lambda r:r["distance"])
        preferred = scored[base_scored.index(base)]
        blocked = False
        if (beta>0 or force or avoid_ids) and hazards and (force or preferred["risk_factors"]["live_hazards"]>0):
            if force:
                try:
                    avoidance=get_routes([start,end],alternatives=2,timeout=12,
                                         exclude_polygons=_hazard_polygons(hazards))
                except ValueError:
                    avoidance=[]
                candidates=score_route_alternatives(routes+avoidance,layers,hazards,beta)
                clear=[r for r in candidates if avoids_hazards(r,hazards,allow_escape=True)]
                if clear:
                    preferred=min(clear,key=lambda r:r["generalized_cost"])
                else:
                    blocked=True
            else:
                detour_key=(key,json.dumps(base_route["geometry"],sort_keys=True),
                            tuple(sorted((h["latitude"],h["longitude"]) for h in hazards)))
                with self.lock:
                    detour=self.detours.get(detour_key)
                if detour is None:
                    detour=find_hazard_detour(base_route,hazards,end)
                    if detour is not None:
                        with self.lock:
                            if len(self.detours)>=64:self.detours.pop(next(iter(self.detours)))
                            self.detours[detour_key]=detour
                if detour:
                    preferred=score_route_alternatives([detour],layers,hazards,beta)[0]
                else:
                    clear=[r for r in scored if r["risk_factors"]["live_hazards"]==0]
                    if clear:preferred=min(clear,key=lambda r:r["generalized_cost"])
                    else:blocked=True
        if active and force and not blocked:
            preferred=mark_departure_turn(preferred,heading,start)
        def package(route):
            points = route["geometry"]["coordinates"]
            risk_paths=[];chunk=[points[0]];chunk_length=0.0
            def finish_chunk():
                if len(chunk)<2:return
                piece={"distance":max(1,chunk_length),"geometry":{"coordinates":list(chunk)}}
                local=score_route_alternatives([piece],layers,hazards,beta)[0]["risk_indicator"]
                risk_paths.append({"path":[[lat,lon] for lon,lat in chunk],"risk":local,"distance_m":chunk_length})
            for a,b in zip(points,points[1:]):
                length=_meters((a[1],a[0]),(b[1],b[0]))
                count_parts=max(1,math.ceil(length/max(40,route["distance"]/400)))
                for i in range(1,count_parts+1):
                    chunk.append([a[0]+(b[0]-a[0])*i/count_parts,a[1]+(b[1]-a[1])*i/count_parts])
                    chunk_length+=length/count_parts
                    if chunk_length>=60:
                        finish_chunk();chunk=[chunk[-1]];chunk_length=0.0
            finish_chunk()
            total_length=sum(piece["distance_m"] for piece in risk_paths)
            count=min(12,len(risk_paths));segments=[]
            for index in range(count):
                lower=total_length*index/count;upper=total_length*(index+1)/count
                walked=0.0;weighted=0.0
                for piece in risk_paths:
                    overlap=max(0.0,min(upper,walked+piece["distance_m"])-max(lower,walked))
                    weighted+=piece["risk"]*overlap;walked+=piece["distance_m"]
                segments.append(weighted/(upper-lower) if upper>lower else route["risk_indicator"])
            steps = [step for leg in route.get("legs",[]) for step in leg.get("steps",[])]
            return {"distance_m":route["distance"],"risk_indicator":route["risk_indicator"],
                    "risk_factors":route["risk_factors"], "risk_source":"synthetic",
                    "path":[[lat,lon] for lon,lat in points],"segment_risk":segments,"risk_paths":risk_paths,
                    "detour":route.get("detour"),"steps":steps}
        return {"live":True,"shortest":package(shortest),"lower_risk":package(preferred),
                "start":{"lat":start[0],"lon":start[1]},"end":{"lat":end[0],"lon":end[1]},
                "synthetic_zones":zones,"risk_field":risk_field_definition(),"hazard_blocked":blocked,"candidate_count":len(routes),"origin_mode":"current" if active and force else "planned",
                "avoided_report_ids":[r["id"] for r in hazards if r.get("id")] }
