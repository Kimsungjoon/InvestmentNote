"""
스윙 매매 종목 스크리너 (듀얼 모드)
─────────────────────
투자기업가치기준.md 의 2단계(기술적 진입 기준)를 실제 일봉 데이터로 객관 검증한다.

★ 눌림목 모드: 5MA 근처 눌림 + RSI 40~70 + 이격 ≤4% + 거래량(당일≥20일평균×0.7)
☆ 모멘텀 모드: 5MA 가속(+4~10%) + 5-20MA 이격≤10% + RSI 55~80 + 신고가 근처 + 거래량 + RS>QQQ
"""

import requests

HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}

# ── 필터 기준 (원하면 숫자만 조정) ──
MIN_MARKETCAP_B = 10     # 시총 하한 (단위: 10억 달러). 중소형 포함, 마이크로캡만 제외
MAX_MARKETCAP_B = 1000   # 시총 상한 (단위: 10억 달러). 초과 시 "초대형(무거움)"으로 제외
# 시총 상한·단가 상한 예외: $1T·$600 초과여도 사이클·고베타로 스윙에 적합한 대형주
CAP_EXEMPT = {"MU"}      # 마이크론 — 메모리 슈퍼사이클, 고가·대형이지만 변동성 유지
MAX_PRICE       = 600    # 주당 단가 상한 ($). 초과 시 제외
RSI_MIN         = 40     # RSI 하한
RSI_MAX         = 70     # RSI 상한

# ── 손실 방지 보강 필터 (눌림목 모드) ──
MAX_EXT_5MA     = 4.0    # 5일선 이격 상한(%). 초과 시 '추격'으로 제외
REQUIRE_MARKET_REGIME = True  # 시장 레짐 필터: 지수(QQQ)가 약세면 전 종목 진입 보류
MARKET_INDEX    = "QQQ"  # 시장 레짐 판단 기준 지수

# ── 모멘텀 모드 (듀얼 출력) ──
MIN_EXT_5MA_MOM = 4.0    # 주가 vs 5일선 이격 하한 — 5MA 위 가속 구간
MAX_EXT_5MA_MOM = 10.0   # 주가 vs 5일선 이격 상한 (15→10 타이트)
MAX_MA5_20_SPREAD = 12.0 # 5일선 vs 20일선 이격 상한(%) — 단기·중기 과도 벌어짐 차단
MOM_RSI_MIN     = 55     # 모멘텀 RSI 하한 (강세 확인)
MOM_RSI_MAX     = 75     # 모멘텀 RSI 상한 (80→75 타이트)
MOM_FROM_HIGH   = -5.0   # 52주 고점 대비 % (-8→-5, 신고가에 더 가까워야 함)
MOM_VOLUME_MULT = 0.9    # 5일 평균 거래량 ≥ 20일 평균의 90% (매수세 지속)
MOM_REQUIRE_RS  = True   # 20일 수익률 > QQQ (상대강도)

# 눌림목 모드: 당일 거래량 ≥ 20일 평균 × VOLUME_MULT (매수세 최소 확인)
REQUIRE_VOLUME  = True   # 거래량 조건 활성화
VOLUME_MULT     = 0.7    # 거래량 기준 배수 (0.7 = 20일 평균의 70% 이상)

# 1단계 펀더멘털 필터 (투자기업가치기준.md — 비반도체 성장주 포용으로 완화)
REQUIRE_FUNDAMENTAL = True
MIN_ROE         = 0.10   # 수익성 경로: ROE 10% 이상
MAX_DEBT_EQUITY = 250    # 부채비율(D/E, %) 250% 이하 (필수)
HIGH_GROWTH     = 0.30   # 초고성장 경로: 매출성장 30%↑이면 적자여도 허용

