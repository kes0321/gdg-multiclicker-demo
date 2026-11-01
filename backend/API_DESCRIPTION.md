# 프론트엔드 연동용 API 명세

갱신일: 2026-11-01

현재 소스 코드의 동작을 기준으로 작성했다. 예시의 호스트, 포트, 플레이어 ID와 시각은 설명용이며 실제 배포 주소는 별도로 설정해야 한다.

## 1. 연결 정보와 공통 규칙

| 항목 | 값 |
| --- | --- |
| HTTP 기본 주소 예시 | `http://localhost:8000` |
| 게임 HTTP 경로 | `/api` |
| WebSocket 주소 예시 | `ws://localhost:8000/ws` |
| 요청·응답 형식 | JSON; HTTP 요청 본문은 `Content-Type: application/json` |
| 인증 방식 | 브라우저의 `session_token` 쿠키 |
| HTTPS 환경 | HTTP 요청은 `https://`, WebSocket은 `wss://` 사용 |

서버는 FastAPI로 구성되어 있으며 Redis 연결이 필요하다. 현재 Redis 접속 설정은 `localhost:6379`이다. HTTP 서버의 호스트와 포트는 코드에 고정되어 있지 않다.

### 세션 쿠키

- `POST /api/players`가 세션 쿠키를 발급하거나 갱신한다. 토큰은 JSON 응답에 포함되지 않는다.
- 쿠키 속성은 `HttpOnly`, `SameSite=Lax`, `Path=/`, `Max-Age=2592000`(30일), `Secure=false`이다. `Domain`은 지정하지 않는다.
- 브라우저 JavaScript에서 쿠키 값을 읽어 토큰을 관리할 필요가 없다. HTTP 요청 시 `credentials: "include"`를 사용하고, WebSocket 연결 시 브라우저가 해당 주소에 적용되는 쿠키를 자동 전송하도록 한다.
- 세션 만료 시간은 유효한 세션으로 `POST /api/players`를 호출해 복원했을 때 연장된다. 조회 요청과 WebSocket 연결·메시지는 만료 시간을 연장하지 않는다.
- WebSocket은 연결 시 세션을 확인한다. 연결 후 메시지마다 세션 만료를 재검사하지 않는다.
- 현재 백엔드에는 CORS 미들웨어가 없다. 프론트엔드 `vite.config.ts`는 `/api`와 `/ws`를 `BACKEND_URL`(기본값 `http://localhost:8000`)로 프록시하며 `/ws`의 WebSocket 전달을 지원한다. 브라우저에서는 프론트엔드 origin의 상대 경로를 사용한다. 백엔드를 서로 다른 origin에서 직접 호출하려면 CORS와 쿠키 조건을 맞추는 별도 설정이 필요하다. `credentials` 설정만으로 CORS 또는 `SameSite` 제한이 해소되지는 않는다.

### 권장 호출 순서

1. 닉네임을 입력받아 `POST /api/players`를 호출한다.
2. 응답의 `player`로 내 상태를 초기화하고, 쿠키 발급이 완료된 후 `/ws`에 연결한다.
3. `connected` 메시지의 `player`로 최신 상태를 반영한다.
4. 클릭은 `click` 또는 `click_batch`, 업그레이드는 `levelup` 메시지로 전송한다.
5. 서버 응답의 최종 `score`, `level`, `total_clicks`를 화면 상태에 반영한다. 응답에 없는 필드는 기존 값을 유지한다.
6. 접속자 리더보드는 WebSocket `leaderboard` 방송으로 갱신한다. 전체 플레이어 기준 상위 랭킹이 필요하면 `GET /api/leaderboard`를 별도로 호출한다. 두 목록은 대상과 동점 정렬 규칙이 다르다.

## 2. 데이터 모델과 게임 규칙

### Player

| 필드 | JSON 타입 | 의미 |
| --- | --- | --- |
| `player_id` | string | 서버가 생성하는 ID. 현재 형식은 `p` + UUID의 32자리 16진수 |
| `nickname` | string | 양끝 공백을 제거한 닉네임, 1~20자 |
| `score` | integer | 현재 보유 골드. 누적 획득량이 아니며 레벨업 시 감소 |
| `level` | integer | 현재 레벨, 최초 1, 최대 40 |
| `total_clicks` | integer | 누적 처리된 클릭 수, 최초 0 |
| `created_at_ms` | integer | 생성 시각, Unix epoch 밀리초 |
| `gold_per_click` | integer | 현재 레벨의 클릭당 골드 `G(L)` |
| `next_gold_per_click` | integer 또는 null | 다음 레벨의 클릭당 골드 `G(L+1)`. 40레벨이면 `null` |
| `next_upgrade_cost` | integer 또는 null | 현재 레벨의 다음 업그레이드 비용 `C(L)`. 40레벨이면 `null` |

