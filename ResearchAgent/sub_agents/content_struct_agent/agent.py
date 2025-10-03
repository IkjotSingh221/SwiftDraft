
# """
# Sub-Agent 1: Content Extraction & Structuring
# Agent Name: ContentStructAgent

# Responsibilities:
# - Extract content from raw medical research text
# - Correct grammar and remove inconsistencies
# - Organize information into structured sections:
#   Abstract, Background, Methodology, Results, Discussion, Conclusion
# """

# from google.adk.agents import LlmAgent
# from google.adk.tools.agent_tool import AgentTool

# from .prompt import content_struct_PROMPT

# MODEL = "gemini-2.5-flash"

# content_struct = LlmAgent(
#     name="content_struct_agent",
#     model=MODEL,
#     description=(
#         "Extracts and structures content from raw research documents. "
#         "It corrects grammar, removes inconsistencies, and organizes "
#         "the text into standard research paper sections: Abstract, "
#         "Background, Methodology, Results, Discussion, Conclusion."
#     ),
#     instruction=content_struct_PROMPT,
#     output_key="structured_content",
# )

# content_struct/agent.py
from google.adk.agents import LlmAgent
from .prompt import content_struct_PROMPT
MODEL = "gemini-2.5-flash"

content_struct = LlmAgent(
    name="content_struct_agent",
    model=MODEL,
    description="Extracts and structures clinical research text and emits YAML+Markdown.",
    instruction=content_struct_PROMPT,
    output_key="structured_content",
)
