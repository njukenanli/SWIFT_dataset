import pathlib
import subprocess
from typing import List, Tuple, Optional
import shlex
import re

import tree_sitter_go as tsgo
from tree_sitter import Language, Parser, Node
from src.utils.runtime import SetupRuntime


REPO_ROOT = pathlib.Path("repos")

# Initialize Go parser
GO_LANGUAGE = Language(tsgo.language())
GO_PARSER = Parser(GO_LANGUAGE)


class Extractor:
    '''
    Go Lang implementation
    '''
    
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
    def _wipe_worktree(repo_path: pathlib.Path) -> None:
        """Discard uncommitted changes and untracked files."""
        subprocess.run(["git", "reset", "--hard", "HEAD"], cwd=repo_path,
                    check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["git", "clean", "-fd"], cwd=repo_path,
                    check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    
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
    
    @staticmethod
    def _extract_test_context(source: str, selector: str, add_lino: bool = True) -> str:
        """
        Extract Go test function with its context.
        
        Keeps:
        • package declaration
        • all import statements  
        • the selected test function (and its doc comments)
        • any top-level helpers / fixtures referenced by that test
        • global variables, constants, types used by the test
        • line numbers at the front of each line (if add_lino=True)
        
        Parameters
        ----------
        source : str
            Go source code
        selector : str
            Test name like "TestFoo" or "TestFoo/subtest_name"
        add_lino : bool
            Whether to add line numbers
            
        Returns
        -------
        str
            Extracted test context
        """
        if not source:
            return ""
        if not selector:
            print(f"Warning! No selector specified! Return the whole file content!")
            if add_lino:
                lines = source.splitlines()
                return "\n".join(f"{i+1}|{line}" for i, line in enumerate(lines))
            return source
        
        lines = source.splitlines()
        source_bytes = source.encode('utf-8')
        
        # Parse the source
        tree = GO_PARSER.parse(source_bytes)
        root = tree.root_node
        
        # Extract the base test function name (before any "/")
        # e.g., "TestFoo/subtest" -> "TestFoo"
        base_test_name = selector.split("/")[0]
        
        # ── 1. Find all top-level declarations ────────────────────────────
        package_node = None
        import_nodes = []
        func_nodes = {}       # name -> node
        type_nodes = {}       # name -> node
        var_nodes = {}        # name -> node (var declarations)
        const_nodes = {}      # name -> node (const declarations)
        
        for child in root.children:
            if child.type == "package_clause":
                package_node = child
            elif child.type == "import_declaration":
                import_nodes.append(child)
            elif child.type == "function_declaration":
                name_node = child.child_by_field_name("name")
                if name_node:
                    func_name = source_bytes[name_node.start_byte:name_node.end_byte].decode('utf-8')
                    func_nodes[func_name] = child
            elif child.type == "method_declaration":
                name_node = child.child_by_field_name("name")
                if name_node:
                    method_name = source_bytes[name_node.start_byte:name_node.end_byte].decode('utf-8')
                    func_nodes[method_name] = child
            elif child.type == "type_declaration":
                # type Foo struct { ... } or type Foo = Bar
                for spec in child.children:
                    if spec.type == "type_spec":
                        name_node = spec.child_by_field_name("name")
                        if name_node:
                            type_name = source_bytes[name_node.start_byte:name_node.end_byte].decode('utf-8')
                            type_nodes[type_name] = child
            elif child.type == "var_declaration":
                for spec in child.children:
                    if spec.type == "var_spec":
                        for name_child in spec.children:
                            if name_child.type == "identifier":
                                var_name = source_bytes[name_child.start_byte:name_child.end_byte].decode('utf-8')
                                var_nodes[var_name] = child
                                break
            elif child.type == "const_declaration":
                for spec in child.children:
                    if spec.type == "const_spec":
                        for name_child in spec.children:
                            if name_child.type == "identifier":
                                const_name = source_bytes[name_child.start_byte:name_child.end_byte].decode('utf-8')
                                const_nodes[const_name] = child
                                break
        
        # ── 2. Find the target test function ──────────────────────────────
        target_func = func_nodes.get(base_test_name)
        
        if target_func is None:
            # Fallback: search for any function containing the test name
            for name, node in func_nodes.items():
                if base_test_name in name or name in base_test_name:
                    target_func = node
                    break
        
        if target_func is None:
            # Last fallback: regex-based search
            pattern = re.compile(rf'^func\s+{re.escape(base_test_name)}\s*\(')
            for idx, line in enumerate(lines):
                if pattern.match(line):
                    # Find function end by brace matching
                    start_line = idx + 1
                    brace_count = 0
                    end_line = start_line
                    for j in range(idx, len(lines)):
                        brace_count += lines[j].count('{') - lines[j].count('}')
                        if brace_count == 0 and '{' in lines[j]:
                            end_line = j + 1
                            break
                        if brace_count == 0 and j > idx:
                            end_line = j + 1
                            break
                    end_line = max(end_line, len(lines))
                    
                    # Include comment above
                    while start_line > 1 and lines[start_line - 2].strip().startswith("//"):
                        start_line -= 1
                    
                    if add_lino:
                        return "\n".join(f"{i}|{lines[i-1]}" for i in range(start_line, end_line + 1))
                    return "\n".join(lines[start_line-1:end_line])
            
            print(f"Test function {base_test_name} not found in source")
            return ""
        
        # ── 3. Collect all referenced identifiers from the test function ──
        referenced_names = set()
        
        def collect_identifiers(node: Node):
            """Recursively collect all identifiers used in a node."""
            if node.type == "identifier":
                name = source_bytes[node.start_byte:node.end_byte].decode('utf-8')
                referenced_names.add(name)
            elif node.type == "type_identifier":
                name = source_bytes[node.start_byte:node.end_byte].decode('utf-8')
                referenced_names.add(name)
            elif node.type == "field_identifier":
                # Field access like foo.Bar - we want Bar
                name = source_bytes[node.start_byte:node.end_byte].decode('utf-8')
                referenced_names.add(name)
            for child in node.children:
                collect_identifiers(child)
        
        collect_identifiers(target_func)
        
        # ── 4. Build content parts (line ranges to include) ───────────────
        content_parts = []  # list of (start_line, end_line) 1-indexed
        
        def get_node_range_with_comments(node: Node) -> Tuple[int, int]:
            """Get line range including preceding comments."""
            start_line = node.start_point[0] + 1  # 0-indexed to 1-indexed
            end_line = node.end_point[0] + 1
            
            # Check for comments above
            while start_line > 1:
                prev_line = lines[start_line - 2].strip()
                if prev_line.startswith("//") or prev_line.startswith("/*"):
                    start_line -= 1
                elif prev_line.endswith("*/"):
                    # Multi-line comment - go back to find start
                    start_line -= 1
                    while start_line > 1 and "/*" not in lines[start_line - 2]:
                        start_line -= 1
                    if start_line > 1 and "/*" in lines[start_line - 2]:
                        start_line -= 1
                else:
                    break
            
            return (start_line, end_line)
        
        # Always include package declaration
        if package_node:
            content_parts.append(get_node_range_with_comments(package_node))
        
        # Always include imports
        for imp_node in import_nodes:
            content_parts.append(get_node_range_with_comments(imp_node))
        
        # Include the target test function
        content_parts.append(get_node_range_with_comments(target_func))
        
        # Include referenced functions
        for name, node in func_nodes.items():
            if name in referenced_names and name != base_test_name:
                content_parts.append(get_node_range_with_comments(node))
        
        # Include referenced types
        for name, node in type_nodes.items():
            if name in referenced_names:
                content_parts.append(get_node_range_with_comments(node))
        
        # Include referenced variables
        for name, node in var_nodes.items():
            if name in referenced_names:
                content_parts.append(get_node_range_with_comments(node))
        
        # Include referenced constants
        for name, node in const_nodes.items():
            if name in referenced_names:
                content_parts.append(get_node_range_with_comments(node))
        
        # ── 5. Merge intervals and generate output ────────────────────────
        content_parts = Extractor._merge_intervals(content_parts)
        content = []
        for interval in content_parts:
            if add_lino:
                content.append("\n".join([f"{i}|{lines[i - 1]}" for i in range(interval[0], interval[1] + 1)]))
            else:
                content.append("\n".join([lines[i - 1] for i in range(interval[0], interval[1] + 1)]))
        
        return "\n......\n".join(content)


    @staticmethod
    def _find_test_file(repo_path: pathlib.Path, test_name: str, test_patch: str) -> Optional[pathlib.Path]:
        """
        Find the Go test file containing the given test function.
        
        Strategy:
        1. Parse test_patch to find which files were modified
        2. Search for the test function in those files
        3. Fall back to searching all *_test.go files
        """
        base_test_name = test_name.split("/")[0]
        
        # Strategy 1: Parse the patch to find modified test files
        patch_files = []
        for line in test_patch.splitlines():
            if line.startswith("diff --git"):
                # Extract file path: "diff --git a/path/file.go b/path/file.go"
                parts = line.split()
                if len(parts) >= 4:
                    file_path = parts[2].lstrip("a/")
                    if file_path.endswith("_test.go"):
                        patch_files.append(file_path)
        
        # Check patch files for the test function
        for file_path in patch_files:
            abs_path = repo_path / file_path
            if abs_path.exists():
                with open(abs_path, encoding="utf-8") as f:
                    content = f.read()
                # Check if test function is in this file
                if re.search(rf'\bfunc\s+{re.escape(base_test_name)}\s*\(', content):
                    return abs_path
        
        # Strategy 2: Search all *_test.go files
        for test_file in repo_path.rglob("*_test.go"):
            # Skip vendor directory
            if "vendor" in test_file.parts:
                continue
            try:
                with open(test_file, encoding="utf-8") as f:
                    content = f.read()
                if re.search(rf'\bfunc\s+{re.escape(base_test_name)}\s*\(', content):
                    return test_file
            except Exception:
                continue
        
        return None

    @staticmethod
    def get_testcase(instance_id: str,
                    repo_id: str,
                    base_commit: str,
                    test_case_list: List[str],
                    test_patch: str,
                    add_lino: bool = True) -> dict[str, str]:
        """
        Parameters
        ----------
        repo_id          e.g. "golang/go"
        base_commit      SHA before the PR
        test_case_list   list[test-id] like ['TestUbuntuConvertToModel', 'TestUbuntuConvertToModel/subtest', ...]
                         Go test names can be:
                         - "TestFoo" (simple test)
                         - "TestFoo/subtest_name" (table-driven subtest)
        test_patch       patch string that introduces / tweaks tests
        add_lino         whether to add line numbers at the front of each line

        Returns:
        mapping of
        testcase_name : test function body with comments and referred vars/funcs/types in the same file.
        """
        repo_path = Extractor._clone_if_needed(repo_id)
        Extractor._wipe_worktree(repo_path)
        Extractor._checkout_commit(repo_path, base_commit)
        Extractor._apply_patch(repo_path, test_patch)           # <- now tests exist

        results = {}
        
        # Group test cases by their base test function
        # e.g., TestFoo/sub1, TestFoo/sub2 -> TestFoo
        processed_base_tests = set()
        
        for testcase in test_case_list:
            base_test_name = testcase.split("/")[0]
            
            # Skip if we already processed this base test
            if base_test_name in processed_base_tests:
                # Still add entry for subtest pointing to same content
                if base_test_name in results:
                    results[testcase] = results[base_test_name]
                continue
            
            # Find the test file containing this test
            test_file = Extractor._find_test_file(repo_path, testcase, test_patch)
            
            if test_file is None:
                print(f"Warning: Could not find test file for {testcase}")
                results[testcase] = ""
                continue
            
            with open(test_file, encoding="utf-8") as f:
                source = f.read()
            
            # Get relative path from repo root
            rel_path = test_file.relative_to(repo_path)
            
            extracted = Extractor._extract_test_context(source, testcase, add_lino)
            # Prepend file path as header
            if extracted:
                extracted = f"# {rel_path}\n{extracted}"
            results[testcase] = extracted
            processed_base_tests.add(base_test_name)
            
            if not extracted:
                print(f"Warning: Could not extract test context for {testcase}")

        Extractor._wipe_worktree(repo_path)
        results = Extractor._merge_same_content(results)
        return results

    @staticmethod
    def _get_func(path,
                source, 
                line_ranges: list[tuple[int]], 
                add_lino: bool) -> dict[str, str]:
        """
        Extract Go functions/methods overlapping with given line ranges,
        along with their dependencies (referenced types, vars, constants, other functions).
        
        Parameters
        ----------
        path : pathlib.Path or str
            The file path (for labeling results)
        source : str
            Go source code
        line_ranges : list[tuple[int]]
            List of (start_line, end_line) tuples (1-indexed)
        add_lino : bool
            Whether to add line numbers at the front of each line
            
        Returns
        -------
        dict[str, str]
            Mapping of "path :: line number range (start, end)" to extracted content
        """
        results: dict[str, str] = {}
        lines = source.splitlines()
        source_bytes = source.encode('utf-8')
        
        # Parse the source
        tree = GO_PARSER.parse(source_bytes)
        root = tree.root_node
        
        # ── 1. Find all top-level declarations ────────────────────────────
        package_node = None
        import_nodes = []
        func_nodes = {}       # name -> node
        type_nodes = {}       # name -> node
        var_nodes = {}        # name -> node (var declarations)
        const_nodes = {}      # name -> node (const declarations)
        
        # Map line numbers to nodes for overlap detection
        line_to_nodes = {}    # line_num -> list of (node, name, node_type)
        
        for child in root.children:
            if child.type == "package_clause":
                package_node = child
            elif child.type == "import_declaration":
                import_nodes.append(child)
            elif child.type == "function_declaration":
                name_node = child.child_by_field_name("name")
                if name_node:
                    func_name = source_bytes[name_node.start_byte:name_node.end_byte].decode('utf-8')
                    func_nodes[func_name] = child
                    # Register lines for this function
                    start_line = child.start_point[0] + 1
                    end_line = child.end_point[0] + 1
                    for ln in range(start_line, end_line + 1):
                        if ln not in line_to_nodes:
                            line_to_nodes[ln] = []
                        line_to_nodes[ln].append((child, func_name, "function"))
            elif child.type == "method_declaration":
                name_node = child.child_by_field_name("name")
                if name_node:
                    method_name = source_bytes[name_node.start_byte:name_node.end_byte].decode('utf-8')
                    func_nodes[method_name] = child
                    # Register lines for this method
                    start_line = child.start_point[0] + 1
                    end_line = child.end_point[0] + 1
                    for ln in range(start_line, end_line + 1):
                        if ln not in line_to_nodes:
                            line_to_nodes[ln] = []
                        line_to_nodes[ln].append((child, method_name, "method"))
            elif child.type == "type_declaration":
                for spec in child.children:
                    if spec.type == "type_spec":
                        name_node = spec.child_by_field_name("name")
                        if name_node:
                            type_name = source_bytes[name_node.start_byte:name_node.end_byte].decode('utf-8')
                            type_nodes[type_name] = child
                            # Register lines for this type
                            start_line = child.start_point[0] + 1
                            end_line = child.end_point[0] + 1
                            for ln in range(start_line, end_line + 1):
                                if ln not in line_to_nodes:
                                    line_to_nodes[ln] = []
                                line_to_nodes[ln].append((child, type_name, "type"))
            elif child.type == "var_declaration":
                for spec in child.children:
                    if spec.type == "var_spec":
                        for name_child in spec.children:
                            if name_child.type == "identifier":
                                var_name = source_bytes[name_child.start_byte:name_child.end_byte].decode('utf-8')
                                var_nodes[var_name] = child
                                # Register lines for this var
                                start_line = child.start_point[0] + 1
                                end_line = child.end_point[0] + 1
                                for ln in range(start_line, end_line + 1):
                                    if ln not in line_to_nodes:
                                        line_to_nodes[ln] = []
                                    line_to_nodes[ln].append((child, var_name, "var"))
                                break
            elif child.type == "const_declaration":
                for spec in child.children:
                    if spec.type == "const_spec":
                        for name_child in spec.children:
                            if name_child.type == "identifier":
                                const_name = source_bytes[name_child.start_byte:name_child.end_byte].decode('utf-8')
                                const_nodes[const_name] = child
                                # Register lines for this const
                                start_line = child.start_point[0] + 1
                                end_line = child.end_point[0] + 1
                                for ln in range(start_line, end_line + 1):
                                    if ln not in line_to_nodes:
                                        line_to_nodes[ln] = []
                                    line_to_nodes[ln].append((child, const_name, "const"))
                                break
        
        def get_node_range_with_comments(node: Node) -> Tuple[int, int]:
            """Get line range including preceding comments."""
            start_line = node.start_point[0] + 1
            end_line = node.end_point[0] + 1
            
            while start_line > 1:
                prev_line = lines[start_line - 2].strip()
                if prev_line.startswith("//") or prev_line.startswith("/*"):
                    start_line -= 1
                elif prev_line.endswith("*/"):
                    start_line -= 1
                    while start_line > 1 and "/*" not in lines[start_line - 2]:
                        start_line -= 1
                    if start_line > 1 and "/*" in lines[start_line - 2]:
                        start_line -= 1
                else:
                    break
            
            return (start_line, end_line)
        
        def collect_identifiers(node: Node) -> set:
            """Recursively collect all identifiers used in a node."""
            referenced = set()
            if node.type == "identifier":
                name = source_bytes[node.start_byte:node.end_byte].decode('utf-8')
                referenced.add(name)
            elif node.type == "type_identifier":
                name = source_bytes[node.start_byte:node.end_byte].decode('utf-8')
                referenced.add(name)
            elif node.type == "field_identifier":
                name = source_bytes[node.start_byte:node.end_byte].decode('utf-8')
                referenced.add(name)
            for child in node.children:
                referenced.update(collect_identifiers(child))
            return referenced
        
        # ── 2. Process each line range ────────────────────────────────────
        for line_range in sorted(line_ranges):
            start_line, end_line = line_range
            
            # Find overlapping nodes
            overlapping_nodes = set()  # (node, name, node_type)
            outside_parts = []
            cur_outside_range = None
            
            for line_num in range(start_line, end_line + 1):
                if line_num in line_to_nodes:
                    for node_info in line_to_nodes[line_num]:
                        overlapping_nodes.add(node_info)
                    if cur_outside_range is not None:
                        outside_parts.append(cur_outside_range)
                        cur_outside_range = None
                else:
                    if cur_outside_range is None:
                        cur_outside_range = (line_num, line_num)
                    else:
                        cur_outside_range = (cur_outside_range[0], line_num)
            
            if cur_outside_range is not None:
                outside_parts.append(cur_outside_range)
            
            # Build content parts
            content_parts = []
            
            # Include outside parts (lines not belonging to any node)
            for out_part in outside_parts:
                content_parts.append(out_part)
            
            # Always include package declaration
            if package_node:
                content_parts.append(get_node_range_with_comments(package_node))
            
            # Always include imports
            for imp_node in import_nodes:
                content_parts.append(get_node_range_with_comments(imp_node))
            
            # Collect all referenced names from overlapping nodes
            referenced_names = set()
            processed_nodes = set()
            
            for node, name, node_type in overlapping_nodes:
                if id(node) in processed_nodes:
                    continue
                processed_nodes.add(id(node))
                
                # Include this node
                content_parts.append(get_node_range_with_comments(node))
                
                # Collect references
                referenced_names.update(collect_identifiers(node))
            
            # Include referenced functions (not already included)
            for name, node in func_nodes.items():
                if name in referenced_names and id(node) not in processed_nodes:
                    content_parts.append(get_node_range_with_comments(node))
                    processed_nodes.add(id(node))
            
            # Include referenced types
            for name, node in type_nodes.items():
                if name in referenced_names and id(node) not in processed_nodes:
                    content_parts.append(get_node_range_with_comments(node))
                    processed_nodes.add(id(node))
            
            # Include referenced variables
            for name, node in var_nodes.items():
                if name in referenced_names and id(node) not in processed_nodes:
                    content_parts.append(get_node_range_with_comments(node))
                    processed_nodes.add(id(node))
            
            # Include referenced constants
            for name, node in const_nodes.items():
                if name in referenced_names and id(node) not in processed_nodes:
                    content_parts.append(get_node_range_with_comments(node))
                    processed_nodes.add(id(node))
            
            # Merge intervals and generate content
            content_parts = Extractor._merge_intervals(content_parts)
            content = []
            num_lines = len(lines)
            for interval in content_parts:
                # Clamp interval to valid line range (1-indexed)
                start_ln = max(1, interval[0])
                end_ln = min(num_lines, interval[1])
                if start_ln > end_ln:
                    continue  # Skip invalid intervals
                if add_lino:
                    content.append("\n".join([f"{i}|{lines[i - 1]}" for i in range(start_ln, end_ln + 1)]))
                else:
                    content.append("\n".join([lines[i - 1] for i in range(start_ln, end_ln + 1)]))
            
            results[f"{path} :: line number range {line_range}"] = "\n......\n".join(content)
        
        return results

    @staticmethod
    def get_content(repo_id: str,
                    base_commit: str,
                    location: dict[str, list[tuple[int]]],
                    add_lino: bool = True) -> dict[str, str]:

        '''
        location: mapping of
            file path : List[(start lineno, end lineno)]
        returns: mapping of
            file path + func def line range : func def and referred vars, funcs, methods in the same file.
        '''
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

            results.update(Extractor._get_func(abs_path, source, location[path], add_lino))

        results = Extractor._merge_same_content(results)
        return results



class APIDefExtractor:
    """
    Extract API function definitions from Go code, including third-party packages.
    
    Works by:
    1. Parsing imports to find package paths
    2. Resolving package paths to filesystem locations (vendor, module cache, stdlib)
    3. Searching for function definitions in those packages using tree-sitter
    """

    # Return this for built-in or standard library functions
    NOT_AVAILABLE = {"api function definition": "Not available."}
    
    # Patterns that indicate generated/mock code files (should be filtered)
    GENERATED_FILE_PATTERNS = {
        "_mock.go", "mock_", "_gen.go", "_generated.go", 
        "wire_gen.go", "_string.go", ".pb.go", ".pb.gw.go",
        "_easyjson.go", "_ffjson.go", "bindata.go",
    }
    
    # Maximum reasonable function length (lines) - functions longer than this are likely parsing errors
    MAX_FUNCTION_LINES = 1000
    
    # Maximum output size in characters - truncate if exceeded
    MAX_OUTPUT_SIZE = 100000  # ~100KB

    def __init__(self, container: SetupRuntime, base_commit: str, patch: str, repo_path: str = "/app"):
        self.container = container
        self.repo_path = repo_path
        
        # Wipe worktree
        self.container.send_command(f"cd {repo_path} && git reset --hard {base_commit}")
        self.container.send_command(f"cd {repo_path} && git clean -fd")
        
        # Checkout base commit
        self.container.send_command(f"cd {repo_path} && git checkout --quiet {base_commit}")
        
        # Apply patch if provided
        if patch and patch.strip():
            patch_file = f"{repo_path}/.tmp_swebench.patch"
            self.container.send_command(f"cat > {patch_file} << 'PATCH_EOF'\n{patch}\nPATCH_EOF")
            
            # Try applying with different strategies
            result = self.container.send_command(f"cd {repo_path} && git apply -v {patch_file}")
            if result.metadata.exit_code != 0:
                result = self.container.send_command(f"cd {repo_path} && git apply -v --reject {patch_file}")
            if result.metadata.exit_code != 0:
                self.container.send_command(f"cd {repo_path} && patch --batch --fuzz=5 -p1 -i {patch_file}")
            
            self.container.send_command(f"rm -f {patch_file}")
        
        # Cache for resolved package paths
        self._pkg_path_cache: dict[str, Optional[str]] = {}
        
        # Cache for Go environment variables
        self._gomodcache: Optional[str] = None
        self._goroot: Optional[str] = None

    
    def _is_stdlib_path(self, path: str) -> bool:
        """Check if a file path is in the Go standard library (GOROOT/src)."""
        if not path:
            return False
        goroot = self._get_goroot()
        stdlib_prefix = f"{goroot}/src/"
        return path.startswith(stdlib_prefix)
    
    def _is_generated_or_mock_file(self, path: str) -> bool:
        """Check if a file is auto-generated or mock code (should be filtered)."""
        if not path:
            return False
        
        filename = path.rsplit('/', 1)[-1].lower()
        
        # Check filename patterns
        for pattern in self.GENERATED_FILE_PATTERNS:
            if pattern in filename:
                return True
        
        # Check if in mocks directory
        if '/mocks/' in path.lower() or '/mock/' in path.lower():
            return True
        
        return False
    
    def _is_generated_content(self, content: str) -> bool:
        """Check if file content indicates it's auto-generated."""
        if not content:
            return False
        # Check first few lines for generation markers
        first_lines = content[:500].lower()
        markers = [
            "code generated", "do not edit", "auto-generated",
            "automatically generated", "generated by", "autogenerated",
        ]
        return any(marker in first_lines for marker in markers)

    def _get_gomodcache(self) -> str:
        """Get GOMODCACHE path, cached."""
        if self._gomodcache is None:
            result = self.container.send_command("go env GOMODCACHE")
            self._gomodcache = result.output.strip().split('\n')[-1]
        return self._gomodcache

    def _get_goroot(self) -> str:
        """Get GOROOT path, cached."""
        if self._goroot is None:
            result = self.container.send_command("go env GOROOT")
            self._goroot = result.output.strip().split('\n')[-1]
        return self._goroot

    def _parse_imports(self, source: str) -> dict[str, str]:
        """
        Parse Go imports and return mapping of alias -> import path.
        
        Examples:
            import "fmt"                    -> {"fmt": "fmt"}
            import f "fmt"                  -> {"f": "fmt"}
            import "github.com/pkg/errors"  -> {"errors": "github.com/pkg/errors"}
        """
        source_bytes = source.encode('utf-8')
        tree = GO_PARSER.parse(source_bytes)
        root = tree.root_node
        
        imports = {}
        
        for child in root.children:
            if child.type == "import_declaration":
                for spec in child.children:
                    if spec.type == "import_spec":
                        path_node = None
                        alias = None
                        
                        for node in spec.children:
                            if node.type == "interpreted_string_literal":
                                path_node = source_bytes[node.start_byte:node.end_byte].decode('utf-8').strip('"')
                            elif node.type == "package_identifier" or node.type == "identifier":
                                alias = source_bytes[node.start_byte:node.end_byte].decode('utf-8')
                            elif node.type == "dot":
                                alias = "."
                            elif node.type == "blank_identifier":
                                alias = "_"
                        
                        if path_node:
                            if alias is None:
                                # Default alias is last component of path
                                alias = path_node.split("/")[-1]
                            imports[alias] = path_node
                    elif spec.type == "import_spec_list":
                        # Handle grouped imports: import ( "fmt" \n "os" )
                        for inner_spec in spec.children:
                            if inner_spec.type == "import_spec":
                                path_node = None
                                alias = None
                                for node in inner_spec.children:
                                    if node.type == "interpreted_string_literal":
                                        path_node = source_bytes[node.start_byte:node.end_byte].decode('utf-8').strip('"')
                                    elif node.type == "package_identifier" or node.type == "identifier":
                                        alias = source_bytes[node.start_byte:node.end_byte].decode('utf-8')
                                    elif node.type == "dot":
                                        alias = "."
                                    elif node.type == "blank_identifier":
                                        alias = "_"
                                if path_node:
                                    if alias is None:
                                        alias = path_node.split("/")[-1]
                                    imports[alias] = path_node
        
        return imports

    def _resolve_package_path(self, import_path: str) -> Optional[str]:
        """
        Resolve a Go import path to a filesystem path in the container.
        
        Resolution order:
        1. Vendor directory: /app/vendor/<import_path>
        2. Module cache: $GOMODCACHE/<import_path>@<version>
        3. Standard library: $GOROOT/src/<import_path>
        """
        if import_path in self._pkg_path_cache:
            return self._pkg_path_cache[import_path]
        
        resolved_path = None
        
        # 1. Check vendor directory first
        vendor_path = f"{self.repo_path}/vendor/{import_path}"
        result = self.container.send_command(f"test -d '{vendor_path}' && echo 'exists'")
        if "exists" in result.output:
            resolved_path = vendor_path
        
        # 2. Check module cache
        if resolved_path is None:
            gomodcache = self._get_gomodcache()
            if gomodcache:
                # Try exact match with version suffix
                find_result = self.container.send_command(
                    f"ls -d '{gomodcache}/{import_path}@'* 2>/dev/null | head -1"
                )
                mod_path = find_result.output.strip().split('\n')[-1]
                if mod_path and "No such file" not in mod_path and mod_path != "":
                    resolved_path = mod_path
                
                # Try finding as subdirectory of a module
                if resolved_path is None:
                    # For paths like "github.com/user/repo/subpkg", 
                    # the module might be "github.com/user/repo"
                    parts = import_path.split("/")
                    for i in range(len(parts), 2, -1):
                        parent_path = "/".join(parts[:i])
                        find_result = self.container.send_command(
                            f"ls -d '{gomodcache}/{parent_path}@'* 2>/dev/null | head -1"
                        )
                        mod_path = find_result.output.strip().split('\n')[-1]
                        if mod_path and "No such file" not in mod_path and mod_path != "":
                            # Append remaining path components
                            remaining = "/".join(parts[i:])
                            if remaining:
                                full_path = f"{mod_path}/{remaining}"
                            else:
                                full_path = mod_path
                            # Verify it exists
                            check_result = self.container.send_command(f"test -d '{full_path}' && echo 'exists'")
                            if "exists" in check_result.output:
                                resolved_path = full_path
                                break
        
        # 3. Check standard library
        if resolved_path is None:
            goroot = self._get_goroot()
            if goroot:
                std_path = f"{goroot}/src/{import_path}"
                result = self.container.send_command(f"test -d '{std_path}' && echo 'exists'")
                if "exists" in result.output:
                    resolved_path = std_path
        
        self._pkg_path_cache[import_path] = resolved_path
        return resolved_path

    def _find_func_in_package(
        self, 
        pkg_path: str, 
        func_name: str, 
        is_method: bool = False,
        receiver_type: Optional[str] = None
    ) -> Optional[Tuple[str, int, int]]:
        """
        Find a function/method definition in a Go package directory.
        
        Parameters
        ----------
        pkg_path : str
            Path to the package directory
        func_name : str
            Name of the function/method to find
        is_method : bool
            If True, only search for methods (with receiver)
        receiver_type : str, optional
            If provided, filter methods by receiver type (e.g., "Client", "*Client")
            
        Returns (file_path, start_line, end_line) or None.
        """
        # Use grep to find the file and line
        # Use grep to find the file and line
        grep_result = self.container.send_command(
            f"grep -rn -E 'func\\s+(\\([^)]+\\)\\s+)?{func_name}\\s*\\[?\\(' '{pkg_path}/'*.go 2>/dev/null | head -50"
        )
        
        output = grep_result.output.strip()
        if not output or "No such file" in output or grep_result.metadata.exit_code != 0:
            # Try without the trailing slash for single files
            grep_result = self.container.send_command(
                f"grep -n -E 'func\\s+(\\([^)]+\\)\\s+)?{func_name}\\s*\\[?\\(' '{pkg_path}'/*.go 2>/dev/null | head -50"
            )
            output = grep_result.output.strip()
            if not output or "No such file" in output:
                return None
        
        # Collect all matches
        matches: List[Tuple[str, int, str, Optional[str]]] = []  # (file, line, content, receiver_type)
        
        # Pattern to extract receiver type: func (x *TypeName) or func (x TypeName)
        receiver_pattern = re.compile(r'func\s+\(\s*\w+\s+(\*?\w+)\s*\)')
        
        # Pattern to verify exact function name match (not a prefix)
        # Matches: func Name(, func Name[, func (recv) Name(, func (recv) Name[
        exact_func_pattern = re.compile(
            rf'func\s+(?:\([^)]+\)\s+)?{re.escape(func_name)}\s*[\[(]'
        )
        
        for line in output.split('\n'):
            if ':' not in line:
                continue
            parts = line.split(':', 2)
            if len(parts) >= 2:
                file_path = parts[0]
                try:
                    line_no = int(parts[1])
                    content = parts[2] if len(parts) >= 3 else ""
                    
                    # Verify this is actually the function definition (not a call)
                    if not content.strip().startswith("func"):
                        continue
                    
                    # Verify exact function name match (not getNamespaceKey when searching for getNamespace)
                    if not exact_func_pattern.search(content):
                        continue
                    
                    # Extract receiver type if present
                    recv_match = receiver_pattern.search(content)
                    recv_type = recv_match.group(1) if recv_match else None
                    
                    matches.append((file_path, line_no, content, recv_type))
                except ValueError:
                    continue
        
        if not matches:
            return None
        
        # If receiver_type is specified, try to find exact match
        if receiver_type:
            # Normalize receiver type (remove pointer if needed for comparison)
            clean_receiver = receiver_type.lstrip('*')
            
            for file_path, line_no, content, recv_type in matches:
                if recv_type:
                    clean_recv = recv_type.lstrip('*')
                    if clean_recv == clean_receiver:
                        end_line = self._find_func_end(file_path, line_no)
                        return (file_path, line_no, end_line)
        
        # If is_method is True, prefer matches with receivers
        if is_method:
            for file_path, line_no, content, recv_type in matches:
                if recv_type is not None:
                    end_line = self._find_func_end(file_path, line_no)
                    return (file_path, line_no, end_line)
        
        # Otherwise, prefer non-method (plain function) first, then any match
        for file_path, line_no, content, recv_type in matches:
            if recv_type is None:  # Plain function, not a method
                end_line = self._find_func_end(file_path, line_no)
                return (file_path, line_no, end_line)
        
        # Fallback to first match
        file_path, line_no, content, recv_type = matches[0]
        end_line = self._find_func_end(file_path, line_no)
        return (file_path, line_no, end_line)
    
    def _find_all_funcs_in_package(
        self, 
        pkg_path: str, 
        func_name: str
    ) -> List[Tuple[str, int, int, Optional[str]]]:
        """
        Find ALL function/method definitions with the given name in a package.
        
        Returns list of (file_path, start_line, end_line, receiver_type) tuples.
        Useful when there are multiple methods with the same name on different types.
        """
        grep_result = self.container.send_command(
            f"grep -rn -E 'func\\s+(\\([^)]+\\)\\s+)?{func_name}\\s*\\[?\\(' '{pkg_path}/'*.go 2>/dev/null"
        )
        
        output = grep_result.output.strip()
        if not output or "No such file" in output or grep_result.metadata.exit_code != 0:
            grep_result = self.container.send_command(
                f"grep -n -E 'func\\s+(\\([^)]+\\)\\s+)?{func_name}\\s*\\[?\\(' '{pkg_path}'/*.go 2>/dev/null"
            )
            output = grep_result.output.strip()
            if not output or "No such file" in output:
                return []
        
        results = []
        receiver_pattern = re.compile(r'func\s+\(\s*\w+\s+(\*?\w+)\s*\)')
        
        # Pattern to verify exact function name match
        exact_func_pattern = re.compile(
            rf'func\s+(?:\([^)]+\)\s+)?{re.escape(func_name)}\s*[\[()]'
        )
        
        for line in output.split('\n'):
            if ':' not in line:
                continue
            parts = line.split(':', 2)
            if len(parts) >= 2:
                file_path = parts[0]
                try:
                    line_no = int(parts[1])
                    content = parts[2] if len(parts) >= 3 else ""
                    
                    if not content.strip().startswith("func"):
                        continue
                    
                    # Verify exact function name match
                    if not exact_func_pattern.search(content):
                        continue
                    
                    recv_match = receiver_pattern.search(content)
                    recv_type = recv_match.group(1) if recv_match else None
                    
                    end_line = self._find_func_end(file_path, line_no)
                    results.append((file_path, line_no, end_line, recv_type))
                except ValueError:
                    continue
        
        return results
    
    def _infer_variable_type(self, source: str, var_name: str, use_line: int) -> Optional[str]:
        """
        Try to infer the type of a variable from the source code.
        
        Strategy:
        1. Search backwards from use_line to find the closest declaration (handles shadowing)
        2. If not found, search the whole file (might be declared after use in some cases)
        3. Handle ALL Go patterns for variable typing
        
        Returns the type name (with * for pointers) or None if not found.
        """
        lines = source.split('\n')
        
        # First, search backwards from use_line (closest declaration wins for shadowing)
        result = self._search_var_type_in_range(lines, var_name, use_line - 1, -1, -1)
        if result:
            return result
        
        # If not found backwards, search forwards from use_line (rare but possible)
        result = self._search_var_type_in_range(lines, var_name, use_line, len(lines), 1)
        if result:
            return result
        
        # Try to find function signature that might span multiple lines
        result = self._find_func_param_type_multiline(source, var_name, use_line)
        if result:
            return result
        
        return None
    
    def _search_var_type_in_range(
        self, 
        lines: List[str], 
        var_name: str, 
        start: int, 
        end: int, 
        step: int
    ) -> Optional[str]:
        """
        Search for variable type declaration in a range of lines.
        
        Parameters:
        - lines: source code split by lines
        - var_name: variable name to find type for
        - start: starting line index (0-based)
        - end: ending line index (exclusive)
        - step: 1 for forward, -1 for backward
        """
        escaped_var = re.escape(var_name)
        
        for i in range(start, end, step):
            if i < 0 or i >= len(lines):
                continue
            line = lines[i]
            
            # Skip if var_name not in line (optimization)
            if var_name not in line:
                continue
            
            # Skip comments
            stripped = line.strip()
            if stripped.startswith("//") or stripped.startswith("/*"):
                continue
            
            # Try all patterns
            result = self._try_all_type_patterns(line, var_name, escaped_var)
            if result:
                return result
        
        return None
    
    def _try_all_type_patterns(self, line: str, var_name: str, escaped_var: str) -> Optional[str]:
        """
        Try all possible Go patterns to infer variable type from a single line.
        Returns the type if found, None otherwise.
        """
        # ============================================================
        # EXPLICIT VAR DECLARATIONS
        # ============================================================
        
        # 1. var x TypeName or var x *TypeName or var x []TypeName
        match = re.search(rf'\bvar\s+{escaped_var}\s+(\*?\[?\]?(?:chan\s+)?[\w.]+)', line)
        if match:
            return self._normalize_type(match.group(1))
        
        # 2. var x, y, z TypeName (grouped var declaration)
        match = re.search(rf'\bvar\s+([\w\s,]+)\s+(\*?\[?\]?(?:chan\s+)?[\w.]+)\s*(?:=|$|\))', line)
        if match:
            var_list = [v.strip() for v in match.group(1).split(',')]
            if var_name in var_list:
                return self._normalize_type(match.group(2))
        
        # 3. var ( x TypeName ) or var ( x, y TypeName ) - inside var block
        match = re.search(rf'^\s*{escaped_var}\s+(\*?\[?\]?(?:chan\s+)?[\w.]+)\s*(?:=|$)', line)
        if match and 'func' not in line and ':=' not in line:
            return self._normalize_type(match.group(1))
        
        # ============================================================
        # SHORT DECLARATIONS WITH STRUCT LITERALS
        # ============================================================
        
        # 4. x := &TypeName{} - address of struct literal
        match = re.search(rf'\b{escaped_var}\s*:=\s*&([\w.]+)\s*\{{', line)
        if match:
            return f"*{self._extract_type_name(match.group(1))}"
        
        # 5. x := TypeName{} - struct literal (type must start with uppercase)
        match = re.search(rf'\b{escaped_var}\s*:=\s*([A-Z][\w]*)\s*\{{', line)
        if match:
            return match.group(1)
        
        # 6. x := pkg.TypeName{} - qualified struct literal
        match = re.search(rf'\b{escaped_var}\s*:=\s*([\w]+\.[\w]+)\s*\{{', line)
        if match:
            return self._extract_type_name(match.group(1))
        
        # 7. x := &pkg.TypeName{} - address of qualified struct literal  
        match = re.search(rf'\b{escaped_var}\s*:=\s*&([\w]+\.[\w]+)\s*\{{', line)
        if match:
            return f"*{self._extract_type_name(match.group(1))}"
        
        # ============================================================
        # CONSTRUCTOR PATTERNS
        # ============================================================
        
        # 8. x := NewTypeName() - constructor pattern (returns pointer usually)
        match = re.search(rf'\b{escaped_var}\s*:=\s*[Nn]ew([A-Z][\w]*)\s*\(', line)
        if match:
            return f"*{match.group(1)}"
        
        # 9. x := pkg.NewTypeName() - qualified constructor
        match = re.search(rf'\b{escaped_var}\s*:=\s*[\w]+\.[Nn]ew([A-Z][\w]*)\s*\(', line)
        if match:
            return f"*{match.group(1)}"
        
        # 10. x, err := NewTypeName() or x, _ := NewTypeName()
        match = re.search(rf'\b{escaped_var}\s*,\s*[\w_]+\s*:=\s*(?:[\w]+\.)?[Nn]ew([A-Z][\w]*)\s*\(', line)
        if match:
            return f"*{match.group(1)}"
        
        # 11. x, y, err := ... NewTypeName() - multiple return values
        match = re.search(rf'\b{escaped_var}\s*(?:,\s*[\w_]+)+\s*:=\s*(?:[\w]+\.)?[Nn]ew([A-Z][\w]*)\s*\(', line)
        if match:
            return f"*{match.group(1)}"
        
        # ============================================================
        # TYPE ASSERTIONS
        # ============================================================
        
        # 12. x := y.(TypeName) or x := y.(*TypeName)
        match = re.search(rf'\b{escaped_var}\s*:=\s*[\w.]+\.\(\s*(\*?[\w.]+)\s*\)', line)
        if match:
            return self._normalize_type(match.group(1))
        
        # 13. x, ok := y.(TypeName)
        match = re.search(rf'\b{escaped_var}\s*,\s*[\w_]+\s*:=\s*[\w.]+\.\(\s*(\*?[\w.]+)\s*\)', line)
        if match:
            return self._normalize_type(match.group(1))
        
        # 14. Type switch: case TypeName: (x is that type in the case block)
        # This is context-dependent, skip for now
        
        # ============================================================
        # MAKE EXPRESSIONS
        # ============================================================
        
        # 15. x := make([]TypeName, ...) - slice
        match = re.search(rf'\b{escaped_var}\s*:=\s*make\s*\(\s*(\[\s*\][\w.*]+)', line)
        if match:
            return match.group(1).replace(' ', '')
        
        # 16. x := make(chan TypeName) - channel
        match = re.search(rf'\b{escaped_var}\s*:=\s*make\s*\(\s*(chan\s+[\w.*]+)', line)
        if match:
            return match.group(1)
        
        # 17. x := make(map[K]V) - map
        match = re.search(rf'\b{escaped_var}\s*:=\s*make\s*\(\s*(map\s*\[[^\]]+\][\w.*]+)', line)
        if match:
            return match.group(1).replace(' ', '')
        
        # ============================================================
        # METHOD RECEIVER
        # ============================================================
        
        # 18. func (x TypeName) method() or func (x *TypeName) method()
        match = re.search(rf'func\s+\(\s*{escaped_var}\s+(\*?[\w.]+)\s*\)', line)
        if match:
            return self._normalize_type(match.group(1))
        
        # ============================================================
        # FUNCTION PARAMETERS (single line)
        # ============================================================
        
        # 19. Check if line is a func declaration and parse params
        func_match = re.search(r'func\s+(?:\([^)]*\)\s+)?[\w]*\s*\(([^)]*)\)', line)
        if func_match:
            params_str = func_match.group(1)
            inferred = self._parse_func_params_for_type(params_str, var_name)
            if inferred:
                return inferred
        
        # ============================================================
        # ASSIGNMENTS (not declarations - var was declared elsewhere)
        # ============================================================
        
        # 20. x = NewTypeName() (assignment)
        match = re.search(rf'\b{escaped_var}\s*=\s*(?:[\w]+\.)?[Nn]ew([A-Z][\w]*)\s*\(', line)
        if match and ':=' not in line:
            return f"*{match.group(1)}"
        
        # 21. x = &TypeName{}
        match = re.search(rf'\b{escaped_var}\s*=\s*&([\w.]+)\s*\{{', line)
        if match and ':=' not in line:
            return f"*{self._extract_type_name(match.group(1))}"
        
        # 22. x = TypeName{}
        match = re.search(rf'\b{escaped_var}\s*=\s*([A-Z][\w]*)\s*\{{', line)
        if match and ':=' not in line:
            return match.group(1)
        
        # ============================================================
        # SLICE/ARRAY LITERALS
        # ============================================================
        
        # 23. x := []TypeName{...}
        match = re.search(rf'\b{escaped_var}\s*:=\s*(\[\][\w.*]+)\s*\{{', line)
        if match:
            return match.group(1)
        
        # 24. x := [n]TypeName{...}
        match = re.search(rf'\b{escaped_var}\s*:=\s*(\[\d+\][\w.*]+)\s*\{{', line)
        if match:
            return match.group(1)
        
        # ============================================================
        # MAP LITERALS
        # ============================================================
        
        # 25. x := map[K]V{...}
        match = re.search(rf'\b{escaped_var}\s*:=\s*(map\[[^\]]+\][\w.*]+)\s*\{{', line)
        if match:
            return match.group(1)
        
        # ============================================================
        # CHANNEL RECEIVE
        # ============================================================
        
        # 26. x := <-ch (need to know ch's type, skip for now)
        
        # ============================================================
        # FOR-RANGE (partial support)
        # ============================================================
        
        # 27. for x := range ... or for _, x := range ...
        # Type depends on what's being ranged over, skip for now
        
        return None
    
    def _find_func_param_type_multiline(self, source: str, var_name: str, use_line: int) -> Optional[str]:
        """
        Find function parameter type when function signature spans multiple lines.
        
        Go allows:
        func foo(
            a TypeA,
            b TypeB,
        ) {
        """
        lines = source.split('\n')
        escaped_var = re.escape(var_name)
        
        # Search backwards from use_line to find 'func' keyword
        func_start = -1
        for i in range(min(use_line - 1, len(lines) - 1), -1, -1):
            if re.search(r'^\s*func\s+', lines[i]):
                func_start = i
                break
        
        if func_start == -1:
            return None
        
        # Collect lines until we find the closing ) of parameters
        params_lines = []
        paren_depth = 0
        found_open = False
        
        for i in range(func_start, min(func_start + 20, len(lines))):
            line = lines[i]
            params_lines.append(line)
            
            for char in line:
                if char == '(':
                    paren_depth += 1
                    found_open = True
                elif char == ')':
                    paren_depth -= 1
                    if found_open and paren_depth == 0:
                        # Found end of parameters
                        params_str = ' '.join(params_lines)
                        # Extract just the parameters part
                        match = re.search(r'func\s+(?:\([^)]*\)\s+)?[\w]*\s*\(([^)]*)\)', params_str, re.DOTALL)
                        if match:
                            return self._parse_func_params_for_type(match.group(1), var_name)
                        return None
        
        return None
    
    def _normalize_type(self, type_str: str) -> str:
        """Normalize a type string by removing extra whitespace."""
        return re.sub(r'\s+', '', type_str.strip())
    
    def _extract_type_name(self, qualified: str) -> str:
        """Extract just the type name from a potentially qualified name like pkg.TypeName."""
        if '.' in qualified:
            return qualified.split('.')[-1]
        return qualified
    
    def _parse_func_params_for_type(self, params_str: str, var_name: str) -> Optional[str]:
        """
        Parse function parameters to find the type of a specific variable.
        
        Handles:
        - func foo(x TypeName)
        - func foo(x, y TypeName)  -> both x and y are TypeName
        - func foo(a TypeA, x TypeName, b TypeB)
        - func foo(x ...TypeName)  -> variadic
        - func foo(x *TypeName)
        - func foo(x []TypeName)
        - func foo(x interface{})
        - func foo(x func() error)
        """
        # Normalize whitespace
        params_str = re.sub(r'\s+', ' ', params_str.strip())
        
        # Split by comma, but be careful of nested types like map[K]V, func(...), interface{}
        params = []
        current = ""
        bracket_depth = 0
        
        for char in params_str:
            if char in '[{(':
                bracket_depth += 1
                current += char
            elif char in ']})':
                bracket_depth -= 1
                current += char
            elif char == ',' and bracket_depth == 0:
                params.append(current.strip())
                current = ""
            else:
                current += char
        if current.strip():
            params.append(current.strip())
        
        # Process each param or param group
        for param in params:
            param = param.strip()
            if not param:
                continue
            
            # Check if this param contains our var_name
            if var_name not in param:
                continue
            
            # Check for variadic: x ...TypeName
            variadic_match = re.match(r'^([\w\s,]+)\s+\.\.\.(.+)$', param)
            if variadic_match:
                var_list = [v.strip() for v in variadic_match.group(1).split(',')]
                if var_name in var_list:
                    return f"[]{variadic_match.group(2).strip()}"
            
            # Check for interface{} or similar
            if 'interface' in param:
                interface_match = re.match(r'^([\w\s,]+)\s+(interface\s*\{[^}]*\})$', param)
                if interface_match:
                    var_list = [v.strip() for v in interface_match.group(1).split(',')]
                    if var_name in var_list:
                        return interface_match.group(2).replace(' ', '')
            
            # Check for func type: x func(...) ...
            func_type_match = re.match(r'^([\w\s,]+)\s+(func\s*\(.*)$', param)
            if func_type_match:
                var_list = [v.strip() for v in func_type_match.group(1).split(',')]
                if var_name in var_list:
                    return func_type_match.group(2)
            
            # Standard pattern: a, b, c TypeName or a TypeName
            # Type can be: *TypeName, []TypeName, map[K]V, chan TypeName, pkg.TypeName, etc.
            standard_match = re.match(r'^([\w\s,]+)\s+(\S.*)$', param)
            if standard_match:
                var_list = [v.strip() for v in standard_match.group(1).split(',')]
                if var_name in var_list:
                    type_part = standard_match.group(2).strip()
                    # Remove trailing comma if any
                    type_part = type_part.rstrip(',').strip()
                    return type_part
        
        return None

    def _find_func_end(self, file_path: str, start_line: int) -> int:
        """Find the end line of a function starting at start_line.
        
        This properly handles strings, comments, and raw strings to avoid 
        counting braces inside them.
        """
        result = self.container.send_command(f"cat '{file_path}'")
        if result.metadata.exit_code != 0:
            return start_line + 10  # Estimate
        
        lines = result.output.split('\n')
        brace_count = 0
        started = False
        in_string = False
        in_raw_string = False
        in_line_comment = False
        in_block_comment = False
        
        for i, line in enumerate(lines[start_line - 1:], start=start_line):
            j = 0
            in_line_comment = False  # Reset for each line
            while j < len(line):
                char = line[j]
                
                # Handle line comments
                if not in_string and not in_raw_string and not in_block_comment:
                    if j + 1 < len(line) and line[j:j+2] == '//':
                        break  # Skip rest of line
                
                # Handle block comments
                if not in_string and not in_raw_string:
                    if j + 1 < len(line) and line[j:j+2] == '/*':
                        in_block_comment = True
                        j += 2
                        continue
                    if in_block_comment and j + 1 < len(line) and line[j:j+2] == '*/':
                        in_block_comment = False
                        j += 2
                        continue
                
                if in_block_comment:
                    j += 1
                    continue
                
                # Handle raw strings (backticks)
                if char == '`' and not in_string:
                    in_raw_string = not in_raw_string
                    j += 1
                    continue
                
                if in_raw_string:
                    j += 1
                    continue
                
                # Handle regular strings
                if char == '"' and not in_raw_string:
                    # Check for escape
                    if j > 0 and line[j-1] == '\\':
                        # Count consecutive backslashes
                        num_backslashes = 0
                        k = j - 1
                        while k >= 0 and line[k] == '\\':
                            num_backslashes += 1
                            k -= 1
                        if num_backslashes % 2 == 1:
                            # Escaped quote
                            j += 1
                            continue
                    in_string = not in_string
                    j += 1
                    continue
                
                if in_string:
                    j += 1
                    continue
                
                # Count braces
                if char == '{':
                    brace_count += 1
                    started = True
                elif char == '}':
                    brace_count -= 1
                    if started and brace_count == 0:
                        return i
                
                j += 1
            
            # Safety: limit max function length to prevent runaway parsing
            if started and (i - start_line) > self.MAX_FUNCTION_LINES:
                return start_line + self.MAX_FUNCTION_LINES
        
        # Fallback: use a reasonable default, not the full file
        return min(start_line + 50, start_line + self.MAX_FUNCTION_LINES, len(lines))

    def _get_func_body(self, file_path: str, start_line: int, end_line: int, add_lino: bool = True) -> str|None:
        """
        Get the function body from a file given start and end line numbers.
        
        Returns the function source code, or None if:
        - File cannot be read
        - File is generated/mock code
        - Content looks corrupted
        - Line range is unreasonably large
        """
        # Sanity check: reject unreasonably large line ranges
        line_count = end_line - start_line + 1
        if line_count > self.MAX_FUNCTION_LINES:
            # Likely a parsing error - truncate to reasonable size
            end_line = start_line + self.MAX_FUNCTION_LINES - 1
        
        if line_count <= 0:
            return None
        
        import uuid
        tmp_file = f"{uuid.uuid4()}.txt"
        res = self.container.send_command(f"cat {file_path} > /mnt/{tmp_file}")
        #print("hit! read!", res.output)
        if res.metadata.exit_code != 0:
            return None
        
        try:
            with open(f"data/logs/{tmp_file}") as f:
                source = f.read()
        except Exception:
            self.container.send_command(f"rm -f /mnt/{tmp_file}")
            return None
        
        self.container.send_command(f"rm -f /mnt/{tmp_file}")
        
        # Check if source file is suspiciously large (might be binary or generated)
        if len(source) > 5_000_000:  # 5MB limit for source files
            return None
        
        #print("hit!", source[:100])
        
        definition = "\n...\n".join(Extractor._get_func(file_path, source, [(start_line, end_line)], True).values())
        
        # Truncate if output is too large
        if len(definition) > self.MAX_OUTPUT_SIZE:
            definition = definition[:self.MAX_OUTPUT_SIZE] + "\n... [truncated - function too large]"
        
        return definition

    def find_api(
        self,
        func_name: str,
        file_path: str,
        line_no: int,
        filter_stdlib: bool = True,
    ) -> dict:
        """
        Find the definition of a function call.
        
        Parameters
        ----------
        func_name : str
            The function call expression (e.g., "errors.Wrap", "fmt.Println", "localFunc", "obj.Method")
        file_path : str  
            Path to file containing the call (e.g., "/app/pkg/handler.go" or "pkg/handler.go")
        line_no : int
            Line number where the call appears
        filter_stdlib : bool
            If True, return "Not available." for Go built-ins and standard library functions.
            Default is True.
            
        Returns
        -------
        dict with keys:
            - "file of api definition": str
            - "lineno of api definition": tuple[int, int]
            - "api function name": str
            - "api function definition": str
        Or {"api function definition": "Not available."} if not found or filtered
        """
        
        # Normalize file path
        if not file_path.startswith("/"):
            file_path = f"{self.repo_path}/{file_path}"
        
        # Extract the actual function name (remove args if present)
        clean_func_name = func_name.split('(')[0].strip()
        
        # Read the source file
        result = self.container.send_command(f"cat '{file_path}'")
        if result.metadata.exit_code != 0:
            return self.NOT_AVAILABLE
        source = result.output
        
        # Parse imports
        imports = self._parse_imports(source)
        
        # Get package directory for local searches
        pkg_dir = '/'.join(file_path.rsplit('/', 1)[:-1]) or self.repo_path
        
        # Determine if it's a qualified call (pkg.Func) or local call
        if '.' in clean_func_name:
            # Could be: pkg.Func, obj.Method, pkg.Type.Method, etc.
            parts = clean_func_name.split('.')
            first_part = parts[0]
            target_func = parts[-1]
            
            # Check if first part is a package alias
            if first_part in imports:
                import_path = imports[first_part]
                pkg_path = self._resolve_package_path(import_path)
                
                if pkg_path is None:
                    # Package not found locally, might be external
                    return self.NOT_AVAILABLE
                
                # If there are 3 parts like pkg.Type.Method, use Type as receiver hint
                receiver_hint = parts[1] if len(parts) == 3 else None
                
                # Try to find the function/method
                location = self._find_func_in_package(pkg_path, target_func, receiver_type=receiver_hint)
                
                if location:
                    def_file, start_line, end_line = location
                    # Filter out stdlib and generated files
                    if not (filter_stdlib and (self._is_stdlib_path(def_file) or self._is_generated_or_mock_file(def_file))):
                        body = self._get_func_body(def_file, start_line, end_line)
                        if body is not None:
                            return {
                                "file of api definition": def_file,
                                "lineno of api definition": (start_line, end_line),
                                "api function name": target_func,
                                "api function definition": body
                            }
                
                # Try as method
                location = self._find_func_in_package(pkg_path, target_func, is_method=True, receiver_type=receiver_hint)
                if location:
                    def_file, start_line, end_line = location
                    # Filter out stdlib and generated files
                    if not (filter_stdlib and (self._is_stdlib_path(def_file) or self._is_generated_or_mock_file(def_file))):
                        body = self._get_func_body(def_file, start_line, end_line)
                        if body is not None:
                            return {
                                "file of api definition": def_file,
                                "lineno of api definition": (start_line, end_line),
                                "api function name": target_func,
                                "api function definition": body
                            }
                
                # If we found a location but it was stdlib/generated, return NOT_AVAILABLE
                # (we don't want to fall through to local function search for pkg.Func calls)
                return self.NOT_AVAILABLE
            else:
                # First part is not a package alias - likely a method call on a variable
                # e.g., myVar.DoSomething() - try to infer the type of myVar
                var_name = first_part
                
                # Try to infer the type of the variable
                inferred_type = self._infer_variable_type(source, var_name, line_no)
                
                if inferred_type:
                    # Check if the type is from an imported package
                    type_pkg = None
                    type_name = inferred_type.lstrip('*').lstrip('[').lstrip(']')
                    
                    if '.' in type_name:
                        # Qualified type like pkg.TypeName
                        type_parts = type_name.split('.')
                        type_pkg_alias = type_parts[0]
                        type_name = type_parts[-1]
                        if type_pkg_alias in imports:
                            type_pkg = self._resolve_package_path(imports[type_pkg_alias])
                    
                    # Search in the appropriate package
                    search_pkg = type_pkg if type_pkg else pkg_dir
                    
                    location = self._find_func_in_package(search_pkg, target_func, is_method=True, receiver_type=type_name)
                    if location:
                        def_file, start_line, end_line = location
                        # Filter out stdlib and generated files
                        if not (filter_stdlib and (self._is_stdlib_path(def_file) or self._is_generated_or_mock_file(def_file))):
                            body = self._get_func_body(def_file, start_line, end_line)
                            if body is not None:
                                return {
                                    "file of api definition": def_file,
                                    "lineno of api definition": (start_line, end_line),
                                    "api function name": target_func,
                                    "api function definition": body,
                                    "inferred_receiver_type": inferred_type
                                }
                    
                    # Also try without the pointer prefix for receiver matching
                    clean_type = type_name.lstrip('*')
                    if clean_type != type_name:
                        location = self._find_func_in_package(search_pkg, target_func, is_method=True, receiver_type=clean_type)
                        if location:
                            def_file, start_line, end_line = location
                            # Filter out stdlib and generated files
                            if not (filter_stdlib and (self._is_stdlib_path(def_file) or self._is_generated_or_mock_file(def_file))):
                                body = self._get_func_body(def_file, start_line, end_line)
                                if body is not None:
                                    return {
                                        "file of api definition": def_file,
                                        "lineno of api definition": (start_line, end_line),
                                        "api function name": target_func,
                                        "api function definition": body,
                                        "inferred_receiver_type": inferred_type
                                    }
                
                # Fallback: search in local package without type hint
                # First try to find all matches and see if there's only one
                all_matches = self._find_all_funcs_in_package(pkg_dir, target_func)
                method_matches = [(f, s, e, r) for f, s, e, r in all_matches if r is not None]
                
                if len(method_matches) == 1:
                    # Only one method with this name, use it
                    def_file, start_line, end_line, recv_type = method_matches[0]
                    # Filter out stdlib and generated files
                    if not (filter_stdlib and (self._is_stdlib_path(def_file) or self._is_generated_or_mock_file(def_file))):
                        body = self._get_func_body(def_file, start_line, end_line)
                        if body is not None:
                            return {
                                "file of api definition": def_file,
                                "lineno of api definition": (start_line, end_line),
                                "api function name": target_func,
                                "api function definition": body,
                                "receiver_type": recv_type
                            }
                elif len(method_matches) > 1:
                    # Multiple methods - filter out stdlib and generated matches first
                    if filter_stdlib:
                        method_matches = [
                            (f, s, e, r) for f, s, e, r in method_matches 
                            if not self._is_stdlib_path(f) and not self._is_generated_or_mock_file(f)
                        ]
                    # Try each match until we find one with valid body
                    for def_file, start_line, end_line, recv_type in method_matches:
                        body = self._get_func_body(def_file, start_line, end_line)
                        if body is not None:
                            result = {
                                "file of api definition": def_file,
                                "lineno of api definition": (start_line, end_line),
                                "api function name": target_func,
                                "api function definition": body,
                                "receiver_type": recv_type,
                            }
                            if len(method_matches) > 1:
                                result["warning"] = f"Multiple methods named '{target_func}' found on different types"
                                result["all_matches"] = [
                                    {"file": f, "line_range": (s, e), "receiver": r}
                                    for f, s, e, r in method_matches
                                ]
                            return result
                
                # Try searching in imported packages too (the variable might be of an imported type)
                for pkg_alias, import_path in imports.items():
                    ext_pkg_path = self._resolve_package_path(import_path)
                    if ext_pkg_path:
                        location = self._find_func_in_package(ext_pkg_path, target_func, is_method=True)
                        if location:
                            def_file, start_line, end_line = location
                            # Filter out stdlib and generated files
                            if filter_stdlib and (self._is_stdlib_path(def_file) or self._is_generated_or_mock_file(def_file)):
                                continue  # Skip, try next package
                            body = self._get_func_body(def_file, start_line, end_line)
                            if body is not None:
                                return {
                                    "file of api definition": def_file,
                                    "lineno of api definition": (start_line, end_line),
                                    "api function name": target_func,
                                    "api function definition": body,
                                    "note": f"Found in imported package {import_path}"
                                }
        
        # Local function - search in same package (same directory)
        location = self._find_func_in_package(pkg_dir, clean_func_name)
        if location:
            def_file, start_line, end_line = location
            # Filter out stdlib and generated files
            if not (filter_stdlib and (self._is_stdlib_path(def_file) or self._is_generated_or_mock_file(def_file))):
                body = self._get_func_body(def_file, start_line, end_line)
                if body is not None:
                    return {
                        "file of api definition": def_file,
                        "lineno of api definition": (start_line, end_line),
                        "api function name": clean_func_name,
                        "api function definition": body
                    }
        
        # Also try as a method (might be called without explicit receiver in same package)
        location = self._find_func_in_package(pkg_dir, clean_func_name, is_method=True)
        if location:
            def_file, start_line, end_line = location
            # Filter out stdlib and generated files
            if not (filter_stdlib and (self._is_stdlib_path(def_file) or self._is_generated_or_mock_file(def_file))):
                body = self._get_func_body(def_file, start_line, end_line)
                if body is not None:
                    return {
                        "file of api definition": def_file,
                        "lineno of api definition": (start_line, end_line),
                        "api function name": clean_func_name,
                        "api function definition": body
                    }
        
        return self.NOT_AVAILABLE

