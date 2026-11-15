#!/usr/bin/env python3
"""신규상장 소프트웨어/연결 기준(원 단위, 매출원가 없음) 투자설명서 → out/{code}/fin.json.
사용: python tools/prospectus_to_fin_sw.py 0147B0 "20261021000341__기재정정_투자설명서.txt"
연결 BS/IS/CF 4열(반기말·FY25·FY24·FY23). 전년 반기(1H25) 열이 없어 quarters 는 최신 반기만, YoY 는 투자설명서 서술값(+58.2%)을 쓴다.
2023년은 K-GAAP 별도 재작성(주1)이므로 basis 연결 + 주석 필요. 합계(자산=부채+자본, 매출유형 합=영업수익) assert."""
import json, os, re, sys, datetime
code, fname = sys.argv[1], sys.argv[2]
base = os.path.join("out", code)
L = open(os.path.join(base, "prospectus", fname), encoding="utf-8").read().split("\n")

def num(s):
    s = s.strip().replace(",", "")
    m = re.fullmatch(r"\(?(-?[\d.]+)\)?", s)
    if not m: return None
    v = float(m.group(1)); return -v if s.startswith("(") else v
def rows(a, b):
    return [[c.strip() for c in ln.strip().strip("|").split("|")] for ln in L[a - 1:b] if ln.startswith("|")]
def clean(l): return re.sub(r"^[\sⅠ-Ⅹ IVX0-9\.\(\)]+", "", re.sub(r"\s+", "", l))
def find(rs, label, nth=0):
    h = [r for r in rs if clean(r[0]).startswith(clean(label))]
    return h[nth] if len(h) > nth else None
def sec(pat, after=0):
    for i, ln in enumerate(L):
        if i >= after and re.match(pat, ln.strip()): return i + 1
    raise SystemExit("섹션 없음: " + pat)

bs_a = sec(r"\| 연 결 재 무 상 태 표 \|"); is_a = sec(r"\| 연 결 포 괄 손 익 계 산 서 \|"); eq_a = sec(r"\| 연 결 자 본 변 동 표 \|")
cf_a = sec(r"\| 연 결 현 금 흐 름 표 \|"); nt_a = sec(r"3\. 연결재무제표 주석")
BS, IS, CF = rows(bs_a, is_a - 1), rows(is_a, eq_a - 1), rows(cf_a, nt_a - 1)
def v4(rs, label, nth=0):
    r = find(rs, label, nth)
    return [num(c) if num(c) is not None else (0.0 if c.strip() == "-" else None) for c in r[1:5]] if r else [None] * 4
bs = {"자산총계": v4(BS, "자산총계"), "부채총계": v4(BS, "부채총계"), "자본총계": v4(BS, "자본총계"),
      "유동자산": v4(BS, "유동자산"), "비유동자산": v4(BS, "비유동자산"), "유동부채": v4(BS, "유동부채"), "비유동부채": v4(BS, "비유동부채"),
      "현금및현금성자산": v4(BS, "현금및현금성자산"), "단기금융상품": v4(BS, "단기금융상품"), "매출채권": v4(BS, "매출채권및기타수취채권"),
      "계약자산": v4(BS, "계약자산"), "계약부채_유동": v4(BS, "계약부채", 0), "계약부채_비유동": v4(BS, "계약부채", 1),
      "상환전환우선주부채": v4(BS, "상환전환우선주부채"), "파생상품부채": v4(BS, "파생상품부채"), "유형자산": v4(BS, "유형자산"),
      "리스부채_유동": v4(BS, "리스부채", 0), "리스부채_비유동": v4(BS, "리스부채", 1),
      "자본금": v4(BS, "자본금"), "자본잉여금": v4(BS, "자본잉여금"), "이익잉여금": v4(BS, "결손금")}
inc = {"매출액": v4(IS, "영업수익"), "영업비용": v4(IS, "영업비용"), "영업이익": v4(IS, "영업이익"), "금융수익": v4(IS, "금융수익"),
       "금융비용": v4(IS, "금융비용"), "법인세차감전이익": v4(IS, "법인세차감전"), "당기순이익": v4(IS, "당기순손실")}
cf = {"영업활동현금흐름": v4(CF, "영업활동으로 인한 현금흐름"), "투자활동현금흐름": v4(CF, "투자활동으로 인한 현금흐름"),
      "재무활동현금흐름": v4(CF, "재무활동으로 인한 현금흐름"), "기초현금": v4(CF, "기초의 현금및현금성자산"), "기말현금": v4(CF, "기말의 현금및현금성자산")}
