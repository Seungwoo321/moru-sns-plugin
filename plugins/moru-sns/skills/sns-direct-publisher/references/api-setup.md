# Threads·Instagram Reels·X 공식 API 연결

Threads·Instagram 확인일: 2026-11-22. X 안내는 2026-11-21 확인 내용을 유지한다. 연결할 때 공식 문서와 앱 콘솔의 최신 조건을 다시 확인하라.

## 목차

- 현재 지원 범위와 실행 환경
- X 연결 순서 / 토큰과 비용
- Threads 연결 순서 / 토큰 유지 / 후속 댓글
- Instagram 로그인 모드와 Reels
- AI 표시 적용
- 연결 완료 확인 / 공식 자료

## 현재 지원 범위와 실행 환경

- `scripts/publisher.py`는 Threads·X 텍스트/영상 원글, 저장된 자기 Threads 원글의 후속 댓글, Instagram Reels의 plan/check/publish/metrics를 지원한다. 사용자가 지정한 플랫폼과 우선순위에 따라 승인된 연결만 준비하라. 계정 생성, OAuth 승인 창·callback 처리, 토큰 교환은 구현되어 있지 않다. 별도 `threads_refresh.py`만 조건을 확인한 Threads 장기 토큰 갱신을 지원하며 스케줄러는 만들지 않는다. 실제 운영 실행은 현재 사용자 요청 또는 승인된 정책이 지정한 운영자의 검증된 실행 환경에서 수행한다.
- 아래 인증 절차는 사용자가 소유한 앱과 별도 인증 구현에서 수행하라. 발급받은 사용자 토큰만 스크립트 실행 환경에 주입하라.
- 발급·저장이 승인된 토큰은 SKILL.md에서 연결한 `references/private-runtime.md`의 숨김 입력 helper로 최초 저장·환경변수 주입을 준비할 수 있다. 이 helper는 OAuth·발급·갱신을 수행하지 않으며 계정 일치 확인과 실제 실행 환경의 장기 보존을 구분한다.
- 토큰·앱 secret·refresh token·계정 ID·발행 기록은 스킬 밖의 비공개 실행 디렉터리나 secret manager에 보관하라. 스킬·소스 저장소·대화·로그에 토큰을 기록하지 마라.
- 프로필에는 AI 에이전트 운영임을 설명하고 사용자가 정한 운영자 계정만 연결하라. 이 연결을 실명·직장·연락처·비공개 저장소의 공개 승인으로 확대하지 마라.

| 플랫폼 | 사용자 토큰 환경변수 | 발행 대상 ID 환경변수 |
| --- | --- | --- |
| X | `X_USER_ACCESS_TOKEN` | `X_EXPECTED_USER_ID` |
| Threads | `THREADS_USER_ACCESS_TOKEN` | `THREADS_EXPECTED_USER_ID` |
| Instagram Login / Unified Login | `INSTAGRAM_USER_ACCESS_TOKEN` | `INSTAGRAM_EXPECTED_USER_ID` |

오프라인 `plan`은 예상 계정 ID가 비어 있어도 텍스트·로컬 영상 검토를 수행하고 `account_id: null`, `readiness.status: NOT_READY`와 `account_unconfigured`를 반환한다. URL 방식에서 로컬 Meta 영상의 공개 URL 누락은 `video_url_missing`으로 분리한다. 이때 ID·URL을 임의로 만들지 마라. 제공했지만 잘못된 ID·URL은 계속 오류다. `publish_ready`는 항상 false이며 plan의 종료 코드 0은 로컬 검토가 끝났다는 뜻이다. `check`·`publish`·`metrics`의 숫자 예상 ID와 토큰은 여전히 필수다. Threads와 Instagram 기본 URL 방식의 실제 영상 publish에는 공개 HTTPS URL이 필요하고, Instagram Unified의 명시적 resumable 로컬 업로드만 그 URL을 요구하지 않는다.

