import os
import sys
import threading
import time
import traceback
import webbrowser
from contextlib import closing
import socket
import shutil
import subprocess


def _open_browser(url: str) -> None:
    time.sleep(1.2)
    webbrowser.open(url)


def _get_log_path() -> str:
    user_profile = os.getenv("USERPROFILE") or os.path.expanduser("~")
    candidates = []
    for base in (
        os.getenv("OneDrive"),
        os.getenv("OneDriveConsumer"),
        os.getenv("OneDriveCommercial"),
    ):
        if base:
            candidates.append(os.path.join(base, "Documentos", "Dados Funcionarios"))
            candidates.append(os.path.join(base, "Documents", "Dados Funcionarios"))
    if user_profile:
        candidates.append(os.path.join(user_profile, "Documents", "Dados Funcionarios"))
        candidates.append(os.path.join(user_profile, "Documentos", "Dados Funcionarios"))
    candidates.append(os.getcwd())
    for folder in candidates:
        if not folder:
            continue
        try:
            os.makedirs(folder, exist_ok=True)
            return os.path.join(folder, "app.log")
        except OSError:
            continue
    return os.path.join(os.getcwd(), "app.log")


def _log_message(message: str) -> None:
    log_path = _get_log_path()
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(log_path, "a", encoding="utf-8") as handle:
            handle.write(f"[{timestamp}] {message}\n")
    except OSError:
        pass


def _is_truthy(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def _find_edge() -> str | None:
    candidates = [
        shutil.which("msedge"),
        shutil.which("msedge.exe"),
    ]
    program_files = os.getenv("ProgramFiles")
    program_files_x86 = os.getenv("ProgramFiles(x86)")
    local_app_data = os.getenv("LOCALAPPDATA")
    for base in (program_files, program_files_x86):
        if base:
            candidates.append(
                os.path.join(base, "Microsoft", "Edge", "Application", "msedge.exe")
            )
    if local_app_data:
        candidates.append(
            os.path.join(local_app_data, "Microsoft", "Edge", "Application", "msedge.exe")
        )
    for path in candidates:
        if path and os.path.exists(path):
            return path
    return None


def _open_edge_app(url: str) -> bool:
    edge_path = _find_edge()
    if not edge_path:
        return False
    try:
        subprocess.Popen([edge_path, f"--app={url}"], close_fds=True)
        return True
    except OSError:
        return False


def _open_ui(url: str) -> None:
    time.sleep(1.2)
    if _is_frozen() and _open_edge_app(url):
        return
    webbrowser.open(url)


def _wait_for_port(port: int, attempts: int = 80, delay: float = 0.3) -> bool:
    for _ in range(attempts):
        with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
            sock.settimeout(0.6)
            try:
                if sock.connect_ex(("127.0.0.1", port)) == 0:
                    return True
            except OSError:
                pass
        time.sleep(delay)
    return False


def main() -> None:
    _log_message("Iniciando aplicativo...")
    try:
        from backend import app
        import uvicorn

        host = os.getenv("HOST", "127.0.0.1")
        port = int(os.getenv("PORT", "8000"))
        url = os.getenv("APP_URL") or f"http://127.0.0.1:{port}/"
        use_webview = _is_truthy(os.getenv("APP_WEBVIEW")) or (
            _is_frozen() and not _is_truthy(os.getenv("APP_NO_WEBVIEW"))
        )

        if use_webview:
            try:
                import webview
            except Exception:
                _log_message("Webview indisponível, abrindo via navegador/app.")
            else:
                server_thread = threading.Thread(
                    target=uvicorn.run,
                    kwargs={
                        "app": app,
                        "host": host,
                        "port": port,
                        "log_config": None,
                        "access_log": False,
                    },
                    daemon=True,
                )
                server_thread.start()
                if not _wait_for_port(port):
                    raise RuntimeError("Servidor não respondeu na porta.")
                webview.create_window("UltraRH", url, width=1400, height=900)
                webview.start()
                return

        if not (_is_truthy(os.getenv("APP_NO_BROWSER")) or _is_truthy(os.getenv("ULTRARH_NO_BROWSER"))):
            if _is_truthy(os.getenv("APP_OPEN_BROWSER")) or _is_truthy(os.getenv("ULTRARH_OPEN_BROWSER")) or _is_frozen():
                threading.Thread(target=_open_ui, args=(url,), daemon=True).start()
        _log_message(f"Iniciando servidor em {url}")
        uvicorn.run(
            app,
            host=host,
            port=port,
            log_config=None,
            access_log=False,
        )
    except Exception:
        _log_message("Falha ao iniciar:\n" + traceback.format_exc())
        raise


if __name__ == "__main__":
    main()
