# Network Test Controller

4개 폴더의 shell 스크립트를 웹에서 실행/중지하고 실시간 로그를 확인하는 컨트롤러입니다.

## 대상 스크립트

| 폴더 | 스크립트 |
|------|----------|
| `extract_urllist_fromCCI` | `genai_test.sh` |
| `extract_urllist_fromCCI_jp` | `genai_test.sh` |
| `malicious_sites` | `web_test.sh` |
| `npa` | `npa_test.sh` |

## 실행 방법

```bash
cd controller
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python run.py
```

브라우저에서 http://localhost:8080 을 엽니다.

`run.py`는 웹 UI를 유지하면서 `app.py`를 시작/중지할 수 있는 supervisor입니다. 페이지 상단에서 **app.py 시작 · 중지 · 재시작** 버튼을 사용할 수 있습니다.

`app.py`를 직접 실행해도 동작하지만, 이 경우 웹에서 **시작**은 불가하고 **중지/재시작**만 가능합니다.

```bash
python run.py   # 권장
python app.py   # 직접 실행
```

포트 8080이 사용 중이면:

```bash
PORT=8081 python run.py
```

## 기능

- **실행**: 각 스크립트를 해당 폴더에서 `bash`로 실행
- **중지**: SIGINT 전송 (스크립트의 `trap`과 동일하게 동작)
- **로그**: stdout/stderr를 실시간 SSE 스트림으로 표시
- **트래픽 통계**: 상단에 스크립트별·전체 다운로드량(MB) 표시
  - **누적**: 실행 시작 이후 총 다운로드량
  - **최근 1분**: 롤링 1분 윈도우 트래픽 (1초마다 갱신)
- **app.py 제어**: supervisor(`run.py`) 실행 시 웹에서 app.py 시작/중지/재시작
