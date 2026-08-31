# Unbricking a Samsung Galaxy A52 4G (SM-A525F) over EDL

**Status: recovered.** A hard-bricked SM-A525F — black screen, no Download Mode,
reachable only via test point into EDL 9008 — was fully restored to stock and boots.

As far as I can tell this is the first documented open-source EDL recovery of an
A525F/A52Q. Every source I found said it couldn't be done: the public firehose loader
"only reads", commercial box tools use the same loader, and the handful of XDA threads
on this exact brick are unresolved (one of them is mine).

The blocker turned out not to be the phone, the loader, write protection, or
anti-rollback. **It was two USB/protocol bugs that break every public EDL tool.**

---

## The brick

Flashing a **newer-BIT CP (modem)** via Odin onto an older firmware.

| | |
|-|-|
| Installed bootloaders | `A525FXXS9FXJ1` — **BIT 9** (2024-10-10) |
| CP that was flashed | `A525FXXSCFYF1` — **BIT C (12)** |

The newer CP burned the anti-rollback fuse to 12. The phone's own BIT 9 bootloaders
then failed rollback verification — **intact but refused** — so PBL fell through to EDL.
Black screen. No Download Mode, which matters: Download Mode is an aboot/HLOS feature,
so if PBL rejects the chain earlier there is no key combo that can reach it.

Diagnosis was read off the device itself: dump `abl`, run `strings`, compare the version
string against the modem partition.

## The fix, in one line

Write the **BIT C bootloader chain** over EDL so the chain satisfies the burned fuse.
Download Mode then returns, and Odin does the rest.

## Why that was hard: two bugs in the tooling

Both `bkerler/edl` and `strongtz/edl-ng` fail to write to this loader, for different
reasons. Reads work perfectly in edl-ng (34 MiB/s); writes fail with `Sent 0/N bytes`.

### 1. Firehose replies span multiple packets

`configure` answers with **three `<log>` packets and only then** the real
`<response value="ACK" .../>`:

```
<log value="INFO: Calling handler for configure" />
<log value="INFO: Storage type set to value UFS" />
<log value="DEBUG: Calling usb_al_bulk_set_zlp_mode(TRUE), zlp is not set to 0" />
<response value="ACK" MemoryName="UFS" ... MaxPayloadSizeToTargetInBytes="1048576" ...
```

Stop reading at the first packet and the real ACK stays queued. The device then refuses
every later command and bulk-OUT writes time out with **errno 110 (LIBUSB_ERROR_TIMEOUT)**.
This is *not* a stall — `clear_halt()` does nothing.

**Fix: read until an actual `<response value=` element arrives.**

### 2. The device arms its bulk read *after* the rawmode ACK

After `ACK rawmode="true"` the loader emits one more line —
`INFO: start_sector=N, num_sectors=M` — and only *then* posts its USB read.
Streaming immediately races it and the transfer returns `Sent 0/N bytes`.

**Fix: drain the post-ACK packets and pause (~0.4 s) before streaming.**
This is almost certainly why edl-ng cannot write to this loader.

Also send an explicit **ZLP after each chunk**: the loader sets `ZLPAwareHost="1"` and
payloads are exact multiples of wMaxPacketSize (512), so nothing terminates the transfer.

### The clue that cracked it

```
ERROR: Read non multiple sector size value from USB 219 sectsize4096
```

The loader had read a stray 219-byte XML command *as raw data*. Proof that it **does**
read the endpoint in raw mode — so the failure was timing, not permission. Everything
before that had pointed at write protection or a read-only loader. Both were wrong.

---

## Procedure

```bash
# 0. EDL via test point. Loader: a525f_bitc.elf (see Credits).

# 1. Upload the loader and read the GPT (edl-ng is the better reader)
edl-ng printgpt --loader a525f_bitc.elf --memory Ufs

# 2. BACK UP the irreplaceable partitions first
./tools/backup.sh          # modemst1/2, efs, sec_efs, persist, fsg, cdt ...

# 3. Write the BIT-C bootloader chain (LUN4 first, xbl last)
./tools/flash-chain.sh     # uses flashmap.txt + fh3.py

# 4. Reset -> Download Mode returns via Vol Up + Vol Down + plug USB
edl-ng reset --loader a525f_bitc.elf

# 5. Full stock flash
odin4 -b BL_*.tar.md5 -a AP_*.tar.md5 -c CP_*.tar.md5 -s CSC_*.tar.md5
```

