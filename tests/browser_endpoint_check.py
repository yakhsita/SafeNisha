"""Manual browser check for focus-based endpoint selection on localhost:8502."""
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    browser = p.chromium.launch(channel="chrome", headless=True)
    context = browser.new_context(permissions=["geolocation"],
        geolocation={"latitude":12.9352,"longitude":77.6245},
        viewport={"width":1440,"height":1100})
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto("http://127.0.0.1:8502", wait_until="networkidle")
    page.wait_for_timeout(3000)
    def frame():
        return next(f for f in page.frames if "streamlit_folium" in f.url)
    def click_map(lat, lng):
        frame().evaluate("p => window.map.fire('click',{latlng:L.latLng(p[0],p[1])})", [lat,lng])
        page.wait_for_timeout(2000)
    start = page.get_by_role("textbox", name="Starting point", exact=True)
    destination = page.get_by_role("textbox", name="Destination", exact=True)
    destination.click()
    page.wait_for_timeout(1500)
    click_map(12.9400,77.6200)
    assert destination.input_value() == "12.940000, 77.620000"
    assert start.input_value() == ""
    start.click()
    page.wait_for_timeout(1500)
    click_map(12.9352,77.6245)
    assert start.input_value() == "12.935200, 77.624500"
    assert destination.input_value() == "12.940000, 77.620000"
    page.wait_for_timeout(10000)
    assert frame().evaluate("() => Object.values(window.map._layers).some(l=>l instanceof L.Polyline)")
    assert page.get_by_text("Map click selects", exact=True).count() == 0
    input_bounds = start.bounding_box()
    map_bounds = page.locator('iframe').filter(visible=True).last.bounding_box()
    assert input_bounds["x"] + input_bounds["width"] < map_bounds["x"]
    assert not errors, errors
    print("PASS: field focus selects destination/start; map fills correct field; route draws; fields are left of map.")
    page.screenshot(path="tests/endpoint-browser-check.png",full_page=True)
    browser.close()
