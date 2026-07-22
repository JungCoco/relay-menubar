#!/usr/bin/env python3
"""전환 코어 — 선택 계정 상태 관리 + 서버에서 자격증명 수신 (크로스플랫폼, 표준 라이브러리만).

CLI:
  python3 core.py select <provider> <account_id> <label>   선택 계정 저장
  python3 core.py current <provider>                        현재 선택 출력(JSON)
  python3 core.py token <provider>                          래퍼용: claude=access token / codex=CODEX_HOME 경로

환경변수:
  POOL_SERVER  기본 http://127.0.0.1:8600
인증:
  ~/.account-pool/session 의 개인 세션 토큰을 X-Pool-Session으로 전송
"""
import os, sys, json, time, base64, tempfile, subprocess, urllib.request, urllib.error
from pathlib import Path

STATE_DIR = Path.home() / ".account-pool"
SERVER = os.environ.get("POOL_SERVER", "https://pool.3-35-2-125.nip.io")


def _private_dir(path):
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass


def atomic_write_private(path, value, encoding="utf-8"):
    """Atomically replace a private file using a same-directory 0600 temp file."""
    path = Path(path)
    _private_dir(path.parent)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        try:
            os.fchmod(fd, 0o600)
        except (AttributeError, OSError):
            pass
        mode = "wb" if isinstance(value, bytes) else "w"
        kwargs = {} if "b" in mode else {"encoding": encoding}
        with os.fdopen(fd, mode, **kwargs) as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        temporary = None
    finally:
        if temporary:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass


def _load_session():
    _private_dir(STATE_DIR)
    f = STATE_DIR / "session"
    return f.read_text(encoding="utf-8").strip() if f.exists() else ""


def _auth_headers():
    """서버 요청마다 최신 개인 세션을 읽어 전송한다."""
    return {"X-Pool-Session": _load_session()}

# Claude Code 표준 OAuth 스코프 (키체인 블롭 재구성용)
CLAUDE_SCOPES = ["user:inference", "user:profile", "user:mcp_servers",
                 "user:file_upload", "user:sessions:claude_code"]
CLAUDE_KC_SERVICE = "Claude Code-credentials"


def _state_file(provider):
    return STATE_DIR / f"selected-{provider}.json"


def select(provider, account_id, label):
    _private_dir(STATE_DIR)
    atomic_write_private(
        _state_file(provider),
        json.dumps({"account_id": int(account_id), "label": label}),
    )


def current(provider):
    f = _state_file(provider)
    if not f.exists():
        return None
    return json.loads(f.read_text(encoding="utf-8"))


def _fetch_credential(account_id):
    req = urllib.request.Request(
        f"{SERVER}/accounts/{account_id}/credential",
        headers=_auth_headers(),
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)


def token(provider):
    """래퍼가 eval로 쓰는 값을 stdout에 출력. 없으면 빈 문자열(래퍼는 기본 계정으로 진행)."""
    sel = current(provider)
    if not sel:
        return ""
    cred = _fetch_credential(sel["account_id"])
    if provider == "claude":
        return cred.get("access_token", "")
    if provider == "codex":
        return apply_codex(cred, sel["account_id"])
    return ""


def apply_claude(cred):
    """선택된 Claude 계정을 로컬 자격증명 저장소에 써서 plain `claude`도 그 계정으로.
    macOS=키체인 / Linux·WSL=~/.claude/.credentials.json 파일. (네이티브 Windows 자격증명
    관리자는 별도 — WSL 환경 권장.)"""
    blob = {"claudeAiOauth": {
        "accessToken": cred["access_token"],
        "refreshToken": cred["refresh_token"],
        "expiresAt": cred.get("expires_at_ms") or int((time.time() + 8 * 3600) * 1000),
        "scopes": CLAUDE_SCOPES,
        "subscriptionType": cred.get("subscription_type") or "max",
    }}
    payload = json.dumps(blob)
    if sys.platform == "darwin":
        acct = os.environ.get("USER") or os.environ.get("LOGNAME") or ""
        subprocess.run(
            ["security", "add-generic-password", "-U", "-s", CLAUDE_KC_SERVICE,
             "-a", acct, "-w", payload],
            check=True,
        )
    else:
        path = Path.home() / ".claude" / ".credentials.json"
        atomic_write_private(path, payload)


def _codex_account_home(account_id):
    account_id = int(account_id)
    root = STATE_DIR / "codex"
    _private_dir(STATE_DIR)
    _private_dir(root)
    home = root / str(account_id)
    _private_dir(home)
    return home


