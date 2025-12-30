import ast
import pathlib
import subprocess
from typing import List, Tuple
import shlex
import re
from itertools import dropwhile, takewhile


REPO_ROOT = pathlib.Path("repos")


class Extractor:
    @staticmethod
    def _get_node_start_with_decorators(node: ast.AST) -> int:
        """
        Get the starting line number of a node including all its decorators.
        For multi-line decorators, AST provides the correct starting line.
        """
        if hasattr(node, 'decorator_list') and node.decorator_list:
            # Return the line number of the first decorator
            return min(d.lineno for d in node.decorator_list)
        return node.lineno
    
    @staticmethod
    def _clone_if_needed(repo_id: str) -> pathlib.Path:
        repo_name = repo_id.split("/")[-1]
        repo_path = REPO_ROOT / repo_name
        if not repo_path.exists():
            url = f"https://github.com/{repo_id}.git"
            subprocess.run(
                ["git", "clone", "--quiet", "--depth", "1", url, str(repo_path)],
                check=True
            )
        return repo_path
    
    @staticmethod
    def _checkout_commit(repo_path: pathlib.Path, commit: str) -> None:
        subprocess.run(["git", "fetch", "--quiet", "origin", commit],
                    cwd=repo_path, check=True)
        subprocess.run(["git", "checkout", "--quiet", commit],
                    cwd=repo_path, check=True)

    GIT_APPLY_CMDS = [
        "git apply -v",                     # strict
        "git apply -v --reject",            # leave *.rej if partial
        "patch --batch --fuzz=5 -p1 -i",    # GNU patch, fuzzy match
    ]
    
    @staticmethod
    def _apply_patch(repo_path: pathlib.Path, patch: str) -> None:
        """
        Try to apply `patch` with the same 3-stage strategy the SWE-bench
        harness uses.  Raises CalledProcessError only if **all** strategies
        fail.
        """
        if not patch.strip():
            return

        tmp_name = ".tmp_swebench.patch"
        patch_file = repo_path / ".tmp_swebench.patch"
        patch_file.write_text(patch, encoding="utf-8")

        last_exc = None
        for cmd in Extractor.GIT_APPLY_CMDS:
            try:
                subprocess.run(
                    shlex.split(f"{cmd} {tmp_name}"),
                    cwd=repo_path,
                    check=True,
                    capture_output=True,
                    text=True,
                )
                patch_file.unlink(missing_ok=True)
                return                       # ✅ applied
            except subprocess.CalledProcessError as exc:
                last_exc = exc               # remember why it failed
                continue                     # try next strategy

        # All strategies failed → re-raise with context
        stderr = (last_exc.stderr or "").strip()
        raise RuntimeError(
            f"❌ Failed to apply patch after {len(Extractor.GIT_APPLY_CMDS)} attempts:\n{stderr}"
        ) from last_exc


    @staticmethod
    def _parse_from_test_patch(test_patch: str, func_name: str) -> str:
            patch_lines = test_patch.splitlines()
            start_iter = dropwhile(
                lambda l: not re.search(rf'\bdef\s+{re.escape(func_name)}\b', l), patch_lines
            )
            snippet = list(
                takewhile(lambda l: not l.startswith('diff --git'), start_iter)
            )
            if snippet:
                return "\n".join(l for l in snippet)
            else:
                return ""

    @staticmethod
    def _wipe_worktree(repo_path: pathlib.Path) -> None:
        """Discard uncommitted changes and untracked files."""
        subprocess.run(["git", "reset", "--hard", "HEAD"], cwd=repo_path,
                    check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["git", "clean", "-fd"], cwd=repo_path,
                    check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    
    @staticmethod
    def _get_class(source: str, class_name: str, add_lino: bool = True) -> str:
        """
        Extract a class with its decorators, header, content, and related dependencies.
        
        Parameters
        ----------
        source : str
            The source code containing the class
        class_name : str
            The name of the class to extract
        add_lino : bool
            Whether to add line numbers at the front of each line
        
        Returns
        -------
        str
            The extracted class content with dependencies
        """
        if not source:
            return ""
        if not class_name:
            print(f"Warning! No class_name specified! Return the whole file content!")
            if add_lino:
                lines = source.splitlines()
                return "\n".join(f"{i+1}|{line}" for i, line in enumerate(lines))
            return source
        
        lines = source.splitlines()
        
        try:
            module = ast.parse(source)
        except Exception as e:
            print(f"Cannot parse source: {e}, returning empty string...")
            return ""
        
        # ── 1. Find the target class ──────────────────────────────────────
        target_class = None
        for node in module.body:
            if isinstance(node, ast.ClassDef) and node.name == class_name:
                target_class = node
                break
        
        if target_class is None:
            print(f"Class {class_name} not found in source")
            return ""
        
        # ── 2. Collect all referenced names from the class ────────────────
        referenced_names = set()
        for node in ast.walk(target_class):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                referenced_names.add(node.id)
            elif isinstance(node, ast.Attribute):
                referenced_names.add(node.attr)
            elif isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    referenced_names.add(node.func.id)
                elif isinstance(node.func, ast.Attribute):
                    if isinstance(node.func.value, ast.Name) and node.func.value.id not in {"self", "cls"}:
                        referenced_names.add(node.func.value.id)
        
        # ── 3. Build content parts ────────────────────────────────────────
        content_parts = []
        
        # Always include imports
        for node in module.body:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                content_parts.append((node.lineno, (node.end_lineno or node.lineno)))
        
        # Include the target class with decorators
        class_start = Extractor._get_node_start_with_decorators(target_class)
        class_end = target_class.end_lineno or target_class.lineno
        content_parts.append((class_start, class_end))
        
        # Include top-level functions and variables referenced by the class
        for node in module.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in referenced_names:
                helper_start = Extractor._get_node_start_with_decorators(node)
                helper_end = node.end_lineno or node.lineno
                content_parts.append((helper_start, helper_end))
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id in referenced_names:
                        var_start = node.lineno
                        var_end = node.end_lineno or node.lineno
                        content_parts.append((var_start, var_end))
                        break
            elif isinstance(node, ast.AnnAssign):
                if isinstance(node.target, ast.Name) and node.target.id in referenced_names:
                    var_start = node.lineno
                    var_end = node.end_lineno or node.lineno
                    content_parts.append((var_start, var_end))
        
        # ── 4. Merge intervals and generate final content ─────────────────
        content_parts = Extractor._merge_intervals(content_parts)
        content = []
        for interval in content_parts:
            if add_lino:
                content.append("\n".join([f"{i}|{lines[i - 1]}" for i in range(interval[0], interval[1] + 1)]))
            else:
                content.append("\n".join([lines[i - 1] for i in range(interval[0], interval[1] + 1)]))
        
        return "\n......\n".join(content)
    
    @staticmethod
    def _extract_test_context(source: str, selector: str, add_lino: bool = True) -> str:
        """
        Trim `source` so that it keeps

        • all import statements
        • the selected test function (sync or async)
        • its containing class (if any)
        • any top-level helpers / fixtures referenced by that test
        • global variables, class data members and class methods used by the test
        • class header if the test method is in a class
        • line numbers at the front of each line
        """
        if not source:
            return ""
        if not selector:
            print(f"{source} : Warning! No selector specified! Return the whole file content!")
            if add_lino:
                lines = source.splitlines()
                return "\n".join(f"{i+1}|{line}" for i, line in enumerate(lines))
            return source
        
        if len(selector.split("::")) >= 2 and selector.split("::")[-1] == "*":
            return Extractor._get_class(source, selector.split("::")[0], add_lino)
        
        lines = source.splitlines()
        
        try:
            module = ast.parse(source)
        except Exception as e:
            print(e)
            print(f"{source} : {selector} : Cannot parse, returning with line numbers...")
            # Fallback to simple line-based extraction with line numbers
            if add_lino:
                return "\n".join(f"{i+1}|{line}" for i, line in enumerate(lines))
            return source

        # ── 1. split selector ──────────────────────────────────────────────
        parts = [p.split("[", 1)[0] for p in selector.split("::")]
        func_name = parts[-1]                 # final component = test function
        class_chain = parts[:-1]              # zero-to-many enclosing classes

        # ── 2. locate target function robustly ────────────────────────────
        target_func = None
        container_cls = None

        def walk(node, todo, parents):
            nonlocal target_func, container_cls
            if not todo:
                return
            head, *rest = todo
            for child in getattr(node, "body", []):
                if isinstance(child, ast.ClassDef) and child.name == head:
                    if rest:                             # need to go deeper
                        walk(child, rest, parents + [child])
                    else:                                # selector ended on class?
                        return
                elif (not rest and isinstance(child, (ast.FunctionDef,
                                                    ast.AsyncFunctionDef))
                    and child.name == head):
                    target_func = child
                    container_cls = parents[-1] if parents else None
                    return
            # keep searching siblings if not found
            for child in getattr(node, "body", []):
                if isinstance(child, ast.ClassDef):
                    walk(child, todo, parents + [child])

        walk(module, parts, [])

        if target_func is None:
            # fallback: any function/async func with matching name anywhere
            for n in ast.walk(module):
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == func_name:
                    target_func = n
                    # Find container class if any by checking line ranges
                    for cls in ast.walk(module):
                        if isinstance(cls, ast.ClassDef):
                            cls_start = cls.lineno
                            cls_end = cls.end_lineno or cls.lineno
                            func_line = n.lineno
                            if cls_start <= func_line <= cls_end:
                                container_cls = cls
                                break
                    break

        if target_func is None:
            # ── Fallback-1: slice function by indentation ────────────────────
            pattern = re.compile(rf'^([ \t]*)((async\s+)?def)\s+{re.escape(func_name)}\b')

            match = None
            for idx, line in enumerate(lines):
                if pattern.match(line):
                    match = idx
                    indent = len(pattern.match(line).group(1).expandtabs(4))
                    break

            if match is not None:
                # include decorators immediately above (fallback for unparseable code)
                start = match
                while start > 0 and lines[start - 1].lstrip().startswith("@"):
                    start -= 1
                start = max(0, start - 3)            # three lines of context

                end = len(lines) - 1
                for j in range(match + 1, len(lines)):
                    l = lines[j]
                    if l.strip() and (len(l) - len(l.lstrip())) <= indent:
                        end = j - 1
                        break
                
                # Add line numbers if requested
                snippet_lines = lines[start:end + 1]
                if add_lino:
                    snippet = "\n".join(f"{i+start+1}|{line}" for i, line in enumerate(snippet_lines))
                else:
                    snippet = "\n".join(snippet_lines)
                return snippet

        if target_func is None:
            # give up
            return ""

        # ── 3. Collect all referenced names from the target function ──────
        referenced_names = set()
        for n in ast.walk(target_func):
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load):
                referenced_names.add(n.id)
            elif isinstance(n, ast.Attribute):
                referenced_names.add(n.attr)
            elif isinstance(n, ast.Call):
                # Collect function names from calls
                if isinstance(n.func, ast.Name):
                    referenced_names.add(n.func.id)
                elif isinstance(n.func, ast.Attribute):
                    if isinstance(n.func.value, ast.Name) and n.func.value.id in {"self", "cls"}:
                        referenced_names.add(n.func.attr)

        # ── 4. Build content parts following get_content method approach ──
        content_parts = []
        
        # Always include imports
        for node in module.body:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                content_parts.append((node.lineno, (node.end_lineno or node.lineno) + 1))

        # Include the target function with decorators
        func_start = Extractor._get_node_start_with_decorators(target_func)
        func_end = target_func.end_lineno or target_func.lineno
        content_parts.append((func_start, func_end))

        # If target function is in a class, include class header
        if container_cls is not None:
            class_start = Extractor._get_node_start_with_decorators(container_cls)
            content_parts.append((class_start, container_cls.lineno))
            
            # Include class members referenced by the test function
            for node in container_cls.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in referenced_names:
                    method_start = Extractor._get_node_start_with_decorators(node)
                    method_end = node.end_lineno or node.lineno
                    content_parts.append((method_start, method_end))
                elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                    # Check if any target names are referenced
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                    if any(isinstance(t, ast.Name) and t.id in referenced_names for t in targets):
                        attr_start = node.lineno
                        attr_end = node.end_lineno or node.lineno
                        content_parts.append((attr_start, attr_end))

        # Include top-level functions and variables referenced by the test
        for node in module.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in referenced_names:
                # Skip if it's the target function (already included)
                if node == target_func:
                    continue
                helper_start = Extractor._get_node_start_with_decorators(node)
                helper_end = node.end_lineno or node.lineno
                content_parts.append((helper_start, helper_end))
            elif isinstance(node, ast.Assign):
                # Check if any target names are referenced
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id in referenced_names:
                        var_start = node.lineno
                        var_end = node.end_lineno or node.lineno
                        content_parts.append((var_start, var_end))
                        break
            elif isinstance(node, ast.AnnAssign):
                # Check if annotated assignment target is referenced
                if isinstance(node.target, ast.Name) and node.target.id in referenced_names:
                    var_start = node.lineno
                    var_end = node.end_lineno or node.lineno
                    content_parts.append((var_start, var_end))
            # Include fixtures (pytest decorators)
            elif isinstance(node, ast.FunctionDef) and any(
                (isinstance(d, ast.Name) and d.id == "fixture") or
                (isinstance(d, ast.Attribute) and d.attr == "fixture")
                for d in node.decorator_list
            ):
                fixture_start = Extractor._get_node_start_with_decorators(node)
                fixture_end = node.end_lineno or node.lineno
                content_parts.append((fixture_start, fixture_end))

        # ── 5. Merge intervals and generate final content ──────────────────
        content_parts = Extractor._merge_intervals(content_parts)
        content = []
        for interval in content_parts:
            if add_lino:
                content.append("\n".join([f"{i}|{lines[i - 1]}" for i in range(interval[0], interval[1] + 1)]))
            else:
                content.append("\n".join([lines[i - 1] for i in range(interval[0], interval[1] + 1)]))
        
        return "\n......\n".join(content)

    @staticmethod
    def _parse_node_id(nid: str) -> tuple[str, str]:
        """Return (relative_file_path, selector) for both pytest and unittest ids."""
        if "::" in nid:                           # pytest-style
            file_part, selector = nid.split("::", 1)
            return file_part, selector
        if nid.count("/") >=2 :
            return nid, ""

        comps = nid.split(".")
        if comps[-2] == comps[-1]:
            comps = comps[:-1]
        mods = comps[:-2]
        cls = comps[-2]
        func = comps[-1]
        path_parts = "/".join(mods)
        file_part = pathlib.Path(path_parts).with_suffix(".py").as_posix()
        selector  = f"{cls}::{func}"
        return file_part, selector

    @staticmethod
    def _get_module_structure(module: ast.Module) -> dict[int, tuple[ast.AST, str, ast.AST]]:
        node_map = {}  # line_num -> (node, node_type, container_class)
        
        for node in ast.walk(module):
            if isinstance(node, ast.ClassDef):
                for child in node.body:
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        for line_num in range(child.lineno, (child.end_lineno or child.lineno) + 1):
                            node_map[line_num] = (child, "method", node)
                    elif isinstance(child, (ast.Assign, ast.AnnAssign)):
                        for line_num in range(child.lineno, (child.end_lineno or child.lineno) + 1):
                            node_map[line_num] = (child, "class_attr", node)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                # Top-level function
                if hasattr(node, "lineno"):
                    for line_num in range(node.lineno, (node.end_lineno or node.lineno) + 1):
                        if line_num not in node_map:
                            node_map[line_num] = (node, "function", None)
        return node_map
    
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

    @staticmethod
    def _merge_same_content(mapping: dict[str, str]) -> dict[str, str]:
        '''input: (path , content)'''
        reverse_mapping = {}
        #flag = False
        for k, v in mapping.items():
            if v in reverse_mapping.keys():
                reverse_mapping[v] += f"; {k}"
            else:
                for old_content in reverse_mapping.keys():
                    if v in old_content:
                        reverse_mapping[old_content] += f"; {k}"
                        break
                    elif old_content in v:
                        reverse_mapping[v] = reverse_mapping[old_content] + f"; {k}"
                        del reverse_mapping[old_content]
                        break
                else:
                    reverse_mapping[v] = k
        #        flag = True
        
        results = {v : k for k, v in reverse_mapping.items()}
        #if flag:
        #    print("\n\n\n".join([f"{k}:\n{v}" for k, v in results.items()]))
        return results

    # ---------- public API --------------------------------------------------------

    @staticmethod
    def get_testcase(instance_id: str,
                    repo_id: str,
                    base_commit: str,
                    test_case_path: List[str],
                    test_patch: str,
                    add_lino: bool = True) -> dict[str, str]:
        """
        Parameters
        ----------
        repo_id          e.g. "pandas-dev/pandas"
        base_commit      SHA before the PR
        test_case_path   list[node-id] like ".../test_foo.py::Class::test_bar"
        test_patch       patch string that introduces / tweaks tests
        add_lino         whether to add line numbers at the front of each line
        """
        repo_path = Extractor._clone_if_needed(repo_id)
        Extractor._wipe_worktree(repo_path)
        Extractor._checkout_commit(repo_path, base_commit)
        Extractor._apply_patch(repo_path, test_patch)           # <- now tests exist

        results = {}
        for testcase in test_case_path:
            # testcase has formats:
            # a/b/c/d.py::class:function
            # a/b/c/d.py:function
            # a/b/c/d.py::class:function[params]
            # a/b/c/d.py:function[params]
            # a.b.c.file.class.function  where file does not have .py suffix
            # a.b.c.file.function
            # Note a may not be the folder in the root dir, a may be the sub folder under some tests/ src/ folders...
            file_part, selector = Extractor._parse_node_id(testcase)
            cand1 = repo_path / file_part
            cand2 = repo_path / "tests" / file_part
            cand3 = repo_path / "src" / file_part
            cand4 = repo_path / file_part.lstrip("tests/")  # repo root fallback
            abs_path = next((p for p in (cand1, cand2, cand3, cand4) if p.exists()),
                            None) 
            if abs_path is None:
                raise ValueError(f"\033[91mWarning! {instance_id} -- {testcase} -- {file_part} -- {cand1} path not found!\033[0m")
            with open(abs_path, encoding="utf-8") as f:
                src = f.read()
            results[testcase] = Extractor._extract_test_context(src, selector, add_lino)
            if not results[testcase]:
                raise ValueError(f"\033[91mWarning! {instance_id} -- {testcase} func content not found!\033[0m")

        Extractor._wipe_worktree(repo_path)
        results = Extractor._merge_same_content(results)
        return results

    @staticmethod
    def _get_func(path,
                source, 
                line_ranges: list[tuple[int]], 
                add_lino: bool) -> dict[str, str]:
        results: dict[str, str] = {}
        lines = source.splitlines()
        try:
            module: ast.Module = ast.parse(source)
        except Exception as e:
            # If parsing fails, fall back to line-based extraction
            print(f"Parsing {path} failed, falling back to line based extraction...")
            for line_range in sorted(line_ranges):
                start, end = line_range
                start_idx = max(1, start - 20)  # 1-indexed
                end_idx = min(len(lines), end + 20)
                content_lines = lines[start_idx - 1 : end_idx]
                if add_lino:
                    content = "\n".join(f"{i+start_idx}|{line}" for i, line in enumerate(content_lines))
                else:
                    content = "\n".join(content_lines)
                results[f"{path}::line_number{line_range}"] = content
            return results
        
        # Build a map of line numbers to AST nodes
        node_map: dict[int, tuple[ast.AST, str, ast.AST]] = Extractor._get_module_structure(module)
        
        for line_range in sorted(line_ranges):
            start_line, end_line = line_range
            
            # Find which nodes overlap with this range
            overlapping_nodes = set() 
            # Here overlapping nodes should be line number in diff, not range in list
            outside_parts = []
            cur_outside_range = None
            for line_num in range(start_line, end_line + 1):
                if line_num in node_map:
                    if cur_outside_range is not None:
                        outside_parts.append(cur_outside_range)
                        cur_outside_range = None
                    overlapping_nodes.add(node_map[line_num])
                else:
                    if cur_outside_range is None:
                        cur_outside_range = [line_num, line_num]
                    else:
                        cur_outside_range[1] = line_num
            if cur_outside_range is not None:
                outside_parts.append(cur_outside_range)
                cur_outside_range = None
            
            content_parts = []
            for out_part in outside_parts:
                # Not in a function/class - get range
                context_start = max(1, out_part[0] - 20)
                context_end = min(len(lines), out_part[1] + 20)
                content_parts.append((context_start, context_end))

            # Extract content for each overlapping node
            processed_nodes = set()
            
            for node_info in overlapping_nodes:
                node, node_type, container_class = node_info
                if id(node) in processed_nodes:
                    continue
                processed_nodes.add(id(node))
                
                if node_type == "function":
                    # Extract whole function
                    func_start = Extractor._get_node_start_with_decorators(node)
                    func_end = (node.end_lineno or node.lineno)
                    content_parts.append((func_start,func_end))

                elif node_type == "method":
                    # Extract class header + method
                    class_start = Extractor._get_node_start_with_decorators(container_class)
                    content_parts.append((class_start, container_class.lineno))
                    
                    method_start = Extractor._get_node_start_with_decorators(node)
                    method_end = (node.end_lineno or node.lineno)
                    content_parts.append((method_start, method_end))
                
                elif node_type == "class_attr":
                    # Extract class header + attribute
                    class_start = Extractor._get_node_start_with_decorators(container_class)
                    content_parts.append((class_start, container_class.lineno))
                    
                    attr_start = node.lineno
                    attr_end = (node.end_lineno or node.lineno)
                    content_parts.append((attr_start, attr_end))
            
            content_parts = Extractor._merge_intervals(content_parts)
            content = []
            for interval in content_parts:
                if add_lino:
                    content.append("\n".join( [f"{i}|{lines[i - 1]}" for i in range(interval[0], interval[1] + 1)]))
                else:
                    content.append("\n".join( [f"{lines[i - 1]}" for i in range(interval[0], interval[1] + 1)]))
            results[f"{path} :: line number range {line_range}"] = "\n......\n".join(content)
        return results

    @staticmethod
    def get_content(repo_id: str,
                    base_commit: str,
                    location: dict[str, list[tuple[int]]],
                    add_lino: bool = True) -> dict[str, str]:

        repo_path = Extractor._clone_if_needed(repo_id)
        Extractor._wipe_worktree(repo_path)
        Extractor._checkout_commit(repo_path, base_commit)

        results: dict[str, str] = {}
        for path in location.keys():
            abs_path = repo_path / path
            if not abs_path.exists():
                raise ValueError(f"Please check the path {abs_path}")
            
            with open(abs_path, encoding="utf-8") as f:
                source = f.read()
            
            suffix = abs_path.suffix
            if (suffix not in [".py", ".rst", ".yaml", ".toml", ".md", ".ipynb", ".yml", ".lock", ".pyi", ".json", ".feature", ".ts", ".js", ".tsx", ".html", ".pot", ".sql"]) and ("Dockerfile" not in str(abs_path)):
                print(f"Unknown file type: {abs_path}, content:")
                print(source)

            results.update(Extractor._get_func(abs_path, source, location[path], add_lino))

        results = Extractor._merge_same_content(results)
        return results



