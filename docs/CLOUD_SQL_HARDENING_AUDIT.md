# Cloud SQL TLS·storage hardening 사전 감사

> 상태: **읽기 전용 preflight 구현·실행 완료** / **GCP 설정 변경 전**
> 관측 시각: 2026-09-08 KST (`2026-09-08T02:40:50Z`)
> 대상: `chemicheck119-pg-staging`, `chemicheck119-be-staging`

## 결론

현재 Backend는 고정된 Secret version으로 PostgreSQL private IP에 연결하고 자격증명을 URL과
분리한다. 그러나 Database URL에 `sslmode`이 명시되지 않았고 Cloud SQL도
`ALLOW_UNENCRYPTED_AND_ENCRYPTED` 상태다. 따라서 실제 연결이 암호화됐다고 증명할 수 없고,
server를 곧바로 `ENCRYPTED_ONLY`로 바꾸는 것도 승인하지 않는다.

pgJDBC 기본 `sslmode=prefer`는 암호화를 먼저 시도하지만 실패하면 비암호화 연결로 fallback할
수 있다. 전환 전에는 `sslmode=require` 이상을 명시한 새 Secret version과 0% traffic Backend
candidate에서 Flyway·readiness·confirmation·record smoke를 먼저 통과해야 한다.

storage는 10GB·auto-resize 활성·상한 0이다. 현재 disk 사용량과 증가율을 수집하지 않았으므로
20GB나 30GB 같은 값을 임의로 권고하지 않는다.

## 실제 관측

| 항목 | 관측값 | 판정 |
|---|---|---|
| Cloud SQL | PostgreSQL 16·`RUNNABLE`·`ZONAL` | 개발용 staging |
| network | public IPv4 없음·private network 있음 | 구성 확인 |
| server TLS | `ALLOW_UNENCRYPTED_AND_ENCRYPTED` | 강제 전환 전 |
| server CA | `GOOGLE_MANAGED_INTERNAL_CA` | 구성 확인 |
| Backend DB URL | Secret version 1 고정·enabled | Secret 계약 통과 |
| URL 자격증명 | URL에 미포함 | 분리 확인 |
| pgJDBC | 42.7.3 | local runtimeClasspath 해결값 |
| URL `sslmode` | 없음 | 명시적 TLS Gate 실패 |
| URL `sslrootcert` | 없음 | server identity 검증 근거 없음 |
| storage | 10GB·auto-resize·limit 0 | 상한 근거 필요 |
| backup·PITR·deletion protection | 모두 활성 | 보호 설정 확인 |
| candidate smoke | 미실행 | 전환 차단 |
| GCP 설정 변경 | 0건 | 읽기 전용 |

## 재현 방법

Database URL 원문은 stdout·보고서·로그에 쓰지 않는다. `--inspect-database-url-secret`은 원문을
메모리에서만 파싱하고 SHA-256과 TLS 관련 boolean만 남긴다.

```bash
python3 scripts/audit_cloud_sql_hardening.py \
  --backend-root ../back \
  --inspect-database-url-secret \
  --output ../private-data/experiments/infra/cloud-sql-hardening-audit-20260908-r3/report.json
```

### 잠금 artifact

- report SHA-256: `40b153134b3a8ae2ae6801679edf1de838580bf1e66f70be20ee16ccefbad276`
- auditor source SHA-256: `30f1d17bf6c181a88badba2000b5828556c2982d3b4bd7ab4a817408dbf03419`
- Backend commit: `3bdce869691e50af3f557c98892a98f078cfffd4`
- report 권한: `0600`
- raw Secret 노출: `false`

## 전환 Gate

1. 현재 URL에 `sslmode=require`를 추가한 새 Database URL Secret version을 만든다.
2. 새 Secret version을 참조하는 Backend candidate를 0% traffic으로 배포한다.
3. 기존 server mode에서 candidate의 Flyway·readiness·confirmation·record smoke를 수행한다.
4. candidate 연결이 실제 TLS인지 `pg_stat_ssl` 또는 동등한 server-side 근거로 확인한다.
5. 사용자 승인 뒤 Cloud SQL을 `ENCRYPTED_ONLY`로 변경한다.
6. 새 연결에만 설정이 적용되므로 candidate connection pool을 재생성하고 smoke를 반복한다.
7. 실패하면 server mode를 되돌리고 기존 serving revision을 유지한다.
8. 별도로 disk 사용량·일/주 증가율을 수집한 뒤 storage 상한을 결정한다.

이번 작업에서는 위 변경·배포·traffic 전환을 실행하지 않았다.

## 사실 상태와 주장 제한

- **구현 완료:** Secret 원문을 노출하지 않는 Cloud SQL·Cloud Run·JDBC read-only preflight
- **부분 구현 또는 개발용 데모:** 현재 staging 설정과 URL 옵션 관측
- **설계 완료·구현 전:** 명시적 TLS Secret, candidate smoke, `ENCRYPTED_ONLY`, storage 상한
- **검증되지 않은 가설:** 전환 후 무중단 동작, 적정 storage 상한, 고가용성

현재 연결이 TLS라는 주장, Cloud SQL TLS 강제 완료, 고가용성 운영 경험, 적정 storage 상한을
주장할 수 없다.

## 공식 근거

- [Cloud SQL SSL/TLS 설정](https://docs.cloud.google.com/sql/docs/postgres/configure-ssl-instance)
- [Cloud SQL instance SSL mode](https://docs.cloud.google.com/sql/docs/postgres/instance-settings)
- [pgJDBC 연결·sslmode](https://jdbc.postgresql.org/documentation/use/)
- [pgJDBC SSL 동작](https://jdbc.postgresql.org/documentation/ssl/)
