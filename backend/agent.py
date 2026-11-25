"""Voice pipeline — Pipecat, one pipeline per call.

Audio flow (exactly what the assignment asks for):

    caller mic -> SmallWebRTC -> Silero VAD -> ElevenLabs STT
               -> Gemini LLM (with the 3 booking tools)
               -> ElevenLabs TTS -> SmallWebRTC -> caller speaker

The transport is SmallWebRTC: plain WebRTC between the browser and this
server, no Daily / telephony account needed. The dashboard posts its SDP
offer to /api/offer and the pipeline starts for that connection.

A small DashboardBridge processor sits after STT and after TTS and
forwards the live transcript to the dashboard WebSocket, so the screen
shows what is being said while the call is happening.
"""
import asyncio

import aiohttp
from loguru import logger

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import (
    Frame,
    LLMRunFrame,
    TranscriptionFrame,
    TTSTextFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.services.elevenlabs.stt import ElevenLabsSTTService
from pipecat.services.elevenlabs.tts import ElevenLabsTTSService
from pipecat.services.google.llm import GoogleLLMService
from pipecat.transports.base_transport import TransportParams
from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection
from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport
from pipecat.workers.runner import WorkerRunner

from . import config, events
from .tools import SYSTEM_PROMPT, TOOLS


class DashboardBridge(FrameProcessor):
    """Forwards transcript frames to the dashboard without changing the audio."""

    def __init__(self, who: str):
        super().__init__()
        self._who = who

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, TranscriptionFrame):
            events.publish("transcript", {"who": "caller", "text": frame.text})
        elif isinstance(frame, TTSTextFrame):
            events.publish("transcript", {"who": "agent", "text": frame.text})
        await self.push_frame(frame, direction)


async def run_pipeline(connection: SmallWebRTCConnection) -> None:
    """Run one Pipecat pipeline for one WebRTC connection. Ends on hang-up."""
    if not config.VOICE_READY:
        logger.error("Voice keys missing: set ELEVENLABS_API_KEY and GEMINI_API_KEY.")
        return

    transport = SmallWebRTCTransport(
        webrtc_connection=connection,
        params=TransportParams(audio_in_enabled=True, audio_out_enabled=True),
    )

    session = aiohttp.ClientSession()
    try:
        stt = ElevenLabsSTTService(
            api_key=config.ELEVENLABS_API_KEY,
            aiohttp_session=session,
            settings=ElevenLabsSTTService.Settings(model=config.ELEVENLABS_STT_MODEL),
        )
        llm = GoogleLLMService(
            api_key=config.GEMINI_API_KEY,
            settings=GoogleLLMService.Settings(
                model=config.GEMINI_MODEL,
                system_instruction=SYSTEM_PROMPT,
                temperature=0.7,
            ),
        )
        tts = ElevenLabsTTSService(
            api_key=config.ELEVENLABS_API_KEY,
            settings=ElevenLabsTTSService.Settings(
                voice=config.ELEVENLABS_VOICE_ID,
                model=config.ELEVENLABS_TTS_MODEL,
            ),
        )

        context = LLMContext(tools=TOOLS)
        user_agg, assistant_agg = LLMContextAggregatorPair(
            context,
            user_params=LLMUserAggregatorParams(vad_analyzer=SileroVADAnalyzer()),
        )

        pipeline = Pipeline([
            transport.input(),
            stt,
            DashboardBridge("caller"),
            user_agg,
            llm,
            tts,
            DashboardBridge("agent"),
            transport.output(),
            assistant_agg,
        ])

        worker = PipelineWorker(
            pipeline,
            params=PipelineParams(enable_metrics=True, enable_usage_metrics=True),
        )
        runner = WorkerRunner(handle_sigint=False, handle_sigterm=False)
        await runner.add_workers(worker)

        @transport.event_handler("on_client_connected")
        async def on_client_connected(transport, client):
            logger.info("Voice call connected")
            events.publish("call_state", {"state": "connected"})
            context.add_message(
                {"role": "developer",
                 "content": "Greet the caller as Maya from CityCare Clinic and ask for their phone number."})
            await worker.queue_frames([LLMRunFrame()])

        @transport.event_handler("on_client_disconnected")
        async def on_client_disconnected(transport, client):
            logger.info("Voice call ended")
            events.publish("call_state", {"state": "ended"})
            await runner.cancel()

        await runner.run()
    finally:
        await session.close()
