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
import base64
import json
import os
import selectors
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from datetime import datetime, timezone
from functools import partial
from pathlib import Path

import pystray
from PIL import Image, ImageDraw, ImageFont

# 전환 코어 (같은 디렉터리의 core.py)
sys.path.insert(0, str(Path(__file__).resolve().parent))
import core  # noqa: E402

REFRESH_SEC = 60          # 메뉴/아이콘 갱신 주기
USAGE_MIN_INTERVAL = 55   # usage API 최소 재조회 간격(초) — rate limit 회피
USAGE_BACKOFF_START = 120  # 429 등 실패 후 첫 백오프(초)
USAGE_BACKOFF_MAX = 900    # 백오프 상한(초)
USAGE_CACHE_TTL = 1800     # 캐시된 라이브 값 유효(초). 넘으면 서버 스냅샷 허용
SINGLETON_PORT = 53918  # 로컬 포트 바인딩으로 중복 실행 방지
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


def _rem(used):
    """사용률 → 남은 %. 경계 뭉갬 방지: 조금이라도 썼으면 100 안 뜨고,
    완전 소진 전엔 0 안 뜨게 한다(소량 사용/거의 소진을 정확히 구분)."""
    if used is None:
        return None
    u = float(used)
    r = max(0, min(100, 100 - int(round(u))))
    if u > 0 and r == 100:
        return 99
    if u < 100 and r == 0:
        return 1
    return r


def _pr(used):
    r = _rem(used)
    return f"{r}% 남" if r is not None else "-"


def _reset(iso):
    """초기화까지 남은 시간. '↻3h'·'↻2d'·'↻45m'·'↻곧'. 없으면 ''."""
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(str(iso))
    except ValueError:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    secs = (dt - datetime.now(timezone.utc)).total_seconds()
    if secs <= 0:
        # 방금 지난 건 '↻곧', 한참 지난(낡은 스냅샷) 시각은 오신호이므로 숨김
        return "↻곧" if secs > -300 else ""
    if secs < 3600:
        return f"↻{int(secs // 60)}m"
    if secs < 86400:
        return f"↻{int(round(secs / 3600))}h"
    return f"↻{int(round(secs / 86400))}d"


def _main_worst(a):
    """아이콘/타이틀용 — 세션·주간·모델별(scoped) 중 가장 높은(빡센) %."""
    vals = [_pct(a.get("session_pct_used")), _pct(a.get("weekly_pct_used"))]
    vals += [_pct(s.get("pct_used")) for s in (a.get("scoped") or [])]
    vals = [v for v in vals if v is not None]
    return max(vals) if vals else None


