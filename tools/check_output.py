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
_bb = (n.get("bm") or {}).get("blocks") or []
# 해자 균형 규칙 유예(기존 분석). 해당 종목을 다음에 업데이트할 때 이 목록에서 제거하고 균형 형식으로 재작성
BALANCE_EXEMPT = {'000990', '0088M0', '014680', '028670', '031210', '031980', '036930', '058610', '059090', '068270', '073490', '078600', '085910', '108490', '121600', '123330', '125490', '128940', '131970', '200470', '218410', '232140', '281820', '319660', '326030', '353200', '356860', '420770', '425420', '452430', '456010', '468530', '481070'}
if _bb:  # bm 통합 섹션이 있으면 개요를 대체: 소제목 5+, 문단 6+, 표·도식 합 3+, 쟁점 3+
    chk(sum(b.get("t") == "h" for b in _bb) >= 5, f"bm 소제목 {sum(b.get('t')=='h' for b in _bb)}개(>=5)")
    chk(sum(b.get("t") in ("p", "ul") for b in _bb) >= 6, f"bm 문단·목록 {sum(b.get('t') in ('p','ul') for b in _bb)}개(>=6)")
    # 고정 뼈대 소제목(작업절차 2-2-1): 신규·갱신 분석은 아래 소제목을 모두 포함해야 한다. 옛 양식 종목은 LEGACY 로 면제(재작성 시 목록에서 뺀다).
    LEGACY = {"004490","005090","005290","006400","006730","020150","028050","041830","043260","062040","093520","214450","240810","317400","383310","393890","394800","489790"}
    _hs = [b.get("x", "") for b in _bb if b.get("t") == "h"]
    for _r in ["한 줄로", "무엇을 파나", "누구에게 파나", "어떻게 버나", "경쟁사와 위치", "해자는 있나"]:
        _ok = any(_r in h for h in _hs)
        if code in LEGACY and not _ok:
            info.append(f"bm 고정 소제목 '{_r}' 없음(옛 양식 종목 면제)")
        else:
            chk(_ok, f"bm 고정 소제목 '{_r}' 포함")
    # 해자는 있나 균형(작업절차 2-2-2): 긍정 목록·반대 목록 각각 (정량) 2+·(정성) 2+, 두 목록 항목 수 차이 1 이하, 마지막 항목은 '입증되지 않은 부분'
    _hi = [k for k, b in enumerate(_bb) if b.get("t") == "h" and "해자는 있나" in b.get("x", "")]
    if _hi and code not in LEGACY and code not in BALANCE_EXEMPT:
        _tail = _bb[_hi[0] + 1:]
        _uls = [b.get("items") or [] for b in _tail if b.get("t") == "ul"]
        _pr = [b.get("x", "") for b in _tail if b.get("t") == "p"]
        chk(len(_uls) >= 3 and len(_pr) >= 2, f"해자 섹션: 긍정 목록·반대 목록·입증 안 된 부분 목록 + 라벨 문단 2개(목록 {len(_uls)}개, 문단 {len(_pr)}개)")
        if len(_uls) >= 3:
            _pos, _neg, _last = _uls[0], _uls[1], _uls[-1]
            chk(any("긍정" in x for x in _pr) and any("비판" in x or "반대" in x for x in _pr), "해자 섹션: '긍정적 검토'·'비판적 검토' 라벨 문단")
            for _nm, _ls in (("긍정", _pos), ("반대", _neg)):
                _q = sum("(정량)" in x for x in _ls); _l = sum("(정성)" in x for x in _ls)
                chk(_q >= 2 and _l >= 2, f"해자 {_nm} 목록 (정량) {_q}개·(정성) {_l}개(각 >=2)")
            chk(abs(len(_pos) - len(_neg)) <= 1, f"해자 긍정 {len(_pos)}개 vs 반대 {len(_neg)}개(차이 <=1)")
            chk(bool(_last) and _last[-1].lstrip("*").startswith("입증되지 않은 부분"), "해자 섹션 마지막 항목이 '입증되지 않은 부분'")
    chk(sum(b.get("t") in ("table", "flow") for b in _bb) >= 3, f"bm 표·도식 {sum(b.get('t') in ('table','flow') for b in _bb)}개(>=3)")
    chk(len(n.get("issues") or []) >= 3, f"issues {len(n.get('issues') or [])}개(>=3)")
else:
    chk(len(n.get("overview", [])) >= 5, f"overview {len(n.get('overview',[]))}문단(>=5)")
