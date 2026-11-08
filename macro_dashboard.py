# -*- coding: utf-8 -*-
"""
거시경제 체크 대시보드 (macro_dashboard.py)  v2

하는 일
  1. 미국 연준 데이터(FRED)에서 거시지표 6개를 내려받습니다. (API 키 필요 없음)
  2. 지표마다 "검증"을 거칩니다.
       - 최신성: 데이터가 너무 오래되지 않았는지
       - 범위:   말이 안 되는 숫자(예: 환율 5,000원)가 아닌지
       - 급변:   하루 만에 비정상적으로 튀지 않았는지
       - 교차검증: 원래 발표 기관(미 재무부, 뉴욕 연준, 미 노동통계국, 유럽중앙은행)의
                  숫자와 일치하는지
     검증에 실패한 지표는 새 숫자로 업데이트하지 않고, 마지막으로 검증된 숫자를 보여줍니다.
  3. 휴대폰에서 보기 좋은 한 장짜리 페이지(site/index.html)를 만듭니다.
  4. data/macro_log.csv 에 검증된 숫자를 날짜별로 쌓습니다.

실행 방법 (Windows)
      python macro_dashboard.py
  파이썬 기본 기능만 쓰기 때문에 pip 설치는 필요 없습니다.

주의
  - 판단을 돕는 참고 자료이지 매수/매도 신호가 아닙니다.
  - 환율은 국제 기준환율이라 은행 앱의 매매기준율과 몇 원 차이 날 수 있습니다.
"""

import csv
import datetime as dt
import html
import io
import json
import os
import shutil
import sys
import webbrowser
from pathlib import Path
from urllib.request import Request, urlopen

HERE = Path(__file__).resolve().parent
SITE_DIR = HERE / "site"
DATA_DIR = HERE / "data"
OUT_HTML = SITE_DIR / "index.html"
LOG_CSV = DATA_DIR / "macro_log.csv"
STATE_JSON = DATA_DIR / "last_verified.json"
ASSETS_DIR = HERE / "assets"  # 홈 화면 아이콘 (tools/make_icons.py로 만듦)
CHART_YEARS = 3
SAMPLE_MODE = False  # True면 화면 위에 "샘플 데이터" 경고를 띄움 (테스트용)

KST = dt.timezone(dt.timedelta(hours=9))


# ===========================================================================
# 1. 데이터 내려받기
# ===========================================================================
def http_get(url, timeout=30):
    req = Request(url, headers={"User-Agent": "Mozilla/5.0 (macro-dashboard)"})
    with urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8-sig")


def parse_date(text):
    text = text.strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y"):
        try:
            return dt.datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    raise ValueError(f"날짜 형식을 모름: {text}")


def fetch_fred(series_id, start):
    """FRED 지표 → [(날짜, 값), ...] (날짜 오름차순)

    FRED_API_KEY(무료 공식 API 키)가 있으면 공식 API로, 없으면 그래프용 CSV로 받음.
    GitHub Actions 서버에서는 CSV 주소가 응답하지 않는 경우가 있어 API 키 사용을 권장.
    """
    key = os.environ.get("FRED_API_KEY", "").strip()
    if key:
        j = json.loads(http_get(
            "https://api.stlouisfed.org/fred/series/observations"
            f"?series_id={series_id}&observation_start={start.isoformat()}&api_key={key}&file_type=json"
        ))
        pairs = [(o["date"], o["value"]) for o in j.get("observations", [])]
    else:
        text = http_get(
            "https://fred.stlouisfed.org/graph/fredgraph.csv"
            f"?id={series_id}&cosd={start.isoformat()}"
        )
        reader = csv.reader(io.StringIO(text))
        next(reader, None)
        pairs = [(rec[0], rec[1]) for rec in reader if len(rec) >= 2]
    rows = []
    for d, v in pairs:
        if v.strip() in ("", "."):
            continue
        try:
            rows.append((parse_date(d), float(v)))
        except ValueError:
            continue
    if not rows:
        raise ValueError("데이터 비어 있음")
    rows.sort()
    return rows


def fetch_treasury(today):
    """미 재무부 공식 국채 금리 → {날짜: {"10": 값, "2": 값}}"""
    years = {today.year} | ({today.year - 1} if today.month == 1 else set())
    out = {}
    for y in years:
        text = http_get(
            "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
            f"daily-treasury-rates.csv/{y}/all?type=daily_treasury_yield_curve"
            f"&field_tdr_date_value={y}&page&_format=csv"
        )
        reader = csv.reader(io.StringIO(text))
        header = [h.strip().lower().replace(" ", "") for h in next(reader)]
        i_date, i10, i2 = header.index("date"), header.index("10yr"), header.index("2yr")
        for rec in reader:
            try:
                out[parse_date(rec[i_date])] = {"10": float(rec[i10]), "2": float(rec[i2])}
            except (ValueError, IndexError):
                continue
    if not out:
        raise ValueError("재무부 데이터 비어 있음")
    return out


def fetch_nyfed():
    """뉴욕 연준 공식 기준금리 범위 → dict"""
    j = json.loads(http_get("https://markets.newyorkfed.org/api/rates/unsecured/effr/last/1.json"))
    r = j["refRates"][0]
    return {
        "date": r["effectiveDate"],
        "effr": float(r["percentRate"]),
        "lower": float(r["targetRateFrom"]),
        "upper": float(r["targetRateTo"]),
    }


def fetch_bls(series_id):
    """미 노동통계국 공식 통계 → {(연, 월): 값}"""
    j = json.loads(http_get(f"https://api.bls.gov/publicAPI/v1/timeseries/data/{series_id}"))
    if j.get("status") != "REQUEST_SUCCEEDED":
        raise ValueError(f"BLS 응답 실패: {j.get('message')}")
    out = {}
    for item in j["Results"]["series"][0]["data"]:
        if item["period"].startswith("M") and item["period"] != "M13":
            try:
                out[(int(item["year"]), int(item["period"][1:]))] = float(item["value"])
            except ValueError:
                continue
    return out


def fetch_ecb_fx(today):
    """유럽중앙은행 기준 원/달러 환율(Frankfurter) → [(날짜, 값), ...]"""
    start = (today - dt.timedelta(days=40)).isoformat()
    j = json.loads(http_get(f"https://api.frankfurter.dev/v1/{start}..?base=USD&symbols=KRW"))
    rows = sorted((parse_date(d), float(v["KRW"])) for d, v in j["rates"].items() if "KRW" in v)
    if not rows:
        raise ValueError("환율 데이터 비어 있음")
    return rows


