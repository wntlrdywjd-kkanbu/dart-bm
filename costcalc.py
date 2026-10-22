"""cache/cost/*.json(비용의 성격별 분류 원문 표) → 분기별 계정 금액 → 변동/준변동/고정 → 공헌이익률·BEP"""
import glob, json, os, re
import costmap

NUM = re.compile(r"^\(?-?[\d,]+(\.\d+)?\)?$")


def _n(c):
    c = c.replace(" ", "")
    if c in ("-", "", "–"):
        return 0.0
    if not NUM.match(c):
        return None
    neg = c.startswith("(") or c.startswith("-")
    v = float(re.sub(r"[(),\-]", "", c))
    return -v if neg else v


def parse_table(t):
    """표 1개 → {'q':{계정:3개월}, 'cum':{계정:누적}, 'total':..} (분기·반기 표) 또는 연간 표는 q 없이 cum 만. 단위는 원으로 환산."""
    rows = t["rows"]
    blob = " ".join(t.get("ctx", [])[-2:]) + " " + " ".join(rows[:2])
    mu = re.search(r"단위\s*[:：]?\s*(백만원|천원|원)", blob)
    mult = {"백만원": 1e6, "천원": 1e3, "원": 1.0}.get(mu.group(1) if mu else "천원", 1e3)
    unit_known = bool(mu)
    quarterly = any("3개월" in r for r in rows[:3])
    q, cum, total = {}, {}, None
    total_first = None
    total_label = ""
    last_tot_label = ""
    for r in rows:
        cells = [x.strip() for x in r.strip().strip("|").split("|")]
        if len(cells) < 2:
            continue
        name = cells[0]
        vals = [_n(c) for c in cells[1:]]
        # '성격별 비용 합계 | 제품과 재공품의 감소(증가) | 값' 처럼 앞 칸이 합계 라벨을 반복하는 행: 둘째 칸을 계정명으로
        if (name and vals and vals[0] is None and len(cells) > 2 and _n(cells[2]) is not None
                and (re.sub(r"\s", "", name).endswith("합계") or re.sub(r"\s", "", name) == "성격별비용") and cells[1]):
            name = cells[1]
            vals = [_n(c) for c in cells[2:]]
        if not name or vals[0] is None or name in ("구분", "구 분"):
            continue
        nn = re.sub(r"\(\*+\d*\)", "", re.sub(r"\s", "", name))
        is_tot = nn.startswith(("합계", "계", "매출원가와판매비", "매출원가및판매비")) or nn.endswith("합계") or nn == "성격별비용"  # 테스: 합계 행이 '성격별 비용'. 한양디지텍: 합계 행이 '매출원가와 판매비와관리비'
        if quarterly:
            if len(vals) < 2 or vals[1] is None:
                continue
            a, b = vals[0] * mult, vals[1] * mult
        else:
            a = b = vals[0] * mult
        if is_tot:
            last_tot_label = nn
            if not q:  # 합계 행이 맨 위에 있는 표(서진시스템): 합계를 기억하고 항목을 계속 읽는다
                total_first = (a, b)
                continue
            total = (a, b)
            total_label = nn
            break  # 첫 합계에서 종료(연결 표 하나만)
        q[name] = q.get(name, 0.0) + a
        cum[name] = cum.get(name, 0.0) + b
    if total is None and total_first is not None and len(q) >= 4:
        # 합계 선행 표는 항목 합이 합계와 2% 이내일 때만 인정
        sa, sb = sum(q.values()), sum(cum.values())
        if abs(sa - total_first[0]) <= 0.02 * abs(total_first[0]) and abs(sb - total_first[1]) <= 0.02 * abs(total_first[1]):
            total = total_first
    if total is None or len(q) < 4:
        return None
    if total_label == "" and last_tot_label:
        total_label = last_tot_label
    return {"q": q, "cum": cum, "total": total, "quarterly": quarterly, "unit_known": unit_known, "total_label": total_label}


