# Speech API 4GiB 자원 후보 검증

## 사실 상태

- 실험 하네스의 zero-traffic tag·기대 자원 검증: **구현 완료**
- CPU 4·4GiB zero-traffic 후보 배포와 30초·58초 실제 측정: **부분 구현 또는 개발용 데모 — 검증 완료**
- CPU 4·4GiB의 preview 기본값 채택: **부분 구현 또는 개발용 데모 — 조건부 후보 채택, live traffic 미이동**
- 상용 용량·현장 음성 안정성: **검증되지 않은 가설**

이 문서는 기존 8GiB preview의 숫자 관측 뒤 처음 수행하는 자원 축소 실험을 사전 등록한다.
서비스의 100% traffic은 `tsfix` revision에 유지하고, 후보는 0% traffic tag URL로만 호출한다.

## 첫 목표 선언

| 항목 | 사전 등록 내용 |
|---|---|
| 목표 이름 | 공개 합성 30초·58초 입력에서 CPU 4·4GiB zero-traffic 후보의 안전 계약과 자원 여유를 확인 |
| 해결하려는 실패 사례 | 8GiB가 근거 없이 유지되거나, 반대로 축소 뒤 모델 load·추론 중 OOM·5xx·timeout이 발생하는 경우 |
| 가설 | 동일 image와 CPU 4에서 memory만 4GiB로 줄여도 revision Ready 상태와 요청 전 readiness preflight, bounded burst 안전 계약이 유지된다 |
| 기준선 | CPU 4·8GiB `tsfix`, 30초 2동시×3 batch에서 200 3·application 429 3·5xx 0, RTF max 0.2917, process max RSS 1.5363GiB |
| 사용할 데이터 | 공개 합성 음성의 로컬 파생 입력. 30초 SHA-256 `edf872a2590e46feaec8a63ff473bf69935b400bc6eab4b08bab761a2a826810`; 같은 입력을 반복·절단한 58초 파생본은 실행 보고서에 hash 고정 |
| 데이터 분리 | 정확도 학습·튜닝을 하지 않는 runtime 실험이다. 30초는 기존 관측과 직접 비교하고 58초는 API 상한 근처의 별도 스트레스 입력으로만 사용 |
| 주요 지표 | readiness, HTTP 200·application 429·platform 429·5xx, client latency, processing seconds, RTF, cgroup current, process current/max RSS |
| 안전 지표 | 허용 상태 외 응답 0, transport error 0, 모든 성공 응답의 판단 미수행 계약 통과, 요청·자원 로그 ID 정확 일치, 음성·전사문·credential 보고서 미포함 |
| 채택 조건 | 두 길이 모두 모든 batch 완료, batch당 성공 1건 이상, 5xx·transport error 0, 안전검사 전부 통과, 4GiB cgroup limit 일치, revision Ready·요청 전 readiness preflight 성공 |
| 기각·중단 조건 | OOM·5xx·timeout·계약 위반 1건, resource log 누락, 예상과 다른 revision·traffic·자원, 누적 추가비용 70,000원 초과 가능성 |
| 예상 컴퓨팅 비용 | 새 image build 없음, min 0·max 1·CPU 실험만 사용. 사전 예상 증분 100원 미만이며 실제 billing attribution은 제공되지 않음 |
| 주장 가능한 범위 | 동일 image의 개발용 Cloud Run zero-traffic 후보가 두 공개 합성 파생 입력에서 보인 단기 자원·안전 계약 |
| 주장 불가능한 범위 | 현장 무전 정확도, cold start 분포, 상용 capacity·p95, 장시간 memory leak, 다중 instance 고가용성, 4GiB가 모든 입력에 안전하다는 보장 |

## 실행 순서

1. 현재 100% traffic revision과 image digest를 읽기 전용으로 고정한다.
2. 같은 digest를 CPU 4·4GiB·min 0·max 1로 배포하되 `--no-traffic`과 tag를 함께 사용한다.
3. service 상태에서 tag traffic 0%, revision, memory 4GiB, Backend-only IAM을 다시 확인한다.
4. 30초 입력을 2동시×3 batch로 실행하고 request/resource log를 대조한다.
5. 30초 Gate가 모두 통과한 경우에만 58초 입력을 같은 프로토콜로 실행한다.
6. 후보가 통과해도 이 실험에서 live traffic은 이동하지 않는다. 결과와 제한을 먼저 문서·이슈에 남긴다.

