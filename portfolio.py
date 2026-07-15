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


def get_exchange_rate() -> float:
    price = get_price("USDKRW=X")
    return round(price, 0) if price else 1484.0


# ── 최고 수익률 기록 관리 ──────────────────────

def load_history() -> dict:
    if HIST_FILE.exists():
        return json.loads(HIST_FILE.read_text(encoding="utf-8"))
    return {}


def save_history(history: dict):
    HIST_FILE.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")


def update_best_return(history: dict, ticker: str, current_rate: float) -> dict:
    """현재 수익률이 역대 최고치면 갱신."""
    today = datetime.now().strftime("%Y-%m-%d")
    if ticker not in history or current_rate > history[ticker]["best_return"]:
        history[ticker] = {"best_return": round(current_rate, 2), "best_date": today}
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

def build_portfolio_snapshot() -> dict:
    """보유 종목 가격·손익·알림을 한 번 계산해 콘솔/텔레그램이 공유한다."""
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

    save_history(history)

    total_profit = total_value - total_cost
    total_rate   = (total_profit / total_cost * 100) if total_cost else 0.0

    return {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "krw_rate": krw_rate,
        "results": results,
        "sell_alerts": sell_alerts,
        "profit_alerts": profit_alerts,
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
    print()


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
    for r in snap["results"]:
        name = html.escape(r["name"])
        tick = html.escape(r["ticker"])
        stop_s = f"${r['stop']:,.0f}" if r["stop"] else "-"
        tgt_s  = f"${r['target']:,.0f}" if r["target"] else "-"
        to_stop = f"{(r['stop']/r['price']-1)*100:+.1f}%" if r["stop"] else "-"
        to_tgt  = f"{(r['target']/r['price']-1)*100:+.1f}%" if r["target"] else "-"
        lines.append(
            f"• <b>{name}</b> ({tick})  {fmt_usd_plain(r['price'])}  "
            f"{fmt_rate(r['rate'])}\n"
            f"  손익 {fmt_usd(r['profit'])} / {fmt_krw(r['profit'] * krw)}\n"
            f"  손절 {stop_s} ({to_stop}) · 목표 {tgt_s} ({to_tgt})"
        )

    total_profit_krw = snap["total_profit"] * krw
    profit_sign = "+" if snap["total_profit"] >= 0 else ""
    lines += [
        "",
        "<b>합계</b>",
        f"평가 {fmt_krw(snap['total_value'] * krw)}  "
        f"손익 {profit_sign}{fmt_krw(total_profit_krw)}  "
        f"({fmt_rate(snap['total_rate'])})",
    ]
    return "\n".join(lines)


def send_telegram(text: str) -> bool:
    """TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 가 있으면 전송.
    없거나 실패하면 False (로컬 실행은 secrets 없이 통과).
    """
    token   = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        print("  [텔레그램] TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 미설정 — 전송 생략")
        return False

    # 텔레그램 메시지 길이 한도
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
        print("  [텔레그램] 일일 리포트 전송 완료")
        return True
    except Exception as e:
        print(f"  [텔레그램] 전송 오류: {e}")
        return False


def main():
    snap = build_portfolio_snapshot()
    print_portfolio(snap)
    msg = build_telegram_summary(snap)
    # CI에서는 secrets가 있을 때만 전송. 로컬도 env 설정 시 전송됨.
    send_telegram(msg)


if __name__ == "__main__":
    main()
