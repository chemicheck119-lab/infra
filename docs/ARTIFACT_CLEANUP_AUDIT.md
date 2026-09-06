# Artifact Registry 정리 후보 감사

> 상태: **읽기 전용 감사 구현 완료** / **정리 정책 적용 전**  
> 관측 시각: 2026-09-07 KST (`2026-09-06T18:51:51Z`)  
> 대상: `chemi-check`, `asia-northeast3`, `chemicheck119`

## 결론

현재 서비스·Job·평가 재현에 쓰이는 image를 보존한 뒤, 생성 후 30일이 지난 비참조 image **7개**를 정리 검토 후보로 분류했다. 후보 manifest 크기 합계는 **2,248,097,461 bytes(약 2.25GB)**다. 이는 공유 layer를 중복 계산할 수 있으므로 실제 삭제 시 회수되는 저장공간이나 비용 절감액이 아니다.

이번 작업은 후보 계산만 수행했다. Artifact Registry cleanup policy, tag 제거, revision 제거, image 삭제는 적용하지 않았다.

## 판정 규칙

다음 중 하나라도 해당하는 digest는 보존한다.

1. Cloud Run service의 `status.traffic`에 남은 revision이 참조한다.
2. Cloud Run Job이 참조한다.
3. 잠금 평가 재현을 위해 `config/artifact_cleanup_protected.json`에 등록했다.

이 조건에 해당하지 않고 생성 후 30일이 지난 image만 후보로 분류한다. Traffic 0%라도 tag가 남은 revision은 URL로 접근할 수 있으므로 보존 대상으로 처리했다.

## 관측 결과

| 항목 | 실제 관측값 | 해석 |
| --- | ---: | --- |
| Repository 저장량 | 10,097,524,121 bytes | Artifact Registry가 보고한 전체 크기 |
| Image manifest | 41개 | `--include-tags` 조회 결과 |
| 보존 image | 31개 | Service·Job·수동 목록 중 하나 이상에서 참조 |
| 30일 초과 정리 후보 | 7개 | 자동 삭제가 아닌 사람 검토 대상 |
| 후보 manifest 합계 | 2,248,097,461 bytes | 실제 회수량·절감액으로 사용 금지 |
| 0% traffic tag | 27개 | tag URL 의존성 확인 후 별도 정리 필요 |

후보 구성은 `be` 2개와 `model-api-preview` 5개다. `fe-develop`과 잠금 평가용 `speech-eval` image는 현재 후보에 포함되지 않았다.

## 재현 방법

```bash
python3 scripts/audit_artifact_cleanup.py \
  --output ../private-data/experiments/infra/artifact-cleanup-audit-20260907-r1/report.json
```

검사기는 `gcloud ... list`와 `gcloud ... describe`만 사용한다. 출력 보고서는 원본 운영 식별정보를 포함할 수 있어 Git에 커밋하지 않고 `private-data`에 권한 `0600`으로 저장한다.

### 잠금 artifact

- 보고서: `private-data/experiments/infra/artifact-cleanup-audit-20260907-r1/report.json`
- SHA-256: `d8742c48c821de1961a2bcb4dd179cd1169a7ce551c93d2f74f86f59d30b5695`
- 권한: `0600`

## 다음 적용 Gate

다음 작업은 아직 구현하지 않았다.

- 27개 0% traffic tag의 실제 URL·롤백 의존성 확인
- delete policy와 keep policy를 함께 둔 Artifact Registry 공식 dry-run
- Cloud Build bucket lifecycle 후보 산정
- 삭제 전후 Cloud Run·Job smoke test와 rollback 절차 확정
- 사용자 승인 후 실제 정책 적용

공식 cleanup policy dry-run도 repository 설정을 변경한다. 따라서 현재 읽기 전용 감사와 구분하며, 승인 전에는 실행하지 않는다.

## 주장할 수 있는 범위

- **구현 완료:** 현재 GCP 참조와 수동 보존 digest를 반영하는 읽기 전용 후보 감사
- **부분 구현 또는 개발용 검증:** 현재 시점에서 7개 후보와 27개 0% traffic tag를 식별
- **설계 완료·구현 전:** 공식 dry-run, lifecycle, 실제 정리 및 사후 smoke 검증
- **검증되지 않은 가설:** 후보 manifest 합계만큼 저장공간·비용이 줄어든다는 주장

## 한계

- Cloud Run·Job과 수동 목록 밖에서 digest를 직접 참조하는 외부 사용자는 자동 탐지하지 못한다.
- manifest 크기는 layer 공유를 반영한 순수 회수량이 아니다.
- 생성 30일 기준은 현재 감사용 기준이며 운영 보존정책으로 승인된 값이 아니다.
- 한 시점의 snapshot이므로 실제 적용 직전에 동일 감사를 다시 실행해야 한다.

## 공식 문서

- [Artifact Registry cleanup policy](https://docs.cloud.google.com/artifact-registry/docs/repositories/cleanup-policy)
- [Cloud Run traffic tag·revision 관리](https://cloud.google.com/run/docs/rollouts-rollbacks-traffic-migration)
