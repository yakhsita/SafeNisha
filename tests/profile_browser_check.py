"""Check account UI against an isolated temporary database and local server."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from functools import partial
from http.server import ThreadingHTTPServer
import tempfile
from threading import Thread
from playwright.sync_api import sync_playwright
from core.accounts import Accounts
from core.journey_service import JourneyService
from hazards import database
from web_app import Handler


class QuietHandler(Handler):
    def log_message(self, *_args):
        pass


with tempfile.TemporaryDirectory() as directory:
    path = Path(directory) / "profile-browser.sqlite3"
    database.initialize_database(path)
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(QuietHandler,
        service=JourneyService(path), accounts=Accounts(path)))
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(channel="chrome", headless=True)
            context = browser.new_context()
            context.add_init_script("window.WebGL2RenderingContext=undefined; localStorage.setItem('sn.profile',JSON.stringify({name:'Untrusted preview'}));")
            page = context.new_page()
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            base = "http://127.0.0.1:{}".format(server.server_address[1])
            page.goto(base + "/#profile", wait_until="networkidle")
            assert page.locator("#auth-out").is_visible()
            page.locator('#auth-mode [data-v="up"]').click()
            page.locator("#auth-name").fill("Browser Walker")
            page.locator("#auth-email").fill("browser@example.com")
            page.locator("#auth-pass").fill("browser-password-234")
            page.locator("#auth-phone").fill("+91 98765 43210")
            page.locator("#auth-submit").click()
            page.locator("#auth-in").wait_for(state="visible")
            assert page.locator("#p-name").inner_text() == "Browser Walker"
            page.locator("#c-name").fill("Trusted Friend")
            page.locator("#c-phone").fill("+91 91234 56789")
            page.locator('#c-form [type="submit"]').click()
            page.locator("#c-list .contact").wait_for()
            with page.expect_response(lambda r: r.url.endswith("/api/profile")):
                page.locator("#p-beta").evaluate("el=>{el.value=4;el.dispatchEvent(new Event('input',{bubbles:true}));}")
            page.reload(wait_until="networkidle")
            page.locator("#auth-in").wait_for(state="visible")
            assert page.locator("#p-beta").input_value() == "4"
            assert "Trusted Friend" in page.locator("#c-list").inner_text()
            page.locator('[data-k="name"] .d-btn').click()
            page.locator("#ed-in-name").fill("Saved Walker")
            page.locator('#ed-name [type="submit"]').click()
            page.wait_for_function("()=>document.querySelector('#p-name').textContent==='Saved Walker'")
            assert page.evaluate("()=>localStorage.getItem('sn.profile')") is None
            assert next(c for c in context.cookies() if c["name"] == "sn_session")["httpOnly"]
            page.locator("#p-signout").click()
            page.locator("#auth-out").wait_for(state="visible")
            page.locator('#auth-mode [data-v="in"]').click()
            page.locator("#auth-email").fill("browser@example.com")
            page.locator("#auth-pass").fill("wrong-password")
            page.locator("#auth-submit").click()
            page.locator("#auth-error").wait_for(state="visible")
            assert "incorrect" in page.locator("#auth-error").inner_text()
            page.locator("#auth-pass").fill("browser-password-234")
            page.locator("#auth-submit").click()
            page.locator("#auth-in").wait_for(state="visible")
            assert page.locator("#p-name").inner_text() == "Saved Walker"
            assert "Trusted Friend" in page.locator("#c-list").inner_text()
            page.locator('#c-list [data-del="0"]').click()
            page.wait_for_function("()=>document.querySelectorAll('#c-list .contact').length===0")
            page.reload(wait_until="networkidle")
            page.locator("#auth-in").wait_for(state="visible")
            assert page.locator("#c-list .contact").count() == 0
            anonymous = browser.new_context()
            assert anonymous.request.get(base + "/api/auth/me").json()["profile"] is None
            assert not errors, errors
            browser.close()
            print("PASS: registration, wrong-password rejection, session restore, profile edits, contacts, preference persistence, logout, and separate browser sessions.")
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
