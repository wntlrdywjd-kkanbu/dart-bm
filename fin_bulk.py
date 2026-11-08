#!/usr/bin/env python3
"""fin_bulk.py - 전 종목 분기 매출·영업이익(단독) 일괄 수집 — DART 주요계정 다중조회(fnlttMultiAcnt, 회사 100개/호출)

dart_bm.py 의 pick_fin/build_quarters 를 그대로 재사용한다(분기 단독값 = 누적차, 4Q = 사업보고서 − 3분기 누적, 불일치는 check 표시).
모드
  backfill  from-year ~ 올해의 (사업연도 × 보고서 4종) 전부 조회 → 상태 전체 재구성 (약 800~1,000 호출, 수십 분)
  update    작년~올해만 다시 조회(정정공시·새 분기 반영) → 기존 상태에 병합 (약 200 호출). 간격이 며칠이든 동일하게 동작
입력  fin_q/universe.json  ([{code,name}])
출력  fin_q/fin_q.json       소비용(종목별 분기 단독 매출·영업이익[억원], yoy) — 저장된 전체 분기를 누적해서 내보냄
      fin_q/periods.json.gz  내부 상태(보고서 기간별 원 단위 값, 연결/별도 구분, 접수번호)
      fin_q/corp_map.json    종목코드→corp_code (analyze 워크플로의 cache/corp_map.json 과 충돌하지 않게 분리)
환경  DART_API_KEY
"""
import argparse, contextlib, datetime, gzip, io, json, os, re, sys, time

import dart_bm as D

OUT = "fin_q"
UNI = f"{OUT}/universe.json"
PER = f"{OUT}/periods.json.gz"
FQ = f"{OUT}/fin_q.json"
OWN_CMAP = f"{OUT}/corp_map.json"
KEEP = ("매출액", "영업이익")
# pick_fin 은 전역 FIN_ITEMS/EXTRA_ITEMS 를 쓴다 → 매출·영업이익만 뽑게 좁힌다(계정 매칭 로직은 그대로, 경고 소음 제거)
D.FIN_ITEMS = [x for x in D.FIN_ITEMS if x[0] in KEEP]
D.EXTRA_ITEMS = []
D.QFLOWS = list(KEEP)
D.FLOWS = list(KEEP)

REPRT = (("11013", 3), ("11012", 6), ("11014", 9), ("11011", 12))  # 1분기, 반기, 3분기, 사업
CALLS = 0
MAX_CALLS = 3000
_orig_get = D.get


class Budget(Exception):
    pass


def counted_get(path, **params):
    global CALLS
    if CALLS >= MAX_CALLS:
        raise Budget()
    CALLS += 1
    time.sleep(0.05)
    return _orig_get(path, **params)


D.get = counted_get


