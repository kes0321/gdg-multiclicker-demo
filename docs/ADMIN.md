# 단일 superadmin 설정·배포 가이드

관리자 기능은 **임의 경로 + 단일 비밀번호**를 사용하는 발표 데모용 기능이다.
비밀번호가 확인되면 `superadmin` 세션을 발급한다. 공개 회원가입이나 추가 권한 등급은 없다.
1차 기능은 접속 여부와 무관한 전체 유저 목록 및 **보유 골드·레벨 수정**이다.

## 1. 작업 전 상태와 복구 기준 확인

이번 기능 작업 전 추적 파일은 모두 커밋되어 있었고 작업 트리가 깨끗했다.

- 기준 커밋: `20c9fdd74290bda19aa0032ad069dd15947b35e5`
- 보존 브랜치: `backup/pre-superadmin-20261107`
- `.env`, Redis 데이터, 인증서, 빌드·테스트 결과는 Git 제외 대상이다. 코드 복구와 별도로 보관한다.

```powershell
git status --short
git show --no-patch --oneline backup/pre-superadmin-20261107
```

이전 코드를 확인하거나 별도 위치에서 사용하려면 새 디렉터리에 worktree를 만든다.
기존 작업 파일이나 Git 기록을 삭제하지 않는 방법이다. 대상 디렉터리는 새 경로를 선택한다.

```powershell
git worktree add ../multiclicker-before-admin backup/pre-superadmin-20261107
```

## 2. 비밀번호 해시와 임의 경로 생성

프로젝트 루트에서 다음 명령을 실행한다. 비밀번호는 **16~128자**이며 입력 문자는 화면에 표시되지 않는다.
코드·명령행 인수·`.env`에는 비밀번호 원문을 넣지 않는다. 강한 무작위 비밀번호 또는 긴 패스프레이즈를 사용한다.

Windows PowerShell:

```powershell
Set-Location backend
.venv/Scripts/python.exe -B -m app.admin.security
Set-Location ..
```

두 번 입력한 비밀번호가 일치하면 아래 형태의 세 줄을 출력한다. 이 값은 실제로 생성된 출력으로 대체한다.

```dotenv
ADMIN_PATH=/control-생성된24자리16진수
ADMIN_PASSWORD_HASH=scrypt:32768:8:3:생성된salt:생성된hash
ADMIN_SESSION_TTL_SECONDS=1800
```

`ADMIN_PATH`는 `/` 다음에 영문·숫자·`_`·`-`로 이루어진 8~64자 단일 경로다.
해시는 Compose의 변수 치환과 충돌하지 않는 콜론 형식이며 별도 패키지 없이 Python scrypt로 생성한다.
경로는 비밀번호와 별도로 관리하되, 경로를 알아도 세션 없이는 데이터 API를 사용할 수 없다.

## 3. 기존 `.env`에 세 항목 추가

프로젝트 루트의 기존 `.env`를 편집한다. **프로젝트 이름·포트·기존 인증서 설정은 유지**하고 위 세 줄을 추가한다.
특히 `COMPOSE_PROJECT_NAME`을 바꾸면 다른 Redis 볼륨을 사용하게 되므로 기존 값을 유지한다.

기존 `.env`를 보관하려면 새 이름으로 복사한다. 예를 들어 PowerShell에서:

```powershell
New-Item -ItemType Directory -Force backups | Out-Null
Copy-Item -LiteralPath .env -Destination ("backups/.env-before-admin-" + (Get-Date -Format yyyyMMdd-HHmmss))
```

`.env`와 `backups/`는 Git에서 제외된다. 비밀번호 해시도 공개 저장소·채팅·스크린샷에 올리지 않는다.
둘 중 하나라도 비어 있으면 관리자 라우터는 등록되지 않는다. 잘못된 경로·해시는 시작 시 오류로 거절한다.

## 4. 로컬에서 먼저 테스트

기존 개발 Redis를 시작한 뒤 두 터미널에서 실행한다. 사용 중인 Redis 포트는 기존 설정을 따른다.

터미널 A — 프로젝트 루트 기준:

```powershell
Set-Location backend
.venv/Scripts/python.exe -B -m uvicorn app.main:app --env-file ../.env --host 127.0.0.1 --port 8000
```

터미널 B — 프로젝트 루트 기준:

```powershell
Set-Location frontend
npm run dev -- --host 127.0.0.1 --port 5173 --strictPort
```

브라우저에서 `http://localhost:5173` 뒤에 생성된 `ADMIN_PATH`를 붙인다.
예: `http://localhost:5173/control-실제로생성된값`.
Vite는 루트 `.env`에서 경로만 읽으며 비밀번호 해시는 React 빌드에 전달하지 않는다.
로컬 HTTP에서는 `SESSION_COOKIE_SECURE=false`를 사용한다.
설정 변경 후에는 백엔드와 Vite를 다시 시작한다.

## 5. 서버에 배포

로컬 커밋은 자동으로 원격 저장소나 VM에 배포되지 않는다. 사용자가 로컬에서 커밋을 push하고,
VM에서 최신 코드를 pull한 뒤 VM의 `.env`에도 위 세 설정을 추가한다.

```sh
# 로컬 저장소
git push origin main

# VM의 기존 프로젝트 디렉터리
git status --short
git pull --ff-only origin main
# 기존 .env를 편집하여 생성한 ADMIN_PATH, ADMIN_PASSWORD_HASH, TTL 추가
docker compose config --quiet
docker compose up -d --build --wait
docker compose ps
```

