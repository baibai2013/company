from agents_v2.shared.smart_graph import build_smart_agent
from agents_v2.mechanical.prompts import SYSTEM_PROMPT

def build_agent(checkpointer):
    return build_smart_agent(SYSTEM_PROMPT, checkpointer)
