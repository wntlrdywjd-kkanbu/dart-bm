#!/usr/bin/env python3
"""신규상장 직후(정기보고서는 첫 분기보고서뿐) 종목용: 투자설명서(별도 4개 기간: 반기·FY25·FY24·FY23, 단위 원)와
첫 분기보고서(3분기: 3개월·누적, 별도)를 합쳐 out/{code}/fin.json 을 만든다. 영광와이케이엠씨(345290)에서 처음 사용.
사용: python tools/prospectus_to_fin_3q.py 345290 "투자설명서.txt" "분기보고서.txt"
검증: 자산=부채+자본, 매출−원가=총이익, 제품표 합계=매출, 수출+내수=매출, 수주잔고 항목 합계=잔고.
1H25(전년 반기)는 투자설명서 손익계산서에 열이 없어 '전년 9개월 누적 − 전년 3분기 3개월'로 계산한다(how=직접, check 에 표기)."""
import json, os, re, sys, datetime
code, f1, f2 = sys.argv[1:4]
base = os.path.join("out", code)
L1 = open(os.path.join(base, "prospectus", f1), encoding="utf-8").read().split("\n")
L2 = open(os.path.join(base, "prospectus", f2), encoding="utf-8").read().split("\n")

def num(s):
    s = s.strip().replace(",", "")
    m = re.fullmatch(r"\(?(-?[\d.]+)\)?", s)
    if not m: return None
    v = float(m.group(1)); return -v if s.startswith("(") else v
def sec(L, pat, after=0):
    for i, ln in enumerate(L):
        if i >= after and re.match(pat, ln.strip()): return i + 1
    raise SystemExit("섹션 없음: " + pat)
def rows(L, a, b):
    return [[c.strip() for c in ln.strip().strip("|").split("|")] for ln in L[a - 1:b] if ln.startswith("|")]
def clean(l): return re.sub(r"^[\sⅠ-Ⅹ IVX0-9\.\(\)]+", "", re.sub(r"\s+", "", l))
def find(rs, label, nth=0):
    hit = [r for r in rs if clean(r[0]).startswith(clean(label))]
    return hit[nth] if len(hit) > nth else None
def vals(r, a, n): return [None if num(c) is None else num(c) for c in r[a:a + n]] if r else [None] * n

# ---- 투자설명서: BS/IS (열: 과목,주석,1H26,FY25,FY24,FY23) CF (열: 구분,1H26,FY25,FY24,FY23)
bs_a = sec(L1, r"\| 재 무 상 태 표 \|"); is_a = sec(L1, r"\| 반 기 포 괄 손 익 계 산 서 \|"); eq_a = sec(L1, r"\| 반 기 자 본 변 동 표 \|")
cf_a = sec(L1, r"\| 반 기 현 금 흐 름 표 \|"); notes_a = sec(L1, r"5\. 재무제표 주석")
BS1, IS1, CF1 = rows(L1, bs_a, is_a - 1), rows(L1, is_a, eq_a - 1), rows(L1, cf_a, notes_a - 1)
bsmap = {"자산총계": "자산총계", "부채총계": "부채총계", "자본총계": "자본총계", "유동자산": "유동자산", "비유동자산": "비유동자산", "유동부채": "유동부채",
         "비유동부채": "비유동부채", "현금및현금성자산": "현금및현금성자산", "재고자산": "재고자산", "매출채권": "매출채권및기타채권", "이익잉여금": "이익잉여금", "자본금": "자본금"}
bs = {k: vals(find(BS1, v), 2, 4) for k, v in bsmap.items()}
ismap = {"매출액": "매출액", "매출원가": "매출원가", "매출총이익": "매출총이익", "판관비": "판매비와관리비", "영업이익": "영업이익", "당기순이익": "당기순이익"}
inc = {k: vals(find(IS1, v), 2, 4) for k, v in ismap.items()}
cfmap = {"영업활동현금흐름": "영업활동으로 인한 현금흐름", "투자활동현금흐름": "투자활동으로 인한 현금흐름", "재무활동현금흐름": "재무활동으로 인한 현금흐름",
         "기초현금": "기초의 현금및현금성자산", "기말현금": "기말의 현금및현금성자산"}
cf = {k: vals(find(CF1, v), 1, 4) for k, v in cfmap.items()}
for i in range(4):
    a, l, e = bs["자산총계"][i], bs["부채총계"][i], bs["자본총계"][i]; assert abs(a - l - e) <= 1000, ("BS", i)
    r_, c_, g_ = inc["매출액"][i], inc["매출원가"][i], inc["매출총이익"][i]; assert abs(r_ - c_ - g_) <= 1000, ("IS", i)