```json
{
  "player_id": "p0123456789abcdef0123456789abcdef",
  "nickname": "빵굽는고양이",
  "score": 0,
  "level": 1,
  "total_clicks": 0,
  "created_at_ms": 1793062800000,
  "gold_per_click": 10,
  "next_gold_per_click": 12,
  "next_upgrade_cost": 100
}
```

### 골드와 레벨 계산

- 클릭당 골드: `G(L) = floor(10 × 1.25^(L - 1))`.
- 다음 레벨로 올라가는 비용: `C(L) = ceil(100 × 1.6^(L - 1))`.
- 승인된 단일 클릭: `획득 골드 = G(처리 시점의 level)`, `total_clicks += 1`.
- 승인된 배치 클릭: `획득 골드 = G(처리 시점의 level) × accepted_clicks`, `total_clicks += accepted_clicks`.
- 클릭 1회당 토큰 1개를 소비한다. 단일 클릭과 배치 클릭은 플레이어별 버킷을 공유하며, 토큰이 부족하면 요청 전체를 거절한다. 상세 충전 규칙과 실패 응답은 4.4절을 참고한다.
- 레벨업은 잔액이 충분하면 `C(L)`을 차감하고 레벨을 1 올린다. `total_clicks`는 변하지 않는다. 최대 40레벨에서는 클릭 보상 60,185골드를 유지하며 레벨업을 `MAX_LEVEL_REACHED`로 거절한다. 다음 보상·비용은 모두 `null`이다.
- 예: 레벨 1에서는 클릭당 10골드이며 100골드를 쓰면 레벨 2가 된다. 레벨 2에서는 클릭당 12골드, 다음 비용은 160골드이다.
- 랭킹은 현재 보유 골드 내림차순이다. 레벨업으로 골드를 사용하면 순위가 내려갈 수 있다.
- 클릭과 레벨업은 각각 Redis Lua 스크립트에서 상태와 랭킹을 함께 갱신한다.
- `app/balance.py`의 `MAX_LEVEL = 40`을 Python과 Lua가 공유한다. 문자열 계수와 `Decimal`로 필요한 레벨의 정수 값을 계산하고 캐시하며, 응답과 Lua가 공유한다. Lua는 Redis의 현재 레벨에 해당하는 값이 필요하면 쓰기 없이 서버에 요청하고, 서버가 계산한 값을 전달받은 뒤 실제 레벨을 다시 확인한다. 도중에 다른 연결이 레벨업했다면 새 레벨의 값을 다시 요청한다. 파생 필드는 Redis에 중복 저장하지 않는다.
- 기존 골드·레벨은 초기화하지 않는다. 저장된 레벨이 1~40 범위를 벗어나거나 유효한 정수가 아니면 자동 보정하지 않고 `INVALID_PLAYER_LEVEL` 오류와 서버 로그로 알린다. 해당 플레이어의 상태 변경은 거절한다.
- 현재 Lua·JavaScript·랭킹 점수에서 정수를 정확하게 표현하는 범위는 `2^53 - 1`까지다. 만렙 이후에도 골드·클릭 수는 누적되므로, 상태 변경 결과가 이 범위를 벗어나면 기존 `NUMERIC_LIMIT_EXCEEDED` 보호로 거절한다.

| 현재 레벨 | 클릭당 골드 | 다음 업그레이드 비용 |
| --- | ---: | ---: |
| 1 | 10 | 100 |
| 2 | 12 | 160 |
| 5 | 24 | 656 |
| 10 | 74 | 6,872 |
| 15 | 227 | 72,058 |
| 30 | 6,462 | 83,076,750 |
| 31 | 8,077 | 132,922,800 |
| 39 | 48,148 | 5,708,990,771 |
| 40 | 60,185 | null |

Player를 포함하는 HTTP 응답, WebSocket `connected.player`, 클릭·배치·레벨업 성공 결과는 모두 현재 응답 레벨 기준의 `gold_per_click`, `next_gold_per_click`, `next_upgrade_cost`를 포함한다. `next_gold_per_click`은 해당 응답 레벨에서 한 단계 더 올라갔을 때의 보상이며, 초기 접속부터 레벨업 버튼의 보상 미리보기에 사용한다. 랭킹 항목은 기존 필드를 유지한다.

## 3. HTTP API

