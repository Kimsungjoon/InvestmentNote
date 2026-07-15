import argparse
import html
import json
import os
import requests
from datetime import datetime
from pathlib import Path

# GitHub Actions 등 CI 환경에서는 ANSI 색상 비활성화
IS_CI = os.environ.get("CI", "false").lower() == "true"

# ─────────────────────────────────────────────
# 보유 주식 설정 (종목명, 티커, 평단가, 수량, 매수일)
# ─────────────────────────────────────────────
# 청산 기준: 종목별 목표가·손절가로 관리 (고정 %룰 폐지)
#   target_price : 업종 멀티플 × 예상 이익성장 기반 현실적 목표가
#   stop_price   : 20일 ATR 고려, 추세가 꺾였다고 판단되는 손절 가격
#                  (일시적 흔들림엔 견디되 지지선·주요 이평선 이탈 시 청산)
# 추세가 진행되면 stop_price를 지지선 상향에 맞춰 올려 관리한다(트레일).
PORTFOLIO = [
    {"name": "케이던스",          "ticker": "CDNS", "avg_price": 396.3280,
     "stop_price": 358.0, "target_price": 430.0, "qty": 10, "buy_date": "2026-06-14"},
    {"name": "하우멧 에어로스페이스", "ticker": "HWM",  "avg_price": 279.5975,
     "stop_price": 252.0, "target_price": 315.0, "qty": 12, "buy_date": "2026-07-07"},
]

# 매도 알림에서 제외할 티커
ALERT_EXCLUDE = set()

HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
BASE_DIR   = Path(__file__).parent
HIST_FILE  = BASE_DIR / "portfolio_history.json"


# ── 주가 / 환율 조회 ─────────────────────────

def get_price(ticker: str) -> float | None:
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval=1d&range=1d"
    try:
        res  = requests.get(url, headers=HEADERS, timeout=5)
        data = res.json()
        return round(float(data["chart"]["result"][0]["meta"]["regularMarketPrice"]), 2)
    except Exception:
        return None


def get_bars(ticker: str, range_: str = "3mo") -> dict | None:
    """일봉 close/volume + 현재가."""
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
           f"?interval=1d&range={range_}")
    try:
        res = requests.get(url, headers=HEADERS, timeout=10)
        result = res.json()["chart"]["result"][0]
        meta = result["meta"]
        q = result["indicators"]["quote"][0]
        closes = [c for c in q["close"] if c is not None]
        vols   = [v for v in q["volume"] if v is not None]
        price  = meta.get("regularMarketPrice") or (closes[-1] if closes else None)
        if price is None or len(closes) < 25:
            return None
        return {
            "price": round(float(price), 2),
            "close": closes,
            "volume": vols,
        }
    except Exception:
        return None


def _sma(values: list[float], window: int) -> float | None:
    if len(values) < window:
        return None
    return sum(values[-window:]) / window


def _ret_nd(closes: list[float], n: int = 20) -> float | None:
    if len(closes) < n + 1:
        return None
    return (closes[-1] / closes[-(n + 1)] - 1) * 100


def get_exchange_rate() -> float:
    price = get_price("USDKRW=X")
    return round(price, 0) if price else 1484.0


# 보유 흐름 분류 (나스닥_보유매도판단_프롬프트 §2 요약)
FLOW_SELL     = "매도 우선"
FLOW_REDUCE   = "비중 축소"
FLOW_REVIEW   = "보유 재검토"
FLOW_HOLD     = "상승 여지 유지"


