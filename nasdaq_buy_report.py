"""
나스닥 매수후보 스크린 리포트 생성
────────────────────────────────────────────────────────────────────────
사용법:
  python3 screener.py --report
  python3 nasdaq_buy_report.py
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from screener import scan_nasdaq

BASE_DIR = Path(__file__).resolve().parent
REPORT_DIR = BASE_DIR / "b_매수후보리스트" / "나스닥"
HOLDINGS_FILE = BASE_DIR / "보유주식" / "나스닥.md"


def _usd(v, nd=0) -> str:
    if v is None:
        return "N/A"
    return f"${v:,.{nd}f}"


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


def load_owned_tickers() -> set[str]:
    if not HOLDINGS_FILE.exists():
        return set()
    text = HOLDINGS_FILE.read_text(encoding="utf-8")
    return {m.group(1) for m in re.finditer(r"\|\s*[^|]+\|\s*([A-Z]{1,5})\s*\|", text)}


def compute_trade_levels(a: dict) -> dict:
    price = a.get("price")
    supports = [x for x in (a.get("ma50"), a.get("swing_low_20")) if x]
    stop = min(supports) * 0.98 if supports else None
    target = a.get("target_mean")
    rr = None
    if price and stop and target and price > stop:
        rr = (target - price) / (price - stop)
    return {"stop": stop, "target": target, "rr": rr}


def _peg_label(a: dict) -> tuple[str, str]:
    fwd = a.get("peg_forward")
    trail = a.get("peg_trailing")
    if fwd is not None:
        extra = f" (trail {_num(trail, 1)})" if trail is not None else ""
        return _num(fwd, 1), f"**{_num(fwd, 1)}**{extra}"
    if trail is not None:
        return _num(trail, 1), _num(trail, 1)
    return "N/A", "N/A"


def _volume_note(a: dict) -> str:
    vr = a.get("vol_ratio_5d")
    et = a.get("entry_type")
    if et == "돌파" and vr and vr >= 1.1:
        return "거래량 동반 급증"
    if et == "눌림목" and vr and vr <= 1.05:
        return "거래량 수축(눌림 정석)"
    if vr and vr < 0.8:
        return "거래량 급감"
    return "거래량 보통"


def classify_candidate(a: dict, levels: dict, regime_ok: bool, owned: set[str]) -> tuple[str, str, str]:
    """최종분류, 선정 이유, 미충족 사유."""
    tick = a["ticker"]
    price = a.get("price") or 0
    target = levels.get("target")
    stop = levels.get("stop")
    rr = levels.get("rr")
    reasons_good: list[str] = []
    reasons_bad: list[str] = []

    if a.get("entry_type"):
        reasons_good.append(a["entry_type"])
    if a.get("rs_ok"):
        reasons_good.append("RS▲")
    if a.get("aligned"):
        reasons_good.append("정배열")
    elif a.get("entry_type"):
        reasons_bad.append("정배열 X")

    peg_fwd = a.get("peg_forward")
    if peg_fwd is not None and peg_fwd <= 1.0:
        reasons_good.append(f"fwd PEG {peg_fwd:.1f}")
    elif peg_fwd is not None and peg_fwd <= 2.5:
        reasons_good.append(f"fwd PEG {peg_fwd:.1f}")

    rg = a.get("rev_growth")
    if rg is not None and rg >= 0.15:
        reasons_good.append(f"성장 {_num(rg, 0, 100)}%")

    good = " · ".join(reasons_good) if reasons_good else "펀더·기술 통과"

    if target and price and target < price * 0.99:
        msg = f"애널 목표 {_usd(target)} < 현재 {_usd(price)}"
        if rr is not None:
            msg += f"(RR {rr:.1f})"
        return "제외", msg, msg

    if rr is not None and rr < -0.05:
        msg = f"목표가가 현재가 아래(RR {rr:.1f})"
        return "제외", msg, msg

    fh = a.get("from_high")
    if fh is not None and fh < -25:
        reasons_bad.append(f"고점 대비 {_pct(fh)} — 깊은 하락")
        bad = " · ".join(reasons_bad)
        return "조건 미달", good, bad

    if not a.get("trend_ok"):
        return "제외", "추세 훼손(50MA 이탈·우상향 X)", "추세 훼손"

    rsi = a.get("rsi")
    if rsi is not None and rsi > 82:
        return "제외", f"RSI 과열({rsi:.0f})", "과열"

    if rr is not None and rr < 0.5:
        reasons_bad.append(f"RR {rr:.1f}(목표가 근접)")
    elif rr is not None and rr < 2.0:
        reasons_bad.append(f"RR {rr:.1f} 미달")

    if not regime_ok:
        reasons_bad.append("QQQ 레짐 약세")

    vol_note = _volume_note(a)
    vol_ok = (
        a.get("entry_type") == "돌파"
        or (a.get("vol_ratio_5d") or 0) >= 1.0
    )
    if not vol_ok and a.get("entry_type") == "눌림목":
        reasons_bad.append(vol_note)

    per = a.get("per")
    if per is not None and per > 60:
        reasons_bad.append(f"trailing PER {per:.0f}")

    if tick in owned:
        reasons_bad.append("이미 보유(신규 매수 아님)")

    ret_20 = a.get("ret_20d")
    if ret_20 is not None and ret_20 < 0 and a.get("rs_ok"):
        reasons_bad.append(f"20일 절대 수익률 {_pct(ret_20)}")

    bad = " · ".join(reasons_bad) if reasons_bad else ""

    if (
        regime_ok
        and a.get("aligned")
        and a.get("rs_ok")
        and rr is not None
        and rr >= 2.0
        and vol_ok
        and tick not in owned
    ):
        return "우선 검토", good, bad

    if a.get("trend_ok") and a.get("rs_ok") and (rr is None or rr >= 1.5):
        if bad:
            return "관찰 필요", good, bad
        return "관찰 필요", good, "추가 확인 필요"

    if not bad:
        bad = "손익비·거래량·레짐 중 약점"
    return "조건 미달", good, bad


def _rr_str(rr) -> str:
    if rr is None:
        return "N/A"
    if abs(rr) < 0.05:
        return "≈0"
    return f"{rr:.1f}"


def _sort_passed(candidates: list[dict]) -> list[dict]:
    order = {"우선 검토": 0, "관찰 필요": 1, "조건 미달": 2, "제외": 3}

    def score(x):
        a = x["a"]
        rr = x["levels"]["rr"] or -999
        aligned = 1 if a.get("aligned") else 0
        fh = a.get("from_high") or -999
        return (aligned, rr, fh)

    return sorted(
        candidates,
        key=lambda x: (order.get(x["classification"], 9),) + tuple(-s for s in score(x)),
    )


def enrich_scan(scan: dict, owned: set[str]) -> list[dict]:
    regime_ok = scan["regime"]["ok"]
    out = []
    for a in scan["passed"]:
        levels = compute_trade_levels(a)
        classification, pick_reason, miss_reason = classify_candidate(
            a, levels, regime_ok, owned,
        )
        out.append({
            "a": a,
            "levels": levels,
            "classification": classification,
            "pick_reason": pick_reason,
            "miss_reason": miss_reason,
        })
    return _sort_passed(out)


def _detail_block(item: dict, owned: set[str], rank_note: str = "") -> str:
    a = item["a"]
    lv = item["levels"]
    tick = a["ticker"]
    _, peg_disp = _peg_label(a)
    aligned = "✅" if a.get("aligned") else "❌"
    owned_tag = " *(보유)*" if tick in owned else ""
    title_extra = rank_note or item["classification"]

    lines = [
        f"#### {a['name']} ({tick}) — {title_extra}{owned_tag}",
        "",
        "| 항목 | 값 |",
        "|------|-----|",
        f"| RSI / 5MA이격 | {_num(a.get('rsi'), 1)} / {_pct(a.get('ext_5ma'))} |",
        f"| 고점대비 / 20일수익률 | {_pct(a.get('from_high'))} / "
        f"**{_pct(a.get('ret_20d'))}** ({'RS▲' if a.get('rs_ok') else 'RS▼'}) |",
        f"| 정배열 | {aligned} |",
        f"| PER / fwdPER / fwd PEG | {_num(a.get('per'), 0)} / "
        f"{_num(a.get('forward_per'), 0)} / {peg_disp} |",
        f"| ROE / 성장 / 부채 | {_num(a.get('roe'), 0, 100)}% / "
        f"{_num(a.get('rev_growth'), 0, 100)}% / {_num(a.get('debt_equity'), 0)}% |",
        f"| 손절 / 목표 / RR | {_usd(lv['stop'])} / {_usd(lv['target'])} / **{_rr_str(lv['rr'])}** |",
        "",
        f"**후보 선정 이유:** {item['pick_reason']}.",
        "",
    ]
    if item["miss_reason"]:
        lines.append(f"**미충족:** {item['miss_reason']}")
        lines.append("")

    if tick in owned:
        lines.append("- **보유:** `보유주식/나스닥.md` 손절·목표가 유지")
        lines.append("- **추가매수:** 레짐 회복 + 20MA 지지 확인 후만")
    elif lv["stop"] and lv["target"]:
        ma20 = a.get("ma20")
        entry_hint = _usd(ma20) if ma20 else _usd(a.get("price"))
        lines.append(f"- **진입:** {entry_hint}(20MA) 지지 + QQQ 50MA 회복 후")
        lines.append(f"- **손절:** {_usd(lv['stop'])} | **목표:** {_usd(lv['target'])}")
    lines.append("")
    return "\n".join(lines)


def generate_markdown(scan: dict, candidates: list[dict], owned: set[str]) -> str:
    today = date.today().isoformat()
    regime = scan["regime"]
    regime_flag = "🟢 진입 허용" if regime["ok"] else "🔴 진입 보류"
    regime_word = "강세" if regime["ok"] else "약세 (50MA 이탈)"

    groups = {
        "우선 검토": [],
        "관찰 필요": [],
        "조건 미달": [],
        "제외": [],
    }
    for c in candidates:
        groups[c["classification"]].append(c)

    priority = groups["우선 검토"]
    watch = groups["관찰 필요"]
    weak = groups["조건 미달"]
    excluded = groups["제외"]

    rr2_count = sum(
        1 for c in candidates
        if c["levels"]["rr"] is not None and c["levels"]["rr"] >= 2
    )

    new_watch = [
        c for c in watch
        if c["a"]["ticker"] not in owned
    ]
    watch_line = ""
    if priority:
        watch_line = f"**우선 검토: {', '.join(c['a']['ticker'] for c in priority)}**"
    elif new_watch:
        top = new_watch[0]["a"]["ticker"]
        watch_line = f"**신규 매수 1순위(관찰): {top}**"
    held_rr = [c for c in candidates if c["a"]["ticker"] in owned and (c["levels"]["rr"] or 0) >= 2]
    if held_rr:
        watch_line += f" | **보유 재검토: {held_rr[0]['a']['ticker']}** (RR≥2)"

    lines = [
        f"# 나스닥 매수 후보 리스트 ({today})",
        "",
        "> 적용 기준: `screener.py` (기술 + 펀더 · **forward PEG 우선**) · `나스닥_최종분류_프롬프트.md`",
        f"> 스캔: `python3 screener.py --report` ({scan['universe_size']}종목, {today} 실행)",
        ">",
        "> 전략: **추세추종 + 단기 스윙** | 보유 2주~2개월",
        "",
        "---",
        "",
        "## 📊 시장 레짐",
        "",
        "| 항목 | 상태 |",
        "|------|------|",
        f"| QQQ | {_usd(regime['price'], 2)} / 50MA {_usd(regime['ma50'], 2)} "
        f"→ **{regime_word}** {regime_flag} |",
        f"| QQQ 20일 | {_pct(regime.get('ret_20d'))} |",
        f"| 스크리너 | 눌림목 **{len(scan['pullbacks'])}** · "
        f"돌파 **{len(scan['breakouts'])}** · "
        f"에너지응축 **{len(scan['squeezes'])}** "
        f"(펀더 통과 **{len(candidates)}**종목) |",
        "",
    ]
    if not regime["ok"]:
        lines.append(f"> ⚠ QQQ 50MA({_usd(regime['ma50'], 0)}) 회복 전까지 **실탄 매수 보류**. "
                     "아래는 대기·재검토 리스트.")
        lines.append("")

    lines.extend([
        "---",
        "",
        f"## ✅ 스크리너 통과 {len(candidates)}종목",
        "",
        "| # | 종목 | 티커 | 유형 | 현재가 | fwd PEG | RR | 최종분류 |",
        "|---|------|------|------|-------:|--------:|---:|---------|",
    ])

    for i, c in enumerate(candidates, 1):
        a = c["a"]
        peg, peg_bold = _peg_label(a)
        owned_mark = " *(보유)*" if a["ticker"] in owned else ""
        lines.append(
            f"| {i} | {a['name']} | {a['ticker']} | {a['entry_type']} | "
            f"{_usd(a['price'], 2)} | {peg_bold} | {_rr_str(c['levels']['rr'])} | "
            f"{c['classification']}{owned_mark} |"
        )

    lines.extend([
        "",
        f"**우선 검토(§1: RR≥2·정배열·RS·거래량): {len(priority)}종목**"
        f" — 레짐 {'🟢' if regime['ok'] else '🔴'}"
        f" + RR 2 미달 다수" if len(priority) == 0 else "",
    ])
    if watch_line:
        lines.append(watch_line)
    lines.extend(["", "---", "", "## 최종분류별 상세", ""])

    if priority:
        lines.extend(["### ✅ 우선 검토", ""])
        for c in priority:
            lines.append(_detail_block(c, owned))

    if watch:
        lines.extend(["### 👀 관찰 필요", ""])
        top_new = next(
            (c["a"]["ticker"] for c in watch if c["a"]["ticker"] not in owned),
            None,
        )
        for c in watch:
            note = "신규 1순위" if top_new and c["a"]["ticker"] == top_new else ""
            lines.append(_detail_block(c, owned, note))

    if weak:
        lines.extend(["### ⚠️ 조건 미달", ""])
        lines.append("| 종목 | 핵심 이유 | 다음 확인 |")
        lines.append("|------|----------|----------|")
        for c in weak:
            a = c["a"]
            ma20 = _usd(a.get("ma20"), 0) if a.get("ma20") else "20MA"
            next_step = f"{ma20} 재지지"
            if c["levels"]["rr"] is not None and c["levels"]["rr"] < 1.5:
                next_step = "RR·목표가 개선 확인"
            lines.append(
                f"| **{a['ticker']}** | {c['miss_reason']} | {next_step} |"
            )
        lines.append("")

    if excluded:
        lines.extend(["### ❌ 제외", ""])
        lines.append("| 종목 | 핵심 이유 |")
        lines.append("|------|----------|")
        for c in excluded:
            lines.append(f"| **{c['a']['ticker']}** | {c['miss_reason']} |")
        lines.append("")

    fund_ok_n = len(candidates)
    lines.extend([
        "---",
        "",
        f"## 선정 요약 — {fund_ok_n}종목 게이트",
        "",
        "| 게이트 | 기준 | 통과 |",
        "|--------|------|------|",
        f"| 추세 | 50MA 위 + 우상향 | {fund_ok_n}/{fund_ok_n} ✅ |",
        f"| 진입유형 | 눌림목 / 돌파 / 에너지응축 | {fund_ok_n}/{fund_ok_n} ✅ |",
        f"| RS | QQQ 대비 20일 우위 | "
        f"{sum(1 for c in candidates if c['a'].get('rs_ok'))}/{fund_ok_n} ✅ |",
        f"| 펀더 | ROE≥7% · 성장≥5% · **fwd PEG≤2.5** · trail PER≤80 | {fund_ok_n}/{fund_ok_n} ✅ |",
        f"| **최종분류 RR≥2** | 애널 목표·손절 기준 | **{rr2_count}/{fund_ok_n}** |",
        f"| **레짐** | QQQ > 50MA | "
        f"{'✅' if regime['ok'] else '❌'} |",
        "",
        f"{scan['tech_passed_count']}종목 기술 통과 → "
        f"펀더 미달 {len(scan['fund_rejected'])}종목 → **{fund_ok_n}종목**",
        "",
    ])

    if scan["fund_rejected"]:
        lines.extend([
            "## ⬜ 기술 통과 · 펀더 미달 "
            f"({len(scan['fund_rejected'])}종목)",
            "",
            "| 종목 | 유형 | 탈락 |",
            "|------|------|------|",
        ])
        for a in sorted(
            scan["fund_rejected"],
            key=lambda x: x["ticker"],
        )[:15]:
            fail = ",".join(a.get("fund_fail") or ["펀더X"])
            lines.append(
                f"| {a['name']} | {a.get('tech_entry_type')} | {fail} |"
            )
        if len(scan["fund_rejected"]) > 15:
            lines.append(f"| … | | 외 {len(scan['fund_rejected']) - 15}종목 |")
        lines.append("")

    lines.extend([
        "---",
        "",
        "## 다음 액션",
        "",
        f"1. **QQQ {_usd(regime['ma50'], 0)}(50MA) 회복** → 레짐 "
        f"{'🟢' if regime['ok'] else '확인'}",
    ])
    if new_watch:
        top = new_watch[0]
        lines.append(
            f"2. **신규:** {top['a']['ticker']} "
            f"{_usd(top['a'].get('ma20') or top['a']['price'], 0)}(20MA) 지지 "
            f"+ RR 2.0 재확인"
        )
    if owned:
        lines.append(
            f"3. **보유 {', '.join(sorted(owned))}:** "
            "`보유주식/나스닥.md` 손절·목표 유지"
        )
    if weak:
        tickers = "·".join(c["a"]["ticker"] for c in weak[:4])
        lines.append(f"4. {tickers} — **RR·목표가 개선** 전까지 관망")
    lines.append("")

    return "\n".join(lines)


def run_report(verbose: bool = True) -> Path:
    if verbose:
        print("  나스닥 매수후보 리포트 생성 중...")
    owned = load_owned_tickers()
    scan = scan_nasdaq(verbose=verbose)
    candidates = enrich_scan(scan, owned)
    md = generate_markdown(scan, candidates, owned)

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = REPORT_DIR / f"나스닥_매수후보_{date.today().isoformat()}.md"
    out_path.write_text(md, encoding="utf-8")

    if verbose:
        print(f"\n  ✅ 리포트 저장: {out_path}")
        print(f"     통과 {len(candidates)}종목 · "
              f"우선 {sum(1 for c in candidates if c['classification'] == '우선 검토')} · "
              f"관찰 {sum(1 for c in candidates if c['classification'] == '관찰 필요')}")
    return out_path


if __name__ == "__main__":
    run_report()
