"""Exercise asynchronous environment updates with controlled data, no public API."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from functools import partial
from http.server import ThreadingHTTPServer
import tempfile
from threading import Thread
from unittest.mock import patch
from playwright.sync_api import sync_playwright
from core.accounts import Accounts
from core.journey_service import JourneyService
from hazards import database
from web_app import Handler
from tests.test_journey_service import ROUTE
from tests.test_environment import snapshot,place


class Provider:
    calls=0
    failed=False
    def get(self,routes):
        self.calls+=1
        if self.failed:return {"status":"unavailable"}
        if self.calls==1:return {"status":"pending"}
        return snapshot([place(1,12.9352,77.6245,"24/7"),
            {"type":"way","id":2,"tags":{"highway":"residential","lit":"yes"},
             "geometry":[{"lat":lat,"lon":lon} for lon,lat in ROUTE["geometry"]["coordinates"]]}])


class QuietHandler(Handler):
    def log_message(self,*args):pass


with tempfile.TemporaryDirectory() as directory:
    path=Path(directory)/"context.sqlite3"
    database.initialize_database(path)
    provider=Provider()
    server=ThreadingHTTPServer(("127.0.0.1",0),partial(QuietHandler,
        service=JourneyService(path,environment_provider=provider),accounts=Accounts(path)))
    thread=Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        with patch("core.journey_service.get_route_alternatives",return_value=[ROUTE]), \
             patch("core.journey_service.find_risk_alternatives",return_value=[]), sync_playwright() as p:
            browser=p.chromium.launch(channel="chrome",headless=True)
            context=browser.new_context(viewport={"width":1280,"height":950})
            context.add_init_script("window.WebGL2RenderingContext=undefined;")
            page=context.new_page();errors=[]
            page.on("pageerror",lambda e:errors.append(str(e)))
            page.goto("http://127.0.0.1:{}".format(server.server_address[1]),wait_until="networkidle")
            page.locator("#src").fill("12.9352, 77.6245")
            page.locator("#dst").fill("12.9400, 77.6200")
            page.wait_for_function("()=>SN.planner.scene.routes?.environment?.status==='pending'")
            assert page.locator("#plan-panel .route-card").count()==2
            assert "Checking" in page.locator("#route-context").inner_text()
            page.wait_for_function("()=>SN.planner.scene.routes?.environment?.status==='available'",timeout=15000)
            assert "estimated open" in page.locator("#route-context").inner_text()
            assert "Map fetched" in page.locator("#route-context").inner_text()
            assert "mixed sources" in page.locator("#plan-panel").inner_text()
            assert page.locator("#plan-map image.kde-heatmap").count()==0
            page.locator("#tab-atlas").click()
            assert page.locator("#atlas-map image.kde-heatmap").count()==1
            provider.failed=True
            page.evaluate("()=>SN.planner.refresh({background:true})")
            page.wait_for_function("()=>SN.planner.scene.routes?.environment?.status==='unavailable'")
            assert "Synthetic estimates" in page.locator("#route-context").inner_text()
            assert page.evaluate("()=>SN.planner.scene.routes.lower_risk.risk_source")=="synthetic"
            page.locator("#tab-plan").click()
            page.set_viewport_size({"width":390,"height":844})
            assert page.evaluate("()=>document.documentElement.scrollWidth<=innerWidth+1")
            assert not errors,errors
            browser.close()
            print("PASS: initial route remains visible; background context changes risk/source labels; outage restores fallback; atlas heatmap and mobile layout remain valid.")
    finally:
        server.shutdown();server.server_close();thread.join()
