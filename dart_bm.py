#!/usr/bin/env python3
"""dart_bm.py - DART 사업보고서 -> bm_card.md (LLM 없음, 수집/가공/계산만)

사용법:  python dart_bm.py 028670      (종목코드 6자리)
         python dart_bm.py 팬오션      (기업명, 상장사 정확 일치)
필요:    환경변수 DART_API_KEY
출력:    out/{종목코드}/bm_card.md, biz_full.txt, fin.json
"""
import argparse, gzip
import datetime
import io
import json
import os
import re
import sys
import time
import zipfile
import xml.etree.ElementTree as ET

import warnings

import requests
from bs4 import BeautifulSoup, NavigableString, XMLParsedAsHTMLWarning

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

API = "https://opendart.fss.or.kr/api"
KEY = os.environ.get("DART_API_KEY", "").strip()
NF = "not_found"
# 한글은 대략 1.5~2자/토큰 -> 6500자 ~= 3~4k 토큰 (분기 표·최신 보고서 수치 추가로 상향)
CARD_CHAR_LIMIT = 6500


# ---------------------------------------------------------------- HTTP
def get(path, **params):
    """DART 호출. 오류 메시지에 URL(=API 키)이 섞이지 않게 감싼다."""
    params["crtfc_key"] = KEY
    last = None
    for i in range(3):
        try:
            r = requests.get(f"{API}/{path}", params=params, timeout=60)
            r.raise_for_status()
            return r
        except requests.RequestException as e:
            last = type(e).__name__
            time.sleep(2 * (i + 1))
    raise RuntimeError(f"{path} 요청 실패 ({last})") from None


def get_json(path, **params):
    j = get(path, **params).json()
    if j.get("status") == "020":
        raise RuntimeError("DART 일일 호출 한도 초과(020). 내일 다시 실행하세요.")
    return j


# ---------------------------------------------------------------- 기업 식별
def load_corps():
    r = get("corpCode.xml")
    if not zipfile.is_zipfile(io.BytesIO(r.content)):
        raise RuntimeError("corpCode.xml 응답이 ZIP이 아님: API 키를 확인하세요.")
    z = zipfile.ZipFile(io.BytesIO(r.content))
    root = ET.fromstring(z.read(z.namelist()[0]))
    corps = []
    for e in root.iter("list"):
        corps.append({k: (e.findtext(k) or "").strip()
                      for k in ("corp_code", "corp_name", "stock_code")})
    return corps


CACHE = "cache"


def cached_corp(query):
    """종목코드 6자리면 캐시된 corp_code 사용 → corpCode.xml(대용량) 다운로드 생략."""
    q = query.strip()
    if not re.fullmatch(r"\d{6}", q):
        return None
    try:
        return json.load(open(os.path.join(CACHE, "corp_map.json"), encoding="utf-8")).get(q)
    except (OSError, ValueError):
        return None


def save_corp(c):
    os.makedirs(CACHE, exist_ok=True)
    fp = os.path.join(CACHE, "corp_map.json")
    try:
        m = json.load(open(fp, encoding="utf-8"))
    except (OSError, ValueError):
        m = {}
    m[c["stock_code"]] = c
    json.dump(m, open(fp, "w", encoding="utf-8"), ensure_ascii=False, indent=0)


def get_biz(rcept_no):
    """보고서는 접수번호 단위로 불변 → '사업의 내용' 구간을 캐시(gz). 새 보고서만 DART에서 내려받는다."""
    fp = os.path.join(CACHE, "biz", f"{rcept_no}.txt.gz")
    if os.path.exists(fp):
        try:
            return extract_biz(gzip.open(fp, "rt", encoding="utf-8").read().split("\n"))
        except OSError:
            pass
    b = extract_biz(to_text(fetch_doc(rcept_no)))
    if b:
        os.makedirs(os.path.dirname(fp), exist_ok=True)
        with gzip.open(fp, "wt", encoding="utf-8") as f:
            f.write("\n".join(b["all"]))
    return b


def resolve(query, corps):
    listed = [c for c in corps if c["stock_code"]]
    q = query.strip()
    if re.fullmatch(r"\d{6}", q):
        hit = [c for c in listed if c["stock_code"] == q]
    else:
        hit = [c for c in listed if c["corp_name"] == q]
        if not hit:
            hit = [c for c in listed if q in c["corp_name"]]
    if not hit:
        raise RuntimeError(f"상장사를 찾지 못함: '{q}' (비상장이거나 이름 불일치). 종목코드 6자리로 다시 시도하세요.")
    if len(hit) > 1:
        cands = ", ".join(f"{c['corp_name']}({c['stock_code']})" for c in hit[:10])
        raise RuntimeError(f"후보가 여러 개: {cands} -> 종목코드로 다시 실행하세요.")
    return hit[0]


# ---------------------------------------------------------------- 보고서 조회
PERIODIC_RE = re.compile(r"(사업|반기|분기)보고서\s*\((\d{4})\.(\d{2})\)")


