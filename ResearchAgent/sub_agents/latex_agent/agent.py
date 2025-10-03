# # agent.py

# from google.adk.agents import LlmAgent
# from .prompt import latex_PROMPT

# MODEL = "gemini-2.5-flash"

# latex_agent = LlmAgent(
#     name="latex_agent",
#     model=MODEL,
#     description=(
#         "Assembles enriched academic content into a cohesive document, "
#         "applies formatting rules, and outputs the result as a LaTeX file."
#     ),
#     instruction=latex_PROMPT,
#     output_key="latex_document",
# )

# latex_agent/agent.py
# from google.adk.agents import LlmAgent
# from .prompt import latex_PROMPT
# MODEL = "gemini-2.5-pro"

# latex_agent = LlmAgent(
#     name="latex_agent",
#     model=MODEL,
#     description="Converts enriched markdown + schema to compile-ready LaTeX source.",
#     instruction=latex_PROMPT,
#     output_key="latex_document",
# )

from google.adk.agents import LlmAgent
from .prompt import latex_PROMPT
from ResearchAgent.tools.latex_to_pdf import latex_to_pdf

MODEL = "gemini-2.5-pro"

latex_agent = LlmAgent(
    name="latex_agent",
    model=MODEL,
    description="Converts enriched markdown + schema to compile-ready LaTeX source.",
    instruction=latex_PROMPT,
    output_key="latex_document",
    tools=[latex_to_pdf],   # now it can auto-compile into PDF
)