# ===========================================================================
# 2. 계산 도우미
# ===========================================================================
def years_ago(day, n):
    try:
        return day.replace(year=day.year - n)
    except ValueError:
        return day.replace(year=day.year - n, day=28)


def value_on_or_before(rows, day):
    found = None
    for d, v in rows:
        if d <= day:
            found = (d, v)
        else:
            break
    return found


def nearest(rows, day, max_days=3):
    best = None
    for d, v in rows:
        gap = abs((d - day).days)
        if gap <= max_days and (best is None or gap < best[0]):
            best = (gap, d, v)
    return (best[1], best[2]) if best else None


def since(rows, day):
    return [(d, v) for d, v in rows if d >= day]


def cpi_yoy(rows):
    by_month = {(d.year, d.month): (d, v) for d, v in rows}
    out = []
    for (y, m), (d, v) in sorted(by_month.items()):
        prev = by_month.get((y - 1, m))
        if prev:
            out.append((d, round((v / prev[1] - 1) * 100, 2)))
    return out


def sahm_from_unrate(rows):
    """실업률로 삼의 법칙 지표를 직접 계산 (검증용)."""
    vals = [v for _, v in rows]
    ma3 = [sum(vals[i - 2:i + 1]) / 3 for i in range(2, len(vals))]
    if len(ma3) < 13:
        return None
    return round(ma3[-1] - min(ma3[-13:-1]), 2)


def last_rate_change(rows):
    last = None
    for (d0, v0), (d1, v1) in zip(rows, rows[1:]):
        if abs(v1 - v0) > 1e-9:
            last = (d1, round(v1 - v0, 2))
    return last


def month_ago_delta(rows, d, v):
    prev = value_on_or_before(rows, d - dt.timedelta(days=30))
    return None if prev is None else round(v - prev[1], 4)


def chart_series(rows, latest):
    start = years_ago(latest, CHART_YEARS)
    pts = since(rows, start)
    if len(pts) > 400:  # 휴대폰에서 가볍게: 대략 주 단위로 줄이기
        step = len(pts) // 300 + 1
        pts = pts[::step] + ([pts[-1]] if pts[-1] not in pts[::step] else [])
    return [[d.isoformat(), v] for d, v in pts]


# ===========================================================================
# 3. 검증 도우미
# ===========================================================================
class MissingData(Exception):
    pass


def chk(name, ok, detail):
    """ok: True(통과) / False(실패) / None(확인 불가)"""
    return {"name": name, "ok": ok, "detail": detail}


def check_fresh(last_date, today, max_days):
    age = (today - last_date).days
    return chk("최신성", age <= max_days, f"{last_date.isoformat()} 기준 ({age}일 전, 허용 {max_days}일)")


def check_range(v, lo, hi, unit=""):
    return chk("범위", lo <= v <= hi, f"{v:g}{unit} (정상 범위 {lo:g}~{hi:g}{unit})")


def check_jumps(rows, max_step, unit="", recent=25):
    tail = rows[-recent:]
    jumps = [(d1, v1 - v0) for (d0, v0), (d1, v1) in zip(tail, tail[1:]) if abs(v1 - v0) > max_step]
    fmt = (lambda x: f"{x:,.0f}") if unit == "원" else (lambda x: f"{x:.2f}")
    if not jumps:
        return chk("급변", True, f"최근 하루(한 달) 변동 모두 {fmt(max_step)}{unit} 이내")
    d, s = jumps[-1]
    return chk("급변", False, f"{d.isoformat()}에 {'+' if s > 0 else ''}{fmt(s)}{unit} 변동 (기준 {fmt(max_step)}{unit})")


def check_cross(name, mine, theirs, tol, unit="", rel=False):
    if theirs is None:
        return chk(name, None, "비교할 데이터를 받지 못함")
    diff = abs(mine - theirs) / abs(theirs) * 100 if rel else abs(mine - theirs)
    tol_txt = f"{tol:g}%" if rel else f"{tol:g}{unit}"
    diff_txt = f"{diff:.2f}%" if rel else f"{diff:.2f}{unit}"
    return chk(name, diff <= tol, f"FRED {mine:g} vs 원출처 {theirs:g} (차이 {diff_txt}, 허용 {tol_txt})")


def decide(checks):
    """검증 결과 판정.
    - 하나라도 실패(False): 단, '급변' 실패는 교차검증이 통과하면 진짜 변동으로 인정
    - 교차검증을 하나도 못 했으면 '단일 출처'
    """
    cross = [c for c in checks if c["name"].startswith("교차")]
    cross_ok = any(c["ok"] is True for c in cross) and not any(c["ok"] is False for c in cross)
    failed = [c for c in checks if c["ok"] is False and not (c["name"] == "급변" and cross_ok)]
    if failed:
        return "blocked", "; ".join(f"{c['name']}: {c['detail']}" for c in failed)
    if cross_ok:
        n = sum(1 for c in cross if c["ok"])
        return "verified", f"원출처와 교차검증 {n}건 일치"
    return "single", "원출처를 받지 못해 교차검증은 못 함 (최신성·범위·급변 검사는 통과)"


# ===========================================================================
# 4. 지표 카드 만들기
#    gauge: 막대 위에 현재 위치를 표시. zones = [(이 값까지, 상태), ...]
# ===========================================================================
def gauge(lo, hi, zones, marker, ref=None, ref_label=""):
    def pct(x):
        return max(0.0, min(100.0, (x - lo) / (hi - lo) * 100))
    segs, prev = [], lo
    for upto, status in zones:
        end = min(upto, hi)
        if end > prev:
            segs.append({"from": pct(prev), "to": pct(end), "status": status})
        prev = end
    ticks = sorted({lo, hi} | {u for u, _ in zones if lo < u < hi})
    return {
        "segs": segs, "marker": pct(marker),
        "ticks": [{"pos": pct(t), "label": f"{t:,.0f}" if hi - lo > 50 else f"{t:g}"} for t in ticks],
        "ref": pct(ref) if ref is not None else None, "ref_label": ref_label,
    }


