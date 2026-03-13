import re
import shlex
import os
import json
import time
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


from src.utils.pyt.cov import Stack, Frame

class StackTraceExtractorByInjection:
    def __init__(self, instance_id: str, container: SetupRuntime, patch_to_apply: str, cmds: list[str]):
        self.instance_id = instance_id
        self.container: SetupRuntime = container
        # Setup repo/runtime, then apply patch (same pattern as CoverageExtractor)
        for sing_cmd in cmds:
            out = self.container.send_command(sing_cmd).output
        if patch_to_apply.strip():
            out = self.container.apply_patch(patch_to_apply)
        self.last_std = ""

    def inject_stack_collector_into_source(self, locs: dict[str, list[int]]) -> None:
        '''
        input: mapping of file_path to line number range
        Inject a trace collector into each location to print stack trace when the code is executed to that location
        print stack trace to /mnt/instance_id.txt with each line in the format of file_path lineno func_name line_content
        the first item the most outside frame, the last item the most inner frame (at the injected location)
        should not interrupt normal code execution
        '''
        out_path = f"/mnt/{self.instance_id}.txt"
        self.container.send_command(f"rm -f {out_path}")

        injector_go = r'''
package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"go/ast"
	"go/format"
	"go/parser"
	"go/token"
	"os"
	"path/filepath"
	"sort"
	"strings"
)

type LocMap map[string][]int

func norm(p string) string {
	p = strings.TrimSpace(strings.ReplaceAll(p, "\\", "/"))
	p = strings.TrimPrefix(p, "/testbed/")
	p = strings.TrimPrefix(p, "/app/")
	p = strings.TrimPrefix(p, "./")
	p = strings.TrimPrefix(p, "/")
	return p
}

func helperSource(pkg, outPath string) string {
	return fmt.Sprintf(`package %s

import (
	"encoding/json"
	"fmt"
	"os"
	"runtime"
	"strings"
	"sync"
)

var __ablationMu sync.Mutex
var __ablationSeen = map[string]struct{}{}
var __ablationFileCache = map[string][]string{}
var __ablationOut *os.File

func __ablationOpen() *os.File {
	if __ablationOut != nil {
		return __ablationOut
	}
	f, err := os.OpenFile(%q, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0666)
	if err != nil {
		return nil
	}
	__ablationOut = f
	return __ablationOut
}

func __ablationGetLines(path string) []string {
	if lines, ok := __ablationFileCache[path]; ok {
		return lines
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		__ablationFileCache[path] = nil
		return nil
	}
	text := strings.ReplaceAll(string(raw), "\r\n", "\n")
	lines := strings.Split(text, "\n")
	__ablationFileCache[path] = lines
	return lines
}

func __ablationLineContent(path string, line int) string {
	if !strings.Contains(path, "/app/") || line <= 0 {
		return ""
	}
	lines := __ablationGetLines(path)
	if len(lines) == 0 || line > len(lines) {
		return ""
	}
	if strings.Contains(strings.ToLower(path), "test") {
		return lines[line-1]
	}
	start := line - 15
	if start < 1 {
		start = 1
	}
	end := line + 15
	if end > len(lines) {
		end = len(lines)
	}
	chunk := make([]string, 0, end-start+1)
	for ln := start; ln <= end; ln++ {
		chunk = append(chunk, fmt.Sprintf("L%%d: %%s", ln, lines[ln-1]))
	}
	return strings.Join(chunk, "\n")
}

func __ablationCollect() [][]interface{} {
	pcs := make([]uintptr, 128)
	n := runtime.Callers(2, pcs)
	frames := runtime.CallersFrames(pcs[:n])
	res := make([][]interface{}, 0, n)
	for {
		fr, more := frames.Next()
		if fr.File != "" {
			res = append(res, []interface{}{fr.File, fr.Line, fr.Function, __ablationLineContent(fr.File, fr.Line)})
		}
		if !more {
			break
		}
	}
	for i, j := 0, len(res)-1; i < j; i, j = i+1, j-1 {
		res[i], res[j] = res[j], res[i]
	}
	return res
}

func __ablationTraceHit(file string, line int) {
	key := fmt.Sprintf("%%s:%%d", file, line)
	__ablationMu.Lock()
	defer __ablationMu.Unlock()
	if _, ok := __ablationSeen[key]; ok {
		return
	}
	__ablationSeen[key] = struct{}{}
	f := __ablationOpen()
	if f == nil {
		return
	}
	_, _ = f.WriteString("TRACE_STACK_BEGIN\n")
	for _, item := range __ablationCollect() {
		b, err := json.Marshal(item)
		if err != nil {
			continue
		}
		_, _ = f.WriteString("FRAME|" + string(b) + "\n")
	}
	_, _ = f.WriteString("TRACE_STACK_END\n")
	_ = f.Sync()
}
`, pkg, outPath)
}

func callStmt(filePath string, line int) ast.Stmt {
	return &ast.ExprStmt{
		X: &ast.CallExpr{
			Fun: ast.NewIdent("__ablationTraceHit"),
			Args: []ast.Expr{
				&ast.BasicLit{Kind: token.STRING, Value: fmt.Sprintf("%q", filePath)},
				&ast.BasicLit{Kind: token.INT, Value: fmt.Sprintf("%d", line)},
			},
		},
	}
}

func main() {
	if len(os.Args) != 4 {
		panic("usage: injector <repo_root> <locs_json_path> <out_path>")
	}
	repoRoot := os.Args[1]
	locPath := os.Args[2]
	outPath := os.Args[3]

	raw, err := os.ReadFile(locPath)
	if err != nil {
		panic(err)
	}
	var locs LocMap
	if err := json.Unmarshal(raw, &locs); err != nil {
		panic(err)
	}

	byFile := map[string]map[int]struct{}{}
	for k, lines := range locs {
		nk := norm(k)
		if nk == "" || !strings.HasSuffix(nk, ".go") {
			continue
		}
		if _, ok := byFile[nk]; !ok {
			byFile[nk] = map[int]struct{}{}
		}
		for _, ln := range lines {
			if ln > 0 {
				byFile[nk][ln] = struct{}{}
			}
		}
	}

	dirsToPkg := map[string]string{}
	insertCnt := 0
	for relPath, lineSet := range byFile {
		absPath := filepath.Join(repoRoot, relPath)
		if _, err := os.Stat(absPath); err != nil {
			continue
		}

		fset := token.NewFileSet()
		node, err := parser.ParseFile(fset, absPath, nil, parser.ParseComments)
		if err != nil {
			continue
		}

		targets := make([]int, 0, len(lineSet))
		for ln := range lineSet {
			targets = append(targets, ln)
		}
		sort.Ints(targets)
		if len(targets) == 0 {
			continue
		}

		used := map[int]struct{}{}
		insertedAny := false
		ast.Inspect(node, func(n ast.Node) bool {
			blk, ok := n.(*ast.BlockStmt)
			if !ok || len(blk.List) == 0 {
				return true
			}
			newList := make([]ast.Stmt, 0, len(blk.List)*2)
			changed := false
			for _, stmt := range blk.List {
				start := fset.Position(stmt.Pos()).Line
				end := fset.Position(stmt.End()).Line
				for _, t := range targets {
					if t < start || t > end {
						continue
					}
					if _, ok := used[t]; ok {
						continue
					}
					newList = append(newList, callStmt("/app/"+relPath, t))
					used[t] = struct{}{}
					changed = true
					insertCnt++
				}
				newList = append(newList, stmt)
			}
			if changed {
				blk.List = newList
				insertedAny = true
			}
			return true
		})

		if !insertedAny {
			continue
		}

		var buf bytes.Buffer
		if err := format.Node(&buf, fset, node); err != nil {
			continue
		}
		if err := os.WriteFile(absPath, buf.Bytes(), 0o644); err != nil {
			continue
		}

		dir := filepath.Dir(absPath)
		if _, ok := dirsToPkg[dir]; !ok {
			dirsToPkg[dir] = node.Name.Name
		}
	}

	for dir, pkg := range dirsToPkg {
		helperPath := filepath.Join(dir, "zz_ablation_trace_inject_gen.go")
		src := helperSource(pkg, outPath)
		_ = os.WriteFile(helperPath, []byte(src), 0o644)
	}
	fmt.Printf("INJECTOR_OK files=%d calls=%d\n", len(dirsToPkg), insertCnt)
}
'''
        self.container.write_file(
            injector_go, "/tmp/ablation_go_injector.go"
        )
        self.container.write_file(
            json.dumps(locs), "/tmp/ablation_go_locs.json"
        )
        cmd_res = self.container.send_command(
            f"go run /tmp/ablation_go_injector.go /app /tmp/ablation_go_locs.json {out_path}"
        )
        out = cmd_res.output
        self.last_std = out
        #print(out, flush=True)
        if "INJECTOR_OK" not in out:
            raise RuntimeError(f"go source injector failed:\n{out}")
        return None

    def parse_trace(self, log: str) -> list[Stack]:
        '''
        the first item the most outside frame, the last item the most inner frame (at the injected location)
        '''
        traces: list[Stack] = []
        current: Stack = []
        for raw in log.splitlines():
            line = raw.strip()
            if not line:
                continue
            if line == "TRACE_STACK_BEGIN":
                current = []
                continue
            if line == "TRACE_STACK_END":
                if current:
                    traces.append(current)
                current = []
                continue
            if not line.startswith("FRAME|"):
                continue
            payload = line[len("FRAME|"):]
            try:
                item = json.loads(payload)
            except json.JSONDecodeError:
                continue
            if (not isinstance(item, list)) or len(item) != 4:
                continue
            file_path, lineno, func_name, line_content = item
            if "ablation_trace_inject" in str(file_path):
                continue
            try:
                lineno = int(lineno)
            except (TypeError, ValueError):
                continue
            current.append((str(file_path), lineno, str(func_name), str(line_content)))
        return traces

    def merge_trace_by_func(self, traces: list[Stack]) -> list[Stack]:
        '''
        If
        Trace a: [A, B, C, D]
        Trace b: [A, B, C, D, E, F ...]
        where A, B, C ... is (file_path, func_name) (no need to consider line number)
        retain b and discard a.
        If 
        Trace a: [A, B, C, D]
        Trace b: [A, B, C, D]
        retain a.
        If any of the two frames to be compared does not have a function name, keep both a and b.
        '''
        def relation(a: Stack, b: Stack) -> str:
            """
            Returns one of:
              - "same": a and b have the same (file, func) sequence
              - "a_prefix_b": a is a strict prefix of b by (file, func)
              - "b_prefix_a": b is a strict prefix of a by (file, func)
              - "different": comparable but diverges
              - "incomparable": some compared frame misses function name
            """
            min_len = min(len(a), len(b))
            for i in range(min_len):
                a_file, _, a_func, _ = a[i]
                b_file, _, b_func, _ = b[i]
                if (not a_func) or (not b_func):
                    return "incomparable"
                if (a_file, a_func) != (b_file, b_func):
                    return "different"
            if len(a) == len(b):
                return "same"
            if len(a) < len(b):
                return "a_prefix_b"
            return "b_prefix_a"

        kept: dict[int, Stack] = {}
        for cur in traces:
            if not cur:
                continue

            discard_cur = False
            remove: list[int] = []
            for i, prev in kept.items():
                rel = relation(cur, prev)
                if rel == "same":
                    # Keep the first one seen (prev), discard cur.
                    discard_cur = True
                    break
                if rel == "a_prefix_b":
                    # cur is shorter and fully covered by prev -> discard cur.
                    discard_cur = True
                    break
                if rel == "b_prefix_a":
                    # prev is shorter and covered by cur -> remove prev.
                    remove.append(i)

            if discard_cur:
                continue

            for i in remove:
                del kept[i]
            key = max(kept.keys())+1 if len(kept.keys()) > 0 else 1
            kept[key] = cur

        # JSON serialization downstream expects concrete list objects.
        return list(kept.values())
    
    def cutoff_trace_by_loc(self, trace: Stack, locs: dict[str, list[int]]):
        res: Stack = []
        for frame in trace:
            norm_path = frame[0].replace("/testbed/", "").replace("/app/", "").strip("/")
            if frame[1] not in locs.get(norm_path, []):
                res.append(frame)
        if (not res):
            return res
        if "test" in res[-1][0].replace("/testbed/", "").replace("_pytest", ""):
            res = []
        return res

    def _norm_repo_path(self, p: str) -> str:
        return p.replace("\\", "/").replace("/testbed/", "").replace("/app/", "").lstrip("./").strip("/")


    def filter_stack(self, trace: Stack, locs: dict[str, list[int]]) -> bool:
        if not trace:
            return False
        paths = [self._norm_repo_path(i) for i in locs.keys()]
        norm = self._norm_repo_path(trace[-1][0])
        if (norm in paths) and ("test" not in norm):
            return True
        return False

    def _fallback_stacks_from_go_output(self, output: str, locs: dict[str, list[int]]) -> list[Stack]:
        """
        Build pseudo stacks from Go compiler/test error lines when runtime trace is empty.
        Format examples:
          path/file.go:123: message
          path/file.go:123:45: message
        """
        pattern = re.compile(r'(?m)^\s*([^\s:]+\.go):(\d+)(?::\d+)?:\s*(.+)$')
        loc_sets: dict[str, set[int]] = {self._norm_repo_path(k): set(v or []) for k, v in locs.items()}
        target_paths = set(loc_sets.keys())
        stacks: list[Stack] = []
        seen = set()

        for m in pattern.finditer(output or ""):
            raw_path = m.group(1).strip()
            line_no = int(m.group(2))
            msg = m.group(3).strip()
            file_path = raw_path if raw_path.startswith("/") else f"/app/{raw_path.lstrip('./')}"
            key = (file_path, line_no, msg)
            if key in seen:
                continue
            seen.add(key)
            norm = self._norm_repo_path(file_path)
            if norm not in target_paths:
                continue
            if not self._line_close_to_targets(line_no, loc_sets.get(norm, set()), tol=2):
                continue
            stacks.append([(file_path, line_no, "", msg)])

        return stacks
    
    def discard_stack_last_test(self, traces: list[Stack]) -> list[Stack]:
        res = []
        for trace in traces:
            if "test" not in trace[-1][0].replace("testbed" , "").replace("_pytest", ""):
                res.append(trace)
        return res
    
    def extract(self, test_cmd: str, locs: dict[str, list[int]]) -> list[Stack]:
        '''
        locs: file_name: (start_line, end_line)
        '''
        self.inject_stack_collector_into_source(locs)
        self.last_std = self.container.send_command(test_cmd).output
        with open(f"data/exec_logs/{self.instance_id}.txt", mode="a") as f:
            f.write(self.last_std)
            f.write("\n========================\n\n\n")
        time.sleep(16) # wait for file sys to sync
        host_path = f"data/logs/{self.instance_id}.txt"
        if os.path.exists(host_path):
            with open(host_path, encoding="utf-8", errors="replace") as f:
                log = f.read()
        else:
            #print(f"Error! {host_path} not found!")
            log = ""
        # self.container.send_command(f"rm -f /mnt/{self.instance_id}.txt")
        stacks: list[Stack] = self.parse_trace(log)
        merged_stacks: list[Stack] = self.merge_trace_by_func(stacks)
        if not merged_stacks:
            fallback = self._fallback_stacks_from_go_output(self.last_std, locs)
            merged_stacks = self.merge_trace_by_func(fallback)
        # print(self.instance_id, [len(i) for i in merged_stacks], flush=True)
        filtered_stacks = [s for s in merged_stacks if self.filter_stack(s, locs)]
        return filtered_stacks