def jload_gz(fp, default):
    try:
        with gzip.open(fp, "rt", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def jsave(state, fq, meta, cmap):
    os.makedirs(OUT, exist_ok=True)
    with open(OWN_CMAP, "w", encoding="utf-8") as f:
        json.dump({k: v for k, v in cmap.items() if k in state}, f, ensure_ascii=False, separators=(",", ":"))
    with gzip.open(PER, "wt", encoding="utf-8") as f:
        json.dump({"meta": meta, "stocks": state}, f, ensure_ascii=False, separators=(",", ":"))
    with open(FQ, "w", encoding="utf-8") as f:
        json.dump({"meta": meta, "stocks": fq}, f, ensure_ascii=False, separators=(",", ":"))


def corp_for(code, cmap, holder):
    c = cmap.get(code)
    if c:
        return c["corp_code"]
    if holder[0] is None:
        holder[0] = {x["stock_code"]: x for x in D.load_corps() if x["stock_code"]}
    x = holder[0].get(code)
    if x:
        cmap[code] = x
        return x["corp_code"]
    return None


def compact(items):
    return {k: {"당기": v["당기"], "당기누적": v["당기누적"], "전기": v["전기"]} for k, v in items.items() if k in KEEP and v}


def end_month(row):
    m = re.findall(r"(\d{4})\.(\d{2})\.(\d{2})", row.get("thstrm_dt") or "")
    return int(m[-1][1]) if m else None


def fetch_periods(state, by_corp, years):
    """years 의 (보고서 4종)을 100사씩 조회해 state[code]['p'][div][key] 에 반영"""
    corps = sorted(by_corp)
    for year in years:
        for rc, month in REPRT:
            key = f"{year}.{month:02d}"
            got = 0
            for i in range(0, len(corps), 100):
                batch = corps[i:i + 100]
                j = D.get_json("fnlttMultiAcnt.json", corp_code=",".join(batch), bsns_year=str(year), reprt_code=rc)
                rows = j.get("list") or []
                grp = {}
                for r in rows:
                    grp.setdefault((r["corp_code"], r.get("fs_div")), []).append(r)
                for (cc, div), rs in grp.items():
                    code = by_corp.get(cc)
                    if not code or div not in ("CFS", "OFS"):
                        continue
                    with contextlib.redirect_stderr(io.StringIO()):
                        items = compact(D.pick_fin(rs))
                    if not items:
                        continue
                    rec = state.setdefault(code, {"corp": cc, "p": {}, "r": {}})
                    rec["p"].setdefault(div, {})[key] = items
                    rec["r"][key] = rs[0].get("rcept_no")
                    em = end_month(rs[0])
                    if em is not None and em != month:
                        rec["nd"] = True  # 12월 결산이 아니면 분기 환산 가정이 깨지므로 건너뜀
                    got += 1
            print(f"  {key}: {got}건 (호출 누계 {CALLS})", flush=True)


def to_uk(v):
    return None if v is None else round(v / 1e8, 2)


def yoy(cur, prev):
    if cur is None or prev is None:
        return None, "na"
    if prev > 0:
        return round((cur - prev) / prev * 100, 1), ("normal" if cur > 0 else "turned_loss")
    if cur > 0:
        return None, "turned_profit"
    return None, "loss"


def consumer(rec):
    """상태 → 소비용 분기 목록(최신순). 연결 우선(최신 기간에 연결이 있으면 연결, 아니면 별도). yoy 는 금액으로 직접 계산"""
    if rec.get("nd"):
        return {"skip": "비12월결산"}
    P = rec["p"]
    if not P:
        return {"skip": "재무 없음"}
    latest = lambda d: max(d) if d else ""
    div = "CFS" if latest(P.get("CFS", {})) >= latest(P.get("OFS", {})) and P.get("CFS") else "OFS"
    p = {tuple(map(int, k.split("."))): v for k, v in P[div].items()}
    lk = max(p)
    qi = lambda k: k[0] * 4 + k[1] // 3
    n = min(80, qi(lk) - qi(min(p)) + 1)
    qs = D.build_quarters(p, lk, 12, n=n)
    out = []
    for i, q in enumerate(qs):
        s, o = q["values"].get("매출액"), q["values"].get("영업이익")
        if s is None and o is None:
            continue
        base = qs[i + 4]["values"] if i + 4 < len(qs) else {}
        ys, _ = yoy(s, base.get("매출액"))
        yo, st = yoy(o, base.get("영업이익"))
        out.append({"k": q["key"], "s": to_uk(s), "o": to_uk(o), "ys": ys, "yo": yo, "st": st,
                    "how": q["how"], "chk": q["check"], "src": "DART"})
    if not out:
        return {"skip": "분기값 없음"}
    other = "OFS" if div == "CFS" else "CFS"
    return {"basis": "연결" if div == "CFS" else "별도", "alt": bool(P.get(other)), "rd": rec["r"].get(f"{lk[0]}.{lk[1]:02d}"), "q": out}


def main():
    global MAX_CALLS
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["backfill", "update"])
    ap.add_argument("--from-year", type=int, default=2019)
    ap.add_argument("--max-calls", type=int, default=3000)
    a = ap.parse_args()
    MAX_CALLS = a.max_calls
    if not D.KEY:
        sys.exit("DART_API_KEY 없음")
    uni = {x["code"]: x["name"] for x in json.load(open(UNI, encoding="utf-8"))}
    saved = jload_gz(PER, {"meta": {}, "stocks": {}})
    state, meta = saved["stocks"], saved["meta"]
    cmap = {}
    for fp in (os.path.join(D.CACHE, "corp_map.json"), OWN_CMAP):
        try:
            cmap.update(json.load(open(fp, encoding="utf-8")))
        except (OSError, ValueError):
            pass
    holder, by_corp, nocorp = [None], {}, []
    for code in uni:
        cc = corp_for(code, cmap, holder)
        if cc:
            by_corp[cc] = code
        else:
            nocorp.append(code)
    this_year = datetime.date.today().year
    if a.mode == "backfill":
        years, state = list(range(a.from_year, this_year + 1)), {}
    else:
        if not state:
            sys.exit("상태 없음: 먼저 backfill 을 실행하세요.")
        years = [this_year - 1, this_year]
    print(f"[{a.mode}] 회사 {len(by_corp)}개(corp_code 없음 {len(nocorp)}) · 사업연도 {years[0]}~{years[-1]}", flush=True)
    stopped = ""
    try:
        fetch_periods(state, by_corp, years)
    except Budget:
        stopped = "호출 예산 소진: 결과를 저장하지 않고 종료" 
        sys.exit(f"[중단] {stopped}")
    fq = {}
    for code in uni:
        if code in state:
            fq[code] = consumer(state[code])
        else:
            fq[code] = {"skip": "corp_code 없음" if code in nocorp else "DART 정기보고서 없음"}
    meta.update(last_scan=datetime.date.today().strftime("%Y%m%d"), mode=a.mode, years=[years[0], years[-1]],
                calls_last=CALLS, updated=datetime.datetime.now().strftime("%Y-%m-%d %H:%M"))
    if a.mode == "backfill":
        meta["backfill_start"] = meta["last_scan"]
    meta["n"] = len(fq)
    meta["n_ok"] = sum(1 for v in fq.values() if "q" in v)
    meta["n_skip"] = sum(1 for v in fq.values() if "skip" in v)
    jsave(state, fq, meta, cmap)
    from collections import Counter
    print("[건너뜀 사유]", dict(Counter(v["skip"] for v in fq.values() if "skip" in v)))
    print(f"[완료] 호출 {CALLS} / 종목 {meta['n']} (정상 {meta['n_ok']}, 건너뜀 {meta['n_skip']})")


if __name__ == "__main__":
    main()
