"""本地 Web 界面。零额外依赖，只用标准库 http.server。

    radar ui            # 打开 http://127.0.0.1:8787
"""
from __future__ import annotations

import json
import subprocess
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from ..core import (
    COMPANIES_FILE,
    CONFIG_DIR,
    DATA_DIR,
    HOME,
    PROFILE_FILE,
    dump_yaml,
    list_industries,
    load_profile,
    load_watchlist,
    load_yaml,
)

STATIC = Path(__file__).resolve().parent / "static"
RUN_STATE: dict = {"running": False, "log": [], "ok": None}


def _latest_report() -> dict:
    files = sorted((DATA_DIR / "scored").glob("*.json"))
    if not files:
        return {}
    try:
        return json.loads(files[-1].read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _run_in_background(extra: list[str]) -> None:
    if RUN_STATE["running"]:
        return
    RUN_STATE.update(running=True, log=[], ok=None)

    def worker() -> None:
        cmd = [sys.executable, "-m", "radar.cli", "run"] + extra
        RUN_STATE["log"].append("$ " + " ".join(cmd))
        try:
            p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 text=True, bufsize=1, cwd=str(HOME))
            assert p.stdout
            for line in p.stdout:
                RUN_STATE["log"].append(line.rstrip())
            p.wait()
            RUN_STATE["ok"] = p.returncode == 0
        except Exception as exc:  # noqa: BLE001
            RUN_STATE["log"].append(f"[错误] {exc}")
            RUN_STATE["ok"] = False
        finally:
            RUN_STATE["running"] = False

    threading.Thread(target=worker, daemon=True).start()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a) -> None:  # 静音
        pass

    # ---- 工具 ----
    def _json(self, data, code: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _file(self, path: Path, ctype: str) -> None:
        if not path.exists():
            self.send_error(404)
            return
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}

    # ---- 路由 ----
    def do_GET(self) -> None:  # noqa: N802
        p = self.path.split("?")[0]
        if p in ("/", "/index.html"):
            return self._file(STATIC / "index.html", "text/html; charset=utf-8")
        if p == "/api/bootstrap":
            profile = load_profile() if PROFILE_FILE.exists() else None
            wl = load_watchlist()
            st = wl.stats()
            return self._json({
                "initialized": profile is not None,
                "profile": profile.raw if profile else None,
                "industries": [
                    {"id": i["id"], "name": i.get("name"), "name_en": i.get("name_en"),
                     "description": i.get("description")}
                    for i in list_industries()
                ],
                "stats": st,
                "companies": wl.active()[:300],
                "report": _latest_report(),
                "run": {"running": RUN_STATE["running"], "ok": RUN_STATE["ok"]},
                "home": str(HOME),
            })
        if p == "/api/run-log":
            return self._json({"running": RUN_STATE["running"], "ok": RUN_STATE["ok"],
                               "log": RUN_STATE["log"][-400:]})
        return self.send_error(404)

    def do_POST(self) -> None:  # noqa: N802
        p = self.path.split("?")[0]
        data = self._body()

        if p == "/api/profile":
            raw = data.get("profile") or {}
            dump_yaml(raw, PROFILE_FILE)
            return self._json({"ok": True, "path": str(PROFILE_FILE)})

        if p == "/api/companies":
            raw = data.get("companies")
            if not isinstance(raw, list):
                return self._json({"ok": False, "error": "companies 必须是数组"}, 400)
            existing = load_yaml(COMPANIES_FILE)
            existing["companies"] = raw
            existing.setdefault("meta", {})["updated_at"] = __import__("datetime").date.today().isoformat()
            dump_yaml(existing, COMPANIES_FILE)
            return self._json({"ok": True, "count": len(raw)})

        if p == "/api/run":
            if RUN_STATE["running"]:
                return self._json({"ok": False, "error": "已经有一个任务在跑"}, 409)
            _run_in_background(data.get("extra") or [])
            return self._json({"ok": True})

        if p == "/api/init":
            from ..init_wizard import run_init
            prof = run_init(industry=data.get("industry") or "general",
                            cities=data.get("cities") or ["北京"],
                            non_interactive=True, force=bool(data.get("force")))
            return self._json({"ok": True, "profile": prof.raw})

        return self.send_error(404)


def serve(port: int = 8787, open_browser: bool = True) -> None:
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}"
    print(f"\n  🎯 求职雷达 Web 界面")
    print(f"     {url}")
    print(f"     数据目录：{HOME}")
    print(f"     Ctrl+C 退出\n")
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n  已停止。")
    finally:
        httpd.server_close()
