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
    """최근 5년 정기보고서(사업/반기/분기)를 기간별 1건(정정본 포함 최신 접수)으로 정리. 최신 기간 순."""
    today = datetime.date.today()
    bgn = today - datetime.timedelta(days=365 * 5 + 30)
    rows, page, total = [], 1, 1
    while page <= total and page <= 10:
        j = get_json("list.json", corp_code=corp_code,
                     bgn_de=bgn.strftime("%Y%m%d"), end_de=today.strftime("%Y%m%d"),
                     pblntf_ty="A", page_count=100, page_no=page)
        rows += j.get("list") or []
        total = int(j.get("total_page") or 1)
        page += 1
    best, allv = {}, {}
    for r in rows:
        m = PERIODIC_RE.search(r.get("report_nm", ""))
        if not m:
            continue  # 자율공시·기타 공시 제외
        key = (int(m.group(2)), int(m.group(3)))
        r = dict(r, kind=m.group(1), year=key[0], month=key[1])
        allv.setdefault(key, []).append(r)
        if key not in best or (r["rcept_dt"], r["rcept_no"]) > (best[key]["rcept_dt"], best[key]["rcept_no"]):
            best[key] = r
    # [첨부정정]은 첨부만 바꾼 정정이라 원문 파일이 없을 수 있음 → 원문이 없으면 같은 기간의 이전 접수본 사용
    for key, r in list(best.items()):
        if "첨부정정" not in r.get("report_nm", ""):
            continue
        for alt in sorted(allv[key], key=lambda x: (x["rcept_dt"], x["rcept_no"]), reverse=True):
            try:
                get_biz(alt["rcept_no"])
            except RuntimeError:
                continue
            best[key] = alt
            if alt["rcept_no"] != r["rcept_no"]:
                print(f"[안내] {key} {r['report_nm']} 원문 없음 → {alt['report_nm']}({alt['rcept_no']}) 사용")
            break
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
    ("당기순이익", ("IS", "CIS"), ["당기순이익", "당기순이익(손실)", "연결당기순이익", "당기순손익", "분기순이익", "반기순이익", "분기순이익(손실)", "반기순이익(손실)", "re:^(연결)?(당기|분기|반기)(연결)?순(이익|손익)(\\(손실\\))?$"]),
    ("자산총계", ("BS",), ["자산총계"]),
    ("부채총계", ("BS",), ["부채총계"]),
    ("자본총계", ("BS",), ["자본총계"]),
]
FLOWS = [x[0] for x in FIN_ITEMS[:3]]
# 대시보드/fin.json 전용 추가 항목 (카드 표에는 넣지 않음)
EXTRA_ITEMS = [
    ("영업활동현금흐름", ("CF",), ["영업활동현금흐름", "영업활동으로인한현금흐름", "영업활동순현금흐름", "영업활동으로인한순현금흐름", "영업활동으로인한순현금흐름액"]),
    ("투자활동현금흐름", ("CF",), ["투자활동현금흐름", "투자활동으로인한현금흐름", "투자활동순현금흐름", "투자활동으로인한순현금흐름", "투자활동으로인한순현금흐름액"]),
    ("재무활동현금흐름", ("CF",), ["재무활동현금흐름", "재무활동으로인한현금흐름", "재무활동순현금흐름", "재무활동으로인한순현금흐름", "재무활동으로인한순현금흐름액"]),
    ("현금및현금성자산", ("BS",), ["현금및현금성자산"]),
    ("재고자산", ("BS",), ["재고자산"]),
    ("매출채권", ("BS",), ["매출채권", "매출채권및기타채권", "매출채권및기타유동채권"]),
    ("유동자산", ("BS",), ["유동자산"]),
    ("유동부채", ("BS",), ["유동부채"]),
    ("매출총이익", ("IS", "CIS"), ["매출총이익", "매출총이익(손실)"]),
    ("매출원가", ("IS", "CIS"), ["매출원가"]),
    ("판관비", ("IS", "CIS"), ["판매비와관리비", "판매비와일반관리비", "판매관리비", "판매비및관리비"]),
    ("비유동자산", ("BS",), ["비유동자산"]),
    ("비유동부채", ("BS",), ["비유동부채"]),
    ("이익잉여금", ("BS",), ["이익잉여금", "이익잉여금(결손금)", "이익잉여금(deficit)", "결손금", "미처분이익잉여금", "re:^이익잉여금"]),
    ("자본금", ("BS",), ["자본금"]),
    ("기초현금", ("CF",), ["기초현금및현금성자산", "기초의현금및현금성자산", "기초현금", "re:^기초.*현금"]),
    ("기말현금", ("CF",), ["기말현금및현금성자산", "기말의현금및현금성자산", "기말현금", "re:^기말.*현금"]),
]
# 분기 단독값을 계산할 항목(누적 보고 → 차분). CF는 보고서가 누적값을 주므로 같은 방식으로 처리
QFLOWS = FLOWS + ["매출원가", "판관비", "매출총이익", "영업활동현금흐름", "투자활동현금흐름", "재무활동현금흐름"]


def pick_fin(rows):
    """당기/전기 = thstrm/frmtrm_amount, 당기누적/전기누적 = *_add_amount (분기·반기 보고서에서 3개월 vs 누적 구분용)"""
    out = {}
    for label, divs, cands in FIN_ITEMS + EXTRA_ITEMS:
        found = None
        for cand in cands:
            for r in rows:
                nm0 = norm(r.get("account_nm"))
                hit = bool(re.search(cand[3:], nm0)) if cand.startswith("re:") else nm0 == norm(cand)
                if r.get("sj_div") in divs and hit:
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
        if not found and label == "당기순이익":  # 계정명이 표준과 다른 보고서(서진시스템 2026.06 등): '순이익/순손익' 계열 IS 행으로 대체
            for r in rows:
                nm = norm(r.get("account_nm"))
                if r.get("sj_div") in divs and re.search(r"순(이익|손익|손실)", nm) and not re.search(r"포괄|지배|비지배|주당|법인|세전|계속영업|중단|기타|총|조정", nm):
                    found = {"당기": to_int(r.get("thstrm_amount")), "전기": to_int(r.get("frmtrm_amount")),
                             "당기누적": to_int(r.get("thstrm_add_amount")), "전기누적": to_int(r.get("frmtrm_add_amount")),
                             "전전기": to_int(r.get("bfefrmtrm_amount")), "account_nm": r.get("account_nm")}
                    break
            if not found:
                names = [r.get("account_nm") for r in rows if r.get("sj_div") in divs and "순" in (r.get("account_nm") or "")][:12]
                print(f"[경고] 당기순이익 계정 못 찾음. 후보: {names}", file=sys.stderr)
        out[label] = found
    out["_cf"] = [[r.get("account_nm"), to_int(r.get("thstrm_amount"))] for r in rows if r.get("sj_div") == "CF"]  # 현금흐름표 원 행(워터폴용)
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


def build_quarters(periods, latest_key, fy_end=12, n=20):
    """periods: {(연,월): {항목: pick_fin 결과}} -> 최근 n개 분기 단독 값(원 단위). 최신 분기부터."""
    out, key = [], latest_key
    for _ in range(n):
        y, m = key
        q = qnum(m, fy_end)
        cur = periods.get(key)
        prev = periods.get(prev_period(y, m)) if q > 1 else None
        vals, how, check = {}, "-", ""
        for label in QFLOWS:
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
        for _ in range(20):
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


# ---------------------------------------------------------------- 배당 (DART alotMatter)
def _num(x):
    try:
        return float(str(x).replace(",", "").strip())
    except (ValueError, TypeError):
        return None


