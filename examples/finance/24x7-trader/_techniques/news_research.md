# News & Research Technique Contract

The agent's eyes on the world. Reference implementation uses Perplexity;
substitutes include Tavily, Exa, Brave Search, or Claude Code's native
WebSearch/WebFetch.

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
PERPLEXITY_KEY — credential for the reference implementation
```

## Replacing the implementation

Provide a script satisfying the contract above. The reference
`_scripts/news_perplexity.sh` is intentionally simple — it makes one
API call per verb and returns markdown. A more sophisticated
implementation could cache, batch, or fall back across providers.