def list_periodic(corp_code):
    """최근 3년 정기보고서(사업/반기/분기)를 기간별 1건(정정본 포함 최신 접수)으로 정리. 최신 기간 순."""
    today = datetime.date.today()
    bgn = today - datetime.timedelta(days=365 * 3)
    rows, page, total = [], 1, 1
    while page <= total and page <= 10:
        j = get_json("list.json", corp_code=corp_code,
                     bgn_de=bgn.strftime("%Y%m%d"), end_de=today.strftime("%Y%m%d"),
                     pblntf_ty="A", page_count=100, page_no=page)
        rows += j.get("list") or []
        total = int(j.get("total_page") or 1)
        page += 1
    best = {}
    for r in rows:
        m = PERIODIC_RE.search(r.get("report_nm", ""))
        if not m:
            continue  # 자율공시·기타 공시 제외
        key = (int(m.group(2)), int(m.group(3)))
        r = dict(r, kind=m.group(1), year=key[0], month=key[1])
        if key not in best or (r["rcept_dt"], r["rcept_no"]) > (best[key]["rcept_dt"], best[key]["rcept_no"]):
            best[key] = r
    reps = [best[k] for k in sorted(best, reverse=True)]
    if not any(r["kind"] == "사업" for r in reps):
        raise RuntimeError("사업보고서 없음 (최근 3년 내 정기보고서 목록에 '사업보고서 (YYYY.MM)' 없음)")
    return reps


def qnum(month, fy_end=12):
    """결산월(fy_end) 기준 분기 번호: 12월 결산이면 3월=1, 6월=2, 9월=3, 12월=4"""
    rem = (month - fy_end) % 12
    return rem // 3 if rem else 4


def prev_period(y, m):
    m -= 3
    return (y - 1, m + 12) if m <= 0 else (y, m)


def reprt_code(r, fy_end=12):
    if r["kind"] == "사업":
        return "11011"
    if r["kind"] == "반기":
        return "11012"
    return "11013" if qnum(r["month"], fy_end) == 1 else "11014"


# ---------------------------------------------------------------- 재무
def norm(s):
    return re.sub(r"\s+", "", s or "")


def to_int(s):
    try:
        return int(str(s).replace(",", "").strip())
    except (ValueError, TypeError):
        return None


FIN_ITEMS = [
    ("매출액", ("IS", "CIS"), ["매출액", "수익(매출액)", "영업수익", "매출"]),
    ("영업이익", ("IS", "CIS"), ["영업이익", "영업이익(손실)", "영업손익"]),
    ("당기순이익", ("IS", "CIS"), ["당기순이익", "당기순이익(손실)", "연결당기순이익", "당기순손익"]),
    ("자산총계", ("BS",), ["자산총계"]),
    ("부채총계", ("BS",), ["부채총계"]),
    ("자본총계", ("BS",), ["자본총계"]),
]
FLOWS = [x[0] for x in FIN_ITEMS[:3]]
# 대시보드/fin.json 전용 추가 항목 (카드 표에는 넣지 않음)
EXTRA_ITEMS = [
    ("영업활동현금흐름", ("CF",), ["영업활동현금흐름", "영업활동으로인한현금흐름", "영업활동순현금흐름"]),
    ("투자활동현금흐름", ("CF",), ["투자활동현금흐름", "투자활동으로인한현금흐름", "투자활동순현금흐름"]),
    ("재무활동현금흐름", ("CF",), ["재무활동현금흐름", "재무활동으로인한현금흐름", "재무활동순현금흐름"]),
    ("현금및현금성자산", ("BS",), ["현금및현금성자산"]),
    ("재고자산", ("BS",), ["재고자산"]),
    ("매출채권", ("BS",), ["매출채권", "매출채권및기타채권", "매출채권및기타유동채권"]),
    ("유동자산", ("BS",), ["유동자산"]),
    ("유동부채", ("BS",), ["유동부채"]),
    ("매출총이익", ("IS", "CIS"), ["매출총이익", "매출총이익(손실)"]),
]


def pick_fin(rows):
    """당기/전기 = thstrm/frmtrm_amount, 당기누적/전기누적 = *_add_amount (분기·반기 보고서에서 3개월 vs 누적 구분용)"""
    out = {}
    for label, divs, cands in FIN_ITEMS + EXTRA_ITEMS:
        found = None
        for cand in cands:
            for r in rows:
                if r.get("sj_div") in divs and norm(r.get("account_nm")) == norm(cand):
                    found = {"당기": to_int(r.get("thstrm_amount")),
                             "전기": to_int(r.get("frmtrm_amount")),
                             "당기누적": to_int(r.get("thstrm_add_amount")),
                             "전기누적": to_int(r.get("frmtrm_add_amount")),
                             "전전기": to_int(r.get("bfefrmtrm_amount")),
                             "account_nm": r.get("account_nm")}
                    break
            if found:
                break
        if not found and label == "매출액":  # 계정명이 표준과 다른 보고서: IS 첫 '수익/매출' 계열 행으로 대체
            for r in rows:
                nm = norm(r.get("account_nm"))
                if r.get("sj_div") in divs and re.search(r"(매출액|영업수익|^수익|^매출$)", nm) and not re.search(r"원가|총이익|채권|비용|손익", nm):
                    found = {"당기": to_int(r.get("thstrm_amount")), "전기": to_int(r.get("frmtrm_amount")),
                             "당기누적": to_int(r.get("thstrm_add_amount")), "전기누적": to_int(r.get("frmtrm_add_amount")),
                             "전전기": to_int(r.get("bfefrmtrm_amount")), "account_nm": r.get("account_nm")}
                    break
            if not found:
                names = [r.get("account_nm") for r in rows if r.get("sj_div") in divs][:12]
                print(f"[경고] 매출액 계정 못 찾음. IS 계정 후보: {names}", file=sys.stderr)
        out[label] = found
    return out


def fetch_period(corp_code, year, code, bases=("CFS", "OFS")):
    for div in bases:
        j = get_json("fnlttSinglAcntAll.json", corp_code=corp_code,
                     bsns_year=str(year), reprt_code=code, fs_div=div)
        if j.get("status") == "000" and j.get("list"):
            return {"basis": "연결" if div == "CFS" else "별도", "div": div, "items": pick_fin(j["list"])}
    return None


