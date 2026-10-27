# 프론트엔드 연동용 API 명세

작성일: 2026-10-27  
기준 커밋: `43bee80db0b971ffa92958f8cefa7cfe89ffdc4c` (`feat: batch player click requests`)

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
- 현재 백엔드에는 CORS 미들웨어가 없고, 프론트엔드 `vite.config.ts`에도 API·WebSocket 프록시가 없다. 서로 다른 origin에서 바로 호출하려면 CORS와 쿠키 조건을 맞추는 별도 설정이 필요하다. 동일 origin에서 `/api`와 `/ws`를 백엔드로 전달하는 구성이 연동에 적합하다. `credentials` 설정만으로 CORS 또는 `SameSite` 제한이 해소되지는 않는다.

### 권장 호출 순서

1. 닉네임을 입력받아 `POST /api/players`를 호출한다.
2. 응답의 `player`로 내 상태를 초기화하고, 쿠키 발급이 완료된 후 `/ws`에 연결한다.
3. `connected` 메시지의 `player`로 최신 상태를 반영한다.
4. 클릭은 `click` 또는 `click_batch`, 업그레이드는 `levelup` 메시지로 전송한다.
5. 서버 응답의 최종 `score`, `level`, `total_clicks`를 화면 상태에 반영한다. 응답에 없는 필드는 기존 값을 유지한다.
6. 리더보드는 `GET /api/leaderboard`로 별도 갱신한다. 서버가 랭킹 변경을 방송하지 않으므로 필요하면 프론트엔드에서 주기적으로 조회한다. 조회 주기는 API에 규정되어 있지 않다.

## 2. 데이터 모델과 게임 규칙

### Player

| 필드 | JSON 타입 | 의미 |
| --- | --- | --- |
| `player_id` | string | 서버가 생성하는 ID. 현재 형식은 `p` + UUID의 32자리 16진수 |
| `nickname` | string | 양끝 공백을 제거한 닉네임, 1~20자 |
| `score` | integer | 현재 보유 골드. 누적 획득량이 아니며 레벨업 시 감소 |
| `level` | integer | 현재 레벨, 최초 1 |
| `total_clicks` | integer | 누적 처리된 클릭 수, 최초 0 |
| `created_at_ms` | integer | 생성 시각, Unix epoch 밀리초 |

```json
{
  "player_id": "p0123456789abcdef0123456789abcdef",
  "nickname": "빵굽는고양이",
  "score": 0,
  "level": 1,
  "total_clicks": 0,
  "created_at_ms": 1793062800000
}
```

### 골드와 레벨 계산

- 단일 클릭: `획득 골드 = 처리 시점의 level`, `total_clicks += 1`.
- 배치 클릭: `획득 골드 = 처리 시점의 level × count`, `total_clicks += count`.
- 레벨업 비용: `현재 level × 100`. 잔액이 충분하면 비용을 차감하고 레벨을 1 올린다. `total_clicks`는 변하지 않는다.
- 예: 레벨 1에서 100골드를 쓰면 레벨 2가 되고, 다음 비용은 200골드이다.
- 랭킹은 현재 보유 골드 내림차순이다. 레벨업으로 골드를 사용하면 순위가 내려갈 수 있다.
- 클릭과 레벨업은 각각 Redis Lua 스크립트에서 상태와 랭킹을 함께 갱신한다.

## 3. HTTP API

| 메서드 | 경로 | 용도 | 세션 필요 |
| --- | --- | --- | --- |
| `POST` | `/api/players` | 플레이어 생성 또는 세션 복원 | 선택 |
| `GET` | `/api/players/{player_id}` | 플레이어 상세 조회 | 아니요 |
| `GET` | `/api/leaderboard` | 상위 랭킹 조회 | 아니요 |
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
    "created_at_ms": 1793062800000
  }
}
```

복원 응답은 동일한 구조이며 `created`가 `false`이고 `player`는 기존 저장 상태이다. 복원 요청에서도 유효한 `nickname` 본문이 필수다. 복원 시 요청의 닉네임으로 기존 닉네임을 변경하지 않는다. 세션이 만료되었거나 연결된 플레이어가 없거나 세션 연장에 실패하면 신규 생성 경로로 진행한다.

오류:

| 상태 코드 | `detail` 또는 의미 |
| --- | --- |
| `422` | 닉네임 누락, 타입·길이 오류, 잘못된 JSON 등 요청 검증 실패 |
| `500` | `생성된 플레이어를 조회할 수 없습니다.` |
| `503` | `Redis 처리 실패` |

### 3.2. 플레이어 조회

`GET /api/players/{player_id}`

- `player_id`: 필수 경로 문자열.
- 성공: `200 OK`, 본문은 **Player 객체 자체**이다. `{ "player": ... }`로 감싸지 않는다.
- 세션 없이 다른 플레이어도 조회할 수 있다.
- 오류: `404` / `플레이어가 없습니다.`, `503` / `플레이어 조회 실패`.

### 3.3. 리더보드 조회

`GET /api/leaderboard?limit=25`

| 쿼리 | 타입 | 기본값 | 허용 범위 |
| --- | --- | --- | --- |
| `limit` | integer | 10 | 1~100 |

성공: `200 OK`

```json
{
  "players": [
    {"rank": 1, "player_id": "p0123456789abcdef0123456789abcdef", "score": 1500},
    {"rank": 2, "player_id": "pabcdef0123456789abcdef0123456789", "score": 900}
  ]
}
```

- 각 항목은 `rank`(integer, 1부터 시작), `player_id`(string), `score`(integer)만 포함한다.
- 플레이어가 없으면 `{"players": []}`이다. 전체 인원과 내 순위는 별도 제공하지 않는다.
- 동점이어도 공동 순위를 부여하지 않고 1, 2, 3처럼 연속 순위를 반환한다. 현재 `ZREVRANGE` 동작에 따라 동점은 `player_id`의 역 사전순으로 정렬된다.
- 닉네임이 필요하면 각 ID로 플레이어 상세를 조회하고 캐시할 수 있다.
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

명시적으로 처리한 HTTP 오류는 다음 형식이다.

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
    "created_at_ms": 1793062800000
  }
}
```

