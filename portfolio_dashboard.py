#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
포트폴리오 모니터링 대시보드 (버킷 분리판)
- 일반 위탁 / DC 퇴직연금 / IRP 세 버킷으로 나눠서 봄
- 한국주식·ETF + 미국주식 + 환율 반영
- 전체 합계 + 버킷별 표 + 자산군(섹터) 분산 + 쏠림 알림
- HTML 대시보드 출력, (옵션) Notion 동기화

사용법:
    python portfolio_dashboard.py
    python portfolio_dashboard.py --setup-notion
"""
import sys, csv, datetime as dt
from pathlib import Path
from collections import defaultdict, OrderedDict

try:
    import yaml
except ImportError:
    print("pyyaml 필요: pip install pyyaml"); sys.exit(1)

BASE = Path(__file__).resolve().parent
CONFIG_PATH = BASE / "config.yaml"
HOLDINGS_PATH = BASE / "holdings.csv"
OUTPUT_HTML = BASE / "dashboard.html"


# ── 입력 ────────────────────────────────────────────────
def load_config():
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, encoding="utf-8") as f:
            return yaml.safe_load(f)
    return {"concentration_threshold": 0.25, "sector_threshold": 0.40,
            "notion": {"enabled": False}}

SHEET_CSV_URL = "https://docs.google.com/spreadsheets/d/e/2PACX-1vTUZF0HHHPbDMyVkmr0rlfa1SNlXxFPvirk96lbnFg9I_RW5TObbFA2Zbyd_R0B8A/pub?gid=216230072&single=true&output=csv"


def _bucket_from_account(acc):
    """계좌명에서 bucket(일반/DC/IRP) 추론."""
    if "DC" in acc:
        return "DC"
    if "IRP" in acc:
        return "IRP"
    return "일반"


def _local_avg_map():
    """로컬 holdings.csv에서 (ticker→native avg_cost) 매핑. 평단 정확도 유지용."""
    m = {}
    try:
        with open(HOLDINGS_PATH, encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                t = (r.get("ticker") or "").strip()
                if t:
                    m[t] = float(r["avg_cost"])
    except Exception:
        pass
    return m


def load_holdings_from_sheet():
    """구글시트(게시된 CSV)에서 보유종목 읽기. 실패 시 None 반환.
    수량·종목·계좌는 시트에서, 평단(native)은 로컬 holdings.csv에서 가져온다."""
    import urllib.request, io
    try:
        req = urllib.request.Request(SHEET_CSV_URL, headers={"User-Agent": "Mozilla/5.0"})
        data = urllib.request.urlopen(req, timeout=20).read().decode("utf-8-sig")
        avg_map = _local_avg_map()
        rows = []
        for r in csv.DictReader(io.StringIO(data)):
            t = (r.get("티커") or "").strip()
            if not t:
                continue
            shares_raw = (r.get("수량") or "0").replace(",", "").strip()
            if not shares_raw or shares_raw in ("0", "0.0"):
                continue
            mk = "US" if (r.get("시장") or "").strip() in ("미국", "US") else "KR"
            acc = (r.get("계좌") or "").strip()
            rows.append({
                "bucket": _bucket_from_account(acc),
                "account": acc,
                "ticker": t,
                "market": mk,
                "shares": float(shares_raw),
                "avg_cost": avg_map.get(t, 0.0),   # native 평단(로컬). 신규종목은 0
                "name": (r.get("종목명") or "").strip(),
                "sector": "",
            })
        return rows if rows else None
    except Exception as e:
        print(f"(구글시트 읽기 실패, 로컬 holdings.csv 사용: {e})")
        return None


def load_holdings():
    # 1순위: 구글시트, 실패 시 로컬 holdings.csv
    sheet_rows = load_holdings_from_sheet()
    if sheet_rows:
        print(f"  구글시트에서 {len(sheet_rows)}개 종목 읽음")
        return sheet_rows
    rows = []
    with open(HOLDINGS_PATH, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            if not r.get("ticker"):
                continue
            rows.append({
                "bucket": (r.get("bucket") or "기타").strip(),
                "account": (r.get("account") or "").strip(),
                "ticker": r["ticker"].strip(),
                "market": r["market"].strip().upper(),
                "shares": float(r["shares"]),
                "avg_cost": float(r["avg_cost"]),
                "name": (r.get("name") or "").strip(),
                "sector": (r.get("sector") or "").strip(),
            })
    return rows


# ── 시세/환율/섹터 (사용자 PC에서만 동작) ────────────────
def get_usdkrw():
    import FinanceDataReader as fdr
    return float(fdr.DataReader("USD/KRW")["Close"].dropna().iloc[-1])

_KR_LIST = None
def _kr_listing():
    global _KR_LIST
    if _KR_LIST is None:
        import FinanceDataReader as fdr
        df = fdr.StockListing("KRX")
        if "Code" not in df.columns and "Symbol" in df.columns:
            df = df.rename(columns={"Symbol": "Code"})
        _KR_LIST = df
    return _KR_LIST

def get_kr_quote(h):
    import FinanceDataReader as fdr
    price = float(fdr.DataReader(h["ticker"])["Close"].dropna().iloc[-1])
    name = h["name"] or h["ticker"]
    sector = h["sector"] or "기타"
    if not h["name"]:
        try:
            hit = _kr_listing(); hit = hit[hit["Code"] == h["ticker"]]
            if len(hit): name = str(hit.iloc[0].get("Name", name))
        except Exception:
            pass
    return price, name, sector

def get_us_quote(h):
    import yfinance as yf
    t = yf.Ticker(h["ticker"])
    price = None
    fi = getattr(t, "fast_info", None)
    if fi is not None:
        price = float(fi.get("last_price") or 0) or None
    if not price:
        price = float(t.history(period="5d")["Close"].dropna().iloc[-1])
    name, sector = h["name"] or h["ticker"], h["sector"] or "Unknown"
    if not h["name"] or not h["sector"]:
        try:
            info = t.info
            if not h["name"]:   name = info.get("shortName") or name
            if not h["sector"]: sector = info.get("sector") or sector
        except Exception:
            pass
    return price, name, sector

def fetch_quote(h):
    if h["market"] == "KR":
        p, n, s = get_kr_quote(h); cur = "KRW"
    else:
        p, n, s = get_us_quote(h); cur = "USD"
    return {"price": p, "name": n, "sector": s, "currency": cur}


# ── 계산 ────────────────────────────────────────────────
def load_display_names():
    """names.csv에서 티커→표시명 매핑 로드 (미국주 한글명/ETF 풀네임)."""
    path = BASE / "names.csv"
    names = {}
    if path.exists():
        with open(path, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if r.get("ticker") and r.get("display"):
                    names[r["ticker"].strip()] = r["display"].strip()
    return names


def compute(holdings, quotes, usdkrw, config):
    display_names = load_display_names()
    # (bucket,ticker)로 합산
    agg = OrderedDict()
    for h in holdings:
        q = quotes[h["ticker"]]
        fx = usdkrw if q["currency"] == "USD" else 1.0
        key = (h["bucket"], h["ticker"])
        if key not in agg:
            agg[key] = {"bucket": h["bucket"], "ticker": h["ticker"],
                        "name": display_names.get(h["ticker"], q["name"]), "market": h["market"],
                        "sector": h["sector"] or q["sector"],
                        "currency": q["currency"], "price": q["price"],
                        "shares": 0.0, "cost_native": 0.0}
        a = agg[key]
        a["shares"] += h["shares"]
        a["cost_native"] += h["avg_cost"] * h["shares"]

    rows = []
    for a in agg.values():
        fx = usdkrw if a["currency"] == "USD" else 1.0
        avg = a["cost_native"] / a["shares"] if a["shares"] else 0
        value_krw = a["price"] * a["shares"] * fx
        cost_krw = avg * a["shares"] * fx
        rows.append({**a, "avg_cost": avg, "value_krw": value_krw,
                     "cost_krw": cost_krw, "pnl_krw": value_krw - cost_krw,
                     "pnl_pct": (a["price"]/avg - 1) if avg else 0})

    total_value = sum(r["value_krw"] for r in rows)
    total_cost = sum(r["cost_krw"] for r in rows)
    for r in rows:
        r["weight"] = r["value_krw"]/total_value if total_value else 0

    # 버킷별 묶기
    buckets = OrderedDict()
    for r in sorted(rows, key=lambda x: x["value_krw"], reverse=True):
        buckets.setdefault(r["bucket"], []).append(r)
    bucket_summ = OrderedDict()
    for b, brs in buckets.items():
        bv = sum(r["value_krw"] for r in brs)
        bc = sum(r["cost_krw"] for r in brs)
        for r in brs:
            r["bucket_weight"] = r["value_krw"]/bv if bv else 0
        bucket_summ[b] = {"value": bv, "cost": bc, "pnl": bv-bc,
                          "pnl_pct": (bv/bc-1) if bc else 0,
                          "weight": bv/total_value if total_value else 0}

    # 자산군(섹터) 분산 — 전체 기준
    sectors = defaultdict(float)
    for r in rows:
        sectors[r["sector"]] += r["weight"]
    sectors = OrderedDict(sorted(sectors.items(), key=lambda x: x[1], reverse=True))

    # 쏠림 알림 — 전체 기준
    ct = config.get("concentration_threshold", 0.25)
    st = config.get("sector_threshold", 0.40)
    alerts = []
    for r in sorted(rows, key=lambda x: x["weight"], reverse=True):
        if r["weight"] > ct:
            alerts.append(f"종목 쏠림: {r['name']} {r['weight']*100:.1f}% (기준 {ct*100:.0f}%)")
    for s, w in sectors.items():
        if w > st:
            alerts.append(f"자산군 쏠림: {s} {w*100:.1f}% (기준 {st*100:.0f}%)")

    # 군별 묶기 (1군/2군/3군/미분류) — groups.csv 기준
    gmap = {}
    gpath = BASE / "groups.csv"
    if gpath.exists():
        with open(gpath, encoding="utf-8") as f:
            for gr in csv.DictReader(f):
                gmap[gr["ticker"]] = gr["group"]
    groups = OrderedDict()
    for r in sorted(rows, key=lambda x: x["value_krw"], reverse=True):
        g = gmap.get(r["ticker"], "미분류")
        groups.setdefault(g, []).append(r)
    group_summ = OrderedDict()
    for g, grs in groups.items():
        gv = sum(r["value_krw"] for r in grs)
        gc = sum(r["cost_krw"] for r in grs)
        for r in grs:
            r["group_weight"] = r["value_krw"]/gv if gv else 0
        group_summ[g] = {"value": gv, "cost": gc, "pnl": gv-gc,
                         "pnl_pct": (gv/gc-1) if gc else 0,
                         "weight": gv/total_value if total_value else 0}
    # 군 표시 순서 고정
    gorder = [g for g in ["1군","2군","3군","미분류"] if g in groups]
    groups = OrderedDict((g, groups[g]) for g in gorder)
    group_summ = OrderedDict((g, group_summ[g]) for g in gorder)

    # 테마별 묶기 (사이클 정류장) — themes.csv 기준
    tmap = {}
    tpath = BASE / "themes.csv"
    if tpath.exists():
        with open(tpath, encoding="utf-8") as f:
            for tr in csv.DictReader(f):
                tmap[tr["ticker"]] = tr["theme"]
    theme_alloc = defaultdict(float)
    for r in rows:
        theme_alloc[tmap.get(r["ticker"], "기타")] += r["value_krw"]
    theme_alloc = OrderedDict(sorted(theme_alloc.items(),
                              key=lambda x: x[0]))  # ①②③… 순서

    return {"buckets": buckets, "bucket_summ": bucket_summ, "sectors": sectors,
            "groups": groups, "group_summ": group_summ, "theme_alloc": theme_alloc,
            "alerts": alerts, "total_value": total_value, "total_cost": total_cost,
            "total_pnl": total_value-total_cost,
            "total_pnl_pct": (total_value/total_cost-1) if total_cost else 0,
            "usdkrw": usdkrw, "asof": dt.datetime.now().strftime("%Y-%m-%d %H:%M")}


def compute_rebalance(rows, config):
    """1/2/3군 목표 비중 리밸런싱. groups.csv + finance.yaml(현금성) 사용."""
    gpath = BASE / "groups.csv"
    if not gpath.exists():
        return None
    gmap = {}
    with open(gpath, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            gmap[r["ticker"]] = r["group"]

    # 현금성(현금+적금) — finance.yaml에서
    cash = 0.0
    fpath = BASE / "finance.yaml"
    if fpath.exists():
        fc = yaml.safe_load(open(fpath, encoding="utf-8")) or {}
        cash = sum(x["amount"] for x in fc.get("cash", []))

    # 종목별 군 배정 + 군별 합
    by_group = defaultdict(float)
    unclassified = []
    holdings_by_group = defaultdict(list)
    for r in rows:
        g = gmap.get(r["ticker"], "미분류")
        by_group[g] += r["value_krw"]
        holdings_by_group[g].append(r)
        if g == "미분류":
            unclassified.append(r)
    by_group["현금성"] = cash

    grand = sum(by_group.values())
    targets = config.get("group_targets", {"1군":0.50, "2군":0.20, "3군":0.10, "현금성":0.20})

    table = []
    for g in ["1군","2군","3군","현금성"]:
        cur = by_group.get(g, 0.0)
        tgt = targets.get(g, 0)*grand
        table.append({"group":g, "cur":cur, "cur_pct":cur/grand if grand else 0,
                      "tgt_pct":targets.get(g,0), "tgt":tgt, "gap":tgt-cur})
    unc = by_group.get("미분류", 0.0)
    table.append({"group":"미분류", "cur":unc, "cur_pct":unc/grand if grand else 0,
                  "tgt_pct":None, "tgt":None, "gap":None})

    return {"table":table, "grand":grand,
            "unclassified":sorted(unclassified, key=lambda x:x["value_krw"], reverse=True),
            "holdings_by_group":holdings_by_group, "targets":targets,
            "candidates":{  # 군별 매수 후보(이미지 리스트, 미보유 포함)
                "2군":["SCHD","JP모건(JPM)","GLD","코스트코(COST)","TJX","일라이릴리(LLY)","XLF"],
                "3군":["AMD","팔란티어(PLTR)","로켓랩(RKLB)","SPCX","로빈후드(HOOD)","오클로(OKLO)","BWXT","LEU"]}}


# ── HTML ────────────────────────────────────────────────
def won(x): return f"{x:,.0f}원"
def pct(x): return f"{x*100:+.2f}%"

def render_html(d, config=None):
    config = config or {}
    roster = config.get("group_roster", {})
    pc = "pos" if d["total_pnl"] >= 0 else "neg"
    GICON = {"1군":"🟢 1군 (Core)", "2군":"🔵 2군 (Steady)",
             "3군":"🟠 3군 (Challenge)", "미분류":"⚪ 미분류"}
    bucket_html = ""
    for g, grs in d["groups"].items():
        s = d["group_summ"][g]
        bpc = "pos" if s["pnl"] >= 0 else "neg"
        label = GICON.get(g, g)
        tr = ""
        for r in grs:
            cls = "pos" if r["pnl_krw"] >= 0 else "neg"
            nat = f"${r['price']:,.2f}" if r["currency"]=="USD" else f"{r['price']:,.0f}"
            tr += f"""<tr><td><b>{r['name']}</b><span class="tk">{r['ticker']} · {r['sector']}</span></td>
              <td class="num">{r['shares']:g}</td><td class="num">{nat}</td>
              <td class="num">{won(r['value_krw'])}</td>
              <td class="num {cls}">{pct(r['pnl_pct'])}</td>
              <td class="num">{r['group_weight']*100:.1f}%</td></tr>"""
        # 미보유 종목 (이미지엔 있으나 내 계좌엔 없는 것)
        missing_html = ""
        if g in roster:
            held = {r["ticker"] for r in grs}
            miss = [(t, nm) for t, nm in roster[g].items() if t not in held and t != "현금성"]
            if miss:
                chips = " ".join(f'<span class="miss">{nm}</span>' for t, nm in miss)
                missing_html = f'<div class="missbox"><span class="misslbl">미보유</span>{chips}</div>'
        bucket_html += f"""<div class="panel"><div class="bhead">
            <h3>{label}</h3><div class="bsum">{won(s['value'])}
            <span class="{bpc}">{won(s['pnl'])} ({pct(s['pnl_pct'])})</span>
            <span class="bw">전체 {s['weight']*100:.1f}%</span></div></div>
            <table><thead><tr><th>종목</th><th class="num">수량</th><th class="num">현재가</th>
            <th class="num">평가금액</th><th class="num">손익률</th><th class="num">군내</th></tr></thead>
            <tbody>{tr}</tbody></table>{missing_html}</div>"""

    # 자산군 분산 → 군별 비중
    gtargets = config.get("group_targets", {})
    GBAR = {"1군":"#22c55e","2군":"#3b82f6","3군":"#f59e0b","미분류":"#8b909a"}
    sec = ""
    for g in ["1군","2군","3군","미분류"]:
        if g not in d["group_summ"]:
            continue
        w = d["group_summ"][g]["weight"]
        tgt = gtargets.get(g)
        tgt_txt = f" / 목표 {tgt*100:.0f}%" if tgt else ""
        color = GBAR.get(g, "#6366f1")
        sec += f"""<div class="srow"><span class="slabel">{g}</span>
          <div class="sbar"><span style="width:{min(w*100,100):.1f}%;background:{color}"></span></div>
          <span class="sval">{w*100:.1f}%<span class="mut" style="font-size:11px">{tgt_txt}</span></span></div>"""

    # 테마별 분산 (사이클 정류장)
    theme_html = ""
    talloc = d.get("theme_alloc", {})
    tgrand = sum(talloc.values()) or 1
    THCOLOR = ["#22c55e","#10b981","#3b82f6","#6366f1","#8b5cf6","#a855f7",
               "#ec4899","#f43f5e","#f59e0b","#eab308","#14b8a6","#64748b"]
    for i, (th, v) in enumerate(talloc.items()):
        w = v/tgrand
        color = THCOLOR[i % len(THCOLOR)]
        theme_html += f"""<div class="srow"><span class="slabel" style="width:175px">{th}</span>
          <div class="sbar"><span style="width:{min(w*100,100):.1f}%;background:{color}"></span></div>
          <span class="sval">{w*100:.1f}%</span></div>"""

    if d["alerts"]:
        al = '<div class="alerts"><h3>⚠ 쏠림 알림</h3><ul>' + \
             "".join(f"<li>{a}</li>" for a in d["alerts"]) + "</ul></div>"
    else:
        al = '<div class="alerts ok"><h3>✓ 쏠림 없음</h3></div>'

    return f"""<!DOCTYPE html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>포트폴리오 대시보드</title>