def cum(it):
    """누적값: 누적 필드가 있으면 그것, 없으면 당기 값(1Q·사업보고서는 이미 누적과 같음)"""
    if not it:
        return None
    return it["당기누적"] if it.get("당기누적") is not None else it["당기"]


def build_quarters(periods, latest_key, fy_end=12, n=8):
    """periods: {(연,월): {항목: pick_fin 결과}} -> 최근 n개 분기 단독 값(원 단위). 최신 분기부터."""
    out, key = [], latest_key
    for _ in range(n):
        y, m = key
        q = qnum(m, fy_end)
        cur = periods.get(key)
        prev = periods.get(prev_period(y, m)) if q > 1 else None
        vals, how, check = {}, "-", ""
        for label in FLOWS:
            c = cur.get(label) if cur else None
            v = None
            if c is not None:
                if q == 1:
                    v, how = cum(c), "직접"
                elif prev and prev.get(label) and cum(c) is not None and cum(prev[label]) is not None:
                    v, how = cum(c) - cum(prev[label]), "누적차"
                    d = c.get("당기") if (q in (2, 3) and c.get("당기누적") is not None) else None
                    if d is not None and abs(v - d) > max(1, abs(d) * 0.005):
                        check = "불일치"
                elif q in (2, 3) and c.get("당기누적") is not None:
                    v, how = c["당기"], "직접"
            vals[label] = v
        out.append({"key": f"{y}.{m:02d}", "label": f"{y % 100}.{q}Q", "values": vals, "how": how, "check": check})
        key = prev_period(y, m)
    return out


def latest_yoy(periods, key, fy_end=12):
    """최신 분기의 전년동기 대비(%). 보고서가 직접 주는 3개월 값끼리만 비교(4Q는 제외)."""
    cur = periods.get(key)
    q = qnum(key[1], fy_end)
    if not cur or q == 4:
        return {}
    out = {}
    for label in FLOWS:
        it = cur.get(label)
        prev = it.get("전기") if it else None
        if prev is None:  # 반기 등: 전기(3개월) 미제공 → 전년 동일 보고서의 당기(3개월) 사용
            py = periods.get((key[0] - 1, key[1]), {}).get(label)
            prev = py.get("당기") if py else None
        if it and (q == 1 or it.get("당기누적") is not None) and it["당기"] is not None and prev:
            out[label] = (it["당기"] - prev) / abs(prev) * 100
    return out


def collect_fin(corp_code, reps):
    fy_end = next(r for r in reps if r["kind"] == "사업")["month"]
    annual = next(r for r in reps if r["kind"] == "사업")
    latest = reps[0]
    by_key = {(r["year"], r["month"]): r for r in reps}
    ak, lk = (annual["year"], annual["month"]), (latest["year"], latest["month"])
    need = [ak]
    if fy_end == 12:
        key = lk
        for _ in range(8):
            need.append(key)
            if qnum(key[1], fy_end) > 1:
                need.append(prev_period(*key))
            key = prev_period(*key)
    need.append((lk[0] - 1, lk[1]))  # 전년동기 3개월 값(YoY용)
    periods, basis_by_key, div0, seen = {}, {}, None, set()
    for key in need:
        if key in seen or key not in by_key:
            continue
        seen.add(key)
        r = by_key[key]
        bases = (div0, "OFS" if div0 == "CFS" else "CFS") if div0 else ("CFS", "OFS")
        res = fetch_period(corp_code, r["year"], reprt_code(r, fy_end), bases)
        if res:
            periods[key] = res["items"]
            basis_by_key[key] = res["basis"]
            div0 = div0 or res["div"]
    ann = periods.get(ak)
    fin = {"basis": basis_by_key.get(ak, NF), "year": annual["year"], "unit": "원",
           "items": ann or {}, "quarters": [], "yoy_latest": {}, "latest_period": f"{lk[0]}.{lk[1]:02d}"}
    if fy_end == 12:
        fin["quarters"] = build_quarters(periods, lk, fy_end)
        fin["yoy_latest"] = latest_yoy(periods, lk, fy_end)
    if len(set(basis_by_key.values())) > 1:
        fin["note"] = f"연결/별도 혼재: {basis_by_key}"
    fin["periods_raw"] = {f"{k[0]}.{k[1]:02d}": v for k, v in periods.items()}
    return fin


def eok(v):
    return NF if v is None else f"{v / 1e8:,.0f}"


def quarter_block(fin):
    qs = (fin.get("quarters") or [])[:4]  # 카드는 4개 분기만(8개 분기는 fin.json/대시보드)
    if not qs:
        return ""
    lines = [f"### 최근 4개 분기 (단독, 억원, {fin['basis']}; 4Q=연간-3Q누적 계산값)",
             "| 항목 | " + " | ".join(q["label"] for q in qs) + " |",
             "| --- |" + " --- |" * len(qs)]
    for label in FLOWS:
        lines.append(f"| {label} | " + " | ".join(eok(q["values"].get(label)) for q in qs) + " |")
    opm = []
    for q in qs:
        r, o = q["values"].get("매출액"), q["values"].get("영업이익")
        opm.append(f"{o / r * 100:.1f}%" if r and o is not None else "-")
    lines.append("| 영업이익률(계산) | " + " | ".join(opm) + " |")
    yoy = fin.get("yoy_latest") or {}
    if yoy:
        lines.append(f"- {qs[0]['label']} 전년동기 대비(보고서 직접값): "
                     + ", ".join(f"{k} {v:+.1f}%" for k, v in yoy.items()))
    bad = [q["label"] for q in qs if q.get("check")]
    if bad:
        lines.append(f"- 주의: 누적차 계산과 보고서 직접값이 다른 분기 {bad} (정정·회계변경 가능성, 원문 확인)")
    if fin.get("note"):
        lines.append(f"- 주의: {fin['note']}")
    return "\n".join(lines) + "\n"