이 배포는 기존 Redis named volume을 사용한다. 관리자 기능을 위해 기존 볼륨·컨테이너를 수동 삭제할 필요가 없다.
백엔드·Redis 포트를 외부에 공개하지 않고, 현재 Compose처럼 프런트 Nginx만 공개한다.

외부 접속에서는 먼저 [HTTPS 설정](HTTPS.md)을 적용한다. 기존 HTTPS 배포라면
`COMPOSE_FILE=compose.yaml:compose.https.yaml` 설정을 유지한다.
HTTPS override가 `SESSION_COOKIE_SECURE=true`를 적용한다. 인증서 경고를 우회하지 않는다.

호스트에 Python이 없는 VM에서 직접 해시를 생성하려면, 관리자 설정을 비워 둔 상태로 최신 이미지를
먼저 배포한 후 다음 명령을 실행하고, 출력값을 `.env`에 넣어 `docker compose up -d --wait`로 다시 적용한다.

```sh
docker compose exec backend python -B -m app.admin.security
```

접속 주소는 `https://gdgclicker.duckdns.org` 뒤에 `ADMIN_PATH`를 붙인 주소다.
화면의 비밀번호 입력란에는 **해시가 아니라 처음 정한 비밀번호 원문**을 입력한다.
로그인하면 `superadmin` 표시, 유저 목록, 저장 버튼이 나타난다.

## 6. 유저 조회·수정 방법

1. 첫 페이지에서 접속 여부와 무관한 유저를 확인한다. 다음·이전 페이지로 전체 목록을 탐색한다.
2. 해당 행의 보유 골드·레벨을 바꾸고 저장한다.
3. 골드는 `0..9007199254740991`, 레벨은 `1..40`의 정수만 허용된다.
4. 저장은 골드·레벨·랭킹 점수를 Redis Lua에서 함께 변경한다.
5. 조회 후 게임 클릭 등으로 해당 골드·레벨이 바뀌었다면 `409`로 거절한다. 클릭을 잠시 멈추고
   새로고침하여 현재 값을 확인한 뒤 다시 저장한다.
6. 닉네임·누적 클릭·생성 시각·클릭 토큰은 이 화면에서 변경하지 않는다. 파생 보상·비용은 서버가 계산한다.
7. 리더보드는 즉시 방송한다. 당사자 화면의 골드·레벨·클릭 보상은 다음 게임 응답이나 새로고침으로 동기화된다.
8. 작업을 마치면 로그아웃한다. 관리자 세션은 기본 30분 후 만료되며, 활동만으로 연장되지 않는다.

현재 목록은 Redis SCAN 커서를 사용한다. COUNT는 조회 힌트이므로 페이지마다 행 수가 다를 수 있고,
유저 생성·변경 중에는 목록이 고정된 스냅샷이 아니다. 안전한 정수로 읽을 수 없는 데이터는 저장 버튼을 비활성화한다.
서버 로그에는 superadmin의 대상 ID와 골드·레벨 변경 전후 값을 남긴다. 비밀번호·세션 토큰·CSRF 값은 기록하지 않는다.

```sh
docker compose logs --tail=100 backend
```

## 7. 비밀번호 변경·비활성화·오류 대응

- **비밀번호 변경:** 생성기를 다시 실행해 새 해시를 `.env`에 넣고 `docker compose up -d --wait`로 적용한다.
  새 설정으로 백엔드가 다시 시작되면 이전 관리자 세션은 사용할 수 없다. 경로를 바꾸려면 함께 갱신한다.
- **비활성화:** `ADMIN_PATH`와 `ADMIN_PASSWORD_HASH`를 빈 값으로 만든 후 위 명령을 실행한다.
- **401:** 비밀번호 오류, 세션 만료 또는 로그아웃 상태다. 다시 로그인한다.
- **403:** Origin 또는 CSRF 검증 실패다. 올바른 주소에서 페이지를 새로고침한다. 보호 검사를 해제하지 않는다.
- **409:** 조회 이후 유저 상태가 바뀌었다. 새로고침 후 다시 확인한다.
- **422:** 입력 범위 또는 자료형을 확인한다.
- **429:** 동일 IP의 로그인 시도가 제한되었다. 5분 후 재시도한다.
- **503:** Redis 상태와 백엔드 로그를 확인한다. 인증 저장소 장애 시에도 관리자 접근은 허용하지 않는다.
- **관리자 대신 게임 화면이 보임:** 실제 경로와 `.env`를 확인하고, 프런트·백엔드 모두 새 설정으로 재생성됐는지 확인한다.

## 8. 검증 범위

비밀번호 해시, 설정 누락 시 비활성화, 일반 게임 쿠키·위조 쿠키 차단, 로그인 시도 제한,
세션 만료·폐기·비밀번호 회전, CSRF·Origin 검사, 전체 유저 페이지 조회, 입력 범위,
Lua 변경과 랭킹 반영, 동시 클릭 충돌, Redis 장애 시 거절을 자동 테스트한다.
Docker/Nginx 경유 실제 HTTP에서도 로그인·쿠키·조회·수정·로그아웃·위조 X-Forwarded-For의 제한 우회 방지를 확인한다.

```powershell
# 테스트 전용 Redis를 사용한다. 예시는 전용 컨테이너의 16379, DB 15다.
$env:REDIS_TEST_URL='redis://127.0.0.1:16379/15'
Set-Location backend
.venv/Scripts/python.exe -B -m unittest discover -s tests -v
```

운영 유저의 데이터 수정은 이 검증에 사용하지 않는다. 관리자 화면은 서버 HTML/CSS/JavaScript이며,
추가 프런트 프레임워크나 인증 서비스는 필요하지 않다.
