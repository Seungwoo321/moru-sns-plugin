# 공식 조회 범위

확인일: 2026-11-22. 플랫폼이 바뀔 수 있으므로 실제 연결 전에 현재 공식 문서와 승인된 앱 권한을 대조하라. 아래 기능 설명은 도구 구현·현재 계정 접근·행동 승인을 뜻하지 않는다.

## 전체 관계 명단과 실행

확인한 Threads·Instagram 공식 문서 인덱스와 사용자 참조에는 전체 팔로워/팔로잉 계정 명단 조회 또는 follow/unfollow 실행 endpoint가 문서화되어 있지 않다. 임의 endpoint를 만들거나 비공식 API로 우회하지 마라. `followers_count`, `follows_count` 같은 집계값으로 개별 관계나 명단을 만들지 마라. [1][2][3][4]

이 스킬은 팔로우 변경·댓글·좋아요·DM을 실행하지 않는다. 이 범위를 "Meta의 모든 자동화가 금지된다"는 주장으로 바꾸지 말고, 공식 안전 횟수나 성장 효과를 만들어 제시하지 마라. X 연결·API 확장도 이 스킬에서 수행하지 않는다.

## 사용할 수 있는 자료와 제한

| 자료 | 확인 가능한 범위와 제한 |
| --- | --- |
| 사용자 제공 목록·내보내기·확인 화면 | 해당 자료 시점의 계정·관계 근거. 전체·현재 상태라고 자동 간주하지 말고 범위·날짜를 기록 |
| 허용된 공개 검색·게시물 링크 | 현재 접근 가능한 공개 글·프로필의 주제와 활동. 로그인·비공개 접근을 우회하지 않으며 검색 누락을 활동 부재로 해석하지 않음 |
| Threads 원글의 replies/conversation | 허용된 원글 답글의 내용·시각과 공개 사용자의 username/permalink. 일부 필드는 공개 사용자와 자기 사용자에만 반환됨. 조회할 글·권한·도구 지원을 먼저 확인하고 댓글 생성 기능은 사용하지 않음 [5] |
| Threads public profile discovery | `threads_profile_discovery`와 정확한 username 필요. 공식 문서상 공개 프로필·100명 이상 팔로워 조건이 있고 Standard Access는 일부 Meta 공식 계정만 조회. 이는 API 가용성 제약이며 추천 자격이나 팔로워 수 점수 기준이 아님 [6] |
| Instagram 자기 게시물 댓글 | 승인된 댓글 조회 권한으로 내용·시각·작성자 정보를 확인할 수 있음. Unified/Facebook Login은 `instagram_manage_comments`, Instagram Login은 `instagram_business_manage_comments` 등 현재 조건을 확인. 기본 발행 연결만으로 이 기능이 추가되는 것은 아님 [7] |
| Instagram Business Discovery | Facebook Login·Unified Login에서 특정 다른 professional account의 정보·게시물·집계값 조회. 일반 개인계정 전수 검색이나 팔로워 명단 조회가 아님 [8] |

API 조회 권한이 없으면 이미 제공된 목록이나 접근 가능한 공개 근거로 진행하고 그 한계를 표시하라. 이 스킬을 위해 새 권한·OAuth·웹훅·인프라를 만들거나 기존 발행 스크립트에 조회 기능을 추가하지 마라. 비공개 계정이나 작은 계정을 API로 읽지 못했다는 사실은 그 계정의 부적합·봇·휴면 증거가 아니다.

## Instagram 관계 표시의 제한된 예외

Instagram User Profile API에는 `is_user_follow_business`, `is_business_follow_user`가 있다. 이는 메시징 webhook의 IGSID와 관련 권한을 사용하는 경로이며, 상대가 먼저 메시지를 보내거나 icebreaker/menu를 눌러 동의가 설정된 상황 등이 전제다. 댓글만 달았다는 이유로 동의가 생기지 않는다. 전체 명단이나 follow/unfollow 실행 기능이 아니다. [9]

이미 적법하게 제공된 해당 관계 근거가 있다면 날짜와 출처를 기록할 수 있지만, 이 스킬에서는 관계 확인을 위해 DM을 보내거나 메시징 권한·동의 수집을 새로 설정하지 마라. 직접 근거가 없으면 `follows_me`와 `following_by_us`를 `unknown`으로 유지하라.

## 공식 자료

1. Threads 전체 문서 인덱스: https://developers.facebook.com/documentation/threads/llms.txt
2. Threads 사용자 endpoint: https://developers.facebook.com/documentation/threads/reference/user
3. Instagram 전체 문서 인덱스: https://developers.facebook.com/documentation/instagram-platform/llms.txt
4. Instagram 사용자 fields/edges: https://developers.facebook.com/documentation/instagram-platform/instagram-graph-api/reference/ig-user
5. Threads 답글·대화 조회: https://developers.facebook.com/documentation/threads/retrieve-and-manage-replies/replies-and-conversations
6. Threads 공개 프로필 조회 조건: https://developers.facebook.com/documentation/threads/threads-profiles
7. Instagram 댓글 조회와 권한: https://developers.facebook.com/documentation/instagram-platform/comment-moderation
8. Instagram Business Discovery: https://developers.facebook.com/documentation/instagram-platform/instagram-api-with-facebook-login/business-discovery
9. Instagram 메시징 User Profile의 관계 표시와 동의: https://developers.facebook.com/documentation/instagram-platform/instagram-api-with-instagram-login/messaging-api/user-profile
