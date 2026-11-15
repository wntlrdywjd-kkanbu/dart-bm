#!/usr/bin/env python3
"""신규상장 종목용 최소 bm_card.md 생성(투자설명서 기반). 사용: python tools/prospectus_card.py 0162D0"""
import json, os, sys
code = sys.argv[1]
base = os.path.join("out", code)
d = json.load(open(os.path.join(base, "fin.json"), encoding="utf-8")); f = d["fin"]; m = d["meta"]
it = f["items"]; E = lambda v: "-" if v is None else f"{v/1e8:,.0f}"
y = f["year"]
def row(nm):
    v = it[nm]; return f"| {nm} | {E(v['전전기'])} | {E(v['전기'])} | {E(v['당기'])} |"
pr = f["periods_raw"]
s1 = {k: (pr["2026.06"][k]["당기"], pr["2025.06"][k]["당기"]) for k in ("매출액", "영업이익", "당기순이익")}
seg = f["segments"][-1]["items"]; tot = sum(seg.values())
out = [f"# {m['corp_name']} ({m['stock_code']}) BM 카드 (신규상장 — 정기보고서 없음, 투자설명서 기반)",
       "", f"출처: {m['report_nm']} (접수번호 {m['rcept_no']}). 별도재무제표 기준. 2023년 손익은 K-GAAP, 2024년 이후 K-IFRS(2023 BS는 K-IFRS 환산).", "",
       f"## 0. 재무 요약 (별도, 단위 억원, FY{y-2}~FY{y})", "", f"| 항목 | FY{y-2} | FY{y-1} | FY{y} |", "|---|---|---|---|"]
out += [row(n) for n in ("매출액", "영업이익", "당기순이익", "자산총계", "부채총계", "자본총계")]
out += ["", f"반기(2026.06, 검토): 매출 {E(s1['매출액'][0])}억(전년 반기 {E(s1['매출액'][1])}억), 영업이익 {E(s1['영업이익'][0])}억(전년 반기 {E(s1['영업이익'][1])}억), 순이익 {E(s1['당기순이익'][0])}억(전년 반기 {E(s1['당기순이익'][1])}억).", "",
        "## 1. 한 줄 사업 정의 (투자설명서 사업의 개요, 기업 서술)", "",
        "반도체 및 첨단 산업용 장비에 적용되는 고부가가치 정밀부품·모듈(Assembly)을 고객 맞춤형(Custom-made)으로 제조·공급하는 B2B 장비부품 제조 기업으로, 식각·증착·세정·이온주입 장비용 샤워헤드·챔버부품·히터·스핀척 등을 램리서치·세메스·엑셀리스 등에 납품한다.", "",
        "## 2. 매출 구성 (투자설명서 표, 2026 반기 누적, 단위 억원)", ""]
out += [f"- {k}: {v/1e8:,.0f} ({v/tot*100:.1f}%)" for k, v in sorted(seg.items(), key=lambda kv: -kv[1])]
open(os.path.join(base, "bm_card.md"), "w", encoding="utf-8").write("\n".join(out) + "\n")
print("bm_card.md", len("\n".join(out)))