def collect_dividend(corp_code, annual_year):
    """사업보고서 기준 최근 6개 사업연도 DPS·배당성향·배당수익률. 한 번 호출에 3개년(당기/전기/전전기)."""
    res = {}
    for by in (annual_year, annual_year - 3):
        try:
            j = get_json("alotMatter.json", corp_code=corp_code, bsns_year=str(by), reprt_code="11011")
        except RuntimeError:
            continue
        for r in j.get("list") or []:
            se, knd = norm(r.get("se")), norm(r.get("stock_knd"))
            if "주당" in se:
                print(f"[배당 {by}] se={r.get('se')!r} knd={r.get('stock_knd')!r} 당={r.get('thstrm')!r} 전={r.get('frmtrm')!r} 전전={r.get('lwfr')!r}")
            if "주당현금배당금" in se and knd in ("보통주", "", "-"):
                key = "dps"
            elif "현금배당성향" in se:
                key = "payout"
            elif "현금배당수익률" in se and knd in ("보통주", "", "-"):
                key = "yield"
            else:
                continue
            for off, f in ((0, "thstrm"), (1, "frmtrm"), (2, "lwfr")):
                v = _num(r.get(f))
                if v is not None:
                    res.setdefault(key, {}).setdefault(by - off, v)
    ys = sorted({y for d in res.values() for y in d})
    return {"years": ys, **{k: [res.get(k, {}).get(y) for y in ys] for k in ("dps", "payout", "yield")}} if ys else {}


# ---------------------------------------------------------------- 보고서별 부문 매출 비중
UNIT_MULT = {"백만원": 1e6, "천원": 1e3, "억원": 1e8, "원": 1.0}


def _unit(caption):
    m = re.search(r"단위\s*:\s*([가-힣]+)", caption or "")
    return UNIT_MULT.get(m.group(1)) if m else None


def _seg_main(lines):
    """'주요 제품 등의 현황' 표(매출액·비율 열)에서 사업부문별 매출을 원 단위로 추출. 합계 검증 실패 시 None."""
    for i, ln in enumerate(lines):
        if not ln.startswith("|") or "매출액" not in ln or not re.search(r"비율|구성비|비중", ln):
            continue
        hdr = [c.strip() for c in ln.strip().strip("|").split("|")]
        if len(hdr) < 3 or hdr[-1] not in ("비율", "구성비", "비중"):
            continue
        cap = next((lines[k] for k in range(i - 1, max(i - 6, -1), -1) if lines[k].strip() and not lines[k].startswith("|")), "")
        mult = _unit(cap)
        if not mult:  # '| (단위 : 백만원) |' 처럼 표 형태로 단위만 적은 줄
            cap2 = next((lines[k] for k in range(i - 1, max(i - 4, -1), -1) if "단위" in lines[k]), "")
            mult = _unit(cap2)
        if not mult:
            continue
        items, total, name, k = {}, None, None, i + 1
        subs, has_sub = {}, False
        while k < len(lines) and lines[k].startswith("|"):
            c = [x.strip() for x in lines[k].strip().strip("|").split("|")]
            k += 1
            if len(c) < 2:
                continue
            amt = _num(c[-2])
            c0 = c[0].replace(" ", "")
            if c0 in ("합계", "계", "총계"):
                total = amt
                continue
            if c0 == "소계":  # 부문 소계 행: 직전 부문의 합
                if name is not None and amt is not None:
                    subs[name] = amt
                    has_sub = True
                continue
            if len(c) >= 4 and "소계" in c[1].replace(" ", "") and c[0]:  # '| 음향·영상기기 | 제품 및 상품 등 소 계 | 금액 | 비율 |'
                name = c[0]
                if amt is not None:
                    subs[name] = amt
                    has_sub = True
                continue
            if c0.startswith("내부거래") or c0.startswith("연결조정"):
                if amt is not None:
                    subs[c[0]] = amt
                    has_sub = True
                continue
            if len(c) == len(hdr) and c[0]:
                name = c[0]
            if amt is None or name is None:
                continue
            items[name] = items.get(name, 0) + amt
        if has_sub:
            for kk, vv in subs.items():
                items[kk] = vv
        if items and total and abs(sum(items.values()) - total) <= total * 0.03:
            return {k2: v * mult for k2, v in items.items()}
        if items and not total:  # 합계 행 없는 표: 품목별 행(비중 열 합이 100% 근처)이면 품목 단위로 다시 읽음
            r2 = _seg_noTotal(lines, i, hdr, mult)
            if r2:
                return r2
        return None
    return None


def _seg_noTotal(lines, i, hdr, mult):
    items, pct, k = {}, 0.0, i + 1
    ip = next((x for x, h in enumerate(hdr) if "품목" in h), None)
    while k < len(lines) and lines[k].startswith("|"):
        c = [x.strip() for x in lines[k].strip().strip("|").split("|")]
        k += 1
        if len(c) < 4:
            continue
        amt, pc = _num(c[-2]), _num(c[-1])
        if amt is None or pc is None:
            continue
        nm = c[ip] if (ip is not None and len(c) == len(hdr)) else c[0]
        items[nm] = items.get(nm, 0) + amt
        pct += pc
    if len(items) >= 2 and 98 <= pct <= 102:
        return {k2: v * mult for k2, v in items.items()}
    return None


def _seg_inline(lines):
    """헤더 마지막 칸이 '매출액 (비율)' 이고 값이 '6,443 (9.8%)' 형태인 제품 현황 표. 합계 행과 3% 이내로 맞으면 품목별 매출(원)."""
    for i, ln in enumerate(lines):
        if not ln.startswith("|") or "매출액" not in ln:
            continue
        hdr = [c.strip() for c in ln.strip().strip("|").split("|")]
        if len(hdr) < 3 or not re.fullmatch(r"매출액\s*\(\s*(비율|비중|구성비)\s*\)", hdr[-1]):
            continue
        cap = next((lines[k] for k in range(i - 1, max(i - 6, -1), -1) if "단위" in lines[k]), "")
        mult = _unit(cap)
        if not mult:
            continue
        ip = next((x for x, h in enumerate(hdr) if "품목" in h.replace(" ", "")), None)
        items, total, k = {}, None, i + 1
        while k < len(lines) and lines[k].startswith("|"):
            c = [x.strip() for x in lines[k].strip().strip("|").split("|")]
            k += 1
            mm = re.match(r"^(-?[\d,]+)\s*\(", c[-1]) if c else None
            if not mm:
                continue
            amt = float(mm.group(1).replace(",", ""))
            if re.sub(r"\s+", "", c[0]) in ("합계", "계", "총계"):
                total = amt
                continue
            nm = c[ip] if (ip is not None and len(c) == len(hdr)) else c[0]
            items[re.sub(r"\(.*", "", nm).strip() or nm] = items.get(nm, 0) + amt
        if items and total and abs(sum(items.values()) - total) <= total * 0.03:
            return {k2: v * mult for k2, v in items.items()}
        return None
    return None


def _seg_types(lines):
    """서비스형 회사: 헤더 '매출유형 | 품목 | 당기(매출액|비율) ...' 표. 유형·품목 칸이 생략된 행도 마지막 라벨 칸을 이름으로, 첫 숫자 칸을 당기 금액으로 읽는다. 합계와 3% 이내로 맞을 때만 사용."""
    for i, ln in enumerate(lines):
        if not ln.startswith("|") or "매출유형" not in ln.replace(" ", "") or "품목" not in ln.replace(" ", ""):
            continue
        cap = next((lines[k] for k in range(i - 1, max(i - 6, -1), -1) if "단위" in lines[k]), "")
        mult = _unit(cap)
        if not mult:
            continue
        items, total, k = {}, None, i + 1
        while k < len(lines) and lines[k].startswith("|"):
            c = [x.strip() for x in lines[k].strip().strip("|").split("|")]
            k += 1
            p = next((x for x, v in enumerate(c) if x > 0 and re.fullmatch(r"-?[\d,]+", v)), None)
            if p is None:
                continue
            q_ = p - 1
            while q_ > 0 and re.sub(r"\s+", "", c[q_]) in ("별첨", "-", "–", "해당없음", ""):
                q_ -= 1
            nm = re.sub(r"\s+", "", c[q_])
            amt = float(c[p].replace(",", ""))
            if nm in ("합계", "계", "총계"):
                total = amt
            elif nm != "소계":
                nm = re.sub(r"\(.*", "", c[q_]).strip() or c[q_]
                items[nm] = items.get(nm, 0) + amt
        if any(re.sub(r"\s+", "", n_) in ("수출", "내수", "국내", "해외") or "판매" in n_ for n_ in items):
            continue
        if len(items) >= 2 and total and abs(sum(items.values()) - total) <= total * 0.03:
            return {k2: v * mult for k2, v in items.items()}
        return None
    return None


