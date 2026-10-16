"""out/{code}/ 결과물 → 대시보드용 압축 JSON(out/{code}/dash.json, 256KiB 미만). 사용법: python build_dash_data.py 085910 [...]"""
import json, os, re, sys, glob

OUT = "out"
SECS = {"s2": r"## 2\. ", "s21": r"## 2-1\. ", "s3": r"## 3\. ", "s4": r"## 4\. ", "s5": r"## 5\. ",
        "s52": r"## 5-2\. ", "s6": r"## 6\. ", "s62": r"## 6-2\. "}


def sections(card):
    parts = re.split(r"(?m)^(?=## )", card)
    out = {}
    for p in parts:
        for k, pat in SECS.items():
            if re.match(pat, p):
                out[k] = p.split("\n", 1)[1].strip() if "\n" in p else ""
    return out


def series(q, key):
    return [x["values"].get(key) for x in q]


def nseg(n):
    """부문명 정규화: '&cr'·공백 제거, 부분→부문"""
    return re.sub(r"\s+", "", (n or "").replace("&cr", "")).replace("부분", "부문")


def ttm(vals, i):
    w = vals[i - 3:i + 1] if i >= 3 else []
    return sum(w) if len(w) == 4 and all(v is not None for v in w) else None


def cfrow(rows, pat):
    for nm, v in rows or []:
        if re.search(pat, re.sub(r"\s+", "", nm or "")) and v is not None:
            return v
    return None


def cfw(items):
    """현금흐름 브리지·순이익→영업현금 워터폴(원 단위). 부호는 보고서 값 그대로, 이자·세금·기타는 잔차."""
    rows = items.get("_cf") or []
    g = lambda k: (items.get(k) or {}).get("당기")
    ni = cfrow(rows, r"^(연결)?당기순(이익|손익|손실)(\(손실\))?$")
    adj = cfrow(rows, r"조정을위한가감|^조정$|^조정항목")
    wc = cfrow(rows, r"자산부채의변동|자산및부채의변동")
    ocf, icf, fcf = g("영업활동현금흐름"), g("투자활동현금흐름"), g("재무활동현금흐름")
    out = {"기초현금": g("기초현금"), "기말현금": g("기말현금"), "영업": ocf, "투자": icf, "재무": fcf,
           "환율": cfrow(rows, r"^현금및현금성자산에대한환율변동효과")}
    if None not in (ni, adj, wc, ocf):
        out["워터폴"] = {"순이익": ni, "조정": adj, "운전자본": wc, "이자·세금·기타": ocf - ni - adj - wc, "영업현금": ocf}
    return out


