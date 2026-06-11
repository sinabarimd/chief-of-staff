# Hardware Setup

## Inventory (as of 2026-04-18)

### In hand
- **i9 + RTX 4070 (12GB)** — desktop, currently Windows-only simracing rig. Target: dual-boot Ubuntu Server.
- **Raspberry Pi 4 (2GB)** — kit unopened. Role: main-room audio bridge only (no inference).
- **HA Voice PE** × 1 — ESP32-based voice satellite.
- **Sony SRS-XB100** — Bluetooth speaker for main room (connected via AUX from the Pi).

### Ordered, awaiting delivery
- **HA Voice PE** × 1 (2nd satellite)
- **ReSpeaker XVF3800** — far-field mic array for main room, connects to Pi over USB

### Not yet ordered (needed for full deployment)
- **HA Voice PE** × 1 (3rd satellite, for office zone)
- Optional: wired ethernet for 4070 box (currently WiFi)

## Zone assignments

| Zone | Device | Notes |
|---|---|---|
| Main room (30×30 open plan) | ReSpeaker XVF3800 → Pi 4 → AUX → SRS-XB100 | Far-field mic needed due to room size; HA Voice PE's built-in mic is too weak for this zone |
| Bedroom | HA Voice PE | Satellite #1 (in hand) |
| Family room | HA Voice PE | Satellite #2 (pending delivery) |
| Office | HA Voice PE | Satellite #3 (not yet ordered) |

## Main-room audio topology (detail)

```
Voice (user) ──▶ ReSpeaker XVF3800 (USB) ──▶ Pi 4
                                               │
                                               │  (Wyoming satellite firmware
                                               │   streams audio to HA on
                                               │   the 4070 box over WiFi)
                                               │
                                               ▼
                                       HA Container on 4070
                                               │
                                               │  (pipeline: STT → LLM → TTS)
                                               │
                                               ▼
                                       Audio response → Pi
                                               │
                                               │  AUX 3.5mm out
                                               ▼
                                       Sony SRS-XB100 (speaker)
```

**Pi 4 role: audio bridge only.** No inference of any kind runs on the Pi. The 2GB model is sufficient because it only handles USB audio capture, Wyoming protocol streaming, AUX playback, and optional wake-word on the host (wake word can run on HA Voice PEs natively; for the ReSpeaker-on-Pi setup, wake word runs in HA or on the 4070, not on the Pi).

## Networking

- All nodes over WiFi initially. Move the 4070 box to wired ethernet before first real-world use.
- mDNS must propagate — HA Voice PE satellites discover the HA instance via `_home-assistant._tcp.local`. No cross-VLAN routing between the voice network and the management network without a reflector.
- Packet loss targets: <0.1% on audio streams; bufferbloat bounded to keep RTT jitter under 30ms.

## Power

- 4070 box: on always-on UPS; measure idle draw (vLLM fp8 8B loaded ≈ 40–80W GPU idle + ~40W CPU)
- Pi 4: standard 5V/3A supply
- HA Voice PEs: USB-C, can be powered from any USB-C PD source

## Known constraints

- Sony SRS-XB100 is a Bluetooth speaker but used via AUX here — avoids BT pairing latency and drift
- 30×30 main room is acoustically tough; ReSpeaker's beamforming matters more than the model matters
- WiFi reliability will be the first failure mode noticed in practice
