"""
Daily runner — invoked by launchd.
Runs the LinkedIn job collector, then sends an email notification.
"""

import os
import subprocess
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def main():
    print(f"=== Daily run started at {__import__('datetime').datetime.now()} ===")

    # Run collector
    result = subprocess.run(
        [sys.executable, os.path.join(BASE_DIR, "collect_jobs.py")],
        cwd=BASE_DIR,
    )

    if result.returncode != 0:
        print(f"collect_jobs.py exited with code {result.returncode}")

    # Send email notification regardless of exit code
    # (partial results are still worth sending)
    try:
        from notifier import send_notification
        send_notification()
    except Exception as e:
        print(f"Notification failed: {e}")

    print(f"=== Daily run finished ===")


if __name__ == "__main__":
    main()
