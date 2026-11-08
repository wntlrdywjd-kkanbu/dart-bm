"""DART 주요계정 다중조회(fnlttMultiAcnt) 응답 구조 확인용 (몇 번만 호출). 결과는 로그로만 출력."""
import json, os, sys
import dart_bm as D

codes = [v["corp_code"] for v in json.load(open("cache/corp_map.json", encoding="utf-8")).values()][:100]
print("조회 회사 수", len(codes))
for year, rc, nm in ((2026, "11012", "2026 반기"), (2026, "11013", "2026 1분기"), (2025, "11011", "2025 사업")):
    j = D.get_json("fnlttMultiAcnt.json", corp_code=",".join(codes), bsns_year=str(year), reprt_code=rc)
    L = j.get("list") or []
    print(f"=== {nm}: status={j.get('status')} msg={j.get('message')} rows={len(L)} corps={len({r['corp_code'] for r in L})}")
    if not L:
        continue
    print("필드:", sorted(L[0].keys()))
    divs = {}
    for r in L:
        divs.setdefault(r["corp_code"], set()).add(r.get("fs_div"))
    print("연결(CFS) 보유", sum(1 for v in divs.values() if "CFS" in v), "/ 별도만", sum(1 for v in divs.values() if v == {"OFS"}))
    names = sorted({r["account_nm"] for r in L if r.get("sj_div") in ("IS", "CIS")})
    print("손익 계정명 종류:", names[:20])
    shown = 0
    for r in L:
        if r["account_nm"] in ("매출액", "영업이익") and r.get("fs_div") == "CFS" and shown < 4:
            print({k: r.get(k) for k in ("corp_code", "stock_code", "fs_div", "sj_div", "account_nm", "thstrm_nm", "thstrm_amount", "thstrm_add_amount", "frmtrm_nm", "frmtrm_amount", "frmtrm_add_amount", "rcept_no")})
            shown += 1
