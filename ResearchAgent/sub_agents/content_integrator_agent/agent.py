# # agent.py

# from google.adk.agents import LlmAgent
# from .prompt import content_integrator_PROMPT

# MODEL = "gemini-2.5-flash"

# content_integrator = LlmAgent(
#     name="content_integrator",
#     model=MODEL,
#     description=(
#         "Integrates structured academic content with the required format. "
#         "Fills in missing gaps by generating additional medical research content, "
#         "adds necessary context, transitions, and ensures a formal academic tone."
#     ),
#     instruction=content_integrator_PROMPT,
#     output_key="enriched_content",
# )


# content_integrator/agent.py
from google.adk.agents import LlmAgent
from .prompt import content_integrator_PROMPT
MODEL = "gemini-2.5-pro"

content_integrator = LlmAgent(
    name="content_integrator",
    model=MODEL,
    description="Maps structured content to the desired schema and fills gaps with suggested blocks.",
    instruction=content_integrator_PROMPT,
    output_key="integrated_content",
)