def _scoped_str(a):
    """모델별(scoped, 예: Fable·Sol) 남은 양 + 초기화 시각. 없으면 ''."""
    parts = [f"{s.get('model')} {_pr(s.get('pct_used'))} {_reset(s.get('resets_at'))}".rstrip()
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


def make_image(remaining, used_worst=None, error=False):
    """색 배지 + 숫자(남은 %)를 그린 트레이/메뉴바 아이콘. 색은 소진 임박도(used)."""
    S = 66
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([3, 3, S - 4, S - 4], radius=16,
                        fill=SEVERITY[_sev_key(used_worst, error)])
    text = "!" if error else ("–" if remaining is None else str(remaining))
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


USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
USAGE_HEADERS = {"User-Agent": core.CLAUDE_UA,
                 "anthropic-beta": "oauth-2025-04-20"}


def _parse_usage(data):
    """usage API 응답 → {session/weekly pct·resets, scoped:[{model,pct,resets}]}."""
    out = {"session_pct_used": None, "session_resets_at": None,
           "weekly_pct_used": None, "weekly_resets_at": None, "scoped": []}
    for lim in data.get("limits") or []:
        kind, pct = lim.get("kind"), lim.get("percent")
        if kind == "session":
            out["session_pct_used"], out["session_resets_at"] = pct, lim.get("resets_at")
        elif kind == "weekly_all":
            out["weekly_pct_used"], out["weekly_resets_at"] = pct, lim.get("resets_at")
        elif kind == "weekly_scoped":
            model = ((lim.get("scope") or {}).get("model") or {}).get("display_name")
            if model:
                out["scoped"].append({"model": model, "pct_used": pct,
                                      "resets_at": lim.get("resets_at")})
    # limits 가 없던 구버전 응답 폴백
    if out["session_pct_used"] is None and isinstance(data.get("five_hour"), dict):
        out["session_pct_used"] = data["five_hour"].get("utilization")
        out["session_resets_at"] = data["five_hour"].get("resets_at")
    if out["weekly_pct_used"] is None and isinstance(data.get("seven_day"), dict):
        out["weekly_pct_used"] = data["seven_day"].get("utilization")
        out["weekly_resets_at"] = data["seven_day"].get("resets_at")
    return out


def fetch_live_claude_usage():
    """이 맥의 현재 Claude 토큰으로 usage API 직접 호출 → 실시간 사용량. 실패 시 None."""
    try:
        blob = json.loads(core._read_claude_blob()).get("claudeAiOauth") or {}
        token = blob.get("accessToken")
        if not token:
            return None
        req = urllib.request.Request(
            USAGE_URL, headers={"Authorization": f"Bearer {token}", **USAGE_HEADERS})
        with urllib.request.urlopen(req, timeout=12) as r:
            return _parse_usage(json.load(r))
    except Exception:  # noqa: BLE001
        return None


def _iso(epoch):
    if epoch is None:
        return None
    try:
        return datetime.fromtimestamp(float(epoch), timezone.utc).isoformat()
    except Exception:  # noqa: BLE001
        return None


def _codex_bin():
    """launchd의 최소 PATH에서도 codex 바이너리를 찾는다."""
    for cand in (shutil.which("codex"), "/opt/homebrew/bin/codex",
                 "/usr/local/bin/codex", os.path.expanduser("~/.local/bin/codex")):
        if cand and os.path.exists(cand):
            return cand
    return "codex"


def _selected_codex_home():
    """Relay가 전환한 codex 계정의 격리 홈(~/.account-pool/codex/<id>). 선택 없으면
    None(= 기본 ~/.codex). dir 생성 부작용 없이 경로만 확인한다."""
    try:
        sel = core.current("codex")
        if not sel:
            return None
        home = core.STATE_DIR / "codex" / str(sel["account_id"])
        return str(home) if (home / "auth.json").exists() else None
    except Exception:  # noqa: BLE001
        return None


def _codex_email_from(home):
    """codex auth.json(id_token JWT)에서 이메일. home=None이면 ~/.codex. 실패 시 None."""
    path = (Path(home) if home else Path(os.path.expanduser("~/.codex"))) / "auth.json"
    try:
        auth = json.loads(path.read_text(encoding="utf-8"))
        payload = auth.get("tokens", {}).get("id_token", "").split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload)).get("email")
    except Exception:  # noqa: BLE001
        return None


def _codex_rate_limits(home=None):
    """codex app-server JSON-RPC account/rateLimits/read 호출.
    home 지정 시 CODEX_HOME 으로 그 계정(=Relay 선택) 기준 사용량을 읽는다."""
    env = os.environ.copy()
    if home:
        env["CODEX_HOME"] = home
    proc = subprocess.Popen([_codex_bin(), "app-server"], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0, env=env)
    sel = selectors.DefaultSelector()
    sel.register(proc.stdout, selectors.EVENT_READ)
    try:
        def send(obj):
            proc.stdin.write((json.dumps(obj) + "\n").encode())
            proc.stdin.flush()
        send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
              "params": {"clientInfo": {"name": "relay-menubar", "version": "1"}}})
        time.sleep(0.4)
        send({"jsonrpc": "2.0", "id": 2, "method": "account/rateLimits/read", "params": {}})
        pending, deadline = b"", time.monotonic() + 15
        while time.monotonic() < deadline:
            if not sel.select(deadline - time.monotonic()):
                break
            chunk = os.read(proc.stdout.fileno(), 65536)
            if not chunk:
                break
            pending += chunk
            lines = pending.split(b"\n")
            pending = lines.pop()
            for line in lines:
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if msg.get("id") == 2:
                    return None if msg.get("error") else msg.get("result")
        return None
    finally:
        sel.close()
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()


