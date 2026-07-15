"""
나스닥 / 코스피 매수 후보발굴 스크리너
────────────────────────────────────────────────────────────────────────
나스닥: QQQ 상대강도 · 20/50/200일선 추세 · 눌림목 / 돌파 / 에너지응축
코스피: 안전마진(PBR·PER) · 실적모멘텀(매출성장·목표가괴리율)
        · 수급전환(네이버 기관·외국인 순매수) · 재무안정성(ROE·부채비율)
────────────────────────────────────────────────────────────────────────
사용법:
  python3 screener.py            나스닥 스캔 (기본)
  python3 screener.py --kospi    코스피 스캔
"""

import argparse
import concurrent.futures as cf
import re
import requests
from bs4 import BeautifulSoup

HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
INCOMPLETE_VOL_RATIO = 0.50  # 당일 거래량이 20일 평균의 50% 미만이면 장중 미완성 봉으로 판단

# ══════════════════════════════════════════════════════════════════════
#  나스닥 설정
# ══════════════════════════════════════════════════════════════════════
NASDAQ_INDEX  = "QQQ"
ND_MIN_CAP_B  = 10      # 시총 하한 ($B) — 마이크로캡 제외
ND_MAX_PRICE  = 800     # 단가 상한 ($)
ND_MAX_DEBT   = 200     # 부채비율 상한 (%)

# ── 눌림목: 상승추세 + 5/20MA 근처 조정 + 거래량 수축 ──
PB_5MA_RANGE  = 2.5     # 5MA 이격 ±2.5% 이내
PB_FROM_20MA  = 4.0     # 20MA 아래 최대 -4%까지 허용
PB_RSI_MIN    = 38
PB_RSI_MAX    = 65
PB_VOL_MAX    = 1.05    # 5d/20d vol ≤ 1.05 (거래량 수축)

# ── 돌파: 52주 고점 근접 + 거래량 급증 ──
BK_FROM_HIGH  = -6.0    # 52주 고점 대비 -6% 이내
BK_RSI_MIN    = 58
BK_RSI_MAX    = 82
BK_VOL_MIN    = 1.1     # 5d/20d vol ≥ 1.1 (거래량 동반)

# ── 에너지응축: 5/20/50MA 간격 좁은 삼각수렴 구간 ──
SQ_5_20_MAX   = 5.0     # |5MA - 20MA| / 20MA (%)
SQ_20_50_MAX  = 8.0     # |20MA - 50MA| / 50MA (%)
SQ_RANGE_MAX  = 14.0    # 20일 가격 레인지 / 현재가 (%)
SQ_RSI_MIN    = 43
SQ_RSI_MAX    = 70

# ══════════════════════════════════════════════════════════════════════
#  코스피 설정
# ══════════════════════════════════════════════════════════════════════
KOSPI_INDEX   = "^KS11"

KS_PBR_MAX    = 1.5     # PBR 상한 (안전마진 기준)
KS_PBR_GOOD   = 1.0     # PBR 우수 기준 (2점)
KS_PER_MAX    = 22      # PER 상한
KS_PER_GOOD   = 12      # PER 우수 기준 (2점)
KS_ROE_MIN    = 0.07    # ROE 하한 (7%)
KS_DEBT_MAX   = 200     # 부채비율 상한 (%)
KS_RSI_MIN    = 38
KS_RSI_MAX    = 70
KS_FLOW_DAYS  = 5       # 수급 가점: 기관+외국인 N일 누적 순매수 > 0
KS_TGT_GAP    = 0.15    # 목표주가 괴리율 하한 (15%)

