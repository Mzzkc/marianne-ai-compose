# Local server quickstart: any OpenAI-compatible server as your instrument

You run a local model server (PolyStrata, llama-server, LM Studio, vLLM,
Ollama, anything that answers `POST /v1/chat/completions`) and want Marianne
to use it. This is the whole path, in the order the plumbing needs it. It was
walked against a PolyStrata-shaped server on 2026-11-05; every step below
names the error you get when it is skipped.

## 0. Check the server is up and what it serves

```bash
curl -s http://127.0.0.1:8080/v1/models
```

Note the model `id` the server lists. Many local servers accept **any** name
in a request and answer with the model they have loaded (PolyStrata and
llama-server both do). Marianne's readiness probe only trusts an id the
server lists, so the profile below must name that id, not a nickname.

## 1. Write one profile

Put it in `~/.marianne/instruments/<name>.yaml`. That directory is read by
every `mzt` command and by the conductor wherever it was started.
(A project-local `.marianne/instruments/` works too, but only when the
conductor was started from that project directory; otherwise submit refuses
the score and names both directories.)

```yaml
name: localbox
display_name: "My local server"
kind: http
default_model: qwen3.6-35b-a3b-ud-q3_k_m   # the id from /v1/models
models:
  - name: qwen3.6-35b-a3b-ud-q3_k_m
    context_window: 32768                   # the server's REAL context size
    cost_per_1k_input: 0.0
    cost_per_1k_output: 0.0
http:
  base_url: http://127.0.0.1:8080/v1
  endpoint: /chat/completions
  schema_family: openai
  # auth_env_var: LOCAL_API_KEY           # only if the server needs a key
```

`context_window` is not decoration. The HTTP executor asks for up to 16,384
output tokens by default and caps that at the model's declared capacity. A
local server refuses a request above its context with HTTP 400 before
generating anything, so a profile that omits or overstates the context fails
every sheet with `bad_request`.

## 2. Prove it from Marianne's side

```bash
mzt instruments check localbox     # "Server: answering, serves the default model"
```

| What you see | What it means |
|---|---|
| `no server answered at …` | the server is down or the port is wrong |
| `model '…' is not served at …; served: …` | copy an id from the served list into `default_model` |
| `✗ not found` in `mzt instruments list` | you are not in the directory that holds the profile (see step 1) |

## 3. Run a two-sheet score

```yaml
name: first-local
description: "Two sheets on the local server"
workspace: ./workspaces/first-local
instrument: localbox
instrument_fallbacks: []
sheet:
  total_items: 2        # one item per sheet; `size` defaults to 1
cross_sheet:
  auto_capture_stdout: true
prompt:
  template: |
    {% if sheet_num == 1 %}
    Summarise, in one paragraph, a design for a CLI todo app.
    {% else %}
    Earlier output:
    {{ previous_outputs }}
    Turn that summary into three numbered implementation tasks.
    {% endif %}
```

```bash
mzt validate first-local.yaml      # V215 is quiet when the server is reachable; V234 if context_window overstates /props
mzt start                          # from the directory that holds your profile
mzt run first-local.yaml
mzt status first-local --watch
```

An `http` profile answers; it has no filesystem. Its reply is the sheet's
stdout, reaches later sheets through `cross_sheet` capture, and is recorded on
the sheet. To keep the whole reply as a file, name one per sheet with
`sheet.stdout_to` (`{1: design.md, 2: tasks.md}`): the musician writes it
before validations run, so a `file_exists` or `content_regex` validation can
read it and a later sheet can inject it. A score whose sheets must *edit
files in a project* (most engineering examples, and every score the compiler
or `score-composer` generates) still needs a CLI agent that can edit files. The local way to get one is OpenCode pointed at
the same server:

```json
// ~/.config/opencode/opencode.json
{ "provider": { "localbox": {
    "npm": "@ai-sdk/openai-compatible",
    "options": { "baseURL": "http://127.0.0.1:8080/v1" },
    "models": { "qwen3.6-35b-a3b-ud-q3_k_m": {} } } } }
```

then `instrument_config.model: "localbox/qwen3.6-35b-a3b-ud-q3_k_m"` on the
built-in `opencode` instrument, exactly as
[`examples/getting-started/hello-local.yaml`](../examples/getting-started/hello-local.yaml)
does for Ollama.

## 4. Capability classes

Shipped examples bind classes (`workhorse`, `strong`, `fast`), resolved
through `~/.marianne/classes.yaml`, which `mzt instruments classes write`
generates from the profiles on the machine. A profile of yours joins a class
only where its `class_hints` put it, and only when it satisfies that class's
policy: `fast` and `cheap` accept an `http` profile; `workhorse` requires
`tool_use` and `file_editing`, which an `http` profile cannot provide. So add

```yaml
class_hints: {fast: first, cheap: first}
```

to the profile for the classes it can serve, and route file-writing classes
through the `opencode` recipe above.

## Where the path still has friction

- `mzt instruments classes write` seats a local server only in `fast` and
  `cheap`; `workhorse` on a fresh machine with nothing but a local server
  stays empty and `hello-setup` reports it.
- The 400 cap reads the profile's `models` entry. `mzt instruments check` and
  `mzt validate` (V234) compare it with the server's live context from
  `GET /props`; a server that does not expose `/props` is not checked.
