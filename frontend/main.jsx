import React, { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { VoiceBeam } from "voice-glow";
import "./voice-input.css";
import "./app.js";

function VoiceTaskInput() {
  const [title, setTitle] = useState("");
  const [listening, setListening] = useState(false);
  const meter = useRef(0);
  const frame = useRef(0);

  useEffect(() => {
    window.semesterResetVoiceInput = () => {
      setTitle("");
      setListening(false);
    };
    const quickDialog = document.querySelector("#quick-add");
    const resetOnClose = () => {
      setTitle("");
      setListening(false);
    };
    quickDialog?.addEventListener("close", resetOnClose);
    return () => {
      delete window.semesterResetVoiceInput;
      quickDialog?.removeEventListener("close", resetOnClose);
      cancelAnimationFrame(frame.current);
    };
  }, []);

  useEffect(() => {
    cancelAnimationFrame(frame.current);
    if (!listening) {
      meter.current = 0;
      return undefined;
    }
    const animate = (timestamp) => {
      const wave = (Math.sin(timestamp / 145) + Math.sin(timestamp / 257) + 2) / 4;
      meter.current = 0.2 + wave * 0.56;
      frame.current = requestAnimationFrame(animate);
    };
    frame.current = requestAnimationFrame(animate);
    return () => cancelAnimationFrame(frame.current);
  }, [listening]);

  const toggleListening = () => setListening((active) => !active);

  return <div className="voice-input-ui">
    <VoiceBeam
      type="default"
      level={() => meter.current}
      active
      colorVariant="forest"
      theme="light"
      strength={0.64}
      idle={listening ? 0.16 : 0}
      flow={24}
      reach={0.8}
    >
      <div className={`voice-entry ${listening ? "is-listening" : ""}`}>
        <input
          id="task-title"
          name="title"
          value={title}
          onChange={(event) => setTitle(event.target.value)}
          autoComplete="off"
          placeholder="What needs to be done?"
          required
        />
        <button
          className="voice-button"
          type="button"
          onClick={toggleListening}
          aria-pressed={listening}
          aria-label={listening ? "Stop voice input preview" : "Start voice input preview"}
        >
          {listening ? "Stop" : "Listen"}
        </button>
      </div>
    </VoiceBeam>
    <div className="voice-status" aria-live="polite">
      <span className={listening ? "is-live" : ""}>{listening ? "Listening preview" : "Voice input"}</span>
      <span>{listening ? "Visual only for now" : "Type a task or try the preview"}</span>
    </div>
  </div>;
}

const voiceRoot = document.querySelector("#voice-input-root");
if (voiceRoot) createRoot(voiceRoot).render(<VoiceTaskInput />);
