"""네이버 증권 리서치(종목분석) 수집.
- 공개 저장소(out/{code}/research.json): 메타(제목·증권사·날짜·의견·목표가·링크)만
- 비공개 저장소(private/research/{code}/*.txt): PDF 본문 텍스트 (공개 금지, 저작권)
요청 사이에 무작위 지연(2~6초)을 둔다. 이미 받은 리포트는 건너뛴다.
사용: python research.py 003160 [085910 ...]   (환경: PRIVATE_DIR=비공개 저장소 체크아웃 경로)"""
import html, json, os, random, re, subprocess, sys, time
import urllib.request, urllib.parse

UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
API = "https://m.stock.naver.com/front-api/research"
MAXN = int(os.environ.get("RESEARCH_N", "10"))
OUT = os.environ.get("OUT_DIR", "out")
PRIV = os.environ.get("PRIVATE_DIR", "")


def pause(a=2.0, b=6.0):
    time.sleep(random.uniform(a, b))


def get(url, raw=False, tries=3):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": "https://m.stock.naver.com/"})
            with urllib.request.urlopen(req, timeout=40) as r:
                b = r.read()
            return b if raw else json.loads(b.decode("utf-8"))
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(random.uniform(4, 9) * (i + 1))
    raise RuntimeError(f"요청 실패 {url}: {last}")


def strip_html(s):
    s = re.sub(r"<br\s*/?>|</p>", "\n", s or "")
    return html.unescape(re.sub(r"<[^>]+>", "", s)).strip()


def pdf_text(b):
    p = "/tmp/_r.pdf"
    open(p, "wb").write(b)
    try:
        r = subprocess.run(["pdftotext", "-layout", p, "-"], capture_output=True, timeout=120)
        t = r.stdout.decode("utf-8", "ignore")
        if len(t.strip()) > 200:
            return t
    except Exception:  # noqa: BLE001
        pass
    try:
        from pypdf import PdfReader
        return "\n".join((pg.extract_text() or "") for pg in PdfReader(p).pages)
    except Exception:  # noqa: BLE001
        return ""


def one(code):
    lst = get(f"{API}/stockList?itemCode={code}&pageSize=30&page=1").get("result") or []
    lst = [x for x in lst if x.get("itemCode") == code][:MAXN]
    print(f"{code}: 목록 {len(lst)}건")
    pub_fp = os.path.join(OUT, code, "research.json")
    old = {}
    if os.path.exists(pub_fp):
        try:
            old = {m["id"]: m for m in json.load(open(pub_fp, encoding="utf-8")).get("items", [])}
        except Exception:  # noqa: BLE001
            old = {}
    pdir = os.path.join(PRIV, "research", code) if PRIV else ""
    if pdir:
        os.makedirs(pdir, exist_ok=True)
    items = {}
    for x in lst:
        rid = x["researchId"]
        have_txt = pdir and any(f.endswith(f"_{rid}.txt") for f in os.listdir(pdir))
        if rid in old and (have_txt or not pdir):
            items[rid] = old[rid]
            continue
        pause()
        d = get(f"{API}/stockEnd?itemCode={code}&researchId={rid}")["result"]["researchContent"]
        meta = {"id": rid, "date": d.get("writeDate"), "broker": d.get("brokerName"), "title": d.get("title"),
                "opinion": d.get("opinion"), "goal": d.get("goalPrice"), "prev_goal": d.get("prevGoalPrice"),
                "price": d.get("priceAtWriteDate"), "reads": d.get("readCount"),
                "link": f"https://m.stock.naver.com/domestic/stock/{code}/research/{rid}?domesticStockExchange=KRX"}
        items[rid] = meta
        if pdir:
            body = ""
            url = d.get("attachUrl")
            if url:
                pause()
                try:
                    body = pdf_text(get(url, raw=True))
                except Exception as e:  # noqa: BLE001
                    print(f"  PDF 실패 {rid}: {e}", file=sys.stderr)
            head = (f"[{meta['date']}] {meta['broker']} · {meta['title']}\n의견 {meta['opinion']} / 목표가 {meta['goal']} "
                    f"(직전 {meta['prev_goal']}) / 작성일 주가 {meta['price']}\n{meta['link']}\n\n--- 네이버 요약 ---\n"
                    f"{strip_html(d.get('content'))}\n\n--- PDF 본문 ---\n")
            fn = f"{(meta['date'] or '0000').replace('-', '')}_{re.sub(r'[^0-9A-Za-z가-힣]', '', meta['broker'] or '')}_{rid}.txt"
            open(os.path.join(pdir, fn), "w", encoding="utf-8").write(head + body)
        print(f"  + {meta['date']} {meta['broker']} {meta['title'][:30]}")
    out = sorted(items.values(), key=lambda m: m["date"] or "", reverse=True)
    os.makedirs(os.path.dirname(pub_fp), exist_ok=True)
    json.dump({"code": code, "updated": time.strftime("%Y-%m-%d"), "items": out}, open(pub_fp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    for i, c in enumerate(sys.argv[1:]):
        if i:
            pause(5, 12)
        try:
            one(c)
        except Exception as e:  # noqa: BLE001
            print(f"[경고] {c}: {e}", file=sys.stderr)
