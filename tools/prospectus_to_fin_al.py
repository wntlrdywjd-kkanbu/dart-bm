#!/usr/bin/env python3
"""신규상장 제조업(정기보고서 없음) — 에이엘로봇형: 투자설명서 원문 txt → out/{code}/fin.json.
특징: 별도재무제표, 단위 '원', 재무상태표·손익계산서 4열(반기말·FY25·FY24·FY23), 현금흐름표 4열,
손익계산서에 전년 반기(1H25) 열이 없어 주석 26(매출)·27(판관비)·28(성격별 비용)·32(반기순손실)에서 복원한다.
제품·지역·생산능력·매출처는 '사업의 내용' 표에서 읽는다.
사용: python tools/prospectus_to_fin_al.py 471800 "20261104000315__기재정정_투자설명서.txt"
검증 assert: 자산=부채+자본, 매출-원가=총이익, 영업이익=총이익-판관비, 제품합=매출, 내수+수출=매출, 거래처합=매출,
1H25: 매출-성격별비용합계=영업이익(1H26 에서 같은 식이 맞는지 먼저 확인)."""
import json, os, re, sys, datetime

code, fname = sys.argv[1], sys.argv[2]
base = os.path.join("out", code)
L = open(os.path.join(base, "prospectus", fname), encoding="utf-8").read().split("\n")


def num(s):
    s = s.strip().replace(",", "")
    m = re.fullmatch(r"\(?(-?[\d.]+)\)?", s)
    if not m:
        return None
    try:
        v = float(m.group(1))
    except ValueError:
        return None
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


bs_a = sec(r"\| 재무상태표 \|"); is_a = sec(r"\| 포괄손익계산서 \|", bs_a); eq_a = sec(r"\| 자본변동표 \|", is_a)
cf_a = sec(r"\| 현 금 흐 름 표 \|", eq_a); n26 = sec(r"26\. 고객과의 계약에서 생기는 수익", cf_a)
BS, IS, CF = rows(bs_a, is_a - 1), rows(is_a, eq_a - 1), rows(cf_a, n26 - 1)
n27 = sec(r"27\. 판매비와관리비", n26); n28 = sec(r"28\. 성격별 비용", n27); n29 = sec(r"29\. 기타수익", n28)
n32 = sec(r".*32\. 주당이익", n29)
N26, N27, N28, N32 = rows(n26, n27 - 1), rows(n27, n28 - 1), rows(n28, n29 - 1), rows(n32, n32 + 25)


def vals(rs, label, n, nth=0, k=1.0):
    r = find(rs, label, nth)
    return [None if (r is None or i >= len(r) or num(r[i]) is None) else num(r[i]) * k for i in range(1, n + 1)]


bs = {"자산총계": vals(BS, "자산총계", 4), "부채총계": vals(BS, "부채총계", 4), "자본총계": vals(BS, "자본총계", 4),
      "유동자산": vals(BS, "유동자산", 4), "비유동자산": vals(BS, "비유동자산", 4), "유동부채": vals(BS, "유동부채", 4),
      "비유동부채": vals(BS, "비유동부채", 4), "현금및현금성자산": vals(BS, "현금및현금성자산", 4),
      "재고자산": vals(BS, "재고자산", 4), "매출채권": vals(BS, "매출채권", 4), "이익잉여금": vals(BS, "이익잉여금", 4),
      "자본금": vals(BS, "자본금", 4)}
# 주석 col 존재: BS/IS 행은 [과목, 주석, 1H26, FY25, FY24, FY23] → vals 의 i=1 이 주석. 열을 2부터 읽도록 보정
def vals2(rs, label, n=4):
    r = find(rs, label)
    return [None if (r is None or 2 + i >= len(r) or num(r[2 + i]) is None) else num(r[2 + i]) for i in range(n)]


bs = {k: vals2(BS, lab) for k, lab in (("자산총계", "자산총계"), ("부채총계", "부채총계"), ("자본총계", "자본총계"),
      ("유동자산", "유동자산"), ("비유동자산", "비유동자산"), ("유동부채", "유동부채"), ("비유동부채", "비유동부채"),
      ("현금및현금성자산", "현금및현금성자산"), ("재고자산", "재고자산"), ("매출채권", "매출채권"),
      ("이익잉여금", "이익잉여금"), ("자본금", "자본금"))}
