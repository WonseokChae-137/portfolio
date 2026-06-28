# 📊 포트폴리오 모니터링 대시보드

한국(KOSPI/KOSDAQ) + 미국 종목 보유 현황을 시세·환율 반영해서 계산하고,
HTML 대시보드로 출력하는 도구. Notion 자동 동기화도 옵션으로 지원.

종목을 한 번 적어두면, 실행할 때마다 최신 시세를 받아와 손익·비중·섹터 분산을
다시 계산한다. 종목이 바뀔 때만 `holdings.csv`를 수정하면 끝.

---

## 1. 최초 셋업 (한 번만)

1. 파이썬 설치 (3.10 이상): https://www.python.org/downloads/
   설치 시 "Add Python to PATH" 체크.
2. 이 폴더에서 터미널/명령프롬프트를 열고:

   ```
   pip install -r requirements.txt
   ```

## 2. 보유 종목 입력

`holdings.csv` 를 열어 본인 종목으로 교체. 컬럼 설명:

| 컬럼 | 설명 | 예시 |
|------|------|------|
| bucket | 계좌 묶음 (일반/DC/IRP 등) | `일반` |
| account | 세부 계좌명 (자유) | `키움5154` |
| ticker | 한국=6자리 코드, 미국=티커 | `005930`, `AAPL` |
| market | `KR` 또는 `US` | `KR` |
| shares | 보유 수량 | `50` |
| avg_cost | 평균 매수단가 (한국=원, 미국=달러) | `68000` |
| name | (선택) 표시 이름. 비우면 자동 | `삼성전자` |
| sector | (선택) 자산군/섹터. 비우면 자동 | `미국주식` |

- 같은 종목이 같은 버킷 안 여러 계좌에 있으면 자동으로 가중평균 합산됨.
- 다른 버킷(예: DC와 IRP)에 같은 종목이 있으면 각 버킷에 따로 표시됨.

## 3. 실행

```
python portfolio_dashboard.py
```

→ 같은 폴더에 `dashboard.html` 이 생성됨. 브라우저로 열어서 확인.
   매번 실행하면 최신 시세로 갱신된다.

쏠림 알림 기준은 `config.yaml` 의 `concentration_threshold`(종목),
`sector_threshold`(섹터)로 조정.

---

## 4. Notion 연동 (선택)

대시보드 내용을 Notion 데이터베이스로도 자동 동기화하려면:

1. https://www.notion.so/my-integrations 에서 **새 통합(Internal)** 생성 →
   토큰(`secret_...` 또는 `ntn_...`) 복사.
2. Notion에서 DB를 만들 **부모 페이지**를 하나 열고, 우상단 `•••` → 연결 →
   방금 만든 통합을 추가(페이지 공유).
3. 그 페이지 URL 끝의 32자리 ID를 복사. (예: `.../My-Page-`**`a1b2...`**)
4. `config.yaml` 작성:

   ```yaml
   notion:
     enabled: true
     token: "여기에_통합_토큰"
     parent_page_id: "위에서_복사한_페이지_ID"
     database_id: ""
   ```

5. DB 최초 생성:

   ```
   python portfolio_dashboard.py --setup-notion
   ```

   → 출력된 `database_id` 를 `config.yaml` 의 `database_id` 에 넣는다.
6. 이후부터는 그냥 `python portfolio_dashboard.py` 실행하면
   HTML 생성 + Notion 갱신이 함께 된다.

---

## 자동 실행(완전자동)으로 만들기

매번 직접 실행하기 싫으면 OS 스케줄러에 등록:

- **Windows**: 작업 스케줄러 → 매일 장 마감 후(예: 16:00) `python ...\portfolio_dashboard.py` 실행
- **Mac/Linux**: `crontab -e` →
  ```
  0 16 * * 1-5 cd /경로/portfolio && /usr/bin/python3 portfolio_dashboard.py
  ```

---

## 참고

- 시세·환율은 **마지막 종가 기준**(무료 데이터 소스)이라 실시간 호가와 차이가 날 수 있다.
- 미국 종목 섹터는 yfinance, 한국은 거래소 상장정보를 사용. 일부 종목은 섹터가 비어
  "기타/Unknown"으로 표시될 수 있다.
- 데이터 소스(FinanceDataReader, yfinance)가 일시적으로 막히면 해당 종목만 건너뛰니
  잠시 후 다시 실행.

---

## 🩺 재무건강 대시보드 (현금비중 · runway · 대출)

`finance.yaml` 에 자산·현금·대출 값을 적어두면 재무건강 화면을 만들어준다.

- **단독 화면 (B)**: `python finance_health.py` → `finance_health.html`
- **통합 화면 (A)**: `python portfolio_dashboard.py` 실행 시, finance.yaml이 있으면
  포트폴리오 대시보드 아래에 재무건강 섹션이 자동으로 붙는다.

### finance.yaml 핵심
- `cash`: 진짜 현금(예수금·입출금 잔고)만. 마이너스 여력은 여기 넣지 말 것.
- `loans`: 실제 사용 잔액 기준. `type: minus`면 `unused`(미사용 여력)는
  '쓰면 빚'이라 현금과 분리해서 표시됨.
- `monthly_living`: 월 고정 생활비. **비우면(0)** runway는 '대출 상환만 감당하는
  기간'이라 실제보다 길게 나오니 주의. `living_estimate`로 대략 가늠치만 참고 표시됨.
- 값이 바뀌면 이 파일만 고치면 됨.

### 표시 원칙
진짜 현금 🟢 / 마이너스 여력 🟡(쓰면 빚) / 대출 🔴 를 분리해 색으로 구분.
마이너스 미사용분을 현금으로 착각하지 않도록 설계됨.

---

## ▶ 평소 사용법 (통합본만)

평소엔 이거 하나만 실행하면 된다:

```
python portfolio_dashboard.py
```

→ `dashboard.html` 이 열리면 **포트폴리오 + 재무건강이 한 화면**에 다 나온다.
(finance.yaml 이 있으면 재무건강 섹션이 자동으로 아래에 붙음)

`finance_health.py` 는 재무건강만 따로 보고 싶을 때만 쓰는 보조용. 평소엔 안 써도 됨.

---

## 🎯 리밸런싱 (1/2/3군 목표 비중)

`groups.csv` 에 종목→군 매핑이 있으면, 대시보드에 리밸런싱 섹션이 자동으로 붙는다.

- **목표 비중**: `config.yaml` 의 `group_targets` (기본 1군50·2군20·3군10·현금20)
- **분모**: 주식 평가금액 + 현금성(finance.yaml의 cash, 적금 포함)
- **표시**: 군별 목표 vs 현재(▲매수/▼정리), 미분류 정리 후보, 부족 군 매수 후보
- 종목별 금액은 비중 안 정했으므로 후보만 제시 → 직접 선택
- 연금계좌(DC·IRP) ETF도 성격대로 군에 배정됨(groups.csv). 단 연금은 개별주 매수 불가라 실제 매매는 일반계좌에서.

군 배정을 바꾸려면 `groups.csv` 의 group 칼럼만 수정하면 된다.
