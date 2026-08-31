#!/usr/bin/env bash
# Dump irreplaceable + diagnostic partitions. Read-only.
set -u
E="/home/realni/a525f/edl-ng/edl-ng"
L="/home/realni/a525f/loaders/a525f_bitc.elf"
D="/home/realni/a525f/backup"
mkdir -p "$D"
# name:lun  -- irreplaceable (device-unique) first, then boot chain for diagnosis
for entry in \
  modemst1:0 modemst2:0 fsc:0 ssd:0 persist:0 efs:0 sec_efs:0 param:0 \
  steady:0 persistent:0 keystore:0 frp:0 misc:0 \
  fsg:5 cdt:3 ddr:3 \
  devinfo:4 secdata:4 uefivarstore:4 vk:4 apdp:4 storsec:4 \
  xbl:1 xbl_config:1 \
  abl:4 tz:4 hyp:4 devcfg:4 aop:4 vbmeta:4 cmnlib:4 cmnlib64:4 keymaster:4
do
  n="${entry%:*}"; l="${entry#*:}"
  [ -s "$D/$n.img" ] && { echo "SKIP  $n (already have it)"; continue; }
  out=$(timeout 180 "$E" read-part "$n" "$D/$n.img" --lun "$l" --loader "$L" --memory Ufs 2>&1)
  if echo "$out" | grep -q "Successfully read"; then
    printf "OK    %-14s LUN%s  %s\n" "$n" "$l" "$(du -h "$D/$n.img" | cut -f1)"
  else
    printf "FAIL  %-14s LUN%s  %s\n" "$n" "$l" "$(echo "$out" | grep -iE 'error|not found|fail' | head -1)"
    rm -f "$D/$n.img"
  fi
done
echo; echo "=== backup dir ==="; du -sh "$D"; ls "$D" | wc -l
