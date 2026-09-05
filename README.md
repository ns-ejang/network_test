# Network Test

네트워크 테스트 스크립트 모음과 웹 컨트롤러입니다.

## 구성

| 폴더 | 스크립트 | 설명 |
|------|----------|------|
| `extract_urllist_fromCCI` | `genai_test.sh` | GenAI URL 접속 테스트 (CCI) |
| `extract_urllist_fromCCI_jp` | `genai_test.sh` | GenAI URL 접속 테스트 (CCI JP) |
| `malicious_sites` | `web_test.sh` | 악성 사이트 URL 접속 테스트 |
| `npa` | `npa_test.sh` | NPA 네트워크 테스트 (SSH/VNC/Router) |
| `ai_guardrails` | `send_prompts.py` | ChatGPT / Copilot / Duck.ai / DeepSeek에 한글·일본어 Guardrails 프롬프트 송신 (`--lang ja`) |
| `controller` | `run.py`, `app.py` | 웹 UI 실행/중지/로그/트래픽 모니터링 |

## 웹 컨트롤러 실행

```bash
cd controller
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python run.py
```

브라우저: http://localhost:8080

## 로그 형식 (CCI / Malicious Sites)

```
[ACCESS] https://example.com   --- 200 OK
```
