"""
GDPR routing for every Vercel AI Gateway call (chat and embeddings).

Without this the Gateway may send a request to any provider that serves the
model, including DeepSeek's own servers in China. Pinned to Azure only
("only" disables fallback to any other provider; a request fails rather
than leaving Azure), with zero-data-retention routing on. Azure serves both
deepseek/deepseek-v4-flash and openai/text-embedding-3-small on the Gateway
with ZDR and no-training (checked against the Gateway's
/v1/models/<model>/endpoints on 2026-11-21).

Pass as extra_body= on the OpenAI client call.
"""

GATEWAY_ROUTING = {
    "providerOptions": {
        "gateway": {
            "order": ["azure"],
            "only": ["azure"],
            "zeroDataRetention": True,
        }
    }
}