| 메서드 | 경로 | 용도 | 세션 필요 |
| --- | --- | --- | --- |
| `POST` | `/api/players` | 플레이어 생성 또는 세션 복원 | 선택 |
| `GET` | `/api/players/{player_id}` | 플레이어 상세 조회 | 아니요 |
| `GET` | `/api/leaderboard` | 접속 여부와 무관한 전체 플레이어 상위 랭킹 조회 | 아니요 |
| `GET` | `/api/health` | Redis 연결 상태 확인 | 아니요 |
| `GET` | `/hello` | Redis 읽기·쓰기 데모 | 아니요 |
| `GET` | `/counter` | 공용 카운터 증가 데모 | 아니요 |

### 3.1. 플레이어 생성·복원

`POST /api/players`

요청:

```json
{"nickname": "빵굽는고양이"}
```

`nickname`은 필수 문자열이다. 양끝 공백 제거 후 길이가 1~20자여야 한다. 공백만 있는 값은 거절된다. 닉네임 중복 검사는 없으며, 중복 닉네임을 사용할 수 있다.

| 상황 | 상태 코드 | 처리 |
| --- | --- | --- |
| 사용 가능한 기존 세션 없음 | `201 Created` | 신규 플레이어 생성, 쿠키 발급 |
| 기존 세션과 플레이어가 유효하고 세션 연장 성공 | `200 OK` | 기존 플레이어 반환, 쿠키와 세션 유효기간 갱신 |

신규 생성 응답:

```json
{
  "created": true,
  "player": {
    "player_id": "p0123456789abcdef0123456789abcdef",
    "nickname": "빵굽는고양이",
    "score": 0,
    "level": 1,
    "total_clicks": 0,
    "created_at_ms": 1793062800000,
    "gold_per_click": 10,
    "next_gold_per_click": 12,
    "next_upgrade_cost": 100
  }
}
```

복원 응답은 동일한 구조이며 `created`가 `false`이고 `player`는 기존 저장 상태이다. 복원 요청에서도 유효한 `nickname` 본문이 필수다. 복원 시 요청의 닉네임으로 기존 닉네임을 변경하지 않는다. 세션이 만료되었거나 연결된 플레이어가 없거나 세션 연장에 실패하면 신규 생성 경로로 진행한다.

오류:

| 상태 코드 | `detail` 또는 의미 |
| --- | --- |
| `422` | 닉네임 누락, 타입·길이 오류, 잘못된 JSON 등 요청 검증 실패 |
| `409` | `{"code":"INVALID_PLAYER_LEVEL"}`. 기존 세션의 플레이어 레벨 오류이며 새 플레이어로 대체하지 않음 |
| `409` | `{"code":"NUMERIC_LIMIT_EXCEEDED"}`. 기존 플레이어의 표시값이 정확한 정수 표현 범위를 초과함 |
| `500` | `생성된 플레이어를 조회할 수 없습니다.` |
| `503` | `Redis 처리 실패` |

### 3.2. 플레이어 조회

`GET /api/players/{player_id}`

- `player_id`: 필수 경로 문자열.
- 성공: `200 OK`, 본문은 **Player 객체 자체**이다. `{ "player": ... }`로 감싸지 않는다.
- 세션 없이 다른 플레이어도 조회할 수 있다.
- 오류: `404` / `플레이어가 없습니다.`, `503` / `플레이어 조회 실패`.
- 저장된 레벨 오류: `409`, `{"detail":{"code":"INVALID_PLAYER_LEVEL"}}`.
- 정확한 정수 표현 범위 초과: `409`, `{"detail":{"code":"NUMERIC_LIMIT_EXCEEDED"}}`.

### 3.3. 리더보드 조회

`GET /api/leaderboard?limit=25`

| 쿼리 | 타입 | 기본값 | 허용 범위 |
| --- | --- | --- | --- |
| `limit` | integer | 10 | 1~100 |

성공: `200 OK`

```json
{
  "players": [
    {"rank": 1, "player_id": "p0123456789abcdef0123456789abcdef", "nickname": "빵굽는고양이", "score": 1500},
    {"rank": 2, "player_id": "pabcdef0123456789abcdef0123456789", "nickname": "우주토끼", "score": 900}
  ]
}
```

