import subprocess
import sys
import time

def main():
    print("Starting MCP Agent Hub Supervisor...")
    while True:
        print("Launching uvicorn mcp_hub.hub:app...")
        process = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "mcp_hub.hub:app", "--host", "127.0.0.1", "--port", "8000"]
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
