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

from screener import (
    ND_FAIR_PEG,
    ND_PEG_MAX,
    ND_PER_MAX,
    ND_REGIME_SOFT_PCT,
    ND_REV_GROWTH_MIN,
    ND_ROE_MIN,
    ND_TGT_ATR_MULT,
    scan_nasdaq,
)

BASE_DIR = Path(__file__).resolve().parent
REPORT_DIR = BASE_DIR / "b_매수후보리스트" / "나스닥"
HOLDINGS_FILE = BASE_DIR / "보유주식" / "나스닥.md"

# S&P500 GICS 섹터 ETF — 리포트·§1 보완 시 **한글명 (코드)** 형식 사용
SECTOR_ETF_GLOSSARY: dict[str, tuple[str, str]] = {
    "XLK": ("기술·반도체", "NVDA·MSFT·AAPL·AI·소프트웨어"),
    "XLY": ("임의소비·여행", "AMZN·TSLA·여행·숙박·배달·쇼핑"),
    "XLV": ("헬스케어·바이오", "제약·병원·헬스 IT"),
    "XLI": ("산업·방산", "항공·물류·방위·기계·HWM"),
    "XLF": ("금융", "은행·증권·핀테크·SOFI"),
    "XLC": ("커뮤니케이션", "META·GOOGL·넷플릭스·광고·RDDT"),
    "XLE": ("에너지", "석유·가스"),
    "XLP": ("필수소비", "생필품·식품·음료"),
    "XLU": ("유틸리티", "전력·가스"),
    "XLRE": ("부동산", "리츠"),
    "XLB": ("소재", "화학·금속"),
}


def _sector_etf_legend_md() -> list[str]:
    """§1 섹터 표 하단·용어 설명용."""
    lines = [
        "> **섹터 ETF란?** S&P500 업종별 상장지수펀드 코드. "
        "아래 §1·본문에서는 **한글 섹터명 (코드)** 순으로 씁니다.",
        "",
        "| 코드 | 한글 섹터 | 대표 업종 |",
        "|------|----------|----------|",
    ]
    for code, (name, examples) in SECTOR_ETF_GLOSSARY.items():
        lines.append(f"| {code} | **{name}** | {examples} |")
    return lines


def _top_pick(candidates: list[dict], owned: set[str]) -> dict | None:
    """초보 요약용 대표 1종목 — 우선 검토 우선, 없으면 관찰(미보유)."""
    for want in ("우선 검토", "관찰 필요"):
        for c in candidates:
            if c["classification"] == want and c["a"]["ticker"] not in owned:
                return c
    return None


def _beginner_summary(scan: dict, candidates: list[dict], owned: set[str],
                      level: str) -> list[str]:
    """리포트 맨 위 초보용 한눈 요약 — 지금 시장·오늘 할 일·관심 종목."""
    regime = scan["regime"]
    vs50 = None
    if regime.get("price") and regime.get("ma50"):
        vs50 = (regime["price"] - regime["ma50"]) / regime["ma50"] * 100

    if level == "ok":
        market = "🟢 **시장이 튼튼해요(상승 흐름).** 조건 맞는 종목은 조금씩 사도 되는 때예요."
        todo = "좋은 종목을 **소량부터** 매수 검토. 단, 아래 표에서 손익비(RR)가 2 이상인 것만."
    elif level == "soft":
        market = "🟡 **시장이 살짝 약해요.** 완전히 나쁜 건 아니지만 조심할 구간이에요."
        todo = "사더라도 **아주 조금만**, 한 번에 다 넣지 말고 나눠서. 손익비 좋은 것만."
    else:
        market = ("🔴 **시장이 약해요(하락 흐름).** 지금은 새로 사기보다 "
                  "**현금 들고 기다리기**가 안전해요.")
        todo = "**신규 매수 보류.** 지수(QQQ)가 회복될 때까지 관망하고, 아래는 미리 봐두는 후보예요."

    lines = [
        "## 📌 초보용 한눈 요약",
        "",
        f"- **지금 시장:** {market}",
    ]
    if vs50 is not None:
        lines.append(
            f"  - 쉽게: 미국 기술주 대표지수(QQQ)가 50일 평균가격선보다 "
            f"**{vs50:+.1f}%** 위치예요. (0%보다 아래면 약세)"
        )
    lines.append(f"- **오늘 할 일:** {todo}")

    pick = _top_pick(candidates, owned)
    if pick:
        a = pick["a"]
        lv = pick["levels"]
        lines.append(
            f"- **가장 눈여겨볼 종목:** {a['name']} ({a['ticker']}) — "
            f"현재가 {_usd(a.get('price'), 2)}, 손익비(RR) {_rr_str(lv.get('rr'))}."
        )
        lines.append(
            f"  - 사면 목표 {_usd(lv.get('target'))} / 손절(방어선) {_usd(lv.get('stop'))}. "
            "**단, 위 '지금 시장'이 🔴이면 지금은 사지 말고 기다리세요.**"
        )
    else:
        lines.append("- **가장 눈여겨볼 종목:** 오늘은 바로 살 만한 종목이 없어요. 관망.")

    if owned:
        lines.append(f"- **내 보유:** {', '.join(sorted(owned))} — 손절·목표가는 그대로 유지.")
    else:
        lines.append("- **내 보유:** 없음 (전액 현금).")

    lines.extend([
        "",
        "> 손익비(RR)가 뭔지 등 용어는 맨 아래 **『🔤 용어 쉽게 풀이』** 참고.",
        "",
        "---",
        "",
    ])
    return lines