def load(cache_dir="cache/cost", files=None):
    """보고서별 파싱 결과 {key(YYYY.MM): {...}}"""
    out = {}
    for fp in (glob.glob(os.path.join(cache_dir, "*.json")) if not files else [os.path.join(cache_dir, f) for f in files if os.path.exists(os.path.join(cache_dir, f))]):
        d = json.load(open(fp, encoding="utf-8"))
        cands = []
        for t in d["tables"]:
            if not any("성격별" in c for c in t.get("ctx", [])[-2:]):
                continue
            p = parse_table(t)
            if p:
                p["_last_ctx"] = "성격별" in (t.get("ctx") or [""])[-1]
                cands.append(p)
        # 판관비만 담은 표('판매비와관리비 합계'; 라벨이 '급여, 판관비' 등)가 성격별 표보다 앞에 오는 보고서(테스): 성격별 전체 표를 우선
        full = [p for p in cands if not p.get("total_label", "").startswith("판매비와관리비")]
        # 직전 제목에 '성격별'이 있는 표를 더 우선(제목이 '(1) 판매비와 관리비의 내역'인 표는 판관비 표: 2024.12 테스)
        p = ([x for x in full if x["_last_ctx"]] or full or cands)[0] if cands else None
        if p:
            # 같은 기간의 정정 보고서가 있으면 접수번호가 큰 쪽(나중 것)이 덮어쓴다
            prev = out.get(d["key"])
            if prev is None or os.path.basename(fp) > prev["_f"]:
                p["_f"] = os.path.basename(fp)
                p["kind"] = d["kind"]
                out[d["key"]] = p
    return out


def quarters(rep):
    """보고서 dict → 분기별 {YYYY.MM: {계정: 금액}} (3개월 값, 4Q = 연간 − 3Q 누적)"""
    res = {}
    for k in sorted(rep):
        y, m = int(k[:4]), int(k[5:])
        r = rep[k]
        if m == 12:
            prev = rep.get(f"{y}.09")
            if prev:
                names = set(r["cum"]) | set(prev["cum"])
                res[k] = {n: r["cum"].get(n, 0.0) - prev["cum"].get(n, 0.0) for n in names}
        elif not r["quarterly"] and m in (6, 9):  # 3개월 열 없이 누적만 있는 반기·3분기 표: 직전 분기 누적과의 차
            pk = f"{y}.{m - 3:02d}"
            if pk in rep:
                res[k] = {n: r["cum"].get(n, 0.0) - rep[pk]["cum"].get(n, 0.0) for n in set(r["cum"]) | set(rep[pk]["cum"])}
        elif r["quarterly"]:
            res[k] = dict(r["q"])
            if m == 6 and f"{y}.03" not in rep:  # 1Q 보고서에 주석이 없으면 반기 누적 − 2Q(3개월)
                res[f"{y}.03"] = {n: r["cum"].get(n, 0.0) - r["q"].get(n, 0.0) for n in set(r["cum"]) | set(r["q"])}
        elif m == 3:
            res[k] = dict(r["cum"])  # 1Q 는 3개월=누적(단일 열 표)
    return res


def label(k):
    return f"{k[2:4]}.{int(k[5:]) // 3}Q"


