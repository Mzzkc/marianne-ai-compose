#!/usr/bin/env bash
# news_perplexity.sh — reference news/research implementation using Perplexity.
# Implements the contract in _techniques/news_research.md.
#
# Required env: PERPLEXITY_KEY

set -euo pipefail

KEY="${PERPLEXITY_KEY:-}"
[[ -n "$KEY" ]] || { echo "news_perplexity: PERPLEXITY_KEY required" >&2; exit 1; }

# One helper — single sonar query, returns markdown body
ask() {
  local prompt="$1"
  curl -fsS https://api.perplexity.ai/chat/completions \
    -H "Authorization: Bearer ${KEY}" \
    -H "Content-Type: application/json" \
    -d "$(jq -n --arg p "$prompt" '{model:"sonar", messages:[{role:"user", content:$p}]}')" \
    | jq -r '.choices[0].message.content'
}

verb="${1:-}"
shift || true

case "$verb" in
  macro_today)
    date_arg="${2:-$(date +%F)}"
    ask "Summarize today's macro signals for US equities (rates, inflation, employment, central bank posture, sector rotation, breadth) for $date_arg. Markdown bullets, cite each claim with a URL."
    ;;
  sector_news)
    sector="${1:?sector required}"
    ask "Last 24 hours of news for the $sector sector affecting US equities. Markdown digest, group by theme, cite URLs."
    ;;
  catalysts)
    ticker="${1:?ticker required}"
    ask "Upcoming and recent catalysts for $ticker (earnings dates, product launches, regulatory milestones, analyst actions). Markdown bullets with dates and sources."
    ;;
  peer_compare)
    ticker="${1:?ticker required}"
    ask "Peer-relative performance for $ticker over the last 30 days. Identify top 3 sector peers, compare on price, valuation, and a one-line strength summary. Markdown."
    ;;
  regulatory)
    ticker="${1:?ticker required}"
    ask "Recent regulatory filings or actions affecting $ticker. Markdown bullets with dates and source URLs."
    ;;
  *)
    echo "news_perplexity: unknown verb: $verb" >&2
    exit 1
    ;;
esac
