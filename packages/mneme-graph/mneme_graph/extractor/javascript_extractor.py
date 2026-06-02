"""Tree-sitter JavaScript (and TypeScript) extractor for mneme-graph.

Walks the AST of a single .js/.jsx/.mjs/.cjs/.ts/.tsx file and produces:

* GraphNode per module (one per file), top-level class, top-level
  function declaration, named function/arrow expression assigned to a
  const/let/var declarator, class method, and top-level variable declarator.
* GraphEdge(kind='imports') from the module node to a synthetic target node
  for each ES import_statement and require() call at the top level.
* GraphEdge(kind='defines') from the module node to each top-level
  function/class/variable, and from a class node to each of its methods.
* GraphEdge(kind='inherits') from a class node to its superclass (locally
  resolved if present, else an <external> node).
* GraphEdge(kind='calls') from a function/method node to the callee target.
  Direct and member-expression forms are handled. Callee resolved by
  name against the local function index; unknown callees → <external>.

Limitations (v1):
  * No cross-file binding: 'calls' resolution is same-file name match only.
  * TypeScript-specific constructs (interfaces, type aliases, decorators)
    are not extracted; the function/class/variable nodes that tree-sitter
    surfaces are captured by the same code path.

Security: symlink-escape containment matches python_extractor.
Redaction: only code identifiers stored, never string-literal values.
The extractor is stateless and side-effect-free at import time.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import TYPE_CHECKING

from ..schema import GraphEdge, GraphNode

if TYPE_CHECKING:
    from tree_sitter import Node


def _content_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _vault_rel(path: Path, vault_root: Path) -> str:
    """Return a vault-relative posix path, e.g. 'src/foo.js'."""
    return path.relative_to(vault_root).as_posix()


def _node_text(node: Node) -> str:
    """Return the text of a tree-sitter node as a str."""
    text = node.text
    if text is not None:
        return text.decode("utf-8", errors="replace")
    return ""


def _first_child_of_type(node: Node, type_name: str) -> Node | None:
    for child in node.children:
        if child.type == type_name:
            return child
    return None


def _identifier_text(node: Node) -> str:
    """Return the text of the first 'identifier' or 'type_identifier' child, or ''.

    TypeScript class declarations use 'type_identifier' instead of 'identifier'
    for the class name; both are handled here so the same code path works for
    JS and TS grammars without branching.
    """
    for child in node.children:
        if child.type in ("identifier", "type_identifier"):
            return _node_text(child)
    return ""


def _make_import_target(
    target_name: str,
    line_start: int,
    line_end: int,
) -> GraphNode:
    """Synthetic module node for an imported external module.

    source_path fixed to '<external>' and content_hash to '' so that two
    files importing the same module produce the same node_id and can be
    deduplicated by the GraphStore without ambiguity.
    """
    return GraphNode.make(
        kind="module",
        name=target_name,
        source_path="<external>",
        content_hash="",
        line_start=line_start,
        line_end=line_end,
        confidence="EXTRACTED",
        trust="extracted",
    )


def extract_file(
    path: Path,
    vault_root: Path,
) -> tuple[list[GraphNode], list[GraphEdge]]:
    """Extract GraphNodes and GraphEdges from a single JS/TS file.

    Args:
        path:       Absolute path to the source file.
        vault_root: Absolute vault root. Used for vault-relative paths and
                    symlink-escape containment.

    Returns:
        A 2-tuple (nodes, edges). Both lists may be empty if the file
        cannot be read or parsed.

    Never raises on parse errors or unreadable files — returns ([], []).
    Raises ValueError if path escapes vault_root after symlink resolution.
    """
    # Symlink-escape containment — same guard as python_extractor.
    # FIX 6: resolve() and relative_to() moved inside the try so that OSError
    # from resolve() (e.g. broken symlink, permissions) also returns ([], [])
    # instead of propagating, keeping the never-raises contract.
    try:
        resolved = path.resolve()
        vault_resolved = vault_root.resolve()
        if not resolved.is_relative_to(vault_resolved):
            raise ValueError(
                f"Path {path} resolves to {resolved}, which is outside vault {vault_resolved}"
            )
        raw_bytes = path.read_bytes()
    except ValueError:
        raise
    except OSError:
        return [], []

    chash = _content_hash(raw_bytes)
    rel_path = _vault_rel(resolved, vault_resolved)
    valid_at = ""  # deterministic: no wall-clock value stored

    nodes: list[GraphNode] = []
    edges: list[GraphEdge] = []

    # Lazy import so the package can be imported without the grammar installed.
    suffix = path.suffix.lower()
    try:
        if suffix in (".ts", ".tsx"):
            import tree_sitter_typescript as _tsts
            from tree_sitter import Language, Parser

            if suffix == ".tsx":
                lang_obj = Language(_tsts.language_tsx())
            else:
                lang_obj = Language(_tsts.language_typescript())
        else:
            import tree_sitter_javascript as _tsjs
            from tree_sitter import Language, Parser

            lang_obj = Language(_tsjs.language())
    except ImportError:
        return [], []

    try:
        parser = Parser(lang_obj)
        tree = parser.parse(raw_bytes)
    except Exception:  # noqa: BLE001
        return [], []

    root = tree.root_node

    # --- Module node (one per file) ---
    module_name = Path(rel_path).stem
    module_node = GraphNode.make(
        kind="module",
        name=module_name,
        source_path=rel_path,
        content_hash=chash,
        line_start=0,
        line_end=max(0, root.end_point[0]),
        confidence="EXTRACTED",
        trust="extracted",
    )
    nodes.append(module_node)

    # name -> node_id index for 'calls' resolution
    _local_fn_index: dict[str, str] = {}

    # Walk top-level children of the program node.
    for child in root.children:
        node_type = child.type

        # --- ES import statement: import foo from 'bar' ---
        if node_type == "import_statement":
            # The source string is a 'string' node; extract its fragment.
            src_node = _first_child_of_type(child, "string")
            if src_node is not None:
                target_name = _extract_string_value(src_node)
                if target_name:
                    t = _make_import_target(target_name, child.start_point[0], child.end_point[0])
                    nodes.append(t)
                    edges.append(
                        GraphEdge.make(
                            src_id=module_node.node_id,
                            dst_id=t.node_id,
                            kind="imports",
                            confidence="EXTRACTED",
                            valid_at=valid_at,
                        )
                    )

        # --- Top-level function declarations ---
        elif node_type == "function_declaration":
            fn_name = _identifier_text(child)
            if not fn_name:
                continue
            fn_node = GraphNode.make(
                kind="function",
                name=fn_name,
                source_path=rel_path,
                content_hash=chash,
                line_start=child.start_point[0],
                line_end=child.end_point[0],
                confidence="EXTRACTED",
                trust="extracted",
            )
            nodes.append(fn_node)
            _local_fn_index[fn_name] = fn_node.node_id
            edges.append(
                GraphEdge.make(
                    src_id=module_node.node_id,
                    dst_id=fn_node.node_id,
                    kind="defines",
                    confidence="EXTRACTED",
                    valid_at=valid_at,
                )
            )

        # --- Top-level class declarations ---
        elif node_type == "class_declaration":
            class_name = _identifier_text(child)
            if not class_name:
                continue
            class_node = GraphNode.make(
                kind="class",
                name=class_name,
                source_path=rel_path,
                content_hash=chash,
                line_start=child.start_point[0],
                line_end=child.end_point[0],
                confidence="EXTRACTED",
                trust="extracted",
            )
            nodes.append(class_node)
            _local_fn_index[class_name] = class_node.node_id
            edges.append(
                GraphEdge.make(
                    src_id=module_node.node_id,
                    dst_id=class_node.node_id,
                    kind="defines",
                    confidence="EXTRACTED",
                    valid_at=valid_at,
                )
            )

            # inherits edge from class_heritage
            _emit_inherits_edge(child, class_node, nodes, edges, rel_path, chash, valid_at)

            # method nodes from class_body
            _emit_method_nodes(
                child,
                class_node,
                nodes,
                edges,
                rel_path,
                chash,
                valid_at,
                _local_fn_index,
            )

        # --- Top-level const/let/var declarations ---
        elif node_type in ("lexical_declaration", "variable_declaration"):
            for decl in child.children:
                if decl.type != "variable_declarator":
                    continue
                decl_name = _identifier_text(decl)
                if not decl_name:
                    continue

                # Check the RHS for function/arrow expressions
                rhs = _variable_declarator_rhs(decl)
                if rhs is not None and rhs.type in (
                    "function_expression",
                    "arrow_function",
                ):
                    fn_node = GraphNode.make(
                        kind="function",
                        name=decl_name,
                        source_path=rel_path,
                        content_hash=chash,
                        line_start=child.start_point[0],
                        line_end=child.end_point[0],
                        confidence="EXTRACTED",
                        trust="extracted",
                    )
                    nodes.append(fn_node)
                    _local_fn_index[decl_name] = fn_node.node_id
                    edges.append(
                        GraphEdge.make(
                            src_id=module_node.node_id,
                            dst_id=fn_node.node_id,
                            kind="defines",
                            confidence="EXTRACTED",
                            valid_at=valid_at,
                        )
                    )
                elif rhs is not None and rhs.type == "call_expression":
                    # Check for require('x') pattern
                    require_target = _extract_require(rhs)
                    if require_target:
                        t = _make_import_target(
                            require_target,
                            child.start_point[0],
                            child.end_point[0],
                        )
                        nodes.append(t)
                        edges.append(
                            GraphEdge.make(
                                src_id=module_node.node_id,
                                dst_id=t.node_id,
                                kind="imports",
                                confidence="EXTRACTED",
                                valid_at=valid_at,
                            )
                        )
                    else:
                        # A non-require call assigned to a name → variable node
                        var_node = GraphNode.make(
                            kind="variable",
                            name=decl_name,
                            source_path=rel_path,
                            content_hash=chash,
                            line_start=child.start_point[0],
                            line_end=child.end_point[0],
                            confidence="EXTRACTED",
                            trust="extracted",
                        )
                        nodes.append(var_node)
                        edges.append(
                            GraphEdge.make(
                                src_id=module_node.node_id,
                                dst_id=var_node.node_id,
                                kind="defines",
                                confidence="EXTRACTED",
                                valid_at=valid_at,
                            )
                        )
                else:
                    # Plain variable (number, string, object, etc.)
                    var_node = GraphNode.make(
                        kind="variable",
                        name=decl_name,
                        source_path=rel_path,
                        content_hash=chash,
                        line_start=child.start_point[0],
                        line_end=child.end_point[0],
                        confidence="EXTRACTED",
                        trust="extracted",
                    )
                    nodes.append(var_node)
                    edges.append(
                        GraphEdge.make(
                            src_id=module_node.node_id,
                            dst_id=var_node.node_id,
                            kind="defines",
                            confidence="EXTRACTED",
                            valid_at=valid_at,
                        )
                    )
                break  # only first declarator per declaration statement

        # --- expression_statement at top level (bare require, exports.x = fn, etc.) ---
        elif node_type == "expression_statement":
            expr = child.children[0] if child.children else None
            if expr is not None and expr.type == "call_expression":
                require_target = _extract_require(expr)
                if require_target:
                    t = _make_import_target(
                        require_target,
                        child.start_point[0],
                        child.end_point[0],
                    )
                    nodes.append(t)
                    edges.append(
                        GraphEdge.make(
                            src_id=module_node.node_id,
                            dst_id=t.node_id,
                            kind="imports",
                            confidence="EXTRACTED",
                            valid_at=valid_at,
                        )
                    )

    # --- Second pass: emit 'calls' edges from function/method bodies ---
    _emit_calls_edges(root, nodes, edges, _local_fn_index, rel_path, chash, valid_at)

    return nodes, edges


# ---------------------------------------------------------------------------
# Inherits edge emitter
# ---------------------------------------------------------------------------


def _emit_inherits_edge(
    class_decl: Node,
    class_graph_node: GraphNode,
    nodes: list[GraphNode],
    edges: list[GraphEdge],
    rel_path: str,
    chash: str,
    valid_at: str,
) -> None:
    """Emit an inherits edge for 'class A extends B'."""
    heritage = _first_child_of_type(class_decl, "class_heritage")
    if heritage is None:
        return
    # class_heritage: 'extends' identifier_or_member_expression
    for child in heritage.children:
        if child.type in ("identifier", "member_expression"):
            base_name = _node_text(child)
            if not base_name:
                continue
            # Unqualified name for local lookup (rightmost segment)
            unqualified = base_name.split(".")[-1]
            local_match: GraphNode | None = None
            for n in nodes:
                if n.kind == "class" and n.name == unqualified and n.source_path == rel_path:
                    local_match = n
                    break
            if local_match is not None:
                dst_node = local_match
            else:
                dst_node = GraphNode.make(
                    kind="class",
                    name=base_name,
                    source_path="<external>",
                    content_hash="",
                    line_start=child.start_point[0],
                    line_end=child.end_point[0],
                    confidence="EXTRACTED",
                    trust="extracted",
                )
                nodes.append(dst_node)
            edges.append(
                GraphEdge.make(
                    src_id=class_graph_node.node_id,
                    dst_id=dst_node.node_id,
                    kind="inherits",
                    confidence="EXTRACTED",
                    valid_at=valid_at,
                )
            )
            break  # only one superclass in JS


# ---------------------------------------------------------------------------
# Method node emitter
# ---------------------------------------------------------------------------


def _emit_method_nodes(
    class_decl: Node,
    class_graph_node: GraphNode,
    nodes: list[GraphNode],
    edges: list[GraphEdge],
    rel_path: str,
    chash: str,
    valid_at: str,
    local_fn_index: dict[str, str],
) -> None:
    """Emit function nodes for methods inside a class_body."""
    body = _first_child_of_type(class_decl, "class_body")
    if body is None:
        return
    for item in body.children:
        if item.type != "method_definition":
            continue
        # method_definition: property_identifier formal_parameters statement_block
        method_name = ""
        for child in item.children:
            if child.type == "property_identifier":
                method_name = _node_text(child)
                break
        if not method_name:
            continue
        method_node = GraphNode.make(
            kind="function",
            name=method_name,
            source_path=rel_path,
            content_hash=chash,
            line_start=item.start_point[0],
            line_end=item.end_point[0],
            confidence="EXTRACTED",
            trust="extracted",
        )
        nodes.append(method_node)
        local_fn_index[method_name] = method_node.node_id
        edges.append(
            GraphEdge.make(
                src_id=class_graph_node.node_id,
                dst_id=method_node.node_id,
                kind="defines",
                confidence="EXTRACTED",
                valid_at=valid_at,
            )
        )


# ---------------------------------------------------------------------------
# Calls edge emitter
# ---------------------------------------------------------------------------


def _emit_calls_edges(
    root: Node,
    nodes: list[GraphNode],
    edges: list[GraphEdge],
    local_fn_index: dict[str, str],
    rel_path: str,
    chash: str,
    valid_at: str,
) -> None:
    """Walk function/method bodies and emit 'calls' edges."""
    fn_nodes_by_id = {n.node_id: n for n in nodes if n.kind == "function"}
    _walk_calls(
        root,
        None,
        nodes,
        edges,
        local_fn_index,
        rel_path,
        chash,
        valid_at,
        fn_nodes_by_id,
    )


def _walk_calls(
    node: Node,
    enclosing_fn: GraphNode | None,
    nodes: list[GraphNode],
    edges: list[GraphEdge],
    local_fn_index: dict[str, str],
    rel_path: str,
    chash: str,
    valid_at: str,
    fn_nodes_by_id: dict[str, GraphNode],
) -> None:
    """Recursively walk the AST and emit 'calls' edges for call_expression nodes."""
    node_type = node.type
    new_enclosing = enclosing_fn

    # Enter a named function scope: function_declaration, function_expression,
    # arrow_function, or method_definition.
    if node_type in (
        "function_declaration",
        "function_expression",
        "arrow_function",
        "method_definition",
    ):
        # Determine the name for this function scope.
        fn_name = ""
        if node_type == "function_declaration" or node_type in ("function_expression",):
            fn_name = _identifier_text(node)
        elif node_type == "method_definition":
            for child in node.children:
                if child.type == "property_identifier":
                    fn_name = _node_text(child)
                    break
        # For arrow_function we try to find the enclosing variable name via
        # parent; we can't walk upward easily, so fall back: match by line range.
        if not fn_name:
            # FIX 5: among ALL candidate function nodes whose line range contains
            # this node's start row, pick the one with the SMALLEST span
            # (line_end - line_start) = the innermost scope.  Tiebreak by
            # node_id for determinism.  Previously used dict insertion order
            # (first match = outermost), which misattributed calls inside nested
            # arrow functions to the outer enclosing function.
            best: GraphNode | None = None
            for n in fn_nodes_by_id.values():
                if n.source_path == rel_path and n.line_start <= node.start_point[0] <= n.line_end:
                    if best is None:
                        best = n
                    else:
                        n_span = n.line_end - n.line_start
                        best_span = best.line_end - best.line_start
                        if n_span < best_span or (n_span == best_span and n.node_id < best.node_id):
                            best = n
            if best is not None:
                new_enclosing = best
        else:
            for n in fn_nodes_by_id.values():
                if (
                    n.name == fn_name
                    and n.source_path == rel_path
                    and n.line_start <= node.start_point[0] <= n.line_end
                ):
                    new_enclosing = n
                    break

    if node_type == "call_expression" and new_enclosing is not None:
        callee_name = _extract_callee_name(node)
        if callee_name and callee_name != "require":
            _emit_one_call_edge(
                callee_name,
                new_enclosing,
                nodes,
                edges,
                local_fn_index,
                rel_path,
                chash,
                valid_at,
                node.start_point[0],
                node.end_point[0],
            )

    for child in node.children:
        _walk_calls(
            child,
            new_enclosing,
            nodes,
            edges,
            local_fn_index,
            rel_path,
            chash,
            valid_at,
            fn_nodes_by_id,
        )


def _extract_callee_name(call_node: Node) -> str:
    """Extract the callee name from a call_expression node.

    Handles:
      func(...)          → 'func'
      obj.method(...)    → 'method'  (rightmost identifier)
    """
    if not call_node.children:
        return ""
    fn_child = call_node.children[0]
    if fn_child.type == "identifier":
        return _node_text(fn_child)
    if fn_child.type == "member_expression":
        # member_expression: object '.' property
        last_id = ""
        for child in fn_child.children:
            if child.type in ("identifier", "property_identifier"):
                last_id = _node_text(child)
        return last_id
    return ""


def _emit_one_call_edge(
    callee_name: str,
    caller_node: GraphNode,
    nodes: list[GraphNode],
    edges: list[GraphEdge],
    local_fn_index: dict[str, str],
    rel_path: str,
    chash: str,  # noqa: ARG001 — kept for call-site symmetry with python_extractor
    valid_at: str,
    line_start: int,
    line_end: int,
) -> None:
    """Emit a single 'calls' edge from caller_node to the resolved callee."""
    if callee_name in local_fn_index:
        dst_id = local_fn_index[callee_name]
        confidence = "INFERRED"
    else:
        ext_node = GraphNode.make(
            kind="function",
            name=callee_name,
            source_path="<external>",
            content_hash="",
            line_start=line_start,
            line_end=line_end,
            confidence="EXTRACTED",
            trust="extracted",
        )
        existing_ids = {n.node_id for n in nodes}
        if ext_node.node_id not in existing_ids:
            nodes.append(ext_node)
        dst_id = ext_node.node_id
        confidence = "EXTRACTED"

    if dst_id == caller_node.node_id:
        return
    edge = GraphEdge.make(
        src_id=caller_node.node_id,
        dst_id=dst_id,
        kind="calls",
        confidence=confidence,  # type: ignore[arg-type]
        valid_at=valid_at,
    )
    existing_edge_ids = {e.edge_id for e in edges}
    if edge.edge_id not in existing_edge_ids:
        edges.append(edge)


# ---------------------------------------------------------------------------
# String / require helpers
# ---------------------------------------------------------------------------


def _extract_string_value(string_node: Node) -> str:
    """Extract the text content of a tree-sitter 'string' node."""
    for child in string_node.children:
        if child.type == "string_fragment":
            return _node_text(child)
    # Fallback: strip surrounding quotes from raw text
    raw = _node_text(string_node)
    return raw.strip("'\"")


def _variable_declarator_rhs(decl_node: Node) -> Node | None:
    """Return the right-hand side node of a variable_declarator, or None."""
    # variable_declarator: identifier '=' value
    found_eq = False
    for child in decl_node.children:
        if child.type == "=":
            found_eq = True
            continue
        if found_eq:
            return child
    return None


def _extract_require(call_node: Node) -> str:
    """If call_node is a require('x') call, return 'x'; else return ''."""
    if not call_node.children:
        return ""
    fn_child = call_node.children[0]
    if fn_child.type != "identifier":
        return ""
    if _node_text(fn_child) != "require":
        return ""
    # Find the arguments node
    args_node = _first_child_of_type(call_node, "arguments")
    if args_node is None:
        return ""
    for arg in args_node.children:
        if arg.type == "string":
            return _extract_string_value(arg)
    return ""