def classify_hold_flow(r: dict, bars: dict | None, qqq_ret: float | None) -> dict:
    """목표가·추세·RS·거래량·20MA로 4단계 분류 + 한 줄 대응.

    섹터 역할·매수 논리(프롬프트 6·7)는 자동화 불가 → 사유에 미포함.
    """
    price  = r["price"]
    stop   = r.get("stop")
    target = r.get("target")
    to_tgt  = ((target / price) - 1) * 100 if target and price else None
    to_stop = ((stop / price) - 1) * 100 if stop and price else None

    ma20 = ma50 = ret20 = vol_ratio = None
    above_20 = above_50 = None
    if bars:
        closes, vols = bars["close"], bars["volume"]
        ma20 = _sma(closes, 20)
        ma50 = _sma(closes, 50)
        ret20 = _ret_nd(closes, 20)
        if ma20 is not None:
            above_20 = price > ma20
        if ma50 is not None:
            above_50 = price > ma50
        if len(vols) >= 20:
            avg20 = sum(vols[-20:]) / 20
            avg5  = sum(vols[-5:]) / 5 if len(vols) >= 5 else None
            vol_ratio = (avg5 / avg20) if (avg5 and avg20) else None

    rs_ok = (ret20 is not None and qqq_ret is not None and ret20 > qqq_ret)
    reasons = []

    # ── 우선순위: 매도 우선 > 비중 축소 > 보유 재검토 > 상승 여지 유지 ──
    if stop is not None and price <= stop:
        return {
            "flow": FLOW_SELL,
            "action": f"손절가 도달 → 전량 청산 검토 (손절 ${stop:,.0f})",
            "to_target": to_tgt, "rs_ok": rs_ok, "above_20": above_20,
        }

    if to_stop is not None and to_stop > -3 and above_20 is False and above_50 is False:
        reasons = ["손절 임박", "20/50MA 이탈"]
        if rs_ok is False:
            reasons.append("RS약세")
        return {
            "flow": FLOW_SELL,
            "action": " · ".join(reasons) + " → 손절 준비·비중 축소 우선",
            "to_target": to_tgt, "rs_ok": rs_ok, "above_20": above_20,
        }

    if target is not None and price >= target:
        return {
            "flow": FLOW_REDUCE,
            "action": f"목표가 도달 → 청산·익절 검토 (목표 ${target:,.0f})",
            "to_target": to_tgt, "rs_ok": rs_ok, "above_20": above_20,
        }

    if to_tgt is not None and 0 < to_tgt <= 5:
        return {
            "flow": FLOW_REDUCE,
            "action": f"목표가 {to_tgt:+.1f}% 이내 → 분할 익절·비중 축소 검토",
            "to_target": to_tgt, "rs_ok": rs_ok, "above_20": above_20,
        }

    weak = []
    if above_20 is False:
        weak.append("20MA↓")
    if rs_ok is False and ret20 is not None and qqq_ret is not None:
        weak.append("QQQ대비 열위")
    if to_stop is not None and -5 <= to_stop < 0:
        weak.append(f"손절까지 {to_stop:+.1f}%")
    if vol_ratio is not None and vol_ratio >= 1.3 and (r["rate"] < 0):
        weak.append("하락+거래량↑")

    if weak:
        return {
            "flow": FLOW_REVIEW,
            "action": " · ".join(weak) + " → 손절·비중·논리 재확인",
            "to_target": to_tgt, "rs_ok": rs_ok, "above_20": above_20,
        }

    upside = f"목표 {to_tgt:+.1f}%" if to_tgt is not None else "목표가 N/A"
    if above_20 is True:
        trend = "20MA↑"
    elif above_20 is False:
        trend = "20MA↓"
    else:
        trend = "추세 N/A"
    if rs_ok is True:
        rs = "RS▲"
    elif rs_ok is False:
        rs = "RS·"
    else:
        rs = "RS N/A"

    return {
        "flow": FLOW_HOLD,
        "action": f"{trend} · {rs} · {upside} → 보유 유지·손절가만 관리",
        "to_target": to_tgt, "rs_ok": rs_ok, "above_20": above_20,
    }


def attach_flow_classifications(results: list[dict]) -> float | None:
    """QQQ 대비 RS와 종목별 흐름 분류를 results에 붙인다. QQQ 20일 수익률 반환."""
    qqq = get_bars("QQQ")
    qqq_ret = _ret_nd(qqq["close"], 20) if qqq else None
    for r in results:
        bars = get_bars(r["ticker"])
        flow = classify_hold_flow(r, bars, qqq_ret)
        r["flow"] = flow["flow"]
        r["flow_action"] = flow["action"]
        r["above_20"] = flow["above_20"]
        r["rs_ok"] = flow["rs_ok"]
        r["to_target_pct"] = flow["to_target"]
    return qqq_ret