def _glossary_section() -> list[str]:
    """리포트 맨 아래 초보용 용어 풀이."""
    return [
        "---",
        "",
        "## 🔤 용어 쉽게 풀이",
        "",
        "| 용어 | 쉽게 말하면 |",
        "|------|-------------|",
        "| **QQQ** | 미국 기술주 100개를 묶은 대표 지수. '시장 전체 분위기' 온도계. |",
        "| **50MA(50일선)** | 최근 50일 평균 가격. 주가가 이 위면 상승세, 아래면 약세로 봄. |",
        "| **레짐(🟢🟡🔴)** | 지금이 살 때인지 기다릴 때인지 신호등. 🟢 사도 됨 / 🟡 조심 / 🔴 기다리기. |",
        "| **정배열** | 단기·중기·장기 평균선이 위→아래로 가지런한 상태 = 건강한 상승 추세. |",
        "| **RS(상대강도)** | 시장 평균보다 더 잘 버티거나 오르는 힘. ▲면 시장보다 강함. |",
        "| **RSI** | 0~100 과열·침체 지표. 너무 높으면(80+) 과열, 낮으면 눌린 상태. |",
        "| **PER / PEG** | PER=주가가 이익의 몇 배인지. PEG=그걸 성장속도로 나눈 값(낮을수록 저평가). |",
        "| **RR(손익비)** | **벌 수 있는 돈 ÷ 잃을 수 있는 돈.** 2면 '2 벌고 1 잃는' 구조. 클수록 좋음. |",
        "| **목표 / 손절** | 목표=팔아서 이익 볼 가격. 손절=여기 깨지면 손해 보고 파는 방어선. |",
        "| **ATR** | 하루 평균 움직이는 폭. 목표·손절 간격을 정할 때 씀. |",
        "| **눌림목/돌파/에너지응축** | 사기 좋은 자리 유형. 눌림목=잠깐 쉬는 자리, 돌파=고점 뚫기, 응축=힘 모으는 자리. |",
        "",
    ]


def _sector_section_placeholder() -> list[str]:
    """§1 AI 보완 전 placeholder + 용어집."""
    return [
        "## §1 섹터·자금 흐름 (AI 보완)",
        "",
        "*(`/나스닥_매수후보_스크린` ② 단계에서 채움 — 통과 종목 연관 섹터·자금 방향)*",
        "",
        *( _sector_etf_legend_md() ),
        "",
    ]


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


def _target_technical(a: dict) -> float | None:
    """기술적 목표 = max(52주 고점 저항, 현재가 + N×ATR 측정이동).

    - 눌림목/응축: 전고점(52주 고점)이 1차 저항·목표.
    - 돌파(고점 근접): 전고점이 현재가와 가까워 ATR 측정이동이 목표를 만든다.
    """
    price = a.get("price")
    if not price:
        return None
    cands = []
    high_52w = a.get("high_52w")
    if high_52w and high_52w > price:
        cands.append(high_52w)
    atr = a.get("atr_20")
    if atr and atr > 0:
        cands.append(price + ND_TGT_ATR_MULT * atr)
    return max(cands) if cands else None


