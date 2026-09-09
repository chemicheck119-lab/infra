# GCP 결제·서비스 가용성 감사 — 2026-09-09

## 결론

2026-09-09 12:21 KST에 `chemi-check`를 읽기 전용으로 재확인한 결과, 프로젝트는 결제
계정 참조와 `billingEnabled=true`를 유지하지만 **연결된 결제 계정 자체는 `open=false`**다.
따라서 프로젝트 연결 여부만 보고 배포 가능하다고 판단하면 안 된다.

같은 시점에 Firebase Hosting의 정적 진입점은 HTTP 200이었으나 FE·BE Cloud Run 직접
요청은 HTTP 503이었다. Artifact Registry 목록 조회는 `BILLING_DISABLED`로 거부됐다.
Cloud SQL control plane은 여전히 `RUNNABLE`·`ALWAYS`를 반환했지만, 이 값만으로 실제 DB
접속 가능성이나 과금 상태를 증명하지 않는다. 리소스 메타데이터가 남아 있다는 사실과 서비스가
정상 제공된다는 사실을 분리해야 한다.

이번 감사에서는 결제 계정, 리소스, traffic, DB 또는 IAM을 변경하지 않았다. HTTP 요청은
가용성 확인용 3건만 수행했다.

## 관찰값

| 확인 항목 | 2026-09-09 관찰값 | 해석 |
|---|---|---|
| 프로젝트 billing 참조 | 계정 참조 존재, `billingEnabled=true` | 연결 메타데이터만 존재 |
| 연결 billing account | `open=false` | 현재 GCP 유료 작업 실행 조건 미충족 |
| Budget 객체 | 월 50,000원, 현재 지출 100% threshold 객체 존재 | 계정 폐쇄 상태에서 알림 효력은 검증하지 않음 |
| Firebase Hosting | `https://chemicheck119.site/` HTTP 200 | 정적 화면 진입점만 응답 |
| FE Cloud Run | 직접 URL HTTP 503 | 현재 서비스 가용성 실패 |
| BE Cloud Run | `/actuator/health` HTTP 503 | 현재 API 가용성 실패 |
| Artifact Registry | `BILLING_DISABLED` 403 | image 조회·배포 경로 사용 불가 |
| Cloud Build | 과거 build 목록 조회 성공 | 과거 control-plane 조회이며 새 build 실행 가능성은 아님 |
| Cloud SQL | `RUNNABLE`, `ALWAYS`, ZONAL, 10GB | 실제 접속·과금 여부를 별도 확인해야 하는 메타데이터 |
| Compute Engine | instance·disk 0개 | 잔존 VM·GPU disk 없음 |

정적 웹 화면이 200이어도 핵심 BFF가 503이므로 전체 서비스가 가용하다고 표현할 수 없다.
Cloud Run 목록과 revision 이름이 조회돼도 같은 이유로 “현재 배포 운영 중”이라고 표현하지
않는다.

## 추가한 실행 Gate

모든 GCP 변경·빌드·평가 실행 스크립트는 다음 두 조건을 순서대로 확인한다.

1. `chemi-check` 프로젝트에 billing account 참조가 있고 `billingEnabled=true`인가?
2. 연결된 billing account의 실제 상태가 `open=true`인가?

둘 중 하나라도 실패하거나 조회 권한이 없으면 리소스 변경 전에 종료한다. 실제 폐쇄 계정에서
종료 코드 1과 `linked billing account is closed; refusing GCP mutation`을 확인했다.

적용 대상은 image build, 데이터 upload, Cloud Run Job·서비스 배포 및 실행, Speech API
identity 구성, ML 환경 구성, 신규 quota 요청, 1회성 LoRA runner다. 기존 quota 요청 조회와
읽기 전용 감사는 결제 장애 상태에서도 원인을 확인할 수 있어 이 Gate를 적용하지 않는다.

이 Gate는 비용 차단 장치가 아니다. 계정이 열려 있으면 기존 실행 전 견적, 70,000원 누적
상한, task 1·retry 0·timeout, min 0·max 1, 수명주기 정책이 계속 필요하다.

## 사실 상태

| 항목 | 상태 |
|---|---|
| 결제 계정 open 상태 사전 확인 | 구현 완료 |
| 월 50,000원 Budget 객체 | 부분 구현 또는 개발용 데모 |
| Firebase 정적 화면 | 부분 구현 또는 개발용 데모 |
| Cloud Run 기반 FE·BE·Model·Speech | 부분 구현 또는 개발용 데모 |
| 결제 재개 후 전체 smoke | 설계 완료·구현 전 |
| 상용 운영·고가용성 | 검증되지 않은 가설 |

Budget 객체는 존재하지만 연결 계정이 폐쇄돼 현재 알림 효력을 검증하지 못했다. Cloud Run은
과거 개발용 배포 기록은 있으나 현재 FE·BE 가용성 확인에 실패했다.

## 복구 후 재검증 순서

결제 재개 또는 열린 계정 재연결은 결제 권한을 가진 사람이 수행해야 한다. 재개 직후 바로
GPU 학습이나 image build를 시작하지 않고 다음 순서를 따른다.

1. billing project 참조와 account `open=true`를 다시 확인한다.
2. 월 50,000원·현재 지출 100% Budget 객체의 대상 계정과 알림 수신자를 확인한다.
3. Artifact Registry read와 Cloud Run FE·BE health를 확인한다.
4. Cloud SQL 실제 접속과 현재 과금 예상치를 확인하고, 필요 없다면 중지 정책을 결정한다.
5. FE → BE → Model·Speech의 개발용 smoke를 다시 실행한다.
6. 실행 전 총 개발비 ceiling이 70,000원 이하인지 갱신한다.

결제 계정을 다시 여는 행위, 다른 계정 연결, 유료 실행은 이 감사의 완료 범위가 아니다.

## 재현 명령

```bash
gcloud billing projects describe chemi-check
gcloud billing accounts describe REDACTED
gcloud billing budgets list --billing-account=REDACTED
gcloud run services list --project=chemi-check --region=asia-northeast3
gcloud sql instances describe chemicheck119-pg-staging --project=chemi-check
gcloud compute instances list --project=chemi-check
gcloud artifacts repositories list --project=chemi-check --location=asia-northeast3
curl --max-time 20 https://chemicheck119.site/
curl --max-time 20 https://chemicheck119-fe-develop-REDACTED.a.run.app/
curl --max-time 20 https://chemicheck119-be-staging-REDACTED.a.run.app/actuator/health
```

실제 billing account ID와 사용자 계정은 Git에 기록하지 않는다.