모든 클라이언트 메시지는 **텍스트 JSON 객체**로 전송한다. 서버는 연결별로 메시지를 순서대로 처리한다. 요청 ID와 응답 ID는 제공하지 않으므로 요청과 응답의 대응이 필요하면 클라이언트에서 순서를 관리한다.

### 4.2. 단일 클릭

클라이언트 → 서버:

```json
{"type": "click"}
```

서버 → 클라이언트, 레벨 1에서 첫 클릭한 예:

```json
{
  "type": "click_result",
  "player_id": "p0123456789abcdef0123456789abcdef",
  "score": 1,
  "level": 1,
  "total_clicks": 1,
  "gold_gained": 1
}
```

`score`, `level`, `total_clicks`는 처리 후 최종 상태이며 `gold_gained`는 이번 요청의 획득 골드다. 단일 클릭 응답에는 `success` 필드가 없다.

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
  "score": 3,
  "level": 1,
  "total_clicks": 3,
  "gold_gained": 3,
  "accepted_clicks": 3
}
```

`accepted_clicks`는 이번에 처리한 클릭 수이며 현재 구현은 유효한 요청의 `count` 전체를 처리한다. `gold_gained`는 클릭 한 번의 골드가 아닌 배치 전체의 획득량이다.

입력 오류 응답:

```json
{
  "type": "click_batch_result",
  "success": false,
  "code": "INVALID_BATCH_COUNT",
  "message": "count must be an integer between 1 and 10"
}
```

입력 오류는 상태를 변경하지 않으며 연결을 유지한다.

### 4.4. 레벨업

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
  "gold_spent": 100
}
```

`gold_spent`는 이번에 사용한 골드다. 성공 응답에는 `total_clicks`와 다음 업그레이드 비용이 없으므로 기존 클릭 수를 유지하고 다음 비용은 `level × 100`으로 계산한다.

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

### 4.5. 공통 메시지 오류

| 입력 상황 | 서버 응답 | 연결 |
| --- | --- | --- |
| JSON 구문 오류 | `{"type":"error","code":"INVALID_JSON"}` | 유지 |
| JSON 배열·숫자·문자열·불리언·null 등 객체가 아닌 값 | `{"type":"error","code":"INVALID_MESSAGE"}` | 유지 |
| `type` 누락 또는 지원하지 않는 값 | `{"type":"error","code":"UNKNOWN_MESSAGE_TYPE"}` | 유지 |

`click`과 `levelup`은 `type`만으로 처리하며, 메시지에 임의의 점수나 레벨을 넣어도 게임 상태를 지정할 수 없다. 별도의 애플리케이션 `ping`/`pong` 메시지는 구현되어 있지 않다.

### 4.6. 연결 거절·종료

| 상황 | 코드에서 지정하는 종료 코드 | reason |
| --- | --- | --- |
| 연결 시 쿠키 없음, 세션 무효·만료, 플레이어 없음 | `1008` | 지정하지 않음 |
| 연결 후 클릭·배치 클릭·레벨업 대상 플레이어 없음 | `1008` | `Player not found` |
| Redis 처리 오류 | `1011` | `Redis operation failed` |

연결 수락 전 인증 실패는 `accept()` 전에 `close()`를 호출한다. ASGI 서버에서는 HTTP 핸드셰이크 거절(일반적으로 403)로 처리될 수 있어 브라우저에서 `1008`을 직접 받는다고 가정하면 안 된다. 연결 수락 전 Redis 오류도 정상 연결 후의 종료 프레임과 동일하게 관찰된다고 보장할 수 없다.

