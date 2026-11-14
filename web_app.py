"""Run the integrated standalone frontend: python web_app.py [--port 8502]."""
import argparse
import json
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from core.journey_service import JourneyService
from core.geocoding import AddressSearch, GeocodingServiceError

ADDRESS_SEARCH = AddressSearch()
from core.osm_router import RoutingServiceError
from hazards import database, hazard_service

WEB_ROOT = Path(__file__).resolve().parent / "ui" / "web"


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, service, **kwargs):
        self.service = service
        super().__init__(*args, directory=str(WEB_ROOT), **kwargs)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def respond(self, status, payload):
        raw = json.dumps(payload,allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length",str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        path=urlsplit(self.path).path
        if path == "/api/health":
            return self.respond(200,{"ok":True,"frontend":"integrated-web"})
        if path == "/api/hazards":
            return self.respond(200,{"hazards":self.service.reports()})
        if path.startswith("/api/"):
            return self.respond(404,{"error":"Unknown endpoint."})
        super().do_GET()

    def do_POST(self):
        origin=self.headers.get("Origin")
        if origin and origin != "http://"+self.headers.get("Host", ""):
            return self.respond(403,{"error":"Cross-origin requests are not allowed."})
        try:
            length=int(self.headers.get("Content-Length",0))
            if not 0 < length <= 65536:
                raise ValueError("Invalid request size.")
            body=json.loads(self.rfile.read(length))
            if not isinstance(body,dict):
                raise ValueError("Expected a JSON object.")
            path=urlsplit(self.path).path
            if path == "/api/atlas":
                return self.respond(200,self.service.atlas(body))
            if path == "/api/geocode":
                return self.respond(200,{"places":ADDRESS_SEARCH.search(body.get("query"),body.get("bias"))})
            if path == "/api/routes":
                return self.respond(200,self.service.plan(body))
            if path == "/api/hazards":
                report_id=hazard_service.add_hazard(
                    hazard_type=body.get("hazard_type"),description=body.get("description",""),
                    latitude=body.get("lat"),longitude=body.get("lon"),
                    database_path=self.service.database_path)
                report=next(r for r in self.service.reports() if r["id"]==report_id)
                return self.respond(201,{"ok":True,"hazard":report,"message":"Report saved for local review."})
            if path == "/api/hazards/status":
                hazard_service.set_report_status(body.get("id"),body.get("status"),
                                                database_path=self.service.database_path)
                return self.respond(200,{"ok":True,"hazards":self.service.reports()})
            self.respond(404,{"error":"Unknown endpoint."})
        except (RoutingServiceError,GeocodingServiceError) as error:
            self.respond(502,{"error":str(error)})
        except (ValueError,TypeError,KeyError) as error:
            self.respond(400,{"error":str(error)})
        except Exception:
            self.log_error("Application request failed")
            self.respond(500,{"error":"Could not complete the request. Please retry."})


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--port",type=int,default=8502)
    parser.add_argument("--database",default=None)
    args=parser.parse_args()
    database.initialize_database(args.database)
    server=ThreadingHTTPServer(("127.0.0.1",args.port),partial(Handler,service=JourneyService(args.database)))
    print("SafeNisha integrated frontend: http://127.0.0.1:{}".format(args.port),flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()


if __name__ == "__main__":
    main()