# ── 최고 수익률 기록 관리 ──────────────────────

def load_history() -> dict:
    if HIST_FILE.exists():
        return json.loads(HIST_FILE.read_text(encoding="utf-8"))
    return {}


def save_history(history: dict):
    HIST_FILE.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")


def update_best_return(history: dict, ticker: str, current_rate: float) -> dict:
    """현재 수익률이 역대 최고치면 갱신. 다른 키(_sent_alerts 등)는 유지."""
    today = datetime.now().strftime("%Y-%m-%d")
    entry = history.get(ticker)
    if not isinstance(entry, dict):
        entry = {}
        history[ticker] = entry
    best = entry.get("best_return")
    if best is None or current_rate > best:
        entry["best_return"] = round(current_rate, 2)
        entry["best_date"] = today
    return history


def check_stop_alert(ticker: str, name: str, price: float,
                     stop_price: float | None, current_rate: float) -> list[str]:
    """손절 알림: 현재가가 종목별 손절가(추세 이탈 기준) 이하로 내려오면 알림."""
    if ticker in ALERT_EXCLUDE or stop_price is None:
        return []
    if price <= stop_price:
        return [
            f"[{name} / {ticker}]  현재가 ${price:,.2f} ≤ 손절가 ${stop_price:,.2f} "
            f"(수익률 {fmt_rate(current_rate)}) — 추세 이탈, 손절 실행"
        ]
    return []


def check_target_alert(ticker: str, name: str, price: float,
                       target_price: float | None, current_rate: float) -> list[str]:
    """목표가 알림: 현재가가 종목별 목표가(밸류에이션 기반) 이상이면 청산 검토."""
    if ticker in ALERT_EXCLUDE or target_price is None:
        return []
    if price >= target_price:
        return [
            f"[{name} / {ticker}]  현재가 ${price:,.2f} ≥ 목표가 ${target_price:,.2f} "
            f"(수익률 {fmt_rate(current_rate)}) — 목표가 도달, 청산 검토"
        ]
    return []


def _alert_key(ticker: str, kind: str, level: float) -> str:
    return f"{ticker}:{kind}:{level:.4f}"


def _ensure_sent_alerts(history: dict) -> dict:
    sent = history.get("_sent_alerts")
    if not isinstance(sent, dict):
        sent = {}
        history["_sent_alerts"] = sent
    return sent


def collect_pending_alerts(history: dict, results: list[dict]) -> list[dict]:
    """손절/목표가 도달 중이며 아직 전송하지 않은 알림 목록.

    회복(손절 위·목표 아래)되면 전송 기록을 지워 재도달 시 다시 알림한다.
    전송 성공 후에만 mark_alerts_sent()로 기록한다.
    """
    sent = _ensure_sent_alerts(history)
    held = {r["ticker"] for r in results}
    active_keys = set()
    pending = []

    for r in results:
        tick = r["ticker"]
        if tick in ALERT_EXCLUDE:
            continue
        name, price, rate = r["name"], r["price"], r["rate"]
        stop, target = r.get("stop"), r.get("target")

        if stop is not None and price <= stop:
            key = _alert_key(tick, "stop", stop)
            active_keys.add(key)
            if key not in sent:
                pending.append({
                    "kind": "stop", "ticker": tick, "name": name,
                    "price": price, "level": stop, "rate": rate, "key": key,
                })

        if target is not None and price >= target:
            key = _alert_key(tick, "target", target)
            active_keys.add(key)
            if key not in sent:
                pending.append({
                    "kind": "target", "ticker": tick, "name": name,
                    "price": price, "level": target, "rate": rate, "key": key,
                })

    for key in list(sent.keys()):
        if key in active_keys:
            continue
        parts = key.split(":")
        if len(parts) >= 2 and parts[0] in held:
            del sent[key]

    return pending


