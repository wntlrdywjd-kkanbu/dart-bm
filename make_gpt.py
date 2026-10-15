"""사용: python make_gpt.py 085910  -> out/085910/gpt_paste.md (프롬프트+카드, 그대로 GPT에 붙여넣기)"""
import sys, pathlib
p = pathlib.Path("prompts/gpt_bm.md").read_text(encoding="utf-8")
for c in sys.argv[1:]:
    card = pathlib.Path(f"out/{c}/bm_card.md").read_text(encoding="utf-8")
    pathlib.Path(f"out/{c}/gpt_paste.md").write_text(p + card, encoding="utf-8")
    print(c, "ok", len(p + card))
