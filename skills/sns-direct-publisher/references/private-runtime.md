# 비공개 실행 설정

`private_runtime.py`는 사용자가 이미 발급하고 비공개 저장을 승인한 토큰의 **초기 configure**와 기존 publisher의 **run**만 지원한다. OAuth 승인·토큰 발급·교환·갱신·스케줄러·별도 secret 인프라를 만들지 않는다. 실제 토큰을 대화·모델 출력·명령행 인자·클립보드 출력·로그에 넣지 마라.

## 최초 저장

호출자가 실제 플랫폼·예상 공개 username과 스킬/Git 밖의 절대 설정 경로를 지정하라. 아래 경로와 핸들은 예시이며 실제 승인된 값으로 바꿔라. 토큰을 받지 않는 `--help`로 옵션을 확인할 수 있다.

```bash
python3 scripts/private_runtime.py configure --platform threads --expected-username YOUR_HANDLE --config /private/runtime-config/threads.json
```

- `Access token (hidden):` 프롬프트에만 토큰을 입력하라. TTY가 아니거나 getpass가 입력 숨김을 보장하지 못하면 중단하며 stdin·명령행 token 옵션은 받지 않는다.
- Threads는 고정 공식 `/me?fields=id,username`, X는 `/users/me`, Instagram Login은 `/me?fields=user_id,username`에서 정확한 username을 대조한다. `--expected-user-id`를 지정하면 ID도 대조한다. 불일치·오류에는 설정 파일을 저장하지 않는다.
- Instagram은 `--instagram-login-mode unified|instagram`을 명시하라. Unified는 `--expected-user-id`도 필수이며 고정 `/me/assigned_instagram_accounts?fields=id,username`에서 지정 ID와 username을 모두 확인한다. 최대 10페이지의 검증된 cursor만 사용하고 응답의 next URL이나 첫 계정을 선택하지 않는다.
- 새 부모 디렉터리는 0700, 설정 파일은 0600으로 만든다. 기존 부모도 현재 사용자 소유의 0700이어야 하며 경로의 symlink, Git/스킬/설치 플러그인 내부 경로, 기존 목적 파일을 거절한다. 임시 파일을 fsync한 뒤 완성된 파일을 원자적으로 공개하고 기존 설정은 덮어쓰지 않는다.
- 성공 출력은 공개 계정 확인 결과만 포함한다. 만료는 `unknown`, 실행 환경의 영속성은 미검증이다. 계정 조회 성공은 발행·지표 권한 전체나 정기 운영 준비 완료의 증거가 아니다.

## 기존 발행기 실행

```bash
python3 scripts/private_runtime.py run check --config /private/runtime-config/threads.json --state-dir /private/sns-state
python3 scripts/private_runtime.py run plan --config /private/runtime-config/threads.json --state-dir /private/sns-state --job-id story-001 --text-file /private/content/post.txt
```

`run`은 저장된 플랫폼·예상 계정 ID·Instagram 모드를 고정하고 토큰은 자식 프로세스 환경변수에만 넣는다. 기존 `publisher.py`를 shell 없이 호출하며 결과 출력에서 토큰 문자열과 URL 인코딩 값을 가린다. check/metrics는 실제 조회이고 publish는 실제 공개 동작이다. publish에는 여전히 `--execute`와 기존 계정·미디어·상태·공개 범위 조건이 필요하다. 기존 plan/check/publish/metrics 및 영상·후속 옵션을 그대로 사용하되 플랫폼이나 인증 값을 run 인자로 바꾸지 마라.

설정이 아직 없으면 기존 `publisher.py plan`의 무인증 로컬 검토를 사용하라. 이 helper를 쓰기 위해 가짜 토큰이나 가짜 계정 ID를 저장하지 마라. `--state-dir`은 기존의 동일한 영속 중복 상태 경로를 유지한다. publish/metrics는 직접 CLI와 wrapper 모두 기존 runtime marker·원장·계정 결합을 먼저 검사한다. 원장이나 marker가 없으면 네트워크 전에 중단하고 새 파일을 만들지 않는다. 첫 원장은 아래의 명시적 초기화 절차로만 만든다.

## Threads 토큰 상태 조회

```bash
python3 scripts/token_status.py --config /private/runtime-config/threads.json
python3 scripts/token_status.py --config /private/runtime-config/threads.json --previous-config /private/runtime-config/threads-previous.json
```

