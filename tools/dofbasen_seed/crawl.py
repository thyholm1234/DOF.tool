"""Henter sider fra dofbasen.dk med en rigtig browser (Playwright/Firefox): screenshot, HTML, tekst og links pr. side.

Brug:  python crawl.py <udmappe> https://dofbasen.dk/ https://dofbasen.dk/start.php …
Kræver: pip install playwright && playwright install firefox. Der går 2 sekunder mellem siderne.
"""
import sys, asyncio, re
from playwright.async_api import async_playwright
OUT = sys.argv[1]
urls = sys.argv[2:]
async def main():
    async with async_playwright() as p:
        b = await p.firefox.launch()
        pg = await b.new_page(viewport={"width": 1280, "height": 900}, user_agent="Mozilla/5.0 (X11; Linux x86_64; rv:140.0) Gecko/20100101 Firefox/140.0")
        for u in urls:
            name = re.sub(r"[^a-zA-Z0-9]+", "_", u.replace("https://dofbasen.dk", ""))[:80] or "home"
            try:
                await pg.goto(u, wait_until="load", timeout=45000)
                await pg.wait_for_timeout(1500)
                await pg.screenshot(path=f"{OUT}/shots/{name}.png", full_page=True)
                html = await pg.content()
                open(f"{OUT}/pages/{name}.html", "w").write(html)
                txt = await pg.inner_text("body")
                open(f"{OUT}/pages/{name}.txt", "w").write(txt)
                links = await pg.eval_on_selector_all("a[href]", "els => els.map(e => e.href + ' | ' + e.innerText.trim().slice(0,60))")
                open(f"{OUT}/pages/{name}.links", "w").write("\n".join(links))
                print("OK", u, name, len(txt))
            except Exception as e:
                print("ERR", u, e)
            await asyncio.sleep(2)
        await b.close()
asyncio.run(main())