# ══════════════════════════════════════════════════════════════════════
#  나스닥 종목 리스트 (~220개, 나스닥100 + 성장/모멘텀 확장 유니버스)
# ══════════════════════════════════════════════════════════════════════
NASDAQ_CANDIDATES = {
    # ── 반도체 / GPU / 장비 ──
    "NVDA": "엔비디아",         "AMD":  "AMD",
    "AVGO": "브로드컴",         "MU":   "마이크론",
    "MRVL": "마벨 테크놀로지",   "AMAT": "어플라이드 머티어리얼즈",
    "LRCX": "램리서치",         "KLAC": "KLA",
    "ASML": "ASML",            "ARM":  "ARM 홀딩스",
    "QCOM": "퀄컴",             "TXN":  "텍사스 인스트루먼트",
    "ADI":  "아날로그 디바이스", "TSM":  "TSMC",
    "SNPS": "시놉시스",         "CDNS": "케이던스",
    "INTC": "인텔",             "WDC":  "웨스턴 디지털",
    "ON":   "온세미",           "NXPI": "NXP 반도체",
    "MPWR": "모놀리식 파워",     "ALAB": "아스테라 랩스",
    "CRDO": "크레도 테크놀로지", "COHR": "코히어런트",
    "LITE": "루멘텀",           "LSCC": "래티스 반도체",
    "ONTO": "온토 이노베이션",   "ENTG": "엔테그리스",
    "MKSI": "MKS",             "SITM": "사이타임",
    "CLS":  "셀레스티카",
    "SWKS": "스카이웍스",       "QRVO": "코르보",
    "MTSI": "MACOM",           "CRUS": "시러스 로직",
    "DIOD": "다이오즈",         "VSH":  "비쉐이 인터테크놀로지",
    "AEIS": "어드밴스드 에너지","UCTT": "울트라 클린 홀딩스",
    "ICHR": "아이커 시스템즈",   "AMKR": "앰코 테크놀로지",
    "NVMI": "노바 엘티디",      "CEVA": "세바",
    "SLAB": "실리콘 랩스",      "MXL":  "맥스리니어",
    "AOSL": "알파앤오메가 세미컨덕터","SYNA": "시냅틱스",
    "WOLF": "울프스피드",
    # ── AI 인프라 / 서버 / 네트워킹 ──
    "ANET": "아리스타 네트웍스", "DELL": "델 테크놀로지스",
    "HPE":  "HPE",             "VRT":  "버티브 홀딩스",
    "SMCI": "슈퍼마이크로",     "CSCO": "시스코",
    "FFIV": "F5",              "AKAM": "아카마이",
    "CIEN": "시에나",          "NBIS": "네비우스",
    "JNPR": "쥬니퍼 네트웍스",
    # ── 빅테크 / 클라우드 ──
    "MSFT": "마이크로소프트",   "GOOGL":"알파벳",
    "META": "메타 플랫폼스",   "AMZN": "아마존",
    "NFLX": "넷플릭스",        "IBM":  "IBM",
    # ── 사이버보안 ──
    "CRWD": "크라우드스트라이크","PANW": "팔로알토",
    "FTNT": "포티넷",          "NET":  "클라우드플레어",
    "ZS":   "지스케일러",      "OKTA": "옥타",
    "S":    "센티넬원",        "CHKP": "체크포인트",
    "TENB": "테너블",          "RPD":  "래피드7",
    "VRNS": "베라토",          "QLYS": "퀄리스",
    # ── SaaS / 소프트웨어 ──
    "NOW":  "서비스나우",       "CRM":  "세일즈포스",
    "ADBE": "어도비",          "INTU": "인튜이트",
    "WDAY": "워크데이",        "ORCL": "오라클",
    "TEAM": "아틀라시안",      "HUBS": "허브스팟",
    "MDB":  "몽고DB",          "SNOW": "스노우플레이크",
    "DDOG": "데이터독",        "PLTR": "팔란티어",
    "APP":  "앱러빈",          "SHOP": "쇼피파이",
    "TWLO": "트윌리오",        "IOT":  "Samsara",
    "DT":   "Dynatrace",      "NTNX": "뉴타닉스",
    "VEEV": "비바 시스템스",    "GTLB": "깃랩",
    "ZM":   "줌 커뮤니케이션즈","DOCU": "도큐사인",
    "ESTC": "일래스틱",        "ASAN": "아사나",
    "SMAR": "스마트시트",      "BOX":  "박스",
    "DBX":  "드롭박스",        "PD":   "페이저듀티",
    "BRZE": "브레이즈",        "YEXT": "옉스트",
    "CFLT": "컨플루언트",      "ALTR": "알테어",
    "PATH": "유아이패스",      "FROG": "제이프로그",
    "PCTY": "페이로시티",      "PCOR": "프로코어",
    "BILL": "빌닷컴",          "U":    "유니티 소프트웨어",
    "RBLX": "로블록스",        "DOCN": "디지털오션",
    # ── 핀테크 / 결제 ──
    "XYZ":  "블록",            "PYPL": "페이팔",
    "COIN": "코인베이스",      "SOFI": "소파이",
    "AFRM": "어펌",            "HOOD": "로빈후드",
    "NU":   "누 홀딩스",       "TOST": "토스트",
    "GPN":  "글로벌 페이먼츠", "FIS":  "FIS",
    "PAYX": "페이첵스",        "ADP":  "오토매틱 데이터 프로세싱",
    # ── 인터넷 / 플랫폼 ──
    "UBER": "우버",            "ABNB": "에어비앤비",
    "DASH": "도어대시",        "SPOT": "스포티파이",
    "RDDT": "레딧",            "PINS": "핀터레스트",
    "MTCH": "매치그룹",        "EXPE": "익스피디아",
    "MELI": "메르카도리브레",   "EBAY": "이베이",
    "ETSY": "엣시",            "W":    "웨이페어",
    # ── 중국 ADR (나스닥 상장) ──
    "PDD":  "핀둬둬",          "JD":   "JD닷컴",
    "BIDU": "바이두",          "NTES": "넷이즈",
    "TCOM": "트립닷컴",        "BILI": "빌리빌리",
    # ── 소비 / 리테일 (나스닥) ──
    "COST": "코스트코",        "SBUX": "스타벅스",
    "BKNG": "부킹홀딩스",      "LULU": "룰루레몬",
    "ROST": "로스 스토어스",   "ORLY": "오라일리 오토모티브",
    "CHWY": "츄이",            "WING": "윙스탑",
    "CAVA": "카바 그룹",       "DKNG": "드래프트킹스",
    "ULTA": "울타뷰티",
    # ── 통신 / 케이블 ──
    "TMUS": "T모바일",         "CMCSA":"컴캐스트",
    "CHTR": "차터 커뮤니케이션즈",
    # ── 바이오 / 제약 (나스닥 대형) ──
    "GILD": "길리어드 사이언스","VRTX": "버텍스 파마슈티컬",
    "REGN": "리제네론",        "AMGN": "암젠",
    "BIIB": "바이오젠",        "MRNA": "모더나",
    "ILMN": "일루미나",        "ALNY": "알나일람",
    "BMRN": "바이오마린",      "INCY": "인사이트",
    "EXAS": "이그젝트 사이언스","NBIX": "뉴로크린 바이오사이언스",
    "IONS": "아이오닉스 파마슈티컬",
    # ── 전력 / 에너지 / 산업 ──
    "VST":  "비스트라",        "CEG":  "콘스텔레이션 에너지",
    "GEV":  "GE 버노바",       "ETN":  "이튼",
    "PWR":  "콴타 서비시스",   "HWM":  "하우멧 에어로스페이스",
    "PH":   "파커 하니핀",     "NVT":  "엔베트",
    "CSX":  "CSX",             "PCAR": "팩카",
    "ODFL": "올드 도미니언",   "FAST": "패스널",
    "PAYC": "페이콤",          "JBHT": "제이비 헌트",
    # ── 방산 / 우주 ──
    "LMT":  "록히드마틴",      "AXON": "액손 엔터프라이즈",
    "KTOS": "크라토스 디펜스", "RKLB": "로켓랩",
    # ── EV / 재생에너지 ──
    "TSLA": "테슬라",          "RIVN": "리비안",
    "LCID": "루시드 모터스",   "SEDG": "솔라엣지",
    "RUN":  "선런",            "CHPT": "차지포인트",
    "BE":   "블룸 에너지",
    # ── AI 특화 소형 ──
    "ASTS": "AST 스페이스모바일","IONQ": "아이온큐",
    "BBAI": "빅베어.ai",       "SOUN": "사운드하운드 AI",
    "UPST": "업스타트",        "AI":   "C3.ai",
}