def _fix_units(rep, q20):
    """표에 단위 표기가 없는 보고서: 합계 비용 / 매출액 비율이 1에 가장 가까워지도록 단위(×1e-3/1/×1e3)를 고른다."""
    import math
    lab = {l: i for i, l in enumerate(q20["labels"])}
    out = {}
    for k, r in rep.items():
        if r.get("unit_known", True):
            out[k] = r
            continue
        m = int(k[5:])
        if r["quarterly"] or m == 3:
            i = lab.get(label(k))
            R = q20["매출액"][i] if i is not None else None
            tot = r["total"][0]
        else:
            idx = [lab.get(f"{k[2:4]}.{q}Q") for q in range(1, m // 3 + 1)]  # 반기는 1~2Q, 3분기 누적은 1~3Q 합과 비교
            R = sum(q20["매출액"][j] for j in idx) if all(j is not None and q20["매출액"][j] for j in idx) else None
            tot = r["total"][1]
        if not R or not tot:
            out[k] = r
            continue
        g = min((1e-3, 1.0, 1e3), key=lambda g: abs(math.log(tot * g / R)))
        if g != 1.0:
            r = {**r, "q": {n: v * g for n, v in r["q"].items()}, "cum": {n: v * g for n, v in r["cum"].items()},
                 "total": (r["total"][0] * g, r["total"][1] * g)}
        out[k] = r
    return out


def build(rep, q20, overrides=None, semi=0.5):
    """rep: load() 결과. q20: 대시보드 20분기 시계열(원 단위, labels/매출액/영업이익). 반환: 대시보드용 dict 또는 None"""
    rep = _fix_units(rep, q20)
    qq = quarters(rep)
    keys = sorted(qq)
    if len(keys) < 5:
        return None
    lab = {l: i for i, l in enumerate(q20["labels"])}
    rows = []
    for k in keys:
        acc = qq[k]
        tot = {costmap.V: 0.0, costmap.S: 0.0, costmap.F: 0.0}
        std_amt = {}
        for raw, amt in acc.items():
            std, cls = costmap.classify(raw, overrides)
            tot[cls] += amt
            std_amt[(std, cls)] = std_amt.get((std, cls), 0.0) + amt
        L = label(k)
        i = lab.get(L)
        R = q20["매출액"][i] if i is not None else None
        O = q20["영업이익"][i] if i is not None else None
        rows.append({"k": k, "L": L, "V": tot[costmap.V], "S": tot[costmap.S], "F": tot[costmap.F], "R": R, "O": O, "acc": acc,
                     "std": std_amt, "tc": sum(tot.values())})
    # 연속 분기 확인 후 TTM
    def consec(a, b):
        ya, ma, yb, mb = int(a[:4]), int(a[5:]), int(b[:4]), int(b[5:])
        return (yb * 12 + mb) - (ya * 12 + ma) == 3
    out = {"labels": [r["L"] for r in rows], "V": [r["V"] / 1e8 for r in rows], "S": [r["S"] / 1e8 for r in rows], "F": [r["F"] / 1e8 for r in rows],
           "R": [None if r["R"] is None else r["R"] / 1e8 for r in rows],
           "chk": [None if (r["R"] is None or r["O"] is None or not r["R"]) else (r["R"] - r["tc"] - r["O"]) / r["R"] * 100 for r in rows],
           "dep": [sum(v for (sn, _), v in r["std"].items() if sn == "감가·상각") / 1e8 for r in rows], "semi": semi}
    t = {k: [] for k in ("cmA", "cmB", "bepA", "bepB", "R", "mosB", "fixB", "varB")}
    for i, r in enumerate(rows):
        w = rows[i - 3:i + 1] if i >= 3 else []
        ok = len(w) == 4 and all(consec(w[j]["k"], w[j + 1]["k"]) for j in range(3)) and all(x["R"] for x in w)
        if not ok:
            for k in t:
                t[k].append(None)
            continue
        R = sum(x["R"] for x in w); Vv = sum(x["V"] for x in w); Ss = sum(x["S"] for x in w); Ff = sum(x["F"] for x in w)
        cmA, cmB = (R - Vv) / R, (R - Vv - semi * Ss) / R
        fA, fB = Ff + Ss, Ff + semi * Ss
        bA = fA / cmA if cmA > 0 else None
        bB = fB / cmB if cmB > 0 else None
        t["cmA"].append(cmA * 100); t["cmB"].append(cmB * 100)
        t["bepA"].append(None if bA is None else bA / 1e8); t["bepB"].append(None if bB is None else bB / 1e8)
        t["R"].append(R / 1e8); t["fixB"].append(fB / 1e8); t["varB"].append((Vv + semi * Ss) / 1e8)
        t["mosB"].append(None if bB is None else (R - bB) / R * 100)
    out["ttm"] = t
    # 최근 4분기 계정 매핑 표
    last = rows[-4:]
    agg = {}
    for r in last:
        for raw, amt in r["acc"].items():
            std, cls = costmap.classify(raw, overrides)
            e = agg.setdefault(std, {"std": std, "cls": cls, "amt": 0.0, "raws": []})
            e["amt"] += amt
            if raw not in e["raws"]:
                e["raws"].append(raw)
    tc = sum(e["amt"] for e in agg.values())
    out["map"] = sorted(({"std": e["std"], "cls": e["cls"], "amt": e["amt"] / 1e8, "share": e["amt"] / tc * 100 if tc else None, "raws": e["raws"]} for e in agg.values()), key=lambda x: -abs(x["amt"]))
    out["latest"] = rows[-1]["k"]
    out["unclassified"] = sum(e["amt"] for e in agg.values() if e["std"] == costmap.DEFAULT[0]) / tc * 100 if tc else None
    return out
