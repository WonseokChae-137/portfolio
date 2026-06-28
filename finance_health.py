#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
재무건강 대시보드 (현금비중 + runway + 대출)
- 생활비는 일단 제외(부정확). config에서 monthly_living 채우면 자동 반영.
- 진짜 현금 / 마이너스 여력 / 대출을 색으로 분리해서 착각 방지.
- 자산·대출·현금 값은 finance.yaml 에서 관리(수동 입력).
"""
import sys, datetime as dt
from pathlib import Path
try:
    import yaml
except ImportError:
    print("pip install pyyaml"); sys.exit(1)

BASE = Path(__file__).resolve().parent
FIN = BASE / "finance.yaml"
OUT = BASE / "finance_health.html"

def won(x): return f"{x:,.0f}원"
def man(x): return f"{x/10000:,.0f}만"

def load():
    with open(FIN, encoding="utf-8") as f:
        return yaml.safe_load(f)

def compute(c):
    cash = sum(x["amount"] for x in c["cash"])
    stocks = sum(x["amount"] for x in c["stocks"])
    savings = sum(x["amount"] for x in c.get("savings", []))
    savings_liquid = sum(x["amount"] for x in c.get("savings", []) if x.get("liquid"))
    pension = sum(x["amount"] for x in c.get("pension", []))   # 연금(DC/IRP) — 비유동
    deposit = sum(x["amount"] for x in c.get("deposit", []))    # 보증금 — 묶인 자산(순자산 포함, 현금비중 제외)
    minus_room = sum(x.get("unused",0) for x in c["loans"] if x.get("type")=="minus")
    debt = sum(x["balance"] for x in c["loans"])
    invest = cash + stocks + savings + pension + deposit   # 총자산(적금·연금·보증금 포함)
    net = invest - debt

    # 월 대출 유출(이자 + 분할원금)
    m_interest = sum(x["balance"]*x["rate"]/12 for x in c["loans"])
    m_principal = sum(x.get("monthly_principal",0) for x in c["loans"])
    m_debt = m_interest + m_principal

    living = c.get("monthly_living",0) or 0
    living_est = c.get("living_estimate",0) or 0   # 대충 추정치(참고용)
    income = c.get("monthly_income",0) or 0
    burn = living + m_debt          # 수입 끊김 가정 월 유출(생활비 미입력이면 대출만)
    burn_est = (living_est + m_debt) if (living==0 and living_est) else None
    surplus = income - burn if income else None

    cash_ratio = cash/invest if invest else 0
    target = c.get("cash_target",0.20)

    rw = lambda pot: (pot/burn) if burn>0 else float('inf')
    rwe = (lambda pot: (pot/burn_est) if burn_est else None)
    return {
        "cash":cash,"stocks":stocks,"savings":savings,"savings_liquid":savings_liquid,
        "minus_room":minus_room,"debt":debt,
        "invest":invest,"net":net,"m_interest":m_interest,"m_principal":m_principal,
        "m_debt":m_debt,"living":living,"living_est":living_est,"income":income,
        "burn":burn,"burn_est":burn_est,"surplus":surplus,
        "cash_ratio":cash_ratio,"target":target,
        "gap":max(0,target*invest-cash),
        "rw_cash":rw(cash),"rw_minus":rw(cash+minus_room),
        "rw_savings":rw(cash+savings),"rw_stock":rw(cash+savings+stocks),
        "rwe_cash":rwe(cash),"rwe_minus":rwe(cash+minus_room),
        "rwe_savings":rwe(cash+savings),"rwe_stock":rwe(cash+savings+stocks),
        "asof":dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "loans":c["loans"],
    }

def section_html(d, standalone=True):
    ratio_pc = d["cash_ratio"]*100; tgt_pc=d["target"]*100
    ratio_cls = "neg" if d["cash_ratio"] < d["target"] else "pos"
    netcls = "pos" if d["net"]>=0 else "neg"
    if d["living"]:
        rw_title = "버티는 기간 (runway) · 생활비 포함"
        rw_caveat = ""
    else:
        rw_title = "대출 상환 감당 기간 · 생활비 미반영"
        rw_caveat = ('<div class="warn">⚠ 이건 <b>대출 이자·원금만 감당하는 기간</b>이라 '
                     '실제 생존 기간이 아니에요. 생활비를 더하면 훨씬 짧아집니다. '
                     'finance.yaml의 monthly_living을 채우면 진짜 생존 runway가 나와요.</div>')

    loan_rows=""
    for l in sorted(d["loans"], key=lambda x:x["balance"], reverse=True):
        mi = l["balance"]*l["rate"]/12 + l.get("monthly_principal",0)
        tag = {"minus":"마이너스","installment":"원금분할","bullet":"만기일시"}.get(l.get("type"),"이자")
        loan_rows+=f"""<tr><td>{l['name']}</td><td class="num">{won(l['balance'])}</td>
          <td class="num">{l['rate']*100:.2f}%</td><td class="num">{won(mi)}</td><td>{tag}</td></tr>"""

    def rwrow(color, label, months, sub, months_est=None):
        est = f'<span class="est">생활비 추정 시 {months_est:.1f}개월</span>' if months_est else ''
        wbar = min(months/d['rw_stock']*100,100) if d['rw_stock'] else 100
        return (f'<div class="rw"><div class="rwbar {color}" style="--w:{wbar:.0f}%"></div>'
                f'<div class="rwlab"><span>{label}</span><b>{months:.1f}개월 {est}</b></div>'
                f'<div class="rwsub">{sub}</div></div>')

    runway_html = (
        rwrow("green","🟢 현금만", d["rw_cash"], f"진짜 안전선 · {man(d['cash'])}원", d["rwe_cash"]) +
        (rwrow("blue","🔵 +적금 해지", d["rw_savings"], f"적금 깨면(혜택 손해) · +{man(d['savings'])}원", d["rwe_savings"]) if d["savings"] else "") +
        rwrow("yellow","🟡 +마이너스 여력", d["rw_minus"], f"쓰면 빚 {man(d['minus_room'])}원 늘어남", d["rwe_minus"]) +
        rwrow("red","🔴 +주식 처분", d["rw_stock"], "손실 매도 가정", d["rwe_stock"])
    )

    body = f"""
    <div class="cards">
      <div class="card"><div class="lbl">순자산</div><div class="big {netcls}">{won(d['net'])}</div>
        <div class="sub">자산 {man(d['invest'])}{f" (적금 {man(d['savings'])} 포함)" if d['savings'] else ""} − 대출 {man(d['debt'])}</div></div>
      <div class="card"><div class="lbl">현금 비중</div><div class="big {ratio_cls}">{ratio_pc:.1f}%</div>
        <div class="sub">목표 {tgt_pc:.0f}% · 부족 {man(d['gap'])}원</div></div>
      <div class="card"><div class="lbl">월 대출 상환</div><div class="big">{won(d['m_debt'])}</div>
        <div class="sub">이자 {won(d['m_interest'])} + 원금 {won(d['m_principal'])}</div></div>
    </div>

    <div class="panel"><h3>현금 비중 <span class="mut">(목표 {tgt_pc:.0f}%)</span></h3>
      <div class="gauge"><div class="gfill" style="width:{min(ratio_pc,100):.1f}%"></div>
        <div class="gtarget" style="left:{tgt_pc:.0f}%"></div></div>
      <div class="gtext"><span class="{ratio_cls}">현재 {ratio_pc:.1f}%</span>
        <span class="mut">목표 {tgt_pc:.0f}%까지 {man(d['gap'])}원 부족</span></div>
    </div>

    <div class="panel"><h3>{rw_title}</h3>
      {rw_caveat}
      {runway_html}
      <div class="hint">월 유출 {won(d['burn'])} 기준{' (대출 상환만)' if not d['living'] else ''}.</div>
    </div>

    <div class="panel"><h3>대출 현황 <span class="mut">· {len(d['loans'])}건 · 합 {man(d['debt'])}원</span></h3>
      <table><thead><tr><th>대출</th><th class="num">잔액</th><th class="num">금리</th>
        <th class="num">월상환</th><th>유형</th></tr></thead><tbody>{loan_rows}</tbody></table>
    </div>"""

    if not standalone:
        return body
    return f"""<!DOCTYPE html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>재무건강</title>
