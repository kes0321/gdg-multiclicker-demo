# Multiclicker 로컬 Docker 배포

React/Vite 빌드 결과물은 Nginx 이미지에 포함된다. Compose는 `frontend`,
`backend`, `redis` 세 서비스만 실행한다. 기본은 로컬 HTTP이며,
`gdgclicker.duckdns.org` HTTPS 전환은 [HTTPS 배포 절차](docs/HTTPS.md)를 따른다.
실제 VM 접속·배포·공인 인증서 발급은 사용자가 수행한다.

```text
브라우저 → http://localhost:${HTTP_PORT:-80} → frontend:80 (Nginx + React dist)
                                             ├─ /api/... → backend:8000/api/...
                                             └─ /ws      → backend:8000/ws
                                                              └─ redis:6379
                                                                   └─ redis_data:/data
```

## 필요한 도구와 첫 실행

- Linux 컨테이너를 실행할 Docker Engine + Docker Compose v2 이상. Windows에서는 Docker Desktop의 Linux 컨테이너 모드를 사용한다.
- Git. Docker 배포만 할 때 호스트의 Python/Node/npm은 필요하지 않다.
- 이미지 빌드 시 Docker Hub, PyPI, npm registry에 접근할 수 있어야 한다.
- 직접 개발하거나 검증 스크립트를 실행할 때 Python **3.14.7**, 프런트엔드 직접 개발에는 Node **24.21.0**과 npm을 사용한다.
- 백엔드는 기존 가상환경의 실행 패키지 버전을 `backend/requirements.txt`로 정리했다. Windows 전용 패키지·로컬 경로 의존성은 없다. 프런트엔드는 기존 npm과 `package-lock.json`을 유지한다.

프로젝트 루트에서 실행한다.

```sh
cp .env.example .env
docker compose config --quiet
docker compose up -d --build
docker compose ps
```

PowerShell의 복사 명령은 `Copy-Item .env.example .env`이다. `.env`가 없어도 기본값으로 기동한다.
브라우저에서 **http://localhost**에 접속한다. 준비 상태까지 기다리려면 마지막 시작 명령에 `--wait`를 붙인다.
상태 확인: `curl http://localhost/api/health` → `{"status":"ok"}`.

| 환경변수 | 기본값 | 의미 |
| --- | --- | --- |
| `COMPOSE_PROJECT_NAME` | `multiclicker` | 컨테이너·네트워크·볼륨의 프로젝트 이름. 바꾸면 별도 데이터 볼륨을 사용한다. |
| `HTTP_PORT` | `80` | Nginx 호스트 공개 포트. 예: `8080`이면 `http://localhost:8080`. |
| `HTTPS_PORT` | `443` | HTTPS override에서 추가 공개하는 TLS 포트. 운영에서는 443 사용. |
| `ACME_WEBROOT` | `./.acme-webroot` | 호스트의 HTTP-01 검증 파일 디렉터리. 프런트에 읽기 전용 마운트. |
| `LETSENCRYPT_DIR` | `/etc/letsencrypt` | HTTPS override에서 마운트하는 호스트 인증서 디렉터리. |
| `COMPOSE_FILE` | 미설정 | Linux VM에서 `compose.yaml:compose.https.yaml`로 설정하면 일반 명령도 HTTPS 모드 사용. |
| `SESSION_COOKIE_SECURE` | `false` | `true`/`1`이면 생성·갱신 쿠키에 Secure 적용, `false`/`0`이면 로컬 HTTP 지원. |
| `REDIS_HOST` | `localhost` | 백엔드 직접 실행 기본값. Compose는 항상 `redis`로 덮어쓴다. |
| `REDIS_PORT` | `6379` | 백엔드 직접 실행 기본값. Compose는 항상 `6379`로 덮어쓴다. |
| `BACKEND_URL` | `http://localhost:8000` | Vite 개발 프록시 전용. 필요 시 `frontend/.env.local`에 설정. |

루트 `.env`는 Compose 치환에 사용한다. 백엔드에 전달되는 값은 `compose.yaml`의
`environment`가 결정한다. Python을 직접 실행하면 환경변수를 설정하거나 Uvicorn의 `--env-file`로 파일을 읽는다.
`.env`, `.venv`, `node_modules`, 캐시·빌드 결과는 Git/빌드 컨텍스트에서 제외한다.
기존에 추적되던 Python 캐시와 TypeScript 빌드 캐시도 Git 추적에서 제거했다.

## 운영 명령과 서비스

