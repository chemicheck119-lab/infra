# Speech API 직접 burst 평가

## 사실 상태

- 반복 5×5 client·Cloud Run request log 대조 하네스: **구현 완료**
- 현재 CPU 개발용 preview 직접 burst 평가: **부분 구현 또는 개발용 데모 — 검증 완료**
- Backend를 우회한 Speech API 직접 공개 사용: **기각**
- numeric process/cgroup memory 관측: **부분 구현 또는 개발용 데모 — 검증 완료**
- 축소 자원 후보·GPU A/B: **설계 완료·구현 전**
- 실제 현장 무전·상용 traffic·고가용성: **검증되지 않은 가설**

## 평가 목표와 Gate

| 항목 | 사전 정의 |
|---|---|
| 목표 이름 | private Speech API 동시 5요청의 플랫폼 queue·app busy 분포 측정 |
| 실패 사례 | 플랫폼 대기와 앱 429를 구분하지 못해 긴 대기를 빠른 기권으로 오해 |
| 가설 | CPU 4·8GiB, Cloud Run concurrency 4, app semaphore 1, max instance 1에서 burst가 정상 전사 또는 명시적 429로 종료되고 안전 응답 계약을 유지 |
| 기준선 | 2026-09-07 단일 관찰과 Backend BFF 5요청 결과 |
| 입력 | 7.4414초·16kHz·mono PCM WAV 공개 합성 파생물 1건 반복 |
| 분할 | 정확도 학습·평가가 아니므로 train/test 분할 비해당 |
| 주요 지표 | HTTP 상태, app/platform 429, client latency, processing time, RTF, Cloud Run request log 대조율 |
| 안전 지표 | 5xx·전송 오류 0, 계약 위반 0, 원음 미보존, hotword 미사용, 물질 식별·CAS 확인·위험도 판단 미수행 |
| 채택 조건 | 5 batch 완료, 모든 응답이 200/429, 성공 응답 안전 계약 통과, 25개 요청 로그 일치 |
| 기각·중단 | 5xx·전송 오류·계약 위반, 성공 없는 batch, 민감정보 저장 |
| 비용 경계 | 새 build·deploy·GPU 없음, max instance 1, 자동 재시도 0, 회당 예상 증분 100원 미만 |

정확도나 현장 안전성 평가가 아니므로 전사문과 정답 label은 수집하지 않았습니다. 같은 음성을
반복한 이유는 입력 차이를 고정하고 요청 수락·대기·기권 분포만 보기 위해서입니다.

## r2 실제 결과

평가 대상은 `chemicheck119-speech-api-preview-00003-n4f`, image
`sha256:d24fbab1019c8248b71c937e99a203c8d249075c01779e1523000509638cfa05`입니다.
입력 SHA-256은 `6b584709c1dda8c35f6a900282fc39fa398af84a57b7e5461f3d489684dfd257`,
비공개 집계 보고서 SHA-256은
`fbb3a2c1c382139d796fe0e1bbf23e60e101cec1eb1eb2501275faae71578093`입니다.

| batch | HTTP 200 | 앱 `TRANSCRIBER_BUSY` 429 | 플랫폼 429 |
|---:|---:|---:|---:|
| 1 | 2 | 3 | 0 |
| 2 | 4 | 1 | 0 |
| 3 | 4 | 1 | 0 |
| 4 | 4 | 1 | 0 |
| 5 | 4 | 1 | 0 |
| **합계** | **18** | **7** | **0** |

| 지표 | median | p95 nearest-rank | max |
|---|---:|---:|---:|
| client E2E | 6.5303초 | 13.4498초 | 23.6898초 |
| 성공 요청의 실제 추론 | 3.3290초 | 3.6127초 | 3.6127초 |
| 성공 요청 RTF | 0.4474 | 0.4855 | 0.4855 |
| client E2E − 추론 추정치 | 3.7081초 | 20.6431초 | 20.6431초 |

`E2E − 추론`에는 Cloud Run queue뿐 아니라 네트워크·요청 전송·응답 직렬화가 포함되므로
정확한 queue 시간이라고 부르지 않습니다. 그러나 최대 E2E 23.69초에 비해 실제 추론 최대가
3.61초였다는 차이는 긴 꼬리를 모델 계산만으로 설명할 수 없음을 보여줍니다.