inc4 = {"매출액": vals2(IS, "매출액"), "매출원가": vals2(IS, "매출원가"), "매출총이익": vals2(IS, "매출총이익"),
        "판관비": vals2(IS, "판매비와관리비"), "영업이익": vals2(IS, "영업이익"), "당기순이익": vals2(IS, "당기순이익")}


def cfv(label):
    r = find(CF, label)
    return [None if (r is None or 1 + i >= len(r) or num(r[1 + i]) is None) else num(r[1 + i]) for i in range(4)]


cf4 = {"영업활동현금흐름": cfv("영업활동으로 인한 현금흐름"), "투자활동현금흐름": cfv("투자활동으로 인한 현금흐름"),
       "재무활동현금흐름": cfv("재무활동으로 인한 현금흐름"), "기초현금": cfv("기초의 현금및현금성자산"),
       "기말현금": cfv("기말의 현금및현금성자산")}

for i in range(4):
    a, l, e = bs["자산총계"][i], bs["부채총계"][i], bs["자본총계"][i]
    assert abs(a - l - e) <= 10, ("BS", i, a, l, e)
    r_, c_, g_, s_, o_ = (inc4[k][i] for k in ("매출액", "매출원가", "매출총이익", "판관비", "영업이익"))
    assert abs(r_ - c_ - g_) <= 10 and abs(g_ - s_ - o_) <= 10, ("IS", i)

# --- 1H25 (전반기, 검토받지 않은 재무제표) 주석에서 복원 (단위 천원)
def note_row(rs, label):
    r = find(rs, label)
    assert r, label
    return r


rev25 = num(note_row(N26, "제품매출")[4]) * 1e3
sga25 = num(note_row(N27, "합계")[4]) * 1e3
tot_exp26, tot_exp25 = num(note_row(N28, "합계")[2]) * 1e3, num(note_row(N28, "합계")[4]) * 1e3
rev26 = inc4["매출액"][0]
assert abs(rev26 - num(note_row(N26, "제품매출")[2]) * 1e3) <= 1000
# 검증: 1H26 에서 매출 - 성격별비용합계 = 영업이익
assert abs(rev26 - tot_exp26 - inc4["영업이익"][0]) <= 1000, "성격별비용 합계식 불일치"
assert abs(sga25 - 1905139e3) <= 1000
op25 = rev25 - tot_exp25
cogs25 = tot_exp25 - sga25
ni25 = None
for r in N32:
    if r and "반기순" in r[0] and len(r) >= 5:
        ni25 = num(r[4])
        break
assert ni25 is not None, "1H25 순이익 못 찾음"
h25 = {"매출액": rev25, "매출원가": cogs25, "매출총이익": rev25 - cogs25, "판관비": sga25, "영업이익": op25, "당기순이익": ni25}

# 손익 5열 [1H26, 1H25, FY25, FY24, FY23]
inc = {k: [v[0], h25[k], v[1], v[2], v[3]] for k, v in inc4.items()}
# 현금흐름 [1H26, 1H25(없음), FY25, FY24, FY23]
cf = {k: [v[0], None, v[1], v[2], v[3]] for k, v in cf4.items()}

# --- 제품별 매출
pa = sec(r"가\. 주요 제품 등의 현황"); pb = sec(r"나\. 주요 제품 등의 내용", pa)
seg = {"로봇용 힘/토크센서": [None] * 4, "융합 제어장치": [None] * 4}
for r in rows(pa, pb - 1):
    if len(r) >= 8 and r[0].startswith("로봇용"):
        seg["로봇용 힘/토크센서"] = [num(re.sub(r"\(.*\)", "", c)) * 1e3 for c in r[3:7]]
    elif len(r) >= 8 and r[0].startswith("융합"):
        seg["융합 제어장치"] = [num(re.sub(r"\(.*\)", "", c)) * 1e3 for c in r[3:7]]
tot = [sum(v[i] for v in seg.values()) for i in range(4)]
for i, j in enumerate([0, 2, 3, 4]):
    assert abs(tot[i] - inc["매출액"][j]) <= 1e3 + 1, ("seg", i, tot[i], inc["매출액"][j])

