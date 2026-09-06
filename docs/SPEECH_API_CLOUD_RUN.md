# Speech API private Cloud Run 배포

## 사실 상태

- build·identity·deploy·read-only audit 하네스: **구현 완료**
- 실제 GCP service·IAM·Secret 적용: **설계 완료·구현 전**
- cold/warm 추론·부하·Backend 연결: **설계 완료·구현 전**
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
| CPU | 4 | faster-whisper CPU int8 개발 시작점, 실측 전 |
| memory | 8GiB | embedded small model 시작점, 실측 전 |
| concurrency | 1 | application 추론 semaphore와 일치 |
| min instances | 0 | 유휴 상시 compute 비용 방지 |
| max instances | 1 | 과금·동시 추론 상한 |
| request timeout | 60초 | Speech hard limit 60초 음성과는 별개인 초기 경계 |
| retry | Backend 자동 재시도 없음 | 중복 고비용 추론 방지 |

`min=0`은 요청이 없을 때 instance를 0으로 내릴 수 있다는 뜻이지 무료 보장이 아닙니다.
image 저장비, Cloud Build, 실제 요청 compute는 별도 청구될 수 있습니다. 실제 build·deploy 전
image 크기와 누적 개발비를 확인하고 총 70,000원 상한을 넘을 가능성이 있으면 중단합니다.

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

## 실제 배포 후 추가 검증

- Backend 후보 revision에서 liveness·readiness 확인
- 인증 없는 직접 호출 401/403, Backend 경유 인증 호출 성공
- 제한 PCM WAV 1건의 실제 cold/warm latency·RTF·memory 측정
- 16MiB·60초·WAV 형식 경계와 동시 요청 `SPEECH_BUSY` 확인
- 원본 음성·전사문이 Cloud Logging·DB·GCS에 저장되지 않았는지 감사
- 실패 시 Backend Speech 환경변수가 없는 직전 revision으로 rollback

이 검증 전에는 “GCP에서 실제 음성 기능 운영”이라고 주장하지 않습니다.