발행 대상 계정을 먼저 확인하고 그 계정의 숫자 ID를 설정하라. Instagram Login은 `/me`의 `user_id`, Unified Login은 `/me/assigned_instagram_accounts`의 `id`를 대조하라. Unified의 `/me`는 비즈니스 통합 시스템 사용자이므로 Instagram 계정 ID로 사용하지 마라. 표시 이름이나 운영자 계정의 이름으로 대체하지 마라.

## X 연결 순서

1. X Developer Console에서 개발자 약관·이용 목적을 제출하고 앱을 생성하라. OAuth 2.0을 켜고 서버용 `Automated App / Bot`을 선택하라. 이 유형은 Client ID와 Client Secret을 사용하는 confidential client이다. [1]
2. callback URI를 앱 allowlist에 등록하라. 로컬 개발은 `http://127.0.0.1:포트/경로`, 운영은 HTTPS를 사용하고 경로·포트·끝 슬래시까지 일치시켜라. `localhost`로 바꾸지 마라. [1]
3. 사용자 인증은 Authorization Code + PKCE로 구현하라. `https://x.com/i/oauth2/authorize`에 `response_type=code`, `client_id`, `redirect_uri`, scopes, 무작위 `state`, PKCE challenge를 전달하고 callback의 `state`를 검증하라. [2]
4. 원글 발행·계정 확인·본인 글 조회에는 `tweet.read tweet.write users.read`를 요청하라. 영상 업로드에는 `media.write`, 지속 연결에는 `offline.access`도 요청하라. 좋아요·DM·팔로우 권한은 이 작업에 추가하지 마라. [2][7]
5. 인증 코드는 `POST https://api.x.com/2/oauth2/token`으로 교환하라. form-urlencoded 본문에 `grant_type=authorization_code`, `code`, `redirect_uri`, `code_verifier`를 넣고 confidential client는 Client ID·Secret으로 HTTP Basic 인증하라. [2]
6. 발행할 봇 계정으로 승인했는지 `GET https://api.x.com/2/users/me`로 확인하고, 반환 ID를 `X_EXPECTED_USER_ID`와 대조하라. 운영자 계정과 봇 계정은 서로 다를 수 있다.
7. 원글은 사용자 Bearer 토큰으로 `POST https://api.x.com/2/tweets`에 `{"text":"…"}`를 보내라. 이 스킬에서 답글·인용·리포스트로 확장하지 마라. 성공한 게시물 ID를 보관하라.
8. 봇 계정 설정의 **Your account → Automation**에서 사용자가 정한 관리 계정을 연결하고 자동화 계정 표시를 확인하라. [1]

### X 토큰과 비용

- access token은 기본 2시간이다. `offline.access`는 refresh token을 발급하며 access token을 영구 토큰으로 만들지 않는다. 응답의 `expires_in`으로 만료를 관리하라. [2]
- 갱신은 같은 token endpoint에 `grant_type=refresh_token`, `refresh_token`과 confidential client 인증을 보내라. 새로 반환된 토큰·만료 정보를 비공개 저장소와 실행 환경에 반영하라. 현재 스크립트는 이를 자동 실행하지 않는다. [2]
- 선불 credit을 사용하며 구독·월 최소 사용액은 없다. 자동충전과 사용 한도는 사용자가 정한 예산으로 설정하라. 최초 구매 보너스는 계정별 조건을 확인하고 기본 예산에 포함하지 마라. [3]
- 공개 가격표: 일반 원글 `$0.015/요청`, URL 포함 원글 `$0.200/요청`, 일반 게시물 읽기 `$0.005/객체`, 사용자 읽기 `$0.010/객체`이다. `me` 계정 확인도 무조건 무료라고 가정하지 마라. [3]
- Owned Reads는 지정된 본인 timeline·mentions 등의 경로에서 **인증 사용자 = 개발 앱 소유자**일 때 `$0.001/객체`이다. 다른 봇 계정을 연결했다고 자동 적용되지 않는다. [3]
- 읽기는 HTTP 호출 수가 아니라 반환 객체 수로 계산한다. 같은 UTC 날짜의 동일 객체는 통상 중복 과금하지 않으며, 다음날 재조회하면 다시 과금될 수 있다. 가격표의 중복 제거는 절대 보장이 아니다. [3]