<style>
:root{{--bg:#0f1115;--card:#1a1d24;--line:#2a2e37;--txt:#e6e8ec;--mut:#8b909a;--pos:#22c55e;--neg:#ef4444;--ac:#6366f1}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--txt);
font-family:-apple-system,'Segoe UI','Apple SD Gothic Neo',sans-serif;padding:28px}}
h1{{font-size:20px;margin:0 0 2px}}.asof{{color:var(--mut);font-size:13px;margin-bottom:20px}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:14px;margin-bottom:22px}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px}}
.card .lbl{{color:var(--mut);font-size:13px;margin-bottom:6px}}.card .big{{font-size:23px;font-weight:700}}
.pos{{color:var(--pos)}}.neg{{color:var(--neg)}}
.grid{{display:grid;grid-template-columns:1.7fr 1fr;gap:18px;align-items:start}}
@media(max-width:860px){{.grid{{grid-template-columns:1fr}}}}
.panel{{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:18px;margin-bottom:16px}}
.bhead{{display:flex;justify-content:space-between;align-items:baseline;flex-wrap:wrap;gap:8px;margin-bottom:12px}}
.panel h3{{margin:0;font-size:15px}}.bsum{{font-size:13px;color:var(--mut)}}
.bsum span{{margin-left:10px}}.bw{{color:var(--mut)}}
table{{width:100%;border-collapse:collapse;font-size:13px}}
th{{text-align:left;color:var(--mut);font-weight:500;padding:7px 5px;border-bottom:1px solid var(--line)}}
td{{padding:9px 5px;border-bottom:1px solid var(--line)}}.num{{text-align:right}}
.tk{{display:block;color:var(--mut);font-size:11px;margin-top:2px}}
.srow{{display:flex;align-items:center;gap:10px;margin-bottom:11px;font-size:13px}}
.slabel{{width:130px}}.sbar{{flex:1;height:8px;background:var(--line);border-radius:5px;overflow:hidden}}
.sbar span{{display:block;height:100%;background:linear-gradient(90deg,#6366f1,#8b5cf6)}}
.sval{{width:46px;text-align:right;color:var(--mut)}}
.alerts{{background:var(--card);border:1px solid var(--line);border-left:3px solid var(--neg);border-radius:12px;padding:16px;margin-top:16px}}
.alerts.ok{{border-left-color:var(--pos)}}.alerts h3{{margin:0 0 8px;font-size:14px}}
.alerts ul{{margin:0;padding-left:18px}}.alerts li{{margin:4px 0;font-size:13px}}
.fx{{color:var(--mut);font-size:12px;margin-top:18px}}
.flowarrow{{color:#6366f1;font-weight:600}}
.hint{{color:var(--mut);font-size:12px}}.mut{{color:var(--mut)}}
.missbox{{margin-top:10px;padding-top:10px;border-top:1px dashed var(--line);display:flex;flex-wrap:wrap;gap:6px;align-items:center}}
.misslbl{{color:var(--mut);font-size:11px;margin-right:2px}}
.miss{{display:inline-block;font-size:11px;color:var(--mut);background:#22252e;border:1px solid var(--line);border-radius:10px;padding:2px 8px}}
.tabs{{display:flex;gap:8px;margin-bottom:22px;flex-wrap:wrap;position:sticky;top:0;background:var(--bg);padding:6px 0 10px;z-index:10}}
.tab{{cursor:pointer;border:1px solid var(--line);background:var(--card);color:var(--mut);
  padding:10px 18px;border-radius:12px;font-size:14px;font-weight:600;user-select:none;transition:.15s}}
.tab:hover{{color:var(--txt)}}
.tab.active{{background:var(--ac);color:#fff;border-color:var(--ac)}}
.page{{display:none}}.page.active{{display:block}}
</style></head><body>
<h1>📊 포트폴리오 대시보드</h1>
<div class="asof">기준 {d['asof']} · USD/KRW {d['usdkrw']:,.1f}</div>
<div class="tabs">
  <div class="tab active" onclick="showPage('p1',this)">📊 포트폴리오</div>
  <div class="tab" onclick="showPage('p2',this)">🎯 리밸런싱 · 계좌정리</div>
  <div class="tab" onclick="showPage('p3',this)">🩺 재무건강</div>
</div>
<div id="p1" class="page active">
<div class="cards">
  <div class="card"><div class="lbl">총 평가금액</div><div class="big">{won(d['total_value'])}</div></div>
  <div class="card"><div class="lbl">총 평가손익</div><div class="big {pc}">{won(d['total_pnl'])}</div></div>
  <div class="card"><div class="lbl">총 수익률</div><div class="big {pc}">{pct(d['total_pnl_pct'])}</div></div>
</div>
<div class="grid">
  <div>{bucket_html}</div>
  <div><div class="panel"><h3>자산군 분산 (1·2·3군/미분류)</h3>{sec}</div>
    <div class="panel"><h3>🚌 테마별 분산 (사이클 정류장)</h3>{theme_html}</div>{al}</div>
</div>
</div><!-- /p1 -->
<div id="p2" class="page"><!--REBAL_SLOT--></div>
<div id="p3" class="page"><!--FIN_SLOT--></div>
<div class="fx">시세·환율은 마지막 종가 기준이며 실시간 호가와 다를 수 있습니다. 연금(DC·IRP)은 인출 제한이 있어 일반 계좌와 성격이 다릅니다.</div>
<script>
function showPage(id, el){{
  document.querySelectorAll('.page').forEach(p=>p.classList.remove('active'));
  document.getElementById(id).classList.add('active');
  document.querySelectorAll('.tab').forEach(t=>t.classList.remove('active'));
  el.classList.add('active');
  window.scrollTo(0,0);
}}
</script>
</body></html>"""


# ── Notion (옵션) ───────────────────────────────────────
def notion_schema():
    return {"종목":{"title":{}},"버킷":{"select":{}},"티커":{"rich_text":{}},
            "수량":{"number":{"format":"number"}},"평가금액(원)":{"number":{"format":"won"}},
            "손익률":{"number":{"format":"percent"}},"비중":{"number":{"format":"percent"}},
            "업데이트":{"date":{}}}

def setup_notion(config):
    from notion_client import Client
    n = config["notion"]; c = Client(auth=n["token"])
    db = c.databases.create(parent={"type":"page_id","page_id":n["parent_page_id"]},
        title=[{"type":"text","text":{"content":"📈 포트폴리오 현황"}}], properties=notion_schema())
    print("database_id:", db["id"], "\n→ config.yaml 에 넣어주세요.")

def render_rebalance(rb):
    if not rb: return ""
    rows_html = ""
    for t in rb["table"]:
        if t["gap"] is None:
            badge = '<span class="mut">목표 없음</span>'
            gap_html = f'<td class="num mut">정리 대상</td>'
            tgtpct = "—"; tgt = "—"
        else:
            tgtpct = f'{t["tgt_pct"]*100:.0f}%'
            tgt = won(t["tgt"])
            if abs(t["gap"]) < rb["grand"]*0.01:
                gap_html = '<td class="num pos">≈ 맞음</td>'
            elif t["gap"] > 0:
                gap_html = f'<td class="num neg">{won(t["gap"])} 부족 ▲매수</td>'
            else:
                gap_html = f'<td class="num" style="color:#f59e0b">{won(-t["gap"])} 초과 ▼정리</td>'
        rows_html += (f'<tr><td><b>{t["group"]}</b></td>'
                      f'<td class="num">{won(t["cur"])}</td><td class="num">{t["cur_pct"]*100:.1f}%</td>'
                      f'<td class="num mut">{tgtpct}</td>{gap_html}</tr>')

    # 미분류(정리 후보) 목록
    unc_html = ""
    for r in rb["unclassified"]:
        unc_html += f'<tr><td>{r["name"]}</td><td class="mut">{r["ticker"]}</td><td class="num">{won(r["value_krw"])}</td><td class="mut">{r["bucket"]}</td></tr>'

    # 매수 후보(부족 군)
    cand_html = ""
    for t in rb["table"]:
        if t["gap"] and t["gap"] > rb["grand"]*0.01 and t["group"] in rb["candidates"]:
            names = " · ".join(rb["candidates"][t["group"]])
            cand_html += (f'<div class="cand"><b>{t["group"]}</b> {won(t["gap"])} 더 채우기 — 후보: '
                          f'<span class="mut">{names}</span></div>')

    return f"""
    <h1 style="margin-top:34px">🎯 리밸런싱 (목표 1군50·2군20·3군10·현금20)</h1>
    <div class="panel"><h3>군별 목표 vs 현재</h3>
      <table><thead><tr><th>그룹</th><th class="num">현재금액</th><th class="num">현재%</th>
        <th class="num">목표%</th><th class="num">조정</th></tr></thead><tbody>{rows_html}</tbody></table>
      <div class="hint">평가금액 기준. ▲매수=더 사야 / ▼정리=줄여야 / ≈맞음=목표 도달(±1%)</div>
    </div>
    <div class="panel"><h3>미분류 정리 후보 <span class="mut">· 군에 안 들어가는 일반계좌 개별주</span></h3>
      <table><thead><tr><th>종목</th><th>티커</th><th class="num">평가금액</th><th>계좌</th></tr></thead>
        <tbody>{unc_html}</tbody></table>
      <div class="hint">이걸 정리하면 부족한 2·3군 채울 재원이 됨. 어떤 걸 남길지는 직접 선택.</div>
    </div>
    <div class="panel"><h3>부족 군 매수 후보</h3>{cand_html}
      <div class="hint">그룹별 부족액만 표시. 종목별 금액은 비중 안 정했으니 후보 중 직접 선택.</div>
    </div>"""


def render_relocation(rows, config, usdkrw=1378.0):
    """계좌 정리: 목표계좌와 다른 위치에 있는 종목을 '이동 대상'으로 표시.
    holdings.csv를 직접 읽어 계좌별로 본다(merged rows엔 계좌 정보가 없음)."""
    gpath = BASE / "groups.csv"
    if not gpath.exists():
        return ""
    tmap = {}
    with open(gpath, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            tmap[r["ticker"]] = r.get("target_account", "")
    # 현재가 맵 (merged rows에서)
    pmap = {r["ticker"]: (r["price"], r["currency"]) for r in rows}
    def short(acc):
        if "7081" in acc or "삼성" in acc: return "삼성7081"
        if "5154" in acc: return "키움5154"
        if "5978" in acc: return "키움5978"
        if "356" in acc or "미래" in acc: return "미래에셋356"
        return acc
    roles = config.get("account_roles", {})
    moves = []
    with open(BASE / "holdings.csv", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["bucket"] != "일반":
                continue
            t = r["ticker"]; cur = short(r["account"]); tgt = tmap.get(t, "")
            if not tgt or cur == tgt:
                continue
            sh = float(r["shares"])
            price, ccy = pmap.get(t, (float(r["avg_cost"]), "KRW" if r["market"]=="KR" else "USD"))
            fx = usdkrw if ccy == "USD" else 1.0
            val = price * sh * fx
            moves.append((r["name"] or t, t, cur, tgt, sh, val))
    if not moves:
        return ('<h1 style="margin-top:34px">📦 계좌 정리</h1>'
                '<div class="panel"><div class="pos">✅ 모든 종목이 목표 계좌에 있습니다.</div></div>')
    rows_html = ""
    for n, t, cur, tgt, sh, v in sorted(moves, key=lambda x: -x[5]):
        rows_html += (f'<tr><td>{n}</td><td class="mut">{t}</td><td class="num">{sh:.0f}주</td>'
                      f'<td>{cur}</td><td class="flowarrow">→ {tgt}</td>'
                      f'<td class="num">{won(v)}</td></tr>')
    role_html = " · ".join(f"{k}={v}" for k, v in roles.items())
    return f"""
    <h1 style="margin-top:34px">📦 계좌 정리 <span class="mut">· 이동 대상 {len(moves)}종목</span></h1>
    <div class="panel"><div class="hint" style="margin-bottom:10px">계좌 역할: {role_html}</div>
      <table><thead><tr><th>종목</th><th>티커</th><th class="num">수량</th><th>현재</th><th>목표</th><th class="num">평가금액</th></tr></thead>
      <tbody>{rows_html}</tbody></table>
      <div class="hint">목표 계좌와 다른 위치에 있는 종목입니다. 매도→이체→매수로 이동(미분류는 일부 미래에셋 이관 / 나머지 현금화).</div>
    </div>"""


def sync_notion(config, d):
    from notion_client import Client
    n = config["notion"]; c = Client(auth=n["token"]); db = n["database_id"]
    for p in c.databases.query(database_id=db).get("results", []):
        c.pages.update(page_id=p["id"], archived=True)
    today = dt.date.today().isoformat()
    for b, brs in d["buckets"].items():
        for r in brs:
            c.pages.create(parent={"database_id":db}, properties={
                "종목":{"title":[{"text":{"content":r["name"]}}]},
                "버킷":{"select":{"name":b}},
                "티커":{"rich_text":[{"text":{"content":r["ticker"]}}]},
                "수량":{"number":r["shares"]},
                "평가금액(원)":{"number":round(r["value_krw"])},
                "손익률":{"number":round(r["pnl_pct"],4)},
                "비중":{"number":round(r["weight"],4)},
                "업데이트":{"date":{"start":today}}})
    print("Notion 동기화 완료")


# ── 엔트리 ──────────────────────────────────────────────
def main():
    config = load_config()
    if "--setup-notion" in sys.argv:
        setup_notion(config); return
    holdings = load_holdings()
    print(f"보유 {len(holdings)}행 시세 조회 중...")
    usdkrw = get_usdkrw()
    quotes = {}
    for h in holdings:
        if h["ticker"] not in quotes:
            quotes[h["ticker"]] = fetch_quote(h)
            q = quotes[h["ticker"]]
            print(f"  {h['ticker']:<8} {str(q['name'])[:18]:<18} {q['price']:,.2f} {q['currency']}")
    d = compute(holdings, quotes, usdkrw, config)
    html = render_html(d, config)
    # 리밸런싱 + 계좌정리 → p2 탭
    rebal_html = ""
    try:
        allrows = [r for brs in d["buckets"].values() for r in brs]
        rb = compute_rebalance(allrows, config)
        if rb:
            rebal_html += render_rebalance(rb)
        reloc = render_relocation(allrows, config, usdkrw)
        if reloc:
            rebal_html += reloc
    except Exception as e:
        print(f"(리밸런싱 섹션 생략: {e})")
    html = html.replace("<!--REBAL_SLOT-->", rebal_html, 1)
    # finance.yaml 있으면 재무건강 → p3 탭
    fin_path = BASE / "finance.yaml"
    if fin_path.exists():
        try:
            import finance_health as fh
            fc = yaml.safe_load(open(fin_path, encoding="utf-8"))
            fd = fh.compute(fc)
            fin_body = fh.section_html(fd, standalone=False)
            html = html.replace("</style>", fh.CSS + "</style>", 1)
            html = html.replace("<!--FIN_SLOT-->", fin_body, 1)
        except Exception as e:
            print(f"(재무건강 섹션 생략: {e})")
    OUTPUT_HTML.write_text(html, encoding="utf-8")
    print(f"\n대시보드: {OUTPUT_HTML}")
    print(f"총 평가 {won(d['total_value'])} / 손익 {won(d['total_pnl'])} ({pct(d['total_pnl_pct'])})")
    for b, s in d["bucket_summ"].items():
        print(f"  [{b}] {won(s['value'])} ({pct(s['pnl_pct'])})")
    if config.get("notion", {}).get("enabled"):
        sync_notion(config, d)

if __name__ == "__main__":
    main()