플레이어가 사라진 경우 `PLAYER_NOT_FOUND` JSON 결과를 보내지 않고 연결을 종료한다. 연결이 끊기면 새 연결의 `connected.player` 또는 HTTP 조회로 상태를 다시 맞춘다. 응답을 받지 못한 클릭·레벨업이 이미 처리되었을 수 있으며, 요청 중복 방지 기능이 없으므로 자동 재전송 시 중복 처리될 수 있다.

## 5. 기존 프론트엔드와의 매핑

현재 `frontend/src/game-service.ts`는 로컬 목업이다. 연동 어댑터에서 다음 매핑을 적용한다.

| 프론트엔드 필드·동작 | 백엔드 데이터·호출 |
| --- | --- |
| 내 플레이어 `id` | `player_id` |
| 플레이어 `name` | `nickname` |
| `gold` | `score` |
| `level` | `level` |
| `goldPerClick` | 현재 `level` |
| `upgradeCost` | 현재 `level × 100` |
| 최초 `getState()` | 생성·복원 응답 및 WebSocket `connected`, 리더보드 조회 조합 |
| `click()` | WebSocket `click` 또는 클릭 누적 후 `click_batch` |
| `upgrade()` | WebSocket `levelup` |
| 25명 리더보드 | `GET /api/leaderboard?limit=25` |
| 리더보드 이름 | 각 `player_id`의 상세 조회에서 `nickname` 확보 |
| 내 항목 구분 | 생성·복원된 내 `player_id`와 비교 |
| `avatar` | API 제공 없음. 프론트엔드 기본 이미지 사용 가능 |
| `ping` | API 제공 없음. 현재 목업의 24ms는 서버 측정값이 아님 |

목업의 업그레이드 비용 증가식 `ceil(이전 비용 × 1.65)`는 서버 규칙과 다르다. 실제 연동에서는 `level × 100`을 사용해야 한다. 목업 초기 1,240골드와 달리 서버 신규 플레이어는 0골드로 시작한다.

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
    // connected, click_result, click_batch_result, levelup_result, error 처리
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

- 다른 플레이어의 변경 사항이나 내 다른 연결의 변경 사항을 WebSocket으로 방송하지 않는다. 여러 탭을 열면 별도 조회 등으로 상태 동기화가 필요하다.
- 배치당 최대 10회 제한은 있지만 클릭 빈도를 제한하는 로직은 현재 클릭 처리 경로에 없다. 플레이어 생성 시 토큰 버킷 데이터를 만들지만 클릭 시 사용하지 않으므로 초당 제한이나 `RATE_LIMITED` 응답을 가정하지 않는다.
- 로그아웃, 닉네임 변경, 플레이어 삭제, 내 정보 전용 조회, 개인 순위 조회, HTTP 클릭·레벨업 엔드포인트는 구현되어 있지 않다. 저장소의 `get_player_rank()`는 외부 API로 노출되지 않는다.
- 자동 문서 기본 경로는 `/docs`, `/redoc`, OpenAPI JSON은 `/openapi.json`이다. WebSocket 메시지 계약은 이 문서를 참고한다. 현재 HTTP 라우트에는 상세 응답 모델이 선언되어 있지 않다.

확인한 구현 파일:

- `app/main.py`: 앱 구성, Redis 연결, 데모 HTTP 경로.
- `app/api/http.py`: HTTP 경로, 요청 검증, 쿠키, HTTP 오류.
- `app/api/websocket.py`: 인증, 메시지 분기, 메시지 오류와 연결 종료.
- `app/repositories/repository.py`: 응답 데이터 변환, 세션 기간, 랭킹 조회.
- `app/repositories/scripts/create_player.lua`, `click.lua`, `click_batch.lua`, `levelup.lua`: 초기 상태와 게임 규칙.
- `../frontend/src/game-service.ts`, `../frontend/vite.config.ts`: 기존 목업 구조와 현재 개발 서버 설정.

참고한 Git 기록:

| 커밋 | 내용 | 명세 반영 |
| --- | --- | --- |
| `bc1e91b` | `init: backend` | 초기 HTTP 조회·상태 확인 구조 |
| `8ea5728` | `feat: create player` | 플레이어 생성, 쿠키 세션 복원 |
| `514909e` | `feat: player click` | WebSocket 연결과 단일 클릭 |
| `3f8a01a` | `feat: player levelup` | 레벨업 비용·성공·잔액 부족 처리 |
| `43bee80` | `feat: batch player click requests` | 배치 클릭 1~10회와 입력 검증 |

이 명세는 코드와 Git 기록을 정적으로 확인해 작성했으며, 실행 서버나 Redis를 통한 동작 검증은 수행하지 않았다.
