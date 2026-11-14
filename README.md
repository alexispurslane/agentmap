# agentmap

agentmap is a live dashboard for a portrait side monitor. It shows what every Claude Code and Codex thread of one T3 Code project does now.

- The map at the top is a treemap of the project. Each directory gets an area in proportion to its lines of code. Each thread is a dot, with a tag such as `B2`, in the area of the file that it touched last. Small square marks show the other areas that it touched recently.
- The tree below has one card per thread. A subagent's card sits inside the card of the thread that started it. A card shows what the thread does now, its newest thought, and the first sentence of its last message.
- Agents can post a status line through MCP. The agent row shows it when it is the newest thing the agent said.

The server uses the Python 3 standard library only. The page uses Datastar 1.0.5, which the repository includes. The design is in [docs/plans/agentmap.md](docs/plans/agentmap.md).

## Start the dashboard

You need T3 Code on the same machine, because agentmap reads T3 Code's database (`~/.t3/userdata/statev2.sqlite`) read-only.

1. Start the server for one project:

   ```sh
   nix develop --command python3 -m agentmap --project ~/Development/myproject
   ```

2. Open `http://127.0.0.1:7788/` in a browser window on the portrait monitor.

Options:

| Option | Default | Meaning |
|---|---|---|
| `--project PATH` | none, required | The root of the T3 Code project to show. |
| `--port N` | `7788` | The port on `127.0.0.1`. |
| `--t3-db PATH` | `~/.t3/userdata/statev2.sqlite` | T3 Code's database. |
| `--hours H` | `3` | Show the threads that changed in the last `H` hours. |

The server listens on `127.0.0.1` only.

## Let agents post a status line

The same server is an MCP server at `http://127.0.0.1:7788/mcp`. It gives agents one tool, `set_status`. Its instructions tell the agents to post a line after each major step, and to start the line with the `tk` ticket id that they work on.

The status lines go to `.agentmap/status.jsonl` in the watched project. The file is an append-only log.

### Claude Code

Add this server to `.mcp.json` in the watched project:

```json
{
  "mcpServers": {
    "agentmap": {
      "type": "http",
      "url": "http://127.0.0.1:7788/mcp",
      "headers": { "X-T3-Thread": "${T3CODE_THREAD_ID}" }
    }
  }
}
```

T3 Code sets `T3CODE_THREAD_ID` in each Claude Code process that it starts. Claude Code puts the value in the header, so the dashboard knows which thread calls.

### Codex

Add this server to `~/.codex/config.toml`:

```toml
[mcp_servers.agentmap]
url = "http://127.0.0.1:7788/mcp"
```

All Codex threads in T3 Code share one process, so Codex cannot send a thread header. Instead, the dashboard finds the call in T3 Code's records. A Codex call can therefore wait up to 2 seconds. If two Codex threads make the same call at the same moment, one of them gets an error that asks it to call again.

Keep the server name `agentmap` in both configurations. The dashboard looks for tool calls under that name.

## Run the tests

```sh
nix develop --command python3 -m unittest
```

Coverage and mutation testing also run in the dev shell:

```sh
nix develop --command bash -c 'coverage run --branch -m unittest && coverage report -m'
nix develop --command bash -c 'rm -rf mutants && mutmut run && mutmut results'
```

## Limits

- agentmap reads tables that T3 Code uses internally. A new T3 Code version can change them. The reader's tests show which fields it uses.
- The map counts lines of the files that git tracks, plus new files that git does not ignore. It builds the map again every 10 minutes.
- One server shows one project. Run one server per project, each on its own port, and give each project's agents the matching port.