def fin_block(fin):
    if fin["basis"] == NF:
        return f"## 0. 재무 요약\n재무제표 API 응답 {NF}\n"
    y = fin["year"]
    lines = [f"## 0. 재무 요약 ({fin['basis']}, 단위 억원, 연간 FY{y} vs FY{y - 1})",
             "| 항목 | 당기 | 전기 | 증감% |", "| --- | --- | --- | --- |"]
    for label, _, _ in FIN_ITEMS:
        it = fin["items"].get(label)
        if not it:
            lines.append(f"| {label} | {NF} | {NF} | - |")
            continue
        cur, prv = it["당기"], it["전기"]
        chg = f"{(cur - prv) / abs(prv) * 100:+.1f}" if cur is not None and prv else "-"
        lines.append(f"| {label} | {eok(cur)} | {eok(prv)} | {chg} |")
    rev, op = fin["items"].get("매출액"), fin["items"].get("영업이익")
    if rev and op and rev["당기"] and op["당기"] is not None:
        lines.append(f"- 영업이익률(계산: 영업이익/매출액): {op['당기'] / rev['당기'] * 100:.1f}%")
    return "\n".join(lines) + "\n" + quarter_block(fin)


# ---------------------------------------------------------------- 원문
def decode(b):
    for enc in ("utf-8", "euc-kr", "cp949"):
        try:
            return b.decode(enc)
        except UnicodeDecodeError:
            pass
    return b.decode("utf-8", errors="replace")


def fetch_doc(rcept_no):
    r = get("document.xml", rcept_no=rcept_no)
    if not zipfile.is_zipfile(io.BytesIO(r.content)):
        raise RuntimeError("원문이 ZIP이 아님: " + decode(r.content)[:200])
    z = zipfile.ZipFile(io.BytesIO(r.content))
    names = [n for n in z.namelist() if n.lower().endswith((".xml", ".html", ".htm"))]
    if not names:
        raise RuntimeError("ZIP 안에 원문 파일 없음")
    best = max(names, key=lambda n: z.getinfo(n).file_size)
    return decode(z.read(best))


def table_to_md(table):
    rows = []
    for tr in table.find_all("tr"):
        cells = [re.sub(r"\s+", " ", c.get_text(" ", strip=True)).replace("|", "/")
                 for c in tr.find_all(["td", "th", "te", "tu"])]
        if any(cells):
            rows.append("| " + " | ".join(cells) + " |")
    return "\n".join(rows)


def to_text(raw):
    soup = BeautifulSoup(raw, "html.parser")
    for t in soup.find_all(["style", "script"]):
        t.decompose()
    for table in reversed(soup.find_all("table")):  # 안쪽 표부터
        table.replace_with(NavigableString("\n" + table_to_md(table) + "\n"))
    for br in soup.find_all("br"):
        br.replace_with(NavigableString("\n"))
    for tag in soup.find_all(["p", "title", "section-1", "section-2", "section-3", "li", "div"]):
        tag.append(NavigableString("\n"))
    text = soup.get_text("")
    lines = [re.sub(r"[ \t　\xa0]+", " ", l).strip() for l in text.split("\n")]
    out, blank = [], False
    for l in lines:
        if not l:
            if not blank:
                out.append("")
            blank = True
        else:
            out.append(l)
            blank = False
    return out


SUBS = [
    ("overview", r"사업의\s*개요"),
    ("products", r"주요\s*제품\s*(및|과)\s*서비스"),
    ("materials", r"원재료\s*(및|와)\s*생산설비"),
    ("sales", r"매출\s*(및|과)\s*수주\s*상황"),
    ("risk", r"위험관리"),
    ("rnd", r"주요\s*계약\s*(및|과)\s*연구개발"),
    ("etc", r"기타\s*참고\s*사항"),
]


def extract_biz(lines):
    """'II. 사업의 내용' ~ 'III. 재무에 관한 사항' 중 가장 긴 구간(목차 중복 회피)."""
    st = [i for i, l in enumerate(lines) if re.match(r"^\s*(II|Ⅱ)\s*\.\s*사업의\s*내용", l)]
    en = [i for i, l in enumerate(lines) if re.match(r"^\s*(III|Ⅲ)\s*\.\s*재무에\s*관한\s*사항", l)]
    best = None
    for s in st:
        e = next((x for x in en if x > s), len(lines))
        if best is None or (e - s) > (best[1] - best[0]):
            best = (s, e)
    if not best:
        return None
    sec = lines[best[0]:best[1]]
    marks = []
    for key, pat in SUBS:
        for i, l in enumerate(sec):
            if re.match(r"^\s*\d+\s*\.\s*" + pat, l):
                marks.append((i, key))
                break
    marks.sort()
    subs = {}
    for n, (i, key) in enumerate(marks):
        j = marks[n + 1][0] if n + 1 < len(marks) else len(sec)
        subs[key] = sec[i:j]
    return {"all": sec, "subs": subs}


ORDER_KWS = ["수주잔고", "수주총액", "수주일자", "수주 잔고", "수주상황", "수주금액", "수주잔액"]


