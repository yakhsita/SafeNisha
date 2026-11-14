"""Verify the screenshot route against the isolated server on port 8503."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from playwright.sync_api import sync_playwright
from core.synthetic_routing import point_risk

with sync_playwright() as p:
    browser=p.chromium.launch(channel="chrome",headless=True,args=["--use-angle=swiftshader","--enable-unsafe-swiftshader"])
    context=browser.new_context(viewport={"width":1440,"height":1100},permissions=["geolocation"],
        geolocation={"latitude":12.964514,"longitude":77.505742})
    page=context.new_page();errors=[]
    page.on("pageerror",lambda e:errors.append(str(e)))
    page.goto("http://127.0.0.1:8503",wait_until="networkidle")
    page.wait_for_function("()=>SN.planner.map.city",timeout=60000)
    page.locator("#beta").evaluate("el=>{el.value=0;el.dispatchEvent(new Event('input',{bubbles:true}));}")
    page.locator("#src").fill("12.964514, 77.505742")
    page.locator("#dst").fill("12.968824, 77.477563")
    page.wait_for_function("()=>SN.planner.scene.routes&&!SN.planner.scene.loading",timeout=90000)
    initial=page.evaluate("()=>SN.planner.scene.routes")
    assert initial["shortest"]["path"]==initial["lower_risk"]["path"]
    with page.expect_response(lambda r:r.url.endswith("/api/routes") and r.request.post_data_json.get("beta")==5,timeout=120000):
        page.locator("#beta").evaluate("el=>{el.value=5;el.dispatchEvent(new Event('input',{bubbles:true}));}")
    page.wait_for_function("()=>!SN.planner.scene.loading",timeout=120000)
    result=page.evaluate("()=>SN.planner.scene.routes")
    short,preferred=result["shortest"],result["lower_risk"]
    assert preferred["path"]!=short["path"]
    assert preferred["risk_indicator"]<short["risk_indicator"]-0.02
    assert result["candidate_count"]>1
    rendering=page.evaluate("""()=>{
      const map=SN.planner.map,city=map.city,low=city.routeObjs.find(r=>r.id==='low');
      const a=low.geometry.attributes.instanceColorStart;
      const colours=new Set(Array.from({length:a.count},(_,i)=>[a.getX(i),a.getY(i),a.getZ(i)].join(',')));
      return {colours:colours.size,painted:low.lines.every(l=>l.material.vertexColors),
        preferred:city.state.routes.find(r=>r.id==='low').coords,
        shortest:city.state.routes.find(r=>r.id==='short').coords};
    }""")
    assert rendering["colours"]>3 and rendering["painted"]
    # The display consumes the server's risk pieces, without generating its own route.
    expected=[]
    for piece in preferred["risk_paths"]:
        for pt in piece["path"]:
            if not expected or expected[-1]!=pt:expected.append(pt)
    assert rendering["preferred"]==expected
    assert rendering["shortest"]==short["path"]
    assert max(short["segment_risk"])-min(short["segment_risk"])>0.1
    zones=result["synthetic_zones"]
    for zone in zones:
        w,s,e,n=zone["bounds"];lat,lon=(s+n)/2,(w+e)/2
        browser_risk=page.evaluate("p=>SN.syntheticRiskAt(p.lat,p.lon,p.zones,p.definition)",
                     {"lat":lat,"lon":lon,"zones":zones,"definition":result["risk_field"]})
        assert abs(browser_risk-point_risk(lat,lon,zones))<1e-10
    page.wait_for_timeout(600)
    page.screenshot(path="tests/exact-route-plan.png",full_page=True)
    page.locator("#tab-atlas").click();page.wait_for_timeout(1500)
    assert page.locator("#atlas-map image.kde-heatmap").count()==1
    assert page.locator("#atlas-map .risk-segment").count()>0
    page.screenshot(path="tests/exact-route-atlas.png",full_page=True)
    assert not errors,errors
    print("PASS: exact screenshot route changes at beta=5; per-road colours vary; Python and heatmap field agree to 1e-10.")
    print("Compared",result["candidate_count"],"paths; shortest",round(short["distance_m"]),round(short["risk_indicator"],4),
          "preferred",round(preferred["distance_m"]),round(preferred["risk_indicator"],4))
    browser.close()
