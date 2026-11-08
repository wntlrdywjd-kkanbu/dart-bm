"""fnlttMultiAcnt: 연도별 응답 시간 vs 가공 시간 분리 측정."""
import contextlib, io, json, time
import dart_bm as D
import fin_bulk as F

uni = [x["code"] for x in json.load(open("fin_q/universe.json", encoding="utf-8"))]
bycode = {c["stock_code"]: c["corp_code"] for c in D.load_corps() if c["stock_code"]}
codes = [bycode[c] for c in uni if c in bycode]
batch = codes[400:500]
for year, rc in ((2026, "11012"), (2024, "11012"), (2022, "11013"), (2019, "11013"), (2019, "11014")):
    t = time.time()
    j = D._orig_get if False else D.get_json("fnlttMultiAcnt.json", corp_code=",".join(batch), bsns_year=str(year), reprt_code=rc)
    t1 = time.time() - t
    L = j.get("list") or []
    t = time.time()
    grp = {}
    for r in L:
        grp.setdefault((r["corp_code"], r.get("fs_div")), []).append(r)
    with contextlib.redirect_stderr(io.StringIO()):
        n = sum(1 for k, rs in grp.items() if F.compact(D.pick_fin(rs)))
    print(f"{year}/{rc}: 응답 {t1:.1f}s rows={len(L)} | 가공 {time.time()-t:.2f}s 그룹={len(grp)} 유효={n}", flush=True)
