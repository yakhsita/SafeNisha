"""Manual Chrome smoke check. Requires playwright and installed Chrome; run against localhost:8502."""
from playwright.sync_api import sync_playwright
import json
with sync_playwright() as p:
    browser=p.chromium.launch(channel="chrome",headless=True)
    context=browser.new_context(permissions=["geolocation"],geolocation={"latitude":12.9352,"longitude":77.6245},viewport={"width":1440,"height":1100})
    page=context.new_page()
    page.on("pageerror",lambda e: print("PAGE ERROR",str(e)))
    page.goto("http://127.0.0.1:8502",wait_until="networkidle")
    page.wait_for_timeout(4000)
    print("BUTTONS",page.get_by_role("button").all_text_contents())
    def map_frame():
        return next(f for f in page.frames if "streamlit_folium" in f.url)
    def inspect(label):
        f=map_frame()
        print(label,f.evaluate("() => ({center:window.map.getCenter(),routes:Object.values(window.map._layers).filter(l => l instanceof L.Polyline).map(l=>({n:l.getLatLngs().length,color:l.options.color})),markers:Object.values(window.map._layers).filter(l=>l.getTooltip).map(l=>l.getTooltip()?.getContent()).filter(Boolean)})"))
        print("ERRORS",page.locator('[data-testid="stException"], [data-testid="stAlert"]').all_text_contents())
    inspect("INITIAL")
    map_frame().evaluate("() => window.map.fire('click',{latlng:L.latLng(12.9352,77.6245)})")
    page.wait_for_timeout(2500)
    print("START",page.get_by_role("textbox",name="Starting point",exact=True).input_value())
    map_frame().evaluate("() => window.map.fire('click',{latlng:L.latLng(12.9400,77.6200)})")
    page.wait_for_timeout(12000)
    inspect("AFTER ENDPOINTS")
    assert map_frame().evaluate("() => Object.values(window.map._layers).some(l => l instanceof L.Polyline)")
    print("DEST",page.get_by_role("textbox",name="Destination",exact=True).input_value())
    # Choose an actual middle point of the displayed route.
    point=map_frame().evaluate("() => {const l=Object.values(window.map._layers).find(l=>l instanceof L.Polyline && l.options.color==='#18a558'); if(!l)return null; const ps=l.getLatLngs(); return ps[Math.floor(ps.length/2)];}")
    if point:
        page.get_by_role('button',name='\u26a0',exact=True).filter(visible=True).click()
        page.wait_for_timeout(1500)
        map_frame().evaluate("p => window.map.fire('click',{latlng:L.latLng(p.lat,p.lng)})",point)
        page.wait_for_timeout(20000)
        inspect("AFTER HAZARD")
        assert map_frame().evaluate("() => {const l=Object.values(window.map._layers).find(l=>l instanceof L.Polyline && l.options.color==='#18a558'); const ps=l.getLatLngs().map(p=>p.lat.toFixed(6)+','+p.lng.toFixed(6));return new Set(ps).size===ps.length;}")
        assert map_frame().evaluate("() => Object.values(window.map._layers).some(l => l.getTooltip?.()?.getContent()?.includes('Take the hazard detour'))")
    page.screenshot(path="tests/map-browser-check.png",full_page=True)
    browser.close()
