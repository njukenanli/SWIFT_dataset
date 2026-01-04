import re
import shlex
import os
import json
from typing import TypedDict
from src.utils.runtime import SetupRuntime
from src.utils.pyt.content_extract import Extractor
from typing import List, Tuple


class Trace(TypedDict):
    error: str
    error_content: str
    frames: list[tuple[str, int, str, str]] # file_path, lineno, func_name, line content

class ErrorStackExtractor:
    '''
    The Golang version.
    Go uses panic/recover instead of try/catch. Test frameworks like `testing` package
    recover panics internally, hiding stack traces. We inject a custom test wrapper
    that captures panic stack traces before recover() consumes them.
    '''
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
        Run Go command with GOTRACEBACK=all to capture full panic stack traces.
        The raw output is written to /mnt/{instance_id} for later parsing on host.

        Output path inside container: /mnt/{instance_id}
        """
        out_path = f"/mnt/{self.instance_id}"
        
        # Simply run with GOTRACEBACK=all and capture all output (stdout + stderr)
        # No Python needed in container - just shell commands
        traced_cmd = f"GOTRACEBACK=all {command} > {out_path} 2>&1;"
        
        return traced_cmd

    def _parse_go_panic_output(self, raw_output: str) -> List[Trace]:
        """
        Parse raw Go output containing error traces directly to Trace objects.
        
        Handles three types of Go errors:
        1. Panics: panic: <message> followed by goroutine stack
        2. Compile errors: file.go:line:col: error message (depth=1, no stack trace)
        3. Test failures: --- FAIL: TestName with Error Trace: file.go:line
        
        Returns list of Trace objects.
        """
        result: List[Trace] = []
        
        # 1. Parse panic traces (original logic)
        sections = re.split(r'(?=panic:|runtime error:)', raw_output)
        for section in sections:
            section = section.strip()
            if not section:
                continue
            if 'panic:' not in section and 'runtime error:' not in section:
                continue
            
            lines = section.split('\n')
            if not lines:
                continue
            
            err_line = lines[0].strip()
            if 'panic:' in err_line:
                err_type = 'panic'
                err_val = err_line.replace('panic:', '').strip()
                # If error message is empty on first line, collect from following lines
                # until we hit a goroutine or stack frame line
                if not err_val:
                    msg_lines = []
                    j = 1
                    while j < len(lines):
                        l = lines[j]
                        # Stop at goroutine header or stack frame
                        if l.strip().startswith('goroutine ') or (l.startswith('\t') and '/' in l and ':' in l):
                            break
                        # Also stop at function call line (has parens but not indented)
                        if '(' in l and not l.startswith('\t') and not l.startswith(' '):
                            break
                        msg_lines.append(l.strip())
                        j += 1
                    err_val = ' '.join(msg_lines).strip()
                    # Truncate if too long
                    if len(err_val) > 500:
                        err_val = err_val[:500] + "..."
            elif 'runtime error:' in err_line:
                err_type = 'runtime_error'
                idx = err_line.find('runtime error:')
                err_val = err_line[idx + 14:].strip()
            else:
                continue
            
            frames: List[Tuple[str, int, str, str]] = []
            i = 1
            while i < len(lines):
                line = lines[i].strip()
                if not line or line.startswith('goroutine ') or line.startswith('created by'):
                    i += 1
                    continue
                if '(' in line and not line.startswith('/') and not line.startswith('\t'):
                    func_name = line.split('(')[0]
                    if i + 1 < len(lines):
                        file_line = lines[i + 1].strip()
                        if ':' in file_line and '/' in file_line:
                            parts = file_line.split(':')
                            if len(parts) >= 2:
                                file_path = parts[0]
                                lineno_part = parts[1].split()[0]
                                try:
                                    lineno = int(lineno_part)
                                    frames.append((file_path, lineno, func_name, ""))
                                except ValueError:
                                    pass
                            i += 2
                            continue
                i += 1
            
            if frames:
                result.append({
                    "error": err_type,
                    "error_content": err_val,
                    "frames": frames
                })
        
        # 2. Parse compile errors: file.go:line:col: error message
        # Also handle C/CGO compile errors: file.c/cpp:line:col: error:
        compile_error_pattern = re.compile(
            r'^([^\s:]+\.(?:go|c|cpp|cc|h|hpp)):(\d+):(\d+):\s*(?:error:\s*)?(.+)$', 
            re.MULTILINE
        )
        for match in compile_error_pattern.finditer(raw_output):
            file_path, line_no, col, error_msg = match.groups()
            # Make path absolute if relative
            if not file_path.startswith('/'):
                file_path = '/testbed/' + file_path
            result.append({
                "error": "compile_error",
                "error_content": error_msg.strip(),
                "frames": [(file_path, int(line_no), "", "")]
            })
        
        # 3. Parse test failures with Error Trace
        # Pattern: "Error Trace:    file.go:line" or "file_test.go:123: error message"
        lines = raw_output.split('\n')
        i = 0
        while i < len(lines):
            line = lines[i].strip()
            
            # Look for "--- FAIL: TestName"
            if line.startswith('--- FAIL:'):
                test_name = line.replace('--- FAIL:', '').strip().split()[0]
                frames: List[Tuple[str, int, str, str]] = []
                error_content = ""
                
                # Scan following lines for error trace and error message
                j = i + 1
                while j < len(lines) and not lines[j].strip().startswith('---'):
                    trace_line = lines[j].strip()
                    
                    # Match "Error Trace:    file.go:123"
                    if 'Error Trace:' in trace_line:
                        trace_match = re.search(r'(\S+\.go):(\d+)', trace_line)
                        if trace_match:
                            file_path = trace_match.group(1)
                            if not file_path.startswith('/'):
                                file_path = '/testbed/' + file_path
                            lineno = int(trace_match.group(2))
                            frames.append((file_path, lineno, test_name, ""))
                    
                    # Match "Error:" line for error content
                    if trace_line.startswith('Error:'):
                        error_content = trace_line.replace('Error:', '').strip()
                    
                    # Match direct test output: "file_test.go:123: message" or "file.go:123: message"
                    # Note: line may be indented with spaces/tabs
                    direct_match = re.match(r'\s*(\S+\.go):(\d+):\s*(.+)', trace_line)
                    if direct_match:
                        file_path = direct_match.group(1)
                        if not file_path.startswith('/'):
                            file_path = '/testbed/' + file_path
                        lineno = int(direct_match.group(2))
                        msg = direct_match.group(3).strip()
                        if not error_content:
                            error_content = msg[:200]  # Truncate long messages
                        frames.append((file_path, lineno, test_name, ""))
                    
                    j += 1
                
                if frames:
                    result.append({
                        "error": "test_failure",
                        "error_content": error_content or f"Test {test_name} failed",
                        "frames": frames
                    })
                i = j
            else:
                i += 1
        
        return result

    def _filter_not_source_code(self, frames: list[tuple[str, int, str, str]]) -> list[tuple[str, int, str, str]]:
        """Filter out Go runtime, stdlib, and third-party frames."""
        effective_frame = []
        for frame in frames:
            file_path = frame[0]
            # Skip Go lib
            if "/usr/local/go/" in file_path:
                continue
            # Skip vendor/third-party dependencies
            if "/vendor/" in file_path:
                continue
            if "/pkg/mod/" in file_path:
                continue
            #if "_test.go" in file_path.replace("testbed","").lower():
            #    continue
            effective_frame.append(frame)
        return effective_frame
    
    def _get_effective_trace(self, trace: Trace) -> bool:
        """Check if trace has useful source code frames."""
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
        ''' 
        If the last frame -- the deepest frame -- the error point -- of 2 traces are same, merge.
        As trace would progessively increase, we always keep the last trace with the same last frame -- the deepest one.
        '''
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

        # 1) Run traced command
        run_out = self.container.send_command(trace_cmd).output
        self.last_std = run_out

        # 2) Read traced file (host-side path matches your CoverageExtractor convention)
        host_path = f"data/logs/{self.instance_id}"
        if os.path.exists(host_path):
            with open(host_path, "r", encoding="utf-8", errors="replace") as f:
                raw_content = f.read()
        else:
            print(f"Error! {host_path} not found!")
            raw_content = ""

        # 3) Parse raw Go output directly to Trace objects
        trace_list = self._parse_go_panic_output(raw_content)
        
        # 4) Filter and merge
        log=f"{self.instance_id}\n"
        log+=f"{len(trace_list)} "
        #trace_list = [trace for trace in trace_list if self._get_effective_trace(trace)]
        #log+=f"{len(trace_list)} "
        #trace_list = [trace for trace in trace_list if trace["error"] in self.last_std]
        #log+=f"{len(trace_list)} "
        trace_list = self._merge(trace_list)
        log+=f"{len(trace_list)} "
        #trace_list = self._filter_error_at_test_point(trace_list)
        #log+=f"{len(trace_list)} \n"
        #for i in range(len(trace_list)):
            #log+=f"""{len(trace_list[i]["frames"])} {trace_list[i]["error_content"]}\n"""
            #log+=str([f[0] for f in trace_list[i]["frames"]])+"\n"
        print(log, "\n", json.dumps(trace_list, indent=True), flush=True)

        return trace_list