# ══════════════════════════════════════════════════════════════════════
#  코스피 종목 리스트 (~120개, 코스피 시총 상위 + 섹터 대표주 확장)
# ══════════════════════════════════════════════════════════════════════
KOSPI_CANDIDATES = {
    # ── 반도체 / IT부품 ──
    "005930.KS": "삼성전자",       "000660.KS": "SK하이닉스",
    "011070.KS": "LG이노텍",       "402340.KS": "SK스퀘어",
    "009150.KS": "삼성전기",       "000990.KS": "DB하이텍",
    # ── 2차전지 / 화학 ──
    "373220.KS": "LG에너지솔루션", "006400.KS": "삼성SDI",
    "051910.KS": "LG화학",         "096770.KS": "SK이노베이션",
    "011170.KS": "롯데케미칼",     "010950.KS": "S-Oil",
    "011790.KS": "SKC",            "003670.KS": "포스코퓨처엠",
    "361610.KS": "SK아이이테크놀로지","011780.KS": "금호석유",
    "009830.KS": "한화솔루션",
    # ── 자동차 / 부품 ──
    "005380.KS": "현대차",         "000270.KS": "기아",
    "012330.KS": "현대모비스",     "204320.KS": "만도",
    "018880.KS": "한온시스템",     "011210.KS": "현대위아",
    "307950.KS": "현대오토에버",
    # ── 방산 / 조선 / 중공업 ──
    "012450.KS": "한화에어로스페이스","047810.KS": "한국항공우주",
    "079550.KS": "LIG넥스원",      "042660.KS": "한화오션",
    "009540.KS": "HD한국조선해양", "010620.KS": "HD현대미포",
    "267250.KS": "HD현대",         "329180.KS": "HD현대중공업",
    "034020.KS": "두산에너빌리티", "010140.KS": "삼성중공업",
    "064350.KS": "현대로템",       "272210.KS": "한화시스템",
    # ── 금융 (은행/지주) ──
    "105560.KS": "KB금융",         "055550.KS": "신한지주",
    "086790.KS": "하나금융지주",   "316140.KS": "우리금융지주",
    "138930.KS": "BNK금융지주",    "175330.KS": "JB금융지주",
    "024110.KS": "기업은행",       "071050.KS": "한국금융지주",
    "138040.KS": "메리츠금융지주",
    # ── 보험 / 증권 / 카드 ──
    "032830.KS": "삼성생명",       "000810.KS": "삼성화재",
    "001450.KS": "현대해상",       "005830.KS": "DB손해보험",
    "006800.KS": "미래에셋증권",   "016360.KS": "삼성증권",
    "039490.KS": "키움증권",       "005940.KS": "NH투자증권",
    "088350.KS": "한화생명",       "029780.KS": "삼성카드",
    "003690.KS": "코리안리",
    # ── 철강 / 소재 ──
    "005490.KS": "POSCO홀딩스",    "004020.KS": "현대제철",
    "010130.KS": "고려아연",
    # ── 지주회사 ──
    "034730.KS": "SK",             "003550.KS": "LG",
    "000880.KS": "한화",           "004990.KS": "롯데지주",
    "001040.KS": "CJ",             "000210.KS": "DL",
    "000150.KS": "두산",           "004800.KS": "효성",
    # ── 건설 ──
    "000720.KS": "현대건설",       "006360.KS": "GS건설",
    "047040.KS": "대우건설",       "028050.KS": "삼성엔지니어링",
    "028260.KS": "삼성물산",
    # ── 바이오 / 제약 ──
    "068270.KS": "셀트리온",       "207940.KS": "삼성바이오로직스",
    "128940.KS": "한미약품",       "000100.KS": "유한양행",
    "185750.KS": "종근당",         "069620.KS": "대웅제약",
    "326030.KS": "SK바이오팜",     "302440.KS": "SK바이오사이언스",
    # ── 인터넷 / 플랫폼 / 게임 ──
    "035420.KS": "NAVER",          "035720.KS": "카카오",
    "259960.KS": "크래프톤",       "036570.KS": "엔씨소프트",
    "251270.KS": "넷마블",         "323410.KS": "카카오뱅크",
    "377300.KS": "카카오페이",     "018260.KS": "삼성에스디에스",
    # ── 유통 / 소비재 ──
    "139480.KS": "이마트",         "023530.KS": "롯데쇼핑",
    "097950.KS": "CJ제일제당",     "051900.KS": "LG생활건강",
    "090430.KS": "아모레퍼시픽",   "007070.KS": "GS리테일",
    "271560.KS": "오리온",         "004370.KS": "농심",
    "004170.KS": "신세계",         "069960.KS": "현대백화점",
    "282330.KS": "BGF리테일",      "007310.KS": "오뚜기",
    "000080.KS": "하이트진로",     "033780.KS": "KT&G",
    "192820.KS": "코스맥스",
    # ── 통신 ──
    "017670.KS": "SK텔레콤",       "030200.KS": "KT",
    "032640.KS": "LG유플러스",
    # ── 항공 / 운송 ──
    "003490.KS": "대한항공",       "086280.KS": "현대글로비스",
    "011200.KS": "HMM",            "028670.KS": "팬오션",
    "000120.KS": "CJ대한통운",
    # ── 미디어 / 엔터 ──
    "253450.KS": "스튜디오드래곤", "035760.KS": "CJ ENM",
    # ── 에너지 / 유틸리티 ──
    "015760.KS": "한국전력",       "078930.KS": "GS",
    "010060.KS": "OCI홀딩스",
    # ── 전자 / 가전 ──
    "066570.KS": "LG전자",
}


# ══════════════════════════════════════════════════════════════════════
#  HTTP 유틸리티
# ══════════════════════════════════════════════════════════════════════

def make_session() -> tuple[requests.Session, str]:
    s = requests.Session()
    s.headers.update({"User-Agent": "Mozilla/5.0"})
    s.get("https://fc.yahoo.com", timeout=10)
    crumb = s.get("https://query1.finance.yahoo.com/v1/test/getcrumb",
                  timeout=10).text
    return s, crumb


def fetch_marketcaps(session, crumb, tickers: list[str], batch_size: int = 40) -> dict:
    """시총을 배치로 조회 (한 번에 너무 많은 심볼을 요청하면 URL 길이 초과 위험)."""
    caps = {}
    for i in range(0, len(tickers), batch_size):
        batch = tickers[i:i + batch_size]
        try:
            symbols = ",".join(batch)
            url = (f"https://query1.finance.yahoo.com/v7/finance/quote"
                   f"?symbols={symbols}&crumb={crumb}")
            d = session.get(url, timeout=10).json()
            for q in d["quoteResponse"]["result"]:
                mc = q.get("marketCap")
                if mc:
                    caps[q["symbol"]] = mc / 1e9
        except Exception as e:
            print(f"  ! 시총 조회 실패 (배치 {i}): {e}")
    return caps


def fetch_fundamentals(session, crumb, ticker: str) -> dict:
    """ROE·부채비율·매출성장·PER·PBR·목표주가 조회.
    summaryDetail + defaultKeyStatistics 두 모듈에서 PBR/PER를 모두 시도해
    한국 주식처럼 일부 필드가 누락된 경우를 보완한다.
    """
    try:
        url = (f"https://query1.finance.yahoo.com/v10/finance/quoteSummary/{ticker}"
               f"?modules=financialData,summaryDetail,defaultKeyStatistics&crumb={crumb}")
        res = session.get(url, timeout=10).json()["quoteSummary"]["result"][0]
        fd = res.get("financialData",       {})
        sd = res.get("summaryDetail",       {})
        ks = res.get("defaultKeyStatistics",{})

        def raw(d, k):
            return d.get(k, {}).get("raw") if isinstance(d.get(k), dict) else None

        pbr = raw(sd, "priceToBook") or raw(ks, "priceToBook")
        per = raw(sd, "trailingPE")  or raw(ks, "trailingPE")

        return {
            "roe":           raw(fd, "returnOnEquity"),
            "debt_equity":   raw(fd, "debtToEquity"),
            "rev_growth":    raw(fd, "revenueGrowth"),
            "profit_margin": raw(fd, "profitMargins"),
            "per":           per,
            "pbr":           pbr,
            "target_mean":   raw(fd, "targetMeanPrice"),
        }
    except Exception:
        return {}


