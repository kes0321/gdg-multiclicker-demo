# Multiclicker

## 1. 개요

**게임 서버를 개발하는 과정에서 마주하는 고민들**에 관한 발표를 위해 개발한 프로젝트이다.
여러 사용자가 함께 참여하는 클릭 게임으로, 클릭 처리·레벨업·실시간 랭킹을 구현했다.

## 2. 기술 스택

| 구분 | 기술 | 용도 |
| --- | --- | --- |
| 프론트엔드 | React, TypeScript, Vite | 게임 화면 및 클라이언트 빌드 |
| 백엔드 | Python, FastAPI, Uvicorn | HTTP API 및 비동기 서버 |
| 실시간 통신 | WebSocket | 클릭·레벨업 요청과 랭킹 방송 |
| 데이터 저장·처리 | Redis, Lua | 플레이어·세션·랭킹 저장, 게임 상태의 원자적 갱신 |
| 웹 서버 | Nginx | 정적 파일 제공, API·WebSocket 프록시, HTTPS 처리 |
| 배포 | Docker, Docker Compose | 프론트엔드·백엔드·Redis 빌드 및 실행 |

## 3. 배포 및 재배포

백엔드와 프론트엔드 이미지를 각각 빌드하고, Redis 공식 이미지와 함께 총 3개의 컨테이너를 Docker Compose로 배포한다.
Git과 Docker Engine(또는 Docker Desktop), Docker Compose v2가 필요하다.

### 최초 배포

```sh
git clone https://github.com/kes0321/gdg-multiclicker-demo.git
cd gdg-multiclicker-demo
cp .env.example .env
docker compose config --quiet
docker compose up -d --build --wait
```

PowerShell에서는 복사 명령으로 `Copy-Item .env.example .env`를 사용한다.
기본 접속 주소는 `http://localhost`이며, 포트는 `.env`의 `HTTP_PORT`로 변경한다.
HTTPS 배포는 [HTTPS 설정](docs/HTTPS.md), 관리자 기능은 [관리자 설정](docs/ADMIN.md)을 따른다.

### 재배포

```sh
git pull --ff-only origin main
docker compose config --quiet
docker compose up -d --build --wait
```

기존 `.env`와 `COMPOSE_PROJECT_NAME`을 유지한다. HTTPS 설정이 `.env`에 적용되어 있으면 같은 명령으로 재배포한다.

## 4. 운영 명령어

```sh
# 컨테이너 상태 확인
docker compose ps

# 전체 로그 / 백엔드 로그 확인
docker compose logs -f --tail=100
docker compose logs -f --tail=100 backend

# 백엔드 재시작
docker compose restart backend

# 중지 / 다시 시작
docker compose stop
docker compose start

# 컨테이너·네트워크 제거 (Redis 볼륨 유지)
docker compose down

# Redis 연결 / 영속화 상태 확인
docker compose exec redis redis-cli ping
docker compose exec redis redis-cli INFO persistence

# Nginx 설정 검사 / 다시 읽기
docker compose exec frontend nginx -t
docker compose exec frontend nginx -s reload
```

서비스 상태 확인:

```sh
curl -f http://localhost/api/health
```

정상 응답은 `{"status":"ok"}`이다. HTTPS 또는 포트를 변경한 환경에서는 주소를 맞춘다.

- Redis 데이터는 `redis_data` 볼륨에 저장되며 AOF와 RDB로 영속화한다. `docker compose down -v`는 볼륨까지 삭제하므로 일반 종료·재배포에 사용하지 않는다.
- 현재 백엔드는 **컨테이너 1개·Uvicorn worker 1개**로 운영한다. 연결 목록과 토큰 충전 작업이 프로세스에 있어 다중 실행을 지원하지 않는다.
