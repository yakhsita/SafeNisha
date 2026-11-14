"""Cached Photon searches, prioritizing places within seven kilometres of the user."""
import json
import math
import os
import time
from threading import Lock
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from core.osm_router import USER_AGENT


class GeocodingServiceError(ValueError):
    pass


class AddressSearch:
    def __init__(self):
        self.lock = Lock()
        self.cache = {}
        self.last_request = 0.0

    def search(self, query, bias=None):
        if not isinstance(query, str) or not 2 <= len(query.strip()) <= 250:
            raise ValueError("Enter an address or place name between 2 and 250 characters.")
        query = query.strip()
        params = {"q":query,"limit":20,"lang":"en"}
        search_center=None
        if bias is not None:
            if not isinstance(bias,dict):
                raise ValueError("Invalid search location.")
            lat,lon=bias.get("lat"),bias.get("lon")
            if any(isinstance(v,bool) or not isinstance(v,(float,int)) or not math.isfinite(v) for v in (lat,lon)) or abs(lat)>90 or abs(lon)>180:
                raise ValueError("Invalid search location.")
            search_center=(lat,lon)
            latitude_span=7000/110574.0
            longitude_span=7000/(111320.0*max(0.01,math.cos(math.radians(lat))))
            params.update(lat=round(lat,3),lon=round(lon,3),zoom=13,location_bias_scale=0.1,
                          bbox=",".join(str(v) for v in (max(-180,lon-longitude_span),max(-90,lat-latitude_span),min(180,lon+longitude_span),min(90,lat+latitude_span))))
        key=json.dumps(params,sort_keys=True)
        with self.lock:
            if key in self.cache:
                return self.cache[key]
            endpoint=os.environ.get("SAFENISHA_GEOCODER_URL","https://photon.komoot.io/api/")
            def fetch(parameters):
                delay=1.0-(time.monotonic()-self.last_request)
                if delay>0:time.sleep(delay)
                self.last_request=time.monotonic()
                request=Request(endpoint+"?"+urlencode(parameters),headers={"User-Agent":USER_AGENT,"Accept":"application/json"})
                try:
                    with urlopen(request,timeout=15) as response:
                        payload=json.loads(response.read(2_000_000).decode("utf-8"))
                except (HTTPError,URLError,OSError,ValueError) as error:
                    raise GeocodingServiceError("Address search is unavailable. Retry or choose a point on the map.") from error
                if not isinstance(payload,dict) or not isinstance(payload.get("features"),list):
                    raise GeocodingServiceError("The address service returned an invalid response.")
                return payload["features"]
            features=fetch(params)
            if search_center:
                center_lat,center_lon=search_center
                def is_near(feature):
                    try:
                        lon,lat=feature["geometry"]["coordinates"]
                        return math.hypot((lat-center_lat)*110574,(lon-center_lon)*111320*math.cos(math.radians(center_lat)))<=7000
                    except (KeyError,TypeError,ValueError):return False
                features=[f for f in features if is_near(f)]
            local=bool(search_center)
            if not features and search_center:
                # Explicitly named places farther away remain searchable too.
                features=fetch({k:v for k,v in params.items() if k!="bbox"})
                local=False
            places=[]
            for feature in features[:20]:
                try:
                    lon,lat=feature["geometry"]["coordinates"]
                    if not all(isinstance(v,(float,int)) and math.isfinite(v) for v in (lat,lon)) or abs(lat)>90 or abs(lon)>180:
                        continue
                    props=feature.get("properties",{})
                    labels=[]
                    for field in ("name","housenumber","street","district","city","state","postcode","country"):
                        value=props.get(field)
                        if isinstance(value,str) and value and value not in labels:
                            labels.append(value)
                    distance=None
                    if search_center:
                        center_lat,center_lon=search_center
                        distance=math.hypot((lat-center_lat)*110574,(lon-center_lon)*111320*math.cos(math.radians(center_lat)))
                        if local and distance>7000:continue
                    place={"lat":lat,"lon":lon,"label":", ".join(labels) or query}
                    if distance is not None:place["distance_m"]=round(distance)
                    if not any(p["lat"]==lat and p["lon"]==lon for p in places):places.append(place)
                except (KeyError,ValueError,TypeError):
                    continue
            if search_center:places.sort(key=lambda p:p.get("distance_m",float("inf")))
            if len(self.cache)>=256:
                self.cache.pop(next(iter(self.cache)))
            self.cache[key]=places
            return places
