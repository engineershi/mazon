#!/usr/bin/env python3
"""Detach a process from the caller: double-fork + setsid + stdio -> log file.

Usage: python3 deploy/daemonize.py /path/to/log /cmd arg1 arg2
Exits immediately; the child keeps running in its own session.
"""
import os
import sys


def main():
    if len(sys.argv) < 3:
        print("usage: daemonize.py <logfile> <cmd> [args...]", file=sys.stderr)
        return 1
    logfile, cmd, args = sys.argv[1], sys.argv[2], sys.argv[3:]
    if os.fork():
        return 0
    os.setsid()
    if os.fork():
        return 0
    fd = os.open(logfile, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    os.dup2(fd, 1)
    os.dup2(fd, 2)
    devnull = os.open(os.devnull, os.O_RDONLY)
    os.dup2(devnull, 0)
    os.execv(cmd, [cmd] + args)
    return 1


if __name__ == "__main__":
    sys.exit(main())