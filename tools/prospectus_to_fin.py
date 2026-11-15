#!/usr/bin/env python3
"""신규상장 종목(정기보고서 없음)용: 투자설명서 원문 txt(tools/prospectus_fetch.py 결과)의 재무제표·사업의 내용 표를
읽어 out/{code}/fin.json (+ 최소 bm_card.md)을 만든다. 이후 build_dash_data.py 가 그대로 동작한다.
사용: python tools/prospectus_to_fin.py 0162D0 "20261030000114__기재정정_투자설명서.txt"
주의: 분기 단독값이 없어 quarters 는 반기 누적(1H26/1H25)만 담는다(build_dash_data h1only 경로). 별도재무제표 기준.
단위: 천원/백만원 → 원. 값은 표 그대로 읽고, 합계 검증(자산=부채+자본, 제품 합계=매출)을 assert 한다."""
import json, os, re, sys, datetime

code, fname = sys.argv[1], sys.argv[2]
base = os.path.join("out", code)
L = open(os.path.join(base, "prospectus", fname), encoding="utf-8").read().split("\n")


def num(s):
    s = s.strip().replace(",", "")
    m = re.fullmatch(r"\(?(-?[\d.]+)\)?", s)
    if not m:
        return None
    v = float(m.group(1))
    return -v if s.startswith("(") else v


def rows(a, b):
    out = []
    for ln in L[a - 1:b]:
        if ln.startswith("|"):
            out.append([c.strip() for c in ln.strip().strip("|").split("|")])
    return out


def clean(lbl):
    return re.sub(r"^[\sⅠ-Ⅹ IVX0-9\.\(\)]+", "", re.sub(r"\s+", "", lbl)).replace("　", "")


def find(rs, label, nth=0):
    hit = [r for r in rs if clean(r[0]).startswith(clean(label))]
    return hit[nth] if len(hit) > nth else None


def sec(title_pat, after=0):
    for i, ln in enumerate(L):
        if i >= after and re.match(title_pat, ln.strip()):
            return i + 1
    raise SystemExit("섹션 없음: " + title_pat)


# --- 구간 찾기 (재무상태표/손익계산서/현금흐름표/주석)
bs_a = sec(r"\| 재 무 상 태 표 \|"); is_a = sec(r"\| 포 괄 손 익 계 산 서 \|"); cf_a = sec(r"\| 현 금 흐 름 표 \|")
eq_a = sec(r"\| 자 본 변 동 표 \|"); notes_a = sec(r"5\. 재무제표 주석")
BS, IS, CF = rows(bs_a, eq_a - 1), rows(is_a, eq_a - 1)[0:0] + rows(is_a, eq_a - 1), rows(cf_a, notes_a - 1)
IS = rows(is_a, eq_a - 1)
K = 1000.0  # 천원 → 원
# BS 열: 1H26, FY25, FY24, FY23 / IS 열: 1H26, 1H25, FY25, FY24, FY23 / CF 열: 1H26, 1H25, FY25, FY24
def bsv(label, nth=0):
    r = find(BS, label, nth); return [None if num(c) is None else num(c) * K for c in r[1:5]] if r else [None] * 4
def isv(label, nth=0):
    r = find(IS, label, nth); return [None if num(c) is None else num(c) * K for c in r[1:6]] if r else [None] * 5
def cfv(label, nth=0):
    r = find(CF, label, nth); return [None if num(c) is None else num(c) * K for c in r[1:5]] if r else [None] * 4

bs = {"자산총계": bsv("자산총계"), "부채총계": bsv("부채총계"), "자본총계": bsv("자본총계"), "유동자산": bsv("유동자산"),
      "비유동자산": bsv("비유동자산"), "유동부채": bsv("유동부채"), "비유동부채": bsv("비유동부채"),
      "현금및현금성자산": bsv("현금및현금성자산"), "재고자산": bsv("재고자산"), "매출채권": bsv("매출채권및기타채권"),
      "이익잉여금": bsv("이익잉여금"), "자본금": bsv("자본금")}
inc = {"매출액": isv("매출액"), "매출원가": isv("매출원가"), "매출총이익": isv("매출총이익"), "판관비": isv("판매비와관리비"),
       "영업이익": isv("영업이익"), "당기순이익": isv("당기순이익")}
cf = {"영업활동현금흐름": cfv("영업활동으로 인한 현금흐름"), "투자활동현금흐름": cfv("투자활동으로 인한 현금흐름"),
      "재무활동현금흐름": cfv("재무활동으로 인한 현금흐름"), "기초현금": cfv("기초의 현금및현금성자산"), "기말현금": cfv("기말의 현금및현금성자산")}
