# 케미체크119 Infrastructure

케미체크119의 GCP 리소스와 재현 가능한 배포 스크립트를 관리합니다.

## 원칙

- 개발용 preview와 상용 운영 경험을 구분합니다.
- 비밀값, 원본 데이터, Terraform state는 Git에 저장하지 않습니다.
- 비용이 발생하는 작업은 병렬 수와 재시도, 제한 시간을 명시합니다.
- 현재 GCP 기준 리전은 `asia-northeast3`, 프로젝트는 `chemi-check`입니다.

## 현재 상태

| 항목 | 상태 |
|---|---|
| 저장소 골격 | 구현 완료 |
| ML 비공개 저장소·실행 스크립트 | 구현 완료·GCP 적용 완료 |
| CPU Cloud Run 평가 Job | 개발용 배포·고정 77건 평가 완료 |
| Cloud Run GPU 평가 | L4 할당량 요청 거절·구현 전 |
| Compute Engine T4 LoRA runner | 구현 완료·실행 전 |
| GCP 결제 예산 알림 | 현재 사용자 권한 부족으로 미구성 |
| 고가용성 상용 운영 | 설계·검증 전 |

## ML 평가 환경

`setup_gcp_ml.sh`는 public access prevention과 uniform bucket access가 켜진 전용 버킷, 수명주기 규칙, 최소 권한 실행 계정을 만듭니다. 실행 계정은 기존 객체 읽기와 새 결과 생성만 가능하고 객체 삭제·덮어쓰기 권한은 갖지 않습니다. 원본·staging 객체는 30일, 비공개 실험 산출물은 90일 후 삭제됩니다. 복구 보관으로 인한 숨은 저장비를 피하려고 soft delete는 끕니다.

```bash
scripts/setup_gcp_ml.sh
scripts/upload_aihub_gwangju_fire.sh \
  /secure/AIHUB_DATA_ROOT /path/to/data-pipeline
scripts/build_speech_image.sh /path/to/speech-service baseline-v3
scripts/deploy_speech_eval_job.sh \
  asia-northeast3-docker.pkg.dev/chemi-check/chemicheck119/speech-eval:baseline-v3

# 먼저 3건 개발용 smoke. 정식 77건은 인자 없이 명시적으로 실행한다.
scripts/run_speech_eval.sh 3
scripts/run_speech_eval.sh
```

GPU는 현재 할당량이 0이고 Cloud Run L4 한 장 요청도 거절되어 실행할 수 없습니다. 다음 명령은 기존 요청 상태를 조회하며, 새 요청이 없는 경우에만 할당량 1을 요청합니다. 승인되어도 Job을 자동 실행하지 않습니다.

```bash
scripts/request_cloud_run_l4_quota.sh GOOGLE_ACCOUNT_EMAIL
```

Cloud Run Job은 자동 예약되지 않고 외부 요청을 받는 서비스도 아닙니다. 단일 task, 병렬도 1, 재시도 0, 최대 2시간, CPU 4개·메모리 8GiB로 제한합니다. 이 구성과 개발용 실행은 상용 운영 또는 고가용성 경험이 아닙니다.

## 적용된 평가 증거

- 실행 ID: `chemicheck119-speech-eval-cpu-wgvpc`
- 실행 결과: 77건, 실패 0건, 45분 45초
- 이미지 digest: `sha256:83f3a86f1b0221705bc156000ebe3fd3a1fa1305c41cd16e9208e393830a3708`
- 평가 manifest: `sha256:1064b73f5376b1c40dc41926912031334eaf65ea0643564e93eb3e29fdf5c3b0`
- 실행 형태: 수동 단일 task, 병렬도 1, 재시도 0, 제한 시간 2시간

이는 GCP에서 개발용 평가 Job을 실행한 증거이며 상용 서비스 운영 실적이 아닙니다.

## Whisper LoRA 1회성 L4 runner

서울·도쿄에서 T4가 노출되는 네 zone 요청이 모두 instance 생성 전 재고 부족으로
거부됐습니다. 무기한 zone 순회를 중단하고, 서울 `asia-northeast3-a`에서 지원·quota를
확인한 `g2-standard-4`·L4 1장·100GiB `pd-balanced` disk로 실행합니다. G2 machine에는
L4가 포함되므로 별도 accelerator flag를 사용하지 않습니다. 이는 새 authorization을
사용하는 별도 1회 시도이며, data bucket과 runner가 모두 서울에 있습니다.
실행은 자동화나 일정 등록 없이 사람이 정확한 `speech-service` commit과 24시간 이내 비용
견적을 전달할 때만 시작됩니다. 견적의 authorization은 commit·누적 개발비·1회 실행에
결합되고, 같은 ID의 quote/claim 객체는 GCS에서 원자적 최초 생성만 허용합니다.

```bash
scripts/run_whisper_lora_once.sh \
  SPEECH_SERVICE_MERGE_COMMIT_SHA \
  /secure/current-cost-quote.json
```

두 가지 독립 종료 장치가 있습니다.

- 로컬 runner는 VM이 멈추면 정확한 instance를 즉시 삭제합니다.
- 로컬 세션이 끊겨도 Compute Engine이 생성 후 3시간에 VM과 auto-delete boot disk를 삭제합니다.

학습 process는 2시간 45분(9,900초)에 먼저 종료해 결과 업로드 시간을 15분 남기고, training
retry는 0입니다. Python 내부 cleanup deadline은 9,600초로 더 먼저 실행됩니다. service
account에는 비공개 bucket의 기존 객체 조회와 신규 객체 생성만
허용하며 삭제·덮어쓰기는 허용하지 않습니다. 결과는 `trained_unvalidated` adapter와 집계
보고서이며, 원본 음성·전사문·model weight를 Git에 저장하지 않습니다.

현재 공식 on-demand 가격표의 GPU 포함 G2 machine 시간당 $0.706832276과 USD/KRW
1,344.547원을 적용한 3시간 견적은 25% contingency 포함 약 3,654원입니다. network
transfer ceiling은 $0.25, 등록된 독립 실행 ceiling은 9,032원이고 이전 개발비 ceiling을 더한
전체 ceiling은 59,032원입니다. 이 값은 실행 전 견적이지 실제 청구액이나 성능 성과가
아닙니다.

## 비용 경계

- 업로드 대상은 전체 87GB가 아니라 광주 화재 ZIP 4개(약 526MB)입니다.
- 평가 Job은 수동 실행만 가능하고 실패 시 자동 재시도하지 않습니다.
- 버킷 수명주기로 원본과 실험 산출물을 자동 정리합니다.
- 결제 계정 budget API 권한이 없어 금액 기반 예산 알림은 아직 만들지 못했습니다. 따라서 현재 비용 통제는 리소스 상한과 수명주기에 의존합니다.
