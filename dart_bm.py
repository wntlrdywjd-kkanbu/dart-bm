#!/usr/bin/env python3
"""dart_bm.py - DART 사업보고서 -> bm_card.md (LLM 없음, 수집/가공/계산만)

사용법:  python dart_bm.py 028670      (종목코드 6자리)
         python dart_bm.py 팬오션      (기업명, 상장사 정확 일치)
필요:    환경변수 DART_API_KEY
출력:    out/{종목코드}/bm_card.md, biz_full.txt, fin.json
"""
import argparse
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
# 한글은 대략 1.5~2자/토큰 -> 5000자 ~= 2.5~3k 토큰
CARD_CHAR_LIMIT = 5000


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
def find_report(corp_code):
    today = datetime.date.today()
    bgn = today - datetime.timedelta(days=365 * 3)
    rows, page, total = [], 1, 1
    while page <= total and page <= 10:
        j = get_json("list.json", corp_code=corp_code,
                     bgn_de=bgn.strftime("%Y%m%d"), end_de=today.strftime("%Y%m%d"),
                     pblntf_ty="A", pblntf_detail_type="A001", page_count=100, page_no=page)
        rows += j.get("list") or []
        total = int(j.get("total_page") or 1)
        page += 1
    # API 필터가 느슨할 수 있어 보고서명으로 한 번 더 거른다 (반기/분기/자율공시 제외)
    rows = [r for r in rows if re.search(r"사업보고서\s*\(\d{4}\.\d{2}\)", r.get("report_nm", ""))]
    if not rows:
        raise RuntimeError("사업보고서 없음 (최근 3년 내 조회 결과에 '사업보고서 (YYYY.MM)' 없음)")
    rows.sort(key=lambda r: (r.get("rcept_dt", ""), r.get("rcept_no", "")), reverse=True)
    rep = rows[0]
    m = re.search(r"\((\d{4})\.\d{2}\)", rep.get("report_nm", ""))
    rep["bsns_year"] = int(m.group(1)) if m else int(rep["rcept_dt"][:4]) - 1
    return rep


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


def pick_fin(rows):
    out = {}
    for label, divs, cands in FIN_ITEMS:
        found = None
        for cand in cands:
            for r in rows:
                if r.get("sj_div") in divs and norm(r.get("account_nm")) == norm(cand):
                    found = {"당기": to_int(r.get("thstrm_amount")),
                             "전기": to_int(r.get("frmtrm_amount")),
                             "account_nm": r.get("account_nm")}
                    break
            if found:
                break
        out[label] = found
    return out


def fetch_fin(corp_code, year):
    for div, basis in (("CFS", "연결"), ("OFS", "별도")):
        j = get_json("fnlttSinglAcntAll.json", corp_code=corp_code,
                     bsns_year=str(year), reprt_code="11011", fs_div=div)
        if j.get("status") == "000" and j.get("list"):
            return {"basis": basis, "year": year, "unit": "원", "items": pick_fin(j["list"])}
    return {"basis": NF, "year": year, "unit": "원", "items": {}}


def eok(v):
    return NF if v is None else f"{v / 1e8:,.0f}"