```bash
python3 scripts/evaluate_private_speech_burst.py \
  --project chemi-check \
  --region asia-northeast3 \
  --service chemicheck119-speech-api-preview \
  --traffic-tag mem4g \
  --expected-cpu 4 \
  --expected-memory 4Gi \
  --audio /private/input.wav \
  --output /private/report.json \
  --batches 3 \
  --requests-per-batch 2 \
  --require-resource-logs
```

## 기준선 증거

- 8GiB resource report SHA-256:
  `fd3cff29c56c3bbb5bd6322f4b8cf37ec94c0f2f6463718c56463f4424221f9f`
- 배포 image digest:
  `sha256:6788bbd3b6ee061457c2015bcb5a3f46ddee42dc08446777b5283a341bd00bd5`
- 기준선은 동일 합성 입력 성공 3건뿐이며 cgroup peak를 관측하지 못했다. 이 한계를 후보
  결과에도 그대로 적용한다.

## 2026-09-08 실행 결과

후보 revision `chemicheck119-speech-api-preview-mem4g1`을 같은 image digest로 배포했다.
측정 뒤 동일 image·CPU 4·8GiB의 `chemicheck119-speech-api-preview-baseline1`을 다시 최신
template이자 100% traffic revision으로 배포했다. `mem4g` tag는 0%이고 service IAM invoker도
Backend runtime service account 한 개로 유지됐다. 이는 다음 배포가 4GiB 설정을 무심코
상속하지 않게 하는 복원 조치이며 image build나 후보 삭제는 수행하지 않았다.

| 항목 | 30초 후보 | 58초 후보 |
|---|---:|---:|
| 요청 protocol | 2동시 × 3 batch | 2동시 × 3 batch |
| HTTP | 200 3·application 429 3·platform 429 0 | 200 3·application 429 3·platform 429 0 |
| 5xx·transport error | 0 | 0 |
| processing median / max | 7.2204초 / 9.8700초 | 12.3601초 / 12.7794초 |
| RTF median / max | 0.2407 / 0.3290 | 0.2131 / 0.2203 |
| process max RSS | 최대 1.5422GiB | 최대 1.5422GiB |
| cgroup current | 최대 1.2954GiB | 최대 1.2962GiB |
| cgroup limit | 4GiB, 배포값과 일치 | 4GiB, 배포값과 일치 |
| request/resource log | 성공 ID 3/3 정확 일치 | 성공 ID 3/3 정확 일치 |
| 안전검사 | 13/13 통과 | 13/13 통과 |

30초의 8GiB 기준선과 비교하면 candidate processing median은 7.7339→7.2204초였고 max는
8.7518→9.8700초, RTF max는 0.2917→0.3290이었다. 성공 3건씩이므로 우열이나 p95 개선을
주장하지 않는다. 메모리 값은 두 후보 길이에서 비슷했지만 cgroup peak는 Cloud Run v1
경로에서 계속 `null`이었다.

### 판정

`CONDITIONALLY_ADOPT_4GIB_AS_ZERO_TRAFFIC_PREVIEW_CANDIDATE_KEEP_LIVE_8GIB`

- 사전 등록한 두 길이의 기능·안전 Gate는 통과했다.
- 따라서 4GiB를 **추가 검증할 우선 후보**로 채택한다.
- 동일 합성 문장을 반복한 성공 6건뿐이므로 live 100% traffic은 8GiB `baseline1`에 유지한다.
- 서로 다른 공개 승인 음성, 독립 cold start, 반복 warm sequence와 장시간 leak 관찰 전에는
  상용 기본값이나 안전한 최소 메모리로 표현하지 않는다.

### 재현성 식별자

- 30초 report SHA-256:
  `8e2cb55a381e1df0975d48ddb3b8b284b7cdbb660ece46da0632fa825214eecf`
- 58초 report SHA-256:
  `820e0857ee7dde72b349c114ac2badc3dc734aa50744bac73a529f072b768b7e`
- 실행 당시 evaluator SHA-256:
  `a6e359a5d38bd3b4b1e0956a412e00450d92dc83fedf2664e4598c71fc187e91`
- 58초 WAV SHA-256:
  `ee5b54c96cbbe46dccc606ac2ee43cdd1b796cc48ef1a4dafa2e41e6f3cd4c84`
- 58초 provenance SHA-256:
  `9aea4457807587cf2e5dc6b42414bf94c8f866aa3765c3b8db739a4b9736472d`
- 8GiB 복원 뒤 static audit 11/11 SHA-256:
  `64721f51f1f917739753b8e83d577ca45d7ba9b559385d15c11199c2aaa34d16`
- 보고서에는 음성·전사문·응답 본문·credential이 들어 있지 않다.
