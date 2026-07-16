"""Tree-sitter Python extractor for mneme-graph.

Walks the AST of a single .py file and produces:

* GraphNode per module (one per file), top-level class, top-level
  function/async-function, and module-level variable assignment targets.
* GraphEdge(kind='imports') from the module node to a synthetic target
  node for each import_statement or import_from_statement at the top level.
* GraphEdge(kind='defines') from a class node to each method defined
  directly inside it.
* GraphEdge(kind='inherits') from a class node to each base class (locally
  resolved to EXTRACTED confidence; external bases get an <external> node).
* GraphEdge(kind='calls') from a function/method node to the callee target.
  Direct and attribute-call forms are handled. When the callee resolves to a
  locally-extracted function/method by name the edge confidence is INFERRED
  (heuristic name match, not precise binding). When no local match exists the
  target is an <external> node with confidence EXTRACTED (the call site is
  directly observed).

Limitations (v1 — document honestly):
  * No cross-file binding: 'calls' resolution is same-vault name match only.
  * No TS/JS/Rust: Python-only for v1.
  * No community detection, PR-impact, or entity canonicalization: deferred.

Security: the file is resolved and checked against vault_root before reading
(same pattern as the FTS5 indexer) to prevent vault-escape via symlinks.

Redaction: graph nodes contain ONLY code identifiers (symbol names) and
vault-relative paths. String-literal values are never stored. No redaction
call is needed; nodes are identifier-only by construction.

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
    """Return a vault-relative posix path, e.g. 'src/foo.py'."""
    return path.relative_to(vault_root).as_posix()


def extract_file(
    path: Path,
    vault_root: Path,
) -> tuple[list[GraphNode], list[GraphEdge]]:
    """Extract GraphNodes and GraphEdges from a single Python file.

    Args:
        path:       Absolute path to the .py file.
        vault_root: Absolute vault root. Used for vault-relative paths and
                    symlink-escape containment.

    Returns:
        A 2-tuple (nodes, edges). Both lists may be empty if the file
        cannot be read or parsed.

    Raises:
        ValueError: if path escapes vault_root after symlink resolution.
        OSError:    if the file cannot be read.
    """
    # Symlink-escape containment — same guard as the FTS5 indexer.
    resolved = path.resolve()
    vault_resolved = vault_root.resolve()
    if not resolved.is_relative_to(vault_resolved):
        raise ValueError(
            f"Path {path} resolves to {resolved}, which is outside vault {vault_resolved}"
        )

    raw_bytes = path.read_bytes()
    chash = _content_hash(raw_bytes)
    rel_path = _vault_rel(resolved, vault_resolved)
    # Edges carry no extraction timestamp: a derived graph must rebuild
    # byte-for-byte from unchanged source, so no wall-clock value is stored.
    valid_at = ""

    nodes: list[GraphNode] = []
    edges: list[GraphEdge] = []

    # Lazy import so the package can be imported without tree-sitter installed
    # in environments that only need the schema or store (e.g. type-checkers).
    try:
        import tree_sitter_python as _tspython
        from tree_sitter import Language, Parser
    except ImportError as exc:
        raise ImportError(
            "tree-sitter and tree-sitter-python are required for extraction. "
            "Install mneme-graph with its default dependencies."
        ) from exc

    py_language = Language(_tspython.language())
    parser = Parser(py_language)
    tree = parser.parse(raw_bytes)
    root = tree.root_node

    # --- Module node (one per file) ---
    module_name = Path(rel_path).stem
    module_node = GraphNode.make(
        kind="module",
        name=module_name,
        source_path=rel_path,
        content_hash=chash,
        line_start=0,
        line_end=max(0, root.end_point.row),
        confidence="EXTRACTED",
        trust="extracted",
    )
    nodes.append(module_node)

    # Build a name->node_id index of function/class nodes for 'calls' resolution.
    # Populated incrementally as we emit nodes; used at call-site resolution time.
    # Key: unqualified name (str), value: node_id (str).
    _local_fn_index: dict[str, str] = {}

    # Walk top-level children of the module node.
    for child in root.children:
        node_type = child.type

        # --- Top-level imports → edges from module to import target ---
        if node_type == "import_statement":
            # e.g.  import os, pathlib
            # Children: 'import' keyword, then dotted_name nodes
            for sub in child.children:
                if sub.type in ("dotted_name", "aliased_import"):
                    # For aliased_import grab the first dotted_name child
                    target_name = _extract_dotted_name(sub)
                    if target_name:
                        target_node = _make_import_target(
                            target_name,
                            rel_path,
                            chash,
                            child.start_point.row,
                            child.end_point.row,
                        )
                        nodes.append(target_node)
                        edges.append(
                            GraphEdge.make(
                                src_id=module_node.node_id,
                                dst_id=target_node.node_id,
                                kind="imports",
                                confidence="EXTRACTED",
                                valid_at=valid_at,
                            )
                        )

        elif node_type == "import_from_statement":
            # e.g.  from pathlib import Path, PurePath
            # First dotted_name is the module being imported from.
            module_part = _first_named_child(child, "dotted_name")
            if module_part:
                target_name = _extract_dotted_name(module_part)
                if target_name:
                    target_node = _make_import_target(
                        target_name,
                        rel_path,
                        chash,
                        child.start_point.row,
                        child.end_point.row,
                    )
                    nodes.append(target_node)
                    edges.append(
                        GraphEdge.make(
                            src_id=module_node.node_id,
                            dst_id=target_node.node_id,
                            kind="imports",
                            confidence="EXTRACTED",
                            valid_at=valid_at,
                        )
                    )

        # --- Top-level class definitions ---
        elif node_type == "class_definition":
            class_name = _identifier_text(child, raw_bytes)
            if not class_name:
                continue
            class_node = GraphNode.make(
                kind="class",
                name=class_name,
                source_path=rel_path,
                content_hash=chash,
                line_start=child.start_point.row,
                line_end=child.end_point.row,
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

            # Inherits edges from superclass list.
            _emit_inherits_edges(
                child,
                class_node,
                nodes,
                edges,
                rel_path,
                chash,
                valid_at,
            )

            # Methods directly inside the class body.
            body = _first_named_child(child, "block")
            if body is not None:
                for method in body.children:
                    if method.type in (
                        "function_definition",
                        "async_function_definition",
                        "decorated_definition",
                    ):
                        fn = method
                        if method.type == "decorated_definition":
                            fn = (
                                _first_named_child(method, "function_definition")
                                or _first_named_child(method, "async_function_definition")
                                or method
                            )
                        method_name = _identifier_text(fn, raw_bytes)
                        if not method_name:
                            continue
                        method_node = GraphNode.make(
                            kind="function",
                            name=method_name,
                            source_path=rel_path,
                            content_hash=chash,
                            line_start=method.start_point.row,
                            line_end=method.end_point.row,
                            confidence="EXTRACTED",
                            trust="extracted",
                        )
                        nodes.append(method_node)
                        _local_fn_index[method_name] = method_node.node_id
                        edges.append(
                            GraphEdge.make(
                                src_id=class_node.node_id,
                                dst_id=method_node.node_id,
                                kind="defines",
                                confidence="EXTRACTED",
                                valid_at=valid_at,
                            )
                        )

        # --- Top-level function / async function definitions ---
        elif node_type in ("function_definition", "async_function_definition"):
            fn_name = _identifier_text(child, raw_bytes)
            if not fn_name:
                continue
            fn_node = GraphNode.make(
                kind="function",
                name=fn_name,
                source_path=rel_path,
                content_hash=chash,
                line_start=child.start_point.row,
                line_end=child.end_point.row,
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

        # --- Decorated top-level definitions ---
        elif node_type == "decorated_definition":
            inner = (
                _first_named_child(child, "function_definition")
                or _first_named_child(child, "async_function_definition")
                or _first_named_child(child, "class_definition")
            )
            if inner is None:
                continue
            inner_name = _identifier_text(inner, raw_bytes)
            if not inner_name:
                continue
            inner_kind: str = "class" if inner.type == "class_definition" else "function"
            inner_node = GraphNode.make(
                kind=inner_kind,  # type: ignore[arg-type]
                name=inner_name,
                source_path=rel_path,
                content_hash=chash,
                line_start=child.start_point.row,
                line_end=child.end_point.row,
                confidence="EXTRACTED",
                trust="extracted",
            )
            nodes.append(inner_node)
            _local_fn_index[inner_name] = inner_node.node_id
            edges.append(
                GraphEdge.make(
                    src_id=module_node.node_id,
                    dst_id=inner_node.node_id,
                    kind="defines",
                    confidence="EXTRACTED",
                    valid_at=valid_at,
                )
            )

            # For decorated classes, walk the body to emit method nodes just
            # like the undecorated class_definition branch does.
            if inner.type == "class_definition":
                # Inherits edges for the decorated class.
                _emit_inherits_edges(
                    inner,
                    inner_node,
                    nodes,
                    edges,
                    rel_path,
                    chash,
                    valid_at,
                )

                body = _first_named_child(inner, "block")
                if body is not None:
                    for method in body.children:
                        if method.type in (
                            "function_definition",
                            "async_function_definition",
                            "decorated_definition",
                        ):
                            fn = method
                            if method.type == "decorated_definition":
                                fn = (
                                    _first_named_child(method, "function_definition")
                                    or _first_named_child(method, "async_function_definition")
                                    or method
                                )
                            method_name = _identifier_text(fn, raw_bytes)
                            if not method_name:
                                continue
                            method_node = GraphNode.make(
                                kind="function",
                                name=method_name,
                                source_path=rel_path,
                                content_hash=chash,
                                line_start=method.start_point.row,
                                line_end=method.end_point.row,
                                confidence="EXTRACTED",
                                trust="extracted",
                            )
                            nodes.append(method_node)
                            _local_fn_index[method_name] = method_node.node_id
                            edges.append(
                                GraphEdge.make(
                                    src_id=inner_node.node_id,
                                    dst_id=method_node.node_id,
                                    kind="defines",
                                    confidence="EXTRACTED",
                                    valid_at=valid_at,
                                )
                            )

        # --- Module-level variable assignments (top-level only) ---
        elif node_type == "expression_statement":
            # expression_statement wrapping an assignment yields variable nodes.
            assign = _first_named_child(child, "assignment")
            if assign is not None:
                _emit_variable_nodes(
                    assign,
                    module_node,
                    nodes,
                    edges,
                    rel_path,
                    chash,
                    valid_at,
                    raw_bytes,
                )

    # --- Second pass: emit 'calls' edges from function/method bodies ---
    # We walk function/method nodes collected above and scan their bodies for
    # call_expression nodes.  Resolution is by unqualified name against the
    # _local_fn_index built during the first pass.
    _emit_calls_edges(
        root,
        nodes,
        edges,
        _local_fn_index,
        rel_path,
        chash,
        valid_at,
        raw_bytes,
    )

    return nodes, edges


# ---------------------------------------------------------------------------
# Inherits edge emitter
# ---------------------------------------------------------------------------


def _emit_inherits_edges(
    class_def_node: Node,
    class_graph_node: GraphNode,
    nodes: list[GraphNode],
    edges: list[GraphEdge],
    rel_path: str,
    chash: str,
    valid_at: str,
) -> None:
    """Emit inherits edges from class_def_node's superclass list."""
    # class_definition: 'class' identifier argument_list? ':' block
    arg_list = _first_named_child(class_def_node, "argument_list")
    if arg_list is None:
        return
    for arg in arg_list.children:
        # Each argument is either an identifier or attribute (e.g. base.Sub)
        base_name = _extract_base_class_name(arg)
        if not base_name:
            continue
        # Skip keyword args like metaclass=ABCMeta
        if "=" in base_name:
            continue
        # Determine the unqualified name for local lookup (rightmost segment).
        unqualified = base_name.split(".")[-1]
        # Look for a locally extracted class node with that name.
        # We search nodes list directly — at this point class nodes for
        # earlier top-level classes are already in nodes.
        local_match: GraphNode | None = None
        for n in nodes:
            if n.kind == "class" and n.name == unqualified and n.source_path == rel_path:
                local_match = n
                break
        if local_match is not None:
            dst_node = local_match
        else:
            # Emit an external node for the base class.
            dst_node = GraphNode.make(
                kind="class",
                name=base_name,
                source_path="<external>",
                content_hash="",
                line_start=arg.start_point.row,
                line_end=arg.end_point.row,
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


def _extract_base_class_name(node: Node) -> str:
    """Return the textual name of a base class argument node, or ''."""
    if node.type == "identifier":
        text = node.text
        if text is not None:
            return text.decode("utf-8", errors="replace")
        return ""
    if node.type == "attribute":
        # attribute: object '.' attribute — reconstruct dotted name
        parts: list[str] = []
        for child in node.children:
            if child.type in ("identifier", "attribute"):
                part = _extract_base_class_name(child)
                if part:
                    parts.append(part)
            elif child.type == ".":
                parts.append(".")
        return "".join(parts)
    # keyword_argument, *args, etc. — return "" to skip
    return ""


# ---------------------------------------------------------------------------
# Variable node emitter
# ---------------------------------------------------------------------------


def _emit_variable_nodes(
    assign_node: Node,
    module_node: GraphNode,
    nodes: list[GraphNode],
    edges: list[GraphEdge],
    rel_path: str,
    chash: str,
    valid_at: str,
    source: bytes,
) -> None:
    """Emit variable nodes for module-level assignment targets.

    Only simple identifier targets are emitted (e.g. `X = 1`, `CONSTANT = "v"`).
    Tuple-unpacking and subscript targets are skipped as they cannot be
    represented as a single unambiguous name.

    Redaction note: only the identifier name is stored, never the right-hand
    side value — so string literals and other user content are not captured.
    """
    # assignment: left_side '=' right_side
    # The left side (target) is the first named child of type 'identifier',
    # 'pattern_list', or similar.  We only handle simple identifiers.
    for child in assign_node.children:
        if child.type == "identifier":
            name_bytes = child.text
            if name_bytes is None:
                name_raw = source[child.start_byte : child.end_byte]
                var_name = name_raw.decode("utf-8", errors="replace")
            else:
                var_name = name_bytes.decode("utf-8", errors="replace")
            if not var_name:
                continue
            var_node = GraphNode.make(
                kind="variable",
                name=var_name,
                source_path=rel_path,
                content_hash=chash,
                line_start=assign_node.start_point.row,
                line_end=assign_node.end_point.row,
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
            break  # Only the first identifier target per assignment statement


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
    source: bytes,
) -> None:
    """Walk function and method bodies and emit ``calls`` edges.

    The traversal is deliberately iterative.  Some tree-sitter Python binding
    combinations have crashed while a deeply recursive Python walker repeatedly
    materialised ``Node.children`` under coverage.  Keeping the parsed tree alive
    in ``extract_file`` and traversing an explicit stack avoids both Python stack
    growth and dependence on ``Node.text`` lifetime behaviour.
    """
    fn_nodes = [node for node in nodes if node.kind == "function"]
    fn_by_name: dict[str, list[GraphNode]] = {}
    for fn_node in fn_nodes:
        fn_by_name.setdefault(fn_node.name, []).append(fn_node)

    node_ids = {node.node_id for node in nodes}
    edge_ids = {edge.edge_id for edge in edges}
    stack: list[tuple[Node, GraphNode | None]] = [(root, None)]

    while stack:
        node, enclosing_fn = stack.pop()
        node_type = node.type
        active_fn = enclosing_fn

        if node_type in ("function_definition", "async_function_definition"):
            fn_name = _identifier_text(node, source)
            if fn_name:
                row = node.start_point.row
                for candidate in fn_by_name.get(fn_name, ()):
                    if (
                        candidate.source_path == rel_path
                        and candidate.line_start <= row <= candidate.line_end
                    ):
                        active_fn = candidate
                        break

        if node_type == "call" and active_fn is not None:
            callee_name = _extract_callee_name(node, source)
            if callee_name:
                _emit_one_call_edge(
                    callee_name,
                    active_fn,
                    nodes,
                    edges,
                    local_fn_index,
                    rel_path,
                    chash,
                    valid_at,
                    node.start_point.row,
                    node.end_point.row,
                    node_ids,
                    edge_ids,
                )

        # Reverse preserves the source-order behaviour of the old recursive
        # depth-first traversal while keeping memory bounded by tree width.
        children = node.children
        for child in reversed(children):
            stack.append((child, active_fn))


def _extract_callee_name(call_node: Node, source: bytes) -> str:
    """Extract the callee name from a call node.

    Handles:
      func(...)          → 'func'
      obj.method(...)    → 'method'  (rightmost identifier)
    """
    if not call_node.children:
        return ""
    fn_child = call_node.children[0]
    if fn_child.type == "identifier":
        return source[fn_child.start_byte : fn_child.end_byte].decode(
            "utf-8", errors="replace"
        )
    if fn_child.type == "attribute":
        # attribute: object '.' attribute_name
        # The last child of type 'identifier' is the method name.
        last_id = ""
        for child in fn_child.children:
            if child.type == "identifier":
                last_id = source[child.start_byte : child.end_byte].decode(
                    "utf-8", errors="replace"
                )
        return last_id
    return ""


def _emit_one_call_edge(
    callee_name: str,
    caller_node: GraphNode,
    nodes: list[GraphNode],
    edges: list[GraphEdge],
    local_fn_index: dict[str, str],
    rel_path: str,
    chash: str,
    valid_at: str,
    line_start: int,
    line_end: int,
    node_ids: set[str],
    edge_ids: set[str],
) -> None:
    """Emit a single 'calls' edge from caller_node to the resolved callee."""
    if callee_name in local_fn_index:
        # Heuristic name match → INFERRED confidence.
        dst_id = local_fn_index[callee_name]
        confidence = "INFERRED"
    else:
        # No local match → external node, EXTRACTED confidence.
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
        # Only append if not already present (dedup by node_id).
        if ext_node.node_id not in node_ids:
            nodes.append(ext_node)
            node_ids.add(ext_node.node_id)
        dst_id = ext_node.node_id
        confidence = "EXTRACTED"

    # Avoid self-edges and duplicate edges.
    if dst_id == caller_node.node_id:
        return
    edge = GraphEdge.make(
        src_id=caller_node.node_id,
        dst_id=dst_id,
        kind="calls",
        confidence=confidence,  # type: ignore[arg-type]
        valid_at=valid_at,
    )
    if edge.edge_id not in edge_ids:
        edges.append(edge)
        edge_ids.add(edge.edge_id)


# ---------------------------------------------------------------------------
# Internal AST helpers
# ---------------------------------------------------------------------------


def _identifier_text(node: Node, source: bytes) -> str:
    """Return the text of the first 'identifier' child of node."""
    for child in node.children:
        if child.type == "identifier":
            start_byte = child.start_byte
            end_byte = child.end_byte
            return source[start_byte:end_byte].decode("utf-8", errors="replace")
    return ""


def _first_named_child(node: Node, type_name: str) -> Node | None:
    """Return the first child of node whose type matches type_name, or None."""
    for child in node.children:
        if child.type == type_name:
            return child
    return None


def _extract_dotted_name(node: Node) -> str:
    """Extract text from a dotted_name or aliased_import node."""
    node_type = node.type
    if node_type == "aliased_import":
        # aliased_import: dotted_name 'as' identifier
        sub = _first_named_child(node, "dotted_name")
        if sub is not None:
            return _extract_dotted_name(sub)
        return ""
    if node_type == "dotted_name":
        # dotted_name: identifier ('.' identifier)*
        parts: list[str] = []
        for child in node.children:
            if child.type == "identifier":
                text = child.text
                if text is not None:
                    parts.append(text.decode("utf-8", errors="replace"))
        return ".".join(parts)
    return ""


def _make_import_target(
    target_name: str,
    source_path: str,  # noqa: ARG001 — kept for call-site clarity, unused after fix
    content_hash: str,  # noqa: ARG001 — kept for call-site clarity, unused after fix
    line_start: int,
    line_end: int,
) -> GraphNode:
    """Synthetic module node representing an imported external module.

    source_path is fixed to '<external>' and content_hash to '' so that two
    files importing the same module (e.g. 'os') produce the same node_id and
    can be deduplicated by the GraphStore without ambiguity.  The node_id is
    therefore sha256('<external>:' + target_name + ':module')[:16].
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
