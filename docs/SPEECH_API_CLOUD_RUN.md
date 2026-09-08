# Speech API private Cloud Run 배포

## 사실 상태

- build·identity·deploy·read-only audit 하네스: **구현 완료**
- 실제 GCP service·IAM·Secret 적용: **개발용 preview 구현 완료**
- Backend 연결·제한 WAV 1건 추론: **부분 구현 또는 개발용 데모**
- cold/warm·직접 5×5 burst·실패 경계: **부분 구현 또는 개발용 데모 — 검증 완료**
- numeric process/cgroup memory 관측: **부분 구현 또는 개발용 데모 — 검증 완료**
- 축소 자원·GPU 비교: **설계 완료·구현 전**
- 실제 Pad·현장 무전 효과: **검증되지 않은 가설**

실제 service revision과 추론을 확인한 뒤에도 범위는 **부분 구현 또는 개발용 데모**입니다.
상용 운영·고가용성·현장 무전 검증으로 표현하지 않습니다.

## 경계

```text
Pad browser
  → public Backend BFF + 사용자 session
  → Cloud Run IAM identity token + server-side API key
  → private Speech API candidate
  → 검토 가능한 전사 초안
```

Speech API에는 `allUsers`·`allAuthenticatedUsers` invoker를 두지 않습니다. VPC connector가
없는 현재 Backend가 기본 `run.app` URL로 호출할 수 있도록 ingress는 `all`이지만,
IAM invoker는 Backend runtime service account로 제한합니다. 따라서 `ingress=all`을
“공개 서비스”로, 또는 IAM이 없다는 뜻으로 해석하면 안 됩니다.

## 자원 시작점과 비용 제한

| 설정 | 값 | 근거 상태 |
|---|---:|---|
| CPU | 4 | 개발용 preview 적용값, 최적값 검증 전 |
| memory | 8GiB | 개발용 preview 적용값, process high-water mark 관측 후에도 축소 판단 전 |
| Cloud Run request concurrency | 4 | 짧은 burst를 앱까지 전달하는 제한값 |
| application 추론 semaphore | 1 | 실제 모델 동시 실행 방지 |
| application queue wait | 1초 | 추가 요청을 빠른 429로 기권 |
| min instances | 0 | 유휴 상시 compute 비용 방지 |
| max instances | 1 | 과금·동시 추론 상한 |
| request timeout | 60초 | Speech hard limit 60초 음성과는 별개인 초기 경계 |
| retry | Backend 자동 재시도 없음 | 중복 고비용 추론 방지 |

`min=0`은 요청이 없을 때 instance를 0으로 내릴 수 있다는 뜻이지 무료 보장이 아닙니다.
image 저장비, Cloud Build, 실제 요청 compute는 별도 청구될 수 있습니다. 실제 build·deploy 전
image 크기와 누적 개발비를 확인하고 총 70,000원 상한을 넘을 가능성이 있으면 중단합니다.

Cloud Run concurrency를 1로 두면 추가 요청이 container 앞에서 직렬 대기해 application의
1초 busy gate가 실행되지 않습니다. 2026-09-07 동시 2요청 smoke에서 두 번째 E2E가
9.2474초로 늘어난 현상을 확인해 request concurrency를 4로 조정했습니다. 이는 실제 모델을
4개 동시에 실행한다는 뜻이 아닙니다. application semaphore는 계속 1이며, 추가 요청은
`TRANSCRIBER_BUSY` 429로 기권해야 합니다. 4개를 넘는 burst의 플랫폼 queue는 별도 부하
평가가 필요합니다. 변경 후 동시 2요청에서 한 건은 정상 전사되고 다른 한 건은 1.3062초에
Backend `SPEECH_BUSY`·retryable 응답으로 기권해 이 가설을 조건부로 채택했습니다.

동시 5요청 관찰에서는 Cloud Run 플랫폼이 capacity 초과 요청에 반환한 비JSON 429를 기존
Backend가 422 계약 오류로 잘못 변환하고, 일부 요청은 직렬 대기하는 결함이 추가로
드러났습니다. Backend
`c17bd1f`부터 인스턴스당 전사 1건만 Speech API로 전달하고 나머지는 즉시
`SPEECH_BUSY`로 기권합니다. 수정 후 현재 `max instances=1`인 staging에서 동시 5요청은
200 1건과 429 4건이었고 실제 Speech 호출도 1건이었습니다. 이는 현재 단일 Backend
인스턴스의 bounded burst 근거이지, 다중 인스턴스 전역 제한이나 대규모 용량 근거가
아닙니다.

