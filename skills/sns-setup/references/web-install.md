# 웹에서 같은 정본 사용하기

공식 소스: https://github.com/Seungwoo321/moru-sns-plugin

## Workspace GitHub import

관리자는 **Admin → Plugins → Add → Import marketplace**에서 다음 값을 사용한다.

| 입력 | 값 |
|---|---|
| Source | `https://github.com/Seungwoo321/moru-sns-plugin` |
| Path | 빈 값 — 저장소 루트 |
| Branch, tag, or commit | `master` 또는 검토한 전체 커밋 SHA |
| Marketplace | `moru-sns` |
| Plugin | `moru-sns` |

저장소의 `.agents/plugins/marketplace.json`이 `./plugins/moru-sns`를 가리킨다. `.agents/plugins`를 Path로 입력하지 않는다. import 후 결과와 기존 설치 정책을 확인하고, 구성원이 Plugins에서 Moru SNS를 설치·선택해 Work에서 `sns-setup`을 사용한다. 기존 등록이 있으면 중복 등록하지 않는다. 실제 관측된 ID 없이 `pluginId`를 만들지 않는다. 저장소의 ON_INSTALL은 workspace 인증 정책이나 SNS 연결을 자동 설정하지 않는다.

이 경로에는 workspace 관리자 권한과 저장소 읽기 권한이 필요하다. 개인 계정에 Admin 메뉴가 있다고 가정하지 않는다. 공식 안내: https://learn.chatgpt.com/docs/enterprise/plugin-management

## 개인 계정의 ZIP 업로드

이미 Plugins → Installed/Personal에 정본이 있으면 해당 항목을 선택한다. 업로드 설치는 **추가 → 플러그인 압축 파일 업로드**에서 다음 GitHub 기본 ZIP을 사용한다.

https://github.com/Seungwoo321/moru-sns-plugin/archive/refs/tags/v0.1.5.zip

v0.1.5 태그는 정본 저장소의 `plugins/moru-sns` 하위 트리만 루트로 갖는다. 압축의 단일 최상위 폴더 바로 아래에 `plugin.json`과 `skills/`가 있다. `master` 전체 소스 ZIP과 v0.1.4 이전 ZIP은 사용하지 않는다. 별도 생성기나 릴리스 첨부물은 없다. 이 태그에는 marketplace가 없으므로 위 GitHub marketplace import에는 `master` 또는 전체 저장소 커밋 SHA를 사용한다.

공식 아카이브 요건: https://developers.openai.com/plugins/deploy/submission-errors

ZIP 검사 통과는 실제 웹 설치 완료를 의미하지 않는다. 개인 업로드는 공개 디렉터리 제출과 별개이며, 이 저장소가 공개라는 사실도 디렉터리 게시 완료를 의미하지 않는다. 공개 제출의 권한·검토 요건은 https://developers.openai.com/plugins/deploy/submission 에서 확인한다.

## 설치 후 첫 요청

“모루 시작 점검을 해 주세요. 이 플러그인의 sns-setup을 사용하고 Threads·Instagram만 무게시 확인하세요. 설치된 스킬과 계정·원장·예약의 준비 상태를 구분하세요. 새 연결이나 예약을 만들지 마세요.”

원격 MCP가 없어도 제공된 보고의 글감·초안 작업은 가능하다. 공식 API 호출은 별도 실행 연결이 있어야 한다. 기존 계정·원장·공개 승인·최소 발행 간격 규칙은 그대로 적용한다. 브라우저 게시로 대체하지 않는다.
