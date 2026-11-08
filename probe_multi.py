"""현재 DART 응답 속도 점검: 묶음 크기·단건 조회 비교 (재시도 없이 원시 요청, 호출 8번)."""
import json, os, time, requests
import dart_bm as D

uni = [x["code"] for x in json.load(open("fin_q/universe.json", encoding="utf-8"))]
bycode = {c["stock_code"]: c["corp_code"] for c in D.load_corps() if c["stock_code"]}
codes = [bycode[c] for c in uni if c in bycode]
K = os.environ["DART_API_KEY"].strip()

def call(path, **p):
    p["crtfc_key"] = K
    t = time.time()
    try:
        r = requests.get(f"https://opendart.fss.or.kr/api/{path}", params=p, timeout=100)
        j = r.json()
        return time.time() - t, j.get("status"), len(j.get("list") or []), len(r.content)
    except Exception as e:
        return time.time() - t, type(e).__name__, 0, 0

plan = [("multi", 10, 600), ("multi", 100, 600), ("single", 1, 600), ("multi", 10, 700), ("multi", 100, 700),
        ("single", 1, 700), ("multi", 25, 800), ("multi", 100, 1000)]
for kind, size, off in plan:
    if kind == "multi":
        res = call("fnlttMultiAcnt.json", corp_code=",".join(codes[off:off + size]), bsns_year="2025", reprt_code="11012")
    else:
        res = call("fnlttSinglAcntAll.json", corp_code=codes[off], bsns_year="2025", reprt_code="11012", fs_div="CFS")
    print(f"{kind} size={size} off={off}: {res[0]:.1f}s status={res[1]} rows={res[2]} bytes={res[3]}", flush=True)