def _target_valuation(a: dict) -> float | None:
    """밸류에이션 목표 = 적정 forward PEG(ND_FAIR_PEG) 도달 시 주가.

    목표 = 현재가 × ND_FAIR_PEG / fwdPEG  (fwdPEG = forwardPER ÷ 성장%)
    fwdPEG 없으면 trailing PEG 사용, 둘 다 없으면 None.
    """
    price = a.get("price")
    peg = a.get("peg_forward") or a.get("peg_trailing")
    if not price or not peg or peg <= 0:
        return None
    return price * ND_FAIR_PEG / peg


def compute_trade_levels(a: dict) -> dict:
    """C방식 목표가: 기술·밸류 목표 중 보수적(min) 채택, 애널 median은 참고.

    - 손절 = min(50MA, 20일 스윙저점) × 0.98
    - 목표 = min(기술적 목표, 밸류에이션 목표)  (사용 가능한 것만)
    - 애널 목표(median 우선, 없으면 mean)는 참고·교차검증용
    - RR = (목표 − 현재) ÷ (현재 − 손절)
    """
    price = a.get("price")
    supports = [x for x in (a.get("ma50"), a.get("swing_low_20")) if x]
    stop = min(supports) * 0.98 if supports else None

    t_tech = _target_technical(a)
    t_val = _target_valuation(a)
    t_analyst = a.get("target_median") or a.get("target_mean")

    # 밸류 목표가 현재가 아래 = 이미 적정가 위(고평가). 추세추종 1순위이므로
    # 제외하지 않고 기술적 목표를 쓰되 '고평가 주의' 플래그만 단다.
    overvalued = bool(t_val and price and t_val < price)

    if t_tech is not None and t_val is not None and not overvalued:
        target = min(t_tech, t_val)              # 밸류가 상단 캡 (보수적 min)
        target_src = "밸류" if target == t_val else "기술"
    elif t_tech is not None:
        target = t_tech                          # 기술 목표 (고평가 시에도 유지)
        target_src = "기술"
    elif t_val is not None:
        target = t_val
        target_src = "밸류"
    else:
        target = t_analyst
        target_src = "애널" if t_analyst else None

    rr = None
    if price and stop and target and price > stop:
        rr = (target - price) / (price - stop)
    return {
        "stop": stop, "target": target, "rr": rr, "target_src": target_src,
        "t_tech": t_tech, "t_val": t_val, "t_analyst": t_analyst,
        "overvalued": overvalued,
    }


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


def classify_candidate(a: dict, levels: dict, regime_ok: bool, owned: set[str],
                       regime_level: str = "ok") -> tuple[str, str, str]:
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
        src = levels.get("target_src") or "목표"
        src_label = {"밸류": "밸류에이션 목표(고평가)", "기술": "기술적 목표",
                     "애널": "애널 목표"}.get(src, "목표")
        msg = f"{src_label} {_usd(target)} < 현재 {_usd(price)}"
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
    elif regime_level == "soft":
        reasons_bad.append(f"레짐 🟡 제한 허용(소량·RR≥2, 50MA -{ND_REGIME_SOFT_PCT*100:.0f}% 이내)")

    vol_note = _volume_note(a)
    vol_ok = (
        a.get("entry_type") == "돌파"
        or (a.get("vol_ratio_5d") or 0) >= 1.0
    )
    if not vol_ok and a.get("entry_type") == "눌림목":
        reasons_bad.append(vol_note)

    if levels.get("overvalued") and levels.get("t_val"):
        reasons_bad.append(f"밸류 고평가(적정 {_usd(levels['t_val'])})")

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


def _src_tag(lv: dict) -> str:
    src = lv.get("target_src")
    return f" ({src})" if src else ""


