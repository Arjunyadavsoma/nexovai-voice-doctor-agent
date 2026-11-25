/* CityCare dashboard — REST + WebSocket + WebRTC call client. */
const $ = (id) => document.getElementById(id);
const sessionId = "web-" + Math.random().toString(36).slice(2, 8);

let voiceReady = false;
let pc = null, micStream = null, remoteStream = null, pcId = null;
let pendingCandidates = [];
let callTimer = null, callSeconds = 0, muted = false;
let audioCtx = null, analyser = null, waveRAF = null;
let ringCtx = null, ringTimer = null;

/* ------------------------------------------------------------ data */
async function api(path, opts) {
  const res = await fetch(path, opts);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || data.detail || `HTTP ${res.status}`);
  return data;
}

async function refreshAll() {
  const [status, doctors, appointments] = await Promise.all([
    api("/api/status"), api("/api/doctors"), api("/api/appointments"),
  ]);
  voiceReady = status.voice_ready;
  const pill = $("agent-status");
  pill.textContent = voiceReady ? "Agent ready — voice" : "Agent ready — text demo";
  pill.className = "pill " + (voiceReady ? "ready" : "demo");
  $("stat-doctors").textContent = doctors.length;
  $("stat-slots").textContent = doctors.reduce((n, d) => n + d.open_slots, 0);
  $("stat-bookings").textContent = appointments.filter((a) => a.status === "booked").length;
  renderDoctors(doctors);
  renderAppointments(appointments);
}

function localDateStr(d = new Date()) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}
function prettySlot(when) {
  const [date, time] = when.split(" ");
  const today = localDateStr();
  const tomorrow = localDateStr(new Date(Date.now() + 86400000));
  if (date === today) return `Today ${time}`;
  if (date === tomorrow) return `Tomorrow ${time}`;
  return when;
}

async function renderDoctors(doctors) {
  const wrap = $("doctors");
  wrap.innerHTML = "";
  for (const d of doctors) {
    const slots = await api(`/api/availability?doctor_id=${d.id}`).catch(() => []);
    const today = slots.filter((s) => s.date === localDateStr());
    const card = document.createElement("div");
    card.className = "doctor";
    card.innerHTML = `<h3>${d.name}</h3>
      <span class="spec">${d.speciality}</span>
      <span class="fee">₹${d.fee_inr} consultation</span>
      <span class="next">${d.next_slot ? "Next open: " + prettySlot(d.next_slot) : "No open slots"}</span>
      <div class="slots">${today.slice(0, 6).map((s) =>
        `<span class="slot today">${s.time}</span>`).join("")}
        ${slots.length > today.length ? `<span class="slot-more">+${slots.length - today.length} more</span>` : ""}
      </div>`;
    wrap.appendChild(card);
  }
}

function renderAppointments(list) {
  const body = $("appointments");
  body.innerHTML = "";
  $("no-appointments").hidden = list.length > 0;
  for (const a of list) {
    const tr = document.createElement("tr");
    tr.id = "apt-" + a.id;
    tr.innerHTML = `<td>${a.id}</td><td>${a.doctor_name}<br><small>${a.speciality}</small></td>
      <td>${a.date}</td><td>${a.time}</td><td>${a.phone}</td>
      <td><span class="status-${a.status}">${a.status}</span></td>
      <td>${a.status === "booked"
        ? `<button class="cancel-btn" data-id="${a.id}" data-phone="${a.phone}">Cancel</button>` : ""}</td>`;
    body.appendChild(tr);
  }
  body.querySelectorAll(".cancel-btn").forEach((btn) => {
    btn.addEventListener("click", async () => {
      if (!btn.classList.contains("armed")) {
        btn.classList.add("armed"); btn.textContent = "Confirm cancel?";
        setTimeout(() => { btn.classList.remove("armed"); btn.textContent = "Cancel"; }, 3000);
        return;
      }
      btn.disabled = true;
      const res = await api("/api/cancel", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ phone: btn.dataset.phone, appointment_id: btn.dataset.id }),
      }).catch((e) => ({ ok: false, error: e.message }));
      if (!res.ok) alert(res.error || "Could not cancel.");
      await refreshAll();
    });
  });
}

