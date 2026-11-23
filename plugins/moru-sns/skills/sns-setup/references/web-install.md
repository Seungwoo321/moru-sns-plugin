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

## 개인 계정과 공개 디렉터리

이미 Plugins → Installed/Personal에 정본이 있으면 해당 항목을 선택한다. 없을 때 GitHub URL 입력만으로 개인 웹 설치가 된다고 안내하지 않는다. 배포자는 https://platform.openai.com/plugins 에서 ZIP을 업로드하고 검토·게시 절차를 진행할 수 있다. 이 저장소가 공개라는 사실은 디렉터리 게시 완료를 의미하지 않는다. 제출에는 소유 조직 또는 Apps Management Write 권한과 게시자 검증이 필요하다.

공개 제출은 workspace import와 별개다. 공식 제출 문서는 최초 skills-only 제출 이후 MCP 추가를 현재 지원하지 않는다고 명시한다. 따라서 전체 공식 API 플러그인으로 제출하려면 이 제품의 MCP 포함 범위를 먼저 확정해야 한다. 이 패키지를 임시 skills-only 공개 제품으로 제출해 그 결정을 우회하지 않는다. ZIP은 동일 정본의 패키지 산출물이며 등록·심사 승인 증거가 아니다.

공식 안내: https://developers.openai.com/plugins/deploy/submission

## 설치 후 첫 요청

“모루 시작 점검을 해 주세요. 이 플러그인의 sns-setup을 사용하고 Threads·Instagram만 무게시 확인하세요. 설치된 스킬과 계정·원장·예약의 준비 상태를 구분하세요. 새 연결이나 예약을 만들지 마세요.”

원격 MCP가 없어도 제공된 보고의 글감·초안 작업은 가능하다. 공식 API 호출은 별도 실행 연결이 있어야 한다. 기존 계정·원장·공개 승인·최소 발행 간격 규칙은 그대로 적용한다. 브라우저 게시로 대체하지 않는다.