def order_tables(biz, maxn=3, maxrows=25):
    """수주 현황 표(수주잔고·수주총액 등)를 원문 그대로 추출. 없으면 빈 리스트."""
    if not biz:
        return []
    out, prev = [], None
    for b in split_blocks(biz["all"]):
        pb, prev = prev, b
        if b["type"] != "table" or len(b["rows"]) < 2:
            continue
        head = " ".join(b["rows"][:2]) + " " + (b.get("caption") or "")
        unit = ""
        if pb and pb["type"] == "table" and len(pb["rows"]) == 1 and "단위" in pb["rows"][0]:
            unit = re.sub(r"[|\s]+", " ", pb["rows"][0]).strip()  # 표 바로 앞 '(단위 : US$1,000)' 한 줄 표
        if any(k in head for k in ORDER_KWS) and not re.match(r"^주\d*\)", norm(b["rows"][0].strip("| ").split("|")[0])):
            out.append({"caption": clip(b.get("caption") or "", 120), "unit": unit, "rows": [clip(r, 200) for r in b["rows"][:maxrows]]})
        if len(out) >= maxn:
            break
    return out


# ---------------------------------------------------------------- 최근 주요 공시 (자금조달·계약 등)
DISC_RE = re.compile(r"자기주식|주식소각|소각|배당|유상증자|무상증자|전환사채|신주인수권|전환가액|전환주식|교환사채|자기주식|합병|분할|양수|양도|"
                     r"단일판매|공급계약|시설투자|신규시설|타법인|출자|감자|횡령|소송|회생|불성실|투자판단관련|조회공시|영업정지")


def list_disclosures(corp_code, days=200):
    """주요사항보고(B)·거래소공시(I)·지분공시(D 제외)에서 키워드 해당 공시를 최신순으로."""
    end = datetime.date.today()
    bgn = end - datetime.timedelta(days=days)
    out, seen = [], set()
    for ty in ("B", "I"):
        for page in range(1, 6):
            j = get_json("list.json", corp_code=corp_code, bgn_de=bgn.strftime("%Y%m%d"),
                         end_de=end.strftime("%Y%m%d"), pblntf_ty=ty, page_no=page, page_count=100)
            if j.get("status") != "000":
                break
            for it in j.get("list", []):
                nm = it["report_nm"]
                if DISC_RE.search(nm) and it["rcept_no"] not in seen:
                    seen.add(it["rcept_no"])
                    out.append({"rcept_no": it["rcept_no"], "report_nm": nm.strip(), "rcept_dt": it["rcept_dt"]})
            if page >= int(j.get("total_page", 1) or 1):
                break
    out.sort(key=lambda x: (x["rcept_dt"], x["rcept_no"]), reverse=True)
    return out


def save_disclosures(items, d, maxn=8):
    """핵심 공시 원문(표 포함)을 out/{code}/disc/ 에 저장. 반환: 저장 파일 수."""
    dd = os.path.join(d, "disc")
    os.makedirs(dd, exist_ok=True)
    n = 0
    for it in items[:maxn]:
        try:
            txt = "\n".join(to_text(fetch_doc(it["rcept_no"])))
        except RuntimeError as e:
            print(f"[경고] 공시 원문 실패 {it['report_nm']}: {e}", file=sys.stderr)
            continue
        with open(os.path.join(dd, f"{it['rcept_dt']}_{it['rcept_no']}.txt"), "w", encoding="utf-8") as f:
            f.write(f"# {it['report_nm']} / {it['rcept_dt']} / {it['rcept_no']}\n{txt}")
        n += 1
    return n


def disc_block(items, n=10):
    if not items:
        return ""
    lines = [f"- {it['rcept_dt'][:4]}-{it['rcept_dt'][4:6]}-{it['rcept_dt'][6:]} {it['report_nm']}" for it in items[:n]]
    return "\n".join(lines)


# ---------------------------------------------------------------- 인접 보고서 diff (STEP 2)
DIFF_KWS = ["유상증자", "전환우선주", "전환사채", "신주", "증자", "조달", "소송", "횡령", "배당", "자사주", "수주", "증설", "신규", "계약", "중단", "철수", "인수", "매각", "처분", "취득", "투자", "건조", "도입",
            "고객", "원가", "가동", "감소", "증가", "확대", "축소", "신설", "개발", "승인", "허가", "임상", "양산", "공급"]


def strip_risk(lines):
    """위험관리·파생거래 소항목(분기보고서 사업의 내용에 포함, BM 변화와 무관한 주석성 문장)은 diff에서 제외."""
    out, skip = [], False
    for l in lines:
        m = re.match(r"^\s*\d+\s*\.\s*(\S.*)", l)
        if m and not l.startswith("|"):
            skip = bool(re.match(r"위험\s*관리|파생", m.group(1)))
        if not skip:
            out.append(l)
    return out


def sentences(lines):
    """표 제외 본문 줄을 문장 단위로 쪼갠다. 반환: [(정규화키, 원문)]"""
    out, seen = [], set()
    for l in lines:
        if l.startswith("|") or not l.strip():
            continue
        for sent in re.split(r"(?<=[.다])\s+(?=[^\s])|(?<=다\.)(?=[가-힣(])", l.strip()):
            sent = sent.strip()
            if len(sent) < 20 or "참고하시기" in sent or sent.startswith("그 밖에"):
                continue
            key = re.sub(r"[\d,\.]+", "#", re.sub(r"\s+", "", sent))  # 숫자만 바뀐 문장은 같은 문장으로 취급
            if key not in seen:
                seen.add(key)
                out.append((key, sent))
    return out


def _tok(s):
    return set(re.findall(r"[가-힣A-Za-z]{2,}", s))