def _seg_wide(lines):
    """헤더 '사업부문 | 매출유형 | 품목 | 구체적용도 | 주요상표등 | 제N기 | 제N-1기 …'(기별 매출액·비중 2열씩) 표(메가터치).
    부문(첫 칸)별로 당기(첫 숫자 칸) 금액을 합산. 첫 칸이 병합돼 생략된 행은 직전 부문. 합계와 3% 이내일 때만."""
    for i, ln in enumerate(lines):
        if not ln.startswith("|") or "사업부문" not in ln.replace(" ", "") or "매출유형" not in ln.replace(" ", ""):
            continue
        cap = next((lines[k] for k in range(i - 1, max(i - 6, -1), -1) if "단위" in lines[k]), "")
        mult = _unit(cap)
        if not mult:
            continue
        rows, k = [], i + 1
        while k < len(lines) and lines[k].startswith("|"):
            rows.append([x.strip() for x in lines[k].strip().strip("|").split("|")])
            k += 1
        full = max((len(c) for c in rows), default=0)
        items, total, name = {}, None, None
        for c in rows:
            p = next((x for x, v in enumerate(c) if x > 0 and re.fullmatch(r"-?[\d,]+", v)), None)
            if p is None:
                continue
            c0 = re.sub(r"\s+", "", c[0])
            amt = float(c[p].replace(",", ""))
            if c0 in ("합계", "계", "총계"):
                total = amt
                continue
            if len(c) >= full and c[0]:
                name = c[0]
            if name is None or c0 == "소계":
                continue
            items[name] = items.get(name, 0) + amt
        if len(items) >= 2 and total and abs(sum(items.values()) - total) <= total * 0.03:
            return {k2: v * mult for k2, v in items.items()}
        return None
    return None


def _seg_rowspan(lines):
    """'매출유형 | 품목 | 주요 제품 | 20XX년 ... / 매출액 | 비중 ...' 2단 헤더 + 앞칸 병합(행마다 칸 수 다름) 표(서진시스템).
    값은 행 오른쪽 끝에서 (금액, 비중) 쌍으로 읽고, 이름은 헤더의 '품목' 칸 위치로 보정. 합계와 3% 이내일 때만."""
    for i, ln in enumerate(lines):
        if not ln.startswith("|") or "품목" not in ln or "주요" not in ln or i + 2 >= len(lines):
            continue
        hdr = [c.strip() for c in ln.strip().strip("|").split("|")]
        ip = next((x for x, h in enumerate(hdr) if h.replace(" ", "") == "품목"), None)
        iy = next((x + 1 for x, h in enumerate(hdr) if h.replace(" ", "").startswith("주요제품")), None)  # 기간 열 시작(라벨 칸 수)
        if ip is None or iy is None or iy < 2 or ip >= iy:
            continue
        sub = [c.strip() for c in lines[i + 1].strip().strip("|").split("|")]
        if not sub or sub[0] != "매출액" or "비중" not in sub[1:2] + sub[-1:]:
            continue
        ny = len(sub) // 2  # 연도 수
        cap = next((lines[k] for k in range(i - 1, max(i - 6, -1), -1) if "단위" in lines[k]), "")
        mult = _unit(cap)
        if not mult:
            continue
        items, total, k = {}, None, i + 2
        while k < len(lines) and lines[k].startswith("|"):
            c = [x.strip() for x in lines[k].strip().strip("|").split("|")]
            k += 1
            if len(c) < 2 * ny + 1:
                continue
            vals = c[len(c) - 2 * ny:]
            amt = _num(vals[0])
            labels = c[:len(c) - 2 * ny]
            nm0 = labels[0].replace(" ", "")
            if nm0 in ("합계", "계", "총계"):
                total = amt
                continue
            if amt is None or not labels:
                continue
            j = ip - (iy - len(labels))  # 앞칸 병합으로 빠진 칸 수만큼 당김
            if j < 0 or j >= len(labels):
                continue
            nm = labels[j].strip()
            if not nm or nm in ("-", "별첨"):
                continue
            items[nm] = items.get(nm, 0) + amt
        if len(items) >= 2 and total and abs(sum(items.values()) - total) <= total * 0.03:
            return {k2: v * mult for k2, v in items.items()}
    return None


def _seg_division(lines):
    """'사업부문 | 회사명 | 매출유형 | 품목 | 구분(수출/내수/소계) | 당기 | 전기 ...' 매출실적 표(아모텍): 부문(…부문)별로
    매출유형 묶음마다 '소계' 행을 쓰고(없으면 수출+내수 합), 부문 합계를 낸다. 합계 행과 3% 이내이고 부문 2개 이상일 때만."""
    for i, ln in enumerate(lines):
        if not ln.startswith("|") or "사업부문" not in ln or "매출유형" not in ln or "구분" not in ln:
            continue
        hdr = [c.strip() for c in ln.strip().strip("|").split("|")]
        ig = next((x for x, h in enumerate(hdr) if h.replace(" ", "") == "구분"), None)
        if ig is None or len(hdr) - ig - 1 < 1:
            continue
        npd = len(hdr) - ig - 1
        cap = next((lines[k] for k in range(i - 1, max(i - 6, -1), -1) if "단위" in lines[k]), "")
        mult = _unit(cap)
        if not mult:
            continue
        divs, order, cur, grp, total, in_total = {}, [], None, None, None, False

        def flush():
            if cur is not None and grp is not None:
                divs[cur] += grp["sub"] if grp["sub"] is not None else grp["sum"]
        k = i + 1
        while k < len(lines) and lines[k].startswith("|"):
            c = [x.strip() for x in lines[k].strip().strip("|").split("|")]
            k += 1
            if len(c) < npd + 1:
                continue
            gu = c[-npd - 1].replace(" ", "")
            v = _num(c[-npd])
            labels = c[:-npd - 1]
            if c[0].replace(" ", "") in ("합계", "계", "총계"):  # '합 계 | 수출 | …' 다음의 '소계' 행이 총합
                in_total = True
                continue
            if gu not in ("수출", "내수", "소계"):
                continue
            if v is None:
                continue
            if in_total:
                if gu == "소계":
                    total = v
                    break
                continue
            if len(labels) >= 3 and labels[0].endswith("부문"):
                flush(); grp = None
                cur = re.sub(r"\s+", " ", labels[0]).strip()
                if cur not in divs:
                    divs[cur] = 0.0; order.append(cur)
                grp = {"sub": None, "sum": 0.0}
            elif len(labels) >= 1 and cur is not None:
                flush(); grp = {"sub": None, "sum": 0.0}
            if cur is None or grp is None:
                continue
            if gu == "소계":
                grp["sub"] = v
            else:
                grp["sum"] += v
        flush()
        if len(divs) >= 2 and total and abs(sum(divs.values()) - total) <= total * 0.03:
            return {d: divs[d] * mult for d in order if divs[d]}
    return None


def _seg_subtotal(lines):
    """헤더 '구 분 | 매출유형 | 품목 | 구체적 용도 | 주요상표등 | 제N기 …'(구분 칸이 첫 행에만 있고 부문마다 '소 계' 행이 있는 표; 리노공업).
    열 수가 가장 많은 행(구분 칸 있음)의 첫 칸을 부문명으로, 부문별 '소 계'(당기 첫 숫자)를 금액으로 읽는다. 합계와 3% 이내일 때만."""
    for i, ln in enumerate(lines):
        h = ln.replace(" ", "")
        if not ln.startswith("|") or "구분" not in h or "매출유형" not in h or "품목" not in h or "구체적용도" not in h:
            continue
        cap = next((lines[k] for k in range(i - 1, max(i - 6, -1), -1) if "단위" in lines[k]), "")
        mult = _unit(cap)
        if not mult:
            continue
        rows, k = [], i + 1
        while k < len(lines) and lines[k].startswith("|"):
            rows.append([x.strip() for x in lines[k].strip().strip("|").split("|")])
            k += 1
        full = max((len(c) for c in rows), default=0)
        names, subs, total = [], [], None
        for c in rows:
            c0 = re.sub(r"\s+", "", c[0]) if c else ""
            nums = [x for x in c[1:] if re.fullmatch(r"-?[\d,]+", x)]
            if c0 in ("합계", "계", "총계") and nums:
                total = float(nums[0].replace(",", ""))
            elif c0 == "소계" and nums:
                subs.append(float(nums[0].replace(",", "")))
            elif len(c) >= full and c[0] and not re.fullmatch(r"[\d,.\-]+", c[0]):
                names.append(c[0])
        if len(names) >= 2 and len(names) == len(subs) and total and abs(sum(subs) - total) <= total * 0.03:
            return {n: v * mult for n, v in zip(names, subs)}
        return None
    return None