# 후보 종목군 (반도체·AI인프라·우주항공·소프트웨어/SaaS·핀테크·인터넷·산업/방산 등 멀티섹터)
CANDIDATES = {
    "TSLA": "테슬라",
    "INTC": "인텔",
    "GEV":  "GE 버노바",
    "MRVL": "마벨 테크놀로지",
    "CRWD": "크라우드스트라이크",
    "PLTR": "팔란티어",
    "ORCL": "오라클",
    "AMD":  "AMD",
    "MU":   "마이크론",
    "ANET": "아리스타 네트웍스",
    "VRT":  "버티브 홀딩스",
    "NET":  "클라우드플레어",
    "RKLB": "로켓랩",
    "SMCI": "슈퍼마이크로",
    "DELL": "델 테크놀로지스",
    "NOW":  "서비스나우",
    "PANW": "팔로알토 네트웍스",
    "ASML": "ASML",
    "LRCX": "램리서치",
    "COIN": "코인베이스",
    # 확장 후보
    "AVGO": "브로드컴",
    "KLAC": "KLA",
    "AMAT": "어플라이드 머티어리얼즈",
    "TER":  "테라다인",
    "ARM":  "ARM 홀딩스",
    "SNPS": "시놉시스",
    "CDNS": "케이던스",
    "DDOG": "데이터독",
    "SNOW": "스노우플레이크",
    "ZS":   "지스케일러",
    "NXPI": "NXP 반도체",
    "ON":   "온세미",
    "MPWR": "모놀리식 파워",
    "ENPH": "엔페이즈",
    "FSLR": "퍼스트솔라",
    "CEG":  "콘스텔레이션 에너지",
    "VST":  "비스트라",
    "LMT":  "록히드마틴",
    "AXON": "액손 엔터프라이즈",
    "ASTS": "AST 스페이스모바일",
    # 중소형 미래산업 확장 (AI 연결/광/전력반도체/우주/원전/AI인프라)
    "ALAB": "아스테라 랩스",
    "CRDO": "크레도 테크놀로지",
    "LSCC": "래티스 반도체",
    "RMBS": "램버스",
    "AMBA": "암바렐라",
    "COHR": "코히어런트",
    "LITE": "루멘텀",
    "FN":   "파브리넷",
    "CLS":  "셀레스티카",
    "KTOS": "크라토스 디펜스",
    "LUNR": "인튜이티브 머신스",
    "RGTI": "리게티 컴퓨팅",
    "IONQ": "아이온큐",
    "OKLO": "오클로",
    "SMR":  "뉴스케일 파워",
    "NBIS": "네비우스",
    "IREN": "아이렌",
    "S":    "센티넬원",
    "GTLB": "깃랩",
    "MDB":  "몽고DB",
    "PSTG": "퓨어 스토리지",
    "ALTR": "알테어",
    "INDI": "인디 세미컨덕터",
    "POWI": "파워 인티그레이션스",
    "SITM": "사이타임",
    # ── 소프트웨어 / SaaS / 클라우드 확장 ──
    "CRM":  "세일즈포스",
    "ADBE": "어도비",
    "INTU": "인튜이트",
    "WDAY": "워크데이",
    "TEAM": "아틀라시안",
    "HUBS": "허브스팟",
    "SHOP": "쇼피파이",
    "VEEV": "비바 시스템스",
    "APP":  "앱러빈",
    "TWLO": "트윌리오",
    "OKTA": "옥타",
    "FTNT": "포티넷",
    "ESTC": "일래스틱",
    "CFLT": "컨플루언트",
    "PATH": "유아이패스",
    "U":    "유니티 소프트웨어",
    "RBLX": "로블록스",
    "DOCN": "디지털오션",
    "NTNX": "뉴타닉스",
    "FROG": "제이프로그",
    # ── 핀테크 / 결제 ──
    "XYZ":  "블록",
    "PYPL": "페이팔",
    "SOFI": "소파이",
    "AFRM": "어펌",
    "HOOD": "로빈후드",
    "TOST": "토스트",
    "NU":   "누 홀딩스",
    "BILL": "빌닷컴",
    # ── 인터넷 / 플랫폼 / 소비 ──
    "UBER": "우버",
    "ABNB": "에어비앤비",
    "DASH": "도어대시",
    "SPOT": "스포티파이",
    "RDDT": "레딧",
    "PINS": "핀터레스트",
    # ── 산업 / 방산 / 전력 인프라 ──
    "ETN":  "이튼",
    "PWR":  "콴타 서비시스",
    "NVT":  "엔베트",
    "PH":   "파커 하니핀",
    "HWM":  "하우멧 에어로스페이스",
    # ── 반도체 확장 (GPU·파운드리·메모리·아날로그) ──
    "NVDA": "엔비디아",
    "QCOM": "퀄컴",
    "TXN":  "텍사스 인스트루먼트",
    "ADI":  "아날로그 디바이스",
    "MCHP": "마이크로칩 테크놀로지",
    "TSM":  "TSMC",
    "WDC":  "웨스턴 디지털",
    "STX":  "시게이트 테크놀로지",
    "ENTG": "엔테그리스",
    "MKSI": "MKS",
    "ONTO": "온토 이노베이션",
    "ACLS": "악셀리스",
    # ── 빅테크 / 클라우드 / 플랫폼 ──
    "MSFT": "마이크로소프트",
    "GOOGL": "알파벳",
    "META": "메타 플랫폼스",
    "AMZN": "아마존",
    "NFLX": "넷플릭스",
    "CSCO": "시스코",
    "IBM":  "IBM",
    "HPE":  "HPE",
    "MNDY": "먼데이닷컴",
    "ROKU": "로쿠",
    # ── 네트워크 / 인프라 SW ──
    "AKAM": "아카마이",
    "FFIV": "F5",
    "CIEN": "시에나",
    # ── 사이버보안 확장 ──
    "CHKP": "체크포인트",
    "GEN":  "젠 디지털",
    "TENB": "테너블",
    "CYBR": "사이버아크",
    # ── 게임 / 디지털 콘텐츠 ──
    "EA":   "EA",
    "TTWO": "타케투 인터랙티브",
    # ── AI·데이터·엔터프라이즈 SW ──
    "AI":   "C3.ai",
    "BBAI": "빅베어.ai",
    "IOT":  "Samsara",
    "DT":   "Dynatrace",
    "PCTY": "Paylocity",
    "PCOR": "Procore",
}