def new_sentences(old_lines, new_lines):
    """new에만 있는 문장. 숫자 마스킹 일치 또는 단어 집합 유사도 0.8 이상이면 '수정'으로 보고 제외."""
    old = sentences(old_lines)
    old_keys = {k for k, _ in old}
    old_toks = [_tok(t) for _, t in old]
    res = []
    for key, sent in sentences(new_lines):
        if key in old_keys:
            continue
        t = _tok(sent)
        if t and any(len(t & o) / len(t | o) >= 0.8 for o in old_toks if o):
            continue
        res.append(sent)
    return res


def diff_score(sent):
    return sum(sent.count(k) for k in DIFF_KWS)


def build_diff(chain):
    """chain: [(라벨, 줄목록)] 최신 → 과거 순. 인접 쌍별 신규 문장(키워드 점수순). 반환 [(구간라벨, [문장...])]"""
    out = []
    for i in range(len(chain) - 1):
        nl, nlines = chain[i]
        ol, olines = chain[i + 1]
        news = new_sentences(olines, nlines)
        news.sort(key=lambda x: -diff_score(x))
        out.append((f"{ol} → {nl}", news))
    return out


def diff_card(diffs, budget=800):
    if not diffs:
        return ""
    parts, used = [], 0
    for n, (lab, news) in enumerate(diffs):
        take = 4 if n == 0 else 2
        picked = [x for x in news if diff_score(x) > 0][:take] or news[:1]
        lines = [f"- {clip(x, 110)}" for x in picked]
        blk = f"[{lab}] 신규 문장 {len(news)}개 중 핵심\n" + ("\n".join(lines) if lines else f"- (신규 문장 없음)")
        if used + len(blk) > budget and parts:
            break
        parts.append(blk)
        used += len(blk)
    return "\n".join(parts)


def diff_full(diffs):
    out = []
    for lab, news in diffs:
        out.append(f"===== {lab} (신규 문장 {len(news)}개, 키워드 점수순) =====")
        out += [f"[{diff_score(x)}] {x}" for x in news]
        out.append("")
    return "\n".join(out)


# ---------------------------------------------------------------- 카드용 선별
def split_blocks(lines):
    blocks, cur, cap, prev = [], [], "", ""
    for l in lines:
        if l.startswith("|"):
            if not cur:
                cap = prev
            cur.append(l)
        else:
            if cur:
                blocks.append({"type": "table", "rows": cur, "caption": cap})
                cur = []
            if l.strip():
                blocks.append({"type": "text", "text": l.strip()})
                prev = l.strip()
    if cur:
        blocks.append({"type": "table", "rows": cur, "caption": cap})
    return blocks


def clip(s, n):
    return s if len(s) <= n else s[:n] + "…"


SKIP_ROWS = {"자산", "부채"}  # 세그먼트 표의 자산/부채 행은 BM 판단에 불필요


def pick_tables(blocks, kws, maxn, maxrows, exclude=(), rowclip=160):
    out = []
    for b in blocks:
        if b["type"] != "table" or len(b["rows"]) < 2:
            continue
        if re.match(r"^주\d*\)", norm(b["rows"][0].strip("| ").split("|")[0])):
            continue  # '주) ...' 각주 표 제외
        joined = " ".join(b["rows"])
        if not any(k in joined for k in kws):
            continue
        rows = [r for r in b["rows"]
                if norm(r.strip("| ").split("|")[0]) not in SKIP_ROWS]
        part = []
        if b["caption"] and len(b["caption"]) <= 80:
            part.append(f"*{b['caption']}*")
        part += [clip(r, rowclip) for r in rows[:maxrows]]
        if len(rows) > maxrows:
            part.append(f"(… {len(rows) - maxrows}행 생략, 원문은 biz_full.txt)")
        text = "\n".join(part)
        if text in exclude:
            continue
        out.append(text)
        if len(out) >= maxn:
            break
    return out


def keyword_lines(blocks, kws, maxn):
    out, seen = [], set()
    for b in blocks:
        if b["type"] != "text" or len(b["text"]) < 20:
            continue
        if any(k in b["text"] for k in kws) and b["text"] not in seen:
            seen.add(b["text"])
            out.append("- " + clip(b["text"], 180))
            if len(out) >= maxn:
                break
    return out


BIZ_SENT = re.compile(r"(당사|회사|동사)[는은가이].{0,80}(영위|제조|판매|생산|설계|공급|개발|운영|제공|사업)")


def first_paragraph(blocks, n=350):
    for b in blocks:  # 시장 해설보다 '당사는 ~ 사업을 영위' 류 문장을 우선
        if b["type"] == "text" and len(b["text"]) >= 40 and BIZ_SENT.search(b["text"][:200]):
            return clip(b["text"], n)
    for b in blocks:
        if b["type"] == "text" and len(b["text"]) >= 40:
            return clip(b["text"], n)
    return NF


QUESTIONS = """1. 누구에게(고객·산업) 무엇을(제품/서비스) 파는가?
2. 매출 중 장기계약·고정 비중 vs 스팟·시황 연동 비중은? (근거 문장 확인)
3. 이익은 어느 부문에서 나는가? 부문별 마진 차이는?
4. 원가의 핵심 변수는 무엇이고, 가격 전가가 되는가?
5. 설비/선대/CAPA는 얼마이고, 보유 vs 임차(용선) 구조와 증설 계획은?
6. 매출처 집중도·수출 비중에 따른 리스크는?
7. 직접 피어(동일 BM)와 밸류체인상 연관 기업을 구분 (웹검색 필요, 이 카드에는 없음)"""


