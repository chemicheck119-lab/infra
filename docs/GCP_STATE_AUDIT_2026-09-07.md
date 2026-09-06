# GCP 상태 감사 — 2026-09-07

## 목적과 범위

`chemi-check` 프로젝트를 2026-09-07 03:44 KST에 읽기 전용 `gcloud` 명령으로 확인했습니다.
리소스를 생성·수정·삭제하거나 traffic을 변경하지 않았습니다. 이 문서는 단일 시점 inventory와
설정 gap이며 실제 청구서, 침투 테스트, 고가용성 검증 또는 상용 운영 실적이 아닙니다.

## 요약

| 영역 | 관찰값 | 사실 상태 |
|---|---|---|
| Billing budget | 월 50,000원, `CURRENT_SPEND` 100% 단일 threshold | 구현 완료 |
| Cloud Run Model API IAM | preview·staging 모두 `allUsers` 없음 | 구현 완료 |
| Cloud Run FE·BE IAM | FE·BE `allUsers` invoker | 개발용 공개 edge |
| Cloud Run min instance | 네 서비스 모두 0 | 구현 완료 |
| Compute Engine | 실행 중·중지 instance 모두 0대 | 구현 완료 |
| Cloud SQL | PostgreSQL 16, `db-f1-micro`, `RUNNABLE`, `ZONAL` | 개발용 staging |
| Cloud SQL 보호 | backup·PITR·deletion protection 활성 | 구현 완료 |
| Cloud SQL TLS | `ALLOW_UNENCRYPTED_AND_ENCRYPTED` | 개선 필요 |
| Cloud SQL storage | 10GB, auto-resize 활성, 상한 0 | 개선 필요 |
| Artifact Registry | 실제 repository 약 10.10GB, cleanup policy 없음 | 개선 필요 |
| ML GCS | 약 3.87GB, PAP·uniform access·30/90일 lifecycle | 구현 완료 |
| Cloud Build GCS | 약 1.99GB, lifecycle 없음, soft delete 7일 | 개선 필요 |
| Speech Cloud Run Jobs | 5개, 최신 실행 모두 성공, task 1·parallelism 1·retry 0 | 개발용 평가 |

`100% budget 알림`은 비용이 50,000원에 도달한 뒤 알리는 관측 장치이며 서비스를 자동
중지하지 않습니다. 현재 실행 제한은 별도의 resource cap·timeout·retry 0으로 유지합니다.

## Cloud Run 공개 범위

| 서비스 | IAM invoker | ingress | 현재 revision max instance | traffic tag |
|---|---|---|---:|---:|
| `chemicheck119-fe-develop` | `allUsers` | all | 3 | 0 |
| `chemicheck119-be-staging` | `allUsers` | all | 1 | 21 |
| `chemicheck119-model-api-preview` | 사용자·배포 SA·runtime SA만 | all | 3 | 3 |
| `chemicheck119-model-api-staging` | 사용자·배포 SA·runtime SA만 | all | 3 | 5 |

Model API는 인터넷 ingress가 열려 있어도 IAM에 `allUsers`·`allAuthenticatedUsers`가 없으므로
인증 없는 호출은 허용되지 않습니다. FE·BE는 브라우저 접근을 위한 공개 edge이므로 공개
invoker 자체가 인증 우회라는 뜻은 아닙니다. 애플리케이션 session·incident scope 검증은
별도의 Backend 보안 경계입니다.

BE staging은 100% traffic revision 1개 외에 zero-traffic candidate tag 20개가 남아 있습니다.
tag URL은 service IAM을 공유하므로 호출 가능한 과거 revision 수명주기를 별도로 정해야 합니다.
아직 tag를 제거하거나 revision을 삭제하지 않았습니다.

## Cloud SQL

| 설정 | 관찰값 | 해석 |
|---|---|---|
| 상태·정책 | `RUNNABLE`, `ALWAYS` | 현재 가장 명확한 상시 비용 |
| availability | `ZONAL` | 고가용성 구성이 아님 |
| network | private IP, public IPv4 비활성 | 공개 DB endpoint 없음 |
| TLS | `requireSsl=false`, `ALLOW_UNENCRYPTED_AND_ENCRYPTED` | TLS 강제 전환·호환성 검증 필요 |
| storage | 10GB, auto-resize=true, limit=0 | 갑작스러운 무제한 증가를 막는 상한 없음 |
| backup | 일 1회, 7개 보존, PITR 7일 | staging 복구 기반은 존재 |
| deletion protection | 활성 | 실수 삭제 방지 |

