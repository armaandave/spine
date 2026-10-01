# Production networking and logging

Notes for the Docker Desktop host that serves `https://api.spine-api.com` through a
Cloudflare named tunnel. The config lives in `docker-compose.production.yml` and
`nginx.conf`; the deploy-time checks live in `scripts/`.

Request path: visitor -> Cloudflare -> `cloudflared` (on the host Mac) ->
`127.0.0.1:8000` -> Docker Desktop -> nginx in the `app` container -> gunicorn on
`127.0.0.1:8001`.

## Published port

The app publishes `"127.0.0.1:8000:8000"`. Devices on the LAN get "connection refused"
and must use `https://api.spine-api.com`. The `curl http://127.0.0.1:8000/health/`
checks in `scripts/external-ratings-production.sh` and the sync-featured-lists workflow,
`cloudflared` and the iOS Simulator all still work.

That is not the same as "only the Mac can reach it". Verified on Docker Desktop 4.79.0:
any process on the Mac can reach nginx through `127.0.0.1:8000`, and any container on
that Docker VM can reach it through `host.docker.internal:8000` or directly through the
app container's IP, even from another Docker network. All of them arrive as the gateway
address `172.N.0.1`, exactly like `cloudflared`; containers on the compose network itself
arrive as their own `172.N.0.x`. Both are inside the trusted range below, so any of them
can set its own client IP with a `CF-Connecting-IP` header. Keep untrusted containers off
that Mac.

## Real client IP

Measured on Docker Desktop 4.79.0 (engine 29.5.3, macOS arm64), the source address
nginx sees for a connection made from the host:

| Port published as | Connection made to | nginx sees |
| --- | --- | --- |
| `127.0.0.1:P:8000` | `127.0.0.1` | gateway of the compose network, `172.N.0.1` |
| `P:8000` (all interfaces) | `127.0.0.1`, the Mac's LAN address, `::1` | `192.168.65.1` for all of them |

nginx therefore trusts `CF-Connecting-IP` only from `172.16.0.0/12`. That is where Docker
allocates new networks first (172.17-172.31, as /16s). Docker's default pools also
include `192.168.0.0/16`, handed out as /20s once the 172.x networks are used up (or when
`default-address-pools` is changed); that range is not trusted. Neither is
`192.168.65.0/24`, which is what a non-loopback port mapping looks like, where LAN clients
are indistinguishable from the tunnel. If the `127.0.0.1` binding is ever removed, or the
compose network lands outside `172.16.0.0/12`, the header is ignored (failing closed)
instead of trusted, and every visitor looks like one address.

nginx sends Django `X-Real-IP` and `X-Forwarded-For` set to the real client IP only.
Cloudflare appends to any `X-Forwarded-For` the client supplied, and DRF throttles key
on the whole header, so forwarding it would let clients dodge throttles. When trust
fails, the shared key covers the auth, refresh, anon and search buckets and allauth's
per-IP limits.

The nginx access log line ends with `rt=` (request time), `urt=` (gunicorn time),
`cf_ray=` (`"-"` when absent; match it in the Cloudflare dashboard) and `peer=` (the
address that actually connected). The first field is the real client IP. `peer=` ending
in `.1` means the request came through the published port: `cloudflared`, any local
process, or a container on another network. It does not identify `cloudflared`.

### Not done

- **Authenticate the header.** Address-based trust cannot tell `cloudflared` from any other
  local caller. The real fix is a Cloudflare Request Header Transform Rule that adds a
  secret header, plus an nginx `map` that honours `CF-Connecting-IP` only when that header
  matches. It needs Cloudflare dashboard work.
- **Pin the compose network's subnet** (`networks.default.ipam`) so the trusted range can be
  the single gateway address. Not done: untested on the server Mac's Docker, it recreates
  the network and all three containers, and a subnet that overlaps another network fails
  the deploy.

## Deploy-time checks

Both deploy scripts do the following: `scripts/deploy-production.sh` (manual) and
`scripts/codex-mobile-deploy-backend.sh` (the one the runner workflow calls).