def mark_alerts_sent(history: dict, alerts: list[dict]):
    sent = _ensure_sent_alerts(history)
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    for a in alerts:
        sent[a["key"]] = now


# ── 포맷 헬퍼 ─────────────────────────────────

def fmt_usd(v: float) -> str:
    return f"+${v:,.2f}" if v >= 0 else f"-${abs(v):,.2f}"

def fmt_usd_plain(v: float) -> str:
    return f"${v:,.2f}"

def fmt_krw(v: float) -> str:
    return f"₩{v:,.0f}" if v >= 0 else f"-₩{abs(v):,.0f}"

def fmt_rate(v: float) -> str:
    rounded = round(v, 2)
    if rounded == 0.0:
        return "0.00%"
    return f"{'+' if rounded > 0 else ''}{rounded:.2f}%"

def color(text: str, value: float) -> str:
    """양수=빨강, 음수=파랑 (한국 주식 기준). CI 환경에서는 색상 미적용."""
    if IS_CI:
        return text
    if value > 0:
        return f"\033[91m{text}\033[0m"
    elif value < 0:
        return f"\033[94m{text}\033[0m"
    return text

def yellow(text: str) -> str:
    return text if IS_CI else f"\033[93m{text}\033[0m"


# ── 스냅샷 계산 ────────────────────────────────

def build_portfolio_snapshot(with_flow: bool = True) -> dict:
    """보유 종목 가격·손익·알림을 한 번 계산해 콘솔/텔레그램이 공유한다.

    with_flow=False 이면 장중 알림 전용(흐름 분류·일봉 추가조회 생략).
    """
    krw_rate = get_exchange_rate()
    history  = load_history()

    total_cost    = 0.0
    total_value   = 0.0
    results       = []
    sell_alerts   = []
    profit_alerts = []

    for stock in PORTFOLIO:
        name  = stock["name"]
        tick  = stock["ticker"]
        avg   = stock["avg_price"]
        qty   = stock["qty"]
        price = get_price(tick)

        if price is None:
            continue

        cost   = avg * qty
        value  = price * qty
        profit = value - cost
        rate   = (price - avg) / avg * 100

        history     = update_best_return(history, tick, rate)
        best_return = history[tick]["best_return"]

        target = stock.get("target_price")
        stop   = stock.get("stop_price")
        sell_alerts   += check_stop_alert(tick, name, price, stop, rate)
        profit_alerts += check_target_alert(tick, name, price, target, rate)

        total_cost  += cost
        total_value += value
        results.append({
            "name": name, "ticker": tick, "avg": avg, "price": price,
            "qty": qty, "cost": cost, "value": value, "profit": profit,
            "rate": rate, "best_return": best_return,
            "target": target, "stop": stop,
        })

    new_alerts = collect_pending_alerts(history, results)
    save_history(history)  # best_return·회복된 알림키 정리 반영

    qqq_ret = attach_flow_classifications(results) if with_flow else None

    total_profit = total_value - total_cost
    total_rate   = (total_profit / total_cost * 100) if total_cost else 0.0

    return {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "krw_rate": krw_rate,
        "results": results,
        "sell_alerts": sell_alerts,
        "profit_alerts": profit_alerts,
        "new_alerts": new_alerts,
        "qqq_ret_20d": qqq_ret,
        "total_cost": total_cost,
        "total_value": total_value,
        "total_profit": total_profit,
        "total_rate": total_rate,
    }


# ── 콘솔 출력 ──────────────────────────────────