## Threads 연결 순서

1. Meta 개발자 앱을 만들 때 **Access the Threads API** use case를 선택하라. 일반 Meta App ID·Secret과 **Threads App ID·Secret**을 혼동하지 마라. [4]
2. 앱의 **App roles → Roles → Add People → Threads Tester**로 발행할 Threads 프로필을 초대하라. 해당 프로필로 로그인한 뒤 **Account Settings → Website permissions**에서 초대를 수락하라. [4]
3. 최소 권한 `threads_basic`, `threads_content_publish`를 추가하라. 성과 조회에는 `threads_manage_insights`, 자기 원글 후속 댓글에는 `threads_manage_replies`를 추가하라. 자기 원글 작성자 경로를 사용하므로 타인 글 검색·멘션 권한을 추가하지 마라. [4][8]
4. 실제 운영할 본인 봇 프로필을 Threads Tester 역할로 연결하라. 이 역할을 가짜 테스트 사용자나 비공개 모의 게시 환경과 혼동하지 마라. Advanced Access 승인을 받지 않은 앱도 이 역할의 프로필을 대신해 게시할 수 있다. 역할 없는 외부 사용자를 연결하려면 해당 권한의 App Review와 앱 공개가 필요하다. 본인용 연결을 제3자 서비스 운영 권한으로 해석하지 마라. 첫 실제 게시 뒤 외부 가시성을 확인하라. [4][6]
5. Threads 설정에 OAuth redirect URI를 등록하고 Authorization Window에서 해당 프로필의 동의를 받아라. 인증 코드를 짧은 수명 토큰으로 교환하는 절차는 [4]의 **Get access tokens** 링크를 따르라. callback URI는 등록값과 정확히 일치시키고 `state`를 검증하라.
6. `GET https://graph.threads.com/v1.0/me?fields=id,username`으로 프로필을 확인하고 반환 ID를 `THREADS_EXPECTED_USER_ID`와 대조하라. X ID나 일반 Meta 사용자 ID를 재사용하지 마라.
7. 텍스트 원글은 `POST /v1.0/{id}/threads`에 `media_type=TEXT`, `text`로 컨테이너를 만든 뒤 `POST /v1.0/{id}/threads_publish`에 `creation_id`를 전달하라. API 호스트는 `https://graph.threads.com`이다. 처리 상태를 확인하고 최종 게시물 ID를 기록하라. [6]

본인 공개 Threads 계정을 연결할 때 앱의 Threads 설정 화면에 **사용자 토큰 생성기**가 제공되면, 수락된 Threads Tester를 대상으로 하는 장기 토큰 발급 경로를 사용할 수 있다. 현재 콘솔의 지원 조건과 실제 동의 화면을 확인하라. 사용자 토큰은 채팅이나 공개 로그에 복사하지 말고 지정 발행 운영자의 실제 런타임 비공개 설정으로 전달하라. 이 경로로 토큰을 발급했다고 공개 OAuth 앱의 callback·개인정보 정책·검수 요건까지 완료한 것으로 보고하지 말라.

영상은 `media_type=VIDEO`와 공개 HTTPS `video_url`로 컨테이너를 만들고 상태가 FINISHED인지 확인한 뒤 발행하라. 로그인 필요한 Slack 링크는 사용할 수 없다. [6]

### Threads 토큰 유지

