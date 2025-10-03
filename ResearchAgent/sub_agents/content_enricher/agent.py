# agent.py
from .prompt import content_enricher_PROMPT
from google.adk.agents import LlmAgent
MODEL = "gemini-2.5-pro"

content_enricher = LlmAgent(
    name="content_enricher",
    model=MODEL,
    instruction=content_enricher_PROMPT,
    output_key="enriched_content",
)