def fetch_daily(ticker: str) -> dict | None:
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
           f"?interval=1d&range=1y")
    try:
        res = requests.get(url, headers=HEADERS, timeout=10)
        result = res.json()["chart"]["result"][0]
        closes = result["indicators"]["quote"][0]["close"]
        vols   = result["indicators"]["quote"][0]["volume"]
        closes = [c for c in closes if c is not None]
        vols   = [v for v in vols   if v is not None]
        return {"close": closes, "volume": vols}
    except Exception as e:
        print(f"  ! {ticker} 조회 실패: {e}")
        return None


def to_krx_code(ticker: str) -> str:
    """005930.KS → 005930"""
    return ticker.split(".")[0]


def _parse_signed_int(text: str) -> int | None:
    s = text.strip().replace(",", "").replace("+", "")
    if not s or s == "-":
        return None
    try:
        return int(s)
    except ValueError:
        return None


def fetch_naver_investor_flow(code: str) -> list[dict] | None:
    """네이버 금융 일별 기관·외국인 순매매량(주). 최신일 → 과거 순.

    Yahoo/pykrx(KRX 로그인 필요) 대신 공개 페이지를 사용한다.
    장중에는 당일 수치가 미확정일 수 있음(장 마감 후 확정).
    """
    url = f"https://finance.naver.com/item/frgn.naver?code={code}"
    try:
        res = requests.get(url, headers=HEADERS, timeout=12)
        res.encoding = "euc-kr"
        soup = BeautifulSoup(res.text, "html.parser")
        tables = soup.select("table.type2")
        if len(tables) < 2:
            return None
        rows = []
        for tr in tables[1].select("tr"):
            tds = tr.select("td")
            if len(tds) < 7:
                continue
            date = tds[0].get_text(strip=True)
            if not re.match(r"\d{4}\.\d{2}\.\d{2}", date):
                continue
            inst = _parse_signed_int(tds[5].get_text())
            frgn = _parse_signed_int(tds[6].get_text())
            if inst is None or frgn is None:
                continue
            rows.append({"date": date, "inst": inst, "foreign": frgn})
        return rows or None
    except Exception as e:
        print(f"  ! {code} 수급 조회 실패: {e}")
        return None


def summarize_investor_flow(rows: list[dict] | None) -> dict:
    """5/10/20거래일 기관·외국인 누적 순매수 요약."""
    empty = {
        "flow_ok": False,
        "flow_5d": None, "flow_10d": None, "flow_20d": None,
        "inst_5d": None, "foreign_5d": None,
        "inst_20d": None, "foreign_20d": None,
    }
    if not rows:
        return empty

    def _sum(n: int) -> tuple[int, int, int]:
        sub = rows[:n]
        inst = sum(r["inst"] for r in sub)
        frgn = sum(r["foreign"] for r in sub)
        return inst, frgn, inst + frgn

    i5, f5, t5 = _sum(min(5, len(rows)))
    _, _, t10 = _sum(min(10, len(rows)))
    i20, f20, t20 = _sum(min(20, len(rows)))
    return {
        "flow_ok": t5 > 0,
        "flow_5d": t5, "flow_10d": t10, "flow_20d": t20,
        "inst_5d": i5, "foreign_5d": f5,
        "inst_20d": i20, "foreign_20d": f20,
    }


# ══════════════════════════════════════════════════════════════════════
#  수학 유틸리티
# ══════════════════════════════════════════════════════════════════════

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
    return 100 - (100 / (1 + avg_gain / avg_loss))


def _ema(values: list[float], window: int) -> list[float]:
    k = 2 / (window + 1)
    ema_vals = [values[0]]
    for v in values[1:]:
        ema_vals.append(v * k + ema_vals[-1] * (1 - k))
    return ema_vals


def macd_rising(values: list[float]) -> bool:
    """MACD 히스토그램이 상승 중인지 반환."""
    if len(values) < 35:
        return False
    ema12 = _ema(values, 12)
    ema26 = _ema(values, 26)
    macd_line = [a - b for a, b in zip(ema12, ema26)]
    signal = _ema(macd_line, 9)
    hist = [m - s for m, s in zip(macd_line, signal)]
    return hist[-1] > hist[-2]


def ret_nd(closes: list[float], n: int = 20) -> float | None:
    if len(closes) < n + 1:
        return None
    return (closes[-1] / closes[-(n + 1)] - 1) * 100


def resolve_volume_bars(vols: list[float]) -> tuple[list, float | None, float | None, bool]:
    """장중 미완성 봉 감지 후 전일 거래량으로 대체."""
    if len(vols) < 21:
        return vols, None, None, False
    vol_avg20 = sma(vols, 20)
    vol_today = vols[-1]
    intraday = (vol_avg20 is not None and vol_today is not None
                and vol_today < vol_avg20 * INCOMPLETE_VOL_RATIO)
    if intraday:
        completed = vols[:-1]
        vol_ref   = vols[-2] if len(vols) >= 2 else None
        vol_avg20 = sma(completed, 20) if len(completed) >= 20 else None
    else:
        completed = vols
        vol_ref   = vols[-1]
    return completed, vol_ref, vol_avg20, intraday


# ══════════════════════════════════════════════════════════════════════
#  시장 레짐 판단
# ══════════════════════════════════════════════════════════════════════

def check_regime(index_ticker: str) -> dict:
    d = fetch_daily(index_ticker)
    if not d or len(d["close"]) < 55:
        return {"ok": True, "price": None, "ma20": None, "ma50": None,
                "reason": "데이터없음(통과)", "ret_20d": None}
    closes = d["close"]
    price  = closes[-1]
    ma20   = sma(closes, 20)
    ma50   = sma(closes, 50)
    ma50_5d = sma(closes[:-5], 50)
    above_50  = ma50  is not None and price > ma50
    rising_50 = ma50_5d is not None and ma50 > ma50_5d
    ok = above_50 and rising_50
    reason = "약세 (50MA 이탈)" if not ok else "강세 (50MA 위)"
    return {"ok": ok, "price": price, "ma20": ma20, "ma50": ma50,
            "reason": reason, "ret_20d": ret_nd(closes, 20)}


# ══════════════════════════════════════════════════════════════════════
#  나스닥 종목 분석
# ══════════════════════════════════════════════════════════════════════

