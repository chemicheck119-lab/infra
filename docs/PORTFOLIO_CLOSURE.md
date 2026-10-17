# Chemicheck119 마무리: 비용 원인, 선택 이유, 검증 경계

2026-10-18. 로컬 검증 완료와 운영 적용은 다른 상태다. 실제 결제액/계정 정보는 비공개 증거로 유지한다.

## 결론

“트래픽이 몰려 비용이 나왔다”는 가정을 먼저 검증했다. 청구 표에는 요청 기반 추론 외에도 인스턴스 수명 기반 CPU/메모리, 월 단위 DB 실행, 데이터·이미지 보관이 있다. 따라서 비용은 호출 제어와 유지 시간·보관 정책을 나누어 다뤄야 한다. 운영 자원을 변경하지 않았으므로 실제 비용 절감은 미검증이다.

브라우저와 BFF 양쪽의 자동 POST 재시도는 비용 증폭 가능성이 있어 로컬에서 제거했다. 별도 인프라 대신 BFF의 bounded admission을 추가하고 동일 요청 집중에서 upstream 전달량과 거절, 복구, 권한 차단을 재현했다. 자세한 코드·테스트는 backend `docs/COST_TRAFFIC_SECURITY.md`에 있다.

## 스택별 유지·선택 이유

아래는 현재 구조를 검토한 설계 이유다. 최초 개발 당시 사용자가 이 이유로 선택했다고 단정한 기록은 아니다.

| 구성 | 해결하려는 문제와 유지 이유 | 대안·비용과 결정 |
|---|---|---|
| React/TypeScript | 전사·승인·분석 상태를 분리하고 API 타입을 대조 | FE에 모델 키를 넣지 않고 BFF 경유. 화면 성공이 현장 유효성은 아님 |
| ClawOps SDK/gateway | 전화 공급자 이벤트를 사이트의 사고 단위 입력으로 연결 | 직접 전화 인프라 구축 범위를 줄임. 독점 연결·상시 CPU·분당 통화·외부 모델 비용은 별도 관리 |
| SSE | 서버→브라우저 전사를 전달하는 단방향 요구 | 양방향 WebSocket을 브라우저까지 늘리지 않음. 연결 timeout·재구독·인증 필요. 무제한 연결 지원 아님 |
| Spring BFF | 인증·사고 scope·승인 revision·비밀값 경계를 한곳에 둠 | 브라우저 직접 공급자 호출보다 경계가 명확. 추가 JVM/배포 비용 존재 |
| PostgreSQL | 전사/승인/선택 버전/감사 관계를 트랜잭션으로 관리 | SQLite는 작은 단일 로컬 대안. Cloud SQL은 사용량 없어도 실행/저장 비용을 확인해야 함 |
| Python 처리 | 기존 CAS 정규화·검증·ASR 처리 재사용 | 형식 검증을 물질 정확성 보장으로 확대하지 않음 |
| Airflow | 기준정보 갱신의 의존 순서·실패·재실행을 관찰 | 수동 CLI가 더 단순할 수 있음. 첫 운영은 원천 주기 근거 없으면 수동, 실시간 통화 요청과 분리 |
| Debezium/Kafka | 승인 변경 이벤트를 후속 데이터셋으로 전달하는 실습 | 작은 서비스의 필수 구성 아님. 단일 consumer/outbox polling보다 복잡. 로컬 확장 실습으로 유지 |
| Iceberg/Trino | 스냅샷과 SQL 대조로 데이터 버전 재현 | 사용자 조회는 PG. 사고 요청의 필수 의존성으로 두지 않음. 로컬 확장 실습으로 유지 |
| Docker volume/로컬 저장소 | 실습 객체가 재시작에 사라지는 문제 방지 | volume은 backup이 아니며 `down -v`·디스크 손실을 복구하지 않음 |
| 프로세스 내 admission | 동시 요청·반복 호출을 upstream 전에 제한 | 추가 Redis/유료 edge 없이 시작. 분산 quota/금액 cap은 아님 |

## 권장 최소 운영 범위

공개 포트폴리오는 민감 데이터 없는 설명/명시된 합성 시연을 기본으로 한다. 실제 통화 수신은 동의된 예약 시연 시간에만 별도 활성화 절차를 적용한다. 전화 gateway 1개 번호/1개 프로세스, DB 필요 시간, 모델·BFF scale 상한을 함께 고려해야 한다. 이 문서는 운영 설정을 바꾸는 자동화가 아니다.

Airflow·Kafka·Iceberg·Trino를 상시 클라우드에 새로 배포하지 않는다. PG→승인 변경→실습 lakehouse는 별도 로컬 재현 경로로 설명하고, 실제 통화 경로와 한 그림에서 구분한다. 실제 서비스가 사용하지 않는 스택을 서비스 필수 기술로 꾸미지 않는다.

## 안전하게 재확인하는 명령

```sh
python3 scripts/collect_cost_metadata.py --project PROJECT_ID \
  --output /absolute/private/path/new-audit-directory
python3 -m unittest discover -s scripts -p 'test_cost_metadata.py' -v
bash -n scripts/*.sh
scripts/test_config.sh
```

collector는 `gcloud ... list/describe/get-iam-policy`만 실행하며 API enable, build, deploy, start/stop, Secret payload 조회, BQ 유료 query를 실행하지 않는다. 원본 env/IAM 계정명은 결과에서 제외한다. `actual_costs_included=false`는 비용 미조회이며 무료라는 뜻이 아니다. 실제 비용은 Billing 보고서의 기간/프로젝트/서비스/SKU/credits/세금 조건을 함께 기록한다.

원인 해석의 공식 근거(확인 2026-10-18):

- https://docs.cloud.google.com/run/docs/configuring/billing-settings — instance-based와 request-based 구분
- https://docs.cloud.google.com/run/docs/configuring/max-instances — service/revision 상한 구분
- https://docs.cloud.google.com/sql/faq — 인스턴스가 꺼져도 저장소 과금 지속
- https://docs.cloud.google.com/billing/docs/how-to/budgets-spend-caps — 알림과 별도 spend cap 구분 및 지원 서비스 확인

## 운영 승인 전 남은 항목

1. GCP와 외부 통화/LLM 결제 기간·credits·세금을 맞춰 총액 대조. 월별 다른 기간을 전후 개선율로 쓰지 않는다.
2. DB TLS 강제·디스크 증가 상한·인증서 및 JDBC 호환성·rollback을 별도 승인된 환경에서 검증.
3. 공개 pilot 로그인과 실제 사고 데이터 접근 범위를 분리. default-off 설정만으로 이미 배포된 flag가 꺼져 있다고 가정하지 않음.
4. service/revision max instances와 tag revision을 함께 검토. 프로세스 quota는 scale-out 수만큼 늘어날 수 있음.
5. 실제 동시 사용자 수와 모델 처리 시간·지연·비용은 모델/DB/네트워크가 포함된 별도 승인 시험 필요.

성과 문구는 “비용 항목을 분해하고 로컬 요청 집중에서 제한과 복구를 검증했다”까지다. “서버 비용 X% 절감”, “동시 N명 서비스 운영”, “보안 확보”는 이번 결과로 주장하지 않는다.
