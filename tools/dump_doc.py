#!/usr/bin/env python3
"""DART 정기보고서 원문 전체를 텍스트로 저장(주석 전체 확인용). Actions 에서만 실행(DART_API_KEY 필요).
  python tools/dump_doc.py {code} {rcept_no} [{rcept_no} ...]  → out/{code}/docs/{rcept_no}.txt
"""
import io, os, sys, zipfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dart_bm as D

code, rcepts = sys.argv[1], sys.argv[2:]
d = os.path.join('out', code, 'docs'); os.makedirs(d, exist_ok=True)
for rc in rcepts:
    r = D.get("document.xml", rcept_no=rc)
    z = zipfile.ZipFile(io.BytesIO(r.content))
    out = []
    for n in z.namelist():
        if n.lower().endswith((".xml", ".html", ".htm")):
            out.append(f"=== {n}")
            out.extend(D.to_text(D.decode(z.read(n))))
    open(os.path.join(d, rc + '.txt'), 'w', encoding='utf-8').write("\n".join(out))
    print(rc, len(out), 'lines')
