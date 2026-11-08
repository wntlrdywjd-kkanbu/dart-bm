#!/usr/bin/env python3
"""fin_bulk.py - 전 종목 분기 매출·영업이익(단독, 최근 12분기) 일괄 수집 (DART fnlttSinglAcntAll)

dart_bm.py 의 검증된 로직(fetch_period/pick_fin/build_quarters)을 그대로 재사용한다.
모드
  backfill  universe 중 아직 수집 안 된 종목을 처음부터 수집. 호출 예산(--max-calls)을 다 쓰면 저장하고 정상 종료 → 다음 실행이 이어서 처리
  update    마지막 수집일(-3일 겹침)부터 오늘까지 접수된 정기보고서를 공시목록에서 찾아, 새 보고서가 있는 종목만 갱신(간격이 며칠이든 동일)
  codes     --codes 로 지정한 종목을 강제 재수집(검증용)
입력  fin_q/universe.json  ([{code,name}])
출력  fin_q/fin_q.json       소비용(종목별 최근 12분기 단독 매출·영업이익[억원], yoy)
      fin_q/periods.json.gz  내부 상태(보고서 기간별 원 단위 누적/3개월 값, 접수번호) — 증분 갱신 근거
환경  DART_API_KEY
"""
import argparse, contextlib, datetime, gzip, io, json, os, re, sys, time

import dart_bm as D

OUT = "fin_q"
UNI = f"{OUT}/universe.json"
PER = f"{OUT}/periods.json.gz"
FQ = f"{OUT}/fin_q.json"
NQ = 12
KEEP = ("매출액", "영업이익")
# pick_fin 은 전역 FIN_ITEMS/EXTRA_ITEMS 를 쓴다 → 매출·영업이익만 뽑도록 좁힌다(계정 매칭 로직은 그대로, 경고 소음만 제거)
D.FIN_ITEMS = [x for x in D.FIN_ITEMS if x[0] in KEEP]
D.EXTRA_ITEMS = []
D.QFLOWS = list(KEEP)
D.FLOWS = list(KEEP)

CALLS = 0
MAX_CALLS = 17000
_orig_get = D.get


class Budget(Exception):
    pass


def counted_get(path, **params):
    global CALLS
    if CALLS >= MAX_CALLS:
        raise Budget()
    CALLS += 1
    time.sleep(0.03)
    return _orig_get(path, **params)


D.get = counted_get


def jload_gz(fp, default):
    try:
        with gzip.open(fp, "rt", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def jsave(state, fq, meta):
    os.makedirs(OUT, exist_ok=True)
    with gzip.open(PER, "wt", encoding="utf-8") as f:
        json.dump({"meta": meta, "stocks": state}, f, ensure_ascii=False, separators=(",", ":"))
    out = {"meta": meta, "stocks": fq}
    with open(FQ, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))


def corp_for(code, cmap, corps_holder):
    c = cmap.get(code)
    if c:
        return c["corp_code"]
    if corps_holder[0] is None:
        corps_holder[0] = {x["stock_code"]: x for x in D.load_corps() if x["stock_code"]}
    x = corps_holder[0].get(code)
    if x:
        D.save_corp(x)
        cmap[code] = x
        return x["corp_code"]
    return None


def list_reports(corp_code):
    """정기보고서 기간별 최신 접수(정정 포함). {(연,월): {kind,rcept_no,rcept_dt}} (원문 확인 호출 없음)"""
    today = datetime.date.today()
    bgn = today - datetime.timedelta(days=365 * 5 + 30)
    rows, page, total = [], 1, 1
    while page <= total and page <= 10:
        j = D.get_json("list.json", corp_code=corp_code, bgn_de=bgn.strftime("%Y%m%d"),
                       end_de=today.strftime("%Y%m%d"), pblntf_ty="A", page_count=100, page_no=page)
        rows += j.get("list") or []
        total = int(j.get("total_page") or 1)
        page += 1
    return best_by_period(rows)


def best_by_period(rows):
    best = {}
    for r in rows:
        m = D.PERIODIC_RE.search(r.get("report_nm", ""))
        if not m:
            continue
        key = (int(m.group(2)), int(m.group(3)))
        r = {"kind": m.group(1), "rcept_no": r["rcept_no"], "rcept_dt": r["rcept_dt"]}
        if key not in best or (r["rcept_dt"], r["rcept_no"]) > (best[key]["rcept_dt"], best[key]["rcept_no"]):
            best[key] = r
    return best


def reprt(kind, month):
    return "11011" if kind == "사업" else "11012" if kind == "반기" else "11013" if D.qnum(month) == 1 else "11014"


def compact(items):
    return {k: {"당기": v["당기"], "당기누적": v["당기누적"]} for k, v in items.items() if k in KEEP and v}


