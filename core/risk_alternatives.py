"""Ask the walking router for bounded bypasses of modelled risk hotspots."""
import math
from concurrent.futures import ThreadPoolExecutor

from core.hazard_detour import _has_excursion
from core.osm_router import get_routes
from core.synthetic_routing import KERNEL_BANDWIDTH


def _distance_squared(center,point,sx,sy):
    return ((point[0]-center[0])/sx)**2+((point[1]-center[1])/sy)**2


def hotspot_polygons(route,zones,start,end):
    scored=[]
    for zone in zones:
        w,s,e,n=zone["bounds"]
        center=((w+e)/2,(s+n)/2)
        sx=max((e-w)*KERNEL_BANDWIDTH,1e-8);sy=max((n-s)*KERNEL_BANDWIDTH,1e-8)
        # Never force the router to snap an endpoint away from the selected place.
        if any(_distance_squared(center,(point[1],point[0]),sx,sy)<4.0 for point in (start,end)):
            continue
        closest=float("inf")
        points=route["geometry"]["coordinates"]
        for a,b in zip(points,points[1:]):
            ax,ay=(a[0]-center[0])/sx,(a[1]-center[1])/sy
            dx,dy=(b[0]-a[0])/sx,(b[1]-a[1])/sy
            fraction=max(0,min(1,-(ax*dx+ay*dy)/(dx*dx+dy*dy))) if dx or dy else 0
            closest=min(closest,(ax+fraction*dx)**2+(ay+fraction*dy)**2)
        mass=0.35*zone["historical_incidents"]+0.30*zone["lighting_deficiency"]+0.10*zone["isolation"]
        exposure=mass*math.exp(-closest/2)
        if exposure<0.05:continue
        polygon=[[center[0]+1.35*sx*math.cos(i*math.pi/8),center[1]+1.35*sy*math.sin(i*math.pi/8)] for i in range(16)]
        scored.append((exposure,polygon+[polygon[0]]))
    return [polygon for _,polygon in sorted(scored,key=lambda item:item[0],reverse=True)[:3]]


def unique_routes(routes):
    result=[];seen=set()
    for route in routes:
        key=tuple((round(lon,6),round(lat,6)) for lon,lat in route["geometry"]["coordinates"])
        if key not in seen and not _has_excursion(route):
            result.append(route);seen.add(key)
    return result


def find_risk_alternatives(route,zones,start,end,fetch=get_routes):
    """Explore at most two direct polygon-avoidance requests; no artificial vias."""
    polygons=hotspot_polygons(route,zones,start,end)
    if not polygons:return []
    requests=[polygons[:1]]
    if len(polygons)>1:requests.append(polygons)
    def request(exclusions):
        try:return fetch([start,end],alternatives=2,timeout=12,exclude_polygons=exclusions)
        except ValueError:return []
    with ThreadPoolExecutor(max_workers=2) as pool:
        return unique_routes([candidate for batch in pool.map(request,requests) for candidate in batch])
