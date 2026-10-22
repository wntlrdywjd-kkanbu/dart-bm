"""구형 est {k,v} 문자열 -> 구조형 {k,cols,rows}/{k,items}. 사용: python tools/est_convert.py view/095610.json [--write]"""
import json, re, sys
def conv(e, prev):
    if 'cols' in e or 'items' in e: return e, prev
    k, v = str(e['k']), str(e['v']); segs = v.split(' / ')
    yrs = re.findall(r'20\d\d[A-Z]?', k)
    if len(yrs) > 1: prev = yrs
    else: yrs = prev
    cap = re.sub(r',?\s*20\d\d.*\)\s*$', ')', k); cap = re.sub(r',\s*\)$', ')', cap)
    rows = []; ok = len(yrs) > 1
    if ok:
        for sg in segs:
            i = sg.find(' → ')
            if i < 0: ok = False; break
            head = sg[:i]; lab = re.sub(r'\s+\S+$', '', head); f = head[len(lab):].strip()
            vals = [f] + sg[i+3:].split(' → ')
            if not lab or len(vals) != len(yrs): ok = False; break
            rows.append([lab] + vals)
    if ok and rows: return {'k': cap, 'cols': yrs, 'rows': rows}, prev
    qs = [re.match(r'^(\d[Qq]\d\d[EF]?)\s+(.+)$', x) for x in segs]
    if len(segs) > 1 and all(qs): return {'k': k, 'cols': [m.group(1) for m in qs], 'rows': [['값'] + [m.group(2) for m in qs]]}, prev
    return {'k': k, 'items': segs}, prev
if __name__ == '__main__':
    f = sys.argv[1]; d = json.load(open(f, encoding='utf-8')); t = l = 0
    for r in d.get('reports', []):
        prev = []; out = []
        for e in r.get('est') or []:
            n, prev = conv(e, prev); out.append(n); t += 'cols' in n; l += 'items' in n
        r['est'] = out
    print(f, '표', t, '목록', l)
    if '--write' in sys.argv: json.dump(d, open(f, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
