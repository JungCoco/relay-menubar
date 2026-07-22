# Relay 메뉴바 위젯 (macOS)

여러 Claude·Codex 계정의 구독 한도 잔량을 macOS **메뉴바**에 띄우고, 드롭다운에서
계정을 클릭하면 이 맥의 CLI 로그인이 그 계정으로 전환된다. 계정 풀 서버
([relay-account-pool](https://github.com/springdayclinic4-ux/relay-account-pool))의
`/accounts`에서 잔량을 읽는다.

> Windows 작업표시줄 위젯의 macOS 대응 — 단일 계정이 아니라 **풀 전체**를 본다.

```
🟢 32%              ← 메뉴바 (현재 계정, 가장 빡센 창)
──────────────────
현재: work-max
    5h 32%  ·  7d 61%
──────────────────
CLAUDE
  ● 🟢 work-max      5h 32%  7d 61%   ← 사용 중
  ○ 🟢 backup-max    5h  8%  7d 40%
  ○ 🔴 team-pro      5h 91%  7d 88%
CODEX
  ○ 🟢 codex-main    5h 12%  7d 20%
──────────────────
새로고침 · 종료
```

## 설치

```bash
git clone https://github.com/JungCoco/relay-menubar
cd relay-menubar
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
```

## 실행

```bash
./relay-menubar
```

메뉴바 우측에 아이콘이 뜬다. 계정을 클릭하면 이 맥의 `claude`/`codex` CLI 로그인이
그 계정으로 전환된다(macOS 키체인 / Codex `CODEX_HOME`).

- **인증**: `~/.account-pool/session` 의 개인 세션 토큰이 있어야 서버 잔량을 읽는다.
  Relay 데스크톱 앱에서 로그인하면 생성된다.
- **서버 주소**: 환경변수 `POOL_SERVER` 로 지정(미지정 시 `core.py`의 기본값).
- **진단**: `./.venv/bin/python menubar.py --once` — 창 없이 서버 응답만 출력.

## 색상

| 표시 | 사용률 |
|---|---|
| 🟢 | < 70% |
| 🟡 | 70–90% |
| 🔴 | ≥ 90% |
| ⚪ | 데이터 없음/오류 |

## 로그인 시 자동 실행 (선택)

`~/Library/LaunchAgents/` 에 LaunchAgent를 등록하면 로그인마다 자동으로 뜬다.
(추후 `install.sh`로 자동화 예정.)

## 구성

- `menubar.py` — `rumps` 메뉴바 앱 (표시·전환·새로고침)
- `core.py` — 전환 코어(세션·서버 통신·select·apply). relay-account-pool에서 벤더링.

---

Anthropic과 무관한 비공식 내부 도구. 토큰은 사용자 맥과 사설 풀 서버에만 저장된다.
[MIT License](LICENSE)
