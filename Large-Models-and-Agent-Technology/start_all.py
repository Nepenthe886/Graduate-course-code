#!/usr/bin/env python
"""Large-Models-and-Agent-Technology 一键启动器（透明代理模式）。

默认把真实服务放到内部端口，让适配代理占用对外端口，
agent / Controller 的配置**不需要任何修改**：
    UAV : 8000(代理) -> 8002(MultiUAV-Plat.Server)
    LLM : 8010(代理) -> 8012(llama-server)

--direct：不用代理（服务直接占 8000/8010，老行为）。
--with-proxy：兼容旧写法（等价默认）。

其它：--no-agent / --no-controller / --force-agent / --status / --dry-run /
      --stop [--all] / --restart
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LOG_DIR = ROOT / "logs"

UAV_DIR = ROOT / "MultiUAV-Plat-v0.49_Windows"
UAV_EXE = UAV_DIR / "MultiUAV-Plat.Server.v0.49.exe"
CONTROLLER_EXE = UAV_DIR / "MultiUAV-Plat.Controller.v0.49.exe"
CONTROLLER_IMAGE = CONTROLLER_EXE.name

LLM_DIR = ROOT / "Qwen3-7B"
LLM_PY = LLM_DIR / ".venv" / "Scripts" / "python.exe"
LLM_MODEL = LLM_DIR / "models" / "Qwen3-8B-Q4_K_M.gguf"
LLM_RUNTIME = LLM_DIR / "runtime" / "llama" / "llama-server.exe"
LLM_CACHE = LLM_DIR / ".cache"

AGENT_DIR = ROOT / "agent4drone" / "agent4drone"
AGENT_MAIN = AGENT_DIR / "main.py"
NAV_SETTINGS = AGENT_DIR / "nav_settings.json"
NAV_ENTRY = ROOT / "Location_amd_Navigation" / "tools" / "nav_tools.py"
NAV_DIR = ROOT / "Location_amd_Navigation"
UAV_PROXY = NAV_DIR / "uav_adapter_proxy.py"
LLM_PROXY = NAV_DIR / "llm_adapter_proxy.py"

AGENT_PID_FILE = LOG_DIR / "agent.pid"

UAV_PUBLIC_PORT = 8000      # agent / Controller 使用
LLM_PUBLIC_PORT = 8010
UAV_UPSTREAM_PORT = 8002    # 真实服务
LLM_UPSTREAM_PORT = 8012

CREATE_NEW_CONSOLE = 0x00000010


def enable_utf8_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:
            pass


def log(message: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {message}", flush=True)


def log_file(message: str) -> None:
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        path = LOG_DIR / f"start_all_{datetime.now():%Y%m%d}.log"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {message}\n")
    except Exception:
        pass


# ---------------------------------------------------------------- 探测
def port_listening(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.4)
        return sock.connect_ex((host, port)) == 0


def http_json(url: str, timeout: float = 3.0):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except Exception as exc:  # noqa: BLE001
        return {"__error__": str(exc)}


def pid_on_port(port: int) -> int | None:
    try:
        out = subprocess.run(["netstat", "-ano"], capture_output=True, text=True,
                             encoding="utf-8", errors="replace").stdout
    except Exception:
        return None
    for line in out.splitlines():
        if f":{port}" in line and "LISTENING" in line:
            parts = line.split()
            if parts and parts[-1].isdigit():
                return int(parts[-1])
    return None


def process_running(image_name: str) -> bool:
    try:
        out = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH", "/FI", f"IMAGENAME eq {image_name}"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        ).stdout or ""
    except Exception:
        return False
    return image_name.lower() in out.lower()


def pid_alive(pid: int) -> bool:
    try:
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                             capture_output=True, text=True, encoding="utf-8",
                             errors="replace").stdout or ""
    except Exception:
        return False
    return str(pid) in out


def _pid_is_agent(pid: int) -> bool:
    """PID 存活且命令行确实是 main.py（防止 PID 复用造成假阳性）。"""
    script = (
        "Get-CimInstance Win32_Process -Filter \"ProcessId=%d\" | "
        "Where-Object { $_.CommandLine -and $_.CommandLine -match 'main\\.py' } | "
        "ForEach-Object { $_.ProcessId }" % pid
    )
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", script],
                             capture_output=True, text=True, encoding="utf-8",
                             errors="replace").stdout or ""
    except Exception:
        return False
    return str(pid) in out


def find_agent_pids() -> list[int]:
    script = (
        "Get-CimInstance Win32_Process | "
        "Where-Object { $_.Name -in @('python.exe','py.exe','pythonw.exe') -and "
        "$_.CommandLine -and $_.CommandLine -match 'main\\.py' } | "
        "ForEach-Object { $_.ProcessId }"
    )
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", script],
                             capture_output=True, text=True, encoding="utf-8",
                             errors="replace").stdout or ""
    except Exception:
        return []
    return [int(t) for t in out.split() if t.strip().isdigit()]


def agent_running() -> tuple[bool, int | None]:
    try:
        pid = int(AGENT_PID_FILE.read_text(encoding="utf-8").strip())
    except Exception:
        pid = None
    if pid and _pid_is_agent(pid):
        return True, pid
    for other in find_agent_pids():
        if other != pid:
            return True, other
    return False, pid


def kill_port(port: int, label: str) -> None:
    pid = pid_on_port(port)
    if pid is None:
        return
    log(f"  停止 {label}（端口 {port}，PID {pid}）...")
    subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)
    for _ in range(20):
        if not port_listening(port):
            return
        time.sleep(0.5)


@dataclass
class ServiceState:
    name: str
    port: int
    url: str
    healthy: bool
    pid: int | None
    detail: str

    @property
    def running(self) -> bool:
        return self.pid is not None or port_listening(self.port)


def probe_uav(port: int = UAV_PUBLIC_PORT, label: str = "UAV 后端") -> ServiceState:
    listening = port_listening(port)
    pid = pid_on_port(port) if listening else None
    url = f"http://127.0.0.1:{port}"
    if not listening:
        return ServiceState(label, port, url, False, None, "未运行")
    data = http_json(f"{url}/drones")
    if isinstance(data, list):
        return ServiceState(label, port, url, True, pid, f"{len(data)} 架无人机")
    if isinstance(data, dict) and "__error__" in data:
        return ServiceState(label, port, url, False, pid, f"端口在但 API 异常: {data['__error__'][:50]}")
    return ServiceState(label, port, url, True, pid, "API 可达")


def probe_llm(port: int = LLM_PUBLIC_PORT, label: str = "本地模型") -> ServiceState:
    listening = port_listening(port)
    pid = pid_on_port(port) if listening else None
    url = f"http://127.0.0.1:{port}"
    if not listening:
        return ServiceState(label, port, url, False, None, "未运行")
    data = http_json(f"{url}/health")
    if isinstance(data, dict) and str(data.get("status")) == "ok":
        return ServiceState(label, port, url, True, pid, "llama-server 正常")
    if isinstance(data, dict) and "__error__" in data:
        return ServiceState(label, port, url, False, pid, f"端口在但健康检查失败: {data['__error__'][:50]}")
    return ServiceState(label, port, url, True, pid, "健康检查可达")


def preflight() -> list[str]:
    problems = []
    for label, path in (
        ("UAV 后端可执行", UAV_EXE), ("Controller GUI", CONTROLLER_EXE),
        ("模型 venv", LLM_PY), ("llama-server", LLM_RUNTIME), ("GGUF 模型", LLM_MODEL),
        ("agent 入口", AGENT_MAIN), ("导航模块", NAV_ENTRY), ("导航配置", NAV_SETTINGS),
        ("UAV 适配代理", UAV_PROXY), ("LLM 适配代理", LLM_PROXY),
    ):
        if not path.is_file():
            problems.append(f"缺少 {label}: {path}")
    return problems


# ---------------------------------------------------------------- 启动
def start_uav(server_port: int) -> subprocess.Popen:
    log(f"启动 UAV 后端（端口 {server_port}，UTF-8 控制台）...")
    command = f'cmd.exe /c "chcp 65001>nul && MultiUAV-Plat.Server.v0.49.exe --port {server_port}"'
    return subprocess.Popen(command, cwd=str(UAV_DIR), creationflags=CREATE_NEW_CONSOLE)


def start_controller() -> subprocess.Popen:
    log("启动 Controller GUI ...")
    return subprocess.Popen([str(CONTROLLER_EXE)], cwd=str(UAV_DIR))


def start_llm(server_port: int) -> subprocess.Popen:
    log(f"启动本地模型（端口 {server_port}，32k 上下文）...")
    env = dict(os.environ)
    env.update({
        "TMP": str(LLM_CACHE / "temp"), "TEMP": str(LLM_CACHE / "temp"),
        "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1",
        "MODELSCOPE_CACHE": str(LLM_CACHE / "modelscope"),
        "HF_HOME": str(LLM_CACHE / "hf"),
    })
    (LLM_CACHE / "temp").mkdir(parents=True, exist_ok=True)
    return subprocess.Popen([str(LLM_PY), "-m", "src.server", "--port", str(server_port),
                             "--n-predict", "512"],
                            cwd=str(LLM_DIR), env=env, creationflags=CREATE_NEW_CONSOLE)


def start_uav_proxy(listen_port: int, upstream_port: int) -> subprocess.Popen:
    log(f"启动 UAV 适配代理（{listen_port} -> {upstream_port}：天气改写 + 悬停起飞兜底）...")
    return subprocess.Popen(
        ["py", "-3.12", str(UAV_PROXY), "--port", str(listen_port),
         "--upstream", f"http://127.0.0.1:{upstream_port}"],
        cwd=str(NAV_DIR), creationflags=CREATE_NEW_CONSOLE)


def start_llm_proxy(listen_port: int, upstream_port: int) -> subprocess.Popen:
    log(f"启动 LLM 适配代理（{listen_port} -> {upstream_port}：规则注入 + 完成校验 + 工具纠偏）...")
    return subprocess.Popen(
        ["py", "-3.12", str(LLM_PROXY), "--port", str(listen_port),
         "--upstream", f"http://127.0.0.1:{upstream_port}",
         "--uav-base", f"http://127.0.0.1:{UAV_PUBLIC_PORT}"],
        cwd=str(NAV_DIR), creationflags=CREATE_NEW_CONSOLE)


AGENT_CONSOLE_LOG = LOG_DIR / "agent_console.log"


def start_agent() -> subprocess.Popen:
    log("启动 agent GUI（py -3.12 main.py，输出记录到 logs/agent_console.log）...")
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    command = (
        f'cmd.exe /c "chcp 65001>nul && py -3.12 -u main.py '
        f'> \"{AGENT_CONSOLE_LOG}\" 2>&1"'
    )
    proc = subprocess.Popen(command, cwd=str(AGENT_DIR), creationflags=CREATE_NEW_CONSOLE)
    try:
        AGENT_PID_FILE.parent.mkdir(parents=True, exist_ok=True)
        AGENT_PID_FILE.write_text(str(proc.pid), encoding="utf-8")
    except Exception:
        pass
    log(f"  agent 启动器 PID={proc.pid}，等待 GUI 就绪 ...")
    for _ in range(20):
        time.sleep(1.0)
        pids = find_agent_pids()
        if pids:
            log(f"  [OK] agent 进程已就绪（PID {pids[0]}）")
            return proc
        if proc.poll() is not None:
            break
    log("  [FAIL] agent 未启动成功，日志尾部：")
    try:
        tail = AGENT_CONSOLE_LOG.read_text(encoding="utf-8", errors="replace").splitlines()[-15:]
        for line in tail:
            log("    " + line)
    except Exception:
        log("    （没有日志）")
    return proc


def wait_ready(probe, label: str, timeout: float, proc=None) -> bool:
    started = time.time()
    while time.time() - started < timeout:
        state = probe()
        if state.healthy:
            log(f"  [OK] {label} 就绪（{state.detail}，{time.time() - started:.1f}s）")
            return True
        if proc is not None and proc.poll() is not None:
            log(f"  [FAIL] {label} 进程已退出（exit={proc.returncode}）")
            return False
        time.sleep(1.5)
    log(f"  [FAIL] {label} 等待 {timeout:.0f}s 仍未就绪")
    return False


def wait_port(port: int, label: str, timeout: float) -> bool:
    started = time.time()
    while time.time() - started < timeout:
        if port_listening(port):
            log(f"  [OK] {label} 已监听 {port}（{time.time() - started:.1f}s）")
            return True
        time.sleep(0.5)
    log(f"  [FAIL] {label} 等待 {timeout:.0f}s 未监听 {port}")
    return False



ALL_PORTS = (8000, 8001, 8002, 8010, 8011, 8012)
RELATED_PATTERN = ("main.py", "llama-server", "adapter_proxy.py", "src.server", "src.mission_run")


def _all_related_pids() -> list[int]:
    """找出与本项目相关的 python/py 进程 PID。"""
    script = (
        "Get-CimInstance Win32_Process | "
        "Where-Object { $_.Name -in @('python.exe','py.exe','pythonw.exe') -and "
        "$_.CommandLine -and ($_.CommandLine -match 'main\\.py|llama-server|adapter_proxy\\.py|src\\.server|src\\.mission_run') } | "
        "ForEach-Object { $_.ProcessId }"
    )
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", script],
                             capture_output=True, text=True, encoding="utf-8",
                             errors="replace").stdout or ""
    except Exception:
        return []
    pids = []
    for token in out.split():
        if token.strip().isdigit():
            pids.append(int(token))
    return pids


WINDOW_PATTERNS = (
    "Qwen3-7B", "MultiUAV-Plat", "Location_amd_Navigation",
    "agent4drone", "adapter_proxy.py", "llama-server", "src.server",
    "src.mission_run", "main.py",
)


SAFE_NAMES = (
    "python.exe", "py.exe", "pythonw.exe", "cmd.exe",
    "llama-server.exe", "MultiUAV-Plat.Server.v0.49.exe",
    "MultiUAV-Plat.Controller.v0.49.exe",
)
FORBIDDEN_HINTS = ("DeepSeek Harness", "dsh", "node_modules")


def _matching_pids(extra_patterns: tuple[str, ...] = WINDOW_PATTERNS) -> list[int]:
    """所有与我们相关的进程（含 cmd.exe 包装窗口），排除宿主进程。"""
    joined = "|".join(p.replace("\\", "\\\\") for p in extra_patterns)
    names = ",".join("'%s'" % n for n in SAFE_NAMES)
    forbidden = "|".join(FORBIDDEN_HINTS)
    script = (
        "Get-CimInstance Win32_Process | "
        "Where-Object { $_.Name -in @(" + names + ") -and $_.CommandLine -and "
        "$_.CommandLine -match '" + joined + "' -and "
        "$_.CommandLine -notmatch '" + forbidden + "' } | "
        "ForEach-Object { $_.ProcessId }"
    )
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", script],
                             capture_output=True, text=True, encoding="utf-8",
                             errors="replace").stdout or ""
    except Exception:
        return []
    return [int(t) for t in out.split() if t.strip().isdigit()]


def close_previous_windows() -> None:
    """结束之前启动的全部窗口与进程（taskkill /T /F 会连带关闭控制台窗口）。"""
    log("关闭之前启动的窗口/进程 ...")
    killed = 0
    for pid in _matching_pids():
        if pid == os.getpid():
            continue
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
        killed += 1
    try:
        AGENT_PID_FILE.unlink(missing_ok=True)
    except Exception:
        pass
    log(f"  已结束 {killed} 个进程/窗口")


def clean_all() -> None:
    """彻底清理本项目相关进程与端口。"""
    log("清理残留 ...")
    # 0) 先关掉之前启动的窗口/进程
    close_previous_windows()
    # 1) 按端口
    for port in ALL_PORTS:
        pid = pid_on_port(port)
        if pid:
            log(f"  端口 {port} 被 PID {pid} 占用 -> 结束")
            subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)
    # 2) agent 树
    alive, agent_pid = agent_running()
    if agent_pid:
        subprocess.run(["taskkill", "/PID", str(agent_pid), "/T", "/F"], capture_output=True)
        log(f"  结束 agent 树 PID {agent_pid}")
    # 3) Controller
    if process_running(CONTROLLER_IMAGE):
        subprocess.run(["taskkill", "/IM", CONTROLLER_IMAGE, "/F"], capture_output=True)
        log("  结束 Controller")
    # 4) 按命令行兜底
    for pid in _all_related_pids():
        subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)
        log(f"  结束残留进程 PID {pid}")
    # 5) 等端口释放
    for _ in range(20):
        busy = [p for p in ALL_PORTS if port_listening(p)]
        if not busy:
            break
        for port in busy:
            owner = pid_on_port(port)
            if owner:
                subprocess.run(["taskkill", "/PID", str(owner), "/F"], capture_output=True)
        time.sleep(0.5)
    try:
        AGENT_PID_FILE.unlink(missing_ok=True)
    except Exception:
        pass
    remaining = [p for p in ALL_PORTS if port_listening(p)]
    if remaining:
        log(f"  [WARN] 仍有端口被占用: {remaining}")
    else:
        log("  端口已全部释放")


# ---------------------------------------------------------------- 状态
def check_consistency() -> None:
    session = http_json(f"http://127.0.0.1:{UAV_PUBLIC_PORT}/sessions/current")
    if not isinstance(session, dict) or "__error__" in session:
        log("会话一致性: UAV API 不可用")
        return
    log(f"会话一致性: {session.get('name')} ({session.get('id')})")


def print_status(proxy_mode: bool) -> bool:
    log("=" * 66)
    log("服务状态" + ("（透明代理模式）" if proxy_mode else "（直连模式）"))
    log("=" * 66)
    rows = [probe_uav(UAV_PUBLIC_PORT, "UAV 对外"), probe_llm(LLM_PUBLIC_PORT, "模型对外")]
    if proxy_mode:
        rows += [probe_uav(UAV_UPSTREAM_PORT, "UAV 实体"), probe_llm(LLM_UPSTREAM_PORT, "模型实体")]
    for state in rows:
        flag = "OK" if state.healthy else ("--" if state.running else "XX")
        pid = f"PID {state.pid}" if state.pid else "无进程"
        log(f"  [{flag}] {state.name:<8} :{state.port:<5} {pid:<12} {state.detail}")

    controller_up = process_running(CONTROLLER_IMAGE)
    agent_up, agent_pid = agent_running()
    log(f"  [{'OK' if controller_up else '--'}] Controller (GUI)  {CONTROLLER_IMAGE}")
    log(f"  [{'OK' if agent_up else '--'}] agent (GUI)  PID {agent_pid if agent_up else '未运行'}")
    log("")
    check_consistency()
    problems = preflight()
    if problems:
        log("")
        log("预检问题：")
        for item in problems:
            log(f"  - {item}")
    return not problems


# ---------------------------------------------------------------- 主流程
def main(argv: list[str] | None = None) -> int:
    enable_utf8_console()
    ap = argparse.ArgumentParser(description="Large-Models-and-Agent-Technology 一键启动")
    ap.add_argument("--direct", action="store_true", help="直连模式（服务占 8000/8010，无代理）")
    ap.add_argument("--no-clean", action="store_true", help="启动前不清理残留进程")
    ap.add_argument("--no-proxy", action="store_true", help="同 --direct")
    ap.add_argument("--with-proxy", action="store_true", help="兼容写法（默认即透明代理）")
    ap.add_argument("--no-agent", action="store_true")
    ap.add_argument("--no-controller", action="store_true")
    ap.add_argument("--force-agent", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--stop", action="store_true")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--restart", action="store_true")
    ap.add_argument("--uav-timeout", type=float, default=120.0)
    ap.add_argument("--llm-timeout", type=float, default=180.0)
    args = ap.parse_args(argv)

    proxy_mode = not (args.direct or args.no_proxy)
    uav_server_port = UAV_UPSTREAM_PORT if proxy_mode else UAV_PUBLIC_PORT
    llm_server_port = LLM_UPSTREAM_PORT if proxy_mode else LLM_PUBLIC_PORT

    log_file(" ".join(sys.argv))
    log("=" * 66)
    log("Large-Models-and-Agent-Technology 启动器")
    log(f"根目录: {ROOT}")
    log(f"模式: {'透明代理（8000->8002, 8010->8012）' if proxy_mode else '直连（8000 / 8010）'}")
    log("=" * 66)

    if args.dry_run:
        log("将执行：")
        log(f'  UAV 后端: cmd /c "chcp 65001>nul && MultiUAV-Plat.Server.v0.49.exe --port {uav_server_port}"')
        log(f"  LM 服务 : .venv\\Scripts\\python.exe -m src.server --port {llm_server_port}")
        if proxy_mode:
            log(f"  UAV 代理: py -3.12 uav_adapter_proxy.py --port {UAV_PUBLIC_PORT} --upstream {uav_server_port}")
            log(f"  LLM 代理: py -3.12 llm_adapter_proxy.py --port {LLM_PUBLIC_PORT} --upstream {llm_server_port}")
        log("  agent   : py -3.12 main.py（配置无需修改）")
        return 0

    if args.status:
        return 0 if print_status(proxy_mode) else 1

    if args.stop or args.restart:
        log("停止服务 ...")
        for port in {UAV_PUBLIC_PORT, LLM_PUBLIC_PORT, uav_server_port, llm_server_port, 8001, 8011}:
            kill_port(port, "服务")
        close_previous_windows()
        if args.all:
            if process_running(CONTROLLER_IMAGE):
                log("  结束 Controller ...")
                subprocess.run(["taskkill", "/IM", CONTROLLER_IMAGE, "/F"], capture_output=True)
            alive, pid = agent_running()
            if alive and pid:
                log(f"  结束 agent（PID {pid}）...")
                subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)
        if args.stop:
            print_status(proxy_mode)
            return 0

    if not args.no_clean:
        clean_all()

    problems = preflight()
    if problems:
        log("预检未通过：")
        for item in problems:
            log(f"  - {item}")
        return 2

    # ① 真实 UAV 后端
    uav = probe_uav(uav_server_port, "UAV 实体")
    if uav.healthy:
        log(f"[OK] UAV 后端已在 {uav_server_port} 运行，跳过")
    else:
        kill_port(uav_server_port, "UAV 残留")
        proc = start_uav(uav_server_port)
        if not wait_ready(lambda: probe_uav(uav_server_port, "UAV 实体"), "UAV 后端",
                          args.uav_timeout, proc):
            return 3

    # ② Controller
    if args.no_controller:
        log("[SKIP] 跳过 Controller")
    elif process_running(CONTROLLER_IMAGE):
        log("[OK] Controller 已在运行")
    else:
        start_controller()

    # ③ 真实模型
    llm = probe_llm(llm_server_port, "模型实体")
    if llm.healthy:
        log(f"[OK] 本地模型已在 {llm_server_port} 运行，跳过")
    else:
        kill_port(llm_server_port, "模型残留")
        proc = start_llm(llm_server_port)
        if not wait_ready(lambda: probe_llm(llm_server_port, "模型实体"), "本地模型",
                          args.llm_timeout, proc):
            return 4

    # ④ 透明代理
    if proxy_mode:
        if not port_listening(UAV_PUBLIC_PORT):
            start_uav_proxy(UAV_PUBLIC_PORT, uav_server_port)
            wait_port(UAV_PUBLIC_PORT, "UAV 代理", 30)
        else:
            log(f"[OK] UAV 代理已在 {UAV_PUBLIC_PORT}")
        if not port_listening(LLM_PUBLIC_PORT):
            start_llm_proxy(LLM_PUBLIC_PORT, llm_server_port)
            wait_port(LLM_PUBLIC_PORT, "LLM 代理", 30)
        else:
            log(f"[OK] LLM 代理已在 {LLM_PUBLIC_PORT}")
        wait_ready(lambda: probe_uav(UAV_PUBLIC_PORT, "UAV 对外"), "UAV 代理链", 30)
        wait_ready(lambda: probe_llm(LLM_PUBLIC_PORT, "模型对外"), "模型代理链", 30)

    print_status(proxy_mode)

    if args.no_agent:
        log("")
        log("服务已就绪（未启动 agent）。")
        return 0

    alive, old_pid = agent_running()
    if alive and not args.force_agent:
        log(f"[SKIP] agent 已在运行（PID {old_pid}）；加 --force-agent 可再开一个")
    else:
        start_agent()

    log("")
    log("全部启动完成：")
    if proxy_mode:
        log(f"  UAV : 对外 {UAV_PUBLIC_PORT}（代理）-> 实体 {uav_server_port}")
        log(f"  LLM : 对外 {LLM_PUBLIC_PORT}（代理）-> 实体 {llm_server_port}")
        log("  agent / Controller 配置无需修改（保持 8000 / 8010）")
        log("  代理窗口会打印 [proxy] / [llm-proxy] tool-rewrite: ...")
    log("  agent 日志应显示：已注入导航工具 11 个 / Created 33 tools")
    return 0


if __name__ == "__main__":
    sys.exit(main())