def print_portfolio(snap: dict | None = None):
    snap = snap or build_portfolio_snapshot()
    krw_rate = snap["krw_rate"]
    results  = snap["results"]
    sell_alerts   = snap["sell_alerts"]
    profit_alerts = snap["profit_alerts"]

    col_w = [16, 6, 10, 8, 8, 10, 12, 14, 9, 11, 9]
    header = (
        f"  {'종목':<{col_w[0]}} {'티커':<{col_w[1]}} "
        f"{'평단가($)':>{col_w[2]}} {'손절가':>{col_w[3]}} {'목표가':>{col_w[4]}} "
        f"{'현재가($)':>{col_w[5]}} {'손익($)':>{col_w[6]}} {'손익(₩)':>{col_w[7]}} "
        f"{'수익률':>{col_w[8]}} {'최고수익률':>{col_w[9]}} {'고점대비':>{col_w[10]}}"
    )
    sep = "  " + "─" * (sum(col_w) + len(col_w) + 4)

    print()
    print("  ════════════════════════════════════════════════════════")
    print("           📊  나스닥 포트폴리오 수익률 현황")
    print("  ════════════════════════════════════════════════════════")
    print(f"  적용 환율: 1 USD = {krw_rate:,.0f}원")

    if sell_alerts:
        print()
        print("  ┌─────────────────────────────────────────────────────┐")
        print("  │              🚨  손절가 도달 알림                    │")
        print("  ├─────────────────────────────────────────────────────┤")
        for alert in sell_alerts:
            print(f"  │  🔴 {yellow(alert):<60}│")
        print("  └─────────────────────────────────────────────────────┘")

    if profit_alerts:
        print()
        print("  ┌─────────────────────────────────────────────────────┐")
        print("  │              💰  목표가 도달 알림                    │")
        print("  ├─────────────────────────────────────────────────────┤")
        for alert in profit_alerts:
            print(f"  │  🟢 {alert:<60}│")
        print("  └─────────────────────────────────────────────────────┘")

    print()
    print(header)
    print(sep)

    for r in results:
        profit_krw     = r["profit"] * krw_rate
        rate_str       = color(fmt_rate(r["rate"]), r["rate"])
        profit_str     = color(fmt_usd(r["profit"]), r["profit"])
        profit_krw_str = color(("+") + fmt_krw(profit_krw) if profit_krw >= 0 else fmt_krw(profit_krw), profit_krw)
        diff           = r["rate"] - r["best_return"]
        best_str       = fmt_rate(r["best_return"])
        diff_str       = color(fmt_rate(diff), diff)
        stop_str       = f"${r['stop']:,.0f}" if r["stop"] else "-"
        target_str     = f"${r['target']:,.0f}" if r["target"] else "-"
        print(
            f"  {r['name']:<{col_w[0]}} {r['ticker']:<{col_w[1]}} "
            f"{fmt_usd_plain(r['avg']):>{col_w[2]}} {stop_str:>{col_w[3]}} {target_str:>{col_w[4]}} "
            f"{fmt_usd_plain(r['price']):>{col_w[5]}} {profit_str:>{col_w[6]+9}} "
            f"{profit_krw_str:>{col_w[7]+9}} {rate_str:>{col_w[8]+9}} "
            f"{best_str:>{col_w[9]}} {diff_str:>{col_w[10]+9}}"
        )

    if not results:
        print("  가격 조회에 실패했습니다. 인터넷 연결을 확인하세요.")
        return

    total_profit     = snap["total_profit"]
    total_profit_krw = total_profit * krw_rate
    total_rate       = snap["total_rate"]

    print(sep)
    print(
        f"  {'합계':<{col_w[0]}} {'':<{col_w[1]}} "
        f"{'':{col_w[2]}} {'':{col_w[3]}} {'':{col_w[4]}} {'':{col_w[5]}} "
        f"{color(fmt_usd(total_profit), total_profit):>{col_w[6]+9}} "
        f"{color(('+' if total_profit_krw >= 0 else '') + fmt_krw(total_profit_krw), total_profit_krw):>{col_w[7]+9}} "
        f"{color(fmt_rate(total_rate), total_rate):>{col_w[8]+9}} "
        f"{'':{col_w[9]}} {'':{col_w[10]}}"
    )

    print()
    print("  ┌─── 청산 기준 (목표가·손절가) ───────────────────────┐")
    print(f"  │  {'종목':<14}{'현재가':>9}{'손절가':>9}{'목표가':>9}{'손절까지':>9}{'목표까지':>9} │")
    for r in results:
        stop_s   = f"${r['stop']:,.0f}" if r["stop"] else "-"
        tgt_s    = f"${r['target']:,.0f}" if r["target"] else "-"
        to_stop  = f"{(r['stop']/r['price']-1)*100:+.1f}%" if r["stop"] else "-"
        to_tgt   = f"{(r['target']/r['price']-1)*100:+.1f}%" if r["target"] else "-"
        print(f"  │  {r['name']:<13}{fmt_usd_plain(r['price']):>9}{stop_s:>9}{tgt_s:>9}"
              f"{to_stop:>9}{to_tgt:>9} │")
    print("  └─────────────────────────────────────────────────────┘")

    print()
    print("  ┌─── 원화 환산 요약 ──────────────────────────────────┐")
    print(f"  │  총 매수금액 : {fmt_krw(snap['total_cost'] * krw_rate):<38}│")
    print(f"  │  총 평가금액 : {fmt_krw(snap['total_value'] * krw_rate):<38}│")
    print(f"  │  총 손익     : {color(('+' if total_profit>=0 else '') + fmt_krw(total_profit_krw), total_profit):<47}│")
    print(f"  │  총 수익률   : {color(fmt_rate(total_rate), total_rate):<47}│")
    print("  └─────────────────────────────────────────────────────┘")

    if any(r.get("flow") for r in results):
        print()
        print("  ┌─── 보유 흐름 분류 (자동요약) ───────────────────────┐")
        qqq_s = fmt_rate(snap["qqq_ret_20d"]) if snap.get("qqq_ret_20d") is not None else "N/A"
        print(f"  │  QQQ 20일 {qqq_s}")
        for r in results:
            flow = r.get("flow") or "-"
            action = r.get("flow_action") or ""
            print(f"  │  [{flow}] {r['name']} ({r['ticker']})")
            print(f"  │    → {action}")
        print("  │  ※ 섹터·매수논리는 프롬프트/HTS로 별도 확인")
        print("  └─────────────────────────────────────────────────────┘")
    print()


