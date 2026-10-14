"""out/{code}/ 결과물 → 대시보드용 압축 JSON(out/{code}/dash.json, 256KiB 미만). 사용법: python build_dash_data.py 085910 [...]"""
import json, os, re, sys, glob

OUT = "out"
SECS = {"s2": r"## 2\. ", "s21": r"## 2-1\. ", "s3": r"## 3\. ", "s4": r"## 4\. ", "s5": r"## 5\. ",
        "s52": r"## 5-2\. ", "s6": r"## 6\. ", "s62": r"## 6-2\. "}


def sections(card):
    parts = re.split(r"(?m)^(?=## )", card)
    out = {}
    for p in parts:
        for k, pat in SECS.items():
            if re.match(pat, p):
                out[k] = p.split("\n", 1)[1].strip() if "\n" in p else ""
    return out


def series(q, key):
    return [x["values"].get(key) for x in q]


def build(code):
    base = os.path.join(OUT, code)
    d = json.load(open(os.path.join(base, "fin.json"), encoding="utf-8"))
    meta, fin = d["meta"], d["fin"]
    card = open(os.path.join(base, "bm_card.md"), encoding="utf-8").read()
    it = fin["items"]
    y = fin["year"]

    def ann(label):
        v = it.get(label)
        return [v and v.get("전전기"), v and v.get("전기"), v and v.get("당기")] if v else [None, None, None]

    annual = {"years": [f"FY{y-2}", f"FY{y-1}", f"FY{y}"]}
    for k in ["매출액", "영업이익", "당기순이익", "매출총이익", "자산총계", "부채총계", "자본총계", "유동자산", "유동부채",
              "현금및현금성자산", "재고자산", "매출채권", "영업활동현금흐름", "투자활동현금흐름", "재무활동현금흐름"]:
        annual[k] = ann(k)
    qs = list(reversed(fin.get("quarters") or []))  # 과거 → 최신
    quarters = {"labels": [x["label"] for x in qs], "check": [x["label"] for x in qs if x.get("check")]}
    for k in ["매출액", "영업이익", "당기순이익"]:
        quarters[k] = series(qs, k)
    orders = {}
    try:
        orders = json.load(open(os.path.join(base, "orders.json"), encoding="utf-8"))
    except OSError:
        pass
    secs = sections(card)
    # 한 줄 정의
    m = re.search(r"## 1\. .*?\n(.+?)\n\n", card, re.S)
    doc = {
        "code": code, "name": meta["corp_name"], "generated_at": meta["generated_at"],
        "report": meta["report_nm"], "report_dt": meta["rcept_dt"],
        "latest": meta["latest_report_nm"], "latest_dt": meta["latest_rcept_dt"],
        "basis": fin["basis"], "one_line": m.group(1).strip() if m else "",
        "annual": annual, "quarters": quarters, "yoy_latest": fin.get("yoy_latest") or {},
        "secs": secs, "orders": orders,
    }
    try:  # Claude가 쓴 해석 메모(있으면 대시보드에 표시)
        doc["notes"] = json.load(open(os.path.join(base, "notes.json"), encoding="utf-8"))
    except OSError:
        pass
    s = json.dumps(doc, ensure_ascii=False)
    assert len(s.encode("utf-8")) < 240_000, f"{code}: {len(s)} bytes"
    json.dump(doc, open(os.path.join(base, "dash.json"), "w", encoding="utf-8"), ensure_ascii=False)
    return len(s.encode("utf-8"))


if __name__ == "__main__":
    codes = sys.argv[1:] or [os.path.basename(p) for p in glob.glob(os.path.join(OUT, "*")) if os.path.isdir(p)]
    for c in codes:
        print(c, build(c), "bytes")
