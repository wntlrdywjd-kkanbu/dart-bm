import json, sys
from playwright.sync_api import sync_playwright
URLS = ["https://m.stock.naver.com/domestic/stock/085910/research",
        "https://m.stock.naver.com/domestic/stock/085910/research/91065?domesticStockExchange=KRX"]
out = []
with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1", viewport={"width": 400, "height": 900})
    for u in URLS:
        pg = ctx.new_page()
        reqs = []
        def on_resp(r, reqs=reqs):
            if r.request.resource_type in ("xhr", "fetch", "document") or "pdf" in r.url.lower():
                try:
                    body = r.text()[:700] if "json" in (r.headers.get("content-type") or "") else ""
                except Exception:
                    body = ""
                reqs.append((r.status, r.request.resource_type, r.url, body))
        pg.on("response", on_resp)
        pg.goto(u, wait_until="networkidle", timeout=45000)
        pg.wait_for_timeout(2500)
        out.append("#### " + u)
        for s, t, url, body in reqs:
            if "research" in url or "pdf" in url.lower() or "front-api" in url:
                out.append(f"{s} {t} {url}\n    {body}")
        txt = pg.inner_text("body")[:1500]
        out.append("---- body text\n" + txt)
        links = pg.eval_on_selector_all("a", "els=>els.map(e=>e.href).filter(h=>/pdf|research|file|download/i.test(h)).slice(0,15)")
        out.append("---- links\n" + "\n".join(links))
        pg.close()
    b.close()
open("out/_naver_net.txt", "w", encoding="utf-8").write("\n".join(out))