- 각 항목은 `rank`(integer, 1부터 시작), `player_id`(string), `nickname`(string), `score`(integer)를 포함한다. 닉네임 조회 결과가 없으면 `"Unknown"`을 사용한다.
- 접속 여부와 무관하게 Redis 전체 리더보드의 상위 `limit`명을 반환한다. WebSocket의 접속자 리더보드와 구분한다.
- 플레이어가 없으면 `{"players": []}`이다. 전체 인원과 내 순위는 별도 제공하지 않는다.
- 동점이어도 공동 순위를 부여하지 않고 1, 2, 3처럼 연속 순위를 반환한다. 현재 `ZREVRANGE` 동작에 따라 동점은 `player_id`의 역 사전순으로 정렬된다.
- 닉네임은 응답에 포함되므로 이름 표시를 위한 플레이어 상세 조회는 필요하지 않다.
- 오류: `422` / `limit` 검증 실패, `503` / `랭킹 조회 실패`.

### 3.4. 상태 확인

`GET /api/health`

성공: `200 OK`, `{"status": "ok"}`. Redis 오류 시 `503`, `{"detail": "Redis 연결 실패"}`.

### 3.5. 데모 엔드포인트

| 경로 | 성공 응답 (`200 OK`) | 부수 효과 |
| --- | --- | --- |
| `GET /hello` | `{"message": "Hello Redis!"}` | Redis의 `hello` 값을 설정한 뒤 읽음 |
| `GET /counter` | `{"count": 1}` | 호출마다 공용 카운터 1 증가. 예시 값은 현재 저장 상태에 따라 다름 |

두 경로는 게임 점수와 무관하다. Redis 오류를 게임 HTTP API와 같은 `503` JSON 형식으로 변환하는 처리는 없다.

### HTTP 오류 형식

일반적인 HTTP 오류는 다음 형식이다. 저장된 레벨 또는 숫자 범위 오류의 `detail`은 위의 코드 객체 형식을 사용한다.

```json
{"detail": "플레이어가 없습니다."}
```

요청 검증 실패는 FastAPI의 `422` 응답이며 `detail`은 배열이다. 다음은 구조 예시이며 `type`, `msg`, `input`, `ctx` 등 세부 필드는 실패 원인과 라이브러리 버전에 따라 달라질 수 있다.

```json
{
  "detail": [
    {"loc": ["body", "nickname"], "msg": "Field required", "type": "missing"}
  ]
}
```

## 4. WebSocket API

### 4.1. 연결과 초기 상태

`GET /ws`로 WebSocket 업그레이드를 요청한다. `/api/ws`가 아니다. URL 쿼리나 메시지의 `player_id`로 인증하지 않으며, `session_token` 쿠키에 연결된 플레이어를 사용한다.

연결 수락 후 첫 서버 메시지:

```json
{
  "type": "connected",
  "player": {
    "player_id": "p0123456789abcdef0123456789abcdef",
    "nickname": "빵굽는고양이",
    "score": 0,
    "level": 1,
    "total_clicks": 0,
    "created_at_ms": 1793062800000,
    "gold_per_click": 10,
    "next_gold_per_click": 12,
    "next_upgrade_cost": 100
  }
}
```

서버는 `connected` 전송 후 현재 프로세스에 등록된 모든 연결에 `leaderboard`를 방송한다. 이후에도 접속자 변경과 성공한 게임 동작에 따라 방송이 도착한다.

모든 클라이언트 메시지는 **텍스트 JSON 객체**로 전송한다. 서버는 연결별로 요청을 순서대로 처리하지만, 요청 응답 사이에 `leaderboard` 방송이 들어올 수 있다. 성공한 클릭·배치 클릭·레벨업은 리더보드 방송을 먼저 시도한 뒤 요청자에게 결과를 보낸다. 요청 ID와 응답 ID는 제공하지 않으므로 클라이언트는 메시지의 `type`으로 방송과 동작 결과를 구분하고, 필요한 요청·응답 순서를 관리한다.

### 4.2. 단일 클릭

클라이언트 → 서버:

```json
{"type": "click"}
```

서버 → 클라이언트, 레벨 1에서 첫 클릭한 예:

```json
{
  "type": "click_result",
  "success": true,
  "player_id": "p0123456789abcdef0123456789abcdef",
  "score": 10,
  "level": 1,
  "total_clicks": 1,
  "gold_gained": 10,
  "accepted_clicks": 1,
  "gold_per_click": 10,
  "next_gold_per_click": 12,
  "next_upgrade_cost": 100
}
```

`score`, `level`, `total_clicks`는 처리 후 최종 상태이며 `gold_gained`는 이번 요청의 획득 골드다. 단일 클릭은 내부적으로 `count=1`인 배치 클릭으로 처리하므로 성공 응답에 `success: true`, `accepted_clicks: 1`을 포함한다. 토큰 부족 시 4.4절의 `click_result` 실패 응답을 반환한다.

### 4.3. 배치 클릭

클라이언트 → 서버:

```json
{"type": "click_batch", "count": 3}
```