function flashRow(id) {
  const tr = $(`apt-${id}`);
  if (!tr) { refreshAll(); return; }
  tr.classList.remove("flash"); void tr.offsetWidth; tr.classList.add("flash");
  setTimeout(refreshAll, 1200);
}

/* ------------------------------------------------------------ live feed */
function connectWS() {
  const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.event === "snapshot") { renderAppointments(msg.appointments); return; }
    if (msg.event === "transcript") { addLine(msg.who, msg.text); return; }
    if (msg.event === "appointment_booked") {
      showBookingCard(msg.appointment); flashRow(msg.appointment.id); return;
    }
    if (msg.event === "appointment_cancelled") { flashRow(msg.appointment.id); return; }
    if (msg.event === "call_state" && msg.state === "ended") endCallUI("Call ended");
  };
  ws.onclose = () => setTimeout(connectWS, 3000);
}
connectWS();

/* ------------------------------------------------------------ phone UI */
function addLine(who, text) {
  if (!text) return;
  const box = $("transcript");
  const div = document.createElement("div");
  div.className = "line " + (who === "agent" ? "agent" : who === "caller" ? "caller" : "system");
  div.textContent = text;
  box.appendChild(div);
  box.scrollTop = box.scrollHeight;
}

function showBookingCard(a) {
  $("booking-card").hidden = false;
  $("booking-card-doctor").textContent = a.doctor_name;
  $("booking-card-when").textContent = `${a.date} at ${a.time}`;
  $("booking-card-id").textContent = "Booking ID " + a.id;
}

function setCallState(text) { $("call-state").textContent = text; }

function startTimer() {
  callSeconds = 0;
  $("call-timer").textContent = "00:00";
  callTimer = setInterval(() => {
    callSeconds += 1;
    $("call-timer").textContent =
      String(Math.floor(callSeconds / 60)).padStart(2, "0") + ":" +
      String(callSeconds % 60).padStart(2, "0");
  }, 1000);
}
function stopTimer() { clearInterval(callTimer); callTimer = null; }

/* Ringtone + end tone, WebAudio only — no audio files. */
function startRing() {
  ringCtx = ringCtx || new (window.AudioContext || window.webkitAudioContext)();
  let on = false;
  const beep = () => {
    if (!ringCtx) return;
    const o = ringCtx.createOscillator(), g = ringCtx.createGain();
    o.frequency.value = 880; g.gain.value = 0.04;
    o.connect(g).connect(ringCtx.destination);
    o.start(); o.stop(ringCtx.currentTime + 0.18);
  };
  ringTimer = setInterval(() => { on = !on; if (on) { beep(); setTimeout(beep, 220); } }, 900);
}
function stopRing() { clearInterval(ringTimer); ringTimer = null; }
function endTone() {
  if (!ringCtx) return;
  const o = ringCtx.createOscillator(), g = ringCtx.createGain();
  o.frequency.value = 440; g.gain.value = 0.05;
  o.connect(g).connect(ringCtx.destination);
  o.start(); o.stop(ringCtx.currentTime + 0.35);
}

/* Waveform from mic + remote audio. */
function startWaveform() {
  audioCtx = audioCtx || new (window.AudioContext || window.webkitAudioContext)();
  analyser = audioCtx.createAnalyser(); analyser.fftSize = 256;
  const canvas = $("waveform"), ctx = canvas.getContext("2d");
  const data = new Uint8Array(analyser.frequencyBinCount);
  const draw = () => {
    waveRAF = requestAnimationFrame(draw);
    analyser.getByteFrequencyData(data);
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    const barW = canvas.width / 48;
    for (let i = 0; i < 48; i++) {
      const v = data[Math.floor(i * data.length / 48)] / 255;
      const h = 6 + v * 40;
      ctx.fillStyle = v > 0.15 ? "#2f9d8a" : "#33544a";
      ctx.fillRect(i * barW + 1, (canvas.height - h) / 2, barW - 2, h);
    }
  };
  draw();
}
function stopWaveform() { cancelAnimationFrame(waveRAF); waveRAF = null; }