- 짧은 수명 토큰은 1시간이다. 만료 전에 서버에서 `GET https://graph.threads.com/access_token`에 `grant_type=th_exchange_token`, Threads `client_secret`, 짧은 수명 `access_token`으로 교환하라. 긴 수명 토큰은 60일이다. [5]
- 긴 수명 토큰은 발급·갱신 후 24시간 이상 지났고 아직 만료되지 않았을 때 `GET https://graph.threads.com/refresh_access_token`과 `grant_type=th_refresh_token`, `access_token`으로 갱신할 수 있다. 갱신 후 다시 60일이며 만료된 토큰은 갱신할 수 없다. [5]
- 권한 부여의 90일 수명은 토큰 수명과 별개다. 공개 프로필은 토큰 갱신으로 권한 기간도 연장되지만 비공개 프로필은 만료 시 다시 동의해야 한다. [5]
- 승인된 토큰 관리는 `references/private-runtime.md`의 `threads_refresh.py`를 사용하라. 공식 메타데이터와 24시간·잔여 7일 조건을 확인하고 새 토큰의 계정을 대조한 뒤 비공개 설정만 원자 교체한다. 7일은 도구의 운영 기준이다. 오류 뒤 pending 표식을 우회해 재시도하지 마라. 갱신 스케줄러나 OAuth 연결 마법사는 없다.

## 연결 완료 확인

요청된 플랫폼의 `me` 확인과 예상 ID 일치, 필요한 권한·토큰 유효기간·예산을 확인한 뒤 지정 운영자가 승인 범위의 정상 콘텐츠를 발행하도록 연결하라. 시뮬레이션 요청 중에는 공개 게시나 컨테이너 생성도 하지 말라. 토큰 발급이나 컨테이너 생성만으로 게시 완료라고 보고하지 마라.

Meta 앱의 공개 전환까지 요청되면 앱 대시보드의 게시 요건을 확인하라. 개인정보처리방침 URL 등 필요한 실제 운영 항목을 완성하고, 실제 게시 상태를 확인한 뒤 완료를 보고하라. 임시 URL이나 사실과 다른 데이터 처리 설명으로 필수 항목을 채우지 마라. 앱 공개, 권한의 Advanced Access 승인, 개별 프로필 OAuth 연결은 서로 다른 상태다.

## Threads 후속 댓글

`POST /v1.0/{id}/threads`의 `reply_to_id`와 `threads_manage_replies`를 사용하라. 이 도구는 임의 게시물 ID를 받지 않으며 `--reply-to-job-id`로 같은 Threads 계정의 성공한 원글 기록만 선택한다. 원글이 published이고 post_id가 있으며 그 자체가 후속 댓글이 아니어야 한다. 발행 직전 `GET /{post_id}?fields=id,owner`로 소유자를 재확인한다. 별도 작업 ID·부모 게시물 ID·콘텐츠 해시를 묶어 중복/결과 불명 재시도를 차단한다. 다른 사람이 원글 아래에 단 댓글에도 답하지 마라. [8]

## Instagram 로그인 모드와 Reels

`--platform instagram --instagram-login-mode unified|instagram`으로 실제 앱의 로그인 구성을 명시하라. 모드 생략 시 실행하지 않으며 토큰에서 호스트를 추정하지 않는다. 이 도구는 기존 Facebook Login 경로를 별도로 지원하지 않는다. [10][16][17]

| 모드 | 고정 API 호스트 | 토큰 | 계정 확인 | 콘텐츠/지표 권한 |
| --- | --- | --- | --- | --- |
| `unified` | `https://graph.facebook.com/v27.0` | Business Integration System User (콘솔 SUAT) | `/me/assigned_instagram_accounts?fields=id,username` | `instagram_basic`, `instagram_content_publish`, 지표에 `instagram_manage_insights` |
| `instagram` | `https://graph.instagram.com/v27.0` | Instagram User | `/me?fields=user_id,username` | `instagram_business_basic`, `instagram_business_content_publish`, 지표에 `instagram_business_manage_insights` |

Unified Login 설정은 비즈니스 포트폴리오·Facebook Login for Business 구성과 `config_id`를 사용한다. 실제 구성의 필수 권한과 승인 화면을 확인하라. 이 도구는 OAuth·포트폴리오 생성·토큰 발급/갱신을 실행하지 않는다. 발급된 토큰은 환경변수 `INSTAGRAM_USER_ACCESS_TOKEN`에 비공개로 주입하고 토큰 종류와 모드를 함께 관리하라. [16][17]