def zone_of(v, zones):
    for upto, status in zones:
        if v < upto:
            return status
    return zones[-1][1]


def card_us10y(f, s, today):
    rows = f.get("DGS10") or []
    if not rows:
        raise MissingData("DGS10")
    d, v = rows[-1]
    checks = [check_fresh(d, today, 10), check_range(v, 0, 20, "%"), check_jumps(rows, 0.5, "%p")]
    src = "FRED DGS10"
    tr = s.get("treasury")
    if tr is not None:
        t = tr.get(d)
        checks.append(check_cross("교차검증(미 재무부)", v, t["10"] if t else None, 0.02, "%p"))
        newer = sorted(k for k in tr if k > d)
        if newer and checks[-1]["ok"]:
            d, v = newer[-1], tr[newer[-1]]["10"]
            src = "미 재무부 (FRED와 교차검증)"
    else:
        checks.append(chk("교차검증(미 재무부)", None, s.get("_err", {}).get("treasury", "받지 못함")))
    zones = [(4.5, "good"), (5.0, "watch"), (99, "warn")]
    status = zone_of(v, zones)
    meaning = {
        "good": "주식·부동산에 큰 부담은 아닌 수준이에요.",
        "watch": "높은 편이에요. 더 오르면 주식·부동산에 부담이 커져요.",
        "warn": "2007년 이후 최고 수준권. 주식·부동산에 큰 부담이에요.",
    }[status]
    return dict(
        id="us10y", title="미국 10년물 국채 금리", value=v, value_text=f"{v:.2f}", unit="%",
        date=d.isoformat(), delta=month_ago_delta(rows, d, v), delta_unit="%p",
        status=status, meaning=meaning,
        explain="전 세계 '돈의 기본 가격'. 이게 오르면 대출 이자가 비싸지고, 예금·채권 매력이 커져서 주식과 부동산으로 가던 돈이 줄어요.",
        source=src, checks=checks,
        gauge=gauge(2, 6, zones, v),
        chart={"series": [{"name": "10년물", "pts": chart_series(rows, d)}], "unit": "%"},
    )


def card_spread(f, s, today):
    rows = f.get("T10Y2Y") or []
    if not rows:
        raise MissingData("T10Y2Y")
    d, v = rows[-1]
    checks = [check_fresh(d, today, 10), check_range(v, -5, 5, "%p"), check_jumps(rows, 0.4, "%p")]
    a, b = value_on_or_before(f.get("DGS10", []), d), value_on_or_before(f.get("DGS2", []), d)
    if a and b and a[0] == d and b[0] == d:
        checks.append(check_cross("교차검증(10년-2년 직접 계산)", v, round(a[1] - b[1], 2), 0.03, "%p"))
    tr = s.get("treasury")
    src = "FRED T10Y2Y"
    if tr is not None:
        t = tr.get(d)
        checks.append(check_cross("교차검증(미 재무부)", v, round(t["10"] - t["2"], 2) if t else None, 0.03, "%p"))
        newer = sorted(k for k in tr if k > d)
        if newer and checks[-1]["ok"]:
            d = newer[-1]
            v = round(tr[d]["10"] - tr[d]["2"], 2)
            src = "미 재무부 (FRED와 교차검증)"
    recent_inv = any(x < 0 for _, x in since(rows, d - dt.timedelta(days=730)))
    if v < 0:
        status, meaning = "warn", "역전 상태. 시장이 앞으로 경기가 나빠질 거라고 보고 있다는 신호예요."
    elif v < 0.5 or recent_inv:
        status = "watch"
        meaning = ("최근 2년 안에 역전됐다가 플러스로 돌아왔어요. 과거엔 이 무렵에 경기침체가 온 경우가 많았어요."
                   if recent_inv else "플러스지만 폭이 좁아요. 역전으로 넘어가는지 지켜볼 구간이에요.")
    else:
        status, meaning = "good", "정상 상태. 장기 금리가 단기보다 충분히 높아요."
    return dict(
        id="spread", title="장단기 금리차 (10년−2년)", value=v, value_text=f"{v:+.2f}", unit="%p",
        date=d.isoformat(), delta=month_ago_delta(rows, d, v), delta_unit="%p",
        status=status, meaning=meaning,
        explain="10년짜리 금리에서 2년짜리 금리를 뺀 값. 원래는 오래 빌려줄수록 이자가 높아야 정상(플러스)이에요. 마이너스가 되면 '역전'이라고 하고, 전통적인 경기침체 경고로 봐요.",
        source=src, checks=checks,
        gauge=gauge(-1, 2, [(0, "warn"), (0.5, "watch"), (99, "good")], v),
        chart={"series": [{"name": "10년−2년", "pts": chart_series(rows, d)}], "unit": "%p", "ref": 0},
    )


def card_fed(f, s, today):
    rows = f.get("DFEDTARU") or []
    if not rows:
        raise MissingData("DFEDTARU")
    d, v = rows[-1]
    checks = [check_fresh(d, today, 10), check_range(v, 0, 20, "%")]
    ny = s.get("nyfed")
    if ny is not None:
        checks.append(check_cross("교차검증(뉴욕 연준)", v, ny["upper"], 0.001, "%p"))
        checks.append(chk("실제 시장금리", ny["lower"] - 0.05 <= ny["effr"] <= ny["upper"] + 0.05,
                          f"실효 연방기금금리 {ny['effr']:.2f}%가 목표 범위 {ny['lower']:.2f}~{ny['upper']:.2f}% 안에 있음"))
    else:
        checks.append(chk("교차검증(뉴욕 연준)", None, s.get("_err", {}).get("nyfed", "받지 못함")))
    lower = round(v - 0.25, 2)
    chg = last_rate_change(rows)
    if chg:
        cd, delta = chg
        word = "인상" if delta > 0 else "인하"
        last_txt = f"마지막 변경 {cd.strftime('%Y.%m.%d')} · {abs(delta):.2f}%p {word}"
        if delta <= -0.5:
            status, meaning = "warn", "0.5%p 이상 큰 폭 인하. 과거엔 위기 대응일 때가 많았어요."
        elif delta < 0:
            status, meaning = "info", "0.25%p씩 내리는 중. 위기보단 예방 차원의 인하일 가능성이 커요."
        else:
            status, meaning = "info", "마지막 움직임이 인상. 연준이 아직 물가를 더 걱정하고 있어요."
    else:
        last_txt, status, meaning = "기간 내 변경 없음", "info", "한동안 변화가 없었어요."
    return dict(
        id="fed", title="미국 기준금리", value=v, value_text=f"{lower:.2f}~{v:.2f}", unit="%",
        date=d.isoformat(), delta=None, delta_text=last_txt,
        status=status, meaning=meaning,
        explain="연준이 정하는 '은행끼리 하루 빌려주는 금리'의 목표 범위. 내리는 폭(0.25 vs 0.5 이상)과 정례회의가 아닌 긴급 인하인지가 위기 신호를 가르는 기준이에요.",
        source="FRED DFEDTARU", checks=checks,
        gauge=gauge(0, 6, [(99, "info")], v),
        chart={"series": [{"name": "기준금리 상단", "pts": chart_series(rows, d)}], "unit": "%", "step": True},
    )