def extra(base, meta, fin):
    """1단계 확장: 20분기 시계열, BS 시계열, 현금흐름, 배당, 부문 비중, 시세·밸류에이션"""
    ex = {}
    qs = list(reversed(fin.get("quarters") or []))
    keys = ["매출액", "영업이익", "당기순이익", "매출원가", "판관비", "매출총이익", "영업활동현금흐름", "투자활동현금흐름", "재무활동현금흐름"]
    q20 = {"labels": [x["label"] for x in qs], "keys": [x["key"] for x in qs], "check": [x["label"] for x in qs if x.get("check")]}
    for k in keys:
        q20[k] = series(qs, k)
    for k in ("매출액", "영업이익", "당기순이익", "매출총이익"):
        q20[k + "_ttm"] = [ttm(q20[k], i) for i in range(len(qs))]
    ex["q20"] = q20
    pr = fin.get("periods_raw") or {}
    pk = sorted(pr)[-12:]
    bs = {"labels": [f"{k[2:4]}.{k[5:]}" for k in pk], "keys": pk}
    for k in ["자산총계", "부채총계", "자본총계", "유동자산", "비유동자산", "유동부채", "비유동부채", "이익잉여금", "자본금", "현금및현금성자산", "재고자산", "매출채권"]:
        bs[k] = [((pr[p].get(k) or {}).get("당기")) for p in pk]
    ex["bs"] = bs
    akeys = [k for k in sorted(pr) if k.endswith(".12")]
    cfd = {}
    if akeys:
        cfd["연간"] = {"label": "FY" + akeys[-1][:4], **cfw(pr[akeys[-1]])}
    lk = max(pr) if pr else None
    if lk and lk not in akeys:
        cfd["최신"] = {"label": lk[2:4] + "." + lk[5:] + " 누적", **cfw(pr[lk])}
    ex["cf"] = cfd
    if fin.get("dividend"):
        ex["div"] = fin["dividend"]
    segs = [{**s_, "items": {n: v for n, v in s_["items"].items() if v is not None and v >= 0}} for s_ in (fin.get("segments") or [])]  # 내부거래(-) 제외
    if segs:
        names = {}
        for s_ in segs:
            for n, v in s_["items"].items():
                names[nseg(n)] = n
        latest = {nseg(n): v for n, v in segs[-1]["items"].items()}
        order = sorted(names, key=lambda n: -(latest.get(n) or 0))
        ex["seg"] = {"keys": [s_["key"] for s_ in segs], "kinds": [s_["kind"] for s_ in segs],
                     "names": [re.sub(r"\s+", " ", names[n].replace("&cr", "")).strip() for n in order],
                     "values": [[{nseg(n): v for n, v in s_["items"].items()}.get(n) for s_ in segs] for n in order]}
    ins = fin.get("insider") or []
    if ins:
        flat = []
        for r in ins:
            if not r.get("chg"):
                continue
            ts = r.get("trades") or []
            if ts:
                for t in ts:
                    flat.append([r["dt"], r["who"], r["pos"], t["reason"], t["date"], t["price"], (t["price"] * t["chg"]) if t.get("price") else None, t["before"], t["chg"], t["after"]])
            else:
                flat.append([r["dt"], r["who"], r["pos"], "", r["dt"], None, None, None, r["chg"], r["own"]])
        flat.sort(key=lambda x: (x[4], x[0]), reverse=True)
        today = (meta.get("generated_at") or "")[:7]
        yy, mm = int(today[:4]), int(today[5:7])
        months = []
        for _ in range(24):
            months.append(f"{yy}{mm:02d}")
            mm -= 1
            if mm == 0:
                yy, mm = yy - 1, 12
        months.reverse()
        net = {k: 0.0 for k in months}
        mk = lambda x: (not x[3]) or any(w in x[3] for w in ("장내", "시간외", "장외"))
        for x in flat:
            k = x[4].replace("-", "")[:6]
            if k in net and mk(x):
                net[k] += x[8]
        l12all = [x for x in flat if x[4].replace("-", "")[:6] in months[-12:]]
        l12 = [x for x in l12all if mk(x)]
        ex["ins"] = {"rows": flat[:40], "labels": [k[2:4] + "." + k[4:] for k in months], "v": [net[k] for k in months],
                     "net12": sum(x[8] for x in l12), "amt12": sum(x[6] or 0 for x in l12),
                     "n_up": sum(1 for x in l12 if x[8] > 0), "n_dn": sum(1 for x in l12 if x[8] < 0), "n_etc": len(l12all) - len(l12)}
    ctr = fin.get("contracts") or []
    if ctr:
        def qk(r):
            d_ = re.sub(r"\D", "", r.get("date") or r["dt"])[:8] or r["dt"]
            return f"{d_[2:4]}.{(int(d_[4:6]) - 1) // 3 + 1}Q"
        qs_ = []
        yq = [int(meta["generated_at"][:4]), (int(meta["generated_at"][5:7]) - 1) // 3 + 1]
        for _ in range(12):
            qs_.append(f"{yq[0] % 100}.{yq[1]}Q")
            yq = [yq[0] - 1, 4] if yq[1] == 1 else [yq[0], yq[1] - 1]
        qs_.reverse()
        qv = {k: 0.0 for k in qs_}
        for r in ctr:
            if r["kind"] == "정정" or qk(r) not in qv:
                continue
            qv[qk(r)] += -r["amt"] if r["kind"] == "해지" else r["amt"]
        ex["ctr"] = {"rows": [[r["dt"], r["kind"], r.get("party", ""), r["amt"], r.get("pct"), r.get("start", ""), r.get("end", "")] for r in ctr[:30]],
                     "labels": qs_, "v": [qv[k] for k in qs_]}
    oh = fin.get("orders_hist") or []
    if len(oh) >= 3:
        units = [o["unit"] for o in oh if o.get("unit")]
        un = max(set(units), key=units.count) if units else ""
        mm = re.search(r"([\d,]+)", un)
        us = "US" in un
        mult = float(mm.group(1).replace(",", "")) if mm else (1e8 if "억" in un else 1e6 if "백만" in un else 1e3 if "천" in un else 1.0)
        div = 1e6 if us else 1e8
        uname = "US$ 백만" if us else "억원"
        qkeys = sorted({f"{o['key'][:4]}.{int(o['key'][5:]):02d}" for o in oh})
        by = {o["key"]: o for o in oh}
        ys = int(qkeys[0][:4])
        ye = int(qkeys[-1][:4])
        labels, bl, dl, nw = [], [], [], []
        prevb = None
        for y in range(ys, ye + 1):
            for m in (3, 6, 9, 12):
                k = f"{y}.{m:02d}"
                if k < qkeys[0] or k > qkeys[-1]:
                    continue
                o = by.get(k)
                labels.append(f"{str(y)[2:]}.{m}")
                b = o["backlog"] * mult / div if o else None
                q = None
                if o and o.get("deliv") is not None:
                    pk_ = by.get(f"{y}.{m-3:02d}") if m > 3 else None
                    q = (o["deliv"] - (pk_["deliv"] if pk_ else 0)) * mult / div if (m == 3 or pk_) else None
                if q is not None and q < 0:
                    q = None  # 집계 기간이 달라 누적이 역전된 보고서
                n_ = (q + b - prevb) if (q is not None and b is not None and prevb is not None) else None
                if n_ is not None and n_ < 0:
                    n_ = None
                bl.append(b); dl.append(q); nw.append(n_)
                prevb = b
        ex["ord"] = {"unit": uname, "labels": labels, "backlog": bl, "deliv": dl, "new": nw,
                     "items": {k: v for k, v in (oh[-1].get("items") or {}).items() if v}, "latest": oh[-1]["key"]}
    ch = fin.get("capa_hist") or []
    if len(ch) >= 3:
        def gname(n):
            n = re.sub(r"&cr|\s+", "", n)
            n = re.sub(r"부문t$", "부문", n)
            return n.replace("부분", "부문")
        byk = {c["key"]: {gname(g): v for g, v in c["groups"].items() if gname(g) != "합계"} for c in ch}
        ks = sorted(byk)
        qlab = []
        for k in ks:
            y, m = int(k[:4]), int(k[5:])
            qlab.append((k, f"{str(y)[2:]}.{m // 3}Q"))
        names = []
        for k in ks:
            for g in byk[k]:
                if g not in names:
                    names.append(g)
        gs = []
        for g in names:
            cap, prd, utl, unit = [], [], [], ""
            for k, lb in qlab:
                y, m = int(k[:4]), int(k[5:])
                cur = byk[k].get(g)
                pk_ = byk.get(f"{y}.{m-3:02d}") if m > 3 else None
                pv = pk_.get(g) if pk_ else None
                if not cur:
                    cap.append(None); prd.append(None); utl.append(None)
                    continue
                if m > 3 and not pv:
                    cap.append(None); prd.append(None); utl.append(cur.get("util"))
                    continue
                unit = unit or cur.get("unit", "")
                c1 = cur.get("capa"); p1 = cur.get("prod")
                c0 = (pv or {}).get("capa", 0) if pv else 0
                p0 = (pv or {}).get("prod", 0) if pv else 0
                cq = None if c1 is None or (pv and pv.get("capa") is None) else c1 - c0
                pq = None if p1 is None else p1 - p0
                cap.append(cq); prd.append(pq)
                utl.append(cur.get("util"))  # 보고서가 공시한 연초 누적 가동률 (분기 차이로 다시 계산하면 가동일 기준 차이로 튄다)
            if sum(1 for u in utl if u is not None) >= 3:
                gs.append({"name": g, "unit": unit, "capa": cap, "prod": prd, "util": utl})
        if gs:
            ex["capa"] = {"labels": [l for _, l in qlab], "groups": gs}
    cf_ = fin.get("cost_files")
    try:
        import costcalc
        rep = costcalc.load(os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache", "cost"), cf_ or None)
        nt_ = {}
        try:
            nt_ = json.load(open(os.path.join(base, "notes.json"), encoding="utf-8"))
            nt_ = nt_ if isinstance(nt_, dict) else {}
        except (OSError, ValueError):
            pass
        cc = costcalc.build(rep, q20, nt_.get("cost_map"), nt_.get("semi_var_ratio", 0.5)) if rep else None
        if cc:
            ex["cost"] = cc
    except Exception as e:
        print("[경고] 비용 구조 계산 실패:", e)
    rh = fin.get("region_hist") or []
    if len(rh) >= 4:
        rb = {o["key"]: o for o in rh}
        rl, rd, re_ = [], [], []
        for k_ in sorted(rb):
            y_, m_ = int(k_[:4]), int(k_[5:])
            if m_ == 3:
                a_, b_ = rb[k_]["dom"], rb[k_]["exp"]
            else:
                pv_ = rb.get(f"{y_}.{m_-3:02d}")
                if not pv_:
                    continue
                a_, b_ = rb[k_]["dom"] - pv_["dom"], rb[k_]["exp"] - pv_["exp"]
            if a_ < 0 or b_ < 0:
                continue
            rl.append(f"{str(y_)[2:]}.{m_ // 3}Q"); rd.append(a_ / 1e8); re_.append(b_ / 1e8)
        if len(rl) >= 4:
            ex["region"] = {"labels": rl, "dom": rd, "exp": re_}
    # capex (현금흐름표 취득액, 누적 → 분기) + 감가상각 대비, 시설투자 공시, 설비 계획·증가 표
    try:
        pr_ = fin.get("periods_raw") or {}
        cum = {}
        for k_, it_ in pr_.items():
            rows_ = it_.get("_cf") or []
            a_ = cfrow(rows_, r"^유형자산의?취득$|^유형자산취득$")
            b_ = cfrow(rows_, r"^무형자산의?취득$|^무형자산취득$")
            if a_ is not None or b_ is not None:
                cum[k_] = (abs(a_ or 0), abs(b_ or 0))
        cl, cp_, cq_ = [], [], []
        for k_ in sorted(cum):
            y_, m_ = int(k_[:4]), int(k_[5:])
            if m_ == 3:
                q_ = cum[k_]
            else:
                pk2 = cum.get(f"{y_}.{m_-3:02d}")
                if not pk2:
                    continue
                q_ = (cum[k_][0] - pk2[0], cum[k_][1] - pk2[1])
            cl.append(f"{str(y_)[2:]}.{m_ // 3}Q"); cp_.append((q_[0] + q_[1]) / 1e8); cq_.append(q_[0] / 1e8)
        capx = {"labels": cl, "capex": cp_, "ppe": cq_}
        dep_ = (ex.get("cost") or {})
        if dep_.get("dep"):
            dm = dict(zip(dep_["labels"], dep_["dep"]))
            capx["dep"] = [dm.get(l) for l in cl]
        notices = [[x["dt"], x.get("kind", ""), x.get("what", ""), x["amt"], x.get("pct"), x.get("start", ""), x.get("end", ""), x.get("why", ""), 1 if x.get("cancel") else 0]
                   for x in (fin.get("capex_notices") or []) if not x.get("fix")][:12]
        capx["notices"] = notices
        txt = ""
        try:
            lines_ = open(os.path.join(base, "biz_latest.txt"), encoding="utf-8").read().split("\n")
            for ln in lines_:
                m2 = re.search(r"설비의\s*신설[ㆍ·]?\s*매입\s*계획", ln)
                if m2:
                    txt = ln[m2.end():m2.end() + 450].strip()
                    txt = re.sub(r"\s*\d\.\s*매출.*$", "", txt)
                    txt = re.sub(r"^등\s*", "", txt)
                    break
            facs, i_ = [], 0
            while i_ < len(lines_):
                if lines_[i_].startswith("|") and "기초장부가액" in lines_[i_]:
                    j_ = i_
                    while j_ < len(lines_) and lines_[j_].startswith("|"):
                        j_ += 1
                    cap_ = ""
                    k2 = i_ - 1
                    while k2 >= 0 and not lines_[k2].strip():
                        k2 -= 1
                    cap_ = lines_[k2].strip()[:80] if k2 >= 0 and not lines_[k2].startswith("|") else (lines_[k2].strip()[:80] if k2 >= 0 else "")
                    facs.append({"caption": cap_, "rows": [r[:200] for r in lines_[i_:j_][:14]]})
                    i_ = j_
                    continue
                i_ += 1
            capx["facs"] = facs[:3]
        except OSError:
            pass
        capx["plan"] = txt
        if len(cl) >= 3 or notices or txt:
            ex["capex"] = capx
    except Exception as e:
        print("[경고] capex 계산 실패:", e)
    try:
        p = json.load(open(os.path.join(base, "price.json"), encoding="utf-8"))
    except OSError:
        p = None
    if p:
        pv = {"date": p["date"], "close": p["close"], "change_pct": p["change_pct"], "mcap": p["mcap"], "shares": p["shares"],
              "market": p["market"], "history": p["history"]}
        i = len(qs) - 1
        ni, rv = ttm(q20["당기순이익"], i), ttm(q20["매출액"], i)
        eq = bs["자본총계"][-1] if bs["자본총계"] else None
        dps = (fin.get("dividend") or {}).get("dps") or []
        pv["val"] = {"PER": p["mcap"] / ni if ni and ni > 0 else None, "PBR": p["mcap"] / eq if eq and eq > 0 else None,
                     "PSR": p["mcap"] / rv if rv else None,
                     "배당수익률": (dps[-1] / p["close"] * 100) if dps and dps[-1] and p["close"] else None,
                     "기준": "TTM(최근 4분기) 연결 당기순이익·매출, 최근 보고서 자본총계"}
        ex["price"] = pv
    return ex


def diff_md(path):
    """diff.txt 전체에서 보고서 구간별 핵심 문장을 '자르지 않고' 뽑아 대시보드용 마크다운으로(카드는 글자 수 제한으로 잘림)."""
    try:
        txt = open(path, encoding="utf-8").read()
    except OSError:
        return ""
    out = []
    for blk in re.split(r"(?m)^===== ", txt)[1:]:
        head, _, rest = blk.partition("\n")
        m = re.match(r"(.+?) \(신규 문장 (\d+)개", head)
        if not m:
            continue
        rows = [re.match(r"\[(-?\d+)\] (.+)", l) for l in rest.split("\n")]
        rows = [(int(r.group(1)), r.group(2).strip()) for r in rows if r]
        pick = [t for sc, t in rows if sc > 0][:5] or [t for _, t in rows][:2]
        out.append(f"[{m.group(1)}] 신규 문장 {m.group(2)}개 중 핵심\n" + "\n".join("- " + t for t in pick))
    return "\n".join(out)


def build(code):
    base = os.path.join(OUT, code)
    d = json.load(open(os.path.join(base, "fin.json"), encoding="utf-8"))
    meta, fin = d["meta"], d["fin"]
    card = open(os.path.join(base, "bm_card.md"), encoding="utf-8").read()
    it = fin["items"]
    y = fin["year"]

    def ann(label):
        v = it.get(label)
        return [v and v.get("전전기"), v and v.get("전기"), v and v.get("당기")] if v else [None, None, None]

    annual = {"years": [f"FY{y-2}", f"FY{y-1}", f"FY{y}"]}
    for k in ["매출액", "영업이익", "당기순이익", "매출총이익", "자산총계", "부채총계", "자본총계", "유동자산", "유동부채",
              "현금및현금성자산", "재고자산", "매출채권", "영업활동현금흐름", "투자활동현금흐름", "재무활동현금흐름"]:
        annual[k] = ann(k)
    qs = list(reversed(fin.get("quarters") or []))  # 과거 → 최신
    quarters = {"labels": [x["label"] for x in qs], "check": [x["label"] for x in qs if x.get("check")]}
    for k in ["매출액", "영업이익", "당기순이익"]:
        quarters[k] = series(qs, k)
    orders = {}
    try:
        orders = json.load(open(os.path.join(base, "orders.json"), encoding="utf-8"))
    except OSError:
        pass
    secs = sections(card)
    dm = diff_md(os.path.join(base, "diff.txt"))
    if dm:
        secs["s6"] = dm
    # 한 줄 정의
    m = re.search(r"## 1\. .*?\n(.+?)\n\n", card, re.S)
    doc = {
        "code": code, "name": meta["corp_name"], "generated_at": meta["generated_at"],
        "report": meta["report_nm"], "report_dt": meta["rcept_dt"],
        "latest": meta["latest_report_nm"], "latest_dt": meta["latest_rcept_dt"],
        "basis": fin["basis"], "one_line": m.group(1).strip() if m else "",
        "annual": annual, "quarters": quarters, "yoy_latest": fin.get("yoy_latest") or {},
        "secs": secs, "orders": orders,
    }
    try:  # Claude가 쓴 해석 메모(있으면 대시보드에 표시)
        doc["notes"] = json.load(open(os.path.join(base, "notes.json"), encoding="utf-8"))
    except OSError:
        pass
    doc.update(extra(base, meta, fin))
    s = json.dumps(doc, ensure_ascii=False)
    assert len(s.encode("utf-8")) < 240_000, f"{code}: {len(s)} bytes"
    json.dump(doc, open(os.path.join(base, "dash.json"), "w", encoding="utf-8"), ensure_ascii=False)
    return len(s.encode("utf-8"))


if __name__ == "__main__":
    codes = sys.argv[1:] or [os.path.basename(p) for p in glob.glob(os.path.join(OUT, "*")) if os.path.isdir(p)]
    for c in codes:
        print(c, build(c), "bytes")
