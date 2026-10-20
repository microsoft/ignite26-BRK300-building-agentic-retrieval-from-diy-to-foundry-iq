"""Run every DIY retrieval agent locally, each on its own port, for the portal's DIY agents page.

Usage: python -m scripts.run_diy_agents [step-name ...]
"""

import os
import re
import signal
import subprocess
import sys
import threading
from pathlib import Path

from app.backend.diy_agents import DIY_STEPS, DIY_STEPS_BY_NAME, DiyStep

AGENTS_DIR = Path(__file__).parents[1] / "python"
# The agent server logs every telemetry span to the console; only show its activity log, startup, and errors.
RELEVANT_LINE = re.compile(r"^(INFO:diy-|ERROR|WARNING:agent_framework|Traceback|\S*Error: |INFO:hypercorn\.error:Running on)")


def ensure_venv(step_dir: Path) -> Path:
    venv = step_dir / ".venv"
    if not venv.exists():
        subprocess.run(["uv", "venv"], cwd=step_dir, check=True)
        subprocess.run(["uv", "pip", "install", "uv"], cwd=step_dir, check=True)
    return venv


def start(step: DiyStep) -> subprocess.Popen[str]:
    step_dir = AGENTS_DIR / step.name
    venv = ensure_venv(step_dir)
    bin_dir = venv / ("Scripts" if os.name == "nt" else "bin")
    env = {**os.environ, "VIRTUAL_ENV": str(venv), "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}
    return subprocess.Popen(
        ["azd", "ai", "agent", "run", step.name, "--no-client", "--port", str(step.port)],
        cwd=step_dir,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )


def relay(step: DiyStep, process: subprocess.Popen[str]) -> None:
    assert process.stdout is not None
    for line in process.stdout:
        if RELEVANT_LINE.search(line):
            print(f"[{step.name}] {line.rstrip()}", flush=True)
    print(f"[{step.name}] exited with code {process.wait()}", flush=True)


def stop(process: subprocess.Popen[str], force: bool = False) -> None:
    if os.name != "nt":
        os.killpg(process.pid, signal.SIGKILL if force else signal.SIGTERM)
    elif force:
        process.kill()
    else:
        process.terminate()


def main() -> None:
    names = sys.argv[1:]
    unknown = [name for name in names if name not in DIY_STEPS_BY_NAME]
    if unknown:
        sys.exit(f"Unknown step(s): {', '.join(unknown)}. Choose from: {', '.join(DIY_STEPS_BY_NAME)}")
    steps = [DIY_STEPS_BY_NAME[name] for name in names] if names else list(DIY_STEPS)

    processes = []
    for step in steps:
        print(f"Starting {step.name} on port {step.port}...", flush=True)
        process = start(step)
        processes.append(process)
        threading.Thread(target=relay, args=(step, process), daemon=True).start()
    print("Open the portal's DIY agents page. Press Ctrl+C to stop all agents.", flush=True)

    try:
        for process in processes:
            process.wait()
    except KeyboardInterrupt:
        pass
    finally:
        for process in processes:
            if process.poll() is None:
                stop(process)
        for process in processes:
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                stop(process, force=True)


if __name__ == "__main__":
    main()