```sh
docker compose up -d --build              # 최초 빌드·시작 또는 변경 반영
docker compose ps                        # 상태와 공개 포트
docker compose logs -f --tail=100         # 전체 로그
docker compose logs -f backend redis     # 선택 서비스 로그
docker compose stop                      # 컨테이너를 남기고 종료
docker compose start                     # 중지된 컨테이너 시작
docker compose down                      # 컨테이너·네트워크 제거, named volume 유지
docker compose up -d --build --wait       # 다시 빌드하고 상태 확인까지 대기
docker compose up -d --force-recreate --wait # 컨테이너만 재생성, 데이터 유지
```

기본 컨테이너 이름은 `multiclicker-frontend-1`, `multiclicker-backend-1`,
`multiclicker-redis-1`이다. 고정 `container_name`을 지정하지 않아 다른 프로젝트 이름으로 격리 실행할 수 있다.
내부 통신은 Compose DNS의 `backend:8000`, `redis:6379`를 사용한다.
호스트에는 프런트엔드 HTTP 포트만 공개하며 HTTPS 모드에서는 443도 공개한다.
backend와 Redis는 공개하지 않아 기존 `docker run` Redis의 `127.0.0.1:6379`와 충돌하지 않는다.
백엔드는 Redis healthcheck 성공 후 시작하고 자체 `/api/health`가 성공한 뒤 Nginx를 시작한다.
세 서비스 모두 `restart: unless-stopped`이다.

백엔드 빌드 컨텍스트는 `backend/`이고 실행 모듈은 `app.main:app`이다.
`0.0.0.0:8000`, **컨테이너 하나·Uvicorn worker 하나**, `--reload` 없이 실행한다.
접속자 목록은 프로세스 메모리에 있으며 토큰 충전은 해당 프로세스의 백그라운드 작업이다.
`--workers` 증가, `--scale backend=2`, 동일 Redis에 연결하는 별도 앱 프로세스 추가는 하지 않는다.
방송을 공유하고 토큰 충전을 단일 작업으로 분리하기 전에는 수평 확장할 수 없다.

프런트엔드는 `npm ci` 후 `npm run build`로 생성한 `dist/`만 최종 Nginx 이미지로 복사한다.
브라우저 코드는 `/api/...`와 현재 페이지 호스트의 `/ws`를 사용하며 HTTP는 `ws`, HTTPS는 `wss`를 선택한다.
기존 Vite 프록시도 그대로 이 경로를 지원한다. API 경로는 Nginx에서 보존하고 API 404를 SPA로 처리하지 않는다.
WebSocket은 Upgrade 헤더와 1시간 읽기·쓰기 타임아웃을 사용한다.
Nginx는 Docker DNS를 주기적으로 다시 조회해 백엔드 재생성 후 주소 변경을 반영한다.
쿠키는 `HttpOnly; SameSite=lax; Path=/`의 host-only 쿠키이며 Nginx가 Cookie/Set-Cookie를 그대로 전달한다.
`/nginx-health`는 HTTP/HTTPS 전환과 무관하게 Nginx 자체 준비 상태를 확인한다.
Redis를 포함한 앱 상태는 `/api/health`를 사용한다.

## Redis 영속화와 데이터 보존

Redis 공식 이미지 **`redis:8.10.2`**를 사용한다. 이 버전은 기존 개발 컨테이너에서
`redis-server --version`으로 확인한 버전과 같다. `latest`를 사용하지 않는다.
기존 컨테이너·볼륨·데이터는 변경하지 않으며, **기존 개발 데이터는 새 Compose 볼륨에 자동 이관되지 않는다.**

기본 볼륨은 **`multiclicker_redis_data`**이고 Redis의 `/data`에 연결된다.
신규 빈 볼륨은 AOF 활성화, `appendfsync everysec`, RDB `save 60 1`로 시작한다.
대략 1초 내의 쓰기는 비정상 전원 종료 때 유실될 수 있으므로 별도 백업도 필요하다.
컨테이너 재생성·이미지 재빌드·일반 `docker compose down`은 볼륨을 유지한다.

`docker compose down -v`는 해당 프로젝트의 named volume까지 삭제한다.
`docker volume rm multiclicker_redis_data`도 영구 데이터 삭제다. 둘 다 일반 종료나 재배포에 사용하지 않는다.
프로젝트 이름을 변경하면 이전 볼륨은 유지되지만 새 프로젝트에서 자동 연결하지 않는다.

### 기존 개발 Redis 백업