hide = set(n.get("hide") or [])
_dn = (json.load(open(f"out/{code}/dash.json", encoding="utf-8")) if os.path.exists(f"out/{code}/dash.json") else {}).get("notes")
chk(_dn == n, "dash.json 의 notes 가 notes.json 과 일치(불일치면 build_dash_data 재실행 — 사업서술·요약 누락 방지)")
need = {"sales": 3, "cost": 3, "cm": 3, "bs": 2, "cf": 2, "div": 1, "ins": 2, "capex": 2, "region": 2, "seg": 2, "orders": 2, "ctr": 2, "invrev": 2}
dash = json.load(open(f"out/{code}/dash.json", encoding="utf-8")) if os.path.exists(f"out/{code}/dash.json") else {}
have = {"sales": 1, "cost": "cost" in dash, "cm": "cost" in dash, "bs": 1, "cf": 1, "div": "div" in dash, "ins": "ins" in dash,
        "capex": "capex" in dash, "region": "region" in dash, "seg": "seg" in dash, "orders": bool((dash.get("orders") or {}).get("tables") or dash.get("orders", {}).get("series")), "ctr": "ctr" in dash, "invrev": "invrev" in dash}
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
        thin_src = False
        d_ = r['date'].replace('-', '')
        # 같은 날짜에 증권사 리포트가 둘 이상일 수 있어 증권사명까지 맞추고, 없으면 날짜만으로 찾는다
        cands = sorted(glob.glob(f"{priv}/research/{code}/{d_}_{r['broker']}*")) or sorted(glob.glob(f"{priv}/research/{code}/{d_}_*"))
        for f in cands:
            t = open(f, encoding="utf-8").read().split("--- PDF 본문 ---")[-1]
            pages = t.rstrip().count("\f") + 1  # pdftotext 는 쪽마다 form feed
            thin_src = len(t.strip()) < 200 * pages  # 이미지형·껍데기 PDF(쪽당 글자 200자 미만): 작업절차 규칙 6, 지어내지 않는다
            break
        lo, hi = (5, 6) if 0 < pages <= 3 else (8, 10)
        if thin_src:
            lo = 1
        n_ = len(r.get("points", []))
        chk(lo <= n_ <= hi, f"[{tag}] points {n_}개 (원문 {pages}쪽 → {lo}~{hi}개)")
        thin = any("없음" in (r.get("watch", "") + r.get("vs", "")) for _ in [0])
        chk(len(r.get("est", [])) >= 1 or thin, f"[{tag}] est {len(r.get('est',[]))}개(원문에 추정이 없으면 watch/vs 에 '없음' 사유)")
        chk(len(r.get("vs", "")) >= 60 and len(r.get("watch", "")) >= 30, f"[{tag}] vs {len(r.get('vs',''))}자·watch {len(r.get('watch',''))}자")
    chk(len(v.get("caveat", "")) >= 300, f"caveat {len(v.get('caveat',''))}자(>=300)")
    chk(len(v.get("tone_flow", [])) >= len(v.get("reports", [])) - 1, f"tone_flow {len(v.get('tone_flow',[]))}개")
    chk(len(v.get("forecast_check", [])) >= 3, f"forecast_check {len(v.get('forecast_check',[]))}개(>=3)")
    _noest = all(r.get("tp") is None for r in v.get("reports", [])) and "op26" in v.get("caveat", "")
    chk(len(v.get("op26", [])) >= 1 or _noest, f"op26 {len(v.get('op26',[]))}개(>=1, 또는 추정치·목표가 전무+caveat 에 op26 사유)")
# 증권사 추정 YoY 카드(fc.json, 2026-11-11): view 에 est 가 있으면 fc.json 필수 + 단위 점검
try:
    _v = v
except NameError:
    _v = None
if _v and any(r.get("est") and not r.get("old") for r in _v.get("reports", [])):
    _fp = f"{base}/fc.json" if "base" in globals() else f"out/{code}/fc.json"
    if not os.path.exists(_fp):
        chk(False, f"fc.json 없음 — python tools/fc_from_view.py {code} 로 초안 생성·확인 후 build_dash_data 재실행(증권사추정YoY_절차.md)")
    else:
        _fc = json.load(open(_fp, encoding="utf-8"))
        _d = json.load(open(f"out/{code}/dash.json", encoding="utf-8"))
        chk(bool(_d.get("fc")), "dash.json 에 fc 포함(없으면 fc.json 저장 후 build_dash_data 재실행)")
        _a = _d["annual"]; _ar = (_a["매출액"][-1] or 0) / 1e8
        _bad = [f"{e['b']} {e['y']} 매출 {e.get('rev')}" for e in _fc["est"] if not e.get("old") and e.get("rev") and _ar and not (0.2 <= e["rev"] / _ar <= 8)]
        chk(not _bad, "fc 단위 점검(최근 연간 매출 대비 0.2~8배): " + (", ".join(_bad) if _bad else f"이상 없음({len([e for e in _fc['est'] if not e.get('old')])}건)"))
print("\n".join("OK   " + m for m in info))
print("\n".join("FAIL " + m for m in bad))
print("결과:", "통과" if not bad else f"미달 {len(bad)}건 — 보강 후 DB 반영")
sys.exit(1 if bad else 0)
