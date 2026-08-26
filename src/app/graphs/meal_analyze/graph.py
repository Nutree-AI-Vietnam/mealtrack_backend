"""Builder for the meal image analysis graph."""

from typing import Any

from langgraph.graph import END, START, StateGraph

from src.app.graphs.meal_analyze.nodes import (
    acquire_image,
    analyze_vision,
    complete,
    maybe_validate_reference,
    parse_nutrition,
    persist_meal,
    prepare_input,
    select_mode,
)
from src.app.graphs.meal_analyze.runtime import MealAnalyzeRuntime
from src.app.graphs.meal_analyze.state import MealAnalyzeGraphState


def build_meal_analyze_graph() -> Any:
    """Build the default-off meal analysis graph scaffold."""
    graph = StateGraph(MealAnalyzeGraphState)
    graph.add_node("prepare_input", prepare_input)
    graph.add_node("select_mode", select_mode)
    graph.add_node("complete", complete)
    graph.add_edge(START, "prepare_input")
    graph.add_edge("prepare_input", "select_mode")
    graph.add_edge("select_mode", "complete")
    graph.add_edge("complete", END)
    return graph.compile()


def build_runtime_meal_analyze_graph(runtime: MealAnalyzeRuntime) -> Any:
    """Build the meal analysis graph with per-request runtime dependencies."""
    async def acquire_image_node(
        state: MealAnalyzeGraphState,
    ) -> MealAnalyzeGraphState:
        return await acquire_image(state, runtime)

    async def analyze_vision_node(
        state: MealAnalyzeGraphState,
    ) -> MealAnalyzeGraphState:
        return await analyze_vision(state, runtime)

    async def parse_nutrition_node(
        state: MealAnalyzeGraphState,
    ) -> MealAnalyzeGraphState:
        return await parse_nutrition(state, runtime)

    async def maybe_validate_reference_node(
        state: MealAnalyzeGraphState,
    ) -> MealAnalyzeGraphState:
        return await maybe_validate_reference(state, runtime)

    async def persist_meal_node(
        state: MealAnalyzeGraphState,
    ) -> MealAnalyzeGraphState:
        return await persist_meal(state, runtime)

    graph = StateGraph(MealAnalyzeGraphState)
    graph.add_node("prepare_input", prepare_input)
    graph.add_node("acquire_image", acquire_image_node)
    graph.add_node("select_mode", select_mode)
    graph.add_node("complete", complete)
    graph.add_edge(START, "prepare_input")
    graph.add_edge("prepare_input", "acquire_image")
    graph.add_edge("acquire_image", "select_mode")
    if runtime.has_analysis_dependencies():
        graph.add_node("analyze_vision", analyze_vision_node)
        graph.add_node("parse_nutrition", parse_nutrition_node)
        graph.add_node("maybe_validate_reference", maybe_validate_reference_node)
        graph.add_node("persist_meal", persist_meal_node)
        graph.add_edge("select_mode", "analyze_vision")
        graph.add_edge("analyze_vision", "parse_nutrition")
        graph.add_edge("parse_nutrition", "maybe_validate_reference")
        graph.add_edge("maybe_validate_reference", "persist_meal")
        graph.add_edge("persist_meal", "complete")
    else:
        graph.add_edge("select_mode", "complete")
    graph.add_edge("complete", END)
    return graph.compile()


def run_meal_analyze_graph(
    state: MealAnalyzeGraphState,
) -> MealAnalyzeGraphState:
    """Run the scaffold graph synchronously for the current API contract."""
    graph = build_meal_analyze_graph()
    return graph.invoke(state)


async def run_meal_analyze_graph_async(
    state: MealAnalyzeGraphState,
    runtime: MealAnalyzeRuntime,
) -> MealAnalyzeGraphState:
    """Run the runtime-bound graph for acquisition and later orchestration nodes."""
    graph = build_runtime_meal_analyze_graph(runtime)
    return await graph.ainvoke(state)