### LUN map (SM-A525F, read from a live device)

| LUN | partitions |
|-|-|
| 0 | `boot` `super` `modem` `userdata` `persist` `efs` `sec_efs` `modemst1/2` `param` `dsp` ... |
| 1 | `xbl` `xbl_config` |
| 2 | `xbl` `xbl_config` (second copy) |
| 3 | `cdt` `ddr` |
| 4 | `abl` `tz` `hyp` `aop` `devcfg` `cmnlib` `cmnlib64` `keymaster` `vbmeta` `vk` `tz_kg` `hdm` `storsec` `uefisecapp` `imagefv` `qupfw` `apdp` `bksecapp` |
| 5 | `fsg` |

**The bootloader chain is not on LUN 0.** Sector numbers are in `flashmap.example.txt`
(verify against your own `printgpt` — do not trust someone else's GPT).

---

## Gotchas that cost hours

- **The PBL sends its Sahara Hello exactly once per EDL entry.** Any edl process that
  starts and is killed eats it; every later attempt hangs on USB read timeouts with the
  device still enumerated. Fix is a physical re-entry — confirm via a changed `lsusb`
  device number.
- **A failed write wedges the loader.** Only a re-upload recovers it. One write attempt
  per EDL entry while debugging.
- Never pipe `edl` through `tail` (buffers everything) or wrap it in a timeout short
  enough to kill a live handshake.
- `edlclient` 3.62's `ErrorDesc` table is **shifted by one** — `SAHARA_STATUS_SUCCESS = 0x00`
  is commented "Invalid command received in current state". A reported *"Invalid image
  type received"* (0x08) actually means **"Invalid image data size received"**.
- `edlclient` 3.62 pads Sahara chunks to 4096; the PBL NAKs the surplus. Upstream does
  not do this. Delete both `padlen` blocks in `Library/sahara.py`.
- `--skipstorageinit` in edlclient only sets an XML attribute; it does **not** skip the
  pre-configure probe read that blocks every command. And it tells the loader not to
  initialise storage, so every later read fails.
- `SM-A525F_prog_firehose_ddr.elf` from some loader repos **is not an ELF** — it begins
  with `.rtc` and is an encrypted container. Use `a525f_bitc.elf`.
- The phone runs on USB power during all this (`bMaxPower 2mA`). Mine was too flat to
  light the display after recovery — charge it on a wall brick before assuming failure.

## Bonus: Samsung FUS changed in 2026

Every public firmware downloader (samloader, samfirm) is broken. `patches/` has a working
samloader patch. Four independent changes: plaintext nonce + fixed-key AES-128-**ECB** hex
signature; endpoints gained a `Smart` infix; request XML uses `BINARY_*` names plus
`REQUEST_TYPE`/`CmdID`; session moved from `JSESSIONID` to a `SESSION` cookie. Success
status is `S00`, not `200`.

## Credits

- `a525f_bitc.elf` — extracted by others from Chimera USB traffic; circulated via
  [Alephgsm/SAMSUNG-EDL-Loaders](https://github.com/Alephgsm/SAMSUNG-EDL-Loaders) and
  [bkerler/Loaders](https://github.com/bkerler/Loaders) (byte-identical). Not redistributed here.
- [bkerler/edl](https://github.com/bkerler/edl) and
  [strongtz/edl-ng](https://github.com/strongtz/edl-ng) — this work is built on both.

## Disclaimer

This worked on my device. EDL writes can permanently brick a phone. Back up
`modemst1`/`modemst2`/`efs`/`persist` before writing anything — those carry your IMEI and
radio calibration and cannot be replaced. No dumps are included in this repo for that reason.