`count`는 필수이며 1~10의 JSON 정수만 허용한다. `"3"`, `3.0`, `true`, `null`, 누락, 범위 밖 값은 거절한다. 10회를 초과하는 클릭을 묶으려면 클라이언트에서 여러 요청으로 나눈다.

성공 응답, 레벨 1·0골드·0클릭에서 3회를 처리한 예:

```json
{
  "type": "click_batch_result",
  "success": true,
  "player_id": "p0123456789abcdef0123456789abcdef",
  "score": 30,
  "level": 1,
  "total_clicks": 3,
  "gold_gained": 30,
  "accepted_clicks": 3,
  "gold_per_click": 10,
  "next_gold_per_click": 12,
  "next_upgrade_cost": 100
}
```

`accepted_clicks`는 이번에 처리한 클릭 수다. 토큰이 충분하면 `count` 전체를 처리하고, 부족하면 전체를 거절한다. 일부 클릭만 승인하지 않는다. `gold_gained`는 클릭 한 번의 골드가 아닌 배치 전체의 획득량이다.

입력 오류 응답:

```json
{
  "type": "click_batch_result",
  "success": false,
  "code": "INVALID_BATCH_COUNT"
}
```

입력 오류는 상태를 변경하지 않으며 연결을 유지한다. `message` 필드는 포함하지 않는다.

### 4.4. 클릭 속도 제한

단일 클릭과 배치 클릭에 동일한 플레이어별 토큰 버킷을 적용한다.

| 항목 | 현재 동작 |
| --- | --- |
| 버킷 용량·신규 생성 시 토큰 | 30개 |
| 소비량 | 클릭 1회당 1개, 배치는 `count`개 |
| 충전 | 서버 백그라운드 작업이 0.5초 간격으로 기존 버킷에 4개씩 추가, 최대 30개 |
| 버킷 유효기간 | 생성 또는 클릭 승인 후 60초 |
| 버킷 만료 후 클릭 | 30개로 초기화한 뒤 요청 클릭 수만큼 소비 |
| 토큰 부족 | 클릭·골드·랭킹·토큰을 변경하지 않고 요청 전체 거절, 연결 유지 |

정상적으로 충전 작업이 실행될 때 명목 충전량은 초당 8개이며, 초기·누적 토큰으로 순간적으로 더 많은 클릭을 처리할 수 있다. 따라서 고정된 1초 구간마다 최대 8회를 허용하는 방식은 아니다. 충전 작업이 지연되면 밀린 충전을 연속 실행하지 않으며, 클릭 요청 자체는 경과 시간에 따른 충전을 하지 않는다.

버킷은 `player_id` 기준이므로 같은 플레이어의 여러 탭·연결이 토큰을 공유한다. 세션 복원이나 WebSocket 재연결로 기존 버킷을 초기화하지 않는다. 충전과 거절된 클릭은 버킷 유효기간을 연장하지 않는다. 레벨업에는 이 토큰 제한을 적용하지 않는다.

단일 클릭의 토큰 부족 응답:

```json
{
  "type": "click_result",
  "success": false,
  "code": "RATE_LIMITED",
  "remaining_tokens": 0
}
```

배치 클릭의 토큰 부족 응답, `count=3`을 요청했으나 토큰이 2개인 예:

```json
{
  "type": "click_batch_result",
  "success": false,
  "code": "RATE_LIMITED",
  "remaining_tokens": 2
}
```

`remaining_tokens`는 거절 시점의 잔여 토큰이며 현재 골드나 클릭 수가 아니다. 실패 응답에는 `player_id`, `score`, `level`, `total_clicks`, `gold_gained`, `accepted_clicks`, `message`, 재시도 대기 시간 필드가 없다. 클라이언트는 실패한 클릭을 획득량에 반영하지 않고, 예측 반영한 상태가 있다면 서버 상태에 맞춰 보정한다. 토큰 잔량 조회나 충전 완료 알림 API는 제공하지 않는다.

### 4.5. 레벨업

클라이언트 → 서버:

```json
{"type": "levelup"}
```

성공 응답, 레벨 1·150골드에서 업그레이드한 예:

```json
{
  "type": "levelup_result",
  "success": true,
  "player_id": "p0123456789abcdef0123456789abcdef",
  "score": 50,
  "level": 2,
  "gold_spent": 100,
  "gold_per_click": 12,
  "next_gold_per_click": 15,
  "next_upgrade_cost": 160
}
```