Cloud Run request log는 전용 User-Agent·revision·시간·POST 전사 경로로 제한해 25건을
25건 모두 대조했습니다. client와 log의 상태 분포도 `200 18 + 429 7`로 일치했습니다.
보고서에는 request metadata만 포함하고 요청·응답 body, 음성, 전사문, API Key, ID token은
포함하지 않았습니다.

## 해석과 결정

Cloud Run `container concurrency=4`는 4개가 동시에 모델 추론된다는 뜻이 아닙니다.
application semaphore는 모델 추론을 1개로 제한하지만, 아직 container에 들어오지 않은 요청은
플랫폼에서 기다렸다가 앞 요청이 끝난 뒤 진입할 수 있습니다. 그 결과 warm batch에서는
동시 제출 5개 중 4개가 직렬로 성공했고 마지막 성공 요청은 오래 기다렸습니다.

결정은 다음과 같습니다.

1. **평가 결과 채택:** 5×5 측정과 안전 계약, client/log 대조가 재현됐습니다.
2. **현재 CPU preview 조건부 유지:** 짧은 합성 입력의 성공 추론 RTF는 모두 1 미만이었습니다.
3. **Speech API 직접 공개 사용 기각:** app semaphore만으로 모든 초과 요청의 E2E fail-fast를
   보장할 수 없습니다.
4. **Backend BFF Gate 유지:** 인스턴스당 실제 Speech 전달을 1건으로 제한하고 나머지를 빠른
   `SPEECH_BUSY`로 반환하는 현재 경계가 필요합니다.
5. **현재 queue 문제 해결용 GPU 투입 보류:** GPU가 약 3초대 추론을 줄일 수는 있어도 최대
   20.64초의 비추론 구간이나 platform admission semantics를 제거하지는 못합니다.

GPU A/B는 장문·통신 왜곡 입력에서 CPU RTF 또는 명시적 latency SLO가 반복 실패하거나,
채택 가능한 Whisper LoRA 후보가 생겼을 때 별도 목표로 수행합니다. 이 평가만으로 GPU가
불필요하다고 일반화하지 않습니다.

## r1 실패 기록

첫 실행의 client 결과는 `200 17 + 앱 429 8`이었지만, log query 시간 여유 구간에 직전
readiness 1건이 포함돼 25개 전사 요청을 26개로 집계했습니다. 보고서 SHA-256은
`812364972584700b9ca1bb9dd240b9ca03ded4fd542c59c92cba7079c26580c3`이며 판정은
**조건부 채택**입니다. 평가기를 POST `/api/v1/transcriptions`로 제한한 commit
`3ac4203` 이후 r2를 다시 실행했습니다. r1에도 원음·전사문·인증값은 저장되지 않았습니다.

## 30초 resource 관측과 timestamp 회귀

### 첫 실행: 안전 중단

2026-09-08에 공개 합성 7.4414초 입력을 공백과 함께 결정적으로 반복해 정확히 30초인
파생 입력을 만들었습니다. 이는 **resource 관측용 모의 입력**이며 실제 신고전화·현장 무전
또는 정확도 평가 자료가 아닙니다.

첫 실행은 `MODEL_OUTPUT_INVALID` 502 한 건과 앱 429 한 건으로 중단했습니다. 같은
`faster-whisper small`·CPU int8 조건을 로컬에서 전사문 없이 숫자만 재현한 결과, 입력과 모델
audio duration은 모두 30.00초였지만 마지막 segment end가 30.26초였습니다. overlap·비유한값·
quality range 위반은 각각 0건이었습니다. API가 허용하던 0.10초보다 0.26초가 커서 실패한
것입니다.

Speech Service PR #52는 입력 끝을 최대 0.5초 초과한 마지막 `end_seconds`만 실제 입력 길이로
제한하고, segment 시작이 입력 밖이거나 더 큰 초과는 계속 502로 차단합니다. 전사문은
수정하지 않습니다. 전체 unittest 109개를 통과한 뒤 merge commit
`0dfd4905f754ca2ff2758a7c7a0828055bb71786`으로 배포했습니다.

### 재실행 r3: 채택