def make_session() -> tuple[requests.Session, str]:
    """크럼 인증 세션 생성."""
    s = requests.Session()
    s.headers.update({"User-Agent": "Mozilla/5.0"})
    s.get("https://fc.yahoo.com", timeout=10)
    crumb = s.get("https://query1.finance.yahoo.com/v1/test/getcrumb",
                  timeout=10).text
    return s, crumb


def fetch_marketcaps(session, crumb, tickers: list[str]) -> dict:
    """여러 종목 시총을 한 번에 조회 (단위: 10억 달러)."""
    caps = {}
    try:
        symbols = ",".join(tickers)
        url = (f"https://query1.finance.yahoo.com/v7/finance/quote"
               f"?symbols={symbols}&crumb={crumb}")
        d = session.get(url, timeout=10).json()
        for q in d["quoteResponse"]["result"]:
            mc = q.get("marketCap")
            if mc:
                caps[q["symbol"]] = mc / 1e9
    except Exception as e:
        print(f"  ! 시총 조회 실패: {e}")
    return caps


def fetch_fundamentals(session, crumb, ticker: str) -> dict:
    """ROE·부채비율·매출성장·PER·흑자여부 조회."""
    try:
        url = (f"https://query1.finance.yahoo.com/v10/finance/quoteSummary/{ticker}"
               f"?modules=financialData,summaryDetail&crumb={crumb}")
        res = session.get(url, timeout=10).json()["quoteSummary"]["result"][0]
        fd = res.get("financialData", {})
        sd = res.get("summaryDetail", {})

        def raw(d, k):
            return d.get(k, {}).get("raw") if isinstance(d.get(k), dict) else None

        return {
            "roe": raw(fd, "returnOnEquity"),
            "debt_equity": raw(fd, "debtToEquity"),
            "rev_growth": raw(fd, "revenueGrowth"),
            "profit_margin": raw(fd, "profitMargins"),
            "per": raw(sd, "trailingPE"),
        }
    except Exception:
        return {}


def fetch_daily(ticker: str) -> dict | None:
    """1년치 일봉(종가, 거래량) 조회."""
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
        f"?interval=1d&range=1y"
    )
    try:
        res = requests.get(url, headers=HEADERS, timeout=10)
        data = res.json()
        result = data["chart"]["result"][0]
        closes = result["indicators"]["quote"][0]["close"]
        vols = result["indicators"]["quote"][0]["volume"]
        # None 값 제거 (휴장/누락)
        closes = [c for c in closes if c is not None]
        vols = [v for v in vols if v is not None]
        return {"close": closes, "volume": vols}
    except Exception as e:
        print(f"  ! {ticker} 조회 실패: {e}")
        return None


def sma(values: list[float], window: int) -> float | None:
    if len(values) < window:
        return None
    return sum(values[-window:]) / window