`gold_spent`는 이번에 사용한 골드다. `gold_per_click`, `next_gold_per_click`, `next_upgrade_cost`는 상승한 레벨을 기준으로 반환한다. 성공 응답에는 `total_clicks`가 없으므로 기존 클릭 수를 유지한다. 프론트엔드는 비용이나 클릭 보상을 자체 계산하지 않는다.

골드 부족 응답, 레벨 1·50골드인 예:

```json
{
  "type": "levelup_result",
  "success": false,
  "code": "INSUFFICIENT_GOLD",
  "score": 50,
  "required_gold": 100
}
```

`required_gold`는 부족분이 아닌 업그레이드에 필요한 전체 비용이다. 실패 시 골드와 레벨을 변경하지 않으며 연결을 유지한다.

최대 레벨 거절 응답:

```json
{
  "type": "levelup_result",
  "success": false,
  "code": "MAX_LEVEL_REACHED",
  "score": 50,
  "level": 40,
  "gold_per_click": 60185,
  "next_gold_per_click": null,
  "next_upgrade_cost": null
}
```

최대 레벨 거절은 골드·레벨·클릭 수·토큰·랭킹을 변경하지 않으며 연결을 유지한다. 일반·배치 클릭은 계속 가능하다.

정확한 정수 표현 범위를 초과하는 동작의 거절 응답:

```json
{
  "type": "levelup_result",
  "success": false,
  "code": "NUMERIC_LIMIT_EXCEEDED"
}
```

숫자 범위 초과 거절은 토큰·골드·클릭 수·레벨·랭킹을 변경하지 않으며 연결을 유지한다. 클릭·배치 클릭도 해당 `*_result` 타입으로 같은 오류 코드를 반환할 수 있다.

### 4.6. 접속자 리더보드 방송

서버 → 현재 프로세스에 등록된 모든 WebSocket 연결:

```json
{
  "type": "leaderboard",
  "players": [
    {"rank": 1, "player_id": "p0123456789abcdef0123456789abcdef", "nickname": "빵굽는고양이", "score": 1500},
    {"rank": 2, "player_id": "pabcdef0123456789abcdef0123456789", "nickname": "우주토끼", "score": 900}
  ]
}
```

- 방송 대상 목록은 해당 프로세스에 연결된 플레이어만 포함한다. 여러 탭으로 접속한 플레이어는 한 번만 표시하며, 마지막 연결이 제거되면 이후 목록에서 제외한다.
- 항목 필드는 HTTP 리더보드와 같은 `rank`, `player_id`, `nickname`, `score`이다. 닉네임이 없으면 `"Unknown"`을 사용하고, Redis 리더보드에 점수가 없는 플레이어는 제외한다.
- 보유 골드 내림차순, 동점이면 `player_id` 오름차순으로 정렬한다. 순위는 이 접속자 목록 내에서 1부터 연속으로 매긴다. HTTP 전체 랭킹의 동점 정렬과 다르다.
- 서버 측 상위 인원수 제한이나 `limit` 옵션은 없다. 전체 인원 수·개인 전체 순위·레벨·누적 클릭 수는 포함하지 않는다.
- 연결 등록 및 `connected` 전송 후, 성공한 단일 클릭·배치 클릭·레벨업 후, 등록된 연결의 종료 정리 후 방송을 시도한다. 잘못된 메시지, 토큰 부족, 골드 부족은 방송을 유발하지 않는다.
- 주기적으로 방송하는 방식이 아니며, 구독 요청 메시지도 필요하지 않다. 목록이 오면 기존 접속자 목록 전체를 교체한다.
- 방송용 Redis 조회가 실패하면 로그를 남기고 해당 방송을 건너뛴다. 이미 성공한 게임 상태 변경을 되돌리지 않으며 요청자의 결과 응답은 계속 시도한다.

연결 관리자는 프로세스 메모리를 사용하며 프로세스 간 방송을 공유하는 기능은 없다. 다중 워커나 여러 서버에서는 각 프로세스가 관리하는 연결 범위의 접속자 목록을 받는다. 충전 작업도 프로세스마다 시작되므로, 여러 프로세스가 같은 Redis를 사용하면 토큰 충전량이 중복될 수 있다.

### 4.7. 공통 메시지 오류

| 입력 상황 | 서버 응답 | 연결 |
| --- | --- | --- |
| JSON 구문 오류 | `{"type":"error","code":"INVALID_JSON"}` | 유지 |
| JSON 배열·숫자·문자열·불리언·null 등 객체가 아닌 값 | `{"type":"error","code":"INVALID_MESSAGE"}` | 유지 |
| `type` 누락 또는 지원하지 않는 값 | `{"type":"error","code":"UNKNOWN_MESSAGE_TYPE"}` | 유지 |
| 연결 후 저장된 레벨이 유효하지 않은 클릭·배치 클릭·레벨업 | 해당 `*_result`에 `success: false`, `code: "INVALID_PLAYER_LEVEL"`, 상태 변경 없음 | 유지 |

