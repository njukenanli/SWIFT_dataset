import re
import shlex
import os
import json
import time
from typing import TypedDict
from src.utils.runtime import SetupRuntime
from src.utils.pyt.content_extract import Extractor
from typing import List, Tuple



class PerLocationCovInfo(TypedDict):
    class_name: str | None
    func_name: str | None
    line_no: tuple[int, int] # begin, end, both ends are also included

class CovInfo(PerLocationCovInfo):
    file_name: str

class CoverageExtractor:
    def __init__(self, instance_id: str, container: SetupRuntime, patch_to_apply: str, cmd: str):
        self.instance_id = instance_id
        self.container: SetupRuntime = container
        self.container.send_command(cmd)
        self.container.send_command(f"""git apply - <<'NEW_PATCH'\n{patch_to_apply}\nNEW_PATCH""").output

    def _build_trace_command(self, command: str) -> str:
        """
        Build a command that traces Python execution and writes to coverage_file.
        Uses sys.settrace to track function calls and line execution.
        Supports subprocess tracing via sitecustomize.py auto-import.
        """
        # Create a Python tracer script
        tracer_script = f"""
import sys
import os
import atexit
import threading

trace_file_path = "/mnt/{self.instance_id}"
trace_lock = threading.Lock()

def get_trace_file():
    return open(trace_file_path, "a", buffering=1)

trace_file = get_trace_file()

def trace_calls(frame, event, arg):
    global trace_file, trace_lock
    if os is None or trace_file is None:
        return None

    if event in ("call", "line", "return"):
        code = frame.f_code
        filename = code.co_filename
        func_name = code.co_name

        if func_name != "<lambda>" and func_name != "<module>" and func_name.startswith("<") and func_name.endswith(">"):
            return trace_calls

        line_no = frame.f_lineno

        class_name = None
        if "self" in frame.f_locals:
            try:
                class_name = type(frame.f_locals["self"]).__name__
            except:
                pass
        elif "cls" in frame.f_locals:
            try:
                cls_obj = frame.f_locals["cls"]
                class_name = cls_obj.__name__ if isinstance(cls_obj, type) else None
            except:
                pass

        with trace_lock:
            try:
                trace_file.write(
                    f"{{event}}|{{filename}}|{{class_name or ''}}|{{func_name}}|{{line_no}}\\n"
                )
                trace_file.flush()
            except:
                pass

    return trace_calls

def cleanup():
    global trace_file
    if trace_file is not None:
        try:
            trace_file.close()
        except:
            pass

atexit.register(cleanup)
sys.settrace(trace_calls)

try:
    threading.settrace(trace_calls)
except:
    pass
"""
        # Write tracer script to temp file
        self.container.send_command(f"cat > /tmp/tracer.py << 'TRACER_EOF'\n{tracer_script}\nTRACER_EOF")
        
        # Build the traced command
        # Simply prepend PYTHONPATH to include tracer and use sitecustomize
        # Write sitecustomize.py to auto-import tracer
        # This ensures ALL Python subprocesses also load the tracer
        sitecustomize = '''import tracer'''
        self.container.send_command(f"cat > /tmp/sitecustomize.py << 'SITE_EOF'\n{sitecustomize}\nSITE_EOF")
        
        # Run command with PYTHONPATH set to load sitecustomize in all processes
        self.container.send_command("export PYTHONPATH=/tmp:$PYTHONPATH")
        traced_cmd = f"PYTHONPATH=/tmp:$PYTHONPATH {command}"
        
        return traced_cmd

    def _parse_trace_output(self, coverage_output: str) -> list[CovInfo]:
        """
        Parse the trace output file and convert to CovInfo list.
        Groups consecutive lines in the same function into ranges.
        """
        lines = coverage_output.strip().split('\n')
        cov_list: list[CovInfo] = []
        
        current_func_info = None
        current_lines = []
        
        for line in lines:
            if not line.strip():
                continue
            
            parts = line.split('|')
            if len(parts) != 5:
                continue
            
            event, filename, class_name, func_name, line_no = parts
            
            # Skip non-line events for coverage purposes
            if event != "line":
                continue
            
            try:
                line_no = int(line_no)
            except ValueError:
                continue
            
            func_key = (filename, class_name or None, func_name)
            
            # Check if we're still in the same function
            if current_func_info == func_key:
                current_lines.append(line_no)
            else:
                # Save previous function's coverage if exists
                if current_func_info and current_lines:
                    self._add_coverage_entry(cov_list, current_func_info, current_lines)
                
                # Start new function
                current_func_info = func_key
                current_lines = [line_no]
        
        # Don't forget the last function
        if current_func_info and current_lines:
            self._add_coverage_entry(cov_list, current_func_info, current_lines)
        
        return cov_list

    def _add_coverage_entry(self, cov_list: list[CovInfo], func_info: tuple, lines: list[int]):
        """Helper to add a coverage entry with line range."""
        filename, class_name, func_name = func_info
        
        if not lines:
            return
        
        min_line = min(lines)
        max_line = max(lines)
        
        cov_list.append(CovInfo(
            file_name=filename,
            class_name=class_name,
            func_name=func_name,
            line_no=(min_line, max_line)
        ))

    def get_cov_class_func_lineno_inorder(self, command: str) -> list[CovInfo]:
        '''
        only need to consider python environment, like "pytest ...", "python main.py", "sweagent run ..."
        The return value list[CovInfo] should be strictly in the order of the lines executed!
        '''
        trace_cmd = self._build_trace_command(command)

        # 1) Run traced command to write coverage_file
        out = self.container.send_command(trace_cmd, timeout=10*60).output
        # print(out, flush=True)

        # 2) Read coverage_file
        if os.path.exists(f"data/logs/{self.instance_id}"):
            with open(f"data/logs/{self.instance_id}") as f:
                coverage_output = f.read()
        else:
            print(f"Error! data/logs/{self.instance_id} not found!")
            coverage_output = ""

        # 3) Parse trace output to CovInfo list in execution order
        cov_list = self._parse_trace_output(coverage_output)
        self.container.send_command(f"rm  /mnt/{self.instance_id}")

        return cov_list

    @staticmethod
    def _merge_intervals(intervals: list[tuple[int]]) -> list[tuple[int]]:
        if not intervals:
            return []
        res = []
        intervals = sorted(intervals)
        cur = list(intervals[0])
        for interval in intervals[1:]:
            if interval[0] <= cur[1]:
                if interval[1] > cur[1]:
                    cur[1] = interval[1]
            elif interval[0] - 1 == cur[1]:
                cur[1] = interval[1]
            else:
                res.append(tuple(cur))
                cur = list(interval)
        res.append(tuple(cur))
        return res
    
    @classmethod
    def merge_covs(cls, coverage: list[CovInfo]) -> dict[str, list[tuple[int, int]]]:
        res: dict[str, list[tuple[int, int]]] = {}
        for info in coverage:
            if info["file_name"] in res.keys():
                res[info["file_name"]].append(info["lineno"])
            else:
                res[info["file_name"]] = [info["lineno"]]
        for key in res.keys():
            res[key] = cls._merge_intervals(res[key])
        return res

    def get_cov_order_content(self, 
                              python_command: str, 
                              bug_locations: dict[str, list[tuple[int, int]]],
                              get_content_above_or_below: bool) -> tuple[list[CovInfo], dict[str, str]]:
        '''
        input:
        get_content_above_or_below: True for above and False for below
        returns:
        Covered locations in execution order: list[CovInfo]
        The codes of the covered locations: dict[filename, code_with_lineno]

        '''
        cov_inoder: list[CovInfo] = self.get_cov_class_func_lineno_inorder(python_command)
        cov_above, cov_below = self.cov_separator(cov_inoder, bug_locations)
        target = cov_above if get_content_above_or_below else cov_below
        lineno_intervals = self.merge_covs(target)
        content = Extractor.get_content()
    
    def __del__(self):
        self.container.cleanup()

    @staticmethod
    def _merge_intervals(intervals: list[tuple[int]]) -> list[tuple[int]]:
        if not intervals:
            return []
        res = []
        intervals = sorted(intervals)
        cur = list(intervals[0])
        for interval in intervals[1:]:
            if interval[0] <= cur[1]:
                if interval[1] > cur[1]:
                    cur[1] = interval[1]
            elif interval[0] - 1 == cur[1]:
                cur[1] = interval[1]
            else:
                res.append(tuple(cur))
                cur = list(interval)
        res.append(tuple(cur))
        return res
    
    @classmethod
    def build_idx(cls, res: list[CovInfo]) -> dict[str, list[tuple[tuple[int,int],int]]]:
        #files = []
        valid_res = {}
        #return_value = []
        for idx in range(len(res)):
            name = res[idx]["file_name"]
            if not name.strip():
                continue
            if "test" in name.replace("testbed","") and "_pytest" not in name:
                continue
            #if name not in files:
            #    files.append(name)
            if name in valid_res.keys():
                valid_res[name].append((res[idx]["line_no"], idx))
            else:
                valid_res[name] = [(res[idx]["line_no"], idx)]
        #for name in files:
        #    return_value.append([name, cls._merge_intervals(valid_res[name])])
        #return return_value
        return valid_res