def apply_codex(cred, account_id=None):
    """Materialize one account under ~/.account-pool/codex without touching ~/.codex."""
    if account_id is None:
        selected = current("codex")
        if not selected:
            raise ValueError("Codex account id is required")
        account_id = selected["account_id"]
    home = _codex_account_home(account_id)
    dst = home / "auth.json"
    if cred.get("auth_json") is not None:
        payload = json.dumps(cred["auth_json"])
    else:
        source_home = cred.get("codex_home")
        if not source_home:
            raise ValueError("Codex credential has no auth_json")
        src = Path(os.path.expanduser(source_home)) / "auth.json"
        payload = src.read_text(encoding="utf-8")
    atomic_write_private(dst, payload)
    return str(home)


def apply(provider):
    """현재 선택 계정의 로컬 실행 자격증명을 준비한다."""
    sel = current(provider)
    if not sel:
        raise SystemExit(f"{provider} 선택 없음")
    cred = _fetch_credential(sel["account_id"])
    if provider == "claude":
        apply_claude(cred)
    elif provider == "codex":
        apply_codex(cred, sel["account_id"])


def _claude_email(access_token):
    try:
        req = urllib.request.Request(
            "https://api.anthropic.com/api/oauth/profile",
            headers={"Authorization": f"Bearer {access_token}",
                     "anthropic-beta": "oauth-2025-04-20",
                     "User-Agent": "claude-cli/2.1.179 (external, cli)"},
        )
        with urllib.request.urlopen(req, timeout=12) as r:
            acc = json.load(r).get("account", {})
            return acc.get("email") or acc.get("email_address")
    except Exception:
        return None


def _read_claude_blob():
    """현재 로그인된 Claude 자격증명 원본(JSON 문자열). macOS=키체인 / 그 외=파일."""
    if sys.platform == "darwin":
        acct = os.environ.get("USER") or os.environ.get("LOGNAME") or ""
        return subprocess.check_output(
            ["security", "find-generic-password", "-s", CLAUDE_KC_SERVICE, "-a", acct, "-w"],
            text=True).strip()
    return (Path.home() / ".claude" / ".credentials.json").read_text(encoding="utf-8")


def capture_claude():
    """현재 로그인된 Claude 계정을 캡처 → (credential, label=email)."""
    o = json.loads(_read_claude_blob())["claudeAiOauth"]
    cred = {"refresh_token": o["refreshToken"],
            "access_token": o.get("accessToken"),
            "expires_at_ms": o.get("expiresAt")}
    return cred, (_claude_email(o.get("accessToken")) or "새 Claude 계정")


def capture_codex():
    """현재 ~/.codex 에 로그인된 Codex 계정을 캡처 → (credential, label=email)."""
    auth = json.loads((Path(os.path.expanduser("~/.codex")) / "auth.json").read_text())
    email = None
    try:
        payload = auth.get("tokens", {}).get("id_token", "").split(".")[1]
        payload += "=" * (-len(payload) % 4)
        email = json.loads(base64.urlsafe_b64decode(payload)).get("email")
    except Exception:
        pass
    return {"auth_json": auth}, (email or "새 Codex 계정")


def register(provider, label, credential):
    """캡처한 계정을 서버 레지스트리에 등록(POST /accounts)."""
    data = json.dumps({"provider": provider, "label": label, "credential": credential}).encode()
    headers = _auth_headers()
    headers["Content-Type"] = "application/json"
    req = urllib.request.Request(f"{SERVER}/accounts", data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def add_current(provider):
    """현재 로그인된 계정을 캡처해 서버에 등록. (결과, label) 반환."""
    cred, label = capture_claude() if provider == "claude" else capture_codex()
    return register(provider, label, cred), label


def main(argv):
    if len(argv) < 2:
        sys.exit(__doc__)
    cmd = argv[1]
    if cmd == "select":
        select(argv[2], argv[3], argv[4])
        apply(argv[2])
    elif cmd == "add":
        result, label = add_current(argv[2])
        print(json.dumps({"label": label, "result": result}, ensure_ascii=False))
    elif cmd == "apply":
        apply(argv[2])
    elif cmd == "current":
        print(json.dumps(current(argv[2])))
    elif cmd == "token":
        try:
            sys.stdout.write(token(argv[2]))
        except urllib.error.URLError as e:
            sys.stderr.write(f"[account-pool] 서버 연결 실패: {e}\n")
            sys.exit(3)
    else:
        sys.exit(f"알 수 없는 명령: {cmd}")


if __name__ == "__main__":
    main(sys.argv)