`click`과 `levelup`은 `type`만으로 처리하며, 메시지에 임의의 점수나 레벨을 넣어도 게임 상태를 지정할 수 없다. 별도의 애플리케이션 `ping`/`pong` 메시지는 구현되어 있지 않다.

### 4.8. 연결 거절·종료

| 상황 | 코드에서 지정하는 종료 코드 | reason |
| --- | --- | --- |
| 연결 시 쿠키 없음, 세션 무효·만료, 플레이어 없음 | `1008` | 지정하지 않음 |
| 연결 시 저장된 플레이어 레벨 오류 | `1008` | `INVALID_PLAYER_LEVEL` |
| 연결 시 플레이어 표시값의 숫자 범위 초과 | `1008` | `NUMERIC_LIMIT_EXCEEDED` |
| 연결 후 클릭·배치 클릭·레벨업 대상 플레이어 없음 | `1008` | `Player not found` |
| 인증·게임 처리 중 Redis 오류 | `1011` | 지정하지 않음 |
| 처리 중 `TimeoutError`·`OSError` 또는 방송 전송 실패로 연결 제거 | `1001` | 지정하지 않음 |

연결 수락 전 인증 실패는 `accept()` 전에 `close()`를 호출한다. ASGI 서버에서는 HTTP 핸드셰이크 거절(일반적으로 403)로 처리될 수 있어 브라우저에서 `1008`을 직접 받는다고 가정하면 안 된다. 연결 수락 전 Redis 오류도 정상 연결 후의 종료 프레임과 동일하게 관찰된다고 보장할 수 없다.

플레이어가 사라진 경우 `PLAYER_NOT_FOUND` JSON 결과를 보내지 않고 연결을 종료한다. 연결이 끊기면 새 연결의 `connected.player` 또는 HTTP 조회로 상태를 다시 맞춘다. 응답을 받지 못한 클릭·레벨업이 이미 처리되었을 수 있으며, 요청 중복 방지 기능이 없으므로 자동 재전송 시 중복 처리될 수 있다.

## 5. 프론트엔드 연동

현재 `frontend/src/game-service.ts`의 `createApiGameService()`는 HTTP와 WebSocket을 사용하는 API 어댑터다. 다음 매핑으로 서버 상태를 화면에 반영한다.

| 프론트엔드 필드·동작 | 백엔드 데이터·호출 |
| --- | --- |
| 내 플레이어 `id` | `player_id` |
| 플레이어 `name` | `nickname` |
| `gold` | `score` |
| `level` | `level` |
| `goldPerClick` | 서버의 `gold_per_click` |
| `nextGoldPerClick` | 서버의 `next_gold_per_click`. 레벨업 버튼에 현재 → 다음 보상을 표시 |
| `upgradeCost` | 서버의 `next_upgrade_cost`. 잔액이 부족하거나 `null`이면 업그레이드 비활성화. `null`이면 MAX·최대 레벨 도달 표시 |
| 최초 `getState()` | `POST /api/players` 생성·복원 후 WebSocket `connected`로 내 상태 초기화 |
| `click(count)` | WebSocket `click_batch`, `count`는 1~10의 정수 |
| `upgrade()` | WebSocket `levelup` |
| 화면 리더보드 | WebSocket `leaderboard.players`에서 상위 25명 표시 |
| 전체 플레이어 상위 25명 조회 | `GET /api/leaderboard?limit=25` (접속자 목록과 별도) |
| 리더보드 이름 | 각 항목의 `nickname` |
| 내 항목 구분 | 생성·복원된 내 `player_id`와 비교 |
| `avatar` | API 제공 없음. 프론트엔드 기본 이미지 사용 가능 |
| `ping` | 전용 API 필드는 없음. 어댑터에서 동작 요청부터 결과 수신까지의 경과 시간을 측정하며, 측정 전에는 `null` |

신규 플레이어는 0골드·레벨 1로 시작한다. 보상과 비용은 서버 응답 값을 사용하며, 클릭 성공 응답의 `accepted_clicks`와 최종 상태를 기준으로 예측 상태를 보정한다. `leaderboard` 방송은 요청 결과와 별도로 처리해야 하며, 접속자 목록의 도착을 `connected`와 같은 시점으로 가정하지 않는다.

### 브라우저 연결 예시