function openPhone() {
  $("phone-overlay").hidden = false;
  $("transcript").innerHTML = "";
  $("booking-card").hidden = true;
  $("demo-form").style.display = "flex";
}
function closePhone() { $("phone-overlay").hidden = true; }

/* ------------------------------------------------------------ voice call */
async function startVoiceCall() {
  openPhone();
  setCallState("Ringing…");
  startRing();
  try {
    micStream = await navigator.mediaDevices.getUserMedia({ audio: true });
    pc = new RTCPeerConnection();
    micStream.getTracks().forEach((t) => pc.addTrack(t, micStream));
    pc.ontrack = (ev) => {
      remoteStream = ev.streams[0];
      $("remote-audio").srcObject = remoteStream;
      if (audioCtx) {
        const src = audioCtx.createMediaStreamSource(remoteStream);
        src.connect(analyser);
      }
    };
    pc.onicecandidate = (ev) => {
      if (!ev.candidate) return;
      if (!pcId) { pendingCandidates.push(ev.candidate.toJSON()); return; }
      fetch("/api/offer", {
        method: "PATCH", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ pc_id: pcId, candidates: [ev.candidate.toJSON()] }),
      }).catch(() => {});
    };
    const offer = await pc.createOffer();
    await pc.setLocalDescription(offer);
    setCallState("Connecting…");
    const answer = await api("/api/offer", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sdp: offer.sdp, type: offer.type }),
    });
    pcId = answer.pc_id;
    await pc.setRemoteDescription(new RTCSessionDescription(answer));
    if (pendingCandidates.length) {
      fetch("/api/offer", {
        method: "PATCH", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ pc_id: pcId, candidates: pendingCandidates }),
      }).catch(() => {});
      pendingCandidates = [];
    }
    stopRing(); setCallState("Connected"); startTimer(); startWaveform();
    const micSrc = audioCtx.createMediaStreamSource(micStream);
    micSrc.connect(analyser);
  } catch (err) {
    stopRing();
    addLine("system", err.message || "Voice call failed. Type to Maya below instead.");
    setCallState("Demo — text");
    startDemoGreeting();
  }
}

function startDemoGreeting() {
  addLine("agent", "Hello, this is Maya from CityCare Clinic. Could you share your 10-digit phone number first? That is how I find your bookings.");
  setCallState("Demo — text");
  startTimer();
}

async function endCall() {
  stopRing(); stopTimer(); stopWaveform(); endTone();
  if (pc) { pc.close(); pc = null; }
  if (micStream) { micStream.getTracks().forEach((t) => t.stop()); micStream = null; }
  setCallState("Call ended");
  setTimeout(closePhone, 900);
}
function endCallUI(reason) {
  if (!$("phone-overlay").hidden) { stopTimer(); setCallState(reason || "Call ended"); setTimeout(closePhone, 900); }
}

$("try-call").addEventListener("click", async () => {
  await refreshAll();
  if (voiceReady) startVoiceCall();
  else { openPhone(); startDemoGreeting(); }
});

$("end-call").addEventListener("click", endCall);

$("mute-btn").addEventListener("click", () => {
  muted = !muted;
  if (micStream) micStream.getAudioTracks().forEach((t) => { t.enabled = !muted; });
  $("mute-btn").setAttribute("aria-pressed", String(muted));
  $("mute-btn").textContent = muted ? "Unmute" : "Mute";
});

$("demo-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const input = $("demo-input");
  const text = input.value.trim();
  if (!text) return;
  input.value = "";
  const res = await api("/api/chat", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId, text }),
  }).catch((e) => ({ reply: "Sorry — " + e.message }));
  if (res.booking) {
    showBookingCard({ id: res.booking.appointment_id,
                      doctor_name: res.booking.doctor_name,
                      date: res.booking.date, time: res.booking.time });
  }
});

refreshAll();
