#!/usr/bin/env python3
"""Relay 메뉴바 위젯 — macOS.

relay 계정 풀 서버(GET /accounts)에서 풀 전체 계정의 잔량을 읽어 메뉴바에 '현재
계정'의 가장 빡센 사용률을 표시하고, 드롭다운에서 계정을 클릭하면 이 맥의 CLI
로그인을 그 계정으로 전환한다. (core.py 재사용: 세션·서버 주소·select·apply)

Windows 작업표시줄 위젯의 macOS 대응 — 단일 계정이 아니라 풀 전체를 본다.

실행:  .venv/bin/python menubar.py        (또는 ./relay-menubar)
진단:  .venv/bin/python menubar.py --once  (서버 응답만 출력, 창 안 띄움)
인증:  ~/.account-pool/session (Relay 앱에서 로그인하면 생성됨)
서버:  환경변수 POOL_SERVER (기본값은 core.py 참조)
"""
import json
import sys
import urllib.error
import urllib.request
from functools import partial
from pathlib import Path

import rumps

# 전환 코어 (같은 디렉터리의 core.py)
sys.path.insert(0, str(Path(__file__).resolve().parent))
import core  # noqa: E402

REFRESH_SEC = 60
PROVIDER_LABEL = {"claude": "CLAUDE", "codex": "CODEX"}


def _dot(pct):
    if pct is None:
        return "⚪"
    if pct >= 90:
        return "🔴"
    if pct >= 70:
        return "🟡"
    return "🟢"


def _pct(v):
    return None if v is None else int(round(float(v)))


def _worst(a):
    """계정에서 가장 높은(빡센) 사용률 %. 값이 없으면 None."""
    vals = [x for x in (_pct(a.get("session_pct_used")),
                        _pct(a.get("weekly_pct_used"))) if x is not None]
    return max(vals) if vals else None


def _p(v):
    return f"{v}%" if v is not None else "-"


def fetch_accounts():
    """GET /accounts → (accounts, error). 세션 없거나 서버 불통이면 error 문자열."""
    session = core._load_session()
    if not session:
        return None, "로그인 필요"
    req = urllib.request.Request(
        f"{core.SERVER}/accounts", headers={"X-Pool-Session": session})
    try:
        with urllib.request.urlopen(req, timeout=12) as r:
            return json.load(r), None
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return None, "로그인 필요"
        return None, f"HTTP {e.code}"
    except urllib.error.URLError:
        return None, "서버 불통"
    except Exception as e:  # noqa: BLE001
        return None, str(e)


class RelayMenuBar(rumps.App):
    def __init__(self):
        super().__init__("Relay", title="◔ …", quit_button=None)
        self._accounts = []
        self._error = None
        self.timer = rumps.Timer(self.refresh, REFRESH_SEC)
        self.timer.start()
        self.refresh(None)

    # ------------------------------------------------------------ 데이터
    def refresh(self, _sender):
        accounts, error = fetch_accounts()
        self._accounts, self._error = accounts or [], error
        self._render()

    def _current(self):
        return next((a for a in self._accounts if a.get("is_current")), None)

    # ------------------------------------------------------------ 렌더
    def _render(self):
        # --- 메뉴바 타이틀 ---
        if self._error:
            self.title = "⚠️ Relay"
        else:
            cur = self._current()
            w = _worst(cur) if cur else None
            self.title = f"{_dot(w)} {w}%" if w is not None else "◔ Relay"

        # --- 드롭다운 ---
        self.menu.clear()
        items = []

        if self._error:
            items.append(rumps.MenuItem(f"⚠️  {self._error}"))
            if self._error == "로그인 필요":
                items.append(rumps.MenuItem("Relay 앱에서 로그인하세요"))
        else:
            cur = self._current()
            if cur:
                items.append(rumps.MenuItem(f"현재: {cur.get('label') or '?'}"))
                items.append(rumps.MenuItem(
                    f"    5h {_p(_pct(cur.get('session_pct_used')))}"
                    f"  ·  7d {_p(_pct(cur.get('weekly_pct_used')))}"))
            else:
                items.append(rumps.MenuItem("선택된 계정 없음"))

            for provider in ("claude", "codex"):
                group = [a for a in self._accounts if a.get("provider") == provider]
                if not group:
                    continue
                items.append(rumps.separator)
                items.append(rumps.MenuItem(PROVIDER_LABEL[provider]))
                group.sort(key=lambda x: (not x.get("is_current"), _worst(x) or 0))
                for a in group:
                    items.append(self._account_item(provider, a))

        items.append(rumps.separator)
        items.append(rumps.MenuItem("새로고침", callback=self.refresh))
        items.append(rumps.MenuItem("종료", callback=rumps.quit_application))
        self.menu.update(items)

    def _account_item(self, provider, a):
        cur = a.get("is_current")
        mark = "●" if cur else "○"
        name = a.get("label") or a.get("account_name") or "?"
        if a.get("error"):
            title = f"{mark} ⚪ {name} — 오류"
        else:
            title = (f"{mark} {_dot(_worst(a))} {name}"
                     f"   5h {_p(_pct(a.get('session_pct_used')))}"
                     f"  7d {_p(_pct(a.get('weekly_pct_used')))}")
        cb = None if cur else partial(self.on_switch, provider, a.get("id"), name)
        return rumps.MenuItem(title, callback=cb)

    # ------------------------------------------------------------ 전환 (메인 스레드)
    def on_switch(self, provider, account_id, name, _sender):
        """계정 클릭 → 이 맥의 CLI 로그인을 그 계정으로 즉시 전환."""
        try:
            core.select(provider, int(account_id), name or "")
            core.apply(provider)
            self._notify(provider, f"{name} 로 전환됨")
        except urllib.error.HTTPError as e:
            if e.code >= 500:
                msg = (f"{name}: 서버 오류(500) — 이 계정 자격증명이 만료/손상됐을 수 "
                       "있어요. 재로그인 후 '계정 추가'로 다시 등록하세요.")
            elif e.code in (401, 403):
                msg = f"{name}: 인증 만료 — Relay 앱에서 다시 로그인하세요."
            else:
                msg = f"{name}: 전환 실패 HTTP {e.code}"
            self._notify(provider, msg)
        except Exception as e:  # noqa: BLE001
            self._notify(provider, f"{name}: 전환 실패 — {e}")
        self.refresh(None)

    @staticmethod
    def _notify(title, message):
        try:
            rumps.notification("Relay", title, message)
        except Exception:  # 앱 번들이 아니면 알림이 안 될 수 있음 — 무시
            pass


if __name__ == "__main__":
    if "--once" in sys.argv:
        accts, err = fetch_accounts()
        print("server:", core.SERVER)
        print("error :", err)
        print("accounts:", json.dumps(accts, ensure_ascii=False, indent=2) if accts else None)
    else:
        RelayMenuBar().run()