def needed_keys(lk):
    need, key = [], lk
    for _ in range(NQ):
        need.append(key)
        if D.qnum(key[1]) > 1:
            need.append(D.prev_period(*key))
        key = D.prev_period(*key)
    return list(dict.fromkeys(need))


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
    """periods 상태 → 소비용 분기 목록(최신순). yoy 는 금액으로 직접 계산"""
    p = {tuple(map(int, k.split("."))): v for k, v in rec["p"].items()}
    lk = max(p) if p else None
    if not lk:
        return None
    qi = lambda k: k[0] * 4 + k[1] // 3
    n = min(60, max(NQ, qi(lk) - qi(min(p)) + 1))  # 저장된 기간 전체(누적). 백필 직후는 12분기, 시즌마다 1분기씩 늘어난다
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
    return {"basis": rec.get("basis"), "rd": rec.get("rd"), "q": out}


def fetch_keys(corp_code, keys, reps, div0):
    """keys 의 기간별 보고서 재무를 받아 compact 로 반환. (dict, div0, basis)"""
    res, basis = {}, {}
    for key in keys:
        r = reps.get(key)
        if not r:
            continue
        bases = (div0, "OFS" if div0 == "CFS" else "CFS") if div0 else ("CFS", "OFS")
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            got = D.fetch_period(corp_code, key[0], reprt(r["kind"], key[1]), bases)
        if got:
            res[f"{key[0]}.{key[1]:02d}"] = compact(got["items"])
            div0 = div0 or got["div"]
            basis[f"{key[0]}.{key[1]:02d}"] = got["basis"]
    return res, div0, basis


def full_collect(corp_code):
    reps = list_reports(corp_code)
    ann = [k for k, r in reps.items() if r["kind"] == "사업"]
    if not ann:
        return {"skip": "사업보고서 없음"}
    fy_end = max(ann)[1]
    if fy_end != 12:
        return {"skip": f"비12월결산({fy_end}월)"}
    lk = max(reps)
    p, div0, basis = fetch_keys(corp_code, needed_keys(lk), reps, None)
    if not p:
        return {"skip": "재무 응답 없음"}
    mixed = len(set(basis.values())) > 1
    return {"corp": corp_code, "div": div0, "basis": ("연결" if div0 == "CFS" else "별도") + ("(혼재)" if mixed else ""),
            "r": {f"{k[0]}.{k[1]:02d}": [v["rcept_dt"], v["rcept_no"]] for k, v in reps.items()},
            "rd": max(v["rcept_dt"] for v in reps.values()), "p": p,
            "ts": datetime.datetime.now().strftime("%Y-%m-%d")}


def incremental(rec, new_reps):
    """새/정정된 보고서 기간만 다시 받는다. new_reps: {(연,월): {kind,rcept_no,rcept_dt}}"""
    stored = {tuple(map(int, k.split("."))): v for k, v in rec["r"].items()}
    todo = {}
    for k, r in new_reps.items():
        old = stored.get(k)
        if not old or (r["rcept_dt"], r["rcept_no"]) > (old[0], old[1]):
            todo[k] = r
    if not todo:
        return False
    # kind 복원: 새 보고서는 목록의 kind 사용, 기존은 12월결산 전제로 월에서 추정(12=사업, 6=반기, 그 외=분기)
    allreps = {k: {"kind": "사업" if k[1] == 12 else "반기" if k[1] == 6 else "분기", "rcept_no": v[1], "rcept_dt": v[0]}
               for k, v in stored.items()}
    allreps.update(todo)
    lk = max(allreps)
    keys = list(todo)
    prevk = [D.prev_period(*k) for k in todo if D.qnum(k[1]) > 1]
    for pk in prevk:
        if f"{pk[0]}.{pk[1]:02d}" not in rec["p"] and pk in allreps:
            keys.append(pk)
    got, div0, _ = fetch_keys(rec["corp"], list(dict.fromkeys(keys)), allreps, rec["div"])
    if not got:
        return False
    rec["p"].update(got)
    rec["r"] = {f"{k[0]}.{k[1]:02d}": [v["rcept_dt"], v["rcept_no"]] for k, v in allreps.items()}
    rec["rd"] = max(v["rcept_dt"] for v in allreps.values())
    rec["ts"] = datetime.datetime.now().strftime("%Y-%m-%d")
    return True  # 오래된 기간은 지우지 않고 계속 누적한다


