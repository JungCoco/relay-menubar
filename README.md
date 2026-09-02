# Relay 트레이 위젯 (macOS · Windows)

여러 Claude·Codex 계정의 구독 한도 잔량을 **시스템 트레이(Windows)·메뉴바(macOS)**
아이콘에 `색 + 숫자`로 띄우고, 드롭다운에서 계정을 클릭하면 이 컴퓨터의 CLI 로그인이
그 계정으로 전환된다. **Fable 등 모델별 잔량**도 함께 표시. 계정 풀 서버
([relay-account-pool](https://github.com/springdayclinic4-ux/relay-account-pool))의
`/accounts`에서 잔량을 읽는다.

> 한 벌의 파이썬 코드(`pystray` + `Pillow`)로 Windows·macOS에서 **동일하게** 동작한다.
> % 는 아이콘 이미지 안에 렌더링돼 트레이·메뉴바 어느 쪽이든 똑같이 보인다.

```
[🟩32]  ← 트레이/메뉴바 아이콘 (현재 계정의 가장 빡센 창 %, 색=심각도)
────────────────────
현재: work-max   5h 32% · 7d 61% · Fable 58%
────────────────────
CLAUDE
  ✓ work-max      5h 32%  7d 61%  · Fable 58%   ← 사용 중
    backup-max    5h  8%  7d 40%  · Fable 12%
    team-pro      5h 91%  7d 88%  · Fable 74%
CODEX
    codex-main    5h 12%  7d 20%
────────────────────
새로고침 · 대시보드 열기 · 종료
```

## 정확도 (실시간 추적)

- **현재 사용 중인 계정(Claude·Codex 모두)**은 이 컴퓨터에서 잔량을 **직접 실시간 조회**한다.
  - Claude: 로컬 토큰으로 `usage` API 호출(세션·주간·Fable 등 모델별).
  - Codex: `codex app-server` JSON-RPC(세션·주간·모델별).
  - 30초마다 갱신 + 전환 직후 즉시 갱신. 각 줄에 남은 양 **진행바**(█/░) 표시.
  - 각 창마다 **초기화까지 남은 시간** 표시(`↻2h`·`↻5d` = 2시간·5일 후 초기화).
  - API rate limit(429) 대비: 최소 간격·지수 백오프로 조회하고, 실패해도 **마지막
    성공 라이브 값을 유지**해 숫자가 낡은 스냅샷으로 튀지 않는다. 이메일 감지도
    계정이 바뀔 때만 호출.
- **나머지 계정**은 풀 서버가 수집한 스냅샷을 사용(다소 지연될 수 있음).
- **캡처백**: 로컬 CLI가 refresh 토큰을 회전시키면 위젯이 감지해 서버 저장본을
  갱신한다(`POST /accounts/<id>/credential`, 풀에 있는 계정만). 서버 collector와
  로컬 CLI가 같은 토큰을 각자 회전시켜 서버본이 무효화되는 사고를 막는다.
  구버전 서버(엔드포인트 없음)에서는 조용히 건너뛴다.
- 표시되는 모든 %는 **남은 양**이다(`79% 남` = 79% 남음). 아이콘 숫자도 남은 %.
- 계정 앞 색 원: 🟢 사용 중 · 🔴 거의 소진(10% 이하 남음) · ⚪ 여유.

| 아이콘 색 | 남은 양 |
|---|---|
| 🟩 초록 | 30% 초과 남음 |
| 🟨 노랑 | 10–30% 남음 |
| 🟥 빨강 | 10% 이하 남음 |
| ⬜ 회색 | 데이터 없음/오류 |

## 설치

**공통** — Python 3.11+ 필요 (macOS: `brew install python@3.13` 권장 — Xcode/시스템 Python은
Xcode 업데이트 시 venv가 깨질 수 있다).

```bash
git clone https://github.com/JungCoco/relay-menubar
cd relay-menubar
python3.13 -m venv .venv                    # (Windows: py -3.13 -m venv .venv)
./.venv/bin/pip install -r requirements.txt  # (Windows: .venv\Scripts\pip install -r requirements.txt)
```

## 실행

- **macOS / Linux**: `./relay-menubar`
- **Windows**: `relay-menubar.cmd` 더블클릭 (콘솔창 없이 트레이에 상주)

계정을 클릭하면 이 컴퓨터의 `claude`/`codex` CLI 로그인이 그 계정으로 전환된다
(macOS 키체인 / Codex `CODEX_HOME` / 그 외 `~/.claude/.credentials.json`).

> Windows 트레이는 **우클릭**으로 메뉴가 열린다(네이티브 동작). macOS는 아이콘 클릭.

- **인증**: `~/.account-pool/session` 의 개인 세션 토큰이 있어야 서버 잔량을 읽는다.
  Relay 데스크톱 앱에서 로그인하면 생성된다.
- **서버 주소**: 환경변수 `POOL_SERVER` (미지정 시 `core.py` 기본값).
- **진단**: `python menubar.py --once` (서버 응답만 출력) / `--render` (샘플 아이콘 PNG).

## 플랫폼 동작 차이 (native)

| | macOS | Windows |
|---|---|---|
| 위치 | 메뉴바(우상단) | 시스템 트레이(우하단) |
| 메뉴 열기 | 아이콘 클릭 | 아이콘 우클릭 |
| 자격증명 전환 | 키체인 (`security`) | `~/.claude/.credentials.json` 파일 · Codex `CODEX_HOME` |

> 네이티브 Windows Claude 자격증명 전환은 파일 기반으로 최선 노력(best-effort).
> 미검증 환경에서는 WSL 권장(상위 프로젝트 `relay-account-pool` 참조).

## 로그인 시 자동 실행

- **macOS**: `./autostart-macos.sh` — LaunchAgent 등록(로그인마다 1개 자동 실행,
  크래시 시 자동 재시작, 메뉴 "종료"로 끄면 그대로 유지). 해제 방법은 스크립트가 안내.
- **Windows**: `autostart-windows.cmd` — 시작프로그램에 등록(콘솔창 없이 상주).

싱글턴 잠금(로컬 포트 53918)이 있어 중복 실행돼도 아이콘은 항상 하나만 뜬다.

## 구성

- `menubar.py` — `pystray` 트레이/메뉴바 앱 (아이콘 렌더·전환·60초 새로고침·싱글턴)
- `core.py` — 전환 코어(세션·서버 통신·select·apply). relay-account-pool에서 벤더링.
- `relay-menubar` / `relay-menubar.cmd` — mac·linux / windows 런처
- `autostart-macos.sh` / `autostart-windows.cmd` — 로그인 자동 실행 등록
- `make-app-macos.sh` — 더블클릭용 `~/Applications/Relay.app` 생성(venv 참조 경량 래퍼)
- `sync-core.sh` — `core.py` 벤더링 드리프트 확인/동기화(원본 = relay-account-pool)
- `brand/` — 앱 아이콘: `icon.svg`(원본) · `Relay.icns`(mac) · `icon.ico`(win) · `icon-1024.png`

## 로드맵

- [x] 브랜드 로고 앱 아이콘 (`brand/`)
- [x] 로그인 시 자동 실행 (mac LaunchAgent / win 시작 프로그램)
- [x] mac 더블클릭 번들 — `./make-app-macos.sh` → `~/Applications/Relay.app`
- [ ] win 더블클릭 `.exe` — relay-account-pool의 GitHub Actions exe 빌드 패턴 이식

---

Anthropic과 무관한 비공식 내부 도구. 토큰은 사용자 컴퓨터와 사설 풀 서버에만 저장된다.
[MIT License](LICENSE)