`token_status.py`는 같은 private config 검사를 거쳐 고정 공식 `https://graph.threads.com/v1.0/debug_token`을 GET 조회한다. 현재 config의 토큰이 `access_token`, 검사 대상 토큰이 `input_token`이며, `--previous-config`는 같은 계정의 이전 설정을 추가 검사한다. 현재 토큰은 해당 앱의 Threads tester 사용자 토큰이어야 하고, 이전 토큰도 같은 앱에서 발급되어야 한다. 이 도구는 앱 토큰 입력이나 다른 플랫폼을 지원하지 않는다.

출력은 current/previous별 `is_valid`, `app_id`, `user_id`, `expires_at`, `data_access_expires_at`, `scopes`만 포함하고 없는 필드는 null이다. `app_id`는 응답에 있을 때만 표시하며 현재 공식 필드 표에는 명시되지 않는다. 만료 숫자는 원본 Unix timestamp로 유지하며 0이나 null을 무기한 유효로 해석하지 마라. 전체 응답·URL·토큰·원격 오류 내용은 출력하지 않는다. 조회 실패는 무효 판정이 아니며 `is_valid: false`만 해당 조회 시점의 API 무효 응답이다. 이 조회는 토큰 폐기·교체·갱신을 실행하지 않고 설정/중복 상태 파일을 쓰지 않는다. 현재 토큰 자체가 만료되었거나 tester 권한이 없으면 진단도 실패할 수 있다.

