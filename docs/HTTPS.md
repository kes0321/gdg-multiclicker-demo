# gdgclicker.duckdns.org HTTPS 적용

프런트 Nginx 컨테이너가 TLS를 종료하고 `/api/`와 `/ws`를 기존 백엔드로 전달한다.
HTTP 기본 구성은 인증서 없이 실행된다. 인증서 발급 후 `compose.https.yaml`을 추가하면
443 공개·인증서 마운트·HTTPS Nginx 설정·Secure 쿠키를 함께 적용한다.
상시 실행 서비스는 기존 `frontend`, `backend`, `redis` 세 개이며 단일 backend/worker 제한을 유지한다.
호스트 Nginx나 추가 TLS 프록시는 설치하지 않는다. Certbot은 VM에서 인증서만 관리한다.

이 문서는 Ubuntu VM에서 실행하는 절차다. 실제 VM 적용, 공인 인증서 발급과 외부 연결 검증은
사용자가 수행한다. 로컬 테스트 인증서를 실제 서비스에 사용하지 않는다.

## 1. DNS·Google Cloud 방화벽·저장소 준비

- DuckDNS의 `gdgclicker.duckdns.org` A 레코드를 VM 외부 IPv4로 지정한다. Google Cloud에서 외부 IP를 고정 예약해 두면 재할당으로 DNS가 어긋나는 것을 방지할 수 있다.
- IPv6를 제공하지 않는 VM에는 다른 서버를 가리키는 AAAA 레코드를 두지 않는다.
- 해당 VM의 VPC 방화벽에서 인바운드 TCP **80·443**을 허용한다. 규칙의 대상 태그도 VM과 일치해야 한다. 8000·6379는 공개하지 않는다.
- 최초 발급뿐 아니라 HTTP-01 갱신을 위해서도 80번 포트를 유지한다.
- 로컬 커밋이 원격에 올라간 후 VM에서 받는다. VM에서 예전 안내대로 직접 수정한 파일이 있다면 `git status`와 `git diff`로 확인하고 먼저 보관·통합한다.

```sh
cd ~/apps/gdg-multiclicker-demo
git status --short
git pull --ff-only origin main
```

기존 `.env`를 덮어쓰지 말고 편집한다. 프로젝트 이름은 기존 Redis 볼륨을 사용하도록 유지한다.
최초 발급 전에는 아래처럼 **HTTP만** 사용한다. `COMPOSE_FILE`이 있으면 `compose.yaml`로 설정한다.

```dotenv
COMPOSE_PROJECT_NAME=multiclicker
COMPOSE_FILE=compose.yaml
HTTP_PORT=80
HTTPS_PORT=443
ACME_WEBROOT=./.acme-webroot
LETSENCRYPT_DIR=/etc/letsencrypt
SESSION_COOKIE_SECURE=false
```

`ACME_WEBROOT`를 변경하면 아래 Certbot `-w` 경로도 같은 호스트 디렉터리로 바꿔야 한다.
`LETSENCRYPT_DIR`는 Certbot 기본 저장소이며 인증서는 Git이나 이미지에 넣지 않는다.

## 2. HTTP와 ACME 검증 경로 기동

```sh
mkdir -p .acme-webroot/.well-known/acme-challenge
printf 'acme-ok\n' > .acme-webroot/.well-known/acme-challenge/test
sudo docker compose config --quiet
sudo docker compose up -d --build --wait
curl -f http://127.0.0.1/nginx-health
curl -f http://gdgclicker.duckdns.org/api/health
curl -f http://gdgclicker.duckdns.org/.well-known/acme-challenge/test
```

마지막 응답은 `acme-ok`이다. 내 PC에서도 마지막 URL을 확인한다.
없는 challenge 파일은 404이며 SPA `index.html`로 대체하지 않는다.
이 단계에서는 인증서가 없어도 프런트가 실행되고 쿠키는 HTTP용이다.

## 3. Certbot 설치와 인증서 발급