def _target_breakdown(lv: dict) -> str:
    """기술·밸류·애널 3목표 + 채택 근거."""
    val_str = _usd(lv.get("t_val"))
    if lv.get("overvalued"):
        val_str += " ⚠고평가"
    parts = [
        f"기술 {_usd(lv.get('t_tech'))}",
        f"밸류 {val_str}",
        f"애널 {_usd(lv.get('t_analyst'))}",
    ]
    src = lv.get("target_src")
    if not src:
        tail = ""
    elif lv.get("overvalued"):
        tail = f" → **{src} 채택**(밸류 현재가 아래 → 캡 미적용)"
    else:
        tail = f" → **{src} 채택**(보수적 min)"
    return " · ".join(parts) + tail


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
    regime = scan["regime"]
    regime_ok = regime["ok"]
    regime_level = regime.get("level", "ok" if regime_ok else "off")
    out = []
    for a in scan["passed"]:
        levels = compute_trade_levels(a)
        classification, pick_reason, miss_reason = classify_candidate(
            a, levels, regime_ok, owned, regime_level,
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
        f"| 손절 / 목표 / RR | {_usd(lv['stop'])} / {_usd(lv['target'])}"
        f"{_src_tag(lv)} / **{_rr_str(lv['rr'])}** |",
        f"| 목표근거 | {_target_breakdown(lv)} |",
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
    level = regime.get("level", "ok" if regime["ok"] else "off")
    if level == "ok":
        regime_flag = "🟢 진입 허용"
        regime_word = "강세 (50MA 위)"
    elif level == "soft":
        regime_flag = "🟡 제한 허용"
        regime_word = f"제한 허용 (50MA -{ND_REGIME_SOFT_PCT*100:.0f}% 이내)"
    else:
        regime_flag = "🔴 진입 보류"
        regime_word = "약세 (50MA 이탈)"

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
        ">",
        f"> **목표가 산정(C방식):** 기술적 목표(전고점·현재가+{ND_TGT_ATR_MULT:g}×ATR20)와 "
        f"밸류에이션 목표(적정 fwdPEG {ND_FAIR_PEG:g} 도달가) 중 **보수적 값(min)** 채택. "
        "애널리스트 목표(median)는 **참고·교차검증**용. RR = (목표−현재)÷(현재−손절).",
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
    if level == "off":
        soft_px = (regime["ma50"] * (1 - ND_REGIME_SOFT_PCT)
                   if regime.get("ma50") else None)
        lines.append(
            f"> ⚠ QQQ 50MA 대비 -{ND_REGIME_SOFT_PCT*100:.0f}% "
            f"(≈{_usd(soft_px, 0)}) 회복 전까지 **실탄 매수 보류**. "
            "아래는 대기·재검토 리스트."
        )
        lines.append("")
    elif level == "soft":
        lines.append(
            "> ⚠ 레짐 **🟡 제한 허용** — 실탄은 **소량·RR≥2·분할**만 권장. "
            "50MA 완전 회복 전엔 추격·풀베팅 금지."
        )
        lines.append("")

    lines.extend(_beginner_summary(scan, candidates, owned, level))
    lines.extend(_sector_section_placeholder())
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

    flag_emoji = {"ok": "🟢", "soft": "🟡", "off": "🔴"}.get(level, "🔴")
    lines.extend([
        "",
        f"**우선 검토(§1: RR≥2·정배열·RS·거래량): {len(priority)}종목**"
        f" — 레짐 {flag_emoji}"
        + (" + RR 2 미달 다수" if len(priority) == 0 else ""),
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
        f"| 펀더 | ROE≥{ND_ROE_MIN*100:.0f}% · 성장≥{ND_REV_GROWTH_MIN*100:.0f}% · "
        f"**fwd PEG≤{ND_PEG_MAX:g}** · PER≤{ND_PER_MAX} | {fund_ok_n}/{fund_ok_n} ✅ |",
        f"| **최종분류 RR≥2** | 목표(기술·밸류 min)·손절 기준 | **{rr2_count}/{fund_ok_n}** |",
        f"| **레짐** | QQQ > 50MA (🟡 -{ND_REGIME_SOFT_PCT*100:.0f}% 이내 허용) | "
        f"{'✅' if level == 'ok' else ('🟡' if level == 'soft' else '❌')} |",
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

    if level == "off":
        soft_px = (regime["ma50"] * (1 - ND_REGIME_SOFT_PCT)
                   if regime.get("ma50") else None)
        action1 = (
            f"1. **QQQ ≈{_usd(soft_px, 0)}(50MA -{ND_REGIME_SOFT_PCT*100:.0f}%)** "
            f"회복 → 🟡 제한 허용 / **{_usd(regime['ma50'], 0)}(50MA)** → 🟢"
        )
    elif level == "soft":
        action1 = (
            f"1. **레짐 🟡** — 소량·RR≥2만. "
            f"**QQQ {_usd(regime['ma50'], 0)}(50MA)** 회복 시 🟢 정상 허용"
        )
    else:
        action1 = f"1. **레짐 🟢** 유지 — QQQ {_usd(regime['ma50'], 0)}(50MA) 위"

    lines.extend([
        "---",
        "",
        "## 다음 액션",
        "",
        action1,
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

    lines.extend(_glossary_section())

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