def analyze_nasdaq(ticker: str, name: str, cap: float | None,
                   fund: dict, regime_ok: bool,
                   benchmark_ret: float | None) -> dict | None:
    d = fetch_daily(ticker)
    if not d or len(d["close"]) < 60:
        return None
    closes = d["close"]
    vols   = d["volume"]
    price  = closes[-1]

    ma5   = sma(closes, 5)
    ma20  = sma(closes, 20)
    ma50  = sma(closes, 50)
    ma200 = sma(closes, 200)
    ma50_5d = sma(closes[:-5], 50) if len(closes) > 55 else None

    r = rsi(closes, 14)

    completed, vol_ref, vol_avg20, vol_intraday = resolve_volume_bars(vols)
    vol_avg5 = sma(completed, 5) if len(completed) >= 5 else None
    vol_ratio_5d = (vol_avg5 / vol_avg20) if (vol_avg5 and vol_avg20) else None

    high_52w = max(closes[-252:]) if len(closes) >= 252 else max(closes)
    from_high = (price - high_52w) / high_52w * 100

    recent_20   = closes[-20:] if len(closes) >= 20 else closes
    range_20d   = ((max(recent_20) - min(recent_20)) / price * 100) if price else None
    ret_stock   = ret_nd(closes, 20)

    ext_5ma   = ((price - ma5) / ma5 * 100)    if ma5  else None
    ext_5_20  = ((ma5 - ma20)  / ma20 * 100)   if (ma5 and ma20)  else None
    ext_20_50 = ((ma20 - ma50) / ma50 * 100)   if (ma20 and ma50) else None

    # 추세: 주가 > 50MA, 50MA 우상향
    above_50ma  = ma50  is not None and price > ma50
    above_200ma = ma200 is not None and price > ma200
    ma50_rising = ma50_5d is not None and ma50 > ma50_5d
    trend_ok    = above_50ma and ma50_rising

    # 5>20>50 정배열
    aligned = (ma5 and ma20 and ma50 and ma5 > ma20 > ma50)

    # QQQ 상대강도
    rs_ok = (ret_stock is not None and benchmark_ret is not None
             and ret_stock > benchmark_ret)

    # 기본 필터
    cap_ok   = cap is None or cap >= ND_MIN_CAP_B
    price_ok = price <= ND_MAX_PRICE
    dte      = fund.get("debt_equity")
    debt_ok  = dte is None or dte <= ND_MAX_DEBT

    entry_type = None
    if trend_ok and cap_ok and price_ok and debt_ok:

        # ① 눌림목: 5MA 근처 + RSI 건강 + 거래량 수축
        pb_ok = (
            ext_5ma is not None and abs(ext_5ma) <= PB_5MA_RANGE
            and ext_5_20 is not None and ext_5_20 > -PB_FROM_20MA
            and r is not None and PB_RSI_MIN <= r <= PB_RSI_MAX
            and (vol_ratio_5d is None or vol_ratio_5d <= PB_VOL_MAX)
        )

        # ② 돌파: 52주 고점 근접 + 거래량 급증
        bk_ok = (
            from_high >= BK_FROM_HIGH
            and r is not None and BK_RSI_MIN <= r <= BK_RSI_MAX
            and vol_ratio_5d is not None and vol_ratio_5d >= BK_VOL_MIN
            and above_200ma
            and ma20 is not None and price > ma20
        )

        # ③ 에너지응축: MA 간격 좁음 + 레인지 압축
        sq_ok = (
            ext_5_20  is not None and abs(ext_5_20)  <= SQ_5_20_MAX
            and ext_20_50 is not None and abs(ext_20_50) <= SQ_20_50_MAX
            and range_20d  is not None and range_20d  <= SQ_RANGE_MAX
            and r is not None and SQ_RSI_MIN <= r <= SQ_RSI_MAX
        )

        if pb_ok:
            entry_type = "눌림목"
        elif bk_ok:
            entry_type = "돌파"
        elif sq_ok:
            entry_type = "에너지응축"

    # RS 점수 (전체 순위 비교용)
    rs_score = ret_stock if ret_stock is not None else -999

    return {
        "ticker": ticker, "name": name, "price": price, "cap": cap,
        "rsi": r, "from_high": from_high,
        "ext_5ma": ext_5ma, "ext_5_20": ext_5_20, "ext_20_50": ext_20_50,
        "range_20d": range_20d, "vol_ratio_5d": vol_ratio_5d,
        "ret_20d": ret_stock, "rs_ok": rs_ok, "rs_score": rs_score,
        "trend_ok": trend_ok, "aligned": aligned,
        "above_50ma": above_50ma, "above_200ma": above_200ma,
        "cap_ok": cap_ok, "price_ok": price_ok, "debt_ok": debt_ok,
        "entry_type": entry_type, "regime_ok": regime_ok,
        "ma5": ma5, "ma20": ma20, "ma50": ma50,
        "roe": fund.get("roe"), "debt_equity": dte,
        "rev_growth": fund.get("rev_growth"),
        "per": fund.get("per"),
        "vol_intraday": vol_intraday,
    }


# ══════════════════════════════════════════════════════════════════════
#  코스피 종목 분석
# ══════════════════════════════════════════════════════════════════════

