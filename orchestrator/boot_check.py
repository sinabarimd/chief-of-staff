#!/usr/bin/env python3
"""Voice Hub full-stack boot validation.

Checks every component in order, reports status, and optionally
starts the orchestrator if everything passes.

Run from the 4070:
    python3 -m orchestrator.boot_check
    python3 -m orchestrator.boot_check --start  # also start orchestrator
    python3 -m orchestrator.boot_check --fix     # attempt auto-fixes
"""

from __future__ import annotations

import argparse
import asyncio
import json
import socket
import subprocess
import sys
import time

# --- Configuration ---
PI_HOST = "192.168.1.10"
PI_USER = "sinabot"
VLLM_URL = "http://localhost:8000"
WHISPERLIVE_HOST = "localhost"
WHISPERLIVE_PORT = 9090
KOKORO_HOST = "localhost"
KOKORO_PORT = 10200
SATELLITE_PORT = 10700

# ANSI colors
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
RESET = "\033[0m"
BOLD = "\033[1m"


def ok(msg: str) -> None:
    print(f"  {GREEN}✓{RESET} {msg}")


def fail(msg: str) -> None:
    print(f"  {RED}✗{RESET} {msg}")


def warn(msg: str) -> None:
    print(f"  {YELLOW}!{RESET} {msg}")


def ssh(cmd: str, timeout: int = 10) -> tuple[int, str]:
    """Run command on Pi via SSH. Returns (returncode, stdout+stderr)."""
    try:
        r = subprocess.run(
            ["ssh", "-o", "ConnectTimeout=5", f"{PI_USER}@{PI_HOST}", cmd],
            capture_output=True, text=True, timeout=timeout,
        )
        return r.returncode, r.stdout + r.stderr
    except subprocess.TimeoutExpired:
        return -1, "SSH timeout"
    except Exception as e:
        return -1, str(e)


def tcp_check(host: str, port: int, timeout: float = 3.0) -> bool:
    """Check if a TCP port is open."""
    try:
        s = socket.create_connection((host, port), timeout=timeout)
        s.close()
        return True
    except (socket.timeout, ConnectionRefusedError, OSError):
        return False


def http_get(url: str, timeout: float = 5.0) -> tuple[int, str]:
    """Simple HTTP GET without dependencies."""
    import urllib.request
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status, resp.read().decode()
    except Exception as e:
        return 0, str(e)


def main() -> None:
    parser = argparse.ArgumentParser(description="Voice Hub boot validation")
    parser.add_argument("--start", action="store_true", help="Start orchestrator if all checks pass")
    parser.add_argument("--fix", action="store_true", help="Attempt auto-fixes for common issues")
    parser.add_argument("--wait", action="store_true", help="Retry until all checks pass (for systemd)")
    parser.add_argument("--wait-interval", type=int, default=30, help="Seconds between retries (default 30)")
    parser.add_argument("--wait-max", type=int, default=300, help="Max seconds to wait (default 300)")
    args = parser.parse_args()

    if args.wait:
        deadline = time.time() + args.wait_max
        attempt = 0
        while time.time() < deadline:
            attempt += 1
            print(f"\n{'='*50}")
            print(f"Boot check attempt {attempt}")
            print(f"{'='*50}")
            if _run_checks(args):
                break
            remaining = int(deadline - time.time())
            if remaining <= 0:
                print(f"\n{RED}{BOLD}Timed out after {args.wait_max}s. Giving up.{RESET}")
                sys.exit(1)
            wait = min(args.wait_interval, remaining)
            print(f"\n{YELLOW}Retrying in {wait}s ({remaining}s remaining)...{RESET}")
            time.sleep(wait)
    else:
        if not _run_checks(args):
            sys.exit(1)

    if args.start:
        print(f"\nStarting orchestrator...")
        import os
        os.execvp(
            sys.executable,
            [sys.executable, "-m", "orchestrator"],
        )


