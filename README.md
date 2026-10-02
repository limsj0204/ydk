# 양도끼 조회수 추이

YouTube 채널 [@양도끼](https://www.youtube.com/@양도끼)와 [@양도끼얏호](https://www.youtube.com/@양도끼얏호)의 영상별 조회수를 주기적으로 수집해서 웹페이지로 보여줍니다. 웹페이지 위쪽 탭으로 채널을 바꿔 볼 수 있습니다.

- **수집**: 매시 정각에 GitHub Actions가 `collector/collect.py`를 실행합니다 (한국 시간 기준).
  - 업로드 후 7일 이내 영상은 매시 정각에 수집합니다.
  - 그 이후 영상은 매일 00시와 12시에 수집합니다.
  - 정각 실행은 외부 예약 서비스가 GitHub에 실행 요청을 보내는 방식입니다. GitHub 자체 예약은 정각에 몇 분~수십 분씩 늦기 때문입니다. 매시 30분에 GitHub 자체 예약이 백업으로 한 번 더 돌고, 그 시간대에 이미 수집했으면 바로 끝납니다.
- **저장**: 채널별 폴더 `docs/data/<채널 key>/`에 저장합니다. 영상마다 `snapshots/<영상ID>.csv`에 한 줄씩 쌓이고, 영상 목록과 요약은 `videos.json`에 들어갑니다.
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

## 정각 실행 설정 (cron-job.org)

1. **GitHub 토큰 발급**: GitHub → Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token.
   - Repository access: Only select repositories → `limsj0204/ydk`
   - Permissions → Repository permissions → **Actions: Read and write**
   - 만료일은 원하는 만큼 지정합니다. 만료되면 다시 발급해서 바꿔 넣어야 합니다.
2. **[cron-job.org](https://cron-job.org) 가입 후 Create cronjob**
   - URL: `https://api.github.com/repos/limsj0204/ydk/actions/workflows/collect.yml/dispatches`
   - Execution schedule: Every hour, minute 0
   - Advanced 탭
     - Request method: `POST`
     - Headers: `Authorization: Bearer <발급한 토큰>`, `Accept: application/vnd.github+json`, `X-GitHub-Api-Version: 2022-11-28`
     - Request body: `{"ref":"main"}`
   - 저장 후 "Test run"을 눌러 응답이 `204`면 성공입니다.

## 설정 바꾸기 (`config.json`)

| 항목 | 기본값 | 의미 |
|---|---|---|
| `channels` | 양도끼, 양도끼얏호 | 추적할 채널 목록. 각 채널은 `key`(데이터 폴더 이름, 영문)와 `channel_handle`을 갖습니다. 아래 항목을 채널 안에 적으면 그 채널만 다르게 설정됩니다 |
| `early_days` | 7 | 집중 수집 기간 (업로드 후 일수) |
| `early_interval_hours` | 1 | 집중 수집 기간의 수집 간격 (시간) |
| `late_interval_hours` | 12 | 그 이후의 수집 간격 (시간). 하루를 이 간격으로 나눈 정각에 수집 (12면 00시·12시) |
| `timezone_offset_hours` | 9 | 수집 시각을 맞출 시간대 (한국 = 9) |
| `full_scan_interval_hours` | 24 | 전체 영상 목록을 다시 훑는 간격 (24면 매일 00시) |
| `shorts_max_seconds` | 180 | 쇼츠 목록을 못 받아올 때 쇼츠로 볼 최대 길이 |

수집 간격은 1시간 단위이고, 24의 약수(1, 2, 3, 4, 6, 8, 12, 24)로 두면 매일 같은 시각에 맞춰집니다.

## 참고

- 공개 API는 현재 조회수만 알려주므로, 과거 추이는 수집을 시작한 시점부터 쌓입니다.
- API 사용량은 하루 무료 한도(10,000 유닛)에 비해 매우 적습니다. 실행 한 번에 보통 5 유닛 안팎이고, 하루 한 번 하는 전체 목록 조회 때는 영상 수에 따라 조금 늘어납니다.
- 테스트: `python -m unittest discover tests`