다음은 동일 origin에 `/api`, `/ws`가 연결된 환경의 최소 예시다. 실제 어댑터에서는 메시지별 상태 갱신과 연결 복구를 추가한다.

```ts
async function startGame(nickname: string) {
  const response = await fetch('/api/players', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'include',
    body: JSON.stringify({ nickname }),
  });
  if (!response.ok) throw new Error(`플레이어 준비 실패: ${response.status}`);
  const { created, player } = await response.json();

  const wsUrl = new URL('/ws', window.location.href);
  wsUrl.protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const socket = new WebSocket(wsUrl);
  socket.addEventListener('message', (event) => {
    const message = JSON.parse(event.data);
    // connected, leaderboard, click_result, click_batch_result, levelup_result, error 처리
    console.log(message);
  });

  // socket이 OPEN이고 connected를 받은 후 사용자 입력에 따라 전송:
  // socket.send(JSON.stringify({ type: 'click' }));
  // socket.send(JSON.stringify({ type: 'click_batch', count: 3 }));
  // socket.send(JSON.stringify({ type: 'levelup' }));
  return { created, player, socket };
}
```

## 6. 현재 구현 범위와 참고 근거

- 다른 플레이어 및 내 다른 연결에서 성공한 동작의 골드 변경은 접속자 리더보드 방송에 반영된다. 다만 내 전체 Player 상태를 모든 탭에 방송하지는 않는다. 다른 탭의 레벨·누적 클릭 수까지 동기화하려면 플레이어 조회 또는 재연결의 `connected.player`로 확인한다.
- 배치당 최대 10회 검증과 플레이어별 토큰 버킷 제한을 함께 적용한다. 단일 클릭도 동일한 제한을 사용한다. 성공한 클릭은 `app/repositories/scripts/click_batch.lua`에서 처리하며, `click.lua`는 현재 단일 클릭 API의 실행 경로가 아니다.
- 요청 중복 방지·요청 ID·클라이언트별 처리 확인 번호는 제공하지 않는다. 결과를 받기 전에 연결이 끊기면 서버 상태를 확인한 뒤 후속 입력을 처리한다.
- 로그아웃, 닉네임 변경, 플레이어 삭제, 내 정보 전용 조회, 개인 순위 조회, HTTP 클릭·레벨업 엔드포인트는 구현되어 있지 않다. 저장소의 `get_player_rank()`는 외부 API로 노출되지 않는다.
- 자동 문서 기본 경로는 `/docs`, `/redoc`, OpenAPI JSON은 `/openapi.json`이다. WebSocket 메시지 계약은 이 문서를 참고한다. 현재 HTTP 라우트에는 상세 응답 모델이 선언되어 있지 않다.

확인한 구현 파일:

- `app/main.py`: 앱 구성, Redis 연결, 토큰 충전 작업 수명주기, 데모 HTTP 경로.
- `app/api/http.py`: HTTP 경로, 요청 검증, 쿠키, HTTP 오류.
- `app/api/websocket.py`: 인증, 메시지 분기, 메시지 오류와 연결 종료.
- `app/services/connection_manager.py`: 프로세스 내 연결 관리, 접속자 중복 제거, 방송 및 전송 실패 처리.
- `app/services/token_refill.py`: 0.5초 간격 충전 작업과 지연 처리.
- `app/repositories/repository.py`: 응답 데이터 변환, 세션 기간, 전체·접속자 랭킹 조회, 클릭 경로 및 버킷 충전 호출.
- `app/balance.py`: Decimal 기반 레벨별 성장값 생성·캐시, 공통 응답 필드와 숫자 범위 확인, Lua 값 전달 지원.
- `app/repositories/scripts/create_player.lua`, `click_batch.lua`, `levelup.lua`, `refill_buckets.lua`: 초기 상태, 클릭 제한, 게임 규칙, 토큰 충전.
- `../frontend/src/game-service.ts`, `../frontend/vite.config.ts`: 현재 API 어댑터와 개발 서버 프록시 설정.

성장 규칙은 `backend`에서 `.venv/Scripts/python.exe -B -m unittest discover -s tests -v`로 검증할 수 있다. 테스트는 기본적으로 로컬 Redis DB 15의 고유 테스트 키만 사용하고 정리하며, `REDIS_TEST_URL`로 테스트 접속 주소를 지정할 수 있다. 수식은 1~40레벨에서 독립적인 정확한 분수 계산과 비교하고, 실제 Redis/Lua 및 HTTP·WebSocket ASGI 경로로 39→40, 동시 레벨업 시 40 초과 방지, 만렙 클릭·배치 보상, 거절 시 상태 유지와 응답 일치를 검증한다.
