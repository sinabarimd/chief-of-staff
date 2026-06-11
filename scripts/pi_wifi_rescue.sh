#!/bin/bash
# Pi wifi rescue — run from init=/bin/bash root shell
# Usage: bash /boot/firmware/wifi_rescue.sh
# (this script lives on the FAT32 boot partition so it's accessible without systemd)

mount -o remount,rw /
mount /dev/mmcblk0p1 /boot/firmware
modprobe brcmfmac
rfkill unblock wifi
ip link set wlan0 up
wpa_supplicant -B -i wlan0 -c /etc/wpa_supplicant/wpa_supplicant.conf
sleep 3
dhcpcd wlan0
mkdir -p /run/sshd
/usr/sbin/sshd
echo Wifi and SSH up - check: ip addr show wlan0