def fetch_live_codex_usage():
    """현재 Codex 계정(Relay 선택 홈 또는 ~/.codex)으로 실시간 사용량.
    짧은 창=세션, 주 모델 풀=Sol(Spark 제외). 실패 시 None."""
    try:
        result = _codex_rate_limits(_selected_codex_home())
    except Exception:  # noqa: BLE001
        return None
    if not result:
        return None
    rl = result.get("rateLimits") or {}
    byid = result.get("rateLimitsByLimitId") or {}
    out = {"session_pct_used": None, "session_resets_at": None,
           "weekly_pct_used": None, "weekly_resets_at": None, "scoped": []}
    # 세션 창(~5h 이하). 300분 하드코딩 대신 창 길이 범위로 견고하게.
    for lim in (rl.get("primary"), rl.get("secondary")):
        if lim and lim.get("usedPercent") is not None and 0 < float(lim.get("windowDurationMins") or 0) <= 360:
            out["session_pct_used"] = lim.get("usedPercent")
            out["session_resets_at"] = _iso(lim.get("resetsAt"))
    # 주 모델 풀 → 'Sol' 로 추적 (Spark 미니 모델은 제외)
    base = (byid.get("codex") or rl).get("primary") or {}
    if base.get("usedPercent") is not None:
        out["scoped"].append({"model": "Sol", "pct_used": base.get("usedPercent"),
                              "resets_at": _iso(base.get("resetsAt"))})
    for lid, obj in byid.items():
        if lid == "codex" or not isinstance(obj, dict):
            continue
        name = obj.get("limitName") or lid
        if "spark" in name.lower() or "spark" in lid.lower() or "bengalfox" in lid.lower():
            continue  # Spark 미니 모델은 추적하지 않음
        prim = obj.get("primary") or {}
        if prim.get("usedPercent") is not None:
            out["scoped"].append({"model": name, "pct_used": prim.get("usedPercent"),
                                  "resets_at": _iso(prim.get("resetsAt"))})
    return out


