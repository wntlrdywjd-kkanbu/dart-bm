#!/usr/bin/env python3
"""신규상장 제조업(정기보고서 없음) 연결 기준용: 투자설명서 원문 txt → out/{code}/fin.json.
prospectus_to_fin.py(별도·제조업) 와 같은 출력 형식이되, 연결재무제표(4열: 반기말·FY25·FY24·FY23)를 읽는다.
연결 손익계산서에는 전년 반기(1H25) 열이 없어서 주석(26 수익, 27 매출원가·판관비, 32 순손실)에서 읽어 채운다.
사용: python tools/prospectus_to_fin_cons.py 0053F0 "20261016000162__기재정정_투자설명서.txt"
검증 assert: 자산=부채+자본, 매출-원가=총이익, 제품표 합계=매출, 수출+내수=매출(지역표)."""
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
    return [[c.strip() for c in ln.strip().strip("|").split("|")] for ln in L[a - 1:b] if ln.startswith("|")]


def clean(lbl):
    return re.sub(r"^[\sⅠ-Ⅹ IVX0-9\.\(\)]+", "", re.sub(r"\s+", "", lbl))


def find(rs, label, nth=0):
    hit = [r for r in rs if clean(r[0]).startswith(clean(label))]
    return hit[nth] if len(hit) > nth else None


def sec(pat, after=0):
    for i, ln in enumerate(L):
        if i >= after and re.match(pat, ln.strip()):
            return i + 1
    raise SystemExit("섹션 없음: " + pat)


def v4(rs, label, nth=0, k=1.0):
    r = find(rs, label, nth)
    if not r:
        return [None] * 4
    out = []
    for c in r[1:5]:
        n = num(c)
        out.append(n * k if n is not None else (0.0 if c.strip() == "-" else None))
    return out


bs_a = sec(r"\| 연 결 재 무 상 태 표 \|"); is_a = sec(r"\| 연 결 포 괄 손 익 계 산 서 \|"); eq_a = sec(r"\| 연 결 자 본 변 동 표 \|")
cf_a = sec(r"\| 연 결 현 금 흐 름 표 \|"); nt_a = sec(r"3\. 연결재무제표 주석")
BS, IS, CF = rows(bs_a, is_a - 1), rows(is_a, eq_a - 1), rows(cf_a, nt_a - 1)
bs = {"자산총계": v4(BS, "자산총계"), "부채총계": v4(BS, "부채총계"), "자본총계": v4(BS, "자본총계"),
      "유동자산": v4(BS, "Ⅰ.유동자산"), "비유동자산": v4(BS, "Ⅱ.비유동자산"), "유동부채": v4(BS, "Ⅰ.유동부채"), "비유동부채": v4(BS, "Ⅱ.비유동부채"),
      "현금및현금성자산": v4(BS, "현금및현금성자산"), "재고자산": v4(BS, "재고자산"), "매출채권": v4(BS, "매출채권및기타유동수취채권"),
      "이익잉여금": v4(BS, "결손금"), "자본금": v4(BS, "자본금"), "자본잉여금": v4(BS, "자본잉여금"),
      "매입채무": v4(BS, "매입채무"), "계약부채": v4(BS, "계약부채"), "단기차입금": v4(BS, "단기차입금"),
      "유동성장기차입금": v4(BS, "유동성장기차입금"), "장기차입금": v4(BS, "장기차입금"), "유형자산": v4(BS, "유형자산"),
      "유동전환상환우선주": v4(BS, "유동전환상환우선주"), "유동파생상품부채": v4(BS, "유동당기손익-공정가치측정금융부채")}
inc4 = {"매출액": v4(IS, "Ⅰ.매출액"), "매출원가": v4(IS, "Ⅱ.매출원가"), "매출총이익": v4(IS, "Ⅲ.매출총이익"), "판관비": v4(IS, "판매비와관리비"),
        "영업이익": v4(IS, "Ⅳ.영업이익"), "당기순이익": v4(IS, "Ⅵ.당기순이익"), "금융비용": v4(IS, "금융비용"), "금융수익": v4(IS, "금융수익")}
