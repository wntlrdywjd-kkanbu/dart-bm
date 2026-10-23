"""분석 결과 분량·구성 최소 기준 점검. 사용: python tools/check_output.py {code} [--private ../dart-bm-private]
기준: 작업절차.md 2-3 "리포트 상세 고정 최소 기준" — points 개수는 원문 쪽수에 비례(3쪽 이하 5~6개, 4쪽 이상 8~10개), 글자 수 기준은 쓰지 않는다. 미달이면 DB 반영 전에 보강한다."""
import datetime, glob, json, os, re, sys
code = sys.argv[1]
priv = "../dart-bm-private"
if "--private" in sys.argv:
    priv = sys.argv[sys.argv.index("--private") + 1]
bad, info = [], []
def chk(ok, msg):
    (info if ok else bad).append(msg)
# ---------- notes.json ----------
nf = f"out/{code}/notes.json"
n = json.load(open(nf, encoding="utf-8")) if os.path.exists(nf) else {}
g = n.get("glance") or {}
chk(len(g.get("headline", "")) >= 120, f"glance.headline {len(g.get('headline',''))}자(>=120)")
chk(len(g.get("points", [])) >= 9, f"glance.points {len(g.get('points',[]))}개(>=9)")
chk(len(n.get("overview", [])) >= 5, f"overview {len(n.get('overview',[]))}문단(>=5)")
hide = set(n.get("hide") or [])
_dn = (json.load(open(f"out/{code}/dash.json", encoding="utf-8")) if os.path.exists(f"out/{code}/dash.json") else {}).get("notes")
chk(_dn == n, "dash.json 의 notes 가 notes.json 과 일치(불일치면 build_dash_data 재실행 — 사업서술·요약 누락 방지)")
need = {"sales": 3, "cost": 3, "cm": 3, "bs": 2, "cf": 2, "div": 1, "ins": 2, "capex": 2, "region": 2, "seg": 2, "orders": 2, "ctr": 2}
dash = json.load(open(f"out/{code}/dash.json", encoding="utf-8")) if os.path.exists(f"out/{code}/dash.json") else {}
have = {"sales": 1, "cost": "cost" in dash, "cm": "cost" in dash, "bs": 1, "cf": 1, "div": "div" in dash, "ins": "ins" in dash,
        "capex": "capex" in dash, "region": "region" in dash, "seg": "seg" in dash, "orders": bool((dash.get("orders") or {}).get("tables") or dash.get("orders", {}).get("series")), "ctr": "ctr" in dash}
for k, mn in need.items():
    if k in hide or not have.get(k):
        continue
    t = (n.get("texts") or {}).get(k) or []
    chk(len(t) >= mn, f"texts.{k} {len(t)}개(>={mn})")
# ---------- view/{code}.json ----------
vf = f"{priv}/view/{code}.json"
if not os.path.exists(vf):
    bad.append("view json 없음(증권사 리포트 상세 미작성)")
else:
    v = json.load(open(vf, encoding="utf-8"))
    for r in v.get("reports", []):
        tag = f"{r['date']} {r['broker']}"
        pages = 0
        d_ = r['date'].replace('-', '')
        # 같은 날짜에 증권사 리포트가 둘 이상일 수 있어 증권사명까지 맞추고, 없으면 날짜만으로 찾는다
        cands = sorted(glob.glob(f"{priv}/research/{code}/{d_}_{r['broker']}*")) or sorted(glob.glob(f"{priv}/research/{code}/{d_}_*"))
        for f in cands:
            t = open(f, encoding="utf-8").read().split("--- PDF 본문 ---")[-1]
            pages = t.rstrip().count("\f") + 1  # pdftotext 는 쪽마다 form feed
            break
        lo, hi = (5, 6) if 0 < pages <= 3 else (8, 10)
        n_ = len(r.get("points", []))
        chk(lo <= n_ <= hi, f"[{tag}] points {n_}개 (원문 {pages}쪽 → {lo}~{hi}개)")
        thin = any("없음" in (r.get("watch", "") + r.get("vs", "")) for _ in [0])
        chk(len(r.get("est", [])) >= 1 or thin, f"[{tag}] est {len(r.get('est',[]))}개(원문에 추정이 없으면 watch/vs 에 '없음' 사유)")
        chk(len(r.get("vs", "")) >= 60 and len(r.get("watch", "")) >= 30, f"[{tag}] vs {len(r.get('vs',''))}자·watch {len(r.get('watch',''))}자")
    chk(len(v.get("caveat", "")) >= 300, f"caveat {len(v.get('caveat',''))}자(>=300)")
    chk(len(v.get("tone_flow", [])) >= len(v.get("reports", [])) - 1, f"tone_flow {len(v.get('tone_flow',[]))}개")
    chk(len(v.get("forecast_check", [])) >= 3, f"forecast_check {len(v.get('forecast_check',[]))}개(>=3)")
    chk(len(v.get("op26", [])) >= 1, f"op26 {len(v.get('op26',[]))}개(>=1)")
print("\n".join("OK   " + m for m in info))
print("\n".join("FAIL " + m for m in bad))
print("결과:", "통과" if not bad else f"미달 {len(bad)}건 — 보강 후 DB 반영")
sys.exit(1 if bad else 0)