def section(title, parts, fallback=NF):
    body = "\n".join(p for p in parts if p).strip()
    return f"## {title}\n{body if body else fallback}\n"


def build_card(meta, fin, biz, rows, nlines, latest_biz=None, latest_nm="", diff_md="", disc_md=""):
    subs = biz["subs"] if biz else {}
    allb = split_blocks(biz["all"]) if biz else []
    B = {k: split_blocks(v) for k, v in subs.items()}
    sales = B.get("sales", [])
    mats = B.get("materials", [])
    rnd = B.get("rnd", [])
    cap_kws = ["생산능력", "운항가능일수", "가동률", "CAPA", "Capa", "선대", "DWT", "dwt", "용선", "신설", "투자대상"]

    head = (f"# {meta['corp_name']} ({meta['stock_code']}) BM 카드\n"
            f"- BM 서술 출처: DART {meta['report_nm']} / 접수번호 {meta['rcept_no']} / 접수일 {meta['rcept_dt']}\n")
    if meta.get("latest_report_nm") and meta["latest_report_nm"] != meta["report_nm"]:
        head += (f"- 최신 정기보고서(분기 재무·부문 수치): {meta['latest_report_nm']} / "
                 f"접수번호 {meta['latest_rcept_no']} / 접수일 {meta['latest_rcept_dt']}\n")
    head += f"- 아래는 공시 원문 발췌(기업 서술 포함)와 코드 계산값이며 해석 아님. 미확인 값은 {NF}.\n"

    # 최신 정기보고서(반기/분기)의 부문·생산 표
    lb = split_blocks(latest_biz["all"]) if latest_biz else []
    tl_sales = pick_tables(lb, ["비율", "비중", "영업이익", "매출액"], 2, rows)
    tl_cap = pick_tables(lb, cap_kws, 2, rows)

    s1 = section("1. 한 줄 사업 정의 (원문 개요 첫 문단, 기업 서술)",
                 [first_paragraph(B.get("overview", allb))])

    # 제품/부문별 매출·비중 표는 보통 '주요 제품 및 서비스'에 있음 -> 먼저 보고, 없으면 '매출 및 수주상황'
    n2 = 1 if tl_sales else 2
    t2 = (pick_tables(B.get("products", []), ["비율", "비중", "%", "매출액"], n2, rows)
          or pick_tables(sales, ["비율", "비중", "%", "매출액"], n2, rows))
    s2 = section("2. 매출 구성 (사업보고서 표 원문, 단위는 표 캡션 확인)", t2)
    s21 = ""
    if latest_biz is not None:
        s21 = section(f"2-1. 최신 정기보고서 수치 ({latest_nm})",
                      [t for t in tl_sales if t not in t2] + tl_cap)

    t3 = pick_tables(sales, ["매출처", "내수", "수출액", "지역별"], 2, rows, exclude=t2)[:1]
    s3 = section("3. 고객·판매 구조",
                 keyword_lines(sales, ["판매경로", "판매방법", "판매전략", "주요 매출처", "주요매출처",
                                       "수출", "내수", "직판", "대리점", "장기계약", "COA", "CVC", "거래처",
                                       "수주", "주문생산", "의존도", "고객사"], nlines)
                 + t3)

    t4 = pick_tables(mats, ["매입액", "매입유형", "원재료"], 1, rows)
    s4 = section("4. 원가 구조",
                 keyword_lines(mats, ["원재료", "매입", "가격변동", "주요 원자재", "연료", "벙커"], nlines) + t4)

    if tl_cap:
        t5 = []  # 최신 보고서의 생산·설비 표가 있으면 2-1에서 보여주므로 연간 표는 생략
    else:
        t5 = (pick_tables(mats, cap_kws, 3, rows, exclude=t4)
              or pick_tables(allb, ["DWT", "dwt", "생산능력", "가동률"], 2, rows, exclude=t4))
    t5c = pick_tables(rnd, ["선박건조", "장기운송", "주요계약", "공급계약"], 1, rows, rowclip=330)
    fleet_note = []
    is_ship = sum(l.count("선박") for l in (biz["all"] if biz else [])) > 15
    if is_ship and not any(re.search(r"\|\s*(선종|선박명|보유\s*척수)", t) for t in t5 + t4 + tl_cap):
        fleet_note = [f"- 선종별 척수·보유/용선 구분 표: {NF} (이 보고서의 사업의 내용 기준)"]
    s5 = section("5. 생산·설비·계약·R&D",
                 t5 + fleet_note + t5c
                 + keyword_lines(rnd, ["연구개발비용", "연구개발", "R&D"], max(1, nlines - 4)))

    s5b = section("5-2. 경쟁·시장 (원문 발췌, 기업 서술)",
                  keyword_lines(B.get("etc", []) or allb,
                                ["경쟁", "점유율", "진입장벽", "과점", "독점", "대체"], max(2, nlines - 2)))

    s6 = section("6. 바뀐 문구 (인접 보고서 신규 문장, 숫자만 바뀐 문장 제외; 전체는 diff.txt)",
                 [diff_md] if diff_md else [], fallback=f"{NF} (비교할 인접 보고서 없음)")
    s6b = section("6-2. 최근 주요 공시 (증자·사채·계약·자기주식 등, 최근 200일; 원문은 disc/)",
                  [disc_md] if disc_md else [], fallback="해당 공시 없음")
    s7 = f"## 7. 고정 질문 (BM 판별 체크리스트)\n{QUESTIONS}\n"

    parts = [head, fin_block(fin), s1, s2, s21, s3, s4, s5, s5b, s6, s6b, s7]
    return "\n".join(p for p in parts if p)


