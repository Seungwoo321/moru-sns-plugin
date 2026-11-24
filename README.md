# Moru SNS

개발 보고를 근거로 콘텐츠를 편집하고, 사용자가 지정한 계정의 공식 API로 게시하는 ChatGPT·Codex 플러그인입니다. 모루는 플러그인 이름이며 특정 봇 계정이나 운영자의 정체성을 지정하지 않습니다. 각 사용자가 계정·원천·언어·공개 범위·게시 정책을 정하고 자신의 공식 API 인증으로 운영합니다.

네 스킬과 Python 발행기를 MIT 라이선스로 제공합니다. 패키지에 SNS 계정·개인 보고·운영 원장·인증정보는 포함하지 않습니다.

## 포함된 스킬과 위치

| 스킬 | 역할 | 저장소 경로 |
| --- | --- | --- |
| `sns-setup` | 웹 Work에서 설치 후 시작·무게시 준비 상태 구분 | [`plugins/moru-sns/skills/sns-setup/SKILL.md`](plugins/moru-sns/skills/sns-setup/SKILL.md) |
| `sns-dev-stories` | 승인된 개발 보고·커밋·미디어에서 소재를 고르고 프로젝트 단계와 공개 범위에 맞게 초안 작성 | [`plugins/moru-sns/skills/sns-dev-stories/SKILL.md`](plugins/moru-sns/skills/sns-dev-stories/SKILL.md) |
| `sns-direct-publisher` | 공식 API 연결 확인, 무게시 검토, 발행, 지표 조회 | [`plugins/moru-sns/skills/sns-direct-publisher/SKILL.md`](plugins/moru-sns/skills/sns-direct-publisher/SKILL.md) |
| `sns-follow-review` | 실제 활동과 교류 근거로 관심 계정 추천·유지·해제 검토 | [`plugins/moru-sns/skills/sns-follow-review/SKILL.md`](plugins/moru-sns/skills/sns-follow-review/SKILL.md) |

각 스킬 폴더에는 필요한 `references/`, UI 메타데이터와 아이콘이 들어 있습니다. 발행기와 오프라인 회귀 검사는 `plugins/moru-sns/skills/sns-direct-publisher/scripts/`에 있습니다. 팔로우 검토 스킬은 실제 팔로우·해제·댓글·DM을 실행하지 않습니다.

## 웹 설치와 시작

