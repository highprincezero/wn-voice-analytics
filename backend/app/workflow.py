"""Microsoft Agent Framework runner shared by the two in-process workflows."""

import asyncio

from agent_framework import Executor, WorkflowContext, handler


class Step(Executor):
    """Runs one sync function and sends its state to the next node."""

    def __init__(self, step_id: str, fn) -> None:
        super().__init__(id=step_id)
        self._fn = fn

    @handler
    async def handle(self, state: dict, ctx: WorkflowContext[dict]) -> None:
        await ctx.send_message(self._fn(state))


class Finish(Executor):
    """Runs one sync function and yields its state as the workflow result."""

    def __init__(self, step_id: str, fn) -> None:
        super().__init__(id=step_id)
        self._fn = fn

    @handler
    async def handle(self, state: dict, ctx: WorkflowContext[dict, dict]) -> None:
        await ctx.yield_output(self._fn(state))


class Emit(Executor):
    """Yields the state as the result without another step."""

    def __init__(self, step_id: str) -> None:
        super().__init__(id=step_id)

    @handler
    async def handle(self, state: dict, ctx: WorkflowContext[dict, dict]) -> None:
        await ctx.yield_output(state)


def run_workflow(workflow, message: dict) -> dict:
    """Run one workflow and return the state it yielded."""

    async def _finish() -> dict:
        result = await workflow.run(message)
        outputs = result.get_outputs()
        if not outputs:
            raise RuntimeError("workflow finished without a result")
        return outputs[-1]

    # The caller is sync (Celery, or a sync route). A fresh loop each run.
    return asyncio.run(_finish())