def _run_checks(args) -> bool:
    """Run all checks. Returns True if all pass."""
    all_ok = True
    warnings = []

    # =========================================================
    print(f"\n{BOLD}=== Pi Satellite (192.168.1.10) ==={RESET}\n")
    # =========================================================

    # 1. Pi SSH connectivity
    rc, out = ssh("echo ok")
    if rc == 0 and "ok" in out:
        ok("SSH to Pi")
        goto_4070 = False
    else:
        fail(f"SSH to Pi: {out.strip()}")
        all_ok = False
        print(f"\n{RED}Cannot reach Pi — skipping Pi checks.{RESET}")
        goto_4070 = True

    if not goto_4070:
        # 2. ReSpeaker USB detected
        rc, out = ssh("arecord -l 2>&1")
        if "Array" in out or "reSpeaker" in out:
            ok("ReSpeaker USB detected")
        else:
            fail("ReSpeaker USB not detected")
            all_ok = False

        # 3. ReSpeaker audio streaming (not just enumerated)
        rc, out = ssh(
            "sudo systemctl stop wyoming-satellite 2>/dev/null; "
            "timeout 3 arecord -D plughw:Array,0 -r 16000 -c 1 -f S16_LE -d 1 /tmp/boot_mic_test.wav 2>&1; "
            "stat -c %s /tmp/boot_mic_test.wav 2>&1"
        )
        if rc == 0 or "boot_mic_test.wav" in out:
            # Last line of output should be the file size in bytes
            try:
                size = int(out.strip().split("\n")[-1])
                if size > 1000:
                    ok(f"ReSpeaker audio streaming ({size} bytes)")
                else:
                    fail(f"ReSpeaker not streaming audio ({size} bytes — needs USB replug)")
                    all_ok = False
                    if args.fix:
                        warn("Auto-fix: attempting USB reset...")
                        ssh("sudo usbreset '2886:001a' 2>&1")
                        time.sleep(2)
                        rc2, out2 = ssh(
                            "timeout 3 arecord -D plughw:Array,0 -r 16000 -c 1 -f S16_LE -d 1 /tmp/boot_mic_test2.wav 2>&1; "
                            "stat -c %s /tmp/boot_mic_test2.wav 2>&1"
                        )
                        if rc2 == 0 and out2.strip().split("\n")[-1].isdigit() and int(out2.strip().split("\n")[-1]) > 1000:
                            ok("USB reset fixed the mic")
                            all_ok = True  # recovered
                        else:
                            fail("USB reset didn't help — physical replug needed")
            except (IndexError, ValueError):
                fail(f"Could not parse mic test output")
                all_ok = False
        else:
            fail("ReSpeaker mic test failed")
            all_ok = False

        # 4. ALSA volume
        rc, out = ssh("amixer -c 0 get PCM 2>&1")
        if "100%" in out:
            ok("ALSA PCM volume at 100%")
        else:
            warn("ALSA PCM volume not at 100%")
            if args.fix:
                ssh("amixer -c 0 set PCM 100% && sudo alsactl store")
                ok("Fixed: ALSA volume set to 100%")
            else:
                warnings.append("Run: amixer -c 0 set PCM 100% && sudo alsactl store")

        # 5. Speaker test
        rc, out = ssh("aplay -D plughw:Headphones,0 /usr/share/sounds/alsa/Front_Center.wav 2>&1")
        if rc == 0:
            ok("Speaker output (headphone jack)")
        else:
            fail(f"Speaker test failed: {out.strip()}")
            all_ok = False

        # 6. openWakeWord service
        rc, out = ssh("systemctl is-active wyoming-openwakeword 2>&1")
        if "active" in out:
            ok("openWakeWord service running")
        else:
            fail("openWakeWord service not running")
            all_ok = False
            if args.fix:
                ssh("sudo systemctl restart wyoming-openwakeword")
                time.sleep(2)
                rc2, out2 = ssh("systemctl is-active wyoming-openwakeword 2>&1")
                if "active" in out2:
                    ok("Fixed: openWakeWord restarted")
                else:
                    fail("Could not restart openWakeWord")

        # 7. wyoming-satellite service
        # Restart it since we stopped it for mic test
        ssh("sudo systemctl start wyoming-satellite")
        time.sleep(2)
        rc, out = ssh("systemctl is-active wyoming-satellite 2>&1")
        if "active" in out:
            ok("wyoming-satellite service running")
        else:
            fail("wyoming-satellite service not running")
            all_ok = False

        # 8. Satellite TCP port
        if tcp_check(PI_HOST, SATELLITE_PORT):
            ok(f"Satellite port {SATELLITE_PORT} accepting connections")
        else:
            fail(f"Satellite port {SATELLITE_PORT} not reachable")
            all_ok = False

    # =========================================================
    print(f"\n{BOLD}=== 4070 GPU Services ==={RESET}\n")
    # =========================================================
    gpu_ok = True  # Track GPU service health separately

    # 9. Docker running
    r = subprocess.run(["docker", "info"], capture_output=True, timeout=5)
    if r.returncode == 0:
        ok("Docker daemon running")
    else:
        fail("Docker not running")
        all_ok = False
        gpu_ok = False

    # 10. vLLM
    status, body = http_get(f"{VLLM_URL}/v1/models")
    if status == 200:
        try:
            models = json.loads(body)
            model_id = models["data"][0]["id"]
            ok(f"vLLM serving: {model_id}")
        except (json.JSONDecodeError, KeyError, IndexError):
            ok("vLLM responding (could not parse model)")
    else:
        fail(f"vLLM not responding: {body[:100]}")
        all_ok = False
        gpu_ok = False

    # 11. WhisperLive
    if tcp_check(WHISPERLIVE_HOST, WHISPERLIVE_PORT):
        ok(f"WhisperLive port {WHISPERLIVE_PORT} open")
    else:
        fail(f"WhisperLive port {WHISPERLIVE_PORT} not reachable")
        all_ok = False
        gpu_ok = False

    # 12. Kokoro TTS
    if tcp_check(KOKORO_HOST, KOKORO_PORT):
        ok(f"Kokoro TTS port {KOKORO_PORT} open")
    else:
        fail(f"Kokoro TTS port {KOKORO_PORT} not reachable")
        all_ok = False
        gpu_ok = False

    # 13. VRAM check
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0:
            parts = r.stdout.strip().split(", ")
            used = parts[0].strip()
            total = parts[1].strip()
            ok(f"GPU VRAM: {used} / {total}")
        else:
            warn("Could not query GPU")
    except Exception:
        warn("nvidia-smi not available")

    # =========================================================
    print(f"\n{BOLD}=== Connectivity ==={RESET}\n")
    # =========================================================

    # 14. 4070 can reach Pi satellite
    if not goto_4070:
        if tcp_check(PI_HOST, SATELLITE_PORT):
            ok(f"4070 → Pi satellite ({PI_HOST}:{SATELLITE_PORT})")
        else:
            fail(f"4070 cannot reach Pi satellite")
            all_ok = False

    # 15. Quick LLM test
    try:
        status, body = http_get(f"{VLLM_URL}/v1/models")
        if status == 200:
            model_id = json.loads(body)["data"][0]["id"]
            import urllib.request
            req = urllib.request.Request(
                f"{VLLM_URL}/v1/chat/completions",
                data=json.dumps({
                    "model": model_id,
                    "messages": [{"role": "user", "content": "Say ok"}],
                    "max_tokens": 5,
                }).encode(),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                r = json.loads(resp.read())
                text = r["choices"][0]["message"]["content"]
                ok(f"LLM inference working: '{text}'")
    except Exception as e:
        fail(f"LLM inference test failed: {e}")
        all_ok = False

    # =========================================================
    # Summary
    # =========================================================
    print()
    if warnings:
        for w in warnings:
            warn(f"Suggestion: {w}")
        print()

    if all_ok:
        print(f"{GREEN}{BOLD}All checks passed.{RESET}")
        return True
    elif gpu_ok:
        # GPU services are up — start orchestrator even if Pi satellite has issues.
        # ESPHome satellites don't need boot checks (they auto-reconnect).
        print(f"{YELLOW}{BOLD}GPU services OK. Pi satellite has issues but orchestrator can start.{RESET}")
        if args.fix:
            print(f"{YELLOW}Auto-fix was attempted — re-run to verify.{RESET}")
        return True
    else:
        print(f"{RED}{BOLD}GPU services failed.{RESET} Fix issues above before starting the orchestrator.")
        if args.fix:
            print(f"{YELLOW}Auto-fix was attempted — re-run to verify.{RESET}")
        return False


if __name__ == "__main__":
    main()