def analyze_kospi(ticker: str, name: str, fund: dict,
                  regime_ok: bool, benchmark_ret: float | None,
                  flow: dict | None = None) -> dict | None:
    d = fetch_daily(ticker)
    if not d or len(d["close"]) < 40:
        return None
    closes = d["close"]
    vols   = d["volume"]
    price  = closes[-1]

    ma20  = sma(closes, 20)
    ma50  = sma(closes, 50)
    ma200 = sma(closes, 200)
    ma50_5d = sma(closes[:-5], 50) if len(closes) > 55 else None

    r = rsi(closes, 14)

    completed, _, vol_avg20, vol_intraday = resolve_volume_bars(vols)
    vol_avg5 = sma(completed, 5) if len(completed) >= 5 else None
    vol_ratio_5d = (vol_avg5 / vol_avg20) if (vol_avg5 and vol_avg20) else None

    high_52w = max(closes[-252:]) if len(closes) >= 252 else max(closes)
    low_52w  = min(closes[-252:]) if len(closes) >= 252 else min(closes)
    from_high = (price - high_52w) / high_52w * 100
    from_low  = (price - low_52w)  / low_52w  * 100

    ret_stock = ret_nd(closes, 20)
    rs_ok = (ret_stock is not None and benchmark_ret is not None
             and ret_stock > benchmark_ret)

    fund  = fund or {}
    pbr   = fund.get("pbr")
    per   = fund.get("per")
    roe   = fund.get("roe")
    dte   = fund.get("debt_equity")
    rg    = fund.get("rev_growth")
    pm    = fund.get("profit_margin")
    tgt   = fund.get("target_mean")

    # 목표주가 괴리율 (목표가 / 현재가 - 1)
    tgt_gap = ((tgt / price) - 1) if (tgt and price and tgt > 0) else None

    flow = flow or summarize_investor_flow(None)

    # ── 10점 만점 점수화 ──────────────────────────────────────────
    score = 0

    # 가치/안전마진 (최대 3점)
    if pbr is not None:
        if pbr <= KS_PBR_GOOD:   score += 2
        elif pbr <= KS_PBR_MAX:  score += 1
    if per is not None and 0 < per <= KS_PER_GOOD:
        score += 1

    # 실적/성장 (최대 4점)
    if rg  is not None and rg > 0:                        score += 1
    if pm  is not None and pm > 0:                        score += 1
    if tgt_gap is not None and tgt_gap >= KS_TGT_GAP:    score += 2

    # 수급/기술 (최대 3점) — 수급은 네이버 기관·외국인 순매수 (거래량 근사 금지)
    if ma20 is not None and price > ma20:                  score += 1
    if r    is not None and KS_RSI_MIN <= r <= KS_RSI_MAX: score += 1
    if flow.get("flow_ok"):                                score += 1

    # ── 필수 탈락 기준 ──────────────────────────────────────────
    debt_ok  = dte is None or dte <= KS_DEBT_MAX
    roe_ok   = roe is None or roe >= KS_ROE_MIN
    pbr_pass = pbr is None or pbr <= KS_PBR_MAX
    per_pass = per is None or per <= KS_PER_MAX
    qualified = debt_ok and roe_ok and pbr_pass and per_pass

    exclude_reason = None
    if   not debt_ok:  exclude_reason = f"부채과다 ({dte:.0f}%)"
    elif not roe_ok:   exclude_reason = f"ROE 부족 ({roe*100:.1f}%)"
    elif not pbr_pass: exclude_reason = f"PBR 고평가 ({pbr:.2f})"
    elif not per_pass: exclude_reason = f"PER 고평가 ({per:.1f})"

    # ── 카테고리 분류 ────────────────────────────────────────────
    if not qualified or score < 4:
        category = "조건 미달"
    elif score >= 7:
        category = "우선 후보"
    elif score >= 5:
        category = "관찰 후보"
    else:
        category = "조건 미달"

    return {
        "ticker": ticker, "name": name, "price": price,
        "rsi": r, "from_high": from_high, "from_low": from_low,
        "vol_ratio_5d": vol_ratio_5d, "ret_20d": ret_stock,
        "rs_ok": rs_ok,
        "ma20": ma20, "ma50": ma50, "ma200": ma200,
        "above_20ma": (ma20 is not None and price > ma20),
        "pbr": pbr, "per": per, "roe": roe, "dte": dte,
        "rev_growth": rg, "profit_margin": pm,
        "target_mean": tgt, "tgt_gap": tgt_gap,
        "score": score, "category": category,
        "qualified": qualified, "exclude_reason": exclude_reason,
        "vol_intraday": vol_intraday, "regime_ok": regime_ok,
        **flow,
    }


# ══════════════════════════════════════════════════════════════════════
#  병렬 조회 워커
# ══════════════════════════════════════════════════════════════════════
MAX_WORKERS = 12  # 동시 조회 스레드 수 (Yahoo 과도한 동시요청 방지)


def _nasdaq_worker(session, crumb, tick, name, cap, regime_ok, bench):
    try:
        fund = fetch_fundamentals(session, crumb, tick)
        return analyze_nasdaq(tick, name, cap, fund, regime_ok, bench)
    except Exception as e:
        print(f"  ! {tick} 분석 실패: {e}")
        return None


def _kospi_worker(session, crumb, tick, name, regime_ok, bench):
    try:
        fund = fetch_fundamentals(session, crumb, tick)
        flow = summarize_investor_flow(
            fetch_naver_investor_flow(to_krx_code(tick))
        )
        return analyze_kospi(tick, name, fund, regime_ok, bench, flow)
    except Exception as e:
        print(f"  ! {tick} 분석 실패: {e}")
        return None


def _run_parallel(worker, session, crumb, items: list[tuple], regime_ok, bench) -> list:
    """items: (ticker, name, extra) 또는 (ticker, name) 튜플 리스트."""
    results = []
    with cf.ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = {ex.submit(worker, session, crumb, *item, regime_ok, bench): item[0]
                   for item in items}
        for fut in cf.as_completed(futures):
            a = fut.result()
            if a:
                results.append(a)
    return results


# ══════════════════════════════════════════════════════════════════════
#  출력 유틸리티
# ══════════════════════════════════════════════════════════════════════

def _f(v, suffix="", nd=2, scale=1):
    if v is None:
        return "N/A"
    return f"{v * scale:,.{nd}f}{suffix}"


def _sep(char="═", width=78):
    print(char * width)


def _print_nasdaq_stock(a: dict):
    rs_mark = "RS▲" if a.get("rs_ok") else "RS▼"
    intraday = " [장중추정]" if a.get("vol_intraday") else ""
    cap_s = f"${a['cap']:,.0f}B" if a.get("cap") else "N/A"
    print(f"\n  {a['name']} ({a['ticker']})  시총 {cap_s}  [{a['entry_type']}]  {rs_mark}")
    print(f"    현재가 ${_f(a['price'])}  RSI {_f(a['rsi'],nd=1)}  5MA이격 {_f(a['ext_5ma'],'%',1)}"
          f"  5-20MA {_f(a['ext_5_20'],'%',1)}  고점대비 {_f(a['from_high'],'%',1)}")
    print(f"    5d거래량 {_f(a['vol_ratio_5d'],'x',2)}{intraday}"
          f"  20일수익률 {_f(a['ret_20d'],'%',1)}"
          f"  20일레인지 {_f(a['range_20d'],'%',1)}")
    print(f"    5MA ${_f(a['ma5'])}  20MA ${_f(a['ma20'])}  50MA ${_f(a['ma50'])}")
    roe_s = _f(a['roe'], '%', 1, 100)
    dte_s = _f(a['debt_equity'], '%', 0)
    rg_s  = _f(a['rev_growth'],  '%', 1, 100)
    print(f"    [펀더] ROE {roe_s}  부채비율 {dte_s}  매출성장 {rg_s}  PER {_f(a['per'],nd=1)}")


def _fmt_shares(n) -> str:
    """순매수 주수 → +1.2백만 / -85만 등."""
    if n is None:
        return "N/A"
    sign = "+" if n > 0 else ""
    abs_n = abs(n)
    if abs_n >= 1_000_000:
        return f"{sign}{n/1_000_000:.1f}백만"
    if abs_n >= 10_000:
        return f"{sign}{n/10_000:.0f}만"
    return f"{sign}{n:,}"