| 항목 | 실제 관찰값 |
|---|---:|
| revision | `chemicheck119-speech-api-preview-tsfix` |
| image digest | `sha256:6788bbd3b6ee061457c2015bcb5a3f46ddee42dc08446777b5283a341bd00bd5` |
| protocol | 30초 입력, 동시 2요청 × 3 batch, 자동 retry 0 |
| 응답 | HTTP 200 3건 + 앱 429 3건 + 플랫폼 429 0건 |
| client E2E | median 4.8546초, p95/max 9.0350초 |
| 성공 추론 | median 7.7339초, p95/max 8.7518초 |
| 성공 RTF | median 0.2578, max 0.2917 |
| 성공 E2E − 추론 추정치 | median 0.2665초, max 0.2832초 |
| resource log 대조 | 성공 request ID 3/3 일치, numeric allowlist 통과 |
| 안전 Gate | 13개 검사 모두 통과 |

| memory counter | median | max | 해석 한계 |
|---|---:|---:|---|
| cgroup current | 1.2947GiB | 1.2949GiB | 요청별 peak가 아닌 관측 시점 container 값 |
| process current RSS | 1.4354GiB | 1.4356GiB | cgroup과 집계 의미가 달라 직접 차감 금지 |
| process max RSS | 1.5360GiB | 1.5363GiB | process 생명주기 누적 high-water mark |
| cgroup limit | 8GiB | 8GiB | 배포 설정과 일치 |
| cgroup peak | 관측 불가 | 관측 불가 | Cloud Run에서 노출된 v1 경로에 counter 없음 |

정적 IAM·Secret·scale audit 11개도 모두 통과했습니다. 정적 audit SHA-256은
`13421419ffd65c39e01899ce4d4c126d901b9840f54a2ae0a0b63601ee391bd5`, 재실행 보고서
SHA-256은 `fd3cff29c56c3bbb5bd6322f4b8cf37ec94c0f2f6463718c56463f4424221f9f`입니다.
보고서에는 음성·전사문·응답 body·credential을 넣지 않았습니다.

수정 image의 Cloud Build ID는 `b03b17df-3646-4ad2-bec1-37ce34dae91a`이고 build 시작부터
종료까지 약 2.54분이었습니다. [Cloud Build 기본 worker 공식 단가](https://cloud.google.com/build/pricing)
`$0.006/min`을 적용한 무료 구간 전
목록가 추정은 약 `$0.0152`이며 실제 청구액은 아닙니다. GPU·새 상시 instance·자동 retry는
사용하지 않았습니다.

첫 성공 보고서 r2는 protocol 숫자는 동시 2요청으로 정확했지만 주장 범위 문장 한 곳이
`동시 5요청`으로 고정돼 있었습니다. 원본 r2 SHA-256
`1bea2331219ebced7f68b81939e0225ffd662784551d887dd667f362ba6dcc1d`를 보존하고 평가기의 해당
문장을 실행 인자로 생성하도록 수정한 뒤 r3를 다시 실행했습니다. r3의 평가기 SHA-256은
`1f4ef96fca1d19d71dc1fbd2c39687b9aa605ce795673c0091b26efdf75ab7db`입니다.

결정은 **resource 관측 방법과 이번 측정값 채택**, **8GiB 축소 판단 보류**입니다. 동일 합성
입력 성공 3건과 process high-water mark만으로 안전한 최소 memory를 확정할 수 없고 cgroup
peak도 관측되지 않았습니다. 30초 입력의 CPU RTF가 모두 1 미만이므로 지금 GPU를 먼저
투입할 근거도 없지만, 이 결과를 장문·실제 traffic·LoRA 후보에 일반화하지 않습니다.

## 재현 명령

원음과 보고서는 Git 밖의 승인된 비공개 경로에 둡니다.

```bash
python3 scripts/evaluate_private_speech_burst.py \
  --project chemi-check \
  --region asia-northeast3 \
  --service chemicheck119-speech-api-preview \
  --audio /private/path/input.wav \
  --output /private/path/report.json \
  --batches 5 \
  --requests-per-batch 5 \
  --pause-seconds 2 \
  --timeout-seconds 70 \
  --require-resource-logs
```

평가기 source SHA-256은
`c936ad7e93ac4936ce34928a54aaf299bea88c0a9df402f6bd502e3b8f82a218`입니다.

## 주장할 수 없는 범위

- 실제 신고 전화나 현장 무전 정확도·안전성
- 동시에 모델 추론된 process 수와 cgroup peak의 계측값
- 다중 인스턴스 전역 제한·상용 capacity·고가용성
- GPU의 실제 latency·비용 우위
- 조직 전체 로그·개인정보 감사

다음 실험은 별도 candidate revision에서 process peak를 더 촘촘히 관찰하고, 실제 peak 근거가
확보된 경우에만 CPU·memory 축소 후보를 비교하는 것입니다.