def acquire_singleton():
    """이미 실행 중이면 None. 아니면 잠금 소켓을 반환(계속 살려둬야 함)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", SINGLETON_PORT))
        s.listen(1)
        return s
    except OSError:
        s.close()
        return None


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
    def __init__(self, lock=None):
        self._lock = lock  # 싱글턴 소켓 — GC로 닫히지 않게 참조 유지
        self._accounts = []
        self._local = {}   # 이 컴퓨터에 실제 로그인된 provider별 이메일
        self._error = None
        self._email_cache = {}       # provider -> email (sticky)
        self._claude_blob_hash = None
        self._usage_cache = {}       # (provider,email) -> {"data", "ts"(monotonic)}
        self._usage_next = {}        # (provider,email) -> 다음 조회 허용 시각(monotonic)
        self._usage_backoff = {}     # (provider,email) -> 현재 백오프(초)
        self._force_provider = None  # 전환 직후 1회 강제 재조회할 provider
        self._stop = threading.Event()
        self._icon = pystray.Icon("Relay", make_image(None, None), "Relay")

    # --- 데이터 ---
    def _local_email(self, provider):
        """이 컴퓨터의 현재 로그인 계정 이메일 (sticky 캐시).
        codex=로컬 JWT 디코드(무네트워크). claude=키체인 블롭이 바뀔 때만 profile API
        호출(매 주기 호출하면 rate limit 유발). 감지 실패 시 직전 캐시 유지."""
        if provider == "codex":
            # Relay 선택 홈(전환 대상) 기준으로 판정 — capture_codex는 항상 ~/.codex라 부정확
            email = _codex_email_from(_selected_codex_home())
            if email:
                self._email_cache["codex"] = email.strip().lower()
            return self._email_cache.get("codex")
        # claude
        try:
            blob = core._read_claude_blob()
        except Exception:  # noqa: BLE001
            return self._email_cache.get("claude")   # 블롭 못 읽음(일시 오류) → 직전 캐시 유지
        digest = hash(blob)
        if digest == self._claude_blob_hash and self._email_cache.get("claude"):
            return self._email_cache["claude"]   # 계정 그대로 → 네트워크 생략
        # 여기 도달 = 블롭이 바뀐(전환) 상태. 옛 이메일을 신뢰하면 안 됨 → 먼저 무효화.
        self._email_cache.pop("claude", None)
        try:
            token = json.loads(blob).get("claudeAiOauth", {}).get("accessToken")
            email = core._claude_email(token)
        except Exception:  # noqa: BLE001
            email = None
        if email:
            self._email_cache["claude"] = email.strip().lower()
            self._claude_blob_hash = digest   # 성공 시에만 갱신 → 실패 지속 시 매 주기 재시도
        else:
            self._claude_blob_hash = None      # 새 이메일 못 얻음 → None 반환(서버값 폴백)
        return self._email_cache.get("claude")

    def _is_current(self, a):
        """서버 선택이 아니라 '이 컴퓨터에서 실제 사용 중'인지로 판단."""
        email = self._local.get(a.get("provider"))
        if email:
            return (a.get("label") or "").strip().lower() == email
        return bool(a.get("is_current"))  # 로컬 감지 실패 시에만 서버값 폴백

    def _currents(self):
        return [a for a in self._accounts if self._is_current(a)]

    def _apply_live_usage(self):
        """현재 사용 중인 계정(Claude·Codex)의 잔량을 이 컴퓨터의 로컬 실시간 값으로 교체.
        rate limit(429) 대비: 최소 간격/백오프로 조회하고, 실패해도 낡은 서버 스냅샷으로
        되돌아가 숫자가 튀지 않게 '마지막 성공 라이브 값'을 캐시에서 계속 쓴다."""
        fetchers = {"claude": fetch_live_claude_usage, "codex": fetch_live_codex_usage}
        now = time.monotonic()
        forced = self._force_provider
        self._force_provider = None   # 1회 소비
        for provider, fetch in fetchers.items():
            email = self._local.get(provider)
            if not email:
                continue
            key = (provider, email)   # 계정별 캐시 — 전환 후 옛 계정 값이 새 계정에 새지 않게
            fresh = None
            if now >= self._usage_next.get(key, 0) or provider == forced:
                fresh = fetch()
                if fresh:
                    self._usage_cache[key] = {"data": fresh, "ts": now}
                    self._usage_next[key] = now + USAGE_MIN_INTERVAL
                    self._usage_backoff[key] = USAGE_BACKOFF_START
                else:  # 대개 429 → 점점 더 길게 쉰다 (그동안 이 계정의 캐시 값 사용)
                    backoff = self._usage_backoff.get(key, USAGE_BACKOFF_START)
                    self._usage_next[key] = now + backoff
                    self._usage_backoff[key] = min(backoff * 2, USAGE_BACKOFF_MAX)
            cached = self._usage_cache.get(key)
            use = fresh or (cached["data"] if cached and now - cached["ts"] < USAGE_CACHE_TTL else None)
            if not use:
                continue   # 이 계정의 라이브 값 없음 → 서버 스냅샷 그대로 둠
            for a in self._accounts:   # 같은 이메일 계정이 여러 개여도 전부 같은 값으로 (break 없음)
                if a.get("provider") == provider and (a.get("label") or "").strip().lower() == email:
                    for k in ("session_pct_used", "session_resets_at", "weekly_pct_used",
                              "weekly_resets_at", "scoped"):
                        a[k] = use[k]
                    a["error"] = None
                    a["_live"] = True

    def _refresh(self, icon):
        accounts, error = fetch_accounts()
        self._accounts, self._error = accounts or [], error
        self._local = ({} if error else
                       {p: self._local_email(p) for p in ("claude", "codex")})
        self._apply_live_usage()   # 현재 Claude 계정은 실시간 API 값으로 덮어씀
        used_worst = None
        for a in self._currents():
            w = _main_worst(a)
            if w is not None:
                used_worst = w if used_worst is None else max(used_worst, w)
        remaining = None if used_worst is None else max(0, 100 - used_worst)
        icon.icon = make_image(remaining, used_worst, bool(error))
        icon.title = self._tooltip()
        icon.menu = self._build_menu()
        icon.update_menu()

    def _tooltip(self):
        if self._error:
            return f"Relay — {self._error}"
        cur = self._currents()
        if not cur:
            return "Relay — 로그인된 계정 감지 안 됨"
        parts = []
        for a in cur:
            seg = [PROVIDER_LABEL[a["provider"]], a.get("label") or "?"]
            if a.get("session_pct_used") is not None:
                seg.append(f"5h {_pr(a['session_pct_used'])}")
            if a.get("weekly_pct_used") is not None:
                seg.append(f"7d {_pr(a['weekly_pct_used'])}")
            sc = _scoped_str(a)
            if sc:
                seg.append(sc)
            parts.append(" ".join(seg))
        return "  |  ".join(parts)

    # --- 메뉴 ---
    def _marker(self, a):
        """계정 앞 색 원: 🔴 소진(≥90%) · 🟢 사용 중 · ⚪ 그 외."""
        if _sev_key(_main_worst(a)) == "red":
            return "🔴"
        if self._is_current(a):
            return "🟢"
        return "⚪"

    @staticmethod
    def _bar(remaining, width=10):
        """남은 양 진행바. 채워진 칸 = 남은 %."""
        if remaining is None:
            return "─" * width
        filled = round(remaining / 100 * width)
        return "█" * filled + "░" * (width - filled)

    def _acct_label(self, a, name):
        su, wu = a.get("session_pct_used"), a.get("weekly_pct_used")
        sc = _scoped_str(a)
        dot = self._marker(a)
        # 세션/주간 수집이 실패해도 Fable 등 있는 데이터는 그대로 보여준다
        if a.get("error") and su is None and wu is None and not sc:
            return f"{dot} {name} — 오류"
        bar = self._bar(_rem(_main_worst(a)))   # 가장 빡센 창의 남은 양
        parts = []
        if su is not None:
            parts.append(f"5h {_pr(su)} {_reset(a.get('session_resets_at'))}".rstrip())
        if wu is not None:
            parts.append(f"7d {_pr(wu)} {_reset(a.get('weekly_resets_at'))}".rstrip())
        if sc:
            parts.append(sc)
        text = f"{dot} {name}  {bar}  {'  '.join(parts)}".rstrip()
        if a.get("error"):
            text += "  ⚠"
        return text

    def _account_item(self, provider, a):
        name = a.get("label") or a.get("account_name") or "?"
        is_cur = self._is_current(a)
        action = None if is_cur else partial(self._on_switch, provider, a.get("id"), name)
        return pystray.MenuItem(self._acct_label(a, name), action, enabled=not is_cur)

    def _build_menu(self):
        items = []
        if self._error:
            items.append(pystray.MenuItem(f"⚠ {self._error}", None, enabled=False))
            if self._error == "로그인 필요":
                items.append(pystray.MenuItem("Relay 데스크톱 앱에서 로그인하세요",
                                              None, enabled=False))
        else:
            cur = self._currents()
            if cur:
                live = " · 실시간" if any(a.get("_live") for a in cur) else ""
                items.append(pystray.MenuItem(f"현재 사용 중 (이 컴퓨터{live})", None, enabled=False))
                for a in cur:
                    items.append(pystray.MenuItem(
                        "  " + self._acct_label(a, a.get("label") or "?"), None, enabled=False))
            else:
                items.append(pystray.MenuItem("로그인된 계정 감지 안 됨", None, enabled=False))
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
            self._force_provider = provider   # 전환 직후 백오프 무시하고 새 계정 즉시 조회
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
        for used, e, fn in [(5, False, "green"), (72, False, "yellow"),
                            (95, False, "red"), (None, False, "none"),
                            (None, True, "error")]:
            rem = None if used is None else 100 - used
            make_image(rem, used, e).save(f"/tmp/relay-icon-{fn}.png")
        print("saved /tmp/relay-icon-*.png")
    else:
        lock = acquire_singleton()
        if lock is None:
            print("Relay 위젯이 이미 실행 중입니다.")
            sys.exit(0)
        RelayTray(lock).run()
