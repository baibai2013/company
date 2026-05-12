"""
算法工程师 LangGraph: plan → execute.
"""
from typing import Annotated, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

from agents_v2.shared.claude_client import make_langchain_llm
from agents_v2.algorithm.prompts import SYSTEM_PROMPT


class AgentState(TypedDict):
    messages: Annotated[list, add_messages]
    task_input: str
    plan: str
    execution_result: str


def plan_node(state: AgentState) -> dict:
    llm = make_langchain_llm()
    resp = llm.invoke([
        SystemMessage(SYSTEM_PROMPT + "\n请分析需求，制定执行方案（100字以内）。"),
        HumanMessage(state["task_input"]),
    ])
    return {"plan": resp.content}


def execute_node(state: AgentState) -> dict:
    llm = make_langchain_llm()
    resp = llm.invoke([
        SystemMessage(SYSTEM_PROMPT),
        HumanMessage(f"执行方案：{state['plan']}\n\n原始需求：{state['task_input']}\n\n请输出完整结果。"),
    ])
    return {"execution_result": resp.content}


def build_agent(checkpointer):
    g = StateGraph(AgentState)
    g.add_node("plan", plan_node)
    g.add_node("execute", execute_node)
    g.add_edge(START, "plan")
    g.add_edge("plan", "execute")
    g.add_edge("execute", END)
    return g.compile(checkpointer=checkpointer)
