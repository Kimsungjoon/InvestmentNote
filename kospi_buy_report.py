"""
코스피 매수후보 스크린 리포트 생성
────────────────────────────────────────────────────────────────────────
사용법:
  python3 screener.py --report-kospi
  python3 kospi_buy_report.py
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from screener import (
    KS_PBR_MAX,
    KS_ROE_MIN,
    KS_SEMI_PBR_GOOD,
    KS_SEMI_PBR_MAX,
    KS_SEMI_ROE_MIN,
    scan_kospi,
)

BASE_DIR = Path(__file__).resolve().parent
REPORT_DIR = BASE_DIR / "b_매수후보리스트" / "코스피"
HOLDINGS_FILE = BASE_DIR / "보유주식" / "코스피.md"


def _krw(v, nd=0) -> str:
    if v is None:
        return "N/A"
    return f"{v:,.{nd}f}원"


def _pct(v, nd=1, signed=True) -> str:
    if v is None:
        return "N/A"
    if signed:
        return f"{v:+.{nd}f}%"
    return f"{v:.{nd}f}%"


def _num(v, nd=1, scale=1) -> str:
    if v is None:
        return "N/A"
    return f"{v * scale:.{nd}f}"


def _fmt_shares(n) -> str:
    if n is None:
        return "N/A"
    sign = "+" if n > 0 else ""
    abs_n = abs(n)
    if abs_n >= 1_000_000:
        return f"{sign}{n/1_000_000:.1f}백만"
    if abs_n >= 10_000:
        return f"{sign}{n/10_000:.0f}만"
    return f"{sign}{n:,}"


def _ticker_code(ticker: str) -> str:
    return ticker.replace(".KS", "").replace(".KQ", "")


def load_owned_tickers() -> set[str]:
    if not HOLDINGS_FILE.exists():
        return set()
    text = HOLDINGS_FILE.read_text(encoding="utf-8")
    codes = set(re.findall(r"\b(\d{6})\b", text))
    return {f"{c}.KS" for c in codes} | codes


def compute_trade_levels(a: dict) -> dict:
    price = a.get("price")
    supports = [x for x in (a.get("ma50"), a.get("swing_low_20")) if x]
    stop = min(supports) * 0.98 if supports else None
    target = a.get("target_mean")
    rr = None
    if price and stop and target and price > stop:
        rr = (target - price) / (price - stop)
    return {"stop": stop, "target": target, "rr": rr}


def classify_candidate(a: dict, levels: dict, owned: set[str]) -> tuple[str, str, str]:
    tick = a["ticker"]
    reasons_good: list[str] = []
    reasons_bad: list[str] = []

    if not a.get("qualified"):
        reason = a.get("exclude_reason") or "필수 재무 기준 미달"
        return "제외", reason, reason

    score = a.get("score", 0)
    cat = a.get("category")

    if a.get("pbr") is not None and a["pbr"] <= (
        KS_SEMI_PBR_GOOD if a.get("semi_sector") else 1.0
    ):
        reasons_good.append(f"PBR {_num(a['pbr'], 2)}")
    elif a.get("pbr") is not None and a["pbr"] <= (
        KS_SEMI_PBR_MAX if a.get("semi_sector") else KS_PBR_MAX
    ):
        reasons_good.append(f"PBR {_num(a['pbr'], 2)}")

    tg = a.get("tgt_gap")
    if tg is not None and tg >= 0.15:
        reasons_good.append(f"목표괴리 +{tg * 100:.0f}%")
    elif tg is not None and tg > 0:
        reasons_bad.append(f"목표괴리 +{tg * 100:.0f}% (15% 미달)")

    if a.get("flow_ok"):
        reasons_good.append("기관·외인 5일 순매수")
    else:
        reasons_bad.append("수급 전환 미확인")

    if a.get("above_20ma"):
        reasons_good.append("20MA 위")
    else:
        reasons_bad.append("20MA 아래")

    if a.get("rs_ok"):
        reasons_good.append("RS▲")
    else:
        reasons_bad.append("RS▼")

    rr = levels.get("rr")
    if rr is not None and rr >= 2.0:
        reasons_good.append(f"RR {rr:.1f}")
    elif rr is not None and rr < 1.0:
        reasons_bad.append(f"RR {rr:.1f}")

    pm = a.get("profit_margin")
    if pm is not None and pm <= 0:
        return "제외", "적자(영업이익률 마이너스)", "적자"

    if not a.get("regime_ok"):
        reasons_bad.append("코스피 50MA 이탈")

    if tick in owned or _ticker_code(tick) in owned:
        reasons_bad.append("이미 보유")

    good = " · ".join(reasons_good) if reasons_good else f"점수 {score}/10"
    bad = " · ".join(reasons_bad) if reasons_bad else ""

    if cat == "우선 후보" and a.get("flow_ok") and score >= 7:
        return "우선 검토", good, bad

    if cat == "우선 후보":
        if not bad:
            bad = "수급·레짐 추가 확인"
        return "관찰 필요", good, bad

    if cat == "관찰 후보":
        if not bad:
            bad = "점수·수급·밸류 중 일부 약점"
        return "관찰 필요", good, bad

    if not bad:
        bad = f"점수 {score}/10 (5점 미만)"
    return "조건 미달", good, bad


def _rr_str(rr) -> str:
    if rr is None:
        return "N/A"
    if abs(rr) < 0.05:
        return "≈0"
    return f"{rr:.1f}"


def enrich_scan(scan: dict, owned: set[str]) -> tuple[list[dict], list[dict], list[dict]]:
    passed_items = []
    for a in scan["passed"]:
        levels = compute_trade_levels(a)
        classification, pick_reason, miss_reason = classify_candidate(a, levels, owned)
        passed_items.append({
            "a": a,
            "levels": levels,
            "classification": classification,
            "pick_reason": pick_reason,
            "miss_reason": miss_reason,
        })

    order = {"우선 검토": 0, "관찰 필요": 1, "조건 미달": 2, "제외": 3}
    passed_items.sort(
        key=lambda x: (
            order.get(x["classification"], 9),
            -(x["a"].get("score") or 0),
            -(x["levels"]["rr"] or -999),
        )
    )

    weak_items = []
    for a in scan["weak"]:
        levels = compute_trade_levels(a)
        _, pick_reason, miss_reason = classify_candidate(a, levels, owned)
        weak_items.append({
            "a": a,
            "levels": levels,
            "classification": "조건 미달",
            "pick_reason": pick_reason,
            "miss_reason": miss_reason or f"점수 {a.get('score')}/10",
        })

    excluded_items = []
    for a in scan["excluded"]:
        levels = compute_trade_levels(a)
        _, pick_reason, miss_reason = classify_candidate(a, levels, owned)
        excluded_items.append({
            "a": a,
            "levels": levels,
            "classification": "제외",
            "pick_reason": pick_reason,
            "miss_reason": miss_reason,
        })

    return passed_items, weak_items, excluded_items


def _top_pick(passed: list[dict], owned: set[str]) -> dict | None:
    """초보 요약용 대표 1종목 — 우선 검토 우선, 없으면 관찰(미보유)."""
    for want in ("우선 검토", "관찰 필요"):
        for c in passed:
            tick = c["a"]["ticker"]
            if c["classification"] == want and tick not in owned \
                    and _ticker_code(tick) not in owned:
                return c
    return None


def _beginner_summary(scan: dict, passed: list[dict], owned: set[str]) -> list[str]:
    """리포트 맨 위 초보용 한눈 요약 — 코스피(가치투자·자산방어) 눈높이."""
    regime = scan["regime"]
    ok = regime["ok"]

    if ok:
        market = "🟢 **코스피가 상승세예요(50일 평균선 위).** 저평가 우량주를 차분히 볼 만한 때."
        todo = "급하게 사지 말고, **싸고 튼튼한 회사**를 골라 조금씩 모아가기."
    else:
        market = ("🔴 **코스피가 약해요(50일 평균선 아래).** 코스피 계좌는 "
                  "'돈 지키기'가 1순위라 더 신중할 때예요.")
        todo = "**서두르지 않기.** 지수가 회복될 때까지 기다리고, 아래는 미리 봐두는 후보예요."

    lines = [
        "## 📌 초보용 한눈 요약",
        "",
        f"- **지금 시장:** {market}",
        f"- **오늘 할 일:** {todo}",
        "- **이 계좌 성격:** 미국(나스닥)과 달리 **가치투자·자산 방어**예요. "
        "짧게 사고파는 게 아니라 **싸고 좋은 회사를 오래** 들고 가는 방식.",
    ]

    pick = _top_pick(passed, owned)
    if pick:
        a = pick["a"]
        lines.append(
            f"- **가장 눈여겨볼 종목:** {a['name']} ({_ticker_code(a['ticker'])}) — "
            f"현재가 {_krw(a.get('price'))}, 스크리너 점수 {a.get('score', 0)}/10점."
        )
        lines.append(
            "  - 쉽게: 재무가 튼튼하고 저평가 신호가 나온 회사예요. "
            "**단, 위 '지금 시장'이 🔴이면 지금 사기보다 관찰만 하세요.**"
        )
    else:
        lines.append("- **가장 눈여겨볼 종목:** 오늘은 바로 살 만한 종목이 없어요. 관망.")

    lines.extend([
        "",
        "> PBR·목표괴리 같은 용어는 맨 아래 **『🔤 용어 쉽게 풀이』** 참고.",
        "",
    ])
    return lines


def _glossary_section() -> list[str]:
    """리포트 맨 아래 초보용 용어 풀이 — 코스피(가치투자)."""
    return [
        "---",
        "",
        "## 🔤 용어 쉽게 풀이",
        "",
        "| 용어 | 쉽게 말하면 |",
        "|------|-------------|",
        "| **KOSPI 50MA** | 코스피 지수의 최근 50일 평균. 이 위면 상승세, 아래면 약세. |",
        "| **PBR** | 회사 순자산 대비 주가. **1보다 낮으면** 자산보다 싸게 거래 = 저평가 신호. |",
        "| **PER** | 주가가 이익의 몇 배인지. 낮을수록 이익 대비 싸다는 뜻. |",
        "| **ROE** | 회사가 자기 돈으로 돈 버는 효율. **높을수록** 잘 버는 회사. |",
        "| **목표괴리** | 증권사 목표가가 지금 주가보다 얼마나 위인지. +20%면 20% 더 오를 여지로 봄. |",
        "| **수급(기관·외인)** | 큰손(기관·외국인)이 사는 중(▲)인지 파는 중(▼)인지. ▲면 긍정적. |",
        "| **스크리너 점수** | 밸류·수급·실적을 종합한 10점 만점 점수. 높을수록 조건이 좋음. |",
        "| **RR(손익비)** | **벌 수 있는 돈 ÷ 잃을 수 있는 돈.** 클수록 유리. |",
        "| **목표 / 방어선(손절)** | 목표=이익 실현 가격. 방어선=여기 깨지면 손해 줄이고 파는 선. |",
        "| **안전마진** | 회사 실제 가치보다 싸게 사서 확보하는 '여유분'. 가치투자의 핵심. |",
        "",
    ]


def _detail_block(item: dict, owned: set[str], rank_note: str = "") -> str:
    a = item["a"]
    lv = item["levels"]
    tick = a["ticker"]
    code = _ticker_code(tick)
    owned_tag = " *(보유)*" if tick in owned or code in owned else ""
    title = rank_note or item["classification"]

    lines = [
        f"#### {a['name']} ({code}) — {title}{owned_tag}",
        "",
        "| 항목 | 값 |",
        "|------|-----|",
        f"| 현재가 / RSI | {_krw(a['price'])} / {_num(a.get('rsi'), 1)} |",
        f"| PBR / PER / ROE | {_num(a.get('pbr'), 2)} / {_num(a.get('per'), 1)} / "
        f"{_num(a.get('roe'), 1, 100)}% |",
        f"| 부채비율 / 매출성장 | "
        f"{_num(a.get('dte'), 0)}%{' (금융)' if a.get('financial_sector') else ''} / "
        f"{_num(a.get('rev_growth'), 1, 100)}% |",
        f"| 목표괴리 / 스크리너점수 | "
        f"{_pct(a['tgt_gap'] * 100 if a.get('tgt_gap') is not None else None, 0)} / "
        f"**{a.get('score', 0)}/10** |",
        f"| 5일 수급 (기관/외인) | {_fmt_shares(a.get('flow_5d'))} "
        f"({_fmt_shares(a.get('inst_5d'))} / {_fmt_shares(a.get('foreign_5d'))}) |",
        f"| 20일수익률 / RS | {_pct(a.get('ret_20d'))} / "
        f"{'RS▲' if a.get('rs_ok') else 'RS▼'} |",
        f"| 손절 / 목표 / RR | {_krw(lv['stop'])} / {_krw(lv['target'])} / "
        f"**{_rr_str(lv['rr'])}** |",
        "",
        f"**후보 선정 이유:** {item['pick_reason']}.",
        "",
    ]
    if item["miss_reason"]:
        lines.append(f"**미충족:** {item['miss_reason']}")
        lines.append("")

    if tick in owned or code in owned:
        lines.append("- **보유:** `보유주식/코스피.md` 손절·목표가 유지")
    elif lv["stop"] and lv["target"]:
        ma20 = a.get("ma20")
        entry = _krw(ma20) if ma20 else _krw(a.get("price"))
        lines.append(f"- **진입 검토:** {entry}(20MA) 지지 + 수급 유지 확인")
        lines.append(f"- **방어선:** {_krw(lv['stop'])} | **목표:** {_krw(lv['target'])}")
    lines.append("")
    return "\n".join(lines)


def generate_markdown(
    scan: dict,
    passed: list[dict],
    weak: list[dict],
    excluded: list[dict],
    owned: set[str],
) -> str:
    today = date.today().isoformat()
    regime = scan["regime"]
    regime_flag = "🟢 강세" if regime["ok"] else "🔴 약세"

    groups = {"우선 검토": [], "관찰 필요": [], "조건 미달": [], "제외": []}
    for c in passed:
        groups[c["classification"]].append(c)
    for c in weak:
        groups["조건 미달"].append(c)
    for c in excluded[:12]:
        groups["제외"].append(c)

    priority = groups["우선 검토"]
    watch = groups["관찰 필요"]
    weak_g = groups["조건 미달"]
    excluded_g = groups["제외"]

    new_watch = [c for c in watch if c["a"]["ticker"] not in owned
                 and _ticker_code(c["a"]["ticker"]) not in owned]

    lines = [
        f"# 코스피 매수 후보 리스트 ({today})",
        "",
        "> 적용 기준: `screener.py` (네이버 금융 PER/PBR/ROE/부채·목표주가 + 네이버 수급) · `코스피_최종분류_프롬프트.md`",
        f"> 스캔: `python3 screener.py --report-kospi` ({scan['universe_size']}종목, {today} 실행)",
        "> 데이터: **네이버 금융** 우선 (Yahoo는 누락 필드만 보완) · 차트는 Yahoo",
        ">",
        "> 전략: **가치투자 + 자산 방어** | 장기 보유·저평가 우량주",
        f"> **반도체·IT부품(6종):** PBR≤{KS_SEMI_PBR_MAX} · ROE≥{KS_SEMI_ROE_MIN*100:.0f}% "
        f"| **일반:** PBR≤{KS_PBR_MAX} · ROE≥{KS_ROE_MIN*100:.0f}%",
        "",
        "---",
        "",
        "## 📊 시장 레짐",
        "",
        "| 항목 | 상태 |",
        "|------|------|",
        f"| KOSPI | {_krw(regime['price'])} / 50MA {_krw(regime['ma50'])} "
        f"→ **{regime['reason']}** {regime_flag} |",
        f"| KOSPI 20일 | {_pct(regime.get('ret_20d'))} |",
        f"| 스크리너 | 우선 후보 **{len(scan['priority'])}** · "
        f"관찰 후보 **{len(scan['watchlist'])}** · "
        f"조건 미달 **{len(scan['weak'])}** · 제외 **{len(scan['excluded'])}** |",
        "",
    ]

    lines.extend(_beginner_summary(scan, passed, owned))

    if passed:
        lines.extend([
            "---",
            "",
            f"## ✅ 스크리너 상위 {len(passed)}종목 (점수 5+)",
            "",
            "| # | 종목 | 코드 | 점수 | PBR | 목표괴리 | 수급 | RR | 최종분류 |",
            "|---|------|------|-----:|----:|--------:|:----:|---:|---------|",
        ])
        for i, c in enumerate(passed, 1):
            a = c["a"]
            code = _ticker_code(a["ticker"])
            tg = f"+{a['tgt_gap']*100:.0f}%" if a.get("tgt_gap") else "N/A"
            flow = "▲" if a.get("flow_ok") else "▼"
            owned_mark = " *(보유)*" if a["ticker"] in owned or code in owned else ""
            semi_mark = " *(반도체)*" if a.get("semi_sector") and not owned_mark else ""
            lines.append(
                f"| {i} | {a['name']} | {code} | **{a.get('score')}/10** | "
                f"{_num(a.get('pbr'), 2)} | {tg} | {flow} | "
                f"{_rr_str(c['levels']['rr'])} | {c['classification']}{owned_mark}{semi_mark} |"
            )

        watch_line = ""
        if priority:
            watch_line = f"**우선 검토: {', '.join(_ticker_code(c['a']['ticker']) for c in priority)}**"
        elif new_watch:
            watch_line = f"**신규 1순위(관찰): {_ticker_code(new_watch[0]['a']['ticker'])}**"
        if watch_line:
            lines.extend(["", watch_line, ""])

    lines.extend(["", "---", "", "## 최종분류별 상세", ""])

    if priority:
        lines.extend(["### ✅ 우선 검토", ""])
        for c in priority:
            lines.append(_detail_block(c, owned))

    if watch:
        lines.extend(["### 👀 관찰 필요", ""])
        top_new = (
            _ticker_code(new_watch[0]["a"]["ticker"]) if new_watch else None
        )
        for c in watch:
            code = _ticker_code(c["a"]["ticker"])
            note = "신규 1순위" if top_new and code == top_new else ""
            lines.append(_detail_block(c, owned, note))

    if weak_g:
        lines.extend(["### ⚠️ 조건 미달", ""])
        lines.append("| 종목 | 핵심 이유 | 다음 확인 |")
        lines.append("|------|----------|----------|")
        for c in weak_g[:10]:
            code = _ticker_code(c["a"]["ticker"])
            lines.append(
                f"| **{code}** | {c['miss_reason']} | 수급·실적·밸류 재확인 |"
            )
        lines.append("")

    if excluded_g:
        lines.extend(["### ❌ 제외", ""])
        lines.append("| 종목 | 핵심 이유 |")
        lines.append("|------|----------|")
        for c in excluded_g[:12]:
            code = _ticker_code(c["a"]["ticker"])
            lines.append(f"| **{code}** | {c['miss_reason']} |")
        if len(scan["excluded"]) > 12:
            lines.append(f"| … | 외 {len(scan['excluded']) - 12}종목 |")
        lines.append("")

    lines.extend([
        "---",
        "",
        "## 선정 요약",
        "",
        "| 게이트 | 기준 | 통과 |",
        "|--------|------|------|",
        f"| 재무(일반) | ROE≥{KS_ROE_MIN*100:.0f}% · 부채≤200% · PBR≤{KS_PBR_MAX} · PER≤22 | "
        f"{sum(1 for a in scan['results'] if a.get('qualified'))}/"
        f"{len(scan['results'])} |",
        f"| 재무(반도체) | ROE≥{KS_SEMI_ROE_MIN*100:.0f}% · PBR≤{KS_SEMI_PBR_MAX} · PER≤22 | "
        f"{sum(1 for a in scan['results'] if a.get('qualified') and a.get('semi_sector'))}/6 |",
        f"| 스크리너 점수 7+ | 우선 후보 | {len(scan['priority'])} |",
        f"| 스크리너 점수 5~6 | 관찰 후보 | {len(scan['watchlist'])} |",
        f"| **최종 우선 검토** | 점수7+ · 수급▲ | **{len(priority)}** |",
        f"| **레짐** | KOSPI > 50MA | {'✅' if regime['ok'] else '❌'} |",
        "",
        "> ⚠️ 코스피는 **자산 방어** 우선. 수급·실적·밸류 중 하나라도 훼손되면 보수적으로 판단.",
        "",
    ])

    lines.extend(["## 다음 액션", ""])
    if not regime["ok"]:
        lines.append("1. **KOSPI 50MA 회복** → 레짐 확인 후 비중 확대 검토")
    if priority:
        top = priority[0]
        lines.append(
            f"{'2' if not regime['ok'] else '1'}. **우선:** "
            f"{top['a']['name']} — 수급·실적 발표일 확인"
        )
    elif new_watch:
        top = new_watch[0]
        lines.append(
            f"1. **관찰 1순위:** {_ticker_code(top['a']['ticker'])} "
            f"— 5일 기관·외인 순매수 유지 확인"
        )
    lines.append("")

    lines.extend(_glossary_section())

    return "\n".join(lines)


def run_report(verbose: bool = True) -> Path:
    if verbose:
        print("  코스피 매수후보 리포트 생성 중...")
    owned = load_owned_tickers()
    scan = scan_kospi(verbose=verbose)
    passed, weak, excluded = enrich_scan(scan, owned)
    md = generate_markdown(scan, passed, weak, excluded, owned)

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = REPORT_DIR / f"코스피_매수후보_{date.today().isoformat()}.md"
    out_path.write_text(md, encoding="utf-8")

    if verbose:
        priority_n = sum(1 for c in passed if c["classification"] == "우선 검토")
        watch_n = sum(1 for c in passed if c["classification"] == "관찰 필요")
        print(f"\n  ✅ 리포트 저장: {out_path}")
        print(f"     상위 {len(passed)}종목 · 우선 {priority_n} · 관찰 {watch_n} · "
              f"제외 {len(scan['excluded'])}")
    return out_path


if __name__ == "__main__":
    run_report()
