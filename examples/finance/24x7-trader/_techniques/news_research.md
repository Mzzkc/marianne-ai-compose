# News & Research Technique Contract

The agent's eyes on the world. The default implementation, `news_harness.sh`,
calls Codex (`--search`) or Claude Code (WebSearch/WebFetch) headless on the
operator's subscription: no API key. `news_perplexity.sh` is the paid-API
alternative; Tavily, Exa or Brave Search also fit. `news_offline.sh` is a test
seam for plumbing tests, not a research source.

## Contract

```
news <verb> [args...]
```

### Required verbs

| Verb | Args | Stdout | Exit |
|---|---|---|---|
| `macro_today` | `[--date YYYY-MM-DD]` | Markdown summary of macro signals for the day | 0 |
| `sector_news` | `<SECTOR>` | Markdown digest of sector news, last 24h | 0 |
| `catalysts` | `<TICKER>` | Markdown bullet list of upcoming/recent catalysts | 0 |
| `peer_compare` | `<TICKER>` | Markdown comparison vs sector peers | 0 |
| `regulatory` | `<TICKER>` | Markdown regulatory filings/news | 0 |

Exit non-zero on API failure with a single-line stderr message. The
score's Andon Cord stage diagnoses repeat failures.

## Output discipline

Output MUST be markdown. Output MUST cite sources with URLs when
network calls were made. If the source is from training-data only
(no web call), the output MUST start with `**[OFFLINE — training data only]**`
so the agent downstream knows not to treat it as fresh.

## Configuration

```
NEWS_CMD       — absolute path to the news script
NEWS_HARNESS   — news_harness.sh only: codex (default) | claude; uses the harness's own login
PERPLEXITY_KEY — news_perplexity.sh only
```

## Replacing the implementation

Provide a script satisfying the contract above. The reference
`_scripts/news_harness.sh` is intentionally simple — it makes one
headless harness call per verb and returns markdown. A more sophisticated
implementation could cache, batch, or fall back across providers.