# ---- 분기보고서: BS(3Q26, FY25) / IS(3개월26, 누적26, 3개월25, 누적25) / CF(9M26, 9M25)
qb = sec(L2, r"4-1\. 재무상태표"); qi = sec(L2, r"4-2\. 포괄손익계산서"); qe = sec(L2, r"4-3\. 자본변동표"); qc = sec(L2, r"4-4\. 현금흐름표"); qn = sec(L2, r"5\. 재무제표 주석")
BS2, IS2, CF2 = rows(L2, qb, qi - 1), rows(L2, qi, qe - 1), rows(L2, qc, qn - 1)
b2map = {"자산총계": "자산총계", "부채총계": "부채총계", "자본총계": "자본총계", "유동자산": "유동자산", "비유동자산": "비유동자산", "유동부채": "유동부채",
         "비유동부채": "비유동부채", "현금및현금성자산": "현금및현금성자산", "재고자산": "유동재고자산", "매출채권": "매출채권및기타유동채권", "이익잉여금": "이익잉여금", "자본금": "자본금"}
bs2 = {k: vals(find(BS2, v), 1, 2) for k, v in b2map.items()}
i2map = {"매출액": "매출액", "매출원가": "매출원가", "매출총이익": "매출총이익", "판관비": "판매비와관리비", "영업이익": "영업이익", "당기순이익": "당기순이익"}
inc2 = {k: vals(find(IS2, v), 1, 4) for k, v in i2map.items()}   # [3M26, 9M26, 3M25, 9M25]
c2map = {"영업활동현금흐름": "영업활동현금흐름", "투자활동현금흐름": "투자활동현금흐름", "재무활동현금흐름": "재무활동현금흐름", "기초현금": "기초현금및현금성자산", "기말현금": "기말현금및현금성자산"}
cf2 = {k: vals(find(CF2, v), 1, 2) for k, v in c2map.items()}   # [9M26, 9M25]
for i in range(2):
    a, l, e = bs2["자산총계"][i], bs2["부채총계"][i], bs2["자본총계"][i]; assert abs(a - l - e) <= 1000, ("BS2", i)
for i in range(4):
    r_, c_, g_ = inc2["매출액"][i], inc2["매출원가"][i], inc2["매출총이익"][i]; assert abs(r_ - c_ - g_) <= 1000, ("IS2", i)
assert bs2["자산총계"][1] == bs["자산총계"][1], "FY25 BS 불일치(분기보고서 vs 투자설명서)"
# 1H25 = 9M25 − 3M25 (손익)
h125 = {k: inc2[k][3] - inc2[k][2] for k in inc2}
# 1H26 검증: 9M26 − 3M26 = 1H26
for k in ("매출액", "영업이익", "당기순이익"):
    assert abs(inc2[k][1] - inc2[k][0] - inc[k][0]) <= 1000, ("1H26 대조", k, inc2[k][1] - inc2[k][0], inc[k][0])

# ---- 제품별 매출, 국내/수출
pa = sec(L1, r"나\. 주요 제품등의 매출 현황"); pb = sec(L1, r"다\. 주요 제품 등의 가격변동 추이", pa)
seg = {}; tot = None
for r in rows(L1, pa, pb - 1):
    if len(r) == 9 and num(r[1]) is not None and r[0] not in ("구분", "매출액"):
        v = [num(r[1]) * 1e3, num(r[3]) * 1e3, num(r[5]) * 1e3, num(r[7]) * 1e3]
        if r[0] == "합계": tot = v
        else: seg[r[0].replace(" ", "")] = v
for i in range(4):
    assert abs(sum(v[i] for v in seg.values()) - tot[i]) <= 3e3, ("seg", i)
    assert abs(tot[i] - inc["매출액"][i]) <= 3e3, ("seg vs 매출", i)
ma = sec(L1, r"가\. 매출현황"); mb = sec(L1, r"나\. 판매경로 및 방법", ma)
dom = exp = None
for r in rows(L1, ma, mb - 1):
    if r[0] == "합 계" and r[1] == "수 출": exp = [num(c) * 1e3 for c in r[2:6]]
    if r[0] == "내 수" and exp is not None and dom is None and len(r) == 5 and num(r[1]) and abs((num(r[1]) + exp[0] / 1e3) - tot[0] / 1e3) < 3: dom = [num(c) * 1e3 for c in r[1:5]]