FLOW_EMOJI = {
    FLOW_HOLD:   "✅",
    FLOW_REVIEW: "⬜",
    FLOW_REDUCE: "🟠",
    FLOW_SELL:   "🔴",
}


def build_flow_telegram_section(snap: dict) -> list[str]:
    """보유매도판단 프롬프트 §2 자동요약 블록."""
    lines = [
        "",
        "<b>🧭 보유 흐름 분류</b>",
        "<i>상승여지유지 / 보유재검토 / 비중축소 / 매도우선</i>",
    ]
    qqq = snap.get("qqq_ret_20d")
    if qqq is not None:
        lines.append(f"QQQ 20일 {fmt_rate(qqq)}")
    lines.append("")

    order = {FLOW_SELL: 0, FLOW_REDUCE: 1, FLOW_REVIEW: 2, FLOW_HOLD: 3}
    ranked = sorted(
        snap["results"],
        key=lambda x: (order.get(x.get("flow"), 9), x.get("ticker") or ""),
    )
    for r in ranked:
        flow = r.get("flow") or "-"
        emoji = FLOW_EMOJI.get(flow, "•")
        name = html.escape(r["name"])
        tick = html.escape(r["ticker"])
        action = html.escape(r.get("flow_action") or "")
        lines.append(f"{emoji} <b>{flow}</b> — {name} ({tick})")
        lines.append(f"  {action}")
        lines.append("")

    lines.append("<i>※ 목표가·20MA·QQQ상대·거래량 자동판정. 섹터/매수논리는 별도 확인.</i>")
    return lines


# ── 텔레그램 ──────────────────────────────────

