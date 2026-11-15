#!/usr/bin/env python3
"""신규상장 종목용: 정기보고서가 없을 때 DART 발행공시(증권신고서·투자설명서)와 있는 정기보고서를
원문 텍스트로 저장한다 (분석은 Claude 가 읽고 수행). 사용: python tools/prospectus_fetch.py 동원파츠
출력: out/{code}/prospectus/{접수번호}_{보고서명}.txt , out/{code}/prospectus/INDEX.txt
"""
import datetime, gzip, os, re, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dart_bm as d


def find_corp(q):
    corps = d.load_corps()
    hit = [c for c in corps if c["stock_code"] and (c["stock_code"] == q or c["corp_name"] == q)]
    if not hit:
        hit = [c for c in corps if c["stock_code"] and q in c["corp_name"]]
    if len(hit) != 1:
        raise SystemExit(f"후보 {len(hit)}개: {[(c['corp_name'], c['stock_code']) for c in hit[:10]]}")
    return hit[0]


def list_reports(corp_code, ty):
    today = datetime.date.today()
    bgn = today - datetime.timedelta(days=365 * 3)
    rows, page, total = [], 1, 1
    while page <= total and page <= 5:
        j = d.get_json("list.json", corp_code=corp_code, bgn_de=bgn.strftime("%Y%m%d"),
                       end_de=today.strftime("%Y%m%d"), pblntf_ty=ty, page_count=100, page_no=page)
        rows += j.get("list") or []
        total = int(j.get("total_page") or 1)
        page += 1
    return rows


def main():
    corp = find_corp(sys.argv[1])
    code = corp["stock_code"]
    out = os.path.join("out", code, "prospectus")
    os.makedirs(out, exist_ok=True)
    idx = [f"{corp['corp_name']} ({code}) corp_code={corp['corp_code']}"]
    rows = []
    for ty in ("A", "C", "B", "I"):  # 정기, 발행, 주요사항, 거래소공시
        try:
            rs = list_reports(corp["corp_code"], ty)
        except RuntimeError as e:
            idx.append(f"[{ty}] 목록 실패: {e}")
            continue
        for r in rs:
            idx.append(f"[{ty}] {r['rcept_dt']} {r['rcept_no']} {r['report_nm']}")
            if ty in ("A", "C"):
                rows.append(r)
    want = [r for r in rows if re.search(r"투자설명서|증권신고서|(사업|반기|분기)보고서|일괄", r["report_nm"])
            and "철회" not in r["report_nm"]]
    for r in want:
        fn = f"{r['rcept_no']}_{re.sub(r'[^0-9A-Za-z가-힣()]+', '_', r['report_nm'])[:40]}.txt"
        fp = os.path.join(out, fn)
        if os.path.exists(fp):
            continue
        try:
            lines = d.to_text(d.fetch_doc(r["rcept_no"]))
        except RuntimeError as e:
            idx.append(f"  원문 실패 {r['rcept_no']}: {e}")
            continue
        with open(fp, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        idx.append(f"  저장 {fn} lines={len(lines)}")
    open(os.path.join(out, "INDEX.txt"), "w", encoding="utf-8").write("\n".join(idx))
    print("\n".join(idx))


if __name__ == "__main__":
    main()