assert dom and exp
for i in range(4): assert abs(dom[i] + exp[i] - inc["매출액"][i]) <= 3e3, ("수출+내수", i)

# ---- 생산능력(가공군/표면처리군): 열 (구분,구분,1H26 수량,금액,FY25,...)
ca = sec(L1, r"\(1\) 생산능력, 생산실적, 가동률"); cb = sec(L1, r"\(2\) 생산설비 현황", ca)
capa = {}; cur = None
for r in rows(L1, ca, cb - 1):
    if r[0] in ("가공군", "표면처리군") and r[1] == "생산능력": cur = r[0]; capa[cur] = {"capa": [num(r[2]), num(r[4]), num(r[6]), num(r[8])]}
    elif cur and r[0] == "생산실적": capa[cur]["prod"] = [num(r[1]), num(r[3]), num(r[5]), num(r[7])]
    elif cur and r[0] == "가동율": capa[cur]["util"] = [float(c.strip("%")) for c in r[1:5]]

# ---- 수주잔고(백만원)
oa = sec(L1, r"라\. 수주상황"); ob = sec(L1, r"5\. 위험관리 및 파생거래", oa)
ind = None; ob_items = {}; otot = None
for r in rows(L1, oa, ob - 1):
    if r[0] in ("반도체", "디스플레이", "표면처리"): ind = r[0]
    if r[0] == "유리성형설비": ind = "유리성형설비"
    if r[0] == "합계": otot = [num(c) for c in r[1:7]]; continue
    if ind and len(r) >= 6 and num(r[-1]) is not None and r[0] != "산업군":
        ob_items[ind] = ob_items.get(ind, 0) + num(r[-1])
assert otot and abs(sum(ob_items.values()) - otot[5]) <= 3, ("수주잔고 합", sum(ob_items.values()), otot)

# ---- 조립
KEYS = ["2026.09", "2026.06", "2025.12", "2024.12", "2023.12"]
KIND = {"2026.09": "분기", "2026.06": "반기", "2025.12": "사업", "2024.12": "사업", "2023.12": "사업"}
P = {}
def mk(d, **kw): return {**{"당기": None, "전기": None, "당기누적": None, "전기누적": None, "전전기": None}, **d, **kw}
# 2026.09 (3Q): BS 3Q말/전기말, IS 3개월·누적, CF 9M
p = {}
for nm, v in bs2.items(): p[nm] = mk({"당기": v[0], "전기": v[1]}, account_nm=nm)
for nm, v in inc2.items(): p[nm] = mk({"당기": v[0], "전기": v[2], "당기누적": v[1], "전기누적": v[3]}, account_nm=nm)
for nm, v in cf2.items(): p[nm] = mk({"당기": v[0], "당기누적": v[0], "전기누적": v[1]}, account_nm=nm)
P["2026.09"] = p
p = {}
for nm, v in bs.items(): p[nm] = mk({"당기": v[0]}, account_nm=nm)
for nm, v in inc.items(): p[nm] = mk({"당기": v[0], "당기누적": v[0], "전기누적": h125[nm]}, account_nm=nm)
for nm, v in cf.items(): p[nm] = mk({"당기": v[0], "당기누적": v[0]}, account_nm=nm)
P["2026.06"] = p
for j, k in ((1, "2025.12"), (2, "2024.12"), (3, "2023.12")):
    p = {}
    for nm, v in bs.items(): p[nm] = mk({"당기": v[j]}, account_nm=nm)
    for nm, v in inc.items(): p[nm] = mk({"당기": v[j]}, account_nm=nm)
    for nm, v in cf.items(): p[nm] = mk({"당기": v[j]}, account_nm=nm)
    P[k] = p
p = {}
for nm, v in inc.items(): p[nm] = mk({"당기": h125[nm], "당기누적": h125[nm]}, account_nm=nm)
P["2025.06"] = p
# 현금흐름 세부(_cf): 투자설명서 열(0=1H26,1=FY25,2=FY24,3=FY23), 분기보고서 9M
def cfrows1(i):
    out = []
    for r in CF1:
        v = num(r[1 + i]) if len(r) > 1 + i else None
        if v is not None and r[0]: out.append([r[0], v])
    return out
cf2rows = []
for r in CF2:
    v = num(r[1]) if len(r) > 1 else None
    if v is not None and r[0]: cf2rows.append([r[0], v])