def build_telegram_summary(snap: dict) -> str:
    """텔레그램용 HTML 요약 (길이 ≤ 4096)."""
    krw = snap["krw_rate"]
    lines = [
        f"<b>📊 나스닥 일일 리포트</b> ({html.escape(snap['date'])})",
        f"환율 1 USD = {krw:,.0f}원",
        "",
    ]

    if snap["sell_alerts"]:
        lines.append("<b>🚨 손절가 도달</b>")
        for a in snap["sell_alerts"]:
            lines.append(f"🔴 {html.escape(a)}")
        lines.append("")

    if snap["profit_alerts"]:
        lines.append("<b>💰 목표가 도달</b>")
        for a in snap["profit_alerts"]:
            lines.append(f"🟢 {html.escape(a)}")
        lines.append("")

    if not snap["results"]:
        lines.append("가격 조회 실패 — 재실행 필요")
        return "\n".join(lines)

    lines.append("<b>보유 종목</b>")
    lines.append("")
    for r in snap["results"]:
        name = html.escape(r["name"])
        tick = html.escape(r["ticker"])
        stop_s = f"${r['stop']:,.0f}" if r["stop"] else "-"
        tgt_s  = f"${r['target']:,.0f}" if r["target"] else "-"
        to_stop = f"{(r['stop']/r['price']-1)*100:+.1f}%" if r["stop"] else "-"
        to_tgt  = f"{(r['target']/r['price']-1)*100:+.1f}%" if r["target"] else "-"
        lines.append(f"• <b>{name}</b> ({tick})  {fmt_usd_plain(r['price'])}  {fmt_rate(r['rate'])}")
        lines.append(f"  손익 {fmt_usd(r['profit'])} / {fmt_krw(r['profit'] * krw)}")
        lines.append(f"  손절 {stop_s} ({to_stop}) · 목표 {tgt_s} ({to_tgt})")
        lines.append("")  # 종목 사이 빈 줄

    total_profit_krw = snap["total_profit"] * krw
    profit_sign = "+" if snap["total_profit"] >= 0 else ""
    lines += [
        "<b>합계</b>",
        f"평가 {fmt_krw(snap['total_value'] * krw)}  "
        f"손익 {profit_sign}{fmt_krw(total_profit_krw)}  "
        f"({fmt_rate(snap['total_rate'])})",
    ]
    lines += build_flow_telegram_section(snap)
    return "\n".join(lines)


def build_alerts_telegram(alerts: list[dict], date: str) -> str:
    """손절·목표가 도달 전용 메시지."""
    lines = [f"<b>⚡ 나스닥 가격 알림</b> ({html.escape(date)})", ""]
    for a in alerts:
        name = html.escape(a["name"])
        tick = html.escape(a["ticker"])
        if a["kind"] == "stop":
            lines.append(
                f"🚨 <b>손절가 도달</b> — {name} ({tick})\n"
                f"현재가 {fmt_usd_plain(a['price'])} ≤ 손절가 ${a['level']:,.2f}\n"
                f"수익률 {fmt_rate(a['rate'])} — 추세 이탈, 손절 실행"
            )
        else:
            lines.append(
                f"💰 <b>목표가 도달</b> — {name} ({tick})\n"
                f"현재가 {fmt_usd_plain(a['price'])} ≥ 목표가 ${a['level']:,.2f}\n"
                f"수익률 {fmt_rate(a['rate'])} — 청산 검토"
            )
        lines.append("")
    return "\n".join(lines).rstrip()


def send_telegram(text: str, label: str = "메시지") -> bool:
    """TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 가 있으면 전송.
    없거나 실패하면 False (로컬 실행은 secrets 없이 통과).
    """
    token   = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        print("  [텔레그램] TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 미설정 — 전송 생략")
        return False

    if len(text) > 4000:
        text = text[:3990] + "\n…"

    try:
        res = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=15,
        )
        data = res.json()
        if not data.get("ok"):
            print(f"  [텔레그램] 전송 실패: {data}")
            return False
        print(f"  [텔레그램] {label} 전송 완료")
        return True
    except Exception as e:
        print(f"  [텔레그램] 전송 오류: {e}")
        return False


