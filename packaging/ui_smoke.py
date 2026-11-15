"""Optional end-to-end UI check in a real headless Chromium (needs: pip install playwright && playwright install chromium).

Starts `vaultmgr serve --demo`, then drives the page: open the demo, hand-edit a header, accept it, apply it, undo it,
and visit every tab. Fails on any console error or page error.   Usage: python packaging/ui_smoke.py
"""
import asyncio, os, socket, subprocess, sys, tempfile, time

from playwright.async_api import async_playwright


async def main(port: int) -> list[str]:
    errs: list[str] = []
    async with async_playwright() as pw:
        b = await pw.chromium.launch()
        pg = await b.new_page(viewport={"width": 1360, "height": 860})
        pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
        pg.on("pageerror", lambda e: errs.append(str(e)))
        await pg.goto(f"http://127.0.0.1:{port}/")
        await pg.click("text=Try the demo vault")
        await pg.wait_for_selector(".ws:not([disabled])")
        await pg.click(".ws >> nth=0")
        await pg.wait_for_selector(".shell")
        await pg.wait_for_selector(".dt-row")
        await pg.click(".dt-row >> nth=0")
        await pg.keyboard.press("e")
        await pg.wait_for_selector(".modal")
        await (await pg.query_selector_all(".modal input"))[0].fill("Smoke MOC")
        await pg.click(".modal button.primary")
        await pg.keyboard.press("5")
        await pg.click(".view:not([hidden]) .dt-row >> nth=0")
        await pg.keyboard.press("y")
        await pg.click("button:has-text('Apply 1')")
        await pg.wait_for_selector(".modal")
        await pg.click(".modal button.primary")
        await pg.wait_for_selector("text=Applied 1 change")
        await pg.click(".view:not([hidden]) button:has-text('Undo')")
        await pg.wait_for_selector(".modal")
        await pg.click(".modal button.danger")
        await pg.wait_for_selector("text=Undone")
        for k in "12346789":
            await pg.evaluate("document.activeElement && document.activeElement.blur()")
            await pg.keyboard.press(k)
            await pg.wait_for_timeout(400)
        await b.close()
    return errs


if __name__ == "__main__":
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    env = dict(os.environ, VAULTMGR_HOME=tempfile.mkdtemp())
    srv = subprocess.Popen([sys.executable, "-m", "vaultweb", "serve", "--port", str(port)], env=env)
    try:
        time.sleep(2.5)
        errors = asyncio.run(main(port))
    finally:
        srv.terminate()
    print("UI smoke:", "FAILED " + repr(errors) if errors else "OK")
    sys.exit(1 if errors else 0)