def card_sahm(f, s, today):
    sahm, un = f.get("SAHMREALTIME") or [], f.get("UNRATE") or []
    if not sahm or not un:
        raise MissingData("SAHMREALTIME/UNRATE")
    d, v = sahm[-1]
    ud, uv = un[-1]
    checks = [check_fresh(ud, today, 80), check_range(uv, 0, 30, "%"), check_range(v, -1, 10),
              check_jumps(un, 1.0, "%p", recent=6)]
    mine = sahm_from_unrate(un)
    checks.append(check_cross("교차검증(실업률로 직접 계산)", v, mine, 0.2))
    bls = s.get("bls_un")
    if bls is not None:
        checks.append(check_cross("교차검증(미 노동통계국 실업률)", uv, bls.get((ud.year, ud.month)), 0.05, "%p"))
    else:
        checks.append(chk("교차검증(미 노동통계국 실업률)", None, s.get("_err", {}).get("bls_un", "받지 못함")))
    zones = [(0.3, "good"), (0.5, "watch"), (99, "warn")]
    status = zone_of(v, zones)
    meaning = {
        "good": "고용이 아직 안정적이에요.",
        "watch": "실업률이 오르기 시작했어요. 0.5를 넘으면 침체 신호예요.",
        "warn": "침체 신호 기준(0.5)을 넘었어요. 과거엔 거의 항상 경기침체였어요.",
    }[status]
    prev = value_on_or_before(sahm, d - dt.timedelta(days=28))
    return dict(
        id="sahm", title="고용 경고등 (삼의 법칙)", value=v, value_text=f"{v:.2f}", unit="",
        date=d.isoformat(), delta=None if prev is None else round(v - prev[1], 2), delta_unit="",
        sub=f"실업률 {uv:.1f}% ({ud.strftime('%Y.%m')})",
        status=status, meaning=meaning,
        explain="최근 3개월 평균 실업률이 지난 1년 중 가장 낮았던 때보다 얼마나 올랐는지. 0.5 이상이면 1970년 이후 미국 경기침체를 거의 다 잡아낸 지표예요.",
        source="FRED SAHMREALTIME, UNRATE", checks=checks,
        gauge=gauge(0, 1, zones, v),
        chart={"series": [{"name": "삼의 법칙", "pts": chart_series(sahm, d)}], "unit": "", "ref": 0.5},
    )


def card_cpi(f, s, today):
    raw = f.get("CPIAUCSL") or []
    if not raw:
        raise MissingData("CPIAUCSL")
    yoy = cpi_yoy(raw)
    d, v = yoy[-1]
    rd, rv = raw[-1]
    checks = [check_fresh(rd, today, 80), check_range(v, -5, 20, "%"), check_jumps(raw, rv * 0.02, "pt", recent=6)]
    bls = s.get("bls_cpi")
    if bls is not None:
        checks.append(check_cross("교차검증(미 노동통계국 물가지수)", rv, bls.get((rd.year, rd.month)), 0.1, rel=True))
    else:
        checks.append(chk("교차검증(미 노동통계국 물가지수)", None, s.get("_err", {}).get("bls_cpi", "받지 못함")))
    zones = [(2.5, "good"), (3.5, "watch"), (99, "warn")]
    status = zone_of(v, zones)
    meaning = {
        "good": "연준 목표(2%)에 가까워요. 금리를 내릴 여유가 있어요.",
        "watch": "목표(2%)보다 높아요. 연준이 금리를 쉽게 못 내려요.",
        "warn": "물가 부담이 커요. 금리 인상 압력이 있어요.",
    }[status]
    prev = yoy[-2][1] if len(yoy) > 1 else None
    return dict(
        id="cpi", title="미국 물가상승률", value=v, value_text=f"{v:.1f}", unit="%",
        date=d.isoformat(), delta=None if prev is None else round(v - prev, 2), delta_unit="%p",
        sub="1년 전 대비",
        status=status, meaning=meaning,
        explain="물가가 1년 전보다 얼마나 올랐는지. 연준 목표는 2%예요. 높을수록 연준이 금리를 내리기 어렵고, 경기가 나빠도 금리를 못 내리는 '스태그플레이션' 위험이 커져요.",
        source="FRED CPIAUCSL (1년 전 대비로 계산)", checks=checks,
        gauge=gauge(0, 6, zones, v, 2, "목표 2%"),
        chart={"series": [{"name": "물가상승률", "pts": chart_series(yoy, d)}], "unit": "%", "ref": 2},
    )


