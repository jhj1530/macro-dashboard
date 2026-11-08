# 거시경제 체크 대시보드

매일 아침(평일 한국시간 07:30) 미국 거시경제 지표 6개를 자동으로 받아,
**검증을 통과한 숫자만** 휴대폰용 웹페이지로 보여줍니다.

- 페이지 주소: `https://jhj1530.github.io/macro-dashboard/`
- 지표: 미국 10년물 국채 금리 · 장단기 금리차 · 기준금리 · 삼의 법칙 · 물가상승률 · 원/달러 환율
- 검증: 최신성 → 범위 → 급변 → 원출처 교차검증(미 재무부, 뉴욕 연준, 미 노동통계국, 유럽중앙은행)
  - ✓ 검증됨 / △ 출처 1곳(원출처를 못 받음) / ⏸ 보류(검증 실패 → 마지막 검증값 표시)

## 어떻게 돌아가나

| 부분 | 역할 |
|---|---|
| `macro_dashboard.py` | 데이터 받기 → 검증 → `site/index.html`, `data/` 생성 (파이썬 기본 기능만 사용) |
| `.github/workflows/update.yml` | 매일 자동 실행, `data/` 커밋, `site/`를 GitHub Pages로 배포 |
| `data/macro_log.csv` | 날짜별 검증된 숫자 기록 |
| `data/last_verified.json` | 지표별 마지막 검증값 (검증 실패 시 이 값을 보여줌) |

## 사용법

- **바로 업데이트하고 싶을 때**: Actions 탭 → "매일 거시경제 업데이트" → `Run workflow`
- **내 컴퓨터에서 실행**: `python macro_dashboard.py` (브라우저가 열림)
- **서버용 실행**: `python macro_dashboard.py --no-browser`
- **FRED API 키**: GitHub Actions 서버에서는 FRED 키 없이 접속하면 응답이 없어서, 무료 API 키를 저장소 Secret `FRED_API_KEY` 로 넣어 둡니다.

## 주의

- 판단을 돕는 참고 자료이지 매수/매도 신호가 아닙니다.
- 공개 저장소입니다. **개인 자산 정보는 절대 올리지 마세요.**
