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
| GPU 평가·파인튜닝 | L4 할당량 요청 거절·구현 전 |
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

## 비용 경계

- 업로드 대상은 전체 87GB가 아니라 광주 화재 ZIP 4개(약 526MB)입니다.
- 평가 Job은 수동 실행만 가능하고 실패 시 자동 재시도하지 않습니다.
- 버킷 수명주기로 원본과 실험 산출물을 자동 정리합니다.
- 결제 계정 budget API 권한이 없어 금액 기반 예산 알림은 아직 만들지 못했습니다. 따라서 현재 비용 통제는 리소스 상한과 수명주기에 의존합니다.
