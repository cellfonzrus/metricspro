#!/usr/bin/env python3
"""System-wide CPU accounting including steal, sampled while something else runs.

Needed because per-process rusage cannot see time the HYPERVISOR took away. If a soak
reports 1.5 of 4 cores used but nothing goes faster, the missing capacity is either steal
or throttling, and only /proc/stat can tell them apart.
"""
import sys, time


def snap():
    with open("/proc/stat") as f:
        p = f.readline().split()[1:]
    v = [int(x) for x in p]
    return v


def main(seconds):
    a = snap()
    t0 = time.time()
    time.sleep(seconds)
    b = snap()
    d = [y - x for x, y in zip(a, b)]
    names = ["user", "nice", "system", "idle", "iowait", "irq", "softirq", "steal",
             "guest", "guest_nice"]
    tot = sum(d[:8])
    print("over %.1fs, %d CPUs:" % (time.time() - t0, __import__("os").cpu_count()))
    for n, v in zip(names, d):
        if v:
            print("  %-9s %7.1f%%" % (n, 100.0 * v / tot))
    busy = tot - d[3] - d[4]
    print("  BUSY(non-idle, non-iowait) %.1f%% of %d cores = %.2f cores"
          % (100.0 * busy / tot, __import__("os").cpu_count(),
             busy / tot * __import__("os").cpu_count()))


if __name__ == "__main__":
    main(float(sys.argv[1]) if len(sys.argv) > 1 else 10)