이 절차는 필요할 때 사용자가 실행한다. 이번 로컬 검증에서는 기존 개발 Redis에 백업·쓰기 명령도 실행하지 않는다.
실제 컨테이너 이름이 `redis`가 아니면 아래 이름을 바꾼다. 원본 컨테이너 삭제/재시작이나 볼륨 변경은 필요 없다.

```sh
mkdir backups
docker exec redis redis-cli --rdb /tmp/multiclicker-export.rdb
docker cp redis:/tmp/multiclicker-export.rdb ./backups/development.rdb
```

`redis-cli --rdb`로 일관된 시점의 RDB를 얻어 호스트에 복사한다.
백업 완료 이후 쓰기는 포함되지 않으므로 실제 이관 시점에는 애플리케이션 쓰기를 멈춘다.
파일에는 세션 토큰도 포함되므로 `backups/`는 Git에서 제외하며 VM 전달·보관 접근을 제한한다.
현재 기존 컨테이너에는 `/data` 볼륨 마운트가 없는 것으로 확인되어 컨테이너를 삭제하기 전에 별도 백업이 특히 필요하다.

### RDB 최초 복원: 새 볼륨에서 RDB 로딩 후 AOF 전환

**빈 새 프로젝트/볼륨에서만 수행한다.** 예시는 별도 프로젝트 `multiclicker-import`를 사용한다.
`docker volume inspect multiclicker-import_redis_data`로 먼저 확인하고, 이미 있으면 다른 새 이름을 선택한다.
오류 `No such volume`이면 아직 없는 볼륨이다. 기존 볼륨에 덮어쓰거나 AOF 파일을 지우지 않는다.

AOF와 RDB가 함께 있으면 Redis는 AOF를 우선 로딩한다.
따라서 AOF가 켜진 서버의 `/data`에 `dump.rdb`만 복사하고 재시작하는 방법은 사용하지 않는다.
`compose.restore.yaml`은 최초 로딩 때만 AOF를 끄는 override이다.

```sh
# 아래 모든 단계는 같은 새 프로젝트 이름과 새 볼륨을 사용한다.
docker compose -p multiclicker-import -f compose.yaml -f compose.restore.yaml create redis
docker compose -p multiclicker-import -f compose.yaml -f compose.restore.yaml cp ./backups/development.rdb redis:/data/dump.rdb
docker compose -p multiclicker-import -f compose.yaml -f compose.restore.yaml up -d --wait redis
docker compose -p multiclicker-import exec redis redis-cli DBSIZE
docker compose -p multiclicker-import exec redis redis-cli INFO keyspace
docker compose -p multiclicker-import exec redis redis-cli CONFIG SET appendonly yes
docker compose -p multiclicker-import exec redis redis-cli INFO persistence
```

원본의 예상 키 수/DB와 로딩된 내용을 확인한다. `INFO persistence`에서
`aof_enabled:1`, `aof_rewrite_in_progress:0`, `aof_rewrite_scheduled:0`,
`aof_last_bgrewrite_status:ok`, `aof_last_write_status:ok`가 될 때까지 기다리고 반복 조회한다.
AOF 생성이 성공하기 전에는 재시작하지 않는다. 실패하면 로그와 파일 권한을 확인하며 다음 단계로 진행하지 않는다.
다음은 **override 없이** 정상 AOF 설정으로 전환하는 명령이다.

```sh
docker compose -p multiclicker-import up -d --force-recreate --wait redis
docker compose -p multiclicker-import exec redis redis-cli DBSIZE
docker compose -p multiclicker-import exec redis redis-cli INFO persistence
docker compose -p multiclicker-import up -d --build --wait
```