TLS 강제와 storage 상한 변경은 Backend JDBC URL·인증서 방식, migration, readiness, rollback을
함께 검증해야 합니다. 이번 감사에서는 변경하지 않았습니다. 미사용 시간 중지는 기존
[Cloud SQL 비용 이슈](https://github.com/chemicheck119-lab/infra/issues/3)에서 관리합니다.

## 저장소와 빌드 artifact

Artifact Registry `chemicheck119` repository는 약 10.10GB이며 cleanup policy가 없습니다.
Docker manifest 기준으로는 41개 항목이고, package별 단순 합계는 layer 중복을 포함할 수 있어
repository 실제 저장량과 다릅니다.

| package | manifest 수 | manifest 표기 크기 합계 |
|---|---:|---:|
| `be` | 22 | 약 2.57GiB |
| `fe-develop` | 1 | 약 0.03GiB |
| `model-api-preview` | 14 | 약 5.24GiB |
| `speech-eval` | 4 | 약 4.08GiB |

Cloud Build bucket은 약 1.99GB이며 lifecycle이 없고 7일 soft delete가 적용되어 있습니다.
삭제 정책은 현재 traffic revision, rollback 후보, 잠금 평가 image digest를 보호하도록
keep 조건과 dry-run을 먼저 정의해야 합니다.

ML bucket은 약 3.87GB입니다. public access prevention·uniform bucket access가 활성이고,
`raw/`·`staging/`은 30일, `experiments/`는 90일 뒤 삭제되며 soft delete는 꺼져 있습니다.

## Speech 평가 compute

Cloud Run Job 5개가 존재하고 최신 실행은 모두 성공했습니다. 모든 Job은 단일 task,
parallelism 1, retry 0이며 외부 요청을 받는 서비스가 아닙니다. Compute Engine instance는
0대여서 잔존 GPU·VM은 없습니다. 현재 실행 중인 Whisper LoRA는 로컬 Apple M4 MPS 실험이며
GCP L4 운영 경험으로 표현하지 않습니다.

## 재현한 읽기 전용 명령

```bash
gcloud billing projects describe chemi-check
gcloud billing budgets list --billing-account=REDACTED
gcloud run services list --region=asia-northeast3 --project=chemi-check
gcloud run services get-iam-policy SERVICE --region=asia-northeast3 --project=chemi-check
gcloud run jobs list --region=asia-northeast3 --project=chemi-check
gcloud run jobs executions list --job=JOB --region=asia-northeast3 --project=chemi-check
gcloud sql instances describe chemicheck119-pg-staging --project=chemi-check
gcloud compute instances list --project=chemi-check
gcloud artifacts repositories describe chemicheck119 \
  --location=asia-northeast3 --project=chemi-check
gcloud artifacts docker images list \
  asia-northeast3-docker.pkg.dev/chemi-check/chemicheck119 --include-tags
gcloud storage du -s gs://chemi-check-ml-data-181872008704 gs://chemi-check_cloudbuild
```

실제 billing account ID, 사용자 계정, Secret 값, 환경변수 값은 문서와 Git에 기록하지 않습니다.

## 다음 Gate

1. [Artifact Registry·Cloud Build·Cloud Run tag cleanup](https://github.com/chemicheck119-lab/infra/issues/17)을 dry-run으로 설계합니다.
2. [Cloud SQL TLS·storage 상한](https://github.com/chemicheck119-lab/infra/issues/16) 전환 전 Backend JDBC·readiness·rollback smoke를 작성합니다.
3. storage auto-resize 상한을 workload 근거로 정하고 staging에만 적용합니다.
4. Cloud SQL을 중지하기 전 시연 일정과 재기동 검증 책임자를 확정합니다.

위 항목은 모두 설계 또는 검증 전이며 이번 문서의 완료 범위가 아닙니다.