Unified의 토큰 하나에는 여러 Instagram 계정이 연결될 수 있다. 스크립트는 반환 목록에서 **설정된 예상 ID가 정확히 일치하는 계정**만 사용한다. 다음 페이지는 같은 고정 endpoint에 응답의 `paging.cursors.after`만 전달하고 임의 `next` URL을 따라가지 않는다. 반복/비정상 cursor 또는 최대 10페이지 안에 예상 ID를 확인하지 못하면 중단한다. 첫 계정이나 `/me` ID로 자동 대체하지 마라. [16]

1. Business 또는 Creator 전문 계정과 실제 앱의 로그인 설정을 준비하라. 계정 OAuth, 토큰 비밀 저장, 만료·갱신 운영은 별도 설정이며 스킬 수정만으로 완료되지 않는다. [9][16]
2. 선택한 모드의 콘텐츠/지표 권한을 사용하라. 이 도구는 댓글/DM을 실행하지 않는다. [10][11]
3. 위 표의 모드별 조회 결과를 `INSTAGRAM_EXPECTED_USER_ID`와 비교하라. Threads 토큰·ID를 재사용하지 마라. [9][16]
4. 기본 경로는 선택한 고정 호스트의 `POST /{instagram_account_id}/media`에 `media_type=REELS`, `caption`, 공개 HTTPS `video_url`, `is_ai_generated`로 컨테이너를 생성한다. 같은 최종 MP4는 Threads와 URL을 공유할 수 있다. Unified의 명시적 `--instagram-upload-mode resumable`은 `video_url` 대신 `upload_type=resumable`로 컨테이너를 만든 뒤 로컬 바이트를 고정 공식 `rupload.facebook.com/ig-api-upload/v27.0/{container_id}`에 전송한다. Instagram Login에는 이 업로드 모드를 적용하지 않는다. [10][12][13]
5. `GET /{container_id}?fields=status_code`가 FINISHED일 때만 `POST /{user_id}/media_publish`에 `creation_id`를 보내라. 스크립트는 처리 대기·24시간 만료·최대 5회 상태 확인과 불명확한 publish 응답의 재제출 차단을 적용한다. [10]
6. 반환된 게시물 ID를 저장하라. metrics는 소유자 확인 후 `GET /{media_id}/insights?metric=views,reach,likes,comments,saved,shares`를 조회한다. 빈 데이터는 알 수 없음이며 0이 아니다. 지표는 최대 48시간 지연될 수 있다. [11][14]

명령은 `--platform instagram --instagram-login-mode unified` 또는 `--instagram-login-mode instagram`과 로컬 `--video-file`, 공개 `--video-url`을 함께 사용하라. Unified에서만 `--instagram-upload-mode resumable`과 로컬 파일을 선택해 URL 없이 업로드할 수 있으며 두 입력 방식을 섞지 마라. 직접 업로드도 컨테이너·계정·파일·전송 모드가 같은 작업 기록을 유지하고, 결과 불명 업로드를 자동 재전송하거나 URL 방식으로 바꾸어 우회하지 마라. 공식 프로토콜 이름이 resumable이어도 이 CLI가 중단 지점부터 바이트를 재전송한다고 설명하지 마라.

Instagram은 이 도구에서 Reels만 지원한다. 운영 프로필은 30~150초 MP4/H.264, 선택적 AAC이고 Instagram 파일은 300MB 이하로 제한한다. 플랫폼의 모든 코덱·비트레이트·moov/GOP 조건을 ffprobe 검사가 검증하는 것은 아니다. OAuth 앱 검수·토큰 저장·선택한 영상 전송 경로·첫 실게시·스케줄러는 각각 별도 완료 항목이다.

## AI 표시 적용