# --- 내수/수출
ra = sec(r"가\. 매출 실적"); rb = sec(r"나\. 판매조직", ra)
RG = rows(ra, rb - 1)
dom = exp = None
for i_, r in enumerate(RG):
    if r[0] == "합계" and len(r) >= 9 and r[1] == "내수":
        dom = [num(r[2]) * 1e3, num(r[4]) * 1e3, num(r[6]) * 1e3, num(r[8]) * 1e3]
        nx = RG[i_ + 1]
        exp = [(num(nx[i]) or 0) * 1e3 for i in (1, 3, 5, 7)]
assert dom and exp
for i, j in enumerate([0, 2, 3, 4]):
    assert abs(dom[i] + exp[i] - inc["매출액"][j]) <= 1e3 + 1, ("region", i)

# --- 생산능력
ca = sec(r"라\. 생산능력 및 생산실적"); cb = sec(r"4\. 매출 및 수주상황", ca)
capa = {}
cur = None
for r in rows(ca, cb - 1):
    if len(r) >= 10 and r[1] == "생산능력":
        cur = "센서" if r[0].startswith("로봇용") else "제어장치"
        capa[cur] = {"capa": [num(r[2]), num(r[4]), num(r[6]), num(r[8])]}
    elif cur and clean(r[0]) == "생산실적":
        capa[cur]["prod"] = [num(r[1]), num(r[3]), num(r[5]), num(r[7])]
    elif cur and clean(r[0]) == "가동율":
        capa[cur]["util"] = [float(c.strip("%")) for c in (r[1], r[3], r[5], r[7])]
assert len(capa) == 2 and all(len(d) == 3 for d in capa.values())

# --- 매출처 (사업의 내용 > 주요 매출처) — 합계 검증만 하고 customers 로 저장
ma = sec(r"마\. 주요 매출처"); mb = sec(r"바\. 수주 현황", ma)
CU = rows(ma, mb - 1)
cust = []
cur_sec = None
tt = None
for r in CU:
    if r[0] in ("매출부문", "매출액") or (len(r) > 1 and r[1] == "매출처"):
        continue
    if r[0] == "합계":
        tt = [(num(r[i]) or 0) * 1e3 for i in (1, 3, 5, 7)]
        continue
    if len(r) >= 10:  # [부문, 매출처, v,%,v,%,v,%,v,%]
        cur_sec, nm, vs = r[0], r[1], [r[2], r[4], r[6], r[8]]
    elif len(r) >= 9:  # [매출처, v,%,v,%,v,%,v,%]
        nm, vs = r[0], [r[1], r[3], r[5], r[7]]
    else:
        continue
    cust.append({"부문": cur_sec, "매출처": nm, "v": [(num(c) or 0) * 1e3 for c in vs]})
assert tt
for i in range(4):
    s_ = sum(c["v"][i] for c in cust)
    assert abs(s_ - tt[i]) <= 4e3, ("cust", i, s_, tt[i])

# --- 조립
KEYS = ["2026.06", "2025.12", "2024.12", "2023.12"]
KIND = {"2026.06": "반기", "2025.12": "사업", "2024.12": "사업", "2023.12": "사업"}
ISIDX = {"2026.06": 0, "2025.12": 2, "2024.12": 3, "2023.12": 4}
CFIDX = {"2026.06": 0, "2025.12": 2, "2024.12": 3, "2023.12": 4}
periods_raw = {}
for j, k in enumerate(KEYS):
    p = {}
    for nm, v in bs.items():
        p[nm] = {"당기": v[j], "전기": None, "당기누적": None, "전기누적": None, "전전기": None, "account_nm": nm}
    for nm, v in inc.items():
        x = v[ISIDX[k]]
        p[nm] = {"당기": x, "전기": None, "당기누적": x if k == "2026.06" else None, "전기누적": (v[1] if k == "2026.06" else None), "전전기": None, "account_nm": nm}
    for nm, v in cf.items():
        x = v[CFIDX[k]]
        p[nm] = {"당기": x, "전기": None, "당기누적": x if k == "2026.06" else None, "전기누적": None, "전전기": None, "account_nm": nm}
    periods_raw[k] = p
