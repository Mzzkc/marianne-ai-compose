#!/usr/bin/env bash
# news_offline.sh — offline news/research for testing the 24x7-trader suite.
# Implements the contract in _techniques/news_research.md with NO network calls.
#
# Every output starts with the contract's offline marker, so downstream agents
# never treat it as fresh. Content comes from a fixture directory when one is
# given, otherwise a fixed placeholder that says so.
#
#   NEWS_FIXTURES  optional absolute path to a directory of markdown files named
#                  <verb>.md or <verb>-<ARG>.md (e.g. catalysts-NVDA.md)
#
# This is a test seam, not a research source. Do not trade on it.

set -euo pipefail

MARK='**[OFFLINE — training data only]**'
die() { echo "news_offline: $*" >&2; exit 1; }

verb="${1:-}"
shift || true

case "$verb" in
  macro_today)
    arg=""
    if [[ "${1:-}" == "--date" ]]; then
      [[ -n "${2:-}" ]] || die "--date needs YYYY-MM-DD"
      arg="${2}"
    elif [[ -n "${1:-}" ]]; then
      die "macro_today takes only [--date YYYY-MM-DD]"
    fi
    ;;
  sector_news|catalysts|peer_compare|regulatory)
    arg="${1:-}"
    [[ -n "$arg" ]] || die "$verb needs one argument"
    [[ "$arg" =~ ^[A-Za-z0-9._-]+$ ]] || die "$verb argument must be a ticker or sector token"
    ;;
  "") die "missing verb (macro_today|sector_news|catalysts|peer_compare|regulatory)" ;;
  *)  die "unknown verb: $verb" ;;
esac

echo "$MARK"
echo
if [[ -n "${NEWS_FIXTURES:-}" ]]; then
  [[ "$NEWS_FIXTURES" == /* ]] || die "NEWS_FIXTURES must be an absolute path"
  for f in "$NEWS_FIXTURES/$verb-$arg.md" "$NEWS_FIXTURES/$verb.md"; do
    if [[ -n "$arg" && -f "$f" ]] || [[ -z "$arg" && "$f" == "$NEWS_FIXTURES/$verb.md" && -f "$f" ]]; then
      cat "$f"
      exit 0
    fi
  done
fi
echo "- No news fixture for \`$verb${arg:+ $arg}\`. Offline test run: treat as no new information."
