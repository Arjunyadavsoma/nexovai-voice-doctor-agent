# Pipeline description — in my own words

I built a voice agent for booking doctor appointments, and a dashboard to
watch it work.

When you click Try Call, your browser opens a WebRTC connection straight
to the Python server — no phone network, no Daily account. Your voice
goes through Pipecat, which is the framework that holds the whole call
together as a pipeline: microphone audio in, spoken reply out.

Inside the pipeline, Silero VAD first decides when you have actually
stopped speaking, so the agent does not cut you off mid-sentence.
ElevenLabs turns your speech into text. That text goes to Google Gemini
(2.5 Flash, on the free tier), which decides what to say next and whether
it needs to do something. If it needs facts, it calls one of three tools
I wrote: check availability, book, or cancel. Those tools read and write
a small SQLite database of doctors, slots and appointments — the same
database the dashboard reads, which is why a booking made on the call
shows up on screen at once. Gemini's reply is turned back into speech
by ElevenLabs and played to you.

The part I was most careful about is identity. There is no login, so
the agent asks for your phone number out loud, reads it back digit by
digit, and only books or cancels after you confirm it. It also confirms
the doctor, date and time before booking, and reads the booking ID back
afterwards, so a mistake is caught on the call, not after it.

The dashboard is plain HTML, CSS and JavaScript served by the same
FastAPI server. It lists the doctors and their open slots, the
appointments, and a phone-style call screen with a timer, mute button,
live transcript, a waveform that moves with the audio, and a booking
card that appears when the agent books. The transcript and the booking
updates travel from the pipeline to the dashboard over a WebSocket.

What I used AI for: I used it to help write the code and the dashboard,
then read through it and tested it myself — the booking rules, the
double-booking check, and the cancel flow. The design choices — the
phone-number read-back, the SQLite store, WebRTC instead of a telephony
provider — are mine, because they are what the task actually asked for.
