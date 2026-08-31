**SOLVED — the phone is fully recovered and booting stock.**

Writing this up properly because everything I could find said this was impossible, and
the actual blocker turned out to be something nobody had identified.

**What actually killed it**

I flashed a CP from a *newer* firmware than the rest of the phone. Right model, wrong
version. That burned the anti-rollback fuse above my installed bootloaders:

- bootloaders on the device: `A525FXXS9FXJ1` — BIT 9
- the CP I flashed: `A525FXXSCFYF1` — BIT C (12)

So my BIT 9 bootloaders were *intact but refused* by PBL — which is why it was a black
screen with no Download Mode. Download Mode is an aboot feature; if PBL rejects the chain
earlier, no key combo can reach it. Only EDL via test point.

**The fix**

Write the BIT **C** bootloader chain over EDL so it satisfies the burned fuse. Download
Mode comes back on its own, then a normal Odin flash finishes the job.

**Why nobody had done it**

`a525f_bitc.elf` is not read-only, and this is not a write-protection or auth problem.
Every public EDL tool fails to write to it because of two USB/protocol bugs:

1. **Firehose replies span multiple packets.** `configure` sends three `<log>` packets and
   only *then* the real `<response value="ACK">`. If your client stops reading at the first
   packet, the ACK stays queued, the device refuses every later command, and bulk-OUT
   writes time out with errno 110. It looks exactly like the device is refusing writes.
   It isn't. (It's a timeout, not a stall — `clear_halt()` does nothing.)

2. **The loader arms its bulk read *after* the rawmode ACK.** Following
   `ACK rawmode="true"` it emits `INFO: start_sector=N, num_sectors=M`, and only then posts
   the USB read. Stream immediately and you get `Sent 0/4096 bytes`. Wait for that line
   first and the data goes straight through. This is why edl-ng can't write here.

   Also needs an explicit ZLP after each chunk — the loader sets `ZLPAwareHost="1"` and
   payloads are exact multiples of 512, so nothing terminates the transfer.

The clue that cracked it was the loader complaining
`ERROR: Read non multiple sector size value from USB 219 sectsize4096` — it had read a
stray 219-byte XML command *as raw data*. That proved it does read the endpoint in raw
mode, so the problem was timing, not permission. Before that I was convinced it was UFS
write protection.

**Tools + full writeup**

https://github.com/miromraz/a525f-edl-unbrick

Includes the working client (`fh3.py`), the chain flasher, the verified per-LUN partition
map for this device (the bootloader chain is on LUN 1/2/4, **not** LUN 0), and a patch for
`samloader` since Samsung changed the FUS protocol in 2026 and every public firmware
downloader is currently broken.

**Two warnings**

- Back up `modemst1`, `modemst2`, `efs`, `sec_efs`, `persist`, `fsg` before writing
  anything. That's your IMEI and radio calibration and nothing can replace it.
- The PBL sends its Sahara Hello once per EDL entry. Any edl process that starts and dies
  eats it, and everything after that hangs with the device still enumerated. If you see
  endless USB timeouts, re-enter EDL rather than retrying — check `lsusb` for a changed
  device number to confirm.

Happy to help if someone else is stuck on the same thing.