def main():
    global MAX_CALLS
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["backfill", "update", "codes"])
    ap.add_argument("--max-calls", type=int, default=17000)
    ap.add_argument("--codes", default="")
    ap.add_argument("--since", default="", help="update 시작일 강제(YYYYMMDD). 기본: 마지막 수집일-3일")
    a = ap.parse_args()
    MAX_CALLS = a.max_calls
    if not D.KEY:
        sys.exit("DART_API_KEY 없음")
    uni = {x["code"]: x["name"] for x in json.load(open(UNI, encoding="utf-8"))}
    saved = jload_gz(PER, {"meta": {}, "stocks": {}})
    state, meta = saved["stocks"], saved["meta"]
    fq = json.load(open(FQ, encoding="utf-8"))["stocks"] if os.path.exists(FQ) else {}
    try:
        cmap = json.load(open(os.path.join(D.CACHE, "corp_map.json"), encoding="utf-8"))
    except (OSError, ValueError):
        cmap = {}
    corps = [None]
    started, n_new, stopped = datetime.date.today(), 0, ""

    def commit_one(code, rec):
        state[code] = rec
        if "skip" in rec:
            fq[code] = {"skip": rec["skip"]}
        else:
            c = consumer(rec)
            if c:
                fq[code] = c

    try:
        if a.mode in ("backfill", "codes"):
            todo = [c for c in (a.codes.split(",") if a.mode == "codes" else uni) if c in uni and (a.mode == "codes" or c not in state)]
            print(f"[{a.mode}] 대상 {len(todo)}종목 (전체 {len(uni)}, 기수집 {len(state)})")
            for i, code in enumerate(todo, 1):
                cc = corp_for(code, cmap, corps)
                if not cc:
                    commit_one(code, {"skip": "corp_code 없음"})
                    continue
                try:
                    commit_one(code, full_collect(cc))
                except RuntimeError as e:
                    if "020" in str(e):
                        raise Budget()
                    print(f"[오류] {code} {uni[code]}: {e}")
                    continue
                n_new += 1
                if i % 50 == 0:
                    meta.update(calls_last=CALLS, n=len(state))
                    jsave(state, fq, meta)
                    print(f"  {i}/{len(todo)} 호출 {CALLS}")
        else:  # update
            last = a.since or meta.get("last_scan")
            if not last:
                sys.exit("last_scan 없음: 먼저 backfill 이 끝나야 합니다(또는 --since).")
            bgn = datetime.datetime.strptime(last, "%Y%m%d").date() - datetime.timedelta(days=3)
            if not a.since and meta.get("backfill_done") is not True:
                print("[경고] backfill 미완료 상태에서 update 실행")
            by_corp = {state[c]["corp"]: c for c in state if "corp" in state[c]}
            hits = {}
            d0 = bgn
            while d0 <= started:
                d1 = min(d0 + datetime.timedelta(days=29), started)
                page, total = 1, 1
                while page <= total:
                    j = D.get_json("list.json", bgn_de=d0.strftime("%Y%m%d"), end_de=d1.strftime("%Y%m%d"),
                                   pblntf_ty="A", page_count=100, page_no=page)
                    total = int(j.get("total_page") or 1)
                    for r in j.get("list") or []:
                        c = by_corp.get(r.get("corp_code"))
                        if c:
                            hits.setdefault(c, []).append(r)
                    page += 1
                d0 = d1 + datetime.timedelta(days=1)
            print(f"[update] {bgn}~{started} 새 정기공시 보유 종목 {len(hits)}")
            for code, rows in hits.items():
                new_reps = best_by_period(rows)
                try:
                    if incremental(state[code], new_reps):
                        commit_one(code, state[code])
                        n_new += 1
                except RuntimeError as e:
                    if "020" in str(e):
                        raise Budget()
                    print(f"[오류] {code}: {e}")
        if a.mode == "update":
            meta["last_scan"] = started.strftime("%Y%m%d")
    except Budget:
        stopped = "호출 예산 소진/한도 → 저장 후 종료(다음 실행이 이어서 처리)"
    meta["calls_last"] = CALLS
    meta["updated"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    if a.mode == "backfill":
        meta.setdefault("backfill_start", started.strftime("%Y%m%d"))
        meta["backfill_done"] = all(c in state for c in uni)
        if meta["backfill_done"] and "last_scan" not in meta:
            meta["last_scan"] = meta["backfill_start"]  # 첫 백필 시작일: 그 이후 접수분은 update 가 받는다
    meta["n"] = len(state)
    meta["n_ok"] = sum(1 for v in fq.values() if "q" in v)
    meta["n_skip"] = sum(1 for v in fq.values() if "skip" in v)
    jsave(state, fq, meta)
    print(f"[완료] 호출 {CALLS} / 신규·갱신 {n_new} / 수집종목 {meta['n']} (정상 {meta['n_ok']}, 건너뜀 {meta['n_skip']}) "
          f"backfill_done={meta.get('backfill_done')} {stopped}")


if __name__ == "__main__":
    main()