def send_price_alerts(snap: dict) -> int:
    """신규 손절·목표가 도달 알림만 텔레그램 전송. 전송 성공 시에만 중복 방지 기록."""
    alerts = snap.get("new_alerts") or []
    if not alerts:
        print("  [알림] 신규 손절/목표가 도달 없음")
        return 0
    for a in alerts:
        kind = "손절" if a["kind"] == "stop" else "목표"
        print(f"  [알림] {kind} 신규 — {a['name']} ({a['ticker']}) "
              f"${a['price']:,.2f} / ${a['level']:,.2f}")
    ok = send_telegram(
        build_alerts_telegram(alerts, snap["date"]), label="가격 알림"
    )
    if ok:
        history = load_history()
        mark_alerts_sent(history, alerts)
        save_history(history)
        return len(alerts)
    print("  [알림] 전송 실패/생략 — 다음 점검에서 재시도")
    return 0


def run_daily():
    snap = build_portfolio_snapshot()
    print_portfolio(snap)
    # 일일 리포트보다 먼저: 아직 안 보낸 손절/목표가 알림
    send_price_alerts(snap)
    send_telegram(build_telegram_summary(snap), label="일일 리포트")


def run_alerts_only():
    """장중 점검: 손절/목표가 새로 닿았을 때만 텔레그램 전송."""
    snap = build_portfolio_snapshot(with_flow=False)
    print(f"  [알림전용] {snap['date']} 보유 {len(snap['results'])}종목 점검")
    for r in snap["results"]:
        stop_s = f"${r['stop']:,.0f}" if r["stop"] else "-"
        tgt_s  = f"${r['target']:,.0f}" if r["target"] else "-"
        print(f"    {r['name']} ({r['ticker']}) {fmt_usd_plain(r['price'])} "
              f"손절 {stop_s} 목표 {tgt_s} 수익률 {fmt_rate(r['rate'])}")
    send_price_alerts(snap)


def run_test_notify():
    """일일 리포트 + 손절/목표가 샘플 알림을 각각 전송 (중복방지 기록 안 함)."""
    snap = build_portfolio_snapshot()
    print_portfolio(snap)

    print("  [테스트] 1/3 일일 리포트 전송...")
    send_telegram(build_telegram_summary(snap), label="일일 리포트(테스트)")

    sample = snap["results"][0] if snap["results"] else {
        "name": "테스트종목", "ticker": "TEST",
        "price": 100.0, "stop": 110.0, "target": 90.0, "rate": -10.0,
    }
    stop_lvl = sample.get("stop") or sample["price"] * 0.9
    tgt_lvl  = sample.get("target") or sample["price"] * 1.1

    print("  [테스트] 2/3 손절가 알림 전송...")
    send_telegram(
        build_alerts_telegram([{
            "kind": "stop",
            "name": sample["name"],
            "ticker": sample["ticker"],
            "price": min(sample["price"], stop_lvl),
            "level": stop_lvl,
            "rate": sample["rate"],
        }], snap["date"]) + "\n\n<i>※ 손절가 알림 테스트 메시지입니다.</i>",
        label="손절가 알림(테스트)",
    )

    print("  [테스트] 3/3 목표가 알림 전송...")
    send_telegram(
        build_alerts_telegram([{
            "kind": "target",
            "name": sample["name"],
            "ticker": sample["ticker"],
            "price": max(sample["price"], tgt_lvl),
            "level": tgt_lvl,
            "rate": ((tgt_lvl / sample.get("avg", sample["price"])) - 1) * 100
                    if sample.get("avg") else sample["rate"],
        }], snap["date"]) + "\n\n<i>※ 목표가 알림 테스트 메시지입니다.</i>",
        label="목표가 알림(테스트)",
    )
    print("  [테스트] 3건 전송 시도 완료")


def main():
    parser = argparse.ArgumentParser(description="나스닥 포트폴리오 / 텔레그램 알림")
    parser.add_argument(
        "--alerts-only", action="store_true",
        help="손절·목표가 신규 도달 시에만 텔레그램 전송 (장중 점검용)",
    )
    parser.add_argument(
        "--test-notify", action="store_true",
        help="일일 리포트·손절·목표가 알림을 각각 테스트 전송",
    )
    args = parser.parse_args()
    if args.test_notify:
        run_test_notify()
    elif args.alerts_only:
        run_alerts_only()
    else:
        run_daily()


if __name__ == "__main__":
    main()
