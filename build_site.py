"""GitHub Pages용 정적 사이트 생성. 사용: python build_site.py
- view.html : dashboard/index.html 과 같은 화면(렌더 코드 공유). ?c=종목코드 로 out/{code}/dash.json 을 읽음
- index.html: out/*/dash.json 종목 목록
- 버튼: HTML로 저장(브라우저 다운로드), GPT용 복사(out/{code}/gpt_paste.md 클립보드)"""
import json, glob, os, re, html

src = open("dashboard/index.html", encoding="utf-8").read()
mark = "  // ------- 데이터 연결"
head = src[:src.index(mark)]
loader = r'''  // ------- Pages: dash.json 을 fetch 로 읽음
  window.__dl = { save: function (o) { return new Promise(function (ok) {
    var a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([o.data], { type: "text/html" }));
    a.download = o.filename; document.body.appendChild(a); a.click(); a.remove(); ok(); }); } };
  var code = new URLSearchParams(location.search).get("c");
  $("#btn-gpt").onclick = function () {
    if (!code) return;
    fetch("out/" + code + "/gpt_paste.md").then(function (r) { return r.text(); }).then(function (t) {
      return navigator.clipboard.writeText(t).then(function () { $("#btn-gpt").textContent = "복사됨 (" + t.length.toLocaleString() + "자)"; });
    }).catch(function () { $("#btn-gpt").textContent = "복사 실패 - 목록에서 gpt_paste 열기"; });
  };
  if (!code) { $("#detail").innerHTML = "<div class='card empty'>종목을 선택하세요. <a href='index.html'>목록</a></div>"; }
  else fetch("out/" + code + "/dash.json").then(function (r) { if (!r.ok) throw 0; return r.json(); })
    .then(function (d) { cur = d; render(); $("#btn-gpt").hidden = false; })
    .catch(function () { $("#detail").innerHTML = "<div class='card empty'>데이터를 읽지 못했습니다.</div>"; });
})();
</script>
'''
head = head.replace('<button id="btn-save" hidden>HTML로 저장</button>',
    '<a href="index.html" class="btn" style="align-self:center">목록</a><button id="btn-gpt" hidden>GPT용 복사</button><button id="btn-save" hidden>HTML로 저장</button>')
head = head.replace("<h1>BM 대시보드</h1>", "<h1>BM 분석</h1>", 1).replace("<title>BM 대시보드</title>", "<title>BM 분석</title>", 1)
open("view.html", "w", encoding="utf-8").write("<!doctype html><html lang='ko'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>\n" + head + loader + "</body></html>")

rows = []
for p in sorted(glob.glob("out/*/dash.json")):
    try:
        d = json.load(open(p, encoding="utf-8"))
    except Exception:
        continue
    c = d.get("code", p.split("/")[1])
    rows.append((d.get("generated_at", ""), c, d.get("name", c)))
rows.sort(reverse=True)
li = "".join(f"<tr><td><a href='view.html?c={c}'>{html.escape(n)}</a></td><td>{c}</td><td>{str(g)[:10]}</td>"
             f"<td><a href='out/{c}/bm_card.md'>카드</a> · <a href='out/{c}/gpt_paste.md'>GPT용</a></td></tr>" for g, c, n in rows)
open("index.html", "w", encoding="utf-8").write(f"""<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>BM 분석 목록</title>
<style>body{{font-family:system-ui,"Malgun Gothic",sans-serif;margin:0;padding:16px;background:#f3f5f7;color:#14202b}}
main{{max-width:760px;margin:0 auto}}table{{width:100%;border-collapse:collapse;background:#fff}}td,th{{padding:10px;border-bottom:1px solid #dce3e9;text-align:left}}
a{{color:#0b6e8a}}@media(prefers-color-scheme:dark){{body{{background:#0d1419;color:#e5ecf1}}table{{background:#141d25}}td,th{{border-color:#25333e}}a{{color:#4cb6d3}}}}</style></head>
<body><main><h1>BM 분석 목록</h1><table><thead><tr><th>종목</th><th>코드</th><th>수집일</th><th>자료</th></tr></thead><tbody>{li}</tbody></table></main></body></html>""")
open(".nojekyll", "w").close()
print(len(rows), "종목")