```sh
sudo apt update
sudo apt install -y snapd
sudo snap install --classic certbot
```

이미 snap Certbot이 설치되어 있으면 재설치는 건너뛴다. 아래 이메일을 실제 주소로 바꾼다.
약관에 동의하는 발급 명령이며, 프런트 중지 없이 webroot 방식으로 인증한다.

```sh
sudo /snap/bin/certbot certonly \
  --webroot -w "$PWD/.acme-webroot" \
  --cert-name gdgclicker.duckdns.org \
  -d gdgclicker.duckdns.org \
  --email YOUR_EMAIL@example.com \
  --agree-tos

sudo /snap/bin/certbot certificates
sudo test -s /etc/letsencrypt/live/gdgclicker.duckdns.org/fullchain.pem
sudo test -s /etc/letsencrypt/live/gdgclicker.duckdns.org/privkey.pem
```

발급에 실패하면 HTTP 상태를 유지하고 DNS·방화벽·challenge URL부터 확인한다.
이전에 standalone/nginx 방식으로 발급했다면 향후 갱신도 webroot를 쓰도록 변경한다.
기존 인증서 이름이 `gdgclicker.duckdns.org-0001` 등이라면 `certbot certificates`로 확인하고
아래 Nginx 인증서 경로와 cert-name을 함께 맞춘다. 기본 구성은 정확히 `gdgclicker.duckdns.org` 이름을 사용한다.

```sh
sudo /snap/bin/certbot reconfigure \
  --cert-name gdgclicker.duckdns.org \
  --webroot -w "$PWD/.acme-webroot"
```

새 webroot 인증서를 방금 발급했다면 reconfigure는 필요 없다.

## 4. HTTPS 활성화

인증서가 준비된 후 `.env`에서 다음 두 줄을 설정한다.

```dotenv
COMPOSE_FILE=compose.yaml:compose.https.yaml
SESSION_COOKIE_SECURE=true
```

`COMPOSE_FILE`의 `:` 구분자는 **Linux VM용**이다. Windows 로컬에서는 두 파일을
`-f compose.yaml -f compose.https.yaml`로 명시한다. HTTPS override 자체도 Secure 쿠키를
`true`로 강제하므로 HTTP 기본값이 남아서 HTTPS 쿠키가 잘못 설정되지 않는다.

```sh
sudo docker compose config --quiet
sudo docker compose up -d --build --wait
sudo docker compose exec -T frontend nginx -t
sudo docker compose ps

curl -I http://gdgclicker.duckdns.org/
curl -I https://gdgclicker.duckdns.org/
curl -f https://gdgclicker.duckdns.org/api/health
```

기대 결과는 HTTP **308** 리다이렉트, HTTPS **200**, `{"status":"ok"}`이다.
`curl -k` 없이 성공해야 실제 인증서·호스트 이름 검증이 통과한 것이다.
리다이렉트는 요청 경로·쿼리를 보존하며 POST 메서드를 유지하는 308을 사용한다.
도메인과 공개 443 포트를 기준으로 하므로 운영에서는 `HTTPS_PORT=443`을 사용한다.
`/nginx-health`와 `/.well-known/acme-challenge/`만 HTTP로 계속 제공한다.
`/nginx-health`는 Nginx 자체 상태이며 Redis 상태는 `/api/health`에서 확인한다.

브라우저에서 `https://gdgclicker.duckdns.org`에 접속하고 플레이어 생성·새로고침·클릭·레벨업을 확인한다.
쿠키는 `Secure; HttpOnly; SameSite=lax; Path=/`이며 WebSocket은 자동으로 `wss://.../ws`를 사용한다.
개발자 도구 Network에서 WebSocket 101 응답과 두 브라우저 연결 사이 방송을 확인한다.
HTTP로 접근하던 IP 주소의 쿠키는 도메인 쿠키와 별개이므로 같은 브라우저라도 새 플레이어가 생성될 수 있다.

## 5. 자동 갱신과 Nginx reload