for i in range(4):  # 합계 검증 (천원 단위 반올림 오차 3천원 허용)
    a, l, e = bs["자산총계"][i], bs["부채총계"][i], bs["자본총계"][i]
    assert abs(a - l - e) <= 3000, (i, a, l, e)
for i in range(5):
    r_, c_, g_ = inc["매출액"][i], inc["매출원가"][i], inc["매출총이익"][i]
    assert abs(r_ - c_ - g_) <= 3000, ("IS", i)

# --- 제품별 매출 (사업의 내용 > 주요 제품 현황), 기간 열: 1H26, FY25, FY24, FY23
pa = sec(r"가\. 주요 제품 현황"); pb = sec(r"나\. 주요 제품 설명", pa)
PR = rows(pa, pb - 1)
seg = {k: [None] * 4 for k in ("샤워헤드", "조립품", "챔버부품", "국가핵심산업부품", "스핀척", "히터", "기타", "용역")}
for r in PR:
    if len(r) >= 12 and r[0] == "반도체부품":
        nm = r[2]; vals = [num(r[4]), num(r[6]), num(r[8]), num(r[10])]
        seg[nm] = [v * 1e6 if v is not None else None for v in vals]
    elif len(r) >= 11 and r[0] == "기타" and r[1] == "용역매출":
        seg["용역"] = [num(r[3]) * 1e6 if num(r[3]) is not None else None, num(r[5]) * 1e6, num(r[7]) * 1e6, None]
    elif r[0] == "합계":
        tot = [num(r[1]) * 1e6, num(r[3]) * 1e6, num(r[5]) * 1e6, num(r[7]) * 1e6]
for i in range(4):
    s_ = sum((v[i] or 0) for v in seg.values())
    assert abs(s_ - tot[i]) <= 3e6, ("seg", i, s_, tot[i])
    assert abs(tot[i] - inc["매출액"][[0, 2, 3, 4][i]]) <= 3e6, ("seg vs 매출", i)  # 억 미만 반올림 허용

# --- 국내/수출 (매출실적 표의 합계 구분)
ra = sec(r"가\. 매출실적"); rb = sec(r"나\. 판매경로", ra)
RG = rows(ra, rb - 1)
dom = exp = None
for i_, r in enumerate(RG):
    if r[0] == "합계" and len(r) == 6 and r[1] == "국내":
        dom = [num(c) * 1e6 for c in r[2:6]]
        exp = [num(RG[i_ + 1][c]) * 1e6 for c in range(1, 5)]
assert dom and exp
# --- 생산능력·실적 (샤워헤드/히터/스핀척)
ca = sec(r"라\. 생산 능력 및 생산 실적"); cb = sec(r"마\. 생산설비의 현황", ca)
CP = rows(ca, cb - 1)
capa = {}
cur = None
for r in CP:
    if r[0] in ("샤워헤드", "히터", "스핀척") and len(r) >= 10:
        cur = r[0]; capa[cur] = {"capa": [num(r[2]), num(r[4]), num(r[6]), num(r[8])], "capa_amt": [num(r[3]), num(r[5]), num(r[7]), num(r[9])]}
    elif cur and r[0] == "생산실적":
        capa[cur]["prod"] = [num(r[1]), num(r[3]), num(r[5]), num(r[7])]
    elif cur and r[0] == "가동률":
        capa[cur]["util"] = [float(c.strip("%")) if c not in ("-", "") else None for c in (r[2], r[4], r[6], r[8])]

# --- 조립
KEYS = ["2026.06", "2025.12", "2024.12", "2023.12"]
KIND = {"2026.06": "반기", "2025.12": "사업", "2024.12": "사업", "2023.12": "사업"}
ISIDX = {"2026.06": 0, "2025.12": 2, "2024.12": 3, "2023.12": 4}
CFIDX = {"2026.06": 0, "2025.12": 2, "2024.12": 3, "2023.12": None}
periods_raw = {}
for j, k in enumerate(KEYS):
    p = {}
    for nm, v in bs.items():
        p[nm] = {"당기": v[j], "전기": None, "당기누적": None, "전기누적": None, "전전기": None, "account_nm": nm}
    for nm, v in inc.items():
        x = v[ISIDX[k]]
        p[nm] = {"당기": x, "전기": None, "당기누적": x if k == "2026.06" else None, "전기누적": (v[1] if k == "2026.06" else None), "전전기": None, "account_nm": nm}
    for nm, v in cf.items():
        i_ = CFIDX[k]
        x = v[i_] if i_ is not None else None
        p[nm] = {"당기": x, "전기": None, "당기누적": x if k == "2026.06" else None, "전기누적": None, "전전기": None, "account_nm": nm}
    periods_raw[k] = p
