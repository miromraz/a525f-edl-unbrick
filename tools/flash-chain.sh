#!/usr/bin/env bash
# Flash the BIT-C bootloader chain using the PROVEN write path (fh3.py).
# Order: LUN4 chain first, xbl (LUN1/2) last, so the chain is complete before
# the first-stage loader is replaced. Resumable: completed writes are recorded
# in flashed.txt and skipped on re-run.
set -u
cd $BASE
lsusb -d 05c6:9008 >/dev/null || { echo "ABORT: phone not in EDL"; exit 1; }
touch flashed.txt
# LUN4 first, then LUN1/2
ORDER=$( (grep -v ':1:\|:2:' flashmap.txt; grep ':1:\|:2:' flashmap.txt) )
total=$(echo "$ORDER" | grep -c .); n=0
while IFS=: read -r f p l s; do
  [ -z "${f:-}" ] && continue
  n=$((n+1))
  key="$f:$p:$l:$s"
  if grep -qxF "$key" flashed.txt; then printf "[%2d/%2d] SKIP  %-16s %-11s LUN%s (done)\n" $n $total "$f" "$p" "$l"; continue; fi
  printf "[%2d/%2d] write %-16s -> %-11s LUN%s sector %s ... " $n $total "$f" "$p" "$l" "$s"
  out=$(timeout 300 python3 fh3.py write "$l" "$s" "bl/$f" 2>&1)
  if echo "$out" | grep -q "WRITE ACKNOWLEDGED"; then
    echo "OK"; echo "$key" >> flashed.txt
  else
    echo "FAILED"
    echo "$out" | grep -iE "error|fail|NAK|log:" | tail -4 | sed 's/^/          /'
    echo "  -> loader likely wedged. Re-enter EDL and re-run this script; it resumes."
    exit 1
  fi
done <<< "$ORDER"
echo
echo "=== ALL WRITES COMPLETE ($(grep -c . flashed.txt)/$total) ==="
