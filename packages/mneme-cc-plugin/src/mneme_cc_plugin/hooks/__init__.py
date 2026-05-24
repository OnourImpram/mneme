"""Claude Code hook implementations for the mneme plugin.

Each hook module exposes a ``main()`` entry point that reads a JSON
event from stdin, performs its task, and writes a JSON response to
stdout. Every hook fails closed: any exception inside the hook is
caught and converted to a benign success response so Claude Code is
never blocked by a mneme defect.

The ``MNEME_DISABLED`` environment variable acts as a global kill
switch. When truthy, every hook short-circuits before doing any work.
"""