def card_fx(f, s, today):
    rows = f.get("DEXKOUS") or []
    if not rows:
        raise MissingData("DEXKOUS")
    d, v = rows[-1]
    checks = [check_fresh(d, today, 14), check_range(v, 800, 2500, "원"), check_jumps(rows, v * 0.04, "원")]
    src = "FRED DEXKOUS (뉴욕 시장 정오 환율)"
    ecb = s.get("ecb_fx")
    if ecb is not None:
        same = nearest(ecb, d, 3)
        checks.append(check_cross("교차검증(유럽중앙은행 기준환율)", v, same[1] if same else None, 1.0, rel=True))
        if ecb[-1][0] > d and checks[-1]["ok"] and abs(ecb[-1][1] - v) / v < 0.04:
            d, v = ecb[-1]
            src = "유럽중앙은행 기준환율 (FRED와 교차검증)"
    else:
        checks.append(chk("교차검증(유럽중앙은행 기준환율)", None, s.get("_err", {}).get("ecb_fx", "받지 못함")))
    window = since(rows, years_ago(d, 3))
    avg3 = sum(x for _, x in window) / len(window)
    diff = v - avg3
    if v < avg3:
        status = "good"
        meaning = f"3년 평균({avg3:,.0f}원)보다 {abs(diff):,.0f}원 낮아요. 박종훈 소장 규칙으로는 나눠서 환전하는 구간이에요."
    else:
        status = "watch"
        meaning = f"3년 평균({avg3:,.0f}원)보다 {diff:,.0f}원 높아요. 박종훈 소장 규칙으로는 환전을 기다리는 구간이에요."
    span = max(150, abs(diff) * 1.3)
    avg_line = []
    for dd, _ in since(rows, years_ago(d, CHART_YEARS))[::5]:
        w = [x for t, x in rows if years_ago(dd, 3) <= t <= dd]
        avg_line.append([dd.isoformat(), round(sum(w) / len(w), 1)])
    return dict(
        id="fx", title="원/달러 환율", value=v, value_text=f"{v:,.0f}", unit="원",
        date=d.isoformat(), delta=month_ago_delta(rows, d, v), delta_unit="원",
        sub=f"3년 평균 {avg3:,.0f}원",
        status=status, meaning=meaning,
        explain="1달러를 사는 데 드는 원화. 국제 기준환율이라 은행 앱의 매매기준율과 몇 원 차이 날 수 있어요. 막대의 점선이 최근 3년 평균이에요.",
        source=src, checks=checks,
        gauge=gauge(avg3 - span, avg3 + span, [(avg3, "good"), (99999, "watch")], v, avg3, "3년 평균"),
        chart={"series": [{"name": "환율", "pts": chart_series(rows, d)}, {"name": "3년 평균", "pts": avg_line}], "unit": "원"},
    )


CARDS = [  # (함수, 카드 제목) — 제목은 기록 파일의 열 이름이라 바꾸지 않는 게 좋아요
    (card_us10y, "미국 10년물 국채 금리"),
    (card_spread, "장단기 금리차 (10년−2년)"),
    (card_fed, "미국 기준금리"),
    (card_sahm, "고용 경고등 (삼의 법칙)"),
    (card_cpi, "미국 물가상승률"),
    (card_fx, "원/달러 환율"),
]


# ===========================================================================
# 5. 저장 (검증된 값만)
# ===========================================================================
def load_state():
    try:
        return json.loads(STATE_JSON.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}


def save_state(state):
    DATA_DIR.mkdir(exist_ok=True)
    STATE_JSON.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


def append_log(today, cards):
    DATA_DIR.mkdir(exist_ok=True)
    fields = ["실행일"] + [t for _, t in CARDS]
    row = {"실행일": today.isoformat()}
    for c in cards:
        if c.get("verify") in ("verified", "single"):
            row[c["title"]] = round(c["value"], 4)
    existing = []
    if LOG_CSV.exists():
        with LOG_CSV.open(encoding="utf-8-sig", newline="") as fh:
            reader = csv.DictReader(fh)
            for name in reader.fieldnames or []:
                if name not in fields:
                    fields.append(name)
            existing = [r for r in reader if r.get("실행일") != today.isoformat()]
    existing.append(row)
    with LOG_CSV.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(existing)
    return existing


# ===========================================================================
# 6. 화면 만들기 (휴대폰 우선)
# ===========================================================================
STATUS = {"good": "양호", "watch": "주의", "warn": "경고", "info": "참고", "error": "오류"}
VERIFY = {
    "verified": ("ok", "✓ 검증됨"),
    "single": ("mid", "△ 출처 1곳"),
    "held": ("bad", "⏸ 업데이트 보류"),
    "error": ("bad", "✕ 데이터 없음"),
}


def fmt_date(iso):
    d = dt.date.fromisoformat(iso)
    return f"{d.month}/{d.day}"


def render_card(c):
    e = html.escape
    if c["verify"] == "error":
        return f"""<article class="card st-error">
  <div class="head"><h2>{e(c['title'])}</h2><span class="pill st-error">오류</span></div>
  <p class="meaning">데이터를 가져오지 못했고, 이전에 검증된 값도 없어요.</p>
  <p class="verify bad">✕ {e(c['reason'])}</p></article>"""

    if c.get("delta") is not None:
        dv = c["delta"]
        arrow = "▲" if dv > 0 else ("▼" if dv < 0 else "–")
        unit = c.get("delta_unit", "")
        num = f"{abs(dv):,.0f}" if unit == "원" else f"{abs(dv):.2f}"
        delta_html = f'<span class="delta">{arrow} {num}{e(unit)} <small>한 달 전보다</small></span>'
    else:
        delta_html = f'<span class="delta"><small>{e(c.get("delta_text", ""))}</small></span>'

    g = c["gauge"]
    segs = "".join(
        f'<i class="seg st-{x["status"]}" style="left:{x["from"]:.2f}%;width:{x["to"] - x["from"]:.2f}%"></i>'
        for x in g["segs"])
    ticks = "".join(f'<span class="tick" style="left:{t["pos"]:.2f}%">{e(t["label"])}</span>' for t in g["ticks"])
    ref = ""
    if g.get("ref") is not None:
        ref = (f'<i class="ref" style="left:{g["ref"]:.2f}%"></i>'
               f'<span class="reflabel" style="left:{g["ref"]:.2f}%">{e(g["ref_label"])}</span>')
    gauge_html = f"""<div class="gauge">
    <div class="track">{segs}{ref}<i class="marker" style="left:{g['marker']:.2f}%"></i></div>
    <div class="ticks">{ticks}</div></div>"""

    vcls, vlabel = VERIFY[c["verify"]]
    vnote = c.get("reason", "")
    checks = "".join(
        f'<li class="{"ok" if k["ok"] else ("na" if k["ok"] is None else "bad")}">'
        f'<b>{"✓" if k["ok"] else ("–" if k["ok"] is None else "✕")} {e(k["name"])}</b> {e(k["detail"])}</li>'
        for k in c.get("checks", []))
    sub = f'<p class="sub">{e(c["sub"])}</p>' if c.get("sub") else ""

    return f"""<article class="card st-{c['status']}" data-id="{c['id']}">
  <div class="head"><h2>{e(c['title'])}</h2><span class="pill st-{c['status']}">{STATUS[c['status']]}</span></div>
  <div class="valrow"><span class="num">{e(c['value_text'])}</span><span class="unit">{e(c['unit'])}</span>{delta_html}</div>
  {sub}
  {gauge_html}
  <p class="meaning">{e(c['meaning'])}</p>
  <p class="verify {vcls}">{vlabel} · {fmt_date(c['date'])} 기준{(' · ' + e(vnote)) if c['verify'] != 'verified' else ''}</p>
  <details>
    <summary>자세히</summary>
    <p class="explain">{e(c['explain'])}</p>
    <div class="chart"><canvas id="ch-{c['id']}"></canvas></div>
    <p class="cap">최근 {CHART_YEARS}년 추이</p>
    <ul class="checks">{checks}</ul>
    <p class="src">출처: {e(c['source'])}</p>
  </details>
</article>"""