- `docker compose up -d --build --wait --wait-timeout 240`. `db` and `redis` have
  healthchecks (`pg_isready` over TCP, because a new data directory is first served by a
  temporary server that only listens on the unix socket; `redis-cli ping` matching
  `PONG`), and `app` starts only once both are healthy (`condition: service_healthy`).
  With the default `service_started`, a slow Postgres can make the entrypoint's `migrate`
  fail and the app restart-loop, and the script's next `exec` then fails with "container is
  restarting" (reproduced with a toy stack and a deliberately slow Postgres).
  If the stack is not healthy in time the script prints `compose ps` and the last log
  lines and exits 1; compose does not roll back. The app's own healthcheck is the
  Dockerfile's (45 s interval, 30 s start period). Engine 25+ probes every 5 s during the
  start period, so `--wait` returns as soon as the app is up (measured: 12.6 s with an
  explicit `start_interval: 5s`, 12.5 s without, so none is set); an older engine would
  wait for the first 45 s probe (not tested). The 240 s also bounds a slow start, for
  example a long migration: past that, compose reports failure even if the app comes up
  later.
- After the existing health and meta checks, `scripts/verify-real-client-ip.sh` runs. It
  sends a loopback request with `CF-Connecting-IP: 203.0.113.250` to
  `http://127.0.0.1:8000/api/v1/health/` (not `/health/`, which has `access_log off`) and
  requires nginx's log line to start with that address. It also sends a request through
  `https://api.spine-api.com` and requires the logged client to differ from its `peer=`,
  which covers Cloudflare stripping the header (the "Remove visitor IP headers" managed
  transform). The visitor address is never printed. On failure it exits 1 with what to
  change; if it could not complete it exits 2. The stack stays up either way.
  `SPINE_REAL_IP_CHECK=warn` reports without failing, `skip` skips it. Under GitHub Actions
  it also writes an `::error` (or `::warning`) annotation.
- The check is skipped, with a notice, on a branch that does not contain the script.

## After the first deploy of this change

```bash
docker inspect spine spine-db spine-redis --format '{{.Name}} {{json .HostConfig.LogConfig}}'
lsof -nP -iTCP:8000 -sTCP:LISTEN          # 127.0.0.1:8000 only
docker logs spine --since 10m 2>&1 | grep ' peer=' | grep -v 'cf_ray="-"' | tail -5
```

Tunnel requests show the visitor's address first and `peer=172.N.0.1`.

Once, from a network other than the Mac's (for example a phone on cellular):

```bash
curl -s -o /dev/null -H 'CF-Connecting-IP: 203.0.113.9' 'https://api.spine-api.com/api/v1/health/?spoof-check=1'
docker logs spine --since 5m 2>&1 | grep 'spoof-check=1'      # on the Mac
```

The logged first field must be the caller's real IP, not `203.0.113.9`. That proves
Cloudflare overwrites a client-supplied `CF-Connecting-IP` instead of passing it through.

Check the tunnel config on the server (`~/.cloudflared/config.yml`, which
`scripts/setup-cloudflare-tunnel.sh` writes, or the service URL in the Cloudflare
dashboard if the tunnel is remotely managed). It must say `http://127.0.0.1:8000`, not
`localhost:8000`: only IPv4 loopback is published, so `localhost` resolves to `::1` first
and that is refused. curl falls back to IPv4; how `cloudflared` copes was not tested.

## Log rotation and container recreation

Every service uses `json-file` with `max-size: 10m` and `max-file: 5` (50 MB per
container). Changing `logging` and adding healthchecks changes the service config, so the
next `up -d` recreates `db` and `redis` as well as `app`; their volumes persist, and the
old containers' unrotated logs are discarded with them. Expect roughly 10-15 s of 5xx
while the three are swapped (13 s measured locally on an idle Mac; about 5 s before the
healthchecks, which add db's and redis's first 5 s probe to the swap).

## Known limits

- gunicorn's own access log still shows `127.0.0.1`; use the nginx line for client IPs.
- The IPv6 nginx variant (`YAMTRACK_IPV6_ENABLED=True`) works, but IPv6 peers are not
  trusted.
