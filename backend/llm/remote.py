"""On-demand vLLM servers on shared GPU boxes, reached through an SSH tunnel.

Lifecycle: pick the GPU with the most free memory -> start `vllm serve` detached
(own process group, pidfile) -> start a remote idle watchdog -> open `ssh -L` tunnel
-> poll /health. The watchdog kills the server after `idle_shutdown_min` without
requests, so a crashed laptop never leaves a GPU occupied.
"""

from __future__ import annotations

import atexit
import shlex
import socket
import subprocess
import time

import httpx

# Runs on the remote host: `watchdog.sh PORT IDLE_MIN PIDFILE`.
WATCHDOG_SH = r"""
port=$1; idle_min=$2; pidfile=$3
last=""; idle=0; healthy=0
while kill -0 "$(cat "$pidfile")" 2>/dev/null; do
  sleep 60
  if ! curl -sf "http://127.0.0.1:$port/health" >/dev/null; then
    [ "$healthy" = 1 ] && idle=$((idle+1))
  else
    healthy=1
    m=$(curl -sf "http://127.0.0.1:$port/metrics")
    done_n=$(echo "$m" | awk '/^vllm:request_success_total/ {s+=$2} END {print s+0}')
    running=$(echo "$m" | awk '/^vllm:num_requests_(running|waiting)/ {s+=$2} END {print s+0}')
    if [ "$done_n" != "$last" ] || [ "${running%.*}" != "0" ]; then idle=0; else idle=$((idle+1)); fi
    last=$done_n
  fi
  if [ "$idle" -ge "$idle_min" ]; then
    echo "$(date) idle ${idle_min}m, stopping"
    kill -- -"$(cat "$pidfile")" 2>/dev/null; rm -f "$pidfile"; exit 0
  fi
done
"""


# Runs on a relay host with clean internet: `fetch.sh MODEL`. Idempotent (pip/hf resume).
FETCH_SH = r"""
set -e
cd ~/s2m-relay
[ -d .venv ] || python3.12 -m venv .venv
.venv/bin/pip install -q --upgrade pip huggingface_hub
.venv/bin/pip download -q vllm -d wheels
HF_HOME=$HOME/s2m-relay/hf .venv/bin/hf download "$1" > /dev/null
"""


class RemoteError(RuntimeError):
    pass


def open_tunnel(host: str, local_port: int, remote_port: int, wait_s: float = 15) -> subprocess.Popen | None:
    """`ssh -L local:127.0.0.1:remote host`, closed at exit. None if the port is already forwarded."""
    def port_open() -> bool:
        with socket.socket() as s:
            return s.connect_ex(("127.0.0.1", local_port)) == 0

    if port_open():
        return None  # an earlier tunnel (or the service itself) already listens here
    proc = subprocess.Popen(
        ["ssh", "-N", "-o", "BatchMode=yes", "-o", "ExitOnForwardFailure=yes",
         "-L", f"{local_port}:127.0.0.1:{remote_port}", host],
    )
    atexit.register(lambda: proc.poll() is None and proc.terminate())
    t0 = time.time()
    while time.time() - t0 < wait_s and proc.poll() is None and not port_open():
        time.sleep(0.2)
    if proc.poll() is not None:
        raise RemoteError(f"[{host}] ssh tunnel {local_port}->{remote_port} failed")
    return proc