p25 = {}
for nm, v in inc.items():
    p25[nm] = {"당기": v[1], "전기": None, "당기누적": v[1], "전기누적": None, "전전기": None, "account_nm": nm}
for nm in cf:
    p25[nm] = {"당기": None, "전기": None, "당기누적": None, "전기누적": None, "전전기": None, "account_nm": nm}
periods_raw["2025.06"] = p25

items = {}
for nm in list(bs) + list(inc) + list(cf):
    if nm in bs:
        v = bs[nm]; cur_, prv_, pp_ = v[1], v[2], v[3]
    elif nm in inc:
        v = inc[nm]; cur_, prv_, pp_ = v[2], v[3], v[4]
    else:
        v = cf[nm]; cur_, prv_, pp_ = v[2], v[3], v[4]
    items[nm] = {"당기": cur_, "전기": prv_, "당기누적": None, "전기누적": None, "전전기": pp_, "account_nm": nm}


def cfrows(idx):
    out = []
    for r in CF:
        v = num(r[1 + idx]) if len(r) > 1 + idx else None
        if v is not None and r[0]:
            out.append([r[0], v])
    return out


for k, i_ in (("2026.06", 0), ("2025.12", 1), ("2024.12", 2)):
    periods_raw[k]["_cf"] = cfrows(i_)
periods_raw["2025.06"]["_cf"] = []
items["_cf"] = cfrows(1)

q = []
for k, lbl, j in (("2026.06", "26.2Q", 0), ("2025.06", "25.2Q", 1)):
    vv = {nm: inc[nm][j] for nm in inc}
    if j == 0:
        vv.update({nm: cf[nm][j] for nm in ("영업활동현금흐름", "투자활동현금흐름", "재무활동현금흐름")})
    q.append({"key": k, "label": lbl, "values": vv, "how": "직접" if j == 0 else "주석(26·27·28·32)", "check": ""})
yoy = {nm: round((inc[nm][0] / inc[nm][1] - 1) * 100, 1) for nm in ("매출액",) if inc[nm][1]}
segments = []
for j, k in enumerate(KEYS):
    segments.append({"key": k, "kind": KIND[k], "items": {nm: v[j] for nm, v in seg.items() if v[j]}})
segments.sort(key=lambda x: x["key"])
region_hist = [{"key": k, "kind": KIND[k], "dom": dom[j], "exp": exp[j]} for j, k in enumerate(KEYS)]
region_hist.sort(key=lambda x: x["key"])
capa_hist = []
for j, k in enumerate(KEYS):
    g = {nm: {"unit": "kEA", "capa": d["capa"][j], "prod": d["prod"][j], "util": d["util"][j]} for nm, d in capa.items()}
    capa_hist.append({"key": k, "kind": KIND[k], "groups": g})
capa_hist.sort(key=lambda x: x["key"])

fin = {"basis": "별도", "year": 2025, "unit": "원", "items": items, "quarters": q, "yoy_latest": yoy, "latest_period": "2026.06",
       "periods_raw": periods_raw, "dividend": {}, "capex_notices": [], "region_hist": region_hist, "capa_hist": capa_hist,
       "cost_files": ["__none__.json"],
       "ltm": {nm: inc[nm][2] + inc[nm][0] - inc[nm][1] for nm in ("매출액", "영업이익", "당기순이익")},
       "orders_hist": [], "segments": segments, "insider": [], "contracts": [], "customers": cust}
rc = re.search(r"(\d{14})", fname).group(1)
meta = {"corp_name": "에이엘로봇", "stock_code": code, "corp_code": "01797635",
        "report_nm": "투자설명서(기재정정) 2026.11.04 — 정기보고서 없음(신규상장)", "rcept_no": rc, "rcept_dt": rc[:8],
        "latest_report_nm": "투자설명서(기재정정) — 2026년 반기(제10기) 검토 재무제표 포함", "latest_rcept_no": rc, "latest_rcept_dt": rc[:8],
        "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), "source": "prospectus"}
json.dump({"meta": meta, "fin": fin}, open(os.path.join(base, "fin.json"), "w", encoding="utf-8"), ensure_ascii=False)
print("fin.json OK", {k: round(v[0] / 1e8, 2) for k, v in inc.items()}, yoy)
print("1H25", {k: round(v / 1e8, 2) for k, v in h25.items()})