def seg_from_lines(lines):
    r = _seg_rowspan(lines) or _seg_division(lines) or _seg_subtotal(lines) or _seg_main(lines)
    if r and any(re.sub(r"\s+", "", k_) in ("별첨", "-") for k_ in r):  # 구체적용도 칸('별첨')을 부문명으로 읽은 경우(상신이디피) 버리고 다른 파서로
        r = None
    return r or _seg_inline(lines) or _seg_product(lines) or _seg_wide(lines) or _seg_types(lines)


def _seg_product(lines):
    """단일 사업부문 회사: '(1) 주요 제품군별 매출실적' 표(구분 | 당기 | 전기 ... | 합계) 의 당기 열."""
    blocks = split_blocks(lines)
    ctx = ""
    for b in blocks:
        if b["type"] == "text":
            ctx = b["text"]
            continue
        rows = b["rows"]
        if len(rows) == 1 and "단위" in rows[0]:
            ctx = ctx + " " + rows[0]
            continue
        if "제품군" not in ctx.replace(" ", "") and "제품군" not in (b.get("caption") or ""):
            ctx = ""
            continue
        mu = re.search(r"단위\s*[:：]?\s*(백만원|천원|원)", ctx + " " + (b.get("caption") or ""))
        ctx = ""
        if not mu or len(rows) < 3:
            continue
        mult = {"백만원": 1e6, "천원": 1e3, "원": 1.0}[mu.group(1)]
        items, total = {}, None
        for r in rows[1:]:
            c = [x.strip() for x in r.strip().strip("|").split("|")]
            if len(c) < 2:
                continue
            v = _num(c[1])
            nm = c[0].replace(" ", "")
            if v is None:
                continue
            if nm in ("합계", "계", "총계"):
                total = v
            else:
                items[c[0].strip()] = v
        if len(items) >= 2 and total and abs(sum(items.values()) - total) <= total * 0.03:
            return {k: v * mult for k, v in items.items()}
    return None


def collect_segments(reps, maxn=20):
    out = []
    for r in reps[:maxn]:
        try:
            b = get_biz(r["rcept_no"])
        except RuntimeError:
            continue
        seg = seg_from_lines(b["all"]) if b else None
        if seg:
            out.append({"key": f"{r['year']}.{r['month']:02d}", "kind": r["kind"], "items": seg})
    return list(reversed(out))  # 과거 → 최신


# ---------------------------------------------------------------- 수주잔고 시계열 (모든 정기보고서의 '수주상황' 표)
_NUMTOK = re.compile(r"^-?[\d,]+(\.\d+)?$")


def _cellnums(row):
    """표 행의 숫자 셀만 (순서 유지). '-' 는 빈 값으로 보고 제외하지 않고 None 으로 둔다."""
    out = []
    for c in [x.strip() for x in row.strip().strip("|").split("|")][1:]:
        c2 = c.replace(" ", "")
        if _NUMTOK.match(c2):
            out.append(_num(c2))
        elif c2 in ("-", "", "–"):
            out.append(None)
    return out


def rows_one_ok(b):
    return True


def _single_total(b, head):
    """합계 행 없이 한 행짜리(연결 전체) 수주상황 표: 마지막 3개 숫자를 수주총액·기납품액·수주잔고로."""
    for r in b["rows"][2:]:
        nums = [v for v in _cellnums(r) if v is not None]
        if len(nums) >= 3:
            return nums[-3:]
    return None


_CTR_DATE = re.compile(r"(\d{4})\s*[-.년]\s*(\d{1,2})\s*[-.월]\s*(\d{1,2})")


def _contract_rows(b, head):
    """계약 목록형 수주 표에서 계약별 {date, cur, total, deliv, backlog}. 수량·금액 쌍 표는 금액만. 통화 열이 없으면 단위(K$)를 USD로 본다."""
    out = []
    for r in b["rows"]:
        cells = [x.strip() for x in r.strip().strip("|").split("|")]
        if not cells or re.match(r"^(합|계|총|소)", re.sub(r"\s+", "", cells[0])):
            continue
        dts, cur, nums = [], "", []
        for c in cells:
            c2 = c.replace(" ", "")
            md = _CTR_DATE.fullmatch(c2) or _CTR_DATE.match(c2)
            if md and len(c2) <= 14:
                dts.append("%04d-%02d-%02d" % tuple(int(x) for x in md.groups()))
            elif c2.upper() in ("USD", "KRW", "EUR", "JPY", "CNY"):
                cur = c2.upper()
            elif _NUMTOK.match(c2):
                nums.append(_num(c2))
            elif c2 in ("-", "", "–"):
                nums.append(None)
        if not dts or len(nums) < 3:
            continue
        amt = [nums[-5], nums[-3], nums[-1]] if ("수량" in head and len(nums) >= 6) else nums[-3:]
        if amt[0] is None:
            continue
        out.append({"date": dts[0], "cur": cur, "total": amt[0], "deliv": amt[1], "backlog": amt[2]})
    return out


def order_series(biz):
    """수주상황 표 1개에서 합계(수주총액·기납품액·수주잔고)와 품목별 잔고를 읽는다. 실패하면 None."""
    if not biz:
        return None
    prev = None
    for b in split_blocks(biz["all"]):
        pb, prev = prev, b
        if b["type"] != "table" or len(b["rows"]) < 3:
            continue
        head = " ".join(b["rows"][:2])
        if not ("수주잔고" in head.replace(" ", "") and ("수주총액" in head.replace(" ", "") or "기납품" in head.replace(" ", ""))):
            continue
        unit = ""
        if pb and pb["type"] == "table" and len(pb["rows"]) == 1 and "단위" in pb["rows"][0]:
            unit = re.sub(r"[|\s]+", " ", pb["rows"][0]).strip()
        elif b.get("caption") and "단위" in b["caption"]:
            unit = b["caption"]
        m = re.search(r"단위\s*[:：]?\s*([^)\s]*[^)]*)", unit)
        unit = (m.group(1).strip() if m else unit).strip(" )")
        tot = None
        tots = {}
        krw = None
        usd_rows = []
        last_cur = ""
        items = {}
        contract_list = "통화" in head.replace(" ", "")  # 계약별 나열 표(통화 열): 합계가 통화별로 따로 있음
        for r in b["rows"][2:]:
            name = re.sub(r"\s+", "", r.strip().strip("|").split("|")[0])
            nums = _cellnums(r)
            vals = [v for v in nums if v is not None]
            if "수량" in head and (len(vals) >= 6 or (len(nums) == 6 and len(vals) >= 2)):  # 수량 열이 "-" 인 행도 금액 열 위치로 읽는다
                amt = [nums[1], nums[3], nums[5]] if len(nums) == 6 else [vals[-5], vals[-3], vals[-1]]
            elif len(nums) >= 3 and len(vals) >= 2:
                amt = nums[-3:]
            else:
                continue
            if contract_list and not re.match(r"^(합계|계|총계|소계)", name):
                last_cur = "USD" if "USD" in r.upper() else "KRW" if "KRW" in r.upper() else last_cur
                if "USD" in r.upper() and amt[2] is not None:
                    usd_rows.append(amt)
            if re.match(r"^(합계|계|총계|소계)(\(|$)", name):
                cur = "USD" if "USD" in r.upper() else "KRW" if "KRW" in r.upper() else last_cur
                if not cur:
                    m_ = re.search(r"\((USD|KRW)\)", name.upper())
                    cur = m_.group(1) if m_ else ""
                tots[cur] = amt
                tot = amt
            elif name:
                items[name] = amt[2]
        if contract_list and not tots.get("USD") and not tots.get("") and usd_rows:
            tots["USD"] = [sum(x[0] or 0 for x in usd_rows), sum(x[1] or 0 for x in usd_rows), sum(x[2] for x in usd_rows)]
        if contract_list and tots and not (tots.get("USD") or tots.get("")):
            tot = None  # 원화 합계만 있는 보고서는 달러 시계열에 섞지 않음
        elif contract_list and tots:
            tot = tots.get("USD") or tots.get("")
            if tots.get("KRW"):
                krw = tots["KRW"][2]
            if "K$" in unit.replace(" ", "") or "USD" in tots:
                unit = "K$"
        if not tot and len(items) == 1 and rows_one_ok(b):
            tot = [None, None, list(items.values())[0]]
            tot = _single_total(b, head)
        if tot and tot[2] is not None:
            res_ = {"unit": unit, "total": tot[0], "deliv": tot[1], "backlog": tot[2], "items": items, "krw_backlog": krw}
            res_["contracts"] = _contract_rows(b, head)
            return res_
    return None