for i in range(4):
    assert abs(bs["자산총계"][i] - bs["부채총계"][i] - bs["자본총계"][i]) < 2, ("BS", i)
    assert abs(inc["매출액"][i] - inc["영업비용"][i] - inc["영업이익"][i]) < 2, ("IS", i)
# 매출유형 (주요 제품 및 서비스 현황), 백만원
pa = sec(r"\| \[주요 제품 및 서비스 현황\] \|"); PR = rows(pa, pa + 14)
seg = {}
for r in PR:
    if r[0].startswith("제품매출"): seg["구독 라이선스"] = [num(r[3]), num(r[5]), num(r[7]), num(r[9])]
    elif r[0] == "영구 라이선스": seg["영구 라이선스"] = [num(r[2]), num(r[4]), num(r[6]), num(r[8])]
    elif r[0].startswith("용역"): seg["기술지원 연장"] = [num(r[3]), num(r[5]), num(r[7]), num(r[9])]
    elif r[0] == "기타매출": seg["기타"] = [num(r[2]), num(r[4]), num(r[6]), num(r[8])]
    elif r[0] == "합계": tot = [num(r[1]), num(r[3]), num(r[5]), num(r[7])]
for i in range(4):
    assert abs(sum(v[i] for v in seg.values()) - tot[i]) <= 2, ("seg", i)
    assert abs(tot[i] * 1e6 - inc["매출액"][i]) <= 1e6, ("seg vs 매출", i)
KEYS = ["2026.06", "2025.12", "2024.12", "2023.12"]; KIND = {"2026.06": "반기", "2025.12": "사업", "2024.12": "사업", "2023.12": "사업"}
def cell(x, cur=False): return {"당기": x, "전기": None, "당기누적": x if cur else None, "전기누적": None, "전전기": None}
periods_raw = {}
for j, k in enumerate(KEYS):
    p = {}
    for d in (bs, inc, cf):
        for nm, v in d.items():
            c = cell(v[j], k == "2026.06"); c["account_nm"] = nm; p[nm] = c
    p["_cf"] = [[r[0], num(r[1 + j])] for r in CF if len(r) > 1 + j and num(r[1 + j]) is not None]
    periods_raw[k] = p
items = {}
for nm, v in {**bs, **inc, **cf}.items():
    cur_, prv_, pp_ = (v[1], v[2], v[3])
    items[nm] = {"당기": cur_, "전기": prv_, "당기누적": None, "전기누적": None, "전전기": pp_, "account_nm": nm}
items["_cf"] = periods_raw["2025.12"]["_cf"]
q = [{"key": "2026.06", "label": "26.2Q", "values": {**{n: inc[n][0] for n in inc}, **{n: cf[n][0] for n in ("영업활동현금흐름", "투자활동현금흐름", "재무활동현금흐름")}}, "how": "직접", "check": ""}]
yoy = {"매출액": 58.2}  # 투자설명서 서술(전년 반기 비교열 없음)
segments = sorted([{"key": k, "kind": KIND[k], "items": {nm: v[j] * 1e6 for nm, v in seg.items() if v[j]}} for j, k in enumerate(KEYS)], key=lambda x: x["key"])
fin = {"basis": "연결", "year": 2025, "unit": "원", "items": items, "quarters": q, "yoy_latest": yoy, "latest_period": "2026.06",
       "periods_raw": periods_raw, "dividend": {}, "capex_notices": [], "region_hist": [], "capa_hist": [],
       "cost_files": ["__none__.json"], "orders_hist": [], "segments": segments, "insider": [], "contracts": []}
rc = re.search(r"(\d{14})", fname).group(1)
meta = {"corp_name": "래블업", "stock_code": code, "corp_code": "01956751",
        "report_nm": "투자설명서(기재정정) 2026.10.21 — 정기보고서 없음(신규상장)", "rcept_no": rc, "rcept_dt": rc[:8],
        "latest_report_nm": "투자설명서(기재정정) — 2026년 반기(제12기) 검토 연결재무제표 포함", "latest_rcept_no": rc, "latest_rcept_dt": rc[:8],
        "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), "source": "prospectus"}
json.dump({"meta": meta, "fin": fin}, open(os.path.join(base, "fin.json"), "w", encoding="utf-8"), ensure_ascii=False)
print("fin.json OK", {k: round(v[0] / 1e8, 1) for k, v in inc.items()}, {k: v for k, v in seg.items()})