def build_html(cards, log_rows, generated):
    counts = {k: sum(1 for c in cards if c["status"] == k) for k in STATUS}
    warn_names = [c["title"] for c in cards if c["status"] == "warn"]
    watch_names = [c["title"] for c in cards if c["status"] == "watch"]
    if warn_names:
        headline = "경고: " + ", ".join(warn_names)
    elif watch_names:
        headline = "경고는 없고, 주의할 지표가 있어요"
    else:
        headline = "눈에 띄는 위험 신호는 없어요"
    nv = sum(1 for c in cards if c["verify"] == "verified")
    held = [c["title"] for c in cards if c["verify"] in ("held", "error")]
    verify_line = f"6개 중 {nv}개 원출처와 교차검증 완료"
    if held:
        verify_line += f" · 업데이트 보류: {', '.join(held)}"

    charts = {c["id"]: c["chart"] for c in cards if c.get("chart")}
    data_json = json.dumps(charts, ensure_ascii=False).replace("</", "<\\/")

    log_html = ""
    if log_rows:
        cols = ["실행일"] + [t for _, t in CARDS]
        head = "".join(f"<th>{html.escape(h)}</th>" for h in cols)
        body = "".join("<tr>" + "".join(f"<td>{html.escape(str(r.get(h, '') or '–'))}</td>" for h in cols) + "</tr>"
                       for r in reversed(log_rows[-12:]))
        log_html = f'<div class="tablewrap"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'

    sample = ('<div class="sample">⚠ 샘플 데이터로 만든 테스트 화면입니다. 숫자는 실제가 아닙니다.</div>'
              if SAMPLE_MODE else "")

    return f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#f6f5f2">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="거시체크">
<link rel="apple-touch-icon" href="apple-touch-icon.png">
<link rel="icon" type="image/png" href="favicon.png">
<link rel="manifest" href="manifest.webmanifest">
<title>거시경제 체크</title>
<style>
:root {{
  --bg:#f6f5f2; --panel:#fff; --ink:#1c1c1a; --muted:#6d6c67; --line:#e5e3dd;
  --good:#2e7d4f; --watch:#b57300; --warn:#c0362c; --info:#3a64a8;
  --good-s:#dff0e5; --watch-s:#fbedd2; --warn-s:#fadedb; --info-s:#e2eaf7;
  --c1:#3a64a8; --c2:#c27a2c;
}}
@media (prefers-color-scheme: dark) {{
  :root {{
    --bg:#141412; --panel:#1e1e1b; --ink:#edece7; --muted:#a2a19a; --line:#34332f;
    --good:#7dd09d; --watch:#f2c46b; --warn:#f3958c; --info:#9dbdf2;
    --good-s:#1c3626; --watch-s:#3a2d12; --warn-s:#40201d; --info-s:#1c2a42;
    --c1:#9dbdf2; --c2:#f2b46b;
  }}
}}
* {{ box-sizing:border-box; }}
html {{ -webkit-text-size-adjust:100%; }}
body {{ margin:0; background:var(--bg); color:var(--ink); line-height:1.5;
  font-family:"Pretendard","Apple SD Gothic Neo","Malgun Gothic",system-ui,sans-serif; }}
main {{ max-width:1040px; margin:0 auto; padding:20px 16px calc(40px + env(safe-area-inset-bottom)); }}
.sample {{ background:var(--warn-s); color:var(--warn); font-weight:700; padding:10px 14px; border-radius:10px; margin-bottom:14px; }}
header h1 {{ font-size:22px; margin:0; letter-spacing:-0.02em; }}
header .upd {{ color:var(--muted); font-size:13px; margin:2px 0 14px; }}
.overview {{ background:var(--panel); border:1px solid var(--line); border-radius:14px; padding:14px 16px; margin-bottom:16px; }}
.overview .hl {{ font-size:16px; font-weight:700; margin:0 0 10px; }}
.chips {{ display:flex; gap:8px; flex-wrap:wrap; }}
.chip {{ font-size:13px; font-weight:700; padding:4px 10px; border-radius:999px; }}
.overview .vl {{ font-size:12.5px; color:var(--muted); margin:10px 0 0; }}
.grid {{ display:grid; grid-template-columns:1fr; gap:12px; }}
@media (min-width:760px) {{ .grid {{ grid-template-columns:1fr 1fr; }} }}
.card {{ background:var(--panel); border:1px solid var(--line); border-radius:16px; padding:16px; min-width:0;
  border-left:5px solid var(--line); }}