def collect_orders(reps, maxn=20):
    out = []
    for r in reps[:maxn]:
        try:
            b = get_biz(r["rcept_no"])
        except RuntimeError:
            continue
        o = order_series(b)
        if o:
            o.update({"key": f"{r['year']}.{r['month']:02d}", "kind": r["kind"]})
            out.append(o)
    return list(reversed(out))


# ---------------------------------------------------------------- 생산능력·가동률 시계열 (모든 정기보고서의 '생산실적 및 가동률' 표)
CAPA_LABELS = (("생산능력", "capa"), ("CAPA", "capa"), ("생산실적", "prod"), ("생산량", "prod"), ("가동률", "util"), ("가동율", "util"), ("기말재고", "inv"))


def _lab(c):
    c2 = re.sub(r"\s+", "", c)
    for k, v in CAPA_LABELS:
        if c2.upper().startswith(k.upper()):
            return v
    return None


def capa_from_lines(lines):
    """보고서 1건에서 부문별 (생산능력·생산실적·가동률·기말재고)의 '당기(첫 숫자 열)' 값을 읽는다. 없으면 None.
    표 형태: '| 부문 | 생산능력 | 당기 | 전기 | 전전기 |' 뒤에 '| 생산실적 | ... |' 가 이어지는 구조(부문 셀은 rowspan 이라 생략되기도 함)."""
    groups, cur, unit = {}, None, ""
    blocks = split_blocks(lines)
    # 형식 2: '| 사업부문 | 품목 | 가동 가능 | 실제 가동 | 가동률 |' (품목별 한 행)
    for b in blocks:
        if b["type"] != "table" or len(b["rows"]) < 2:
            continue
        hc = [re.sub(r"\s+", "", x) for x in b["rows"][0].strip().strip("|").split("|")]
        if len(hc) >= 4 and hc[-1] in ("가동률", "가동율", "평균가동률", "평균가동율") and re.search(r"가능|생산능력|CAPA", "".join(hc[-3:-1]), re.I) and re.search(r"실제|생산실적", "".join(hc[-3:-1])):
            for r in b["rows"][1:]:
                cells = [x.strip() for x in r.strip().strip("|").split("|")]
                if len(cells) < 4:
                    continue
                nums = []
                for c in cells[-3:]:
                    m2 = re.match(r"^-?[\d,]+(\.\d+)?", c.replace(" ", ""))
                    nums.append(float(m2.group(0).replace(",", "")) if m2 else None)
                if None in nums:
                    continue
                nm = re.sub(r"\(.*", "", re.sub(r"\s+", "", cells[-4]))
                if nm:
                    groups[nm] = {"unit": "", "capa": nums[0], "prod": nums[1], "util": nums[2]}
    if groups:
        return groups
    # 형식 2b: 헤더 2단 — '| 사업부문 | 회사명 | 사업소 | 제33기 2분기 |' 아래 '| 가동 가능시간 | 실제 가동시간 | 평균 가동률 |', 행은 부문별(아모텍). 부문명을 그룹명으로
    for b in blocks:
        if b["type"] != "table" or len(b["rows"]) < 3:
            continue
        r1 = [re.sub(r"\s+", "", x) for x in b["rows"][1].strip().strip("|").split("|")]
        if len(r1) != 3 or not (re.search(r"가능", r1[0]) and re.search(r"실제", r1[1]) and r1[2] in ("가동률", "가동율", "평균가동률", "평균가동율")):
            continue
        for r in b["rows"][2:]:
            cells = [x.strip() for x in r.strip().strip("|").split("|")]
            if len(cells) < 5 or not cells[0] or re.sub(r"\s+", "", cells[0]).startswith(("합계", "계")):
                continue
            nums = []
            for c in cells[-3:]:
                m2 = re.match(r"^-?[\d,]+(\.\d+)?", c.replace(" ", ""))
                nums.append(float(m2.group(0).replace(",", "")) if m2 else None)
            if None in nums:
                continue
            groups[re.sub(r"\s+", "", cells[0])] = {"unit": "시간", "capa": nums[0], "prod": nums[1], "util": nums[2]}
    if groups:
        return groups
    # 형식 3: '| 사업부문 | 품목 | 당기 | 전기 | 전전기 |' 아래 '| 가동률 | 가동률 | 가동률 |' 줄만 있고 품목별 가동률(%)만 적은 표(상신이디피)
    for b in blocks:
        if b["type"] != "table" or len(b["rows"]) < 3:
            continue
        r1 = [re.sub(r"\s+", "", x) for x in b["rows"][1].strip().strip("|").split("|")]
        if not r1 or not all(x in ("가동률", "가동율") for x in r1):
            continue
        for r in b["rows"][2:]:
            cells = [x.strip() for x in r.strip().strip("|").split("|")]
            p = next((x for x, c in enumerate(cells) if x > 0 and re.fullmatch(r"-?[\d,.]+\s*%?", c)), None)
            if p is None or not cells[p - 1]:
                continue
            groups[re.sub(r"\s+", "", cells[p - 1])] = {"unit": "%", "util": float(cells[p].replace(",", "").replace("%", "").strip())}
    if groups:
        return groups
    for b in blocks:
        if b["type"] != "table":
            continue

        head = (b.get("caption") or "") + " " + " ".join(b["rows"][:1])
        if not any(k in " ".join(b["rows"]).replace(" ", "") for k in ("가동률", "가동율")):
            continue
        mu = re.search(r"단위\s*[:：]?\s*([^)]+)\)", head)
        unit = mu.group(1).strip() if mu else unit
        for r in b["rows"][1:]:
            cells = [x.strip() for x in r.strip().strip("|").split("|")]
            lab = _lab(cells[0]) if cells else None
            if lab and len(cells) >= 2:
                name, vals = cur, cells[1:]
            elif len(cells) >= 3 and _lab(cells[1]):
                lab, cur, name, vals = _lab(cells[1]), cells[0], cells[0], cells[2:]
            else:
                # 사업부문|회사|품목|사업소|구분(생산능력)|당기… 처럼 구분 열이 뒤에 있는 표: 앞 칸이 모두 글자일 때만 인정
                j = next((j for j in range(2, min(5, len(cells) - 1)) if _lab(cells[j]) and not any(re.fullmatch(r"-?[\d,.]+%?", c.replace(" ", "")) for c in cells[:j])), None)
                if j is not None and cells[0]:
                    lab, cur, name, vals = _lab(cells[j]), cells[0], cells[0], cells[j + 1:]
                else:
                    if cells and cells[0] and _lab(cells[0]) is None and not re.fullmatch(r"-?[\d,.]+%?", cells[0].replace(" ", "")) and len(cells) >= 4:
                        cur = cells[0]  # 가동률 라벨이 없는 새 부문 행(예: 선박 조립) 뒤의 값이 이전 부문에 섞이지 않게 한다
                    continue
            if not name:
                continue
            v = None
            for c in vals:
                c2 = c.replace(",", "").replace("%", "").strip()
                if re.fullmatch(r"-?\d+(\.\d+)?", c2):
                    v = float(c2)
                    break
            if v is not None:
                groups.setdefault(re.sub(r"\s+", "", name), {"unit": unit})[lab] = v
    groups = {k: v for k, v in groups.items() if "util" in v and ("prod" in v or "capa" in v)}
    return groups or None