def fin_block(fin):
    if fin["basis"] == NF:
        return f"## 0. 재무 요약\n재무제표 API 응답 {NF}\n"
    y = fin["year"]
    lines = [f"## 0. 재무 요약 ({fin['basis']}, 단위 억원, FY{y} vs FY{y - 1})",
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
    return "\n".join(lines) + "\n"


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


def pick_tables(blocks, kws, maxn, maxrows):
    out = []
    for b in blocks:
        if b["type"] != "table" or len(b["rows"]) < 2:
            continue
        joined = " ".join(b["rows"])
        if not any(k in joined for k in kws):
            continue
        part = []
        if b["caption"] and len(b["caption"]) <= 80:
            part.append(f"*{b['caption']}*")
        part += [clip(r, 160) for r in b["rows"][:maxrows]]
        if len(b["rows"]) > maxrows:
            part.append(f"(… {len(b['rows']) - maxrows}행 생략, 원문은 biz_full.txt)")
        out.append("\n".join(part))
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


def first_paragraph(blocks, n=350):
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


def build_card(meta, fin, biz, rows, nlines):
    subs = biz["subs"] if biz else {}
    allb = split_blocks(biz["all"]) if biz else []
    B = {k: split_blocks(v) for k, v in subs.items()}
    sales = B.get("sales", [])
    mats = B.get("materials", [])
    rnd = B.get("rnd", [])

    head = (f"# {meta['corp_name']} ({meta['stock_code']}) BM 카드\n"
            f"- 출처: DART {meta['report_nm']} / 접수번호 {meta['rcept_no']} / 접수일 {meta['rcept_dt']}\n"
            f"- 아래는 사업보고서 원문 발췌(기업 서술 포함)와 코드 계산값이며 해석 아님. 미확인 값은 {NF}.\n")

    s1 = section("1. 한 줄 사업 정의 (원문 개요 첫 문단, 기업 서술)",
                 [first_paragraph(B.get("overview", allb))])

    # 제품/부문별 매출·비중 표는 보통 '주요 제품 및 서비스'에 있음 -> 먼저 보고, 없으면 '매출 및 수주상황'
    t2 = (pick_tables(B.get("products", []), ["비중", "%", "매출액"], 2, rows)
          or pick_tables(sales, ["비중", "%", "매출액"], 2, rows))
    s2 = section("2. 매출 구성 (표 원문, 단위는 표 캡션 확인)", t2)

    t3 = [t for t in pick_tables(sales, ["매출처", "수출", "내수"], 2, rows) if t not in t2][:1]
    s3 = section("3. 고객·판매 구조",
                 keyword_lines(sales, ["판매경로", "판매방법", "판매전략", "주요 매출처", "주요매출처",
                                       "수출", "내수", "직판", "대리점", "장기계약", "COA", "CVC", "거래처"], nlines)
                 + t3)

    s4 = section("4. 원가 구조",
                 keyword_lines(mats, ["원재료", "매입", "가격변동", "주요 원자재", "연료", "벙커"], nlines)
                 + pick_tables(mats, ["원재료", "매입", "품목"], 1, rows))

    fleet_kws = ["선박", "선대", "척", "DWT", "dwt", "용선", "보유", "생산능력", "가동률", "CAPA", "Capa", "설비"]
    t5 = pick_tables(mats, fleet_kws, 2, rows) or pick_tables(allb, ["DWT", "dwt", "척", "생산능력", "가동률"], 2, rows)
    s5 = section("5. 생산·설비·R&D (선대/CAPA 표는 없으면 not_found)",
                 t5 + keyword_lines(rnd, ["연구개발비용", "연구개발", "R&D", "수주", "계약"], max(2, nlines - 2))
                 + pick_tables(rnd, ["연구개발"], 1, rows))

    s6 = section("6. 전년 대비 바뀐 문구", ["not_implemented (STEP 2에서 구현)"])
    s7 = f"## 7. 고정 질문 (BM 판별 체크리스트)\n{QUESTIONS}\n"

    return "\n".join([head, fin_block(fin), s1, s2, s3, s4, s5, s6, s7])


def make_card(meta, fin, biz):
    card = ""
    for rows, nlines in [(20, 8), (14, 6), (10, 4), (6, 3)]:
        card = build_card(meta, fin, biz, rows, nlines)
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

    corp = resolve(a.query, load_corps())
    print(f"대상: {corp['corp_name']} ({corp['stock_code']}) corp_code={corp['corp_code']}")
    rep = find_report(corp["corp_code"])
    print(f"보고서: {rep['report_nm']} rcept_no={rep['rcept_no']} 접수일={rep['rcept_dt']}")

    meta = {"corp_name": corp["corp_name"], "stock_code": corp["stock_code"],
            "corp_code": corp["corp_code"], "report_nm": rep["report_nm"],
            "rcept_no": rep["rcept_no"], "rcept_dt": rep["rcept_dt"],
            "generated_at": datetime.datetime.now().isoformat(timespec="seconds")}

    fin = fetch_fin(corp["corp_code"], rep["bsns_year"])
    lines = to_text(fetch_doc(rep["rcept_no"]))
    biz = extract_biz(lines)
    if not biz:
        hints = [l for l in lines if "사업의 내용" in l][:5]
        print(f"[경고] '사업의 내용' 구간 {NF}. 후보 줄: {hints}", file=sys.stderr)
    elif not biz["subs"]:
        print(f"[경고] 하위 항목(1.사업의 개요 등) 분리 {NF}. 전체 구간으로 대체.", file=sys.stderr)

    d = os.path.join(a.out, corp["stock_code"])
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "bm_card.md"), "w", encoding="utf-8") as f:
        f.write(make_card(meta, fin, biz))
    with open(os.path.join(d, "biz_full.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(biz["all"]) if biz else NF)
    with open(os.path.join(d, "fin.json"), "w", encoding="utf-8") as f:
        json.dump({"meta": meta, "fin": fin}, f, ensure_ascii=False, indent=2)
    print(f"완료: {d}/ (bm_card.md, biz_full.txt, fin.json)")


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as e:
        sys.exit(f"오류: {e}")