if __name__ == "__main__":
    with open("data/verified/4_verified_test_loc.jsonl") as f:
        instances = [json.loads(i) for i in f]
    instance = instances[315]
    print(instance["instance_id"])
    image = "swebench/sweb.eval.x86_64." + instance["instance_id"].replace("__", "_1776_")
    patch = instance["patch"] + "\n\n" + instance["test_patch"] 
    cov = CoverageExtractor(image, instance["instance_id"], patch)
    res = cov.get_cov_class_func_lineno_inorder(instance["f2p_cmd"])
    valid_res = cov.merge_cov(res)
    #print(json.dumps(res, indent = True))
    print(valid_res)
    del cov

Frame = tuple[str, int, str, str] # file_path, lineno, func_name, line content
Stack = list[Frame]

class Trace(TypedDict):
    error: str
    error_content: str
    frames: Stack 

class ErrorStackExtractor:
    def __init__(self, instance_id: str, container: "SetupRuntime", patch_to_apply: str, cmd: str):
        self.instance_id = instance_id
        self.container: "SetupRuntime" = container
        # Setup repo/runtime, then apply patch (same pattern as CoverageExtractor)
        self.container.send_command(cmd).output
        self.container.send_command(
            f"git apply - <<'NEW_PATCH'\n{patch_to_apply}\nNEW_PATCH"
        ).output
        self.last_std = ""

    def _build_error_trace_command(self, command: str) -> str:
        """
        Inject a sitecustomize-based tracer that records the last exception stack
        (even if caught) and writes it at process exit if the process exits non-zero.

        Output path inside container: /mnt/{instance_id}
        """
        out_path = f"/mnt/{self.instance_id}"

        tracer_script = r'''

import sys
import os
import threading
import linecache

OUT_PATH = "REPLACE_OUT_PATH"

# Capture builtin references before they're cleared during shutdown
_open = open
_repr = repr
_getattr = getattr
_str = str

last_exc_type = None
last_exc_repr = None

def _get_context(filename, lineno, radius) -> str:
    """
    Returns a list of (line_number, text, is_target_line).
    Lines may be missing if source isn't available.
    """
    start = max(1, lineno - radius)
    end = lineno + radius
    out = []
    for n in range(start, end + 1):
        txt = linecache.getline(filename, n)
        if (not isinstance(txt, str)) or (not txt):
            continue
        txt=txt.rstrip('\n')
        out.append(f"""{n}:{txt}""")
    return "\n".join(out)


def _record_exception(exc_type, exc_val, exc_tb):
    global last_exc_type, last_exc_repr

    # Ignore SystemExit noise
    if exc_type is SystemExit:
        return
    # Optional: ignore KeyboardInterrupt
    if exc_type is KeyboardInterrupt:
        return

    # Build traceback frames outermost -> innermost
    frames = []
    tb = exc_tb
    while tb is not None:
        f = tb.tb_frame
        path = f.f_code.co_filename
        lineno = tb.tb_lineno
        if "test" in path.replace("/testbed/", "").replace("/src/_pytest/", "").lower():
            radius=1
        else:
            radius=15
        try:
            context=_get_context(path, lineno, radius)
        except:
            context=""
        frames.append((path, lineno, f.f_code.co_name, context))
        tb = tb.tb_next

    if not frames:
        return

    last_exc_type = _getattr(exc_type, "__name__", _str(exc_type))
    try:
        last_exc_repr = _repr(exc_val)
    except Exception:
        last_exc_repr = "<unrepr-able exception>"
    
    with _open(OUT_PATH, "a", encoding="utf-8", errors="replace") as f:
        f.write(f"LAST_TRACE|exc={last_exc_type}|val={last_exc_repr}<frame></frame>\n")
        for file, lineno, func, ctx in frames:
            f.write(f"{file}|{lineno}|{func}|{ctx}<frame></frame>\n")
        f.write("\n<===============NEXT_TRACE===============>\n\n")

def _trace_fn(frame, event, arg):
    if event == "exception":
        exc_type, exc_val, exc_tb = arg
        _record_exception(exc_type, exc_val, exc_tb)
    return _trace_fn

def _threading_excepthook(args):
    _record_exception(args.exc_type, args.exc_value, args.exc_traceback)

try:
    threading.excepthook = _threading_excepthook
except Exception:
    pass

sys.settrace(_trace_fn)

'''.replace("REPLACE_OUT_PATH", out_path)

        # Write tracer + sitecustomize into /tmp and ensure import works
        self.container.send_command(
            "cat > /tmp/tracer_err.py << 'TRACER_EOF'\n"
            f"{tracer_script}\n"
            "TRACER_EOF"
        )
        self.container.send_command(
            "cat > /tmp/sitecustomize.py << 'SITE_EOF'\n"
            "import tracer_err\n"
            "SITE_EOF"
        )

        # Ensure env points to same output location (belt + suspenders)
        self.container.send_command("export PYTHONPATH=/tmp:$PYTHONPATH")
        traced_cmd = f"PYTHONPATH=/tmp:$PYTHONPATH {command}"
        return traced_cmd

    def _parse_error_trace_file(self, content: str) -> Trace|None:
        """
        Parse tracer output:
          header: LAST_TRACE|...
          frames: file|lineno|func
        Returns frames in stack order: outermost -> innermost
        Includes line content from source files.
        """
        if not content:
            return None

        lines = [ln.strip() for ln in content.split("<frame></frame>\n") if ln.strip()]
        if not lines:
            return None

        if lines[0].startswith("LAST_TRACE|"):
            error, error_content = lines[0].replace("LAST_TRACE|exc=", "").split("|val=")
        frames: List[Tuple[str, int, str, str]] = []
        for ln in lines[1:] if lines[0].startswith("LAST_TRACE|") else lines:
            parts = ln.split("|")
            if len(parts) != 4:
                continue
            file_path, lineno_s, func, body = parts
            try:
                lineno = int(lineno_s)
            except ValueError:
                continue
            frames.append((file_path, lineno, func, body))
        return {"error": error, "error_content": error_content, "frames": frames}

    def _filter_not_source_code(self, frames: list[tuple[str, int, str, str]]) -> list[tuple[str, int, str, str]]:
        effective_frame=[]
        for frame in frames:
            if "conda" in frame[0]:
                continue
            if "python3." in frame[0]:
                continue
            if "site-packages" in frame[0]:
                continue
            if "frozen" in frame[0]:
                continue
            #if "test" in frame[0].replace("/testbed", "") and "src/_pytest" not in frame[0]:
            #    continue
            effective_frame.append(frame)
        return effective_frame
    
    def _get_effective_trace(self, trace: Trace) -> bool:
        frames = trace["frames"]
        if len(frames) <= 1:
            return False
        effective = self._filter_not_source_code(frames)
        if effective:
            return True
        return False
    
    def _filter_error_at_test_point(self, trace_list: list[Trace]):
        for trace in trace_list:
            deepest = trace["frames"][-1][0].replace("/testbed/", "").replace("src/_pytest", "").lower()
            if "test" in deepest:
                return []
        return trace_list

    def _merge(self, trace_list: list[Trace]) -> list[Trace]:
        ''' Trace would progessively increase '''
        mapping = {}
        for trace in trace_list:
            key=f'{trace["frames"][-1][0]}_{trace["frames"][-1][1]}'
            mapping[key] = trace
        return list(mapping.values())
    
    def get_last_error_stack_trace(self, command: str) -> List[Trace]:
        """
        write last stack to /mnt/{self.instance_id} and read from data/logs/{self.instance_id},
        return List in Error occur order with each element as
            [(file_path, lineno, func_name, line_content), ...] in stack order (outermost -> innermost error point).
        """
        trace_cmd = self._build_error_trace_command(command)

        # 1) Run traced command (output usually irrelevant; tracer writes file at exit if non-zero)
        run_out = self.container.send_command(trace_cmd).output
        self.last_std = run_out
        #print(run_out, flush=True)

        # 2) Read traced file (host-side path matches your CoverageExtractor convention)
        host_path = f"data/logs/{self.instance_id}"
        if os.path.exists(host_path):
            with open(host_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        else:
            print(f"Error! {host_path} not found!")
            content = ""

        # 3) Parse into frames
        trace_list = [self._parse_error_trace_file(trace) for trace in content.split("<===============NEXT_TRACE===============>")]
        log=f"{self.instance_id}\n"
        log+=f"{len(trace_list)} "
        trace_list = [trace for trace in trace_list if (trace is not None) and self._get_effective_trace(trace)]
        log+=f"{len(trace_list)} "
        trace_list = [trace for trace in trace_list if trace["error"] in self.last_std]
        log+=f"{len(trace_list)} "
        trace_list = self._merge(trace_list)
        log+=f"{len(trace_list)} "
        trace_list = self._filter_error_at_test_point(trace_list)
        log+=f"{len(trace_list)} \n"
        for i in range(len(trace_list)):
            log+=f"""{len(trace_list[i]["frames"])} {trace_list[i]["error_content"]}\n"""
            log+=str([f[0] for f in trace_list[i]["frames"]])+"\n"
        print(log, "\n", json.dumps(trace_list, indent=True), flush=True)

        return trace_list

MergedTrace = list[tuple[str,str,list[tuple[int,str]]]] # file_path, func_name, list[(line_number, line_content)]

class StackTraceExtractorByInjection:
    def __init__(self, instance_id: str, container: "SetupRuntime", patch_to_apply: str, cmd: str):
        self.instance_id = instance_id
        self.container: "SetupRuntime" = container
        # Setup repo/runtime, then apply patch (same pattern as CoverageExtractor)
        self.container.send_command(cmd).output
        self.container.send_command(
            f"git apply - <<'NEW_PATCH'\n{patch_to_apply}\nNEW_PATCH"
        ).output
        self.last_std = ""

    def inject_stack_collector_into_source(self, locs: dict[str, tuple[int,int]]) -> None:
        '''
        input: mapping of file_path to line number range
        Inject a trace collector into each location to print stack trace when the code is executed to that location
        print stack trace to /mnt/instance_id.txt with each line in the format of file_path lineno func_name line_content
        the first item the most outside frame, the last item the most inner frame (at the injected location)
        should not interrupt normal code execution
        '''
        pass

    def parse_trace(self, log: str) -> list[Stack]:
        '''
        the first item the most outside frame, the last item the most inner frame (at the injected location)
        '''

    def merge_trace_by_func(self, traces: list[Stack]) -> list[MergedTrace]:
        '''
        Merge two stacks if file_path and func_name are the same 
        '''
    
    def extract(self, test_cmd: str, locs: dict[str, tuple[int,int]]) -> list[MergedTrace]:
        self.inject_stack_collector_into_source(locs)
        self.container.send_command(test_cmd)
        time.sleep(16)
        with open(f"data/logs/{self.instance_id}.txt") as f:
            log=f.read()
        stacks: list[Stack] = self.parse_trace(log)
        merged_stacs: list[MergedTrace] = self.merge_trace_by_func(stacks)
        print(self.instance_id, [len(i) for i in merged_stacs], flush=True)
        return merged_stacs