def collect_capa(reps, maxn=20):
    out = []
    for r in reps[:maxn]:
        try:
            b = get_biz(r["rcept_no"])
        except RuntimeError:
            continue
        g = capa_from_lines(b["all"]) if b else None
        if g:
            out.append({"key": f"{r['year']}.{r['month']:02d}", "kind": r["kind"], "groups": g})
    return list(reversed(out))


# ---------------------------------------------------------------- 비용의 성격별 분류 (재무제표 주석) 원문 수집
COST_KEYS = ("성격별", "비용의 성격")


def collect_cost_raw(reps, maxn=20):
    """각 보고서 전체 문서에서 '비용의 성격별 분류' 주석 표를 원문 그대로 저장(cache/cost/{rcept}.json). 불변 캐시."""
    cd = os.path.join(CACHE, "cost")
    os.makedirs(cd, exist_ok=True)
    n = []
    for r in reps[:maxn]:
        fp = os.path.join(cd, f"{r['rcept_no']}.json")
        if os.path.exists(fp):
            n.append(f"{r['rcept_no']}.json")
            continue
        try:
            lines = to_text(fetch_doc(r["rcept_no"]))
        except Exception as e:
            print(f"[경고] 비용 주석 {r['rcept_no']} 실패: {e}", file=sys.stderr)
            continue
        out, ctx = [], []
        i = 0
        while i < len(lines):
            l = lines[i]
            if l.startswith("|"):
                j = i
                while j < len(lines) and lines[j].startswith("|"):
                    j += 1
                near = " ".join(ctx[-8:])
                body = " ".join(lines[i:j])
                if (any(k in near for k in COST_KEYS) or ("성격" in body[:200])) and j - i >= 4:
                    c2_ = " ".join(ctx[-2:])
                    pr_ = 0 if ("비용의 성격별" in c2_ and "기능별" not in c2_) else 1  # 주석 앞쪽의 '성격별 비용의 기능별 배분' 표들이 6칸을 채우지 않게 본 표를 먼저
                    out.append((pr_, {"ctx": ctx[-4:], "rows": lines[i:j][:80]}))
                i = j
                continue
            if l.strip():
                ctx.append(l.strip()[:160])
            i += 1
        json.dump({"key": f"{r['year']}.{r['month']:02d}", "kind": r["kind"], "tables": [t_ for _, t_ in sorted(out, key=lambda x: x[0])][:6]}, open(fp, "w", encoding="utf-8"), ensure_ascii=False)
        n.append(f"{r['rcept_no']}.json")
    return n


# ---------------------------------------------------------------- 신규시설투자 공시 (거래소공시 '신규시설투자등')
def parse_capex_notice(lines):
    """신규시설투자등 공시 원문 표 → 투자구분·투자대상·금액·자기자본 대비·목적·기간. 금액 못 찾으면 None."""
    res = {}
    for i, l in enumerate(lines):
        if not l.startswith("|"):
            continue
        cs = _cells(l)
        if len(cs) < 2:
            continue
        lab, val = norm(" ".join(cs[:-1])), cs[-1]
        mult = 1e6 if "백만원" in lab else 1e3 if "천원" in lab else 1.0
        if "투자구분" in lab and "kind" not in res and val not in ("", "-"):
            res["kind"] = val[:40]
        elif re.search(r"투자대상|투자내역", lab) and val not in ("", "-") and "what" not in res and not re.search(r"^\d", val):
            res["what"] = val[:80]
        elif re.search(r"투자금액", lab) and "amt" not in res:
            v = _num(val)
            if v is not None:
                res["amt"] = v * mult
        elif "자기자본" in lab and "대비" in lab:
            v = _num(val)
            if v is not None:
                res["pct"] = v
        elif re.search(r"투자목적", lab) and val not in ("", "-") and "why" not in res:
            res["why"] = val[:200]
        elif lab.endswith("시작일") and re.search(r"\d{4}", val) and "start" not in res:
            res["start"] = val
        elif lab.endswith("종료일") and re.search(r"\d{4}", val) and "end" not in res:
            res["end"] = val
    return res if res.get("amt") else None


def collect_capex_notices(corp_code, d, years=5, maxn=40):
    end = datetime.date.today()
    bgn = end - datetime.timedelta(days=365 * years)
    items, seen = [], set()
    for page in range(1, 11):
        j = get_json("list.json", corp_code=corp_code, bgn_de=bgn.strftime("%Y%m%d"), end_de=end.strftime("%Y%m%d"),
                     pblntf_ty="I", page_no=page, page_count=100)
        if j.get("status") != "000":
            break
        for it in j.get("list", []):
            if "신규시설투자" in it["report_nm"] and it["rcept_no"] not in seen:
                seen.add(it["rcept_no"])
                items.append({"no": it["rcept_no"], "nm": it["report_nm"].strip(), "dt": it["rcept_dt"]})
        if page >= int(j.get("total_page", 1) or 1):
            break
    items.sort(key=lambda x: (x["dt"], x["no"]), reverse=True)
    out, miss = [], 0
    cd = os.path.join(CACHE, "capex")
    os.makedirs(cd, exist_ok=True)
    for it in items[:maxn]:
        fp = os.path.join(cd, f"{it['no']}.json")
        if os.path.exists(fp):
            p = json.load(open(fp, encoding="utf-8"))
        else:
            try:
                lines = to_text(fetch_doc(it["no"]))
            except RuntimeError:
                continue
            p = parse_capex_notice(lines) or {}
            if not p:
                miss += 1
            if (not p and miss <= 2) or len(out) < 1:  # 양식 확인용 원문
                os.makedirs(os.path.join(d, "capex_docs"), exist_ok=True)
                open(os.path.join(d, "capex_docs", f"{it['dt']}_{it['no']}.txt"), "w", encoding="utf-8").write("\n".join(lines))
            json.dump(p, open(fp, "w", encoding="utf-8"), ensure_ascii=False)
        if p.get("amt"):
            out.append({**it, **p, "cancel": bool(re.search(r"철회|취소", it["nm"])), "fix": "정정" in it["nm"]})
    print(f"신규시설투자 공시 {len(items)}건 중 금액 파싱 {len(out)}건")
    return out


