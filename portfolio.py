import json
import os
import requests
from datetime import datetime
from pathlib import Path

# GitHub Actions 등 CI 환경에서는 ANSI 색상 비활성화
IS_CI = os.environ.get("CI", "false").lower() == "true"

# ─────────────────────────────────────────────
# 보유 주식 설정 (종목명, 티커, 평단가, 수량)
# ─────────────────────────────────────────────
PORTFOLIO = [
    {"name": "아마존닷컴",      "ticker": "AMZN", "avg_price": 213.80, "qty": 7},
    {"name": "브로드컴",        "ticker": "AVGO", "avg_price": 325.19, "qty": 6},
    {"name": "팔란티어 테크",   "ticker": "PLTR", "avg_price": 140.41, "qty": 14},
    {"name": "비스트라 에너지", "ticker": "VST",  "avg_price": 161.69, "qty": 8},
]

# 매도 알림에서 제외할 티커
ALERT_EXCLUDE = {"PLTR"}

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


def check_alert(ticker: str, name: str, current_rate: float, history: dict) -> str | None:
    """매도 알림 조건 확인. 해당 없으면 None 반환."""
    if ticker in ALERT_EXCLUDE:
        return None

    alerts = []

    # 조건 1: 현재 수익률 -10% 이하
    if current_rate <= -10.0:
        alerts.append(f"수익률 {fmt_rate(current_rate)} — 손실 -10% 초과")

    # 조건 2: 역대 최고 수익률 대비 -10%p 이상 하락
    if ticker in history:
        best = history[ticker]["best_return"]
        best_date = history[ticker]["best_date"]
        if current_rate <= best - 10.0:
            alerts.append(
                f"최고 수익률 {fmt_rate(best)} ({best_date}) 대비 "
                f"{fmt_rate(current_rate - best)} 하락"
            )

    if alerts:
        return f"[{name} / {ticker}]  " + " | ".join(alerts)
    return None


# ── 포맷 헬퍼 ─────────────────────────────────

def fmt_usd(v: float) -> str:
    return f"+${v:,.2f}" if v >= 0 else f"-${abs(v):,.2f}"

def fmt_usd_plain(v: float) -> str:
    return f"${v:,.2f}"

def fmt_krw(v: float) -> str:
    return f"₩{v:,.0f}" if v >= 0 else f"-₩{abs(v):,.0f}"

def fmt_rate(v: float) -> str:
    return f"{'+' if v >= 0 else ''}{v:.2f}%"

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


# ── 마크다운 저장 ──────────────────────────────

def save_markdown(results: list, total_cost: float, total_value: float,
                  krw_rate: float, alerts: list[str]):
    now       = datetime.now()
    date_str  = now.strftime("%Y-%m-%d")
    time_str  = now.strftime("%H:%M")
    month_dir = f"{now.month}월"
    filename  = f"포트폴리오_{date_str}.md"

    month_path = BASE_DIR / "매매노트" / month_dir
    month_path.mkdir(parents=True, exist_ok=True)
    save_path = month_path / filename

    total_profit = total_value - total_cost
    total_rate   = (total_profit / total_cost) * 100

    lines = []
    lines.append("# 나스닥 포트폴리오 수익률 현황")
    lines.append("")
    lines.append(f"> 기준일시: {date_str} {time_str} | 적용 환율: 1 USD = {krw_rate:,.0f}원")
    lines.append("")

    # 매도 알림 섹션
    if alerts:
        lines.append("---")
        lines.append("")
        lines.append("## ⚠️ 매도 권장 알림")
        lines.append("")
        for alert in alerts:
            lines.append(f"> 🚨 **{alert}**")
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("## 보유 종목별 현황")
    lines.append("")
    lines.append("| 종목 | 티커 | 평단가 ($) | 현재가 ($) | 손익 ($) | 손익 (₩) | 수익률 |")
    lines.append("|------|------|----------:|----------:|---------:|---------:|-------:|")

    for name, tick, avg, price, qty, cost, value, profit, rate, best_return in results:
        p_str   = f"+${profit:,.2f}"      if profit >= 0 else f"-${abs(profit):,.2f}"
        pk_str  = f"+₩{profit*krw_rate:,.0f}" if profit >= 0 else f"-₩{abs(profit*krw_rate):,.0f}"
        r_str   = fmt_rate(rate)
        lines.append(
            f"| {name} | {tick} | ${avg:,.2f} | ${price:,.2f} "
            f"| {p_str} | {pk_str} | {r_str} |"
        )

    tp_str  = f"+${total_profit:,.2f}"          if total_profit >= 0 else f"-${abs(total_profit):,.2f}"
    tpk_str = f"+₩{total_profit*krw_rate:,.0f}" if total_profit >= 0 else f"-₩{abs(total_profit*krw_rate):,.0f}"
    tr_str  = fmt_rate(total_rate)
    lines.append(f"| **합계** | | | | **{tp_str}** | **{tpk_str}** | **{tr_str}** |")

    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 원화 환산 요약")
    lines.append("")
    lines.append("| 항목 | 금액 |")
    lines.append("|------|-----:|")
    lines.append(f"| 총 매수금액 | ₩{total_cost * krw_rate:,.0f} |")
    lines.append(f"| 총 평가금액 | ₩{total_value * krw_rate:,.0f} |")
    lines.append(f"| 총 손익 | {tpk_str} |")
    lines.append(f"| 총 수익률 | {tr_str} |")
    lines.append("")

    save_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n  💾 저장 완료: 매매노트/{month_dir}/{filename}")


