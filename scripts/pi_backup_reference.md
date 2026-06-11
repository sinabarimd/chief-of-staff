# Pi Backup Reference (2026-05-07)

Reference configs from the old install. Use for guidance, not copy-paste.

## System Info
- **OS:** Debian (Raspberry Pi OS), kernel 6.12.75+rpt-rpi-v8, aarch64
- **Python:** 3.13.5
- **Hostname:** voicehub-pi
- **User:** sinabot
- **Wifi MAC:** 88:a2:9e:c5:5c:0c
- **Ethernet MAC:** 88:a2:9e:c5:5c:0b
- **Static IP:** 192.168.1.10 (DHCP reservation on eero, not on Pi)

## SSH Authorized Keys
```
ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIKsJTLJfVE1jc2+8q7ZE1nX4OwK8Pn5RuWPDhGqgFH0L sina@imerit.net
ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIPhxFcwjppJsq6W/L+tSEasV6N6s3+MamURFEkx5/vSC user@voicehub-host
```

## wyoming-satellite service
```ini
[Unit]
Description=Wyoming Satellite (voicehub-pi)
Wants=network-online.target wyoming-openwakeword.service
After=network-online.target wyoming-openwakeword.service

[Service]
Type=simple
User=sinabot
ExecStart=/opt/wyoming-satellite/venv/bin/python3 -m wyoming_satellite \
  --name "voicehub-pi" \
  --uri "tcp://0.0.0.0:10700" \
  --mic-command "arecord -D plughw:Array,0 -r 16000 -c 1 -f S16_LE -t raw" \
  --snd-command "aplay -D plughw:Headphones,0 -r 22050 -c 1 -f S16_LE -t raw" \
  --snd-command-rate 22050 \
  --snd-command-channels 1 \
  --snd-command-width 2 \
  --wake-uri "tcp://127.0.0.1:10400" \
  --wake-word-name "ok_nabu" \
  --mic-volume-multiplier 10.0 \
  --vad \
  --no-zeroconf --debug
WorkingDirectory=/opt/wyoming-satellite
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

Note: removed bluetooth.target and bluealsa.service deps (no longer using BT speaker)

## wyoming-openwakeword service
```ini
[Unit]
Description=Wyoming openWakeWord (ok_nabu)
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
User=sinabot
ExecStart=/opt/wyoming-openwakeword/venv/bin/python3 -m wyoming_openwakeword \
  --uri "tcp://127.0.0.1:10400" \
  --preload-model "ok_nabu" --debug --debug-probability
WorkingDirectory=/opt/wyoming-openwakeword
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

## wpa_supplicant.conf
```
ctrl_interface=DIR=/var/run/wpa_supplicant GROUP=netdev
update_config=1
country=US
network={
	ssid="Baby Monkey"
	psk=4e95f25cd96fd07600d6af4bf390430fc3a2acdf0ab7a8680185637d98207da9
}
```

## ALSA
- Mic: `plughw:Array,0` (ReSpeaker XVF3800)
- Speaker: `plughw:Headphones,0` (3.5mm jack)
- PCM volume: 100% (`amixer -c 0 set PCM 100% && sudo alsactl store`)
- No /etc/asound.conf

## Boot config (cmdline.txt)
```
console=serial0,115200 console=tty1 root=PARTUUID=... rootfstype=ext4 fsck.repair=yes rootwait cfg80211.ieee80211_regdom=US usbcore.autosuspend=-1
```

## Boot config (config.txt)
Key additions beyond stock:
```
enable_uart=1
usb_max_current_enable=1
```

## Software installed
- wyoming-satellite: git clone in ~/wyoming-satellite, venv
- wyoming-openwakeword: git clone in ~/wyoming-openwakeword, venv
- bluealsa (installed but no longer needed)
- dhcpcd

## Lessons from this install
- Do NOT create a respeaker-watchdog service — it boot-loops when ReSpeaker isn't ready
- Add `usbcore.autosuspend=-1` to cmdline.txt to prevent USB device dropouts
- Wifi config must be in /etc/wpa_supplicant/wpa_supplicant.conf with update_config=1
- Console font: TerminusBold 16x32 in /etc/default/console-setup
- cmdline.txt real path is /boot/firmware/cmdline.txt, NOT /boot/cmdline.txt