# ---------------------------------------------------------------- 내수·수출 매출 시계열 (사업의 내용 '매출 실적' 표)
def region_from_lines(lines):
    """'매출 실적' 표(구분 열에 내수/수출/합계)에서 당기(첫 숫자 열) 내수·수출 합계. 단위는 원으로 환산. 못 찾으면 None."""
    lines = [re.sub(r"\(\s*(?:US)?\$\s*[\d,\.]+\s*\)", "", l) if "$" in l else l for l in lines]  # '2,085 ($1,369)' 의 달러 병기 제거
    blocks = split_blocks(lines)
    dom = exp = 0.0
    found = 0
    prev_text = ""
    for b in blocks:
        if b["type"] == "text":
            prev_text = b["text"]
            continue
        rows = b["rows"]
        if len(rows) == 1 and "단위" in rows[0]:  # '| (단위 : 백만원) |' 한 줄 표
            prev_text = rows[0]
            continue
        blob = " ".join(rows)
        bs_ = re.sub(r"\s+", "", blob)
        if not ("내수" in bs_ and "수출" in bs_ and ("합계" in bs_ or "총계" in bs_)):
            continue
        head = " ".join(rows[:2])
        if not re.search(r"제\s*\d+\s*기|당기|반기|분기", head):
            continue
        ctx = (b.get("caption") or "") + " " + prev_text + " " + head
        mu = re.search(r"단위\s*[:：]?\s*(백만원|천원|원)", ctx)
        mult = {"백만원": 1e6, "천원": 1e3, "원": 1.0}.get(mu.group(1) if mu else "", None)
        if mult is None:
            continue
        grp, tot = None, {}
        for r in rows[1:]:
            cells = [x.strip() for x in r.strip().strip("|").split("|")]
            labs = {"내수": "dom", "국내": "dom", "수출": "exp", "해외": "exp"}
            idx = None
            for i, c in enumerate(cells[:3]):
                if re.sub(r"\s+", "", c) in labs:
                    idx = i
                    break
            if idx is None:
                continue
            if idx >= 1:  # 첫 칸이 그룹명(행 병합이 풀린 경우)
                grp = re.sub(r"\s+", "", cells[0])
                typ = re.sub(r"\s+", "", cells[1]) if idx == 2 else ""
            else:
                typ = ""
            lab = labs[re.sub(r"\s+", "", cells[idx])]
            vals = cells[idx + 1:]
            v = None
            for c in vals:
                c2 = c.replace(",", "").strip()
                if re.fullmatch(r"-?\d+(\.\d+)?", c2):
                    v = float(c2)
                    break
                if c2 in ("-", ""):
                    v = 0.0
                    break
            if v is None or grp is None:
                continue
            if grp in ("총계", "전체", "총합계") or (grp == "합계" and idx == 1):
                tot.setdefault(grp, {})[lab] = tot.get(grp, {}).get(lab, 0.0) + v * mult
        pick = tot.get("총계") or tot.get("전체") or tot.get("총합계") or tot.get("합계")
        if pick and "dom" in pick and "exp" in pick:
            dom, exp, found = pick["dom"], pick["exp"], 2
    if found >= 2 and (dom + exp) > 0:
        return {"dom": dom, "exp": exp}
    return _region_simple(blocks)


def _region_simple(blocks):
    """'| 사업부문 | 매출유형 | 품목 | 수 출 | 금액 | ...' 처럼 수출/국내 라벨이 앞 4칸 중 하나인 단일 사업부문 표. 첫 표의 당기(첫 숫자열)."""
    prev_text = ""
    for b in blocks:
        if b["type"] == "text":
            prev_text = b["text"]
            continue
        rows = b["rows"]
        if len(rows) == 1 and "단위" in rows[0]:
            prev_text = rows[0]
            continue
        head = " ".join(rows[:1])
        if not re.search(r"제\s*\d+\s*기|당기|반기|분기", head):
            continue
        ctx = (b.get("caption") or "") + " " + prev_text
        if "가격" in ctx or re.search(r"단위\s*[:：]?\s*원\s*/", ctx):  # 가격변동추이 표(원/Km 등)는 매출 표가 아님
            continue
        mu = re.search(r"단위\s*[:：]?\s*(백만원|천원|원)", ctx)
        if not mu:
            continue
        mult = {"백만원": 1e6, "천원": 1e3, "원": 1.0}[mu.group(1)]
        dom = exp = 0.0
        hit = 0
        in_tot, tot_row = False, None
        for r in rows[1:]:
            cells = [x.strip() for x in r.strip().strip("|").split("|")]
            lab = None
            for i, c in enumerate(cells[:4]):
                c2 = re.sub(r"\s+", "", c)
                if c2 in ("내수", "국내", "수출", "해외"):
                    lab, k = ("dom" if c2 in ("내수", "국내") else "exp"), i
                    break
            if not lab:
                continue
            v = None
            for c in cells[k + 1:]:
                c2 = c.replace(",", "").strip()
                if re.fullmatch(r"-?\d+(\.\d+)?", c2):
                    v = float(c2)
                    break
            if v is None:
                continue
            if k == 1 and re.sub(r"\s+", "", cells[0]) == "합계":  # 제품별 행 뒤의 '합계' 묶음 시작(수출 행). 이후 내수 행은 첫 칸에 라벨만 있음
                in_tot = True
                tot_row = (tot_row or {})
                tot_row[lab] = tot_row.get(lab, 0.0) + v * mult
                continue
            if in_tot and k == 0:
                tot_row[lab] = tot_row.get(lab, 0.0) + v * mult
                continue
            hit += 1
            if lab == "dom":
                dom += v * mult
            else:
                exp += v * mult
        if tot_row and tot_row.get("dom") and tot_row.get("exp"):
            return {"dom": tot_row["dom"], "exp": tot_row["exp"]}
        if hit >= 2 and dom > 0 and exp > 0:
            return {"dom": dom, "exp": exp}
    return None


def collect_region(reps, maxn=20):
    out = []
    for r in reps[:maxn]:
        try:
            b = get_biz(r["rcept_no"])
        except RuntimeError:
            continue
        g = region_from_lines(b["all"]) if b else None
        if g:
            out.append({"key": f"{r['year']}.{r['month']:02d}", "kind": r["kind"], **g})
    return list(reversed(out))


# ---------------------------------------------------------------- 내부자 거래 (DART elestock) · 공급계약 (거래소공시)
def collect_insider(corp_code, d):
    """임원·주요주주 특정증권 소유상황 보고 목록(증감 주식 수). 가격·보고사유는 원문에만 있어 최근 3건 원문을 insider_docs/ 에 저장(양식 확인용)."""
    try:
        j = get_json("elestock.json", corp_code=corp_code)
    except RuntimeError as e:
        print(f"[경고] elestock 호출 실패: {e}", file=sys.stderr)
        return []
    print(f"elestock status={j.get('status')} msg={j.get('message')} rows={len(j.get('list') or [])}")
    rows = []
    for r in j.get("list") or []:
        rows.append({"dt": r.get("rcept_dt"), "no": r.get("rcept_no"), "who": (r.get("repror") or "").strip(),
                     "pos": (r.get("isu_exctv_ofcps") or r.get("isu_main_shrholdr") or "").strip(),
                     "own": _num(r.get("sp_stock_lmp_cnt")), "chg": _num(r.get("sp_stock_lmp_irds_cnt")),
                     "rate": _num(r.get("sp_stock_lmp_rate")), "crate": _num(r.get("sp_stock_lmp_irds_rate"))})
    rows.sort(key=lambda x: (x["dt"] or "", x["no"] or ""), reverse=True)
    rows = rows[:400]
    cd = os.path.join(CACHE, "insider")
    os.makedirs(cd, exist_ok=True)
    for r in [x for x in rows if x["chg"]][:80]:  # 최신 80건의 세부 변동내역(보고사유·단가)
        fp = os.path.join(cd, f"{r['no']}.json")
        try:
            if os.path.exists(fp):
                r["trades"] = json.load(open(fp, encoding="utf-8"))
            else:
                _tx = to_text(fetch_doc(r["no"]))
                r["trades"] = parse_insider(_tx)
                json.dump(r["trades"], open(fp, "w", encoding="utf-8"), ensure_ascii=False)
                if not r["trades"] and len(os.listdir(os.path.join(d, "insider_raw")) if os.path.isdir(os.path.join(d, "insider_raw")) else []) < 8:
                    os.makedirs(os.path.join(d, "insider_raw"), exist_ok=True)  # 파서가 0건인 소유상황 원문 점검용
                    open(os.path.join(d, "insider_raw", f"{r['no']}.txt"), "w", encoding="utf-8").write((lambda t: t[max(0, t.find("세부변동")-200):][:3500] if "세부변동" in t else t[-3500:])("\n".join(_tx) if isinstance(_tx, list) else str(_tx)))
        except Exception as e:
            print(f"[경고] 내부자 원문 {r['no']} 실패: {e}", file=sys.stderr)
    return rows


DATE_RE = re.compile(r"(\d{4})\s*[.년]\s*(\d{1,2})\s*[.월]\s*(\d{1,2})")


