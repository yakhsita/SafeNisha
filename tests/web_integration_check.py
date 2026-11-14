"""Manual browser checks for web_app.py on port 8503 with an isolated database."""
from playwright.sync_api import sync_playwright

BASE="http://127.0.0.1:8503"
with sync_playwright() as p:
    browser=p.chromium.launch(channel="chrome",headless=True,args=["--use-angle=swiftshader","--enable-unsafe-swiftshader"])
    context=browser.new_context(viewport={"width":1440,"height":1100},
        permissions=["geolocation"],geolocation={"latitude":12.9352,"longitude":77.6245})
    page=context.new_page();errors=[];requests=[]
    page.on("pageerror",lambda error:errors.append(str(error)))
    page.on("request",lambda request:requests.append(request.post_data_json) if request.url.endswith("/api/routes") and request.method=="POST" else None)
    page.goto(BASE,wait_until="networkidle")
    page.wait_for_function("()=>SN.planner.map.city",timeout=60000)
    page.wait_for_function("() => SN.planner?.scene.currentLocation")
    assert page.locator("#save-preview").count()==0
    assert page.locator("#map-hazard svg").inner_html()==page.locator("#tab-report .tab-ic svg").inner_html()
    # Atlas works even before the user plans a route.
    page.locator("#tab-atlas").click()
    page.locator("#atlas-map image.kde-heatmap").wait_for(state="attached")
    assert page.locator("#plan-map image.kde-heatmap").count()==0
    page.wait_for_timeout(1800)
    page.screenshot(path="tests/risk-atlas-kde.png",full_page=True)
    page.locator("#tab-plan").click()
    assert page.locator('[data-search="start"]').is_hidden()
    # Suggestions appear while typing, without pressing Enter or a search button.
    page.locator("#src").fill("Lalbagh")
    page.locator("#src-results button").first.wait_for(timeout=45000)
    assert page.locator('[data-search="start"]').is_visible()
    assert page.locator("#src").input_value()=="Lalbagh"
    page.locator("#src").press("ArrowDown")
    page.wait_for_timeout(300)
    assert page.locator("#src-results").is_visible()
    page.keyboard.press("Enter")
    page.wait_for_function("() => SN.planner.scene.start && document.querySelector('#src').value.includes('Lalbagh')")
    assert page.locator("#src-results").is_hidden()
    assert page.locator('[data-search="start"]').is_hidden()
    assert page.locator("#plan-map .marker.endpoint.start").count()==1
    print("PASS: atlas-only hotspots and typing-based nearby address suggestions, including keyboard selection",flush=True)
    page.locator("#src").fill("12.9352, 77.6245")
    page.locator("#dst").fill("12.9400, 77.6200")
    page.wait_for_function("() => SN.planner.scene.routes && !SN.planner.scene.loading",timeout=90000)
    assert page.evaluate("()=>SN.planner.map.city.routeObjs.length")>=1
    assert page.locator("#plan-map image.kde-heatmap").count()==0
    assert page.locator("#plan-map .synthetic-zones rect").count()==0
    assert page.evaluate("() => JSON.stringify(SN.computeRoutes(SN.planner.scene).low.path)===JSON.stringify(SN.planner.scene.routes.lower_risk.path)")
    point=page.evaluate("() => {const ps=SN.planner.scene.routes.lower_risk.path;return ps[Math.floor(ps.length/2)]}")
    print("PASS: live route geometry with a clean planner map",flush=True)
    page.locator("#tab-atlas").click()
    page.locator("#atlas-map .rt-main").first.wait_for(state="attached")
    assert page.locator("#atlas-map .rt-main").count()==2
    assert page.locator("#atlas-map image.kde-heatmap").count()==1
    page.locator("#tab-plan").click()
    previous=page.request.post(BASE+"/api/hazards",data={"lat":12.932,"lon":77.628,"hazard_type":"dark_path","description":"Previous report for pin visibility check"}).json()["hazard"]
    page.request.post(BASE+"/api/hazards/status",data={"id":previous["id"],"status":"resolved"})
    start_before=page.locator("#src").input_value();end_before=page.locator("#dst").input_value()
    page.locator("#map-hazard").click()
    assert page.locator("#view-plan").is_visible()
    assert page.locator("#map-hazard").get_attribute("aria-pressed")=="true"
    count=len(requests)
    page.wait_for_timeout(1600)
    screen=page.evaluate("""p=>{
      const city=SN.planner.map.city,b=city.canvas.getBoundingClientRect();
      const q=city.world(p[0],p[1]).project(city.camera);
      const x=b.left+(q.x+1)*b.width/2,y=b.top+(1-q.y)*b.height/2;
      // Clicking a roof selects its real coordinates in the tilted view.
      const hit=city._groundPoint(x,y,true);
      return {x,y,lat:hit.lat,lon:hit.lon};
    }""",point)
    page.mouse.click(screen["x"],screen["y"])
    page.locator("#view-report").wait_for(state="visible")
    assert abs(float(page.locator("#h-lat").input_value())-screen["lat"])<1e-6
    assert abs(float(page.locator("#h-lon").input_value())-screen["lon"])<1e-6
    previous_count=len(page.request.get(BASE+"/api/hazards").json()["hazards"])
    page.wait_for_function("n=>document.querySelectorAll('#report-map .hz').length===n",arg=previous_count)
    pin=page.locator("#report-map .selected-hazard-pin")
    assert abs(float(pin.get_attribute("data-lat"))-screen["lat"])<1e-6
    assert page.locator("#src").input_value()==start_before
    assert page.locator("#dst").input_value()==end_before
    page.wait_for_timeout(700)
    assert len(requests)==count,"Picking a draft report must not reroute"
    assert page.locator("#report-map image.kde-heatmap").count()==0
    colour=page.locator("#tab-report .tab-ic").evaluate("el=>getComputedStyle(el).color")
    assert colour=="rgb(239, 68, 68)"
    print("PASS: atlas routes plus heatmap; hazard button waits for map pick then carries the pin to the red report page",flush=True)
    page.locator('input[name="htype"][value="dark_path"]').check(force=True)
    page.locator("#h-desc").fill("Submit-only reroute integration test")
    with page.expect_response(lambda r:r.url.endswith("/api/hazards") and r.request.method=="POST" and r.status==201) as created:
        page.locator("#h-submit").click()
    report_id=created.value.json()["hazard"]["id"]
    page.locator("#view-plan").wait_for(state="visible")
    page.wait_for_function("id => SN.planner.scene.routes?.avoided_report_ids?.includes(id) && !SN.planner.scene.loading",arg=report_id,timeout=90000)
    assert requests[-1]["force_reroute"] is True
    assert requests[-1]["journey_active"] is False
    assert requests[-1]["start"]=={"lat":12.9352,"lon":77.6245}
    report=next(r for r in page.request.get(BASE+"/api/hazards").json()["hazards"] if r["id"]==report_id)
    assert report["status"]=="new"
    print("PASS: report page waits for submit, then returns and reroutes the full planned trip",flush=True)
    page.locator("#map-reroute").click()
    page.wait_for_timeout(500)
    page.wait_for_function("() => !SN.planner.scene.loading && !document.querySelector('#map-reroute').disabled",timeout=90000)
    assert page.locator("#view-plan").is_visible()
    page.locator("#journey-toggle").click()
    page.get_by_role("button",name="Stop journey",exact=True).wait_for()
    page.wait_for_timeout(700)
    context.set_geolocation({"latitude":12.9356,"longitude":77.6240})
    page.wait_for_function("() => Math.abs(SN.planner.scene.currentLocation.lat-12.9356)<1e-7")
    page.wait_for_timeout(700)
    page.locator("#map-reroute").click()
    page.wait_for_function("() => SN.planner.scene.routes?.origin_mode==='current' && !SN.planner.scene.loading && Math.abs(SN.planner.scene.routes.start.lat-12.9356)<1e-7",timeout=90000)
    assert requests[-1]["current_location"]=={"lat":12.9356,"lon":77.6240}
    assert requests[-1]["journey_active"] is True
    page.locator("#journey-toggle").click()
    print("PASS: dedicated reroute stays on planner and uses the current GPS fix mid-journey",flush=True)
    # Report map includes all saved statuses even with planner hazards hidden.
    saved=page.request.post(BASE+"/api/hazards",data={"lat":12.936,"lon":77.622,"hazard_type":"blocked_footpath","description":"Resolved history marker"}).json()["hazard"]
    page.request.post(BASE+"/api/hazards/status",data={"id":saved["id"],"status":"resolved"})
    page.locator("#settings-btn").click();page.locator("#tg-hazards").uncheck();page.locator("#settings-btn").click()
    page.locator("#tab-report").click()
    expected=len(page.request.get(BASE+"/api/hazards").json()["hazards"])
    page.wait_for_function("n => document.querySelectorAll('#report-map .hz').length===n",arg=expected)
    assert page.locator("#reports .report").count()==expected
    print("PASS: report map shows all saved hazards, including resolved reports, independently of planner visibility",flush=True)
    page.locator("#tab-plan").click()
    page.wait_for_timeout(600)
    page.screenshot(path="tests/integrated-web-desktop.png",full_page=True)
    page.set_viewport_size({"width":390,"height":844});page.wait_for_timeout(500)
    assert page.evaluate("() => document.documentElement.scrollWidth<=innerWidth+1")
    assert page.evaluate("""()=>{
      const map=SN.planner.map,city=map.city;
      return map._points().every(p=>{
        const q=city.world(p[0],p[1]).project(city.camera);
        return Math.abs(q.x)<1 && Math.abs(q.y)<1;
      });
    }"""),"Fitted routes must remain inside the 3D map after resizing to mobile"
    page.screenshot(path="tests/integrated-web-mobile.png",full_page=True)
    assert not errors,errors
    print("PASS: responsive layout and no JavaScript errors",flush=True)
    browser.close()