cf = {"영업활동현금흐름": v4(CF, "Ⅰ. 영업활동으로인한현금흐름"), "투자활동현금흐름": v4(CF, "Ⅱ. 투자활동 현금흐름"),
      "재무활동현금흐름": v4(CF, "Ⅲ. 재무활동 현금흐름"), "기초현금": v4(CF, "Ⅵ. 기초 현금및현금성자산"), "기말현금": v4(CF, "Ⅶ. 기말 현금및현금성자산")}
for i in range(4):
    assert abs(bs["자산총계"][i] - bs["부채총계"][i] - bs["자본총계"][i]) < 2, ("BS", i)
    assert abs(inc4["매출액"][i] - inc4["매출원가"][i] - inc4["매출총이익"][i]) < 2, ("IS", i)
    assert abs(inc4["매출총이익"][i] - inc4["판관비"][i] - inc4["영업이익"][i]) < 2, ("OP", i)

# --- 전년 반기(1H25): 주석 값(천원). 연결 손익계산서 본문에는 열이 없다.
def note_val(title_pat, label, col=2):
    a = sec(title_pat)
    for r in rows(a, a + 60):
        if clean(r[0]).startswith(clean(label)) and num(r[col]) is not None:
            return num(r[col]) * 1000.0
    raise SystemExit(f"주석 값 없음: {title_pat} {label}")

N26 = r"26\. 고객과의 계약에서 생기는 수익"
rev25 = note_val(N26, "고객과의 계약에서 생기는 수익")
a27 = sec(r"27\. 매출원가 및 판매비와관리비")
def first_total(a, span=40):
    for r in rows(a, a + span):
        if r[0] == "합 계" and len(r) >= 3 and num(r[2]) is not None:
            return num(r[2]) * 1000.0
    raise SystemExit("합계 행 없음 @%d" % a)


cogs25 = first_total(a27)
sga25 = first_total(sec(r"\(2\) 당반기 및 전반기 중 판매비와관리비로"))
a32 = sec(r"32\. 주당이익")
ni25 = [num(r[2]) for r in rows(a32, a32 + 10) if r[0].startswith("보통주반기순이익")][0]
inc25 = {"매출액": rev25, "매출원가": cogs25, "매출총이익": rev25 - cogs25, "판관비": sga25, "영업이익": rev25 - cogs25 - sga25, "당기순이익": ni25}
assert abs(inc4["매출액"][0] - 21112556757) < 2
inc = {k: [inc4[k][0], inc25.get(k), inc4[k][1], inc4[k][2], inc4[k][3]] for k in ("매출액", "매출원가", "매출총이익", "판관비", "영업이익", "당기순이익")}

# --- 제품·용역·상품별 매출 (백만원, 열: 1H26, 1H25, FY25, FY24, FY23) → 원
pa = sec(r"가\. 주요 제품 등 매출 현황"); pb = sec(r"나\. 주요 제품 등의 내용", pa)
PR = rows(pa, pb - 1)
seg = {}
tot = None
for r in PR:
    if r[0] == "자사 제품" and len(r) >= 12:
        seg[r[1]] = [num(r[i]) for i in (2, 4, 6, 8, 10)]
    elif r[0] in ("공정용 터보블로워", "수소발전용 에어블로워", "수소발전용 재순환블로워", "선형발전기용 에어블로워(주2)", "수소차용 공기압축기", "기타") and len(r) >= 11:
        seg[r[0].replace("(주2)", "")] = [num(r[i]) for i in (1, 3, 5, 7, 9)]
    elif r[0] == "상 품(주2)":
        seg["상품"] = [num(r[i]) for i in (1, 3, 5, 7, 9)]
    elif r[0] == "용역":
        seg["용역"] = [num(r[i]) for i in (1, 3, 5, 7, 9)]
    elif r[0] == "매출총계":
        tot = [num(r[i]) for i in (1, 3, 5, 7, 9)]
seg["기타(제품)"] = seg.pop("기타")
assert tot and len(seg) == 9, (list(seg), tot)
for i in range(5):
    s_ = sum(v[i] or 0 for v in seg.values())
    assert abs(s_ - tot[i]) <= 3, ("seg", i, s_, tot[i])
    assert abs(tot[i] * 1e6 - inc["매출액"][i]) <= 2e6, ("seg vs 매출", i, tot[i], inc["매출액"][i])

