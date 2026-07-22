#!/usr/bin/env python3
"""Relay 트레이/메뉴바 위젯 — macOS·Windows 공통.

pystray로 시스템 트레이(Windows)·메뉴바(macOS)에 동일하게 동작한다. 풀 계정의
잔량을 아이콘에 '색 + 숫자'로 렌더링하고(가장 빡센 창 %), 드롭다운에서 계정을
클릭하면 이 컴퓨터의 CLI 로그인을 그 계정으로 전환한다. Fable 등 모델별(scoped)
사용량도 함께 보여준다. 전환 코어는 core.py 재사용(크로스플랫폼).

실행:  python menubar.py            (mac: ./relay-menubar / win: relay-menubar.cmd)
진단:  python menubar.py --once     서버 응답만 출력(창 없음)
       python menubar.py --render   샘플 아이콘 PNG 저장(렌더 확인용)
인증:  ~/.account-pool/session (Relay 데스크톱 앱에서 로그인하면 생성)
서버:  환경변수 POOL_SERVER (기본값은 core.py 참조)
"""
import json
import sys
import threading
import urllib.error
import urllib.request
import webbrowser
from functools import partial
from pathlib import Path

import pystray
from PIL import Image, ImageDraw, ImageFont

# 전환 코어 (같은 디렉터리의 core.py)
sys.path.insert(0, str(Path(__file__).resolve().parent))
import core  # noqa: E402

REFRESH_SEC = 60
PROVIDER_LABEL = {"claude": "CLAUDE", "codex": "CODEX"}

# 심각도 색 (아이콘 배경)
SEVERITY = {
    "green": (46, 160, 67, 255),
    "yellow": (210, 153, 34, 255),
    "red": (218, 54, 51, 255),
    "gray": (110, 118, 129, 255),
}

FONT_CANDIDATES = [
    r"C:\Windows\Fonts\arialbd.ttf",
    r"C:\Windows\Fonts\segoeuib.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/Library/Fonts/Arial Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]


# ---------------------------------------------------------------- 유틸
def _pct(v):
    return None if v is None else int(round(float(v)))


def _p(v):
    return f"{v}%" if v is not None else "-"


def _main_worst(a):
    """아이콘/타이틀용 — 주 quota(세션·주간) 중 가장 높은 %."""
    vals = [x for x in (_pct(a.get("session_pct_used")),
                        _pct(a.get("weekly_pct_used"))) if x is not None]
    return max(vals) if vals else None


def _scoped_str(a):
    """모델별(scoped, 예: Fable) 사용량 문자열. 없으면 ''."""
    parts = [f"{s.get('model')} {_pct(s.get('pct_used'))}%"
             for s in (a.get("scoped") or []) if s.get("pct_used") is not None]
    return "  ".join(parts)


def _sev_key(pct, error=False):
    if error or pct is None:
        return "gray"
    if pct >= 90:
        return "red"
    if pct >= 70:
        return "yellow"
    return "green"


def _font(size):
    for path in FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, size)
        except Exception:  # noqa: BLE001
            continue
    return ImageFont.load_default()


def make_image(worst, error=False):
    """색 배지 + 숫자(%)를 그린 트레이/메뉴바 아이콘. mac·win 공통."""
    S = 66
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([3, 3, S - 4, S - 4], radius=16,
                        fill=SEVERITY[_sev_key(worst, error)])
    text = "!" if error else ("–" if worst is None else str(worst))
    size, font = 46, _font(46)
    target = S - 14
    while size > 10:
        font = _font(size)
        l, t, r, b = d.textbbox((0, 0), text, font=font)
        if (r - l) <= target and (b - t) <= target:
            break
        size -= 2
    l, t, r, b = d.textbbox((0, 0), text, font=font)
    d.text(((S - (r - l)) / 2 - l, (S - (b - t)) / 2 - t), text,
           font=font, fill=(255, 255, 255, 255))
    return img


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


