"""수급 스캐너 저장소(비공개)에서 종목 시세를 읽어 out/{code}/price.json 에 저장. Claude가 로컬에서 실행(gh 인증 필요).
사용: python fetch_price.py 085910 [...]
- 최신 거래일 1개: 종가·등락률·시가총액·상장주식수
- history: 저장소에 쌓인 거래일의 [날짜, 종가, 시가총액]. 기존 price.json 에 없는 날짜만 추가 다운로드(증분)
- 수급 데이터는 저장하지 않음(공개 저장소라서 시세 요약만)"""
import gzip, io, json, os, subprocess, sys

REPO = "wntlrdywjd-kkanbu/supply-scanner"


def gh(path, raw=False):
    cmd = ["gh", "api"] + (["-H", "Accept: application/vnd.github.raw"] if raw else []) + [f"repos/{REPO}/contents/{path}"]
    return subprocess.run(cmd, capture_output=True, check=True).stdout


def day_rows(day):
    """day: YYYYMMDD -> {종목코드: row}"""
    name = f"{day[:4]}.{day[4:6]}.{day[6:]}.json.gz"
    d = json.load(gzip.open(io.BytesIO(gh(f"data/{day}/{name}", raw=True))))
    cols = d["price"]["columns"]
    return {r[0]: dict(zip(cols, r)) for r in d["price"]["rows"]}, d["date"]


def main(codes):
    days = sorted(x["name"] for x in json.loads(gh("data")) if x["name"].isdigit() and len(x["name"]) == 8)
    for c in codes:
        fp = os.path.join("out", c, "price.json")
        old = json.load(open(fp, encoding="utf-8")) if os.path.exists(fp) else {}
        hist = {h[0]: h for h in old.get("history", [])}
        for dday in days:
            iso = f"{dday[:4]}-{dday[4:6]}-{dday[6:]}"
            if iso in hist and dday != days[-1]:
                continue
            rows, _ = day_rows(dday)
            r = rows.get(c)
            if r:
                hist[iso] = [iso, r["종가"], r["시가총액"]]
                if dday == days[-1]:
                    last = r
        if "last" not in locals():
            sys.exit(f"{c}: 최신 거래일 시세 없음")
        out = {"code": c, "name": last["종목명"], "market": last["시장구분"], "date": f"{days[-1][:4]}-{days[-1][4:6]}-{days[-1][6:]}",
               "close": last["종가"], "change_pct": last["등락률"], "mcap": last["시가총액"], "shares": last["상장주식수"],
               "history": [hist[k] for k in sorted(hist)]}
        os.makedirs(os.path.dirname(fp), exist_ok=True)
        json.dump(out, open(fp, "w", encoding="utf-8"), ensure_ascii=False)
        print(c, out["name"], out["date"], out["close"], out["mcap"], f"history {len(out['history'])}일")


if __name__ == "__main__":
    main(sys.argv[1:])
