#!/usr/bin/env python3
"""
Prove trace order for: x = b(...) + c(...)
Expected (single-threaded):
1) line event for the assignment line
2) b() call/line/return
3) c() call/line/return
"""

import sys
import os
import threading
from collections import defaultdict

events = []
lock = threading.Lock()

def trace_calls(frame, event, arg):
    # Keep it simple: only record events from THIS file.
    code = frame.f_code
    filename = os.path.abspath(code.co_filename)
    if filename != THIS_FILE:
        return trace_calls

    if event in ("call", "line", "return"):
        func = code.co_name
        lineno = frame.f_lineno
        # Record in-memory (no file I/O noise)
        with lock:
            events.append((event, lineno, func))
    return trace_calls

def b():
    # A couple of lines so you can see line events inside b
    y = 10
    return y

def c():
    # A couple of lines so you can see line events inside c
    z = 20
    return z

def demo():
    # >>> This is the key line we care about
    x = b() + c()
    return x

def main():
    sys.settrace(trace_calls)
    try:
        result = demo()
    finally:
        sys.settrace(None)

    print(f"demo() returned: {result}\n")

    # Print the raw event stream
    print("Raw trace (event, line, func):")
    for e in events:
        print(e)

    # Summarize the first few key transitions
    print("\nKey ordering check:")
    # Find the first 'line' event that occurs inside demo on the assignment line.
    # (We locate it by searching for the first line event in demo that is not the 'def demo' line.)
    demo_line_events = [(i, ev) for i, ev in enumerate(events) if ev[0] == "line" and ev[2] == "demo"]
    if not demo_line_events:
        print("No demo line events found (unexpected).")
        return

    first_demo_line_idx, (ev, lineno, func) = demo_line_events[0]
    print(f"First line event in demo: index={first_demo_line_idx}, line={lineno}")

    # Find first call to b and c after that
    def find_first_call(funcname, start_idx):
        for i in range(start_idx, len(events)):
            ev, ln, fn = events[i]
            if ev == "call" and fn == funcname:
                return i, ln
        return None, None

    b_idx, b_ln = find_first_call("b", first_demo_line_idx)
    c_idx, c_ln = find_first_call("c", first_demo_line_idx)

    print(f"First call to b after demo line: index={b_idx}, line={b_ln}")
    print(f"First call to c after demo line: index={c_idx}, line={c_ln}")

    if b_idx is not None and c_idx is not None:
        if first_demo_line_idx < b_idx < c_idx:
            print("\n✅ Observed order: demo line -> b() -> c() (left-to-right)")
        else:
            print("\n❌ Order did not match expectation (likely threading/async or unusual tracing).")

if __name__ == "__main__":
    THIS_FILE = os.path.abspath(__file__)
    main()
