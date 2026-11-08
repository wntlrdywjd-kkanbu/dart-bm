"""fnlttMultiAcnt 묶음 크기별 응답 시간 측정 (몇 번만 호출)."""
import json, time
import dart_bm as D

uni = [x["code"] for x in json.load(open("fin_q/universe.json", encoding="utf-8"))]
corps = D.load_corps()
bycode = {c["stock_code"]: c["corp_code"] for c in corps if c["stock_code"]}
codes = [bycode[c] for c in uni if c in bycode]
print("유니버스 corp", len(codes), flush=True)
for size, off in ((10, 0), (25, 100), (50, 200), (100, 400), (100, 800), (100, 1200)):
    batch = codes[off:off + size]
    t = time.time()
    j = D.get_json("fnlttMultiAcnt.json", corp_code=",".join(batch), bsns_year="2026", reprt_code="11012")
    L = j.get("list") or []
    print(f"size={size} off={off}: {time.time()-t:.1f}s status={j.get('status')} rows={len(L)} corps={len({r['corp_code'] for r in L})}", flush=True)
