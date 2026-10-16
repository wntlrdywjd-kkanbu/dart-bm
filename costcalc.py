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
    quarterly = any("3개월" in r for r in rows[:3])
    q, cum, total = {}, {}, None
    for r in rows:
        cells = [x.strip() for x in r.strip().strip("|").split("|")]
        if len(cells) < 2:
            continue
        name = cells[0]
        vals = [_n(c) for c in cells[1:]]
        if not name or vals[0] is None or name in ("구분", "구 분"):
            continue
        nn = re.sub(r"\s", "", name)
        is_tot = nn.startswith(("합계", "계")) or nn.endswith("합계")
        if quarterly:
            if len(vals) < 2 or vals[1] is None:
                continue
            a, b = vals[0] * mult, vals[1] * mult
        else:
            a = b = vals[0] * mult
        if is_tot:
            total = (a, b)
            break  # 첫 합계에서 종료(연결 표 하나만)
        q[name] = q.get(name, 0.0) + a
        cum[name] = cum.get(name, 0.0) + b
    if total is None or len(q) < 4:
        return None
    return {"q": q, "cum": cum, "total": total, "quarterly": quarterly}


def load(cache_dir="cache/cost", files=None):
    """보고서별 파싱 결과 {key(YYYY.MM): {...}}"""
    out = {}
    for fp in (glob.glob(os.path.join(cache_dir, "*.json")) if not files else [os.path.join(cache_dir, f) for f in files if os.path.exists(os.path.join(cache_dir, f))]):
        d = json.load(open(fp, encoding="utf-8"))
        for t in d["tables"]:
            if not any("성격별" in c for c in t.get("ctx", [])[-2:]):
                continue
            p = parse_table(t)
            if p:
                # 같은 기간의 정정 보고서가 있으면 접수번호가 큰 쪽(나중 것)이 덮어쓴다
                prev = out.get(d["key"])
                if prev is None or os.path.basename(fp) > prev["_f"]:
                    p["_f"] = os.path.basename(fp)
                    p["kind"] = d["kind"]
                    out[d["key"]] = p
                break
    return out


def quarters(rep):
    """보고서 dict → 분기별 {YYYY.MM: {계정: 금액}} (3개월 값, 4Q = 연간 − 3Q 누적)"""
    res = {}
    for k in sorted(rep):
        y, m = int(k[:4]), int(k[5:])
        r = rep[k]
        if m == 12:
            prev = rep.get(f"{y}.09")
            if prev and prev["quarterly"]:
                names = set(r["cum"]) | set(prev["cum"])
                res[k] = {n: r["cum"].get(n, 0.0) - prev["cum"].get(n, 0.0) for n in names}
        elif r["quarterly"]:
            res[k] = dict(r["q"])
            if m == 6 and f"{y}.03" not in rep:  # 1Q 보고서에 주석이 없으면 반기 누적 − 2Q(3개월)
                res[f"{y}.03"] = {n: r["cum"].get(n, 0.0) - r["q"].get(n, 0.0) for n in set(r["cum"]) | set(r["q"])}
        elif m == 3:
            res[k] = dict(r["cum"])  # 1Q 는 3개월=누적(단일 열 표)
    return res


def label(k):
    return f"{k[2:4]}.{int(k[5:]) // 3}Q"


def build(rep, q20, overrides=None, semi=0.5):
    """rep: load() 결과. q20: 대시보드 20분기 시계열(원 단위, labels/매출액/영업이익). 반환: 대시보드용 dict 또는 None"""
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