이 프로젝트를 계속 쓴다면 `.env`의 `COMPOSE_PROJECT_NAME=multiclicker-import`로 맞춘다.
`HTTP_PORT`가 기존 Compose 프런트와 겹치면 실행 전에 다른 포트로 변경한다.
복원 override를 일반 운영 시작에 다시 추가하지 않는다. 런타임 `CONFIG SET`은 파일을 수정하지 않지만,
정상 Compose 명령이 매번 `--appendonly yes`를 지정하므로 다음 기동에도 AOF가 유지된다.
공식 근거: [Redis persistence](https://redis.io/docs/latest/operate/oss_and_stack/management/persistence/).

## 직접 개발과 검증

Windows 직접 실행 예시 (이미 실행 중인 개발 Redis `localhost:6379` 사용):

```powershell
cd backend
py -3.14 -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt
.venv/Scripts/python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1
# 별도 터미널
cd frontend
npm ci
npm run dev
```

개발 앱도 토큰을 충전하므로 같은 Redis/DB를 대상으로 백엔드를 여러 개 띄우지 않는다.
루트 `.env`를 직접 실행에 적용하려면 backend에서 Uvicorn에 `--env-file ../.env`를 추가한다.
Compose 전용 Redis는 호스트 포트가 없으므로 직접 개발 앱과 별도 데이터 저장소다.

이번 Compose에서만 새 테스트 플레이어를 만들어 Nginx·쿠키·WebSocket·게임 동작을 확인한다.
프로젝트 루트에서 실행한다. 스크립트는 지정한 주소에 데이터를 생성하므로 기존 개발 서버 주소를 사용하지 않는다.

```powershell
backend/.venv/Scripts/python.exe scripts/verify_compose.py --url http://localhost
docker compose up -d --force-recreate --wait
backend/.venv/Scripts/python.exe scripts/verify_compose.py --url http://localhost --check-persistence
node --test frontend/tests/*.test.mjs
```

Linux에서는 요구사항을 설치한 Python으로 `python scripts/verify_compose.py ...`를 실행한다.
검증 상태와 테스트 쿠키는 Git에서 제외한 `.deployment-test/state.json`에 저장한다.
검증 스크립트는 프런트·SPA·API 404, 플레이어 생성과 재접속, 두 연결 사이 방송,
클릭·배치·레벨업, 토큰 소진/거절/충전, 컨테이너 재생성 후 플레이어·세션 보존을 확인한다.
HTTP에서는 Secure 쿠키가 없는지, HTTPS에서는 Secure 쿠키와 WSS 연결까지 검증한다.
로컬 테스트 인증서는 `--ca-file PATH`로 신뢰할 수 있으며 인증서·호스트 검증은 계속 적용한다.
기존 Python 성장 곡선/Redis/Lua 테스트도 Compose의 Redis DB 15로 격리 실행할 수 있다.

```sh
docker compose run --rm --no-deps -e REDIS_TEST_URL=redis://redis:6379/15 -v ./backend/tests:/tests:ro backend python -B -m unittest discover -s /tests -v
```

이 명령은 서버나 충전 작업을 추가 실행하지 않고 고유 테스트 키만 생성·정리한다.
이번 작업에서는 `down -v`, prune, 기존 개발 데이터 이관을 실행하지 않는다.

## 이후 Linux VM 작업과 HTTPS

1. VM에 Docker Engine과 Compose 플러그인, Git을 설치하고 Docker 서비스를 활성화한다.
2. 저장소를 clone한 뒤 루트에서 `.env.example`을 `.env`로 복사한다. 데이터 복원이 필요하면 위 최초 복원 절차를 먼저 수행한다.
3. `HTTP_PORT`, `COMPOSE_PROJECT_NAME`, 쿠키 설정을 선택한 뒤 `docker compose config --quiet`, `docker compose up -d --build --wait`를 실행한다.
4. VM 내부에서 `/api/health`, 브라우저에서 프런트·세션·WebSocket을 확인한다. 클라우드 보안 그룹/방화벽에는 선택한 HTTP 포트만 연다. backend 8000/Redis 6379는 공개하지 않는다.
5. Redis 볼륨의 백업 주기·보관 위치·디스크 용량을 마련한다. 업데이트는 저장소 pull 후 `docker compose up -d --build --wait`이며 볼륨을 삭제하지 않는다.

HTTPS용 파일은 준비되어 있지만 실제 VM의 공인 인증서는 별도로 발급해야 한다.
인증서가 없는 상태에서는 기본 HTTP 구성을 사용한다. 순서는 [HTTPS 배포 절차](docs/HTTPS.md)의
HTTP 검증 경로 기동 → Certbot webroot 발급 → `.env`의 HTTPS 모드 활성화 → 갱신 훅 설치다.
HTTPS 모드에서는 기존 Nginx 컨테이너가 TLS를 종료하고 HTTP를 308로 리다이렉트하며,
Secure 쿠키를 강제한다. ACME와 Nginx healthcheck 경로는 HTTP로 유지한다.
일반 로컬 HTTP 실행은 변경하지 않았으며 실제 VM의 DNS·방화벽·공인 인증서·자동 갱신은
VM에서 별도로 확인한다. 단일 백엔드·단일 worker 제한은 HTTPS에서도 유지한다.
Nginx 프록시 근거: [WebSocket proxying](https://nginx.org/en/docs/http/websocket.html),
[proxy module](https://nginx.org/en/docs/http/ngx_http_proxy_module.html).