# ── 메인 출력 ──────────────────────────────────

def print_portfolio():
    krw_rate = get_exchange_rate()
    history  = load_history()

    col_w = [16, 6, 10, 10, 12, 14, 9]
    header = (
        f"  {'종목':<{col_w[0]}} {'티커':<{col_w[1]}} "
        f"{'평단가($)':>{col_w[2]}} {'현재가($)':>{col_w[3]}} "
        f"{'손익($)':>{col_w[4]}} {'손익(₩)':>{col_w[5]}} {'수익률':>{col_w[6]}}"
    )
    sep = "  " + "─" * (sum(col_w) + len(col_w) + 4)

    total_cost  = 0.0
    total_value = 0.0
    results     = []
    alerts      = []

    # 각 종목 계산
    for stock in PORTFOLIO:
        name  = stock["name"]
        tick  = stock["ticker"]
        avg   = stock["avg_price"]
        qty   = stock["qty"]
        price = get_price(tick)

        if price is None:
            continue

        cost       = avg * qty
        value      = price * qty
        profit     = value - cost
        profit_krw = profit * krw_rate
        rate       = (price - avg) / avg * 100

        history    = update_best_return(history, tick, rate)
        best_return = history[tick]["best_return"]

        alert = check_alert(tick, name, rate, history)
        if alert:
            alerts.append(alert)

        total_cost  += cost
        total_value += value
        results.append((name, tick, avg, price, qty, cost, value, profit, rate, best_return))

    save_history(history)

    # ── 출력 시작 ──
    print()
    print("  ════════════════════════════════════════════════════════")
    print("           📊  나스닥 포트폴리오 수익률 현황")
    print("  ════════════════════════════════════════════════════════")
    print(f"  적용 환율: 1 USD = {krw_rate:,.0f}원")

    # 매도 알림 (최상단 출력)
    if alerts:
        print()
        print("  ┌─────────────────────────────────────────────────────┐")
        print("  │              ⚠️  매도 권장 알림                      │")
        print("  ├─────────────────────────────────────────────────────┤")
        for alert in alerts:
            print(f"  │  🚨 {yellow(alert):<60}│")
        print("  └─────────────────────────────────────────────────────┘")

    print()
    print(header)
    print(sep)

    for name, tick, avg, price, qty, cost, value, profit, rate, best_return in results:
        profit_krw     = profit * krw_rate
        rate_str       = color(fmt_rate(rate), rate)
        profit_str     = color(fmt_usd(profit), profit)
        profit_krw_str = color(("+") + fmt_krw(profit_krw) if profit_krw >= 0 else fmt_krw(profit_krw), profit_krw)
        print(
            f"  {name:<{col_w[0]}} {tick:<{col_w[1]}} "
            f"{fmt_usd_plain(avg):>{col_w[2]}} {fmt_usd_plain(price):>{col_w[3]}} "
            f"{profit_str:>{col_w[4]+9}} {profit_krw_str:>{col_w[5]+9}} {rate_str:>{col_w[6]+9}}"
        )

    if not results:
        print("  가격 조회에 실패했습니다. 인터넷 연결을 확인하세요.")
        return

    total_profit     = total_value - total_cost
    total_profit_krw = total_profit * krw_rate
    total_rate       = (total_profit / total_cost) * 100

    print(sep)
    print(
        f"  {'합계':<{col_w[0]}} {'':<{col_w[1]}} "
        f"{'':{col_w[2]}} {'':{col_w[3]}} "
        f"{color(fmt_usd(total_profit), total_profit):>{col_w[4]+9}} "
        f"{color(('+' if total_profit_krw >= 0 else '') + fmt_krw(total_profit_krw), total_profit_krw):>{col_w[5]+9}} "
        f"{color(fmt_rate(total_rate), total_rate):>{col_w[6]+9}}"
    )

    print()
    print("  ┌─── 원화 환산 요약 ──────────────────────────────────┐")
    print(f"  │  총 매수금액 : {fmt_krw(total_cost * krw_rate):<38}│")
    print(f"  │  총 평가금액 : {fmt_krw(total_value * krw_rate):<38}│")
    print(f"  │  총 손익     : {color(('+' if total_profit>=0 else '') + fmt_krw(total_profit * krw_rate), total_profit):<47}│")
    print(f"  │  총 수익률   : {color(fmt_rate(total_rate), total_rate):<47}│")
    print("  └─────────────────────────────────────────────────────┘")

    save_markdown(results, total_cost, total_value, krw_rate, alerts)
    print()


if __name__ == "__main__":
    print_portfolio()