- Threads 콘텐츠 라벨 필드는 `is_made_with_ai`이며 텍스트에도 지원한다. 이 발행기는 AI가 작성한 Threads 텍스트에 자발적 투명성 표시로 true를 보낸다. 해당 동작을 사용자에게 설명하고 실제 콘텐츠와 승인된 운영 방침에 맞는지 확인한다. 모든 AI 텍스트에 본문 문구 또는 라벨이 정책상 필수라고 말하지 마라. [6][15]
- Instagram의 `is_ai_generated`는 2026-06-22부터 Content Publishing API에 지원되며 Instagram Login에서도 사용한다. 계정 프로필 라벨과 게시물 라벨을 혼동하지 마라. [13]
- Meta 도움말은 실제 같은 생성·변형 영상과 현실적인 AI 음성의 표시를 요구하는 사례를 제시하며, 현실적인 AI 내레이션 Reel을 포함한다. 실제 화면 녹화에 이런 내레이션을 넣어도 표시 대상이다. 이미지와 비실사 만화 영상은 같은 일괄 의무로 설명하지 마라. [15]
- `--video-kind real-demo|blender-render` 자체는 AI 미디어가 아니다. `ai-stylized|ai-realistic` 영상 또는 `--audio-kind ai-stylized|ai-realistic` 음성은 도구가 AI 라벨을 설정한다. 비실사 AI까지 표시하는 부분은 운영상 자발적 공개다. 생성 이미지 오버레이가 있으면 `--ai-generated-media`를 추가하라. 결과에 영상·음성 종류를 별도로 기록하여 실제 화면을 가짜 영상으로 설명하지 말라.
- Threads 전용 자동화 계정 배지는 공식 문서에서 확인하지 못했다. 프로필에 AI 기록자임을 설명하되 Instagram의 AI-generated profile 토글 적용 여부는 실제 계정의 공식 설명을 확인하라. 비인간 3D 아바타만으로 사람을 사칭하는 AI 프로필이라고 단정하지 마라. [15]

## 공식 자료

1. X 앱 유형·callback·자동화 표시: https://docs.x.com/fundamentals/developer-apps
2. X OAuth 2.0·scopes·토큰 수명과 연결 절차: https://docs.x.com/fundamentals/authentication/oauth-2-0/authorization-code
3. X credit·게시 및 조회 요금: https://docs.x.com/x-api/getting-started/pricing
4. Threads 앱·권한·테스터·인증 시작: https://developers.facebook.com/documentation/threads/get-started
5. Threads 토큰 교환·갱신·만료: https://developers.facebook.com/documentation/threads/get-started/long-lived-tokens
6. Threads 게시 절차·테스터 게시 범위: https://developers.facebook.com/documentation/threads/posts
7. X 영상 업로드 OAuth 권한: https://docs.x.com/x-api/media/append-media-upload
8. Threads 후속 댓글·권한: https://developers.facebook.com/documentation/threads/retrieve-and-manage-replies/create-replies
9. Instagram Login 시작·user_id·v27.0: https://developers.facebook.com/documentation/instagram-platform/instagram-api-with-instagram-login/get-started
10. Instagram 컨테이너·상태·발행: https://developers.facebook.com/documentation/instagram-platform/content-publishing
11. Instagram Login 지표·권한: https://developers.facebook.com/documentation/instagram-platform/insights
12. Reels 필드·규격: https://developers.facebook.com/documentation/instagram-platform/instagram-graph-api/reference/ig-user/media
13. Instagram AI 표시 변경 이력: https://developers.facebook.com/documentation/instagram-platform/changelog
14. Reels 지표: https://developers.facebook.com/documentation/instagram-platform/reference/instagram-media/insights
15. Meta AI 표시 범위: https://www.meta.com/help/artificial-intelligence/1783222608822690/
16. Unified Login 구성·assigned 계정 조회: https://developers.facebook.com/documentation/instagram-platform/instagram-api-with-unified-login/get-started
17. Unified Login 토큰·API 호스트: https://developers.facebook.com/documentation/instagram-platform/instagram-api-with-unified-login/authentication