def rsi(values: list[float], period: int = 14) -> float | None:
    if len(values) < period + 1:
        return None
    gains, losses = [], []
    for i in range(-period, 0):
        diff = values[i] - values[i - 1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def ema(values: list[float], window: int) -> list[float]:
    k = 2 / (window + 1)
    ema_vals = [values[0]]
    for v in values[1:]:
        ema_vals.append(v * k + ema_vals[-1] * (1 - k))
    return ema_vals


def macd_hist(values: list[float]) -> tuple[float, float] | None:
    """MACD 히스토그램 (현재, 직전) 반환."""
    if len(values) < 35:
        return None
    ema12 = ema(values, 12)
    ema26 = ema(values, 26)
    macd_line = [a - b for a, b in zip(ema12, ema26)]
    signal = ema(macd_line, 9)
    hist = [m - s for m, s in zip(macd_line, signal)]
    return hist[-1], hist[-2]


def ret_20d(closes: list[float]) -> float | None:
    """20거래일 수익률(%)."""
    if len(closes) < 21:
        return None
    return (closes[-1] / closes[-21] - 1) * 100


def check_market_regime() -> dict:
    """시장 레짐 판단: 지수(QQQ)가 50일선 위 + 50일선 우상향이면 'risk-on'."""
    d = fetch_daily(MARKET_INDEX)
    if not d or len(d["close"]) < 55:
        # 데이터 못 받으면 보수적으로 통과(차단하지 않음)
        return {"ok": True, "price": None, "ma50": None, "reason": "데이터없음(통과)"}
    closes = d["close"]
    price = closes[-1]
    ma50 = sma(closes, 50)
    ma50_prev = sma(closes[:-5], 50)
    above_50 = ma50 is not None and price > ma50
    rising_50 = ma50_prev is not None and ma50 > ma50_prev
    ok = above_50 and rising_50
    reason = "위험(지수 약세)" if not ok else "양호(지수 강세)"
    bench_ret = ret_20d(closes)
    return {"ok": ok, "price": price, "ma50": ma50,
            "above_50": above_50, "rising_50": rising_50, "reason": reason,
            "ret_20d": bench_ret}


def analyze(ticker: str, name: str, marketcap: float | None = None,
            fund: dict | None = None, market_ok: bool = True,
            benchmark_ret_20d: float | None = None) -> dict | None:
    d = fetch_daily(ticker)
    if not d or len(d["close"]) < 60:
        return None
    closes = d["close"]
    vols = d["volume"]
    price = closes[-1]

    ma5 = sma(closes, 5)
    ma20 = sma(closes, 20)
    ma50 = sma(closes, 50)
    ma200 = sma(closes, 200)

    # 기울기: 5거래일 전 이동평균과 비교
    ma5_prev = sma(closes[:-5], 5) if len(closes) > 10 else None
    ma20_prev = sma(closes[:-5], 20) if len(closes) > 25 else None
    ma50_prev = sma(closes[:-5], 50) if len(closes) > 55 else None

    r = rsi(closes, 14)
    mh = macd_hist(closes)
    vol_now = vols[-1] if vols else None
    vol_avg20 = sma(vols, 20) if len(vols) >= 20 else None
    vol_avg5 = sma(vols, 5) if len(vols) >= 5 else None
    high_52w = max(closes)
    from_high = (price - high_52w) / high_52w * 100

    checks = {}
    # 추세(대형 프레임)
    checks["주가>50일선>200일선"] = (
        ma50 is not None and ma200 is not None and price > ma50 > ma200
    )
    checks["50일선 우상향"] = ma50_prev is not None and ma50 > ma50_prev
    checks["52주고점 -30% 이내"] = from_high >= -30
    # 단기 스윙
    checks["주가>5일선"] = ma5 is not None and price > ma5
    checks["5일선 우상향"] = ma5_prev is not None and ma5 > ma5_prev
    checks["5일선>20일선(골든)"] = ma5 is not None and ma20 is not None and ma5 > ma20
    checks["완전정배열(5>20>50)"] = (
        ma5 is not None and ma20 is not None and ma50 is not None
        and ma5 > ma20 > ma50
    )
    # 모멘텀
    checks[f"RSI {RSI_MIN}~{RSI_MAX}"] = r is not None and RSI_MIN <= r <= RSI_MAX
    rsi_key = f"RSI {RSI_MIN}~{RSI_MAX}"
    checks["MACD 히스토 상승"] = mh is not None and mh[0] > mh[1]
    checks["거래량>20일평균"] = (
        vol_now is not None and vol_avg20 is not None
        and vol_now > vol_avg20 * VOLUME_MULT
    )

    score = sum(1 for v in checks.values() if v)

    # ── 진입 적격 판정 (사람 판단을 코드화) ──
    # 1) 상승 추세 배경: 주가>50>200 & 50일선 우상향
    trend_ok = checks["주가>50일선>200일선"] and checks["50일선 우상향"]
    # 2) RSI 건전: 40~65 (과매수 추격 차단)
    rsi_ok = checks[rsi_key]
    # 3) 단기 정배열: 5일선>20일선, 그리고 완전 정배열(5>20>50) 유지
    short_ok = checks["5일선>20일선(골든)"] and checks["완전정배열(5>20>50)"]
    # 4) 진입 여력: 신고점 과열 아님 (RSI 상한 이내)
    not_overbought = r is not None and r <= RSI_MAX
    ext_5ma = ((price - ma5) / ma5 * 100) if ma5 else 0  # 주가 vs 5일선 이격도
    ext_5_20 = ((ma5 - ma20) / ma20 * 100) if (ma5 and ma20) else 0  # 5일선 vs 20일선 이격

    # ── 1단계 펀더멘털 필터 ──
    fund = fund or {}
    roe = fund.get("roe")
    dte = fund.get("debt_equity")
    rev_g = fund.get("rev_growth")
    pmargin = fund.get("profit_margin")
    per = fund.get("per")

    # 필수 조건: 부채비율 + 매출성장(역성장 아님). 부채 데이터 없으면 통과로 간주.
    f_debt = dte is None or dte <= MAX_DEBT_EQUITY
    f_growth = rev_g is not None and rev_g > 0
    # 수익성/성장 경로 (하나 이상)
    p_roe = roe is not None and roe >= MIN_ROE          # ① 수익성
    p_profit = pmargin is not None and pmargin > 0       # ② 흑자
    p_hyper = rev_g is not None and rev_g >= HIGH_GROWTH # ③ 초고성장
    quality_ok = p_roe or p_profit or p_hyper

    fundamental_pass = f_debt and f_growth and quality_ok
    fund_checks = {"부채비율≤250%": f_debt, "매출성장": f_growth,
                   "수익성/성장(ROE10%or흑자or성장30%)": quality_ok}

    # 시총·단가 필터
    cap_ok = (
        marketcap is None
        or (MIN_MARKETCAP_B <= marketcap <= MAX_MARKETCAP_B)
        or (ticker in CAP_EXEMPT and marketcap >= MIN_MARKETCAP_B)
    )
    price_ok = price <= MAX_PRICE or ticker in CAP_EXEMPT

    # 지금 당장 진입: 주가가 5일선 위(단기 모멘텀 확인) + 거래량 동반
    above_5ma = checks["주가>5일선"]
    vol_ok = checks["거래량>20일평균"]
    # 5일선 이격 상한: 너무 멀리 떨어져 오른 종목은 '추격'으로 제외 (되돌림 방지)
    ext_ok = ext_5ma <= MAX_EXT_5MA

    pullback_grade = (trend_ok and rsi_ok and short_ok and not_overbought
                      and cap_ok and price_ok and above_5ma and ext_ok)
    if REQUIRE_VOLUME:
        pullback_grade = pullback_grade and vol_ok
    if REQUIRE_FUNDAMENTAL:
        pullback_grade = pullback_grade and fundamental_pass
    if REQUIRE_MARKET_REGIME:
        pullback_grade = pullback_grade and market_ok

    # ── 모멘텀 적격: 가속·신고가 근처·거래량·상대강도 ──
    stock_ret_20d = ret_20d(closes)
    mom_ext_ok = MIN_EXT_5MA_MOM <= ext_5ma <= MAX_EXT_5MA_MOM
    mom_ma_spread_ok = ext_5_20 <= MAX_MA5_20_SPREAD
    mom_rsi_ok = r is not None and MOM_RSI_MIN <= r <= MOM_RSI_MAX
    mom_near_high = from_high >= MOM_FROM_HIGH
    mom_vol_ok = (
        vol_avg5 is not None and vol_avg20 is not None
        and vol_avg5 > vol_avg20 * MOM_VOLUME_MULT
    )
    rs_ok = True
    if MOM_REQUIRE_RS and benchmark_ret_20d is not None and stock_ret_20d is not None:
        rs_ok = stock_ret_20d > benchmark_ret_20d

    momentum_grade = (trend_ok and short_ok and above_5ma and mom_ext_ok
                      and mom_ma_spread_ok and mom_rsi_ok and mom_near_high
                      and mom_vol_ok and checks["MACD 히스토 상승"] and rs_ok
                      and cap_ok and price_ok)
    if REQUIRE_FUNDAMENTAL:
        momentum_grade = momentum_grade and fundamental_pass
    if REQUIRE_MARKET_REGIME:
        momentum_grade = momentum_grade and market_ok

    # 눌림목 점수: RSI가 45~60 중앙일수록, 5일선 이격이 작을수록 우수
    pullback_quality = 0.0
    if pullback_grade:
        pullback_quality = 100 - abs(r - 52) - abs(ext_5ma) * 2

    # 모멘텀 점수: 이격·거래량·신고가 근접·RSI 강세
    momentum_quality = 0.0
    if momentum_grade:
        vr = (vol_avg5 / vol_avg20) if (vol_avg5 and vol_avg20) else 1
        vol_score = min(vr * 10, 30)
        momentum_quality = (
            min(ext_5ma, MAX_EXT_5MA_MOM) * 2
            + min(-from_high, 5) * 3
            + min(MAX_MA5_20_SPREAD - ext_5_20, 5) * 2
            + vol_score
            + (r - MOM_RSI_MIN) * 0.5
        )

    entry_grade = pullback_grade  # 하위 호환

    return {
        "ticker": ticker, "name": name, "price": price,
        "marketcap": marketcap, "cap_ok": cap_ok, "price_ok": price_ok,
        "roe": roe, "debt_equity": dte, "rev_growth": rev_g, "per": per,
        "fundamental_pass": fundamental_pass, "fund_checks": fund_checks,
        "ma5": ma5, "ma20": ma20, "ma50": ma50, "ma200": ma200,
        "rsi": r, "macd_hist": mh[0] if mh else None,
        "from_high": from_high, "ext_5ma": ext_5ma, "ext_5_20": ext_5_20,
        "ext_ok": ext_ok, "mom_ma_spread_ok": mom_ma_spread_ok,
        "ret_20d": stock_ret_20d,
        "vol_ratio": (vol_now / vol_avg20) if (vol_now and vol_avg20) else None,
        "vol_ratio_5d": (vol_avg5 / vol_avg20) if (vol_avg5 and vol_avg20) else None,
        "checks": checks, "score": score,
        "pullback_grade": pullback_grade, "momentum_grade": momentum_grade,
        "entry_grade": entry_grade,
        "pullback_quality": pullback_quality, "momentum_quality": momentum_quality,
    }


def fmt(v, suffix="", nd=2):
    if v is None:
        return "N/A"
    return f"{v:,.{nd}f}{suffix}"


def print_stock_block(a: dict, quality: float, quality_label: str):
    cap_str = f"${a['marketcap']:,.0f}B" if a['marketcap'] else "N/A"
    print(f"\n  {a['name']} ({a['ticker']})   점수 {a['score']}/{len(a['checks'])} | "
          f"{quality_label} {quality:.0f} | 시총 {cap_str}")
    print(f"    현재가 ${fmt(a['price'])} | RSI {fmt(a['rsi'], nd=1)} | "
          f"고점대비 {fmt(a['from_high'], '%', 1)} | "
          f"5일선이격 {fmt(a['ext_5ma'], '%', 1)} | "
          f"5-20MA {fmt(a.get('ext_5_20'), '%', 1)} | "
          f"거래량(5d) {fmt(a.get('vol_ratio_5d'), 'x', 2)}")
    ret_s = f"{a['ret_20d']:.1f}%" if a.get('ret_20d') is not None else "N/A"
    print(f"    20일수익률 {ret_s} | "
          f"5MA ${fmt(a['ma5'])} | 20MA ${fmt(a['ma20'])} | 50MA ${fmt(a['ma50'])}")
    roe_s = f"{a['roe']*100:.1f}%" if a['roe'] is not None else "N/A"
    dte_s = f"{a['debt_equity']:.0f}%" if a['debt_equity'] is not None else "N/A"
    rg_s = f"{a['rev_growth']*100:.1f}%" if a['rev_growth'] is not None else "N/A"
    per_s = f"{a['per']:.1f}" if a['per'] is not None else "N/A"
    print(f"    [펀더멘털] ROE {roe_s} | 부채비율 {dte_s} | "
          f"매출성장 {rg_s} | PER {per_s}")


def exclusion_tag(a: dict, market_ok: bool) -> str:
    rsi_key = f"RSI {RSI_MIN}~{RSI_MAX}"
    if a["pullback_grade"]:
        return "★눌림"
    if a["momentum_grade"]:
        return "☆모멘"
    if not a["cap_ok"]:
        return "시총↓" if (a["marketcap"] and a["marketcap"] < MIN_MARKETCAP_B) else "시총↑"
    if not a["price_ok"]:
        return "단가↑"
    if REQUIRE_MARKET_REGIME and not market_ok:
        return "시장위험"
    if REQUIRE_FUNDAMENTAL and not a["fundamental_pass"]:
        failed = [k for k, v in a["fund_checks"].items() if not v]
        return "펀더:" + (failed[0] if failed else "?")
    if not a["checks"]["완전정배열(5>20>50)"]:
        return "정배열X"
    if not a["checks"]["주가>5일선"]:
        return "5MA아래"
    # 모멘텀 구간 판정 (주가/5MA 4% 이상)
    if a["ext_5ma"] > MAX_EXT_5MA_MOM:
        return "이격↑"
    if a["ext_5ma"] >= MIN_EXT_5MA_MOM and not a.get("mom_ma_spread_ok", True):
        return "5-20↑"
    if a["ext_5ma"] < MIN_EXT_5MA_MOM and not a.get("ext_ok", True):
        return "이격↑"  # 눌림목: 5MA 대비 +4% 초과
    if REQUIRE_VOLUME and not a["checks"].get("거래량>20일평균", False):
        return "거래량↓"
    if a["ext_5ma"] < MIN_EXT_5MA_MOM:
        return "가속↓"
    if not a["checks"].get(rsi_key, False) and (a["rsi"] or 0) < MOM_RSI_MIN:
        return "RSI↓"
    if a["rsi"] and a["rsi"] > MOM_RSI_MAX:
        return "RSI↑"
    if a["from_high"] < MOM_FROM_HIGH:
        return "고점↓"
    vol_r = a.get("vol_ratio_5d") or a.get("vol_ratio")
    if vol_r is not None and vol_r < MOM_VOLUME_MULT:
        return "거래량↓"
    return "추세"


def main():
    print("\n" + "=" * 78)
    print("  스윙 매매 기술적 스크리너 — 듀얼 모드 (눌림목 + 모멘텀)")
    exempt = f" (예외:{','.join(sorted(CAP_EXEMPT))})" if CAP_EXEMPT else ""
    vol_line = f" | 거래량≥{VOLUME_MULT}x" if REQUIRE_VOLUME else ""
    print(f"  [눌림목] 시총 ${MIN_MARKETCAP_B}~${MAX_MARKETCAP_B}B{exempt} | 단가≤${MAX_PRICE} | "
          f"RSI {RSI_MIN}~{RSI_MAX} | 5MA이격≤{MAX_EXT_5MA}%{vol_line}")
    print(f"  [모멘텀] 주가/5MA {MIN_EXT_5MA_MOM}~{MAX_EXT_5MA_MOM}% | 5-20MA≤{MAX_MA5_20_SPREAD}% | "
          f"RSI {MOM_RSI_MIN}~{MOM_RSI_MAX} | 고점≥{MOM_FROM_HIGH}% | "
          f"거래량≥{MOM_VOLUME_MULT}x | RS>QQQ")
    print("=" * 78)

    session, crumb = make_session()

    # 시장 레짐 판단 (지수 약세면 전 종목 진입 보류)
    regime = check_market_regime()
    market_ok = regime["ok"] or not REQUIRE_MARKET_REGIME
    reg_price = fmt(regime.get("price"))
    reg_ma50 = fmt(regime.get("ma50"))
    flag = "🟢 진입 허용" if regime["ok"] else "🔴 진입 보류"
    bench_ret = regime.get("ret_20d")
    bench_ret_s = f"{bench_ret:.1f}%" if bench_ret is not None else "N/A"
    print(f"  [시장 레짐] {MARKET_INDEX} {reg_price} vs 50일선 {reg_ma50} "
          f"→ {regime['reason']}  {flag} | QQQ 20일 {bench_ret_s}")
    if REQUIRE_MARKET_REGIME and not regime["ok"]:
        print("  ⚠ 지수 약세 → 눌림목·모멘텀 모두 진입 보류.")
    print("=" * 78)

    caps = fetch_marketcaps(session, crumb, list(CANDIDATES.keys()))

    results = []
    for tick, name in CANDIDATES.items():
        fund = fetch_fundamentals(session, crumb, tick)
        a = analyze(tick, name, caps.get(tick), fund, market_ok=market_ok,
                    benchmark_ret_20d=bench_ret)
        if a:
            results.append(a)

    pullbacks = sorted(
        [a for a in results if a["pullback_grade"]],
        key=lambda x: (x["pullback_quality"], x["score"]), reverse=True,
    )
    momentums = sorted(
        [a for a in results if a["momentum_grade"]],
        key=lambda x: (x["momentum_quality"], x["score"]), reverse=True,
    )
    results.sort(
        key=lambda x: (x["pullback_grade"], x["momentum_grade"],
                       x["pullback_quality"], x["momentum_quality"], x["score"]),
        reverse=True,
    )

    print(f"\n{'='*78}")
    print(f"  ★ 눌림목 적격 (5MA이격≤{MAX_EXT_5MA}%, RSI {RSI_MIN}~{RSI_MAX}): {len(pullbacks)}개")
    print("=" * 78)
    for a in pullbacks:
        print_stock_block(a, a["pullback_quality"], "눌림목품질")

    print(f"\n{'='*78}")
    print(f"  ☆ 모멘텀 적격 (주가/5MA {MIN_EXT_5MA_MOM}~{MAX_EXT_5MA_MOM}%, "
          f"5-20MA≤{MAX_MA5_20_SPREAD}%, RSI {MOM_RSI_MIN}~{MOM_RSI_MAX}): {len(momentums)}개")
    print("=" * 78)
    for a in momentums:
        print_stock_block(a, a["momentum_quality"], "모멘텀점수")

    # 4종목 매수 템플릿: 눌림목 2 + 모멘텀 2 (중복 제외)
    pick_pb = pullbacks[:2]
    pick_mom = [a for a in momentums if a["ticker"] not in {x["ticker"] for x in pick_pb}][:2]
    print(f"\n{'='*78}")
    print("  📋 매수 후보 (눌림목 2 + 모멘텀 2)")
    print("=" * 78)
    if pick_pb or pick_mom:
        if pick_pb:
            print("  [눌림목]")
            for i, a in enumerate(pick_pb, 1):
                print(f"    {i}. {a['name']} ({a['ticker']}) — "
                      f"${fmt(a['price'])} | RSI {fmt(a['rsi'], nd=1)} | "
                      f"5MA이격 {fmt(a['ext_5ma'], '%', 1)}")
        if pick_mom:
            print("  [모멘텀]")
            for i, a in enumerate(pick_mom, 1):
                print(f"    {i}. {a['name']} ({a['ticker']}) — "
                      f"${fmt(a['price'])} | RSI {fmt(a['rsi'], nd=1)} | "
                      f"5MA이격 {fmt(a['ext_5ma'], '%', 1)} | "
                      f"거래량(5d) {fmt(a.get('vol_ratio_5d'), 'x', 2)}")
        if len(pick_pb) < 2 or len(pick_mom) < 2:
            print(f"  ⚠ 눌림목 {len(pick_pb)}/2 · 모멘텀 {len(pick_mom)}/2 — "
                  "부족하면 다음 후보 또는 FOMC 이후 재실행.")
    else:
        print("  ⚠ 적격 종목 없음 — 시장 레짐·이벤트 확인 후 재실행.")

    print(f"\n{'='*78}")
    print("  전체 요약 (눌림목·모멘텀 우선 → 점수순)")
    print("=" * 78)
    print(f"  {'종목':<18}{'티커':<7}{'현재가':>10}{'시총($B)':>9}{'RSI':>5}"
          f"{'5MA이격':>8}{'점수':>6}{'적격/제외':>10}")
    for a in results:
        cap_str = f"{a['marketcap']:,.0f}" if a['marketcap'] else "N/A"
        tag = exclusion_tag(a, market_ok)
        print(f"  {a['name']:<16}{a['ticker']:<7}"
              f"{fmt(a['price']):>10}{cap_str:>9}{fmt(a['rsi'], nd=0):>5}"
              f"{fmt(a['ext_5ma'], '%', 1):>9}{a['score']:>3}/{len(a['checks'])}{tag:>10}")
    print()


if __name__ == "__main__":
    main()