.card.st-good {{ border-left-color:var(--good); }} .card.st-watch {{ border-left-color:var(--watch); }}
.card.st-warn {{ border-left-color:var(--warn); }} .card.st-info {{ border-left-color:var(--info); }}
.head {{ display:flex; justify-content:space-between; align-items:center; gap:8px; }}
.head h2 {{ font-size:15px; margin:0; font-weight:650; }}
.pill, .chip {{ white-space:nowrap; }}
.pill {{ font-size:12px; font-weight:700; padding:3px 10px; border-radius:999px; }}
.st-good.pill, .chip.st-good {{ color:var(--good); background:var(--good-s); }}
.st-watch.pill, .chip.st-watch {{ color:var(--watch); background:var(--watch-s); }}
.st-warn.pill, .chip.st-warn {{ color:var(--warn); background:var(--warn-s); }}
.st-info.pill, .chip.st-info {{ color:var(--info); background:var(--info-s); }}
.st-error.pill {{ color:var(--muted); background:var(--bg); }}
.valrow {{ display:flex; align-items:baseline; flex-wrap:wrap; gap:2px 6px; margin:8px 0 0; }}
.num {{ font-size:34px; font-weight:780; letter-spacing:-0.03em; font-variant-numeric:tabular-nums; }}
.unit {{ font-size:15px; color:var(--muted); }}
.delta {{ font-size:14px; font-weight:650; margin-left:auto; font-variant-numeric:tabular-nums; }}
.delta small {{ font-weight:500; color:var(--muted); }}
.sub {{ margin:0; font-size:13px; color:var(--muted); }}
.gauge {{ margin:14px 0 4px; }}
.track {{ position:relative; height:10px; border-radius:999px; background:var(--line); }}
.seg {{ position:absolute; top:0; bottom:0; opacity:.55; }}
.seg:first-child {{ border-radius:999px 0 0 999px; }} .seg:last-of-type {{ border-radius:0 999px 999px 0; }}
.seg.st-good {{ background:var(--good); }} .seg.st-watch {{ background:var(--watch); }}
.seg.st-warn {{ background:var(--warn); }} .seg.st-info {{ background:var(--info); opacity:.3; }}
.marker {{ position:absolute; top:50%; width:18px; height:18px; margin:-9px 0 0 -9px; border-radius:50%;
  background:var(--panel); border:4px solid var(--ink); box-shadow:0 1px 3px rgba(0,0,0,.25); }}
.ref {{ position:absolute; top:-5px; bottom:-5px; border-left:2px dashed var(--ink); opacity:.6; }}
.reflabel {{ position:absolute; top:-22px; transform:translateX(-50%); font-size:11px; color:var(--muted); white-space:nowrap; }}
.ticks {{ position:relative; height:18px; margin-top:4px; }}
.tick {{ position:absolute; transform:translateX(-50%); font-size:11px; color:var(--muted); font-variant-numeric:tabular-nums; }}
.tick:first-child {{ transform:none; }} .tick:last-child {{ transform:translateX(-100%); }}
.gauge:has(.reflabel) {{ margin-top:30px; }}
.meaning {{ margin:8px 0 6px; font-size:14.5px; font-weight:600; }}
.verify {{ margin:0; font-size:12px; }}
.verify.ok {{ color:var(--good); }} .verify.mid {{ color:var(--watch); }} .verify.bad {{ color:var(--warn); }}
details {{ margin-top:10px; border-top:1px solid var(--line); padding-top:8px; }}
summary {{ cursor:pointer; font-size:13px; color:var(--muted); font-weight:600; list-style:none; padding:4px 0; }}
summary::-webkit-details-marker {{ display:none; }}
summary::after {{ content:" ▾"; }} details[open] summary::after {{ content:" ▴"; }}
.explain {{ font-size:13.5px; margin:6px 0 10px; }}
.chart {{ position:relative; height:170px; }}
.cap {{ margin:2px 0 8px; font-size:11.5px; color:var(--muted); text-align:right; }}
.checks {{ list-style:none; padding:0; margin:0 0 8px; font-size:12px; color:var(--muted); }}
.checks li {{ padding:3px 0; }} .checks b {{ font-weight:650; }}
.checks .ok b {{ color:var(--good); }} .checks .bad b {{ color:var(--warn); }}
.src {{ font-size:11.5px; color:var(--muted); margin:0; }}
section {{ margin-top:22px; }}
section h2 {{ font-size:16px; margin:0 0 6px; }}
section p {{ font-size:13px; color:var(--muted); margin:0 0 8px; }}
.tablewrap {{ overflow-x:auto; background:var(--panel); border:1px solid var(--line); border-radius:12px; }}
table {{ border-collapse:collapse; font-size:12.5px; font-variant-numeric:tabular-nums; min-width:100%; }}
th, td {{ padding:7px 10px; border-bottom:1px solid var(--line); text-align:right; white-space:nowrap; }}
th:first-child, td:first-child {{ text-align:left; position:sticky; left:0; background:var(--panel); }}
th {{ color:var(--muted); font-weight:600; }}
footer {{ margin-top:22px; font-size:12px; color:var(--muted); }}
</style>
</head>
<body>
<main>
  {sample}
  <header>
    <h1>거시경제 체크</h1>
    <p class="upd">업데이트 {generated}</p>
  </header>
  <div class="overview">
    <p class="hl">{html.escape(headline)}</p>
    <div class="chips">
      <span class="chip st-warn">경고 {counts['warn']}</span>
      <span class="chip st-watch">주의 {counts['watch']}</span>
      <span class="chip st-good">양호 {counts['good']}</span>
      <span class="chip st-info">참고 {counts['info']}</span>
    </div>
    <p class="vl">{html.escape(verify_line)}</p>
  </div>
  <div class="grid">{''.join(render_card(c) for c in cards)}</div>
  <section>
    <h2>지난 기록</h2>
    <p>검증을 통과한 숫자만 기록돼요. 옆으로 밀어서 보세요.</p>
    {log_html}
  </section>
  <footer>
    막대의 ● 표시가 현재 위치예요. 초록=양호, 주황=주의, 빨강=경고 구간.<br>
    ✓ 검증됨 = FRED 숫자가 원래 발표 기관 숫자와 일치 · △ 출처 1곳 = 원출처를 못 받아 기본 검사만 통과 ·
    ⏸ 보류 = 검증 실패로 마지막 검증값 표시 중.<br>
    데이터: FRED, 미 재무부, 뉴욕 연준, 미 노동통계국, 유럽중앙은행. 투자 권유가 아닌 참고 자료입니다.
  </footer>