# --- 수출/내수 (매출실적 표의 소계 수출/내수 3개 합: 제품·용역·상품)
ra = sec(r"가\. 매출실적"); rb = sec(r"나\. 판매조직", ra)
RG = rows(ra, rb - 1)
exp = [0.0] * 5; dom = [0.0] * 5; n_sub = 0; last = None
for r in RG:
    if r[0] == "소 계" and len(r) == 7 and r[1] == "수출":
        for i in range(5):
            exp[i] += num(r[2 + i])
        last = "수출"; n_sub += 1
    elif last == "수출" and r[0] == "내수" and len(r) == 6:
        for i in range(5):
            dom[i] += num(r[1 + i])
        last = None; n_sub += 1
assert n_sub == 6, n_sub
for i in range(5):
    assert abs(exp[i] + dom[i] - tot[i]) <= 3, ("region", i, exp[i] + dom[i], tot[i])

# --- 생산능력·실적 (대): 열 1H26, 1H25, FY25, FY24, FY23
ca = sec(r"다\. 생산설비에 관한 사항\(1\) 생산능력 및 생산실적"); cb = sec(r"\(2\) 생산설비에 관한 사항", ca)
CP = rows(ca, cb - 1)
NAME = {"양산모델 A": "에어블로워 A", "양산모델 B": "에어블로워 B", "양산모델 C": "재순환블로워 C", "Rotor": "수소차 Rotor", "Bearing": "수소차 Bearing",
        "수처리용": "수처리용 블로워", "공정용": "공정용 블로워"}
capa = {}; cur = None
for r in CP:
    if len(r) >= 12 and r[2] == "생산능력":
        cur = NAME.get(r[1], r[1]); st = 3
    elif len(r) >= 11 and r[1] == "생산능력":
        cur = NAME.get(r[0], r[0]); st = 2
    elif cur and r[0] == "생산실적":
        capa[cur]["prod"] = [num(r[1 + 2 * i]) for i in range(5)]; continue
    elif cur and r[0].replace(" ", "") == "가동율":
        capa[cur]["util"] = [float(c.strip("%")) if c.strip() not in ("-", "") else None for c in r[1:6]]; continue
    else:
        continue
    if cur == "합 계":
        cur = None; continue
    capa[cur] = {"capa": [num(r[st + 2 * i]) for i in range(5)]}
capa = {k: v for k, v in capa.items() if "prod" in v}

KEYS = ["2026.06", "2025.12", "2024.12", "2023.12"]
KIND = {"2026.06": "반기", "2025.12": "사업", "2024.12": "사업", "2023.12": "사업"}
COL4 = {"2026.06": 0, "2025.12": 1, "2024.12": 2, "2023.12": 3}   # BS/CF/연결 IS 4열 인덱스
COL5 = {"2026.06": 0, "2025.12": 2, "2024.12": 3, "2023.12": 4}   # inc/seg/region/capa 5열 인덱스
periods_raw = {}
for k in KEYS:
    p = {}
    for nm, v in bs.items():
        p[nm] = {"당기": v[COL4[k]], "전기": None, "당기누적": None, "전기누적": None, "전전기": None, "account_nm": nm}
    for nm, v in inc.items():
        x = v[COL5[k]]
        p[nm] = {"당기": x, "전기": None, "당기누적": x if k == "2026.06" else None, "전기누적": (v[1] if k == "2026.06" else None), "전전기": None, "account_nm": nm}
    for nm, v in cf.items():
        x = v[COL4[k]]
        p[nm] = {"당기": x, "전기": None, "당기누적": x if k == "2026.06" else None, "전기누적": None, "전전기": None, "account_nm": nm}
    periods_raw[k] = p
p25 = {}
for nm, v in inc.items():
    p25[nm] = {"당기": v[1], "전기": None, "당기누적": v[1], "전기누적": None, "전전기": None, "account_nm": nm}