def parse_insider(lines):
    """'다. 세부변동내역' 표: 보고사유 | 변동일 | 종류 | 변동전 | 증감 | 변동후 | 단가 | 비고. 단가가 없으면 None."""
    out = []
    for l in lines:
        if not l.startswith("|"):
            continue
        c = _cells(l)
        if len(c) < 7 or c[0].startswith("합") or not DATE_RE.search(c[1]):
            continue
        chg = _num(c[4])
        if chg is None:
            continue
        out.append({"reason": c[0], "date": "-".join(("%s-%02d-%02d" % tuple(map(int, DATE_RE.search(c[1]).groups()))).split("-")), "kind": c[2],
                    "note": (c[7] if len(c) > 7 and c[7] != "-" else ""),
                    "before": _num(c[3]), "chg": chg, "after": _num(c[5]), "price": _num(c[6])})
    return out


def _cells(line):
    return [c.strip() for c in line.strip().strip("|").split("|")]


def parse_contract(lines):
    """단일판매·공급계약(체결/해지) 공시 원문 표에서 금액·매출 대비·상대방·기간·계약일 추출. 금액 못 찾으면 None."""
    amt = {}  # 우선순위: 확정 > 총액 > 일반
    res = {}
    for l in lines:
        if not l.startswith("|"):
            continue
        cs = _cells(l)
        if len(cs) < 2:
            continue
        lab, val = norm(" ".join(cs[:-1])), cs[-1]
        mult = 1e6 if "백만원" in lab else 1e3 if "천원" in lab else 1.0
        if "대비" in lab and "매출" in lab:
            v = _num(val)
            if v is not None:
                res["pct"] = v
        elif "최근매출액" in lab:
            v = _num(val)
            if v is not None:
                res["rev"] = v * mult
        elif re.search(r"(해지|계약)금액", lab) and "조건부" not in lab and "매출" not in lab and "비율" not in lab:
            v = _num(val)
            if v is not None:
                pr = 0 if "확정" in lab else 1 if "총액" in lab else 2
                if pr not in amt:
                    amt[pr] = v * mult
        elif "계약상대" in lab or ("회사명" in lab and "상대" in lab):
            if val and val != "-" and "party" not in res:
                res["party"] = val[:40]
        elif lab.endswith("시작일") and re.search(r"\d{4}", val):
            res["start"] = val
        elif lab.endswith("종료일") and re.search(r"\d{4}", val):
            res["end"] = val
        elif re.search(r"계약\(수주\)일자|계약일자|해지일자", lab) and re.search(r"\d{4}", val):
            res["date"] = val
    if not amt:
        return None
    res["amt"] = amt[min(amt)]
    return res


def collect_contracts(corp_code, d, years=5, maxn=60):
    """최근 years년 단일판매·공급계약 공시를 파싱(접수번호별 캐시). 반환: 최신순 목록."""
    end = datetime.date.today()
    bgn = end - datetime.timedelta(days=365 * years)
    items, seen = [], set()
    for page in range(1, 11):
        j = get_json("list.json", corp_code=corp_code, bgn_de=bgn.strftime("%Y%m%d"), end_de=end.strftime("%Y%m%d"),
                     pblntf_ty="I", page_no=page, page_count=100)
        if j.get("status") != "000":
            break
        for it in j.get("list", []):
            nm = it["report_nm"]
            if re.search(r"단일판매|공급계약", nm) and it["rcept_no"] not in seen:
                seen.add(it["rcept_no"])
                items.append({"no": it["rcept_no"], "nm": nm.strip(), "dt": it["rcept_dt"]})
        if page >= int(j.get("total_page", 1) or 1):
            break
    items.sort(key=lambda x: (x["dt"], x["no"]), reverse=True)
    out, miss = [], 0
    cd = os.path.join(CACHE, "contracts")
    os.makedirs(cd, exist_ok=True)
    for it in items[:maxn]:
        fp = os.path.join(cd, f"{it['no']}.json")
        if os.path.exists(fp):
            p = json.load(open(fp, encoding="utf-8"))
        else:
            try:
                lines = to_text(fetch_doc(it["no"]))
            except RuntimeError:
                continue
            p = parse_contract(lines) or {}
            if not p:
                miss += 1
                if miss <= 2:  # 양식 확인용 원문
                    os.makedirs(os.path.join(d, "contract_docs"), exist_ok=True)
                    open(os.path.join(d, "contract_docs", f"{it['dt']}_{it['no']}.txt"), "w", encoding="utf-8").write("\n".join(lines))
            json.dump(p, open(fp, "w", encoding="utf-8"), ensure_ascii=False)
        if p.get("amt"):
            out.append({**it, **p, "kind": "해지" if "해지" in it["nm"] else "정정" if "정정" in it["nm"] else "체결"})
    print(f"공급계약 공시 {len(items)}건 중 금액 파싱 {len(out)}건")
    return out


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


def dump_latest(rcept_no, path):
    """최신 보고서 ZIP 구성과 '부문·성격별·영업수익' 주변 원문을 out/{code}/notes_raw.txt 로 저장(파서 점검용)."""
    r = get("document.xml", rcept_no=rcept_no)
    z = zipfile.ZipFile(io.BytesIO(r.content))
    out = ["ZIP: " + ", ".join(f"{n}({z.getinfo(n).file_size})" for n in z.namelist())]
    keys = ("영업부문", "부문별", "성격별", "비용의 성격", "영업수익", "영업비용", "신탁보수", "금융업")
    for n in z.namelist():
        if not n.lower().endswith((".xml", ".html", ".htm")):
            continue
        lines = to_text(decode(z.read(n)))
        out.append(f"=== {n} lines={len(lines)}")
        last = -99
        for i, l in enumerate(lines):
            if any(k in l for k in keys) and i - last > 6:
                out.append(f"--- {i}")
                out.extend(x[:300] for x in lines[i:i + 14])
                last = i
    open(path, "w", encoding="utf-8").write("\n".join(out)[:150000])


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
    try:
        fin["dividend"] = collect_dividend(corp["corp_code"], annual["year"])
    except Exception as e:
        print(f"[경고] 배당 수집 실패: {e}", file=sys.stderr)
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
        fin["capex_notices"] = collect_capex_notices(corp["corp_code"], d)
    except Exception as e:
        print(f"[경고] 시설투자 공시 수집 실패: {e}", file=sys.stderr)
    try:
        fin["region_hist"] = collect_region(reps)
        print(f"내수·수출 {len(fin['region_hist'])}개 보고서")
    except Exception as e:
        print(f"[경고] 내수·수출 수집 실패: {e}", file=sys.stderr)
    try:
        fin["capa_hist"] = collect_capa(reps)
        print(f"가동률 {len(fin['capa_hist'])}개 보고서")
    except Exception as e:
        print(f"[경고] 가동률 수집 실패: {e}", file=sys.stderr)
    try:
        dump_latest(latest["rcept_no"], os.path.join(d, "notes_raw.txt"))
    except Exception as e:
        print(f"[경고] 주석 원문 덤프 실패: {e}", file=sys.stderr)
    try:
        fin["cost_files"] = collect_cost_raw(reps)
        print(f"비용 주석 원문 {len(fin['cost_files'])}개 보고서")
    except Exception as e:
        print(f"[경고] 비용 주석 수집 실패: {e}", file=sys.stderr)
    try:
        fin["orders_hist"] = collect_orders(reps)
        print(f"수주잔고 {len(fin['orders_hist'])}개 보고서")
    except Exception as e:
        print(f"[경고] 수주잔고 수집 실패: {e}", file=sys.stderr)
    try:
        fin["segments"] = collect_segments(reps)
        print(f"부문 비중 {len(fin['segments'])}개 보고서")
    except Exception as e:
        print(f"[경고] 부문 비중 수집 실패: {e}", file=sys.stderr)
    try:
        fin["insider"] = collect_insider(corp["corp_code"], d)
        print(f"내부자 보고 {len(fin['insider'])}건")
    except Exception as e:
        print(f"[경고] 내부자 수집 실패: {e}", file=sys.stderr)
    try:
        fin["contracts"] = collect_contracts(corp["corp_code"], d)
    except Exception as e:
        print(f"[경고] 공급계약 수집 실패: {e}", file=sys.stderr)
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