개인 ChatGPT 웹에서는 **추가 → 플러그인 압축 파일 업로드**에 [v0.1.13 GitHub 기본 ZIP](https://github.com/Seungwoo321/moru-sns-plugin/archive/refs/tags/v0.1.13.zip)을 사용합니다. v0.1.5부터 릴리스 태그는 `plugins/moru-sns` 하위 트리만 루트로 갖습니다. GitHub가 생성하는 단일 최상위 폴더 바로 아래에 `plugin.json`과 `skills/`가 들어 있습니다. v0.1.4 이전 태그 및 `master` 전체 소스 ZIP은 이 업로드 경로에 사용하지 않습니다.

Workspace GitHub marketplace import와 Codex CLI는 전체 저장소의 `master` 또는 검토한 전체 커밋 SHA를 사용합니다. 플러그인 전용 릴리스 태그에는 marketplace가 없습니다. 기존 GitHub import 경로를 사용할 수 있으면 그 경로를 우선합니다.

설치 후 같은 `moru-sns`를 선택하고 Work에서 “모루 시작 점검”을 요청합니다. [웹 설치 상세](plugins/moru-sns/skills/sns-setup/references/web-install.md)를 참고하세요. ZIP 구조 검증, 실제 웹 설치 성공, 공식 API 실행 연결과 무인 예약 검증은 각각 별개입니다. 별도 ZIP 생성기나 릴리스 첨부물은 제공하지 않습니다.

## Codex CLI 설치

공개 저장소이며 다음 명령은 플러그인 명령을 지원하는 Codex CLI용입니다.

```bash
codex plugin marketplace add Seungwoo321/moru-sns-plugin --ref master
codex plugin add moru-sns@moru-sns
```

로컬 체크아웃을 사용하려면 첫 명령의 저장소 대신 체크아웃의 절대 경로를 지정합니다.

```bash
codex plugin marketplace add /absolute/path/to/moru-sns-plugin
codex plugin add moru-sns@moru-sns
```

설치 후 해당 클라이언트의 스킬 선택에서 필요한 스킬을 선택합니다. 예를 들어 승인된 보고를 첨부하고 `sns-dev-stories`로 소재를 고른 뒤, 최종 콘텐츠에 `sns-direct-publisher`를 적용합니다. 저장소에 묶인 스킬과 기존 개인 스킬은 별도 설치본이므로 업데이트가 자동으로 양방향 동기화되지는 않습니다.

사용자별 운영 설정은 [`examples/operator-policy.example.json`](examples/operator-policy.example.json)을 참고해 저장소 밖에 작성합니다. 원천 채널·프로젝트·공개 허용 범위·발행 언어·시간대·점검/발행 주기·운영자는 사용자가 선택합니다. 이 정책 템플릿은 운영자가 검토하는 입력이며, CLI가 파일을 자동으로 읽어 스케줄을 만들거나 공개 권한을 판정하지는 않습니다.

패키지는 OpenAI가 문서화한 portable `plugin.json`과 `.agents/plugins/marketplace.json` 형식을 사용합니다. 저장소 marketplace 사용 가능 여부는 클라이언트에 따라 다릅니다. 공개 플러그인 디렉터리에 등록된 상태를 뜻하지 않습니다. [공식 패키지 안내](https://developers.openai.com/plugins/build/plugins)

## 실행 준비

발행기는 Python 3.10 이상과 표준 라이브러리를 사용합니다. 영상 규격 검사에는 `ffprobe`가 필요하며 비공개 설정 도구와 상태 가드는 POSIX 환경을 전제로 합니다. 실제 운영 환경에서 플랫폼 권한, 대상 계정, 영속 저장소와 네트워크 접근을 별도로 확인해야 합니다.

플러그인 설치는 SNS OAuth, 개발자 앱 등록, 계정 연결, 예약 실행 환경 연결을 자동으로 완료하지 않습니다. 이 패키지에는 비활성 원격 MCP 앱 팩토리가 있지만 등록된 실행 endpoint나 스케줄러는 없습니다. `private_runtime.py`는 같은 실행 환경의 `publisher.py`를 로컬 subprocess로 실행합니다. marketplace의 `ON_INSTALL` 설정도 Meta나 X 토큰을 발급하지 않습니다.

토큰·계정별 설정·운영 원장·보고 원문·실제 게시용 파일은 저장소와 설치된 플러그인 폴더 밖에 보관합니다. 비공개 GitHub 저장소도 비밀 저장소를 대신하지 않습니다. 설정·원장의 부모는 현재 사용자 소유 `0700`, 설정 파일은 `0600`이어야 하며 symlink를 허용하지 않습니다. 동일 계정의 재실행은 같은 영속 원장을 사용합니다.

각 사용자는 자신이 관리하는 개발자 앱과 공식 OAuth/토큰 발급 절차로 본인 계정을 연결합니다. 다른 사용자의 토큰을 공유하지 않습니다. 환경변수 방식의 키 이름은 [`examples/credentials.env.example`](examples/credentials.env.example)에 빈 값으로만 제공합니다. 실제 값은 비밀 저장소·프로세스 환경 또는 `private_runtime.py configure`가 만든 비공개 설정에 두며, OAuth 브라우저 로그인과 토큰 최초 발급을 자동화하는 helper는 포함하지 않습니다.

상세 연결 절차는 [API 설정](plugins/moru-sns/skills/sns-direct-publisher/references/api-setup.md), [비공개 실행 설정](plugins/moru-sns/skills/sns-direct-publisher/references/private-runtime.md), [운영자와 실행 환경](plugins/moru-sns/skills/sns-direct-publisher/references/dot-operation.md)을 따릅니다. 토큰 값을 채팅·명령행 인자·로그에 넣지 않습니다.

## 무게시 검토

아래 명령은 저장소 루트에서 실행합니다. 경로와 작업 ID는 실제 운영 설정으로 바꿉니다. `post.txt`는 검토할 최종 본문이며 저장소 밖에 준비합니다.

```bash
python3 plugins/moru-sns/skills/sns-direct-publisher/scripts/publisher.py plan \
  --platform threads \
  --text-file /private/content/post.txt \
  --job-id story-001 \
  --state-dir /private/publisher-state-v1
```

`plan`은 네트워크 요청과 상태 쓰기를 하지 않습니다. 계정·토큰이 없으면 `NOT_READY` 사유를 표시할 수 있으며, 종료 코드 0은 로컬 검토 완료입니다. `publish_ready`는 항상 false이며 실제 발행 권한이나 계정 인증을 대신하지 않습니다.

## 이미 주입된 인증 사용

`private_runtime.py doctor --connection threads|instagram|x`는 네트워크와 파일 쓰기 없이 현재 환경의 누락·잘못된 키 이름만 출력합니다. `ready_for_account_check`는 형식 점검이며 실제 인증·권한 검증이 아닙니다. 플랫폼별 `*_USER_ACCESS_TOKEN`, `*_EXPECTED_USER_ID`, `*_EXPECTED_USERNAME`, Instagram의 `INSTAGRAM_LOGIN_MODE`가 필요합니다. 값은 승인된 Personal vault 참조로 주입하고 스킬·플러그인·Git·파일에 복제하지 않습니다. 키 이름 선언과 실제 주입을 구분하며 누락을 토큰 만료로 단정하지 않습니다.

```bash
python3 scripts/private_runtime.py doctor --connection threads
python3 scripts/private_runtime.py run check --connection threads --state-dir /private/existing-state
python3 scripts/runtime_guard.py --connection threads --state-dir /private/existing-state --runtime-id existing-runtime
```

위 명령은 발행 스킬 폴더 기준입니다. 경로와 runtime ID는 기존 운영 설정으로 바꿉니다. `run check`는 공식 API의 ID·username을 함께 대조합니다. `run`은 `--config`가 없으면 환경변수를 사용하며 기본 연결은 Threads입니다. `--config`와 `--connection`은 함께 사용할 수 없습니다. guard는 플랫폼별 `--connection` 반복을 지원하며 같은 플랫폼의 파일 설정과 혼용하지 않습니다. 기존 원장을 검사할 때 `--initialize`를 쓰지 않습니다.

기존 앱의 OAuth 토큰 취득·갱신과 무인 실행의 보관된 토큰 읽기는 별개입니다. 이미 주입된 환경에서는 `configure`를 다시 실행할 필요가 없습니다. `token_status.py`·`threads_refresh.py`는 명시적 외부 파일 전용이며 Personal vault를 갱신하지 않습니다. Instagram·X 자동 갱신은 미구현입니다. 설치·진단·계정·원장·실제 권한·예약 성공은 각각 확인합니다.

## 인증 설정과 발행 예시

공식 개발자 앱에서 발급한 승인된 토큰이 준비되면 `configure`가 숨김 입력으로 받아 공식 계정을 확인하고 비공개 파일에 최초 저장합니다. 계정 이름은 실제 대상 계정으로 바꿉니다.

```bash
python3 plugins/moru-sns/skills/sns-direct-publisher/scripts/private_runtime.py configure \
  --platform threads \
  --config /private/runtime-config/threads.json \
  --expected-username your_bot_handle
```

완전히 새 운영을 시작할 때만 공식 계정 확인 후 원장을 명시적으로 초기화합니다. Threads 하나만 사용할 수 있으며 Instagram·X는 해당 config 옵션을 추가하면 됩니다. 기존 원장이 유실된 경우 이 명령으로 대체하지 않습니다.

```bash
python3 plugins/moru-sns/skills/sns-direct-publisher/scripts/runtime_guard.py \
  --threads-config /private/runtime-config/threads.json \
  --state-dir /private/publisher-state-v1 \
  --runtime-id operator-runtime-v1 --initialize
```

이후 같은 명령에서 `--initialize`를 빼고 기존 상태를 점검합니다. 설정과 원장을 검증한 뒤 같은 실행 환경에서 검토·발행합니다. `check`는 실제 API 조회이고 `publish --execute`는 실제 공개 게시입니다. 현재 사용자 요청 또는 승인된 운영 정책의 범위 안에서만 실행합니다.

```bash
python3 plugins/moru-sns/skills/sns-direct-publisher/scripts/private_runtime.py run check \
  --config /private/runtime-config/threads.json \
  --state-dir /private/publisher-state-v1

python3 plugins/moru-sns/skills/sns-direct-publisher/scripts/private_runtime.py run plan \
  --config /private/runtime-config/threads.json \
  --state-dir /private/publisher-state-v1 \
  --job-id story-001 --text-file /private/content/post.txt

python3 plugins/moru-sns/skills/sns-direct-publisher/scripts/private_runtime.py run publish \
  --config /private/runtime-config/threads.json \
  --state-dir /private/publisher-state-v1 \
  --job-id story-001 --text-file /private/content/post.txt --execute
```

직접 CLI와 private wrapper의 `publish`·`metrics`는 기존 원장·runtime marker·계정 결합을 필수로 검사하며 누락된 상태를 생성하지 않습니다. Git 저장소·스킬·설치 플러그인 내부의 상태 경로도 거절합니다. Instagram은 `ffprobe`가 필요하며 Threads/X 영상 점검은 guard에 `--require-video`를 추가합니다. 기존 원장이 없어진 경우 초기화나 새 작업 ID로 중복 방지를 우회하지 않습니다. 계정별 설정과 원장 복구는 별도 운영 작업입니다.

## 실제 지원 범위

원격 Work 연결을 위한 개발 중인 최소 MCP 전송 계층은 [remote/README.md](plugins/moru-sns/remote/README.md)에 있습니다. 기존 엔진을 재사용하는 앱 팩토리와 격리 테스트이며, 배포된 endpoint나 Work 예약 연결은 아닙니다. 운영에는 병합된 공식 배포본만 사용하며, 승인된 호스트·인증 검증기·기존 영속 원장 연결 전에는 발행을 활성화하지 않습니다.

| 기능 | 현재 구현 |
| --- | --- |
| Threads | 텍스트 원글·단일 영상·저장된 본인 원글에 대한 직접 후속댓글 |
| Instagram | Reels. Instagram Login과 Unified Login을 명시적으로 구분 |
| Instagram Unified 영상 | 로컬 MP4 resumable 업로드 또는 공개 HTTPS 영상 URL |
| Threads 영상 | 로컬 검증 파일과 같은 최종 영상을 제공하는 공개 HTTPS URL |
| X | 텍스트·단일 영상. 별도 계정 연결과 비용 확인 필요 |
| 지표 | 발행 성공 영수증이 있는 작업의 숫자 지표 조회 |
| 토큰 | Threads 상태 조회와 조건부 장기 토큰 갱신. Instagram·X 자동 갱신은 미구현 |
| 미지원 | 이미지·carousel·중첩 후속댓글·댓글 본문 수집·팔로우/해제·좋아요·DM·예약 실행기 |

본문 제한은 도구의 보수 기준으로 Threads UTF-8 500바이트, X 링크 없는 140코드포인트, Instagram 캡션 2200코드포인트입니다. 영상은 30~150초 MP4이며 Instagram은 300MB 이하입니다. Threads 500바이트는 플랫폼의 500자와 다릅니다. 실제 화면·일반 Blender 렌더와 AI 생성 영상/음성을 구분해 표시합니다.

콘텐츠 단계·공개 범위는 운영자가 검토합니다. 저장소 공개 상태, 테스트 통과, 병합, 배포, 제품 출시는 서로 다른 사실입니다. 발행 결과가 불명확하면 새 작업으로 재제출하지 않고 기존 기록과 실제 계정을 먼저 대조합니다.

## 검증

```bash
python3 scripts/check_package.py
python3 -m unittest discover \
  -s plugins/moru-sns/skills/sns-direct-publisher/scripts \
  -p 'test_*.py'
```

패키지 검사와 기존 회귀 검사는 로컬에서 실행하며 실제 SNS 요청을 보내지 않습니다. 회귀 검사의 API 클라이언트·토큰·미디어는 가짜 테스트 자료입니다. 이 검사 통과를 OAuth·실제 발행·예약 실행 성공으로 보고하지 않습니다.

## 라이선스

[MIT](LICENSE). 플랫폼 API 이용에는 각 플랫폼의 약관·권한·요금 정책이 별도로 적용됩니다.

## 릴리스 태그

PR 검증·병합 후 해당 병합 커밋의 플러그인 하위 트리를 태그합니다. 아래 `<merged-commit>`은 검증한 실제 병합 SHA로 바꿉니다. 기존 태그는 덮어쓰지 않습니다.

```bash
git subtree split --prefix=plugins/moru-sns <merged-commit>
# 출력된 커밋을 <plugin-commit>으로 사용
git tag v0.1.13 <plugin-commit>
git push origin refs/tags/v0.1.13
```

태그 트리와 `<merged-commit>:plugins/moru-sns` 트리가 같은지 확인합니다. GitHub 기본 Source code (zip)을 직접 내려받아 단일 최상위 디렉터리의 `plugin.json`, `skills/`, YAML 정책과 파일 구성을 검증합니다. [공식 아카이브 요건](https://developers.openai.com/plugins/deploy/submission-errors)의 루트 규칙을 적용하며, 실제 웹 업로드 결과는 따로 기록합니다.