</main>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<script>
const DATA = {data_json};
const made = {{}};
function draw(id) {{
  if (made[id] || typeof Chart === "undefined" || !DATA[id]) return;
  const el = document.getElementById("ch-" + id); if (!el) return;
  const cfg = DATA[id], css = getComputedStyle(document.documentElement), col = n => css.getPropertyValue(n).trim();
  const colors = [col("--c1"), col("--c2")];
  const labels = cfg.series[0].pts.map(p => p[0]);
  const datasets = cfg.series.map((s, i) => {{
    const m = new Map(s.pts);
    return {{ label: s.name, data: labels.map(x => m.has(x) ? m.get(x) : null), borderColor: colors[i % 2],
      backgroundColor: colors[i % 2], borderWidth: 1.8, pointRadius: 0, spanGaps: true, stepped: !!cfg.step }};
  }});
  if (cfg.ref !== undefined) datasets.push({{ label: "기준선", data: labels.map(() => cfg.ref),
    borderColor: col("--muted"), borderDash: [4, 4], borderWidth: 1, pointRadius: 0 }});
  made[id] = new Chart(el, {{ type: "line", data: {{ labels, datasets }}, options: {{
    responsive: true, maintainAspectRatio: false, animation: false,
    interaction: {{ mode: "index", intersect: false }},
    plugins: {{ legend: {{ display: cfg.series.length > 1, labels: {{ color: col("--muted"), boxWidth: 10 }} }} }},
    scales: {{
      x: {{ ticks: {{ maxTicksLimit: 4, color: col("--muted"), maxRotation: 0,
            callback: function (v) {{ return this.getLabelForValue(v).slice(2, 7).replace("-", "."); }} }}, grid: {{ display: false }} }},
      y: {{ ticks: {{ maxTicksLimit: 4, color: col("--muted") }}, grid: {{ color: col("--line") }} }} }} }} }});
}}
document.querySelectorAll("details").forEach(d => d.addEventListener("toggle", () => {{
  if (d.open) draw(d.closest(".card").dataset.id);
}}));
</script>
</body>
</html>
"""


# ===========================================================================
# 7. 실행
# ===========================================================================
FRED_IDS = ["DGS10", "DGS2", "T10Y2Y", "DFEDTARU", "UNRATE", "SAHMREALTIME", "CPIAUCSL", "DEXKOUS"]
SECONDARY = {
    "treasury": lambda today: fetch_treasury(today),
    "nyfed": lambda today: fetch_nyfed(),
    "bls_un": lambda today: fetch_bls("LNS14000000"),
    "bls_cpi": lambda today: fetch_bls("CUSR0000SA0"),
    "ecb_fx": lambda today: fetch_ecb_fx(today),
}


def collect(today):
    start = years_ago(today, CHART_YEARS + 4)
    fred, sec, errs = {}, {}, {}
    via = "공식 API(키 사용)" if os.environ.get("FRED_API_KEY", "").strip() else "CSV(키 없음)"
    print(f"1) FRED 데이터 받는 중 - {via}")
    for sid in FRED_IDS:
        try:
            fred[sid] = fetch_fred(sid, start)
            print(f"   OK   {sid} ({len(fred[sid])}개, 최신 {fred[sid][-1][0]})")
        except Exception as exc:
            print(f"   실패 {sid}: {exc}")
    print("2) 교차검증용 원출처 데이터 받는 중")
    for name, fn in SECONDARY.items():
        try:
            sec[name] = fn(today)
            print(f"   OK   {name}")
        except Exception as exc:
            errs[name] = f"받지 못함 ({type(exc).__name__}: {exc})"
            print(f"   실패 {name}: {exc}")
    sec["_err"] = errs
    return fred, sec


def build_cards(fred, sec, today, state):
    cards = []
    for builder, title in CARDS:
        key = builder.__name__
        try:
            card = builder(fred, sec, today)
            verdict, reason = decide(card["checks"])
        except MissingData as exc:
            card, verdict, reason = None, "blocked", f"FRED {exc} 데이터를 받지 못함"
        except Exception as exc:
            card, verdict, reason = None, "blocked", f"계산 오류 ({type(exc).__name__}: {exc})"

        if verdict in ("verified", "single"):
            card["verify"], card["reason"] = verdict, reason
            state[key] = card
            cards.append(card)
        elif key in state:  # 실패 → 마지막으로 검증된 값을 보여줌
            old = dict(state[key])
            old["verify"] = "held"
            old["reason"] = f"새 데이터 검증 실패로 이전 값 표시 ({reason})"
            if card is not None:
                old["checks"] = card["checks"]
            cards.append(old)
        else:
            cards.append({"id": key, "title": title, "status": "error", "verify": "error", "reason": reason})
    return cards


MANIFEST = {
    "name": "거시경제 체크", "short_name": "거시체크", "start_url": ".", "display": "standalone",
    "background_color": "#1f3a5f", "theme_color": "#f6f5f2",
    "icons": [{"src": "icon-192.png", "sizes": "192x192", "type": "image/png"},
              {"src": "icon-512.png", "sizes": "512x512", "type": "image/png"}],
}


def write_icons():
    """아이콘과 앱 정보(manifest)를 site/에 넣음 → 홈 화면 추가 시 이 아이콘이 보임"""
    if ASSETS_DIR.is_dir():
        for png in ASSETS_DIR.glob("*.png"):
            shutil.copy2(png, SITE_DIR / png.name)
    (SITE_DIR / "manifest.webmanifest").write_text(json.dumps(MANIFEST, ensure_ascii=False), encoding="utf-8")


def main(open_browser=True, today=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    today = today or dt.datetime.now(KST).date()
    fred, sec = collect(today)
    state = load_state()
    cards = build_cards(fred, sec, today, state)
    save_state(state)
    log_rows = append_log(today, cards)
    SITE_DIR.mkdir(exist_ok=True)
    generated = dt.datetime.now(KST).strftime("%Y.%m.%d %H:%M (한국시간)")
    OUT_HTML.write_text(build_html(cards, log_rows, generated), encoding="utf-8")
    write_icons()

    print("\n3) 결과")
    for c in cards:
        mark = {"verified": "✓검증", "single": "△1출처", "held": "⏸보류", "error": "✕오류"}[c["verify"]]
        val = f"{c.get('value_text', '')}{c.get('unit', '')}"
        print(f"   [{mark}] [{STATUS[c['status']]}] {c['title']}: {val}")
        for k in c.get("checks", []):
            flag = "통과" if k["ok"] else ("확인불가" if k["ok"] is None else "실패")
            print(f"           - {k['name']} {flag}: {k['detail']}")
    print(f"\n대시보드: {OUT_HTML}\n기록: {LOG_CSV}")
    if open_browser:
        webbrowser.open(OUT_HTML.as_uri())


if __name__ == "__main__":
    # GitHub Actions 같은 서버에서는 --no-browser 로 실행 (브라우저 열기 생략)
    main(open_browser="--no-browser" not in sys.argv)