근거: [Meta Threads — Debug access token](https://developers.facebook.com/documentation/threads/troubleshooting/debug-access-token), 2026-11-22 확인. 공식 문서는 같은 앱의 앱 토큰 또는 Threads tester 사용자 토큰으로 호출할 수 있다고 설명한다.

## Threads 장기 토큰 갱신

```bash
python3 scripts/threads_refresh.py --config /private/runtime-config/threads.json
python3 scripts/threads_refresh.py --config /private/runtime-config/threads.json --execute
```

첫 명령은 공식 메타데이터를 조회해 자격만 판단한다. 두 번째는 현재 승인된 토큰 관리 범위에서 조건이 맞을 때만 갱신한다. 새 스케줄을 만들거나 갱신을 모든 발행 전에 강제하지 마라. 기존 운영 작업이 승인된 간격으로 호출할 수 있다.

- Threads USER 토큰의 `is_valid`, 정확한 user_id, `issued_at`, `expires_at`, `threads_basic`을 공식 debug 응답으로 확인한다. 발급/이전 검증 갱신 중 늦은 시점부터 24시간 이상 지났고 아직 유효하며 남은 만료가 7일 이하일 때만 실행한다. 발급부터 만료까지 24시간 이하인 토큰, 시각·권한 근거 누락, 만료 또는 데이터 접근 만료는 차단한다. 7일은 이 도구의 운영 기준이며 Meta 필수 기한이 아니다.
- 실제 갱신은 redirect를 거부하는 고정 HTTPS `graph.threads.com/refresh_access_token` GET에 `grant_type=th_refresh_token`과 현재 토큰만 전달한다. 새 토큰은 공식 `/me`의 username과 ID를 모두 대조한 뒤 저장한다. `token_status.py`의 기본 출력 필드는 늘리지 않으며 issued_at은 갱신 판단 내부에서만 추가로 읽는다.
- 설정마다 사용자 소유 0600 잠금 파일과 비차단 배타 잠금을 쓴다. 완전한 새 설정을 0600 임시 파일에 fsync하고 원본이 그대로임을 재확인한 뒤 원자 교체한다. 기존 부모 0700·파일 0600·symlink 거부를 유지한다. 발행 중인 프로세스는 기존 또는 새 완전한 설정을 읽으며, 이미 시작한 발행을 다시 실행하지 마라.
- 호환성을 위해 최상위 `expiry_status: unknown`, `expires_at: null`은 유지한다. 검증된 갱신 시각과 응답의 expires_in에서 보수적으로 계산한 만료는 별도 `threads_refresh`에 저장한다. 이후 자격 판정은 이 저장값만 믿지 않고 공식 메타데이터를 다시 조회한다. 갱신은 이전 토큰의 폐기를 보장하지 않는다.
- 네트워크 결과 불명·계정 불일치·교체 실패에는 토큰을 출력하지 않고 pending 표식을 유지해 같은 토큰의 자동 재시도를 막는다. 상태가 미해결이면 token_status로 현재 유효성을 조사하고 승인된 재연결 절차로 해결하라. 실행 중 잠금 파일을 삭제하거나 표식을 지워 우회하지 마라. 프로필이 비공개이면 토큰 갱신이 데이터 접근 권한 만료를 연장하지 않을 수 있다.

근거: [Meta Threads — Long-lived access tokens](https://developers.facebook.com/documentation/threads/get-started/long-lived-tokens), 2026-11-22 확인. 공식 갱신은 유효한 장기 토큰·24시간 경과·threads_basic을 요구하며 갱신 후 수명은 60일이다. Instagram Unified의 갱신은 이 도구에 포함하지 않는다.

## 정기 실행 전 상태 가드

`runtime_guard.py`는 `--threads-config`, `--instagram-config`, `--x-config` 중 최소 하나를 받는다. 단일 플랫폼도 초기화·반복 검사를 지원하며 다른 플랫폼의 인증을 요구하지 않는다. 계정·경로·runtime ID는 현재 운영 설정의 비밀이 아닌 인자로 전달한다. 아래는 Threads 단독 예시다. 여러 플랫폼을 처음부터 같은 원장에 연결할 경우 해당 config 옵션들을 함께 넣는다.

```bash
# 최초 한 번: state 디렉터리 자체가 아직 없어야 함
python3 scripts/runtime_guard.py --threads-config /private/runtime-config/threads.json --state-dir /private/runtime-config/publisher-state-v1 --runtime-id operator-runtime-v1 --initialize
# 이후 매번: 같은 인자와 경로, initialize 생략
python3 scripts/runtime_guard.py --threads-config /private/runtime-config/threads.json --state-dir /private/runtime-config/publisher-state-v1 --runtime-id operator-runtime-v1
```

- 초기화는 현재 사용자 소유 0700 부모 아래 **완전히 새 디렉터리**에만 허용한다. 빈 디렉터리도 재사용하지 않는다. 모든 원래 경로의 symlink·Git/스킬/설치 플러그인 내부 경로를 거절하며 state는 0700, marker·DB는 현재 사용자 소유의 단일 링크 일반 파일 0600이어야 한다.
- 초기화 전에 설정의 예상 계정 ID·username을 공식 API로 확인한다. Threads/Instagram은 본인 최신 게시물도 조회하고, X는 계정 확인만 하며 게시물 목록은 미조회로 표시한다. Instagram은 ffprobe를 필수 확인하고 Threads/X 영상은 `--require-video`를 지정한다. 텍스트 전용 운영에는 ffprobe를 요구하지 않는다. 그 뒤 새 SQLite와 runtime marker를 fsync한다. marker는 runtime ID, 계정·로그인 모드, DB의 device/inode를 묶는다. 도중 실패로 남은 디렉터리는 다음 실행이 자동 수리·초기화하지 않는다.
- 반복 점검은 기존 marker·DB를 읽기 전용으로 검사한다. SQLite quick_check·필수 schema, 계정 binding과 DB 파일 교체 여부를 확인하고 누락·불일치에는 중단한다. 이 publisher가 쓰는 DELETE journal 방식만 허용하고 WAL은 연결 전에 거절하여 점검 중 보조 상태 파일 생성을 피한다. 상태가 없어졌다고 initialize를 다시 넣거나 새 경로로 바꾸어 중복 방지를 우회하지 마라.
- 출력은 공개 계정 식별자와 검증된 형식의 최근 게시물 ID·permalink·timestamp, 상태 검사 결과다. Threads/Instagram 최근 목록은 각각 첫 페이지 최대 25개이며 전체 이력·반응 지표가 아니다. X의 목록은 null이다. 토큰·전체 API 응답·인증 URL·원격 오류는 출력하지 않는다. 조회된 UI 게시물을 API 발행 영수증으로 합성하지 마라.
- `status: checked`는 현재 검사 성공이다. PC 불변성·미래 접근·스케줄러·동시 실행 방지·발행/지표 권한 전체·이전 토큰 회수를 보장하지 않는다. 실제 계획·게시·지표는 기존 CLI의 별도 검증을 거치며 가드 실패 시 실행하지 않는다. 복구나 DB 이동은 기존 이력을 대조하는 별도 작업으로 처리하라.

## 보존 한계

설정은 OS 사용자 권한으로 보호되는 로컬 파일이며 암호화된 원격 비밀 저장소가 아니다. 이 작업 공간이 장기 보존되거나 다른 정기 실행 환경에서 보인다고 보장할 수 없다. 실제 실행 환경에서 파일·인증·공통 상태 경로의 접근과 보존을 확인하기 전에는 자동 운영 인계 완료로 보고하지 마라. 이미 만료된 토큰은 이 도구로 갱신하지 않는다. 다른 자격 증명으로 대체하지 말고 원래 승인된 연결 절차에서 별도로 해결하라.