def _print_kospi_stock(a: dict):
    rs_mark  = "RS▲" if a.get("rs_ok") else "RS▼"
    above_ma = "20MA▲" if a.get("above_20ma") else "20MA▼"
    tgt_s    = f"  목표주가괴리 +{a['tgt_gap']*100:.0f}%" if a.get("tgt_gap") else ""
    flow_mark = "수급▲" if a.get("flow_ok") else "수급▼"
    print(f"\n  {a['name']} ({a['ticker']})  [{a['category']}]  점수 {a['score']}/10"
          f"  {rs_mark}  {above_ma}  {flow_mark}{tgt_s}")
    price_s = f"{a['price']:,.0f}원"
    print(f"    현재가 {price_s}  RSI {_f(a['rsi'],nd=1)}"
          f"  고점대비 {_f(a['from_high'],'%',1)}"
          f"  저점대비 +{_f(a['from_low'],'%',1)}")
    print(f"    [수급] 5일 합산 {_fmt_shares(a.get('flow_5d'))}"
          f" (기관 {_fmt_shares(a.get('inst_5d'))}"
          f" / 외인 {_fmt_shares(a.get('foreign_5d'))})"
          f"  20일 합산 {_fmt_shares(a.get('flow_20d'))}")
    print(f"    5d거래량 {_f(a['vol_ratio_5d'],'x',2)}"
          f"  20일수익률 {_f(a['ret_20d'],'%',1)}")
    pbr_s = _f(a['pbr'], nd=2)
    per_s = _f(a['per'], nd=1)
    roe_s = _f(a['roe'], '%', 1, 100)
    dte_s = _f(a['dte'], '%', 0)
    rg_s  = _f(a['rev_growth'], '%', 1, 100)
    pm_s  = _f(a['profit_margin'], '%', 1, 100)
    print(f"    [펀더] PBR {pbr_s}  PER {per_s}  ROE {roe_s}"
          f"  부채비율 {dte_s}  매출성장 {rg_s}  영업이익률 {pm_s}")


# ══════════════════════════════════════════════════════════════════════
#  나스닥 메인 루틴
# ══════════════════════════════════════════════════════════════════════

def run_nasdaq():
    _sep()
    print("  나스닥 매수 후보발굴 스크리너")
    print(f"  기준: QQQ 상대강도 · 눌림목(5MA±{PB_5MA_RANGE}%·RSI {PB_RSI_MIN}~{PB_RSI_MAX}·거래량수축)")
    print(f"       돌파(고점{BK_FROM_HIGH}%이내·RSI {BK_RSI_MIN}~{BK_RSI_MAX}·거래량≥{BK_VOL_MIN}x)")
    print(f"       에너지응축(5-20MA≤{SQ_5_20_MAX}%·20일레인지≤{SQ_RANGE_MAX}%·RSI {SQ_RSI_MIN}~{SQ_RSI_MAX})")
    _sep()

    session, crumb = make_session()

    regime = check_regime(NASDAQ_INDEX)
    flag = "🟢 진입 허용" if regime["ok"] else "🔴 진입 보류"
    print(f"  [시장 레짐] QQQ {_f(regime['price'])} / 50MA {_f(regime['ma50'])} "
          f"→ {regime['reason']}  {flag}  QQQ 20일 {_f(regime['ret_20d'],'%',1)}")
    if not regime["ok"]:
        print("  ⚠ QQQ 50MA 이탈 — 후보는 표시하되 실제 진입은 50MA 회복 후 권장.")
    _sep()

    print(f"  종목 {len(NASDAQ_CANDIDATES)}개 스캔 중 (병렬 {MAX_WORKERS}개)...")
    caps  = fetch_marketcaps(session, crumb, list(NASDAQ_CANDIDATES.keys()))
    bench = regime["ret_20d"]

    items = [(tick, name, caps.get(tick)) for tick, name in NASDAQ_CANDIDATES.items()]
    results = _run_parallel(_nasdaq_worker, session, crumb, items, regime["ok"], bench)

    pullbacks  = [a for a in results if a["entry_type"] == "눌림목"]
    breakouts  = [a for a in results if a["entry_type"] == "돌파"]
    squeezes   = [a for a in results if a["entry_type"] == "에너지응축"]

    # 눌림목: RSI 중앙(52) 가까울수록, 5MA이격 작을수록 우수
    pullbacks.sort(key=lambda x: (
        -abs((x["rsi"] or 52) - 52) - abs(x["ext_5ma"] or 0) * 2
        + (2 if x["rs_ok"] else 0)
    ), reverse=True)

    # 돌파: 고점 근접 + 거래량 높을수록 우수
    breakouts.sort(key=lambda x: (
        (x["from_high"] or -99) + (x["vol_ratio_5d"] or 0) * 5
        + (2 if x["rs_ok"] else 0)
    ), reverse=True)

    # 에너지응축: MA 간격 좁을수록, RS 있으면 우수
    squeezes.sort(key=lambda x: (
        -(abs(x["ext_5_20"] or 0) + abs(x["ext_20_50"] or 0))
        + (2 if x["rs_ok"] else 0)
    ), reverse=True)

    # ── 섹션별 출력 ──
    print(f"\n  ━━ 눌림목 후보 ({len(pullbacks)}개) ━━")
    print(f"     조건: 상승추세 + 5MA±{PB_5MA_RANGE}% + RSI {PB_RSI_MIN}~{PB_RSI_MAX} + 거래량 수축")
    if pullbacks:
        for a in pullbacks:
            _print_nasdaq_stock(a)
    else:
        print("  (해당 없음)")

    print(f"\n  ━━ 돌파 후보 ({len(breakouts)}개) ━━")
    print(f"     조건: 52주 고점 {BK_FROM_HIGH}% 이내 + RSI {BK_RSI_MIN}~{BK_RSI_MAX} + 거래량≥{BK_VOL_MIN}x")
    if breakouts:
        for a in breakouts:
            _print_nasdaq_stock(a)
    else:
        print("  (해당 없음)")

    print(f"\n  ━━ 에너지응축 후보 ({len(squeezes)}개) ━━")
    print(f"     조건: 5-20MA≤{SQ_5_20_MAX}% + 20-50MA≤{SQ_20_50_MAX}% + 20일레인지≤{SQ_RANGE_MAX}%")
    if squeezes:
        for a in squeezes:
            _print_nasdaq_stock(a)
    else:
        print("  (해당 없음)")

    # QQQ 상대강도 Top 10
    rs_ranked = sorted(
        [a for a in results if a.get("ret_20d") is not None],
        key=lambda x: x["ret_20d"], reverse=True
    )[:10]
    print(f"\n  ━━ QQQ 상대강도 Top 10 (20일 수익률 기준, QQQ {_f(bench,'%',1)}) ━━")
    print(f"  {'종목':<18}{'티커':<7}{'현재가':>10}{'20일수익률':>10}{'RSI':>5}{'진입유형':>10}")
    for a in rs_ranked:
        et = a["entry_type"] or "-"
        cap_flag = "" if a["cap_ok"] else " [시총↓]"
        print(f"  {a['name']:<16}{a['ticker']:<7}"
              f"{_f(a['price']):>10}"
              f"{_f(a['ret_20d'],'%',1):>10}"
              f"{_f(a['rsi'],nd=0):>5}"
              f"{et:>10}{cap_flag}")

    # 전체 테이블
    print(f"\n  ━━ 전체 요약 ━━")
    print(f"  {'종목':<18}{'티커':<7}{'현재가':>10}{'RSI':>5}{'5MA이격':>9}"
          f"{'고점대비':>9}{'RS':>4}{'진입유형':>10}")
    results_sorted = sorted(results, key=lambda x: (
        0 if x["entry_type"] else 1,
        x["entry_type"] or "z",
        -(x["rs_score"])
    ))
    for a in results_sorted:
        et = a["entry_type"] or _nd_tag(a)
        rs = "▲" if a["rs_ok"] else "▼"
        print(f"  {a['name']:<16}{a['ticker']:<7}"
              f"{_f(a['price']):>10}"
              f"{_f(a['rsi'],nd=0):>5}"
              f"{_f(a['ext_5ma'],'%',1):>9}"
              f"{_f(a['from_high'],'%',1):>9}"
              f"{rs:>4}"
              f"{et:>10}")
    print()


