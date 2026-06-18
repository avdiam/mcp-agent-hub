import subprocess
import sys
import time
from pathlib import Path

def main():
    logs_dir = Path(__file__).parent / "logs"
    logs_dir.mkdir(exist_ok=True)
    log_path = logs_dir / "hub.log"
    print("Starting MCP Agent Hub Supervisor...")
    while True:
        print(f"Launching uvicorn mcp_hub.hub:app (logging to {log_path})...")
        with open(log_path, "a", encoding="utf-8") as log:
            log.write(f"\n===== supervisor launch {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n")
            log.flush()
            process = subprocess.Popen(
                [sys.executable, "-m", "uvicorn", "mcp_hub.hub:app", "--host", "127.0.0.1", "--port", "8000"],
                stdout=log,
                stderr=subprocess.STDOUT,
            )
            process.wait()
        if process.returncode == 42:
            print("Restart requested (exit code 42). Relaunching in 1 second...")
            time.sleep(1)
        else:
            print(f"Hub exited with code {process.returncode}. Supervisor exiting.")
            sys.exit(process.returncode)

if __name__ == "__main__":
    main()
