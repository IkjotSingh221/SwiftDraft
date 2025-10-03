
# from google.adk.agents import LlmAgent
# from .prompt import format_interpreter_PROMPT

# MODEL = "gemini-2.5-flash"

# format_interpreter = LlmAgent(
#     name="format_interpreter",
#     model=MODEL,
#     description=(
#         "Understands the required output format requested by the user. "
#         "It ensures that the content processed by other agents is returned "
#         "in the correct format (Markdown, summary, bullet points, research outline, etc.) "
#         "as explicitly asked by the user."
#     ),
#     instruction=format_interpreter_PROMPT,
#     output_key="formatted_output",
# )


# format_interpreter/agent.py
from google.adk.agents import LlmAgent
from .prompt import format_interpreter_PROMPT
MODEL = "gemini-2.5-flash"

format_interpreter = LlmAgent(
    name="format_interpreter",
    model=MODEL,
    description="Produces a machine-readable format schema (YAML) for LaTeX output.",
    instruction=format_interpreter_PROMPT,
    output_key="format_schema",
)
