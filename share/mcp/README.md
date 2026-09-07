# Giving an agent the car

    omacar mcp

speaks MCP on stdin and stdout. Every harness below launches it the same way;
only the file it is written in differs.

## Claude Code

    claude mcp add omacar -- omacar mcp

or, for one session without changing your configuration:

    claude --mcp-config share/mcp/omacar.mcp.json --strict-mcp-config

`--strict-mcp-config` is worth knowing about even outside OmaCar: without it a
session also loads every server your account has, which on a machine with a few
connectors means several hundred extra tool definitions in the prompt.

## Codex

    codex --config mcp_servers.omacar.command=omacar \
          --config mcp_servers.omacar.args='["mcp"]'

or the same two keys in `~/.codex/config.toml`.

## Cursor

`.cursor/mcp.json` in a project, or `~/.cursor/mcp.json` for every project.
`share/mcp/omacar.mcp.json` in this directory is already in that shape — copy
it.

## Anything else

The requirement is a harness that speaks MCP over stdio and can run a command.
There is no HTTP endpoint and no port, deliberately: the server reaches your
car, and a thing that reaches your car should not be listening on a socket.

## What it will and will not do

Seven tools. Six of them read: the snapshot, the live sample, the profile, the
mode, and one constrained request to a module — read services only, 0x19, 0x21
and 0x22, through the same gate every other caller uses, with the same motion
check a person gets.

The seventh, `request_write`, queues a proposal for you and sends nothing. That
is not a limitation to work around; it is the arrangement. An agent can read
your car, look up what somebody else has already deciphered, check it against
your car, and write down what it found — at confidence `proposed`, below
`candidate`, which cannot drive a gauge and which no machine can raise. Only a
person checking a value against something real awards `validated`.

An honest note on the boundary: an agent with a shell on this machine can edit
the mode file directly, and every harness above has a shell. What is true is
that OmaCar's own interfaces will not let an agent raise its own privilege, so
a call routed through this server cannot do what you have not allowed. That is
worth having and it is not the same as a sandbox.