# ---------------------------------------------------------------- 앱
class RelayTray:
    def __init__(self):
        self._accounts = []
        self._error = None
        self._stop = threading.Event()
        self._icon = pystray.Icon("Relay", make_image(None), "Relay")

    # --- 데이터 ---
    def _current(self):
        return next((a for a in self._accounts if a.get("is_current")), None)

    def _refresh(self, icon):
        accounts, error = fetch_accounts()
        self._accounts, self._error = accounts or [], error
        cur = self._current()
        icon.icon = make_image(_main_worst(cur) if cur else None, bool(error))
        icon.title = self._tooltip()
        icon.menu = self._build_menu()
        icon.update_menu()

    def _tooltip(self):
        if self._error:
            return f"Relay — {self._error}"
        cur = self._current()
        if not cur:
            return "Relay — 선택된 계정 없음"
        text = (f"{cur.get('label') or '?'}  "
                f"5h {_p(_pct(cur.get('session_pct_used')))} · "
                f"7d {_p(_pct(cur.get('weekly_pct_used')))}")
        sc = _scoped_str(cur)
        return f"{text} · {sc}" if sc else text

    # --- 메뉴 ---
    def _acct_label(self, a, name):
        if a.get("error"):
            return f"{name} — 오류"
        text = (f"{name}   5h {_p(_pct(a.get('session_pct_used')))}"
                f"  7d {_p(_pct(a.get('weekly_pct_used')))}")
        sc = _scoped_str(a)
        return f"{text}  · {sc}" if sc else text

    def _account_item(self, provider, a):
        name = a.get("label") or a.get("account_name") or "?"
        is_cur = bool(a.get("is_current"))
        action = None if is_cur else partial(self._on_switch, provider, a.get("id"), name)
        return pystray.MenuItem(self._acct_label(a, name), action,
                                checked=lambda _i, c=is_cur: c, enabled=not is_cur)

    def _build_menu(self):
        items = []
        if self._error:
            items.append(pystray.MenuItem(f"⚠ {self._error}", None, enabled=False))
            if self._error == "로그인 필요":
                items.append(pystray.MenuItem("Relay 데스크톱 앱에서 로그인하세요",
                                              None, enabled=False))
        else:
            cur = self._current()
            head = (f"현재: {cur.get('label') or '?'}" if cur else "선택된 계정 없음")
            items.append(pystray.MenuItem(head, None, enabled=False))
            if cur:
                items.append(pystray.MenuItem("   " + self._tooltip().split("  ", 1)[-1],
                                              None, enabled=False))
            for provider in ("claude", "codex"):
                group = [a for a in self._accounts if a.get("provider") == provider]
                if not group:
                    continue
                group.sort(key=lambda x: (not x.get("is_current"), _main_worst(x) or 0))
                items.append(pystray.Menu.SEPARATOR)
                items.append(pystray.MenuItem(PROVIDER_LABEL[provider], None, enabled=False))
                for a in group:
                    items.append(self._account_item(provider, a))
        items.append(pystray.Menu.SEPARATOR)
        items.append(pystray.MenuItem("새로고침", self._on_refresh))
        items.append(pystray.MenuItem("대시보드 열기", self._on_open))
        items.append(pystray.MenuItem("종료", self._on_quit))
        return pystray.Menu(*items)

    # --- 액션 ---
    def _on_switch(self, provider, account_id, name, icon, _item):
        try:
            core.select(provider, int(account_id), name or "")
            core.apply(provider)
            self._notify(icon, f"{name} 로 전환됨")
        except urllib.error.HTTPError as e:
            if e.code >= 500:
                msg = (f"{name}: 서버 오류(500) — 이 계정 자격증명이 만료/손상됐을 수 "
                       "있어요. 재로그인 후 다시 등록하세요.")
            elif e.code in (401, 403):
                msg = f"{name}: 인증 만료 — Relay 앱에서 다시 로그인하세요."
            else:
                msg = f"{name}: 전환 실패 HTTP {e.code}"
            self._notify(icon, msg)
        except Exception as e:  # noqa: BLE001
            self._notify(icon, f"{name}: 전환 실패 — {e}")
        self._refresh(icon)

    def _on_refresh(self, icon, _item):
        self._refresh(icon)

    def _on_open(self, _icon, _item):
        try:
            webbrowser.open(core.SERVER)
        except Exception:  # noqa: BLE001
            pass

    def _on_quit(self, icon, _item):
        self._stop.set()
        icon.stop()

    @staticmethod
    def _notify(icon, message):
        try:
            icon.notify(message, "Relay")
        except Exception:  # noqa: BLE001 - 플랫폼에 따라 미지원일 수 있음
            pass

    # --- 실행 ---
    def _setup(self, icon):
        icon.visible = True
        while not self._stop.is_set():
            self._refresh(icon)
            self._stop.wait(REFRESH_SEC)

    def run(self):
        self._icon.menu = self._build_menu()
        self._icon.run(setup=self._setup)


if __name__ == "__main__":
    if "--once" in sys.argv:
        accts, err = fetch_accounts()
        print("server:", core.SERVER)
        print("error :", err)
        print("accounts:", json.dumps(accts, ensure_ascii=False, indent=2) if accts else None)
    elif "--render" in sys.argv:
        for w, e, fn in [(5, False, "green"), (72, False, "yellow"),
                         (95, False, "red"), (None, False, "none"),
                         (None, True, "error")]:
            make_image(w, e).save(f"/tmp/relay-icon-{fn}.png")
        print("saved /tmp/relay-icon-*.png")
    else:
        RelayTray().run()