for nm in cf:
    p25[nm] = {"당기": None, "전기": None, "당기누적": None, "전기누적": None, "전전기": None, "account_nm": nm}
periods_raw["2025.06"] = p25


def cfrows(idx):
    out = []
    for r in CF:
        v = num(r[1 + idx]) if len(r) > 1 + idx else None
        if v is not None and r[0]:
            out.append([r[0], v])
    return out


for k in ("2026.06", "2025.12", "2024.12"):
    periods_raw[k]["_cf"] = cfrows(COL4[k])

items = {}
for nm in list(bs) + list(inc) + list(cf):
    if nm in bs:
        v = bs[nm]; cur_, prv_, pp_ = v[1], v[2], v[3]
    elif nm in inc:
        v = inc[nm]; cur_, prv_, pp_ = v[2], v[3], v[4]
    else:
        v = cf[nm]; cur_, prv_, pp_ = v[1], v[2], v[3]
    items[nm] = {"당기": cur_, "전기": prv_, "당기누적": None, "전기누적": None, "전전기": pp_, "account_nm": nm}
items["_cf"] = cfrows(1)

q = []
for k, lbl, j in (("2026.06", "26.2Q", 0), ("2025.06", "25.2Q", 1)):
    vals = {nm: inc[nm][j] for nm in inc}
    if j == 0:
        vals.update({nm: cf[nm][0] for nm in ("영업활동현금흐름", "투자활동현금흐름", "재무활동현금흐름")})
    q.append({"key": k, "label": lbl, "values": vals, "how": "직접", "check": ""})
yoy = {nm: round((inc[nm][0] / inc[nm][1] - 1) * 100, 1) for nm in ("매출액",) if inc[nm][1]}
segments = []
for k in KEYS:
    segments.append({"key": k, "kind": KIND[k], "items": {nm: v[COL5[k]] * 1e6 for nm, v in seg.items() if v[COL5[k]]}})
segments.sort(key=lambda x: x["key"])
region_hist = sorted([{"key": k, "kind": KIND[k], "dom": dom[COL5[k]] * 1e6, "exp": exp[COL5[k]] * 1e6} for k in KEYS], key=lambda x: x["key"])
capa_hist = []
for k in KEYS:
    g = {}
    for nm, d in capa.items():
        j = COL5[k]
        if d["capa"][j] is not None:
            g[nm] = {"unit": "대", "capa": d["capa"][j], "prod": d["prod"][j], "util": d["util"][j]}
    capa_hist.append({"key": k, "kind": KIND[k], "groups": g})
capa_hist.sort(key=lambda x: x["key"])

fin = {"basis": "연결", "year": 2025, "unit": "원", "items": items, "quarters": q, "yoy_latest": yoy, "latest_period": "2026.06",
       "periods_raw": periods_raw, "dividend": {}, "capex_notices": [], "region_hist": region_hist, "capa_hist": capa_hist,
       "cost_files": ["__none__.json"], "ltm": {nm: inc[nm][2] + inc[nm][0] - inc[nm][1] for nm in ("매출액", "영업이익", "당기순이익")},
       "orders_hist": [], "segments": segments, "insider": [], "contracts": []}
rc = re.search(r"(\d{14})", fname).group(1)
meta = {"corp_name": "티앤이코리아", "stock_code": code, "corp_code": "01543043",
        "report_nm": "투자설명서(기재정정) 2026.10.16 — 정기보고서 없음(신규상장)", "rcept_no": rc, "rcept_dt": rc[:8],
        "latest_report_nm": "투자설명서(기재정정) — 2026년 반기(제13기) 검토 연결재무제표 포함", "latest_rcept_no": rc, "latest_rcept_dt": rc[:8],
        "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), "source": "prospectus"}
json.dump({"meta": meta, "fin": fin}, open(os.path.join(base, "fin.json"), "w", encoding="utf-8"), ensure_ascii=False)
print("fin.json OK", {k: round(v[0] / 1e8, 1) for k, v in inc.items()}, "1H25", {k: round(v[1] / 1e8, 1) for k, v in inc.items()}, yoy)
print("capa groups", list(capa), "region 1H26 수출비중", round(exp[0] / tot[0] * 100, 1))