<style>{CSS}</style></head><body>
<h1>🩺 재무건강 대시보드</h1><div class="asof">기준 {d['asof']}</div>
{body}
<div class="fx">진짜 현금·마이너스 여력·대출을 분리 표시합니다. 마이너스 여력은 '쓰면 빚'이라 현금과 다릅니다. 투자·재무 판단은 본인 책임이며, 본 도구는 참고용입니다.</div>
</body></html>"""

CSS = """
:root{--bg:#0f1115;--card:#1a1d24;--line:#2a2e37;--txt:#e6e8ec;--mut:#8b909a;--pos:#22c55e;--neg:#ef4444;--ac:#6366f1}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--txt);font-family:-apple-system,'Segoe UI','Apple SD Gothic Neo',sans-serif;padding:28px}
h1{font-size:20px;margin:0 0 2px}.asof{color:var(--mut);font-size:13px;margin-bottom:20px}.mut{color:var(--mut);font-weight:400;font-size:12px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:14px;margin-bottom:18px}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px}
.card .lbl{color:var(--mut);font-size:13px;margin-bottom:6px}.card .big{font-size:23px;font-weight:700}
.card .sub{color:var(--mut);font-size:12px;margin-top:5px}.pos{color:var(--pos)}.neg{color:var(--neg)}
.panel{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:18px;margin-bottom:16px}
.panel h3{margin:0 0 14px;font-size:15px}
.gauge{position:relative;height:16px;background:var(--line);border-radius:9px;overflow:visible;margin-bottom:10px}
.gfill{height:100%;background:linear-gradient(90deg,#ef4444,#f59e0b);border-radius:9px}
.gtarget{position:absolute;top:-4px;width:2px;height:24px;background:#fff}
.gtext{display:flex;justify-content:space-between;font-size:13px}
.rw{margin-bottom:14px}.rwbar{height:8px;border-radius:5px;width:var(--w);min-width:6px}
.rwbar.green{background:var(--pos)}.rwbar.blue{background:#3b82f6}.rwbar.yellow{background:#f59e0b}.rwbar.red{background:var(--neg)}
.rwlab{display:flex;justify-content:space-between;font-size:13px;margin-top:5px}.rwlab b{font-size:14px}
.rwsub{color:var(--mut);font-size:11px}
table{width:100%;border-collapse:collapse;font-size:13px}
th{text-align:left;color:var(--mut);font-weight:500;padding:7px 5px;border-bottom:1px solid var(--line)}
td{padding:9px 5px;border-bottom:1px solid var(--line)}.num{text-align:right}
.hint{color:var(--mut);font-size:12px;margin-top:8px}.fx{color:var(--mut);font-size:12px;margin-top:18px}
.warn{background:rgba(239,68,68,.12);border:1px solid rgba(239,68,68,.35);border-radius:10px;padding:11px 13px;font-size:12.5px;margin-bottom:14px;line-height:1.5}
.est{color:#f59e0b;font-size:11px;font-weight:600;margin-left:6px}
"""

def main():
    c = load(); d = compute(c)
    OUT.write_text(section_html(d, standalone=True), encoding="utf-8")
    print(f"재무건강 대시보드: {OUT}")
    print(f"순자산 {won(d['net'])} · 현금비중 {d['cash_ratio']*100:.1f}% · runway(현금) {d['rw_cash']:.1f}개월")

if __name__ == "__main__":
    main()