def _nd_tag(a: dict) -> str:
    if not a["cap_ok"]:
        return "시총↓"
    if not a["price_ok"]:
        return "단가↑"
    if not a["debt_ok"]:
        return "부채↑"
    if not a["above_50ma"]:
        return "추세X"
    if not a["regime_ok"]:
        return "레짐↓"
    r = a["rsi"]
    if r is not None:
        if r < PB_RSI_MIN:
            return "RSI↓"
        if r > BK_RSI_MAX:
            return "RSI↑"
    fh = a["from_high"]
    if fh is not None and fh < BK_FROM_HIGH and fh < -15:
        return "고점↓"
    return "관찰"


# ══════════════════════════════════════════════════════════════════════
#  코스피 메인 루틴
# ══════════════════════════════════════════════════════════════════════

def run_kospi():
    _sep()
    print("  코스피 매수 후보발굴 스크리너")
    print(f"  기준: 안전마진(PBR≤{KS_PBR_MAX}) · 실적모멘텀(목표가괴리율≥{KS_TGT_GAP*100:.0f}%)")
    print(f"       수급전환(기관+외인 {KS_FLOW_DAYS}일 누적순매수>0 · 네이버) · 재무안정성(ROE≥{KS_ROE_MIN*100:.0f}%·부채≤{KS_DEBT_MAX}%)")
    print(f"  점수: 가치(3) + 실적/성장(4) + 수급/기술(3) = 10점 | 우선후보≥7 관찰후보≥5")
    _sep()

    session, crumb = make_session()

    regime = check_regime(KOSPI_INDEX)
    flag   = "🟢 강세" if regime["ok"] else "🔴 약세"
    bench  = regime["ret_20d"]
    print(f"  [코스피 레짐] KOSPI {_f(regime['price'],nd=0)} / 50MA {_f(regime['ma50'],nd=0)} "
          f"→ {regime['reason']}  {flag}  KOSPI 20일 {_f(bench,'%',1)}")
    _sep()

    print(f"  종목 {len(KOSPI_CANDIDATES)}개 스캔 중 (병렬 {MAX_WORKERS}개)...")
    items = [(tick, name) for tick, name in KOSPI_CANDIDATES.items()]
    results = _run_parallel(_kospi_worker, session, crumb, items, regime["ok"], bench)

    priority  = [a for a in results if a["category"] == "우선 후보"]
    watchlist = [a for a in results if a["category"] == "관찰 후보"]
    others    = [a for a in results if a["category"] == "조건 미달"]

    priority.sort(key=lambda x: x["score"], reverse=True)
    watchlist.sort(key=lambda x: x["score"], reverse=True)

    print(f"\n  ━━ 우선 후보 (점수 7+ / {len(priority)}개) ━━")
    print("     실적모멘텀 + 안전마진 + 수급전환 동시 충족")
    if priority:
        for a in priority:
            _print_kospi_stock(a)
    else:
        print("  (해당 없음)")

    print(f"\n  ━━ 관찰 후보 (점수 5~6 / {len(watchlist)}개) ━━")
    print("     일부 기준 충족 — 추가 확인 후 진입 검토")
    if watchlist:
        for a in watchlist:
            _print_kospi_stock(a)
    else:
        print("  (해당 없음)")

    print(f"\n  ━━ 코스피 레짐 상대강도 Top 10 ━━")
    rs_ranked = sorted(
        [a for a in results if a.get("ret_20d") is not None],
        key=lambda x: x["ret_20d"], reverse=True
    )[:10]
    print(f"  {'종목':<14}{'티커':<14}{'현재가':>10}{'20일수익률':>10}{'RSI':>5}{'점수':>5}{'카테고리':>10}")
    for a in rs_ranked:
        price_s = f"{a['price']:,.0f}"
        print(f"  {a['name']:<12}{a['ticker']:<14}"
              f"{price_s:>10}"
              f"{_f(a['ret_20d'],'%',1):>10}"
              f"{_f(a['rsi'],nd=0):>5}"
              f"{a['score']:>5}"
              f"{a['category']:>10}")

    print(f"\n  ━━ 전체 요약 ━━")
    print(f"  {'종목':<12}{'티커':<14}{'현재가':>10}{'PBR':>6}{'PER':>6}{'RSI':>5}"
          f"{'목표괴리':>8}{'점수':>5}{'카테고리':>10}")
    all_sorted = sorted(results, key=lambda x: x["score"], reverse=True)
    for a in all_sorted:
        price_s  = f"{a['price']:,.0f}"
        tgt_s    = f"+{a['tgt_gap']*100:.0f}%" if a.get("tgt_gap") else "N/A"
        excl     = f"  ← {a['exclude_reason']}" if a.get("exclude_reason") else ""
        print(f"  {a['name']:<10}{a['ticker']:<14}"
              f"{price_s:>10}"
              f"{_f(a['pbr'],nd=2):>6}"
              f"{_f(a['per'],nd=1):>6}"
              f"{_f(a['rsi'],nd=0):>5}"
              f"{tgt_s:>8}"
              f"{a['score']:>5}"
              f"{a['category']:>10}{excl}")
    print()


# ══════════════════════════════════════════════════════════════════════
#  진입점
# ══════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="나스닥/코스피 매수 후보발굴 스크리너")
    parser.add_argument("--kospi", action="store_true", help="코스피 모드로 실행")
    args = parser.parse_args()

    if args.kospi:
        run_kospi()
    else:
        run_nasdaq()


if __name__ == "__main__":
    main()
