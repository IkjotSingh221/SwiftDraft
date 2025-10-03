import os
from dotenv import load_dotenv
load_dotenv()

from google.adk.agents import Agent, SequentialAgent, ParallelAgent
from google.adk.tools.agent_tool import AgentTool
# from .sub_agents import content_struct, format_interpreter, latex_agent, content_integrator
from .prompt import ROOT_AGENT_PROMPT
# from .sub_agents.academic_newresearch import academic_newresearch_agent
# from .sub_agents.from .sub_agents.content_struct_agent import agent as content_struct
from .sub_agents.content_struct_agent.agent import content_struct
from .sub_agents.format_interpreter_agent.agent import format_interpreter
from .sub_agents.latex_agent.agent import latex_agent
from .sub_agents.content_integrator_agent.agent import content_integrator
from .sub_agents.content_enricher.agent import content_enricher


MODEL = "gemini-2.5-flash"

# par_agent = ParallelAgent(
#     name="extraction_agent",
#     sub_agents=[content_struct, format_interpreter],
#     description="Runs multiple research agents in parallel to gather information.",
# )

seq_agent=SequentialAgent(
    name="ResearchPipelineAgent",
    sub_agents=[content_struct,content_enricher, format_interpreter, content_integrator, latex_agent],
    description="Executes a sequence of code writing, reviewing, and refactoring.",
    # The agents will run in the order provided: Writer -> Reviewer -> Refactorer
)


root_agent = Agent(
    name="root_agent",
    model=MODEL,
    description=(
        "Root agent that coordinates academic research tasks."
    ),
    instruction=ROOT_AGENT_PROMPT,
    sub_agents=[seq_agent]
)

