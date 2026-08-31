#!/usr/bin/env python3
"""Working firehose client. The key fix: drain replies until a real <response>.

The loader answers a command with SEVERAL packets -- typically <log> lines
first and the actual <response value="ACK"/> last. Stopping after the first
packet leaves the ACK queued and the device then refuses every later command
(bulk-OUT writes time out, errno 110). Read to the <response> and it all works.

Also: never call set_configuration() on an already-configured device; that
resets endpoint state and kills the resident loader session.
"""
import re, sys, time
import usb.core, usb.util

SECTOR = 4096
HDR = '<?xml version="1.0" ?><data>'

def dev():
    d = usb.core.find(idVendor=0x05C6, idProduct=0x9008)
    if d is None: sys.exit("no 05c6:9008 on the bus")
    try:
        if d.is_kernel_driver_active(0): d.detach_kernel_driver(0)
    except Exception: pass
    cfg = d.get_active_configuration()
    intf = cfg[(0, 0)]
    o = usb.util.find_descriptor(intf, custom_match=lambda e: usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_OUT)
    i = usb.util.find_descriptor(intf, custom_match=lambda e: usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_IN)
    return d, o, i

def resp(i, budget=15.0, verbose=True):
    """Read until an actual <response .../> element arrives."""
    buf = b""
    t0 = time.time()
    while time.time() - t0 < budget:
        try:
            c = bytes(i.read(16384, timeout=800))
        except usb.core.USBError:
            if b"<response" in buf: break
            continue
        if not c: continue
        buf += c
        txt = c.decode("utf-8", "replace")
        for m in re.finditer(r'<log value="([^"]*)"', txt):
            if verbose: print(f"      log: {m.group(1)}")
        if b"<response" in buf: break
    m = re.search(r'<response value="(\w+)"([^/]*)', buf.decode("utf-8", "replace"))
    if m:
        ack, attrs = m.group(1), m.group(2)
        if verbose: print(f"      => {ack} {attrs.strip()[:150]}")
        return ack == "ACK", buf
    if verbose: print("      => NO <response> RECEIVED")
    return False, buf

def cmd(o, i, xml, label, budget=15.0):
    print(f"   >> {label}")
    o.write(xml.encode(), timeout=8000)
    return resp(i, budget)

def configure(o, i):
    ok, buf = cmd(o, i, HDR + '<configure MemoryName="ufs" Verbose="0" AlwaysValidate="0" '
        'MaxDigestTableSizeInBytes="2048" MaxPayloadSizeToTargetInBytes="1048576" '
        'ZLPAwareHost="1" SkipStorageInit="0" SkipWrite="0"/></data>', "configure")
    m = re.search(r'MaxPayloadSizeToTargetInBytes="(\d+)"', buf.decode("utf-8", "replace"))
    mp = int(m.group(1)) if m else 1048576
    print(f"   negotiated MaxPayloadSizeToTargetInBytes = {mp}")
    return ok, mp

def main():
    what = sys.argv[1]
    d, o, i = dev()
    try: resp(i, 0.5, verbose=False)
    except Exception: pass
    ok, maxpayload = configure(o, i)
    if not ok: sys.exit("configure failed")

    if what == "read":
        lun, sec, n, out = int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4]), sys.argv[5]
        print(f"   >> read LUN{lun} sector {sec} x{n}")
        o.write((HDR + f'<read SECTOR_SIZE_IN_BYTES="{SECTOR}" num_partition_sectors="{n}" '
                 f'physical_partition_number="{lun}" start_sector="{sec}"/></data>').encode(), timeout=8000)
        time.sleep(0.2)
        data = b""
        t0 = time.time()
        while len(data) < n * SECTOR and time.time() - t0 < 30:
            try: data += bytes(i.read(min(maxpayload, 65536), timeout=2000))
            except usb.core.USBError: break
        open(out, "wb").write(data[:n * SECTOR])
        print(f"   read {len(data)} bytes -> {out}")
        resp(i, 5)

    elif what == "write":
        lun, sec, path = int(sys.argv[2]), int(sys.argv[3]), sys.argv[4]
        payload = open(path, "rb").read()
        if len(payload) % SECTOR:
            payload += b"\x00" * (SECTOR - len(payload) % SECTOR)
        nsec = len(payload) // SECTOR
        print(f"   >> program LUN{lun} sector {sec}, {nsec} sectors, {len(payload)} bytes")
        o.write((HDR + f'<program SECTOR_SIZE_IN_BYTES="{SECTOR}" num_partition_sectors="{nsec}" '
                 f'physical_partition_number="{lun}" start_sector="{sec}"/></data>').encode(), timeout=8000)
        ok, buf = resp(i, 15)
        if not ok: sys.exit("device did not ACK the program command")
        # The device keeps emitting log packets after the rawmode ACK and only
        # posts its bulk read once it has settled. Writing immediately races it
        # and the transfer times out with 0 bytes. Drain the tail, then pause.
        print("   draining post-ACK packets before streaming...")
        t_end = time.time() + 1.5
        while time.time() < t_end:
            try:
                extra = bytes(i.read(16384, timeout=300))
                if extra:
                    for m in re.finditer(r'<log value="([^"]*)"', extra.decode("utf-8", "replace")):
                        print(f"      log: {m.group(1)}")
            except usb.core.USBError:
                break
        time.sleep(0.4)
        print("   streaming payload...")
        sent = 0
        try:
            SUB = int(__import__("os").environ.get("FH_SUB", str(maxpayload)))
            while sent < len(payload):
                chunk = payload[sent:sent + min(maxpayload, SUB)]
                sent += o.write(chunk, timeout=20000)
                # ZLPAwareHost="1": chunks are exact multiples of wMaxPacketSize
                # (512), so USB emits no terminating short packet and the
                # loader's bulk read never completes. Send an explicit ZLP.
                o.write(b"", timeout=5000)
                print(f"      sent {sent}/{len(payload)} (+ZLP)")
        except usb.core.USBError as e:
            sys.exit(f"   STREAM FAILED after {sent} bytes: {e}")
        print(f"   [+] streamed {sent} bytes, awaiting final response")
        ok2, _ = resp(i, 20)
        print(f"   [+] WRITE {'ACKNOWLEDGED' if ok2 else 'NOT ACKNOWLEDGED'}")
main()