snap Certbot의 자동 갱신 타이머를 사용한다. 갱신 후 컨테이너 Nginx도 새 인증서를 읽도록
저장소의 deploy hook을 심볼릭 링크로 설치한다. 훅은 링크의 실제 경로에서 저장소를 찾고
그곳의 `.env`를 읽기 때문에 사용자 이름·체크아웃 경로를 하드코딩하지 않는다.

```sh
sudo mkdir -p /etc/letsencrypt/renewal-hooks/deploy
chmod +x scripts/reload-nginx.sh
sudo ln -sfn "$PWD/scripts/reload-nginx.sh" \
  /etc/letsencrypt/renewal-hooks/deploy/multiclicker-nginx.sh

# 먼저 실제 Nginx 구문 검사와 무중단 reload 확인
sudo /etc/letsencrypt/renewal-hooks/deploy/multiclicker-nginx.sh

# staging 갱신 및 deploy hook 시험; 운영 인증서로 바꾸지 않는다.
sudo /snap/bin/certbot renew --cert-name gdgclicker.duckdns.org \
  --dry-run --run-deploy-hooks
systemctl list-timers --all | grep certbot
```

시험 성공과 다음 갱신 타이머를 확인한다. 이미 설치한 별도 cron 갱신 작업이 있다면
중복 실행하지 않도록 하나의 스케줄러로 정리한다. 저장소 경로를 옮기면 링크도 다시 설치한다.
`live/*.pem`은 `archive/`를 가리키는 링크이므로 개별 파일 대신 `/etc/letsencrypt` 전체를
읽기 전용으로 마운트한다. 키 파일 권한은 임의로 완화하지 않는다.

## 이후 업데이트·장애 확인·HTTP 복귀

HTTPS 모드를 `.env`에 저장했으므로 다음부터는 일반 Compose 명령을 그대로 쓴다.

```sh
git pull --ff-only origin main
sudo docker compose up -d --build --wait
sudo docker compose logs --tail=100 frontend backend
```

`cannot load certificate` 오류는 발급 여부·인증서 이름·호스트 마운트 경로를 확인한다.
timeout은 GCP 외부 IP/방화벽, 인증서 오류는 DNS·유효기간·인증서 체인을 확인한다.
HTTPS에서 502라면 `backend` health와 프런트 로그를 확인한다.

HTTP로 복귀해야 한다면 `.env`에서 `COMPOSE_FILE=compose.yaml`,
`SESSION_COOKIE_SECURE=false`로 바꾸고 `sudo docker compose up -d --build --wait`를 실행한다.
Redis 볼륨과 인증서는 삭제하지 않는다. HTTPS에서 쓰던 Secure 쿠키는 HTTP로 전송되지 않는다.
어느 모드에서도 `down -v`나 볼륨 삭제는 배포 명령에 포함하지 않는다.

## 검증 도구

요구사항을 설치한 Python에서 실제 HTTPS와 WSS를 함께 확인할 수 있다.

```sh
python scripts/verify_compose.py --url https://gdgclicker.duckdns.org
docker compose up -d --force-recreate --wait
python scripts/verify_compose.py --url https://gdgclicker.duckdns.org --check-persistence
```

기존 개발 서버 대신 선택한 배포에 새 테스트 플레이어를 만든다.
검증 클라이언트는 리다이렉트를 자동으로 따르지 않아 로컬 HTTP 검사가 운영 도메인으로 넘어가지 않는다.
로컬 전용 테스트 인증서는 `--ca-file PATH`로 명시적으로 신뢰할 수 있다.
호스트 이름·인증서 검증은 끄지 않는다. 실제 공인 인증서는 기본 신뢰 저장소로 검증한다.

공식 문서: [Certbot](https://eff-certbot.readthedocs.io/en/stable/using.html),
[Nginx HTTPS](https://nginx.org/en/docs/http/configuring_https_servers.html),
[GCP 방화벽](https://docs.cloud.google.com/firewall/docs/firewalls).
