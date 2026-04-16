"""
키움 ka90001 + ka90002 → Supabase kiwoom_theme / kiwoom_theme_stock 업서트
매일 1회 실행 (장 시작 전 or 9:20 이후)
실행: python update_kiwoom_theme.py
"""
import os, time, requests, logging
from pathlib import Path
from dotenv import load_dotenv
from supabase import create_client

load_dotenv(dotenv_path=Path(__file__).parent / ".env")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger(__name__)

# ── 설정 ──────────────────────────────────────────────────
APP_KEY    = os.getenv("KIWOOM_APP_KEY", "")
SECRET_KEY = os.getenv("KIWOOM_SECRET_KEY", "")
BASE_URL   = "https://api.kiwoom.com"

SB_URL     = os.getenv("SUPABASE_URL", "")
SB_KEY     = os.getenv("SUPABASE_KEY", "")

DATE_TP    = "10"   # 기간수익률 기준 (10일)
STEX_TP    = "1"    # KRX


# ── 키움 API ───────────────────────────────────────────────
def get_token() -> str:
    resp = requests.post(f"{BASE_URL}/oauth2/token", json={
        "grant_type": "client_credentials",
        "appkey":     APP_KEY,
        "secretkey":  SECRET_KEY,
    }, timeout=10)
    resp.raise_for_status()
    return resp.json().get("token", "")


def fetch_all_themes(token: str) -> list[dict]:
    """ka90001 전체 테마 수집 (연속조회 포함)"""
    result, c_yn, n_key = [], "N", ""
    while True:
        headers = {
            "Content-Type":  "application/json;charset=UTF-8",
            "authorization": f"Bearer {token}",
            "cont-yn":       c_yn,
            "next-key":      n_key,
            "api-id":        "ka90001",
        }
        resp = requests.post(f"{BASE_URL}/api/dostk/thme", headers=headers, json={
            "qry_tp": "0", "stk_cd": "", "date_tp": DATE_TP,
            "thema_nm": "", "flu_pl_amt_tp": "1", "stex_tp": STEX_TP,
        }, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        log.info(f"  응답 키: {list(data.keys())} / thema_grp 수: {len(data.get('thema_grp', []))}")
        result.extend(data.get("thema_grp", []))
        c_yn  = resp.headers.get("cont-yn", "N")
        n_key = resp.headers.get("next-key", "")
        if c_yn != "Y" or not n_key:
            break
    log.info(f"ka90001: 테마 {len(result)}개 수집")
    return result


def fetch_theme_stocks(token: str, thema_grp_cd: str) -> list[dict]:
    """ka90002 테마 구성종목"""
    headers = {
        "Content-Type":  "application/json;charset=UTF-8",
        "authorization": f"Bearer {token}",
        "cont-yn":       "N",
        "next-key":      "",
        "api-id":        "ka90002",
    }
    resp = requests.post(f"{BASE_URL}/api/dostk/thme", headers=headers, json={
        "date_tp": DATE_TP, "thema_grp_cd": thema_grp_cd, "stex_tp": STEX_TP,
    }, timeout=10)
    resp.raise_for_status()
    return resp.json().get("thema_comp_stk", [])


def safe_float(v) -> float | None:
    try:
        return float(str(v).replace("+", "").replace(",", ""))
    except:
        return None


# ── Supabase 업서트 ────────────────────────────────────────
def upsert_all(themes: list[dict], theme_stocks: dict[str, list]):
    sb = create_client(SB_URL, SB_KEY)

    # 1) kiwoom_theme 엎어치기
    theme_rows = [{
        "thema_grp_cd":   t["thema_grp_cd"],
        "thema_nm":       t["thema_nm"],
        "stk_num":        int(t.get("stk_num") or 0),
        "flu_rt":         safe_float(t.get("flu_rt")),
        "dt_prft_rt":     safe_float(t.get("dt_prft_rt")),
        "rising_stk_num": int(t.get("rising_stk_num") or 0),
        "fall_stk_num":   int(t.get("fall_stk_num") or 0),
        "main_stk":       t.get("main_stk", ""),
    } for t in themes]

    if not theme_rows:
        log.warning("⚠️ 테마 데이터 없음 (장외시간?) → 업서트 스킵")
        return

    sb.table("kiwoom_theme").upsert(theme_rows).execute()
    log.info(f"kiwoom_theme upsert: {len(theme_rows)}개")

    # 2) kiwoom_theme_stock 엎어치기
    stock_rows = []
    for cd, stocks in theme_stocks.items():
        for s in stocks:
            stock_rows.append({
                "thema_grp_cd": cd,
                "stock_code":   s.get("stk_cd", "").strip(),
                "stock_name":   s.get("stk_nm", ""),
                "flu_rt":       safe_float(s.get("flu_rt")),
                "dt_prft_rt":   safe_float(s.get("dt_prft_rt_n")),
            })

    # 500개씩 배치
    for i in range(0, len(stock_rows), 500):
        batch = stock_rows[i:i+500]
        sb.table("kiwoom_theme_stock").upsert(batch).execute()
        log.info(f"kiwoom_theme_stock upsert: {i+len(batch)}/{len(stock_rows)}")

    log.info(f"✅ 완료! 테마:{len(theme_rows)}개 / 종목매핑:{len(stock_rows)}건")


# ── 메인 ──────────────────────────────────────────────────
if __name__ == "__main__":
    log.info("🔑 토큰 발급...")
    token = get_token()

    log.info("📡 ka90001 전체 테마 수집...")
    themes = fetch_all_themes(token)

    log.info("📡 ka90002 구성종목 수집...")
    theme_stocks = {}
    for i, t in enumerate(themes, 1):
        cd = t["thema_grp_cd"]
        try:
            theme_stocks[cd] = fetch_theme_stocks(token, cd)
            log.info(f"  [{i:>3}/{len(themes)}] {t['thema_nm']} → {len(theme_stocks[cd])}종목")
        except Exception as e:
            log.error(f"  [{i:>3}] {t['thema_nm']} 실패: {e}")
            theme_stocks[cd] = []
        time.sleep(0.2)

    log.info("💾 Supabase 업서트...")
    upsert_all(themes, theme_stocks)
