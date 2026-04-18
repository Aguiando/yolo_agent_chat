import os
import cv2
import threading
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from services.event_repository import init_db, list_events, count_events
from services.video_monitor import start_monitor, get_last_frame, get_camera_status, generate_mjpeg
from services.ollama_client import warmup_model, chat_stream, check_ollama
from services.monitoring_agent import build_agent_messages, get_agent_status
from services.schemas import ChatRequest

# =========================
# APP
# =========================
app = FastAPI(title="AgroVision AI")

os.makedirs("static", exist_ok=True)
os.makedirs("static/captures", exist_ok=True)
os.makedirs("templates", exist_ok=True)

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


# =========================
# STARTUP
# =========================
@app.on_event("startup")
def startup_event():
    init_db()
    start_monitor()
    threading.Thread(target=warmup_model, daemon=True).start()
    print("[APP] AgroVision AI iniciado.")


# =========================
# ROTAS PRINCIPAIS
# =========================
@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    events = list_events(20)
    return templates.TemplateResponse("index.html", {
        "request": request,
        "events": events
    })


@app.get("/health")
def health():
    return {"status": "ok", "service": "AgroVision AI"}


@app.get("/events")
def get_events():
    return JSONResponse(content=list_events(50))


@app.get("/frame")
def get_frame():
    frame = get_last_frame()
    if frame is None:
        return JSONResponse({"message": "Sem frame disponível."}, status_code=503)
    success, buffer = cv2.imencode(".jpg", frame)
    if not success:
        return JSONResponse({"message": "Erro ao converter frame."}, status_code=500)
    return Response(content=buffer.tobytes(), media_type="image/jpeg")


@app.get("/video_feed")
def video_feed():
    return StreamingResponse(
        generate_mjpeg(),
        media_type="multipart/x-mixed-replace; boundary=frame"
    )


# =========================
# CÂMERA
# =========================
@app.get("/camera/status")
def camera_status():
    return JSONResponse(content=get_camera_status())


# =========================
# AGENTE
# =========================
@app.get("/agent/status")
def agent_status():
    events = list_events(50)
    return JSONResponse(content=get_agent_status(events))


@app.post("/chat")
def chat(req: ChatRequest):
    events = list_events(50)
    messages = build_agent_messages(req.question, req.history or [], events)

    def stream_response():
        try:
            for token in chat_stream(messages):
                yield token
        except Exception as e:
            yield f"\n\n[Erro ao conectar com Ollama: {e}]"

    return StreamingResponse(stream_response(), media_type="text/plain")


# =========================
# OLLAMA STATUS
# =========================
@app.get("/ollama/status")
def ollama_status():
    return JSONResponse(content=check_ollama())