for k, i in (("2026.06", 0), ("2025.12", 1), ("2024.12", 2)): P[k]["_cf"] = cfrows1(i)
P["2026.09"]["_cf"] = cf2rows
items = {}
for nm in list(bs) + list(inc) + list(cf):
    if nm in bs: v = bs[nm]; c_, p_, pp_ = v[1], v[2], v[3]
    elif nm in inc: v = inc[nm]; c_, p_, pp_ = v[1], v[2], v[3]
    else: v = cf[nm]; c_, p_, pp_ = v[1], v[2], v[3]
    items[nm] = {"당기": c_, "전기": p_, "당기누적": None, "전기누적": None, "전전기": pp_, "account_nm": nm}
items["_cf"] = cfrows1(1)
# 분기 시계열: 3분기 3개월만 단독값, 반기는 누적(h1only 경로)
q = []
v3 = {nm: inc2[nm][0] for nm in inc2}
q.append({"key": "2026.09", "label": "26.3Q", "values": v3, "how": "직접", "check": ""})
q.append({"key": "2026.06", "label": "26.2Q", "values": {nm: inc[nm][0] for nm in inc}, "how": "직접", "check": ""})
q.append({"key": "2025.09", "label": "25.3Q", "values": {nm: inc2[nm][2] for nm in inc2}, "how": "직접", "check": ""})
q.append({"key": "2025.06", "label": "25.2Q", "values": dict(h125), "how": "직접", "check": "9M−3M 차감"})
yoy = {nm: round((inc2[nm][0] / inc2[nm][2] - 1) * 100, 1) for nm in ("매출액", "영업이익", "당기순이익") if inc2[nm][2]}
KI = ["2026.06", "2025.12", "2024.12", "2023.12"]
segments = sorted([{"key": k, "kind": KIND[k], "items": {nm: v[j] for nm, v in seg.items() if v[j]}} for j, k in enumerate(KI)], key=lambda x: x["key"])
region_hist = sorted([{"key": k, "kind": KIND[k], "dom": dom[j], "exp": exp[j]} for j, k in enumerate(KI)], key=lambda x: x["key"])
capa_hist = sorted([{"key": k, "kind": KIND[k], "groups": {nm: {"unit": "개", "capa": d["capa"][j], "prod": d["prod"][j], "util": d["util"][j]} for nm, d in capa.items()}} for j, k in enumerate(KI)], key=lambda x: x["key"])
ltm = {nm: inc[nm][1] + inc2[nm][1] - h125[nm] - 0 for nm in ("매출액", "영업이익", "당기순이익")}
# LTM(3Q) = FY25 + 9M26 − 9M25
ltm = {nm: inc[nm][1] + inc2[nm][1] - inc2[nm][3] for nm in ("매출액", "영업이익", "당기순이익")}
orders_hist = [{"unit": "백만원", "total": otot[3], "deliv": otot[4], "backlog": otot[5], "items": ob_items, "krw_backlog": None, "contracts": [], "key": "2026.09", "kind": "투자설명서"}]
fin = {"basis": "별도", "year": 2025, "unit": "원", "items": items, "quarters": q, "yoy_latest": yoy, "latest_period": "2026.09", "periods_raw": P,
       "dividend": {}, "capex_notices": [], "region_hist": region_hist, "capa_hist": capa_hist, "cost_files": ["__none__.json"], "ltm": ltm,
       "orders_hist": orders_hist, "segments": segments, "insider": [], "contracts": []}
rc2 = re.search(r"(\d{14})", f2).group(1); rc1 = re.search(r"(\d{14})", f1).group(1)
meta = {"corp_name": "영광와이케이엠씨", "stock_code": code, "corp_code": "00960234",
        "report_nm": "투자설명서(기재정정) 2026.11.03 — 정기보고서 없음(신규상장)", "rcept_no": rc1, "rcept_dt": rc1[:8],
        "latest_report_nm": "분기보고서 (2026.09) — 상장 후 첫 정기보고서", "latest_rcept_no": rc2, "latest_rcept_dt": rc2[:8],
        "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), "source": "prospectus"}
json.dump({"meta": meta, "fin": fin}, open(os.path.join(base, "fin.json"), "w", encoding="utf-8"), ensure_ascii=False)
print("fin.json OK", {k: round(v[0] / 1e8, 1) for k, v in inc2.items()}, yoy, "LTM", {k: round(v / 1e8, 1) for k, v in ltm.items()}, "수주잔고", ob_items, otot)
