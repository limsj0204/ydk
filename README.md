# 양도끼 조회수 추이

YouTube 채널 [@양도끼](https://www.youtube.com/@양도끼)의 영상별 조회수를 주기적으로 수집해서 웹페이지로 보여줍니다.

- **수집**: GitHub Actions가 매시간 `collector/collect.py`를 실행합니다.
  - 업로드 후 7일 이내 영상은 1시간마다 수집합니다.
  - 그 이후 영상은 12시간마다 수집합니다.
- **저장**: 영상마다 `docs/data/snapshots/<영상ID>.csv`에 한 줄씩 쌓입니다. 영상 목록과 요약은 `docs/data/videos.json`에 들어갑니다.
- **대시보드**: GitHub Pages가 `docs/index.html`을 서비스합니다.

## 처음 설정하기

1. **YouTube API 키 발급**
   1. [Google Cloud Console](https://console.cloud.google.com/)에서 프로젝트를 만듭니다.
   2. "API 및 서비스 → 라이브러리"에서 **YouTube Data API v3**를 사용 설정합니다.
   3. "사용자 인증 정보 → 사용자 인증 정보 만들기 → API 키"로 키를 발급합니다. 키 제한은 "YouTube Data API v3"만 허용하도록 걸어두는 것을 권장합니다.
2. **키 등록**: 이 저장소의 Settings → Secrets and variables → Actions → New repository secret으로 이름 `YOUTUBE_API_KEY`, 값에 발급한 키를 넣습니다.
3. **기본 브랜치(`main`)에 코드 반영**: 예약 실행(cron)은 기본 브랜치에서만 동작합니다.
4. **첫 수집 실행**: Actions 탭 → "Collect view counts" → Run workflow를 누릅니다. 이후에는 매시간 자동으로 돕니다.
5. **웹페이지 공개**: Settings → Pages → Build and deployment에서 Source를 "Deploy from a branch", 브랜치 `main`, 폴더 `/docs`로 설정합니다. 잠시 뒤 `https://limsj0204.github.io/ydk/`에서 볼 수 있습니다.

## 설정 바꾸기 (`config.json`)

| 항목 | 기본값 | 의미 |
|---|---|---|
| `channel_handle` | `@양도끼` | 추적할 채널 핸들 |
| `early_days` | 7 | 집중 수집 기간 (업로드 후 일수) |
| `early_interval_hours` | 1 | 집중 수집 기간의 수집 간격 (시간) |
| `late_interval_hours` | 12 | 그 이후의 수집 간격 (시간) |
| `full_scan_interval_hours` | 24 | 전체 영상 목록을 다시 훑는 간격 |
| `shorts_max_seconds` | 180 | 쇼츠 목록을 못 받아올 때 쇼츠로 볼 최대 길이 |

워크플로는 매시간 돌기 때문에 수집 간격은 1시간 단위로만 줄일 수 있습니다. GitHub의 예약 실행은 몇 분에서 수십 분씩 늦게 시작되기도 합니다.

## 참고

- 공개 API는 현재 조회수만 알려주므로, 과거 추이는 수집을 시작한 시점부터 쌓입니다.
- API 사용량은 하루 무료 한도(10,000 유닛)에 비해 매우 적습니다. 실행 한 번에 보통 5 유닛 안팎이고, 하루 한 번 하는 전체 목록 조회 때는 영상 수에 따라 조금 늘어납니다.
- 테스트: `python -m unittest discover tests`