class VllmRemote:
    def __init__(self, name: str, cfg: dict):
        self.name = name
        self.host = cfg["host"]
        self.model = cfg["model"]
        self.gpu_mem_gb = cfg.get("gpu_mem_gb", 70)
        self.remote_port = cfg.get("remote_port", 8765)
        self.local_port = cfg.get("local_port", self.remote_port)
        self.remote_dir = cfg.get("remote_dir", "~/s2m-vllm")
        self.idle_min = cfg.get("idle_shutdown_min", 15)
        self.vllm_args = cfg.get("vllm_args", [])
        # Set when the host can't reach PyPI/HF itself (e.g. TLS-intercepting firewall):
        # downloads happen on relay_host and are streamed over through this machine.
        self.relay_host = cfg.get("relay_host")
        self.env = dict(cfg.get("env", {}))
        if self.relay_host:
            self.env.update({"HF_HOME": f"{self.remote_dir}/hf", "HF_HUB_OFFLINE": "1"})
        self.env.setdefault("VLLM_NO_USAGE_STATS", "1")
        self.pidfile = f"{self.remote_dir}/server-{self.remote_port}.pid"
        self.logfile = f"{self.remote_dir}/server-{self.remote_port}.log"
        self._tunnel: subprocess.Popen | None = None

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.local_port}/v1"

    # ---------- ssh helpers ----------
    def _ssh(self, script: str, timeout: int = 60, check: bool = True) -> str:
        r = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", self.host, "bash -s"],
            input=script, capture_output=True, text=True, timeout=timeout,
        )
        if check and r.returncode != 0:
            raise RemoteError(f"[{self.host}] {r.stderr.strip() or r.stdout.strip()}")
        return r.stdout

    def _venv_bin(self) -> str:
        return f"{self.remote_dir}/.venv/bin"

    # ---------- setup ----------
    def setup(self) -> None:
        """Create the remote venv and install vLLM (one-off, a few minutes)."""
        if self.relay_host:
            return self._setup_via_relay()
        print(f"[{self.host}] installing vLLM into {self.remote_dir}/.venv ...")
        r = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", self.host, "bash -s"],
            input=f"""set -e
mkdir -p {self.remote_dir}
cd {self.remote_dir}
[ -d .venv ] || python3.12 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q --upgrade vllm
.venv/bin/python -c "import vllm, torch; print('vllm', vllm.__version__, 'torch', torch.__version__, 'cuda', torch.version.cuda)"
""",
            text=True,
        )
        if r.returncode != 0:
            raise RemoteError(f"[{self.host}] vLLM install failed")

    def _setup_via_relay(self) -> None:
        relay = self.relay_host
        model_dir = "hf/hub/models--" + self.model.replace("/", "--")
        print(f"[{relay}] fetching vLLM wheels + {self.model} (resumes if partial) ...")
        r = subprocess.run(["ssh", "-o", "BatchMode=yes", relay, f"mkdir -p ~/s2m-relay && bash -s {shlex.quote(self.model)}"],
                           input=FETCH_SH, text=True)
        if r.returncode != 0:
            raise RemoteError(f"[{relay}] fetch failed")

        print(f"[{relay} -> {self.host}] streaming wheels + weights via this machine ...")
        src = subprocess.Popen(["ssh", "-o", "BatchMode=yes", relay, f"tar -C ~/s2m-relay -cf - wheels {model_dir}"],
                               stdout=subprocess.PIPE)
        dst = subprocess.run(["ssh", "-o", "BatchMode=yes", self.host,
                              f"mkdir -p {self.remote_dir} && tar -C {self.remote_dir} -xf -"],
                             stdin=src.stdout)
        src.stdout.close()
        if src.wait() != 0 or dst.returncode != 0:
            raise RemoteError("transfer failed")

        print(f"[{self.host}] installing vLLM offline ...")
        self._ssh(f"""set -e
cd {self.remote_dir}
[ -d .venv ] || python3.12 -m venv .venv
.venv/bin/pip install -q --no-index --find-links wheels vllm
.venv/bin/python -c "import vllm, torch; print('vllm', vllm.__version__, 'torch', torch.__version__, 'cuda', torch.version.cuda)"
""", timeout=1800)

    # ---------- status ----------
    def remote_pid(self) -> int | None:
        out = self._ssh(
            f'p={self.pidfile}; [ -f $p ] && kill -0 "$(cat $p)" 2>/dev/null && cat $p || true'
        ).strip()
        return int(out) if out else None

    def healthy(self) -> bool:
        try:
            return httpx.get(f"http://127.0.0.1:{self.local_port}/health", timeout=3).status_code == 200
        except httpx.HTTPError:
            return False

    def status(self) -> str:
        pid = self.remote_pid()
        if pid is None:
            return f"{self.name}: stopped"
        return f"{self.name}: running (pid {pid}) on {self.host}:{self.remote_port}, tunnel {'up' if self.healthy() else 'down'}"

    # ---------- start / stop ----------
    def _pick_gpu(self) -> tuple[int, float]:
        out = self._ssh("nvidia-smi --query-gpu=index,memory.total,memory.free --format=csv,noheader,nounits")
        gpus = []
        for line in out.strip().splitlines():
            idx, total, free = (int(x.strip()) for x in line.split(","))
            gpus.append((free, idx, total))
        gpus.sort(reverse=True)
        free, idx, total = gpus[0]
        need = self.gpu_mem_gb * 1024
        if free < need:
            raise RemoteError(
                f"[{self.host}] no GPU with {self.gpu_mem_gb} GB free "
                f"(best: GPU {idx} with {free // 1024} GB). Shared box is busy; try another backend."
            )
        return idx, round(min(need / total, 0.95), 3)

    def _start_server(self) -> None:
        gpu, util = self._pick_gpu()
        args = " ".join(shlex.quote(a) for a in self.vllm_args)
        env = " ".join(f"{k}={v}" for k, v in self.env.items())
        print(f"[{self.host}] starting {self.model} on GPU {gpu} (util {util}) ...")
        self._ssh(f"""set -e
cd {self.remote_dir}
[ -x {self._venv_bin()}/vllm ] || {{ echo "vLLM not installed; run: s2m server setup --backend {self.name}" >&2; exit 3; }}
cat > watchdog.sh <<'EOF'
{WATCHDOG_SH}
EOF
{env} CUDA_VISIBLE_DEVICES={gpu} nohup setsid {self._venv_bin()}/vllm serve {shlex.quote(self.model)} \\
  --host 127.0.0.1 --port {self.remote_port} --served-model-name s2m \\
  --gpu-memory-utilization {util} {args} > {self.logfile} 2>&1 < /dev/null &
echo $! > {self.pidfile}
nohup setsid bash watchdog.sh {self.remote_port} {self.idle_min} {self.pidfile} \\
  > watchdog-{self.remote_port}.log 2>&1 < /dev/null &
""")

    def _open_tunnel(self) -> None:
        if self._tunnel and self._tunnel.poll() is None:
            return
        self._tunnel = open_tunnel(self.host, self.local_port, self.remote_port)

    def close_tunnel(self) -> None:
        if self._tunnel and self._tunnel.poll() is None:
            self._tunnel.terminate()
        self._tunnel = None

    def ensure_up(self, timeout_s: int = 1800) -> str:
        """Start server + tunnel if needed and wait until healthy. Returns base_url."""
        if self.healthy():
            return self.base_url
        if self.remote_pid() is None:
            self._start_server()
        self._open_tunnel()
        print(f"[{self.host}] waiting for server (first run downloads weights; log: {self.logfile}) ...")
        t0 = time.time()
        while time.time() - t0 < timeout_s:
            if self.healthy():
                print(f"[{self.host}] ready after {time.time() - t0:.0f}s")
                return self.base_url
            if self.remote_pid() is None:
                tail = self._ssh(f"tail -n 30 {self.logfile}", check=False)
                raise RemoteError(f"[{self.host}] vLLM exited during startup:\n{tail}")
            if self._tunnel and self._tunnel.poll() is not None:
                self._tunnel = None
                self._open_tunnel()
            time.sleep(10)
        raise RemoteError(f"[{self.host}] server not healthy after {timeout_s}s")

    def stop(self) -> None:
        self._ssh(
            f'p={self.pidfile}; [ -f $p ] && kill -- -"$(cat $p)" 2>/dev/null; rm -f $p; true'
        )
        self.close_tunnel()
        print(f"[{self.host}] {self.name} stopped")
