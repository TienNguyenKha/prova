import json

from fastapi import FastAPI, WebSocket
from fastapi.responses import HTMLResponse

app = FastAPI()


@app.get("/twiml")
async def twiml_response():
    """Tell Twilio to start a Media Stream pointing at /media-stream."""
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Connect>
    <Stream url="wss://YOUR_HOST/media-stream"/>
  </Connect>
</Response>"""
    return HTMLResponse(content=xml, media_type="application/xml")


@app.websocket("/media-stream")
async def media_stream(twilio_ws: WebSocket):
    """Bridge between Twilio Media Stream and Gemini Live API."""
    await twilio_ws.accept()
    stream_sid = None

    async with client.aio.live.connect(model=GEMINI_MODEL, config=_make_live_config()) as session:

        async def receive_from_twilio():
            nonlocal stream_sid
            async for raw in twilio_ws.iter_text():
                msg = json.loads(raw)
                event = msg.get("event")