## 실행 Gate

1. 새 API key를 권한 0600의 로컬 파일에 생성합니다. 줄바꿈·공백 없는 단일 ASCII 문자열만
   허용하며 값을 shell 인자나 Git에 남기지 않습니다.
2. identity와 Secret을 최초 한 번 구성합니다.
3. 현재 `speech-service/main`의 정확한 40자 commit으로 image를 build합니다.
4. build 결과의 digest URI만 배포합니다.
5. 읽기 전용 감사가 모두 통과한 뒤 Backend 후보 revision을 별도로 연결합니다.

```bash
scripts/setup_speech_api_identity.sh /secure/new-speech-api-key.txt

scripts/build_speech_api_image.sh \
  /path/to/speech-service \
  SPEECH_MAIN_COMMIT

scripts/deploy_private_speech_api.sh \
  asia-northeast3-docker.pkg.dev/chemi-check/chemicheck119/speech-api@sha256:DIGEST

scripts/audit_private_speech_api.sh \
  /private/path/speech-api-cloud-run-audit.json
```

setup은 기존 Secret을 덮어쓰거나 회전하지 않습니다. build는 기존 commit tag를 덮어쓰지
않고, deploy는 immutable digest만 받습니다. audit 결과에는 Secret 값·전사문·음성이
포함되지 않습니다.

## 2026-09-07~08 개발용 preview 증거

| 항목 | 관찰값 | 주장 범위 |
|---|---|---|
| Speech revision | `chemicheck119-speech-api-preview-00003-n4f` | 배포 식별자 |
| image digest | `sha256:d24fbab1019c8248b71c937e99a203c8d249075c01779e1523000509638cfa05` | 불변 image |
| IAM audit v2 | 11개 검사 통과 | 공개 invoker 없음, Backend runtime SA만 호출 |
| audit artifact SHA-256 | `2de2b7e0474e75bfe2dc4349504ea293a2f0f5981f176fd99c119fe7ee866e1c` | 비공개 집계 보고서 무결성 |
| 실제 입력 | AIHub 광주 화재 신고 Validation WAV 30.16초 1건 | 승인 공개 데이터 기반 smoke |
| Backend BFF 응답 | HTTP 200, `TRANSCRIBED`, 156자·13 segment | 연결성과 응답 계약 |
| 추론 | 4.5004초, RTF 0.1492 | 단일 요청 관찰값 |
| 안전 경계 | 원음 미보존, hotword 미사용, 사람 검토 필수 | 응답 계약 관찰값 |
| 판단 경계 | 물질 식별·CAS 확인·위험도 판단 모두 미수행 | LLM 판단 제한 확인 |
| 동시 2요청 | 200 1건 + 429 `SPEECH_BUSY` 1건 | 단일 추론·빠른 기권 |
| backpressure artifact SHA-256 | `1a969c7c32aa920021866fdf120626ae49c88ad8118ef55a955a53208beb22ea` | A/B 집계 보고서 무결성 |
| Backend backpressure revision | `chemicheck119-be-staging-rc17bd1f1` | 인스턴스당 전사 1건 fail-fast Gate |
| 동시 5요청 | 200 1건 + 429 `SPEECH_BUSY` 4건, Speech 호출 1건 | 단일 Backend 인스턴스의 bounded burst |
| Backend backpressure artifact SHA-256 | `73cdcf127709a357944e0eb130ae06ca6e6afe3433feceb1688f970de7971e1d` | 비공개 집계 보고서 무결성 |
| Backend timeout 계약 | 100ms client timeout·500ms mock header 지연, upstream 호출 1건, BFF 504 | 축소된 로컬 mock의 분류·무재시도 계약 |
| Speech 직접 5×5 burst | 200 18건 + 앱 429 7건, 플랫폼 429 0건 | 짧은 합성 입력의 queue·busy 분포 |
| 직접 burst E2E | median 6.5303초, p95 13.4498초, max 23.6898초 | 추론 외 대기 포함 client 관찰값 |
| 직접 burst 추론 | median 3.3290초, max 3.6127초, RTF max 0.4855 | CPU small int8 성공 18건 |
| 직접 burst artifact SHA-256 | `fbb3a2c1c382139d796fe0e1bbf23e60e101cec1eb1eb2501275faae71578093` | 비공개 집계 보고서 무결성 |
| runtime storage 권한 | project role 0, bucket binding 0, user key 0 | GCS 영속 경로 차단 |
| storage audit artifact SHA-256 | `7ebacda7ea430063bba79f32e5993549465d0ff23b8609fada7e3d935ffe5c37` | 권한·코드 경계 집계 무결성 |
| scale-to-zero cold | startup→ready 7.7734초, E2E 14.4097초 | 단일 cold 관찰값 |
| cold artifact SHA-256 | `737436d1411a6bb1f7a9acaf757b397913cebd37bc3abd67fc7c6884e53a645f` | cold 집계 보고서 무결성 |
| resource revision | `chemicheck119-speech-api-preview-tsfix` | timestamp 경계 수정 뒤 개발용 preview |
| resource image digest | `sha256:6788bbd3b6ee061457c2015bcb5a3f46ddee42dc08446777b5283a341bd00bd5` | 불변 image |
| 30초 resource protocol | 동시 2요청 × 3 batch, 200 3건 + 앱 429 3건 | 공개 합성 파생 입력, 실제 무전 아님 |
| 30초 CPU 추론 | median 7.7339초, RTF max 0.2917 | 성공 3건의 제한 관찰 |
| process max RSS | median 1.5360GiB, max 1.5363GiB | process 생명주기 high-water mark |
| cgroup current | median 1.2947GiB, max 1.2949GiB | 관측 시점 값, peak 아님 |
| resource artifact SHA-256 | `fd3cff29c56c3bbb5bd6322f4b8cf37ec94c0f2f6463718c56463f4424221f9f` | 음성·전사문 없는 비공개 집계 보고서 |

