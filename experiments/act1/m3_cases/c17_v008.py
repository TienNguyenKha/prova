import uuid

import anthropic
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="Atlas Agent API", version="0.19")


class AgentRequest(BaseModel):
    message: str
    session_id: str = "default"
    webhook_url: str | None = None


@app.post("/agent/run", response_model=AgentResponse)
async def run_agent(request: AgentRequest):
    """Synchronous: waits for the full response before returning."""
    try:
        result = _run_atlas(request.message)
        return AgentResponse(**result)
    except anthropic.APIError as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.post("/agent/async", response_model=AsyncTaskResponse)
async def async_agent(request: AgentRequest):
    """Enqueue a long-running task. Returns task_id for polling."""
    task = process_agent_task.delay({
        "message": request.message,
        "session_id": request.session_id,
        "task_id": str(uuid.uuid4()),
        "webhook_url": request.webhook_url,
    })
    return AsyncTaskResponse(task_id=task.id, status="queued")


@app.get("/agent/status/{task_id}")
async def get_task_status(task_id: str):
    """Poll for async task result."""
    task = process_agent_task.AsyncResult(task_id)
    return {"task_id": task_id, "status": task.status}