# 2025.06 (전년 반기: 손익·현금흐름만)
p25 = {}
for nm, v in inc.items():
    p25[nm] = {"당기": v[1], "전기": None, "당기누적": v[1], "전기누적": None, "전전기": None, "account_nm": nm}
for nm, v in cf.items():
    p25[nm] = {"당기": v[1], "전기": None, "당기누적": v[1], "전기누적": None, "전전기": None, "account_nm": nm}
periods_raw["2025.06"] = p25

items = {}
for nm in list(bs) + list(inc) + list(cf):
    if nm in bs:
        v = bs[nm]; cur_, prv_, pp_ = v[1], v[2], v[3]
    elif nm in inc:
        v = inc[nm]; cur_, prv_, pp_ = v[2], v[3], v[4]
    else:
        v = cf[nm]; cur_, prv_, pp_ = v[2], v[3], None
    items[nm] = {"당기": cur_, "전기": prv_, "당기누적": None, "전기누적": None, "전전기": pp_, "account_nm": nm}
# 현금흐름표 세부(최신 반기·FY25·FY24): 현금흐름 워터폴·capex 용
def cfrows(idx):
    out = []
    for r in CF:
        v = num(r[1 + idx]) if len(r) > 1 + idx else None
        if v is not None and r[0]:
            out.append([r[0], v * K])
    return out
for k, i_ in (("2026.06", 0), ("2025.12", 2), ("2024.12", 3)):
    periods_raw[k]["_cf"] = cfrows(i_)
periods_raw["2025.06"]["_cf"] = cfrows(1)
items["_cf"] = cfrows(2)

q = []
for k, lbl, j in (("2026.06", "26.2Q", 0), ("2025.06", "25.2Q", 1)):
    vals = {nm: inc[nm][j] for nm in inc}
    vals.update({nm: cf[nm][j] for nm in ("영업활동현금흐름", "투자활동현금흐름", "재무활동현금흐름")})
    q.append({"key": k, "label": lbl, "values": vals, "how": "직접", "check": ""})
yoy = {nm: round((inc[nm][0] / inc[nm][1] - 1) * 100, 1) for nm in ("매출액", "영업이익", "당기순이익") if inc[nm][1]}
segments = []
for j, k in enumerate(KEYS):
    segments.append({"key": k, "kind": KIND[k], "items": {nm: v[j] for nm, v in seg.items() if v[j]}})
segments.sort(key=lambda x: x["key"])
region_hist = [{"key": k, "kind": KIND[k], "dom": dom[j], "exp": exp[j]} for j, k in enumerate(KEYS)]
region_hist.sort(key=lambda x: x["key"])
capa_hist = []
for j, k in enumerate(KEYS):
    g = {}
    for nm, d in capa.items():
        if d["capa"][j] is not None:
            g[nm] = {"unit": "개", "capa": d["capa"][j], "prod": d["prod"][j], "util": d["util"][j]}
    capa_hist.append({"key": k, "kind": KIND[k], "groups": g})
capa_hist.sort(key=lambda x: x["key"])

fin = {"basis": "별도", "year": 2025, "unit": "원", "items": items, "quarters": q, "yoy_latest": yoy, "latest_period": "2026.06",
       "periods_raw": periods_raw, "dividend": {}, "capex_notices": [], "region_hist": region_hist, "capa_hist": capa_hist,
       "cost_files": ["__none__.json"], "ltm": {nm: inc[nm][2] + inc[nm][0] - inc[nm][1] for nm in ("매출액", "영업이익", "당기순이익")}, "orders_hist": [], "segments": segments, "insider": [], "contracts": []}
rc = re.search(r"(\d{14})", fname).group(1)
meta = {"corp_name": "동원파츠", "stock_code": code, "corp_code": "00784184",
        "report_nm": "투자설명서(기재정정) 2026.10.30 — 정기보고서 없음(신규상장)", "rcept_no": rc, "rcept_dt": rc[:8],
        "latest_report_nm": "투자설명서(기재정정) — 2026년 반기(제30기) 검토 재무제표 포함", "latest_rcept_no": rc, "latest_rcept_dt": rc[:8],
        "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), "source": "prospectus"}
json.dump({"meta": meta, "fin": fin}, open(os.path.join(base, "fin.json"), "w", encoding="utf-8"), ensure_ascii=False)
print("fin.json OK", {k: round(v[0] / 1e8, 1) for k, v in inc.items()}, yoy)