def make_card(meta, fin, biz, latest_biz=None, latest_nm="", diff_md="", disc_md=""):
    card = ""
    for rows, nlines in [(20, 8), (14, 6), (10, 4), (6, 3)]:
        card = build_card(meta, fin, biz, rows, nlines, latest_biz, latest_nm, diff_md, disc_md)
        if len(card) <= CARD_CHAR_LIMIT:
            break
    else:
        print(f"[경고] 카드가 {len(card)}자로 한도({CARD_CHAR_LIMIT}자) 초과. biz_full.txt 참고.", file=sys.stderr)
    return card


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("query", help="종목코드 6자리 또는 기업명")
    ap.add_argument("--out", default="out")
    a = ap.parse_args()
    if not KEY:
        sys.exit("DART_API_KEY 환경변수가 비어 있음 (GitHub Secrets 이름 확인)")

    corp = cached_corp(a.query)
    if corp:
        print("corp_code 캐시 사용")
    else:
        corp = resolve(a.query, load_corps())
        save_corp(corp)
    print(f"대상: {corp['corp_name']} ({corp['stock_code']}) corp_code={corp['corp_code']}")
    reps = list_periodic(corp["corp_code"])
    annual = next(r for r in reps if r["kind"] == "사업")
    latest = reps[0]
    print(f"BM 서술: {annual['report_nm']} / 최신: {latest['report_nm']} (접수일 {latest['rcept_dt']})")
    print("정기보고서 목록:", ", ".join(f"{r['year']}.{r['month']:02d}{r['kind']}" for r in reps))

    meta = {"corp_name": corp["corp_name"], "stock_code": corp["stock_code"],
            "corp_code": corp["corp_code"], "report_nm": annual["report_nm"],
            "rcept_no": annual["rcept_no"], "rcept_dt": annual["rcept_dt"],
            "latest_report_nm": latest["report_nm"], "latest_rcept_no": latest["rcept_no"],
            "latest_rcept_dt": latest["rcept_dt"],
            "generated_at": datetime.datetime.now().isoformat(timespec="seconds")}

    fin = collect_fin(corp["corp_code"], reps)
    biz = get_biz(annual["rcept_no"])
    if not biz:
        print(f"[경고] '사업의 내용' 구간 {NF}", file=sys.stderr)
    elif not biz["subs"]:
        print(f"[경고] 하위 항목(1.사업의 개요 등) 분리 {NF}. 전체 구간으로 대체.", file=sys.stderr)

    latest_biz = None
    if latest["rcept_no"] != annual["rcept_no"]:
        try:
            latest_biz = get_biz(latest["rcept_no"])
        except RuntimeError as e:
            print(f"[경고] 최신 보고서 원문 실패: {e}", file=sys.stderr)
        if not latest_biz:
            print(f"[경고] 최신 보고서 '사업의 내용' 구간 {NF}", file=sys.stderr)

    # STEP 2: 최근 4개 정기보고서의 사업의 내용 인접 비교
    chain = []
    for r in reps[:4]:
        try:
            if r["rcept_no"] == annual["rcept_no"] and biz:
                ln = biz["all"]
            elif r["rcept_no"] == latest["rcept_no"] and latest_biz:
                ln = latest_biz["all"]
            else:
                b = get_biz(r["rcept_no"])
                ln = b["all"] if b else None
        except RuntimeError as e:
            print(f"[경고] {r['report_nm']} 원문 실패: {e}", file=sys.stderr)
            ln = None
        if ln:
            chain.append((f"{r['year']}.{r['month']:02d}{r['kind']}", strip_risk(ln)))
    diffs = build_diff(chain)

    d = os.path.join(a.out, corp["stock_code"])
    os.makedirs(d, exist_ok=True)
    try:
        discs = list_disclosures(corp["corp_code"])
        print(f"주요 공시 {len(discs)}건, 원문 {save_disclosures(discs, d)}건 저장")
    except Exception as e:  # 공시 수집 실패가 카드 생성을 막지 않게
        print(f"[경고] 공시 수집 실패: {e}", file=sys.stderr)
        discs = []
    disc_md = disc_block(discs)
    orders = {"annual": {"report": annual["report_nm"], "tables": order_tables(biz)},
              "latest": {"report": latest["report_nm"], "tables": order_tables(latest_biz)}}
    with open(os.path.join(d, "orders.json"), "w", encoding="utf-8") as f:
        json.dump(orders, f, ensure_ascii=False, indent=1)
    with open(os.path.join(d, "bm_card.md"), "w", encoding="utf-8") as f:
        f.write(make_card(meta, fin, biz, latest_biz, latest["report_nm"], diff_card(diffs), disc_md))
    with open(os.path.join(d, "diff.txt"), "w", encoding="utf-8") as f:
        f.write(diff_full(diffs) if diffs else NF)
    with open(os.path.join(d, "biz_full.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(biz["all"]) if biz else NF)
    if latest_biz:
        with open(os.path.join(d, "biz_latest.txt"), "w", encoding="utf-8") as f:
            f.write("\n".join(latest_biz["all"]))
    elif os.path.exists(os.path.join(d, "biz_latest.txt")):
        os.remove(os.path.join(d, "biz_latest.txt"))
    with open(os.path.join(d, "fin.json"), "w", encoding="utf-8") as f:
        json.dump({"meta": meta, "fin": fin}, f, ensure_ascii=False, indent=2)
    print(f"완료: {d}/ (bm_card.md, biz_full.txt, fin.json" + (", biz_latest.txt)" if latest_biz else ")"))


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as e:
        sys.exit(f"오류: {e}")
