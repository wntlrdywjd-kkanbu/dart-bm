# -*- coding: utf-8 -*-
"""view/{code}.json 의 est 표에서 연간 추정(매출·영업이익)을 뽑아 out/{code}/fc.json 초안을 만든다.
사용: python tools/fc_from_view.py CODE  → 결과를 눈으로 확인하고 필요하면 손으로 고친다(자동 추출은 초안).
fc.json: {"asof":..., "est":[{"b","d","y","rev","op","ni","old"}]}  단위 억원. old=true 는 통계에서 제외."""
import json, os, re, sys, datetime
CODE = sys.argv[1]
PRIV = os.environ.get("PRIV", "/home/claude/dart-bm-private")
v = json.load(open(f"{PRIV}/view/{CODE}.json", encoding="utf-8"))
def mult(unit):
    u = unit or ""
    if "조" in u: return 1e4
    if "십억" in u: return 10.0
    if "백만" in u: return 0.01
    if "억" in u: return 1.0
    return None
def num(x):
    x = str(x).replace(",", "").replace("△", "-").strip()
    try: return float(x)
    except ValueError: return None
out = []
for r in v["reports"]:
    got = {}
    for e in r.get("est") or []:
        if "cols" not in e: continue
        m = mult(e.get("unit"))
        if m is None: continue
        for row in e["rows"]:
            lab = row[0].strip()
            key = "rev" if lab in ("매출액", "매출", "영업수익") else "op" if lab == "영업이익" else "ni" if lab in ("순이익", "지배순이익", "지배주주순이익", "당기순이익") else None
            if not key: continue
            for c, val in zip(e["cols"], row[1:]):
                mm = re.fullmatch(r"(20\d\d)[EFP]", c.strip())
                if not mm: continue
                x = num(val)
                if x is None: continue
                got.setdefault(int(mm.group(1)), {})[key] = round(x * m, 1)
    for y, d in sorted(got.items()):
        if "rev" in d or "op" in d:
            out.append({"b": r["broker"], "d": r["date"], "y": y, **d, **({"old": True} if r.get("old") else {})})
json.dump({"asof": str(datetime.date.today()), "est": out}, open(f"out/{CODE}/fc.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
for o in out: print(o)