요청 ID `REQ-BFF-SPEECH-SMOKE-20260907-002`의 Backend 로그에는 route, HTTP 상태,
소요시간만 기록됐습니다. 이 1건으로 STT 정확도, 교차지역 일반화, 현장 무전 성능이나 실제
안전성을 주장하지 않습니다.

## 추가 검증

- Backend 후보 revision에서 liveness·readiness 확인 — 완료
- 인증 없는 직접 호출 401/403, Backend 경유 인증 호출 성공 — 완료
- 제한 PCM WAV 1건의 Backend 연결·RTF 확인 — 완료
- 동일 instance warm-sequence 3회 latency — 완료
- 성공 요청별 numeric process/cgroup memory와 request ID 3/3 대조 — 완료,
  process max RSS 최대 1.5363GiB, cgroup peak는 플랫폼 v1 경로에서 미관측
- 독립 scale-to-zero cold start latency 1건 — 완료, 분포·tail은 미측정
- 16MiB·60초·WAV 형식 경계와 동시 요청 `SPEECH_BUSY` 확인 — 완료
- 단일 Backend 인스턴스의 동시 5요청 fail-fast와 Speech 호출 1건 확인 — 완료
- private Speech API 직접 동시 5요청 5회와 Cloud Run request log 25/25 대조 — 완료,
  warm batch에서 4개 직렬 성공·긴 tail이 관찰되어 Backend fail-fast 유지
- 축소 timeout mock의 `SPEECH_TIMEOUT`·BFF 504·자동 재시도 0건 확인 — 완료,
  실제 Cloud Run 45초 timeout은 미측정
- 원본 음성·전사문 비보존 — 로그·코드·migration·GCS runtime 권한 확인 완료
- 실패 시 Backend Speech 환경변수가 없는 직전 revision으로 rollback

남은 실제 Cloud Run timeout fault·더 큰 부하·다중 인스턴스 전역 제한·자원 축소 비교는
infra #27에서 추적합니다. 이 검증 전에는 “GCP에서 실제 음성 기능 운영”이라고 주장하지
않습니다.

직접 burst의 protocol·실패 r1·GPU 판단은
[`SPEECH_API_BURST_EVALUATION.md`](SPEECH_API_BURST_EVALUATION.md)에 분리해 기록했습니다.
