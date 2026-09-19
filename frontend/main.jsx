import React, { useCallback, useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { VoiceBeam, useMicrophone } from "voice-glow";
import { ChecklistSection } from "./components/ChecklistSection.jsx";
import "./bencho-components.css";
import "./voice-input.css";
import "./app.js";

function getSpeechRecognition() {
  return window.SpeechRecognition || window.webkitSpeechRecognition;
}

function VoiceTranscript() {
  const microphone = useMicrophone();
  const { stream, state: microphoneState, start: startMicrophone, stop: stopMicrophone } = microphone;
  const recognitionRef = useRef(null);
  const finalTranscriptRef = useRef("");
  const transcriptRef = useRef("");
  const [captureState, setCaptureState] = useState("idle");
  const [transcript, setTranscript] = useState("");
  const [message, setMessage] = useState("Press ⌘J to begin.");
  const [theme, setTheme] = useState(() => document.documentElement.dataset.theme === "dark" ? "dark" : "light");

  const releaseRecognition = useCallback(() => {
    const recognition = recognitionRef.current;
    recognitionRef.current = null;
    if (recognition) {
      recognition.onend = null;
      recognition.stop();
    }
  }, []);

  const stopCapture = useCallback((nextMessage = "Transcript paused.") => {
    releaseRecognition();
    stopMicrophone();
    setCaptureState("idle");
    setMessage(nextMessage);
  }, [releaseRecognition, stopMicrophone]);

  const startCapture = useCallback(async () => {
    if (captureState === "starting" || captureState === "live") return;

    const Recognition = getSpeechRecognition();
    if (!Recognition) {
      setCaptureState("error");
      setTranscript("");
      setMessage("Live transcription is not available in this browser.");
      return;
    }

    setCaptureState("starting");
    setTranscript("");
    finalTranscriptRef.current = "";
    setMessage("Starting microphone.");

    const recognition = new Recognition();
    recognition.continuous = true;
    recognition.interimResults = true;
    recognition.lang = navigator.language || "en-US";
    recognitionRef.current = recognition;

    recognition.onresult = (event) => {
      let interim = "";
      let finalText = finalTranscriptRef.current;
      for (let index = event.resultIndex; index < event.results.length; index += 1) {
        const spoken = event.results[index][0].transcript;
        if (event.results[index].isFinal) finalText += `${spoken} `;
        else interim += spoken;
      }
      finalTranscriptRef.current = finalText;
      const nextTranscript = `${finalText}${interim}`.trim();
      transcriptRef.current = nextTranscript;
      setTranscript(nextTranscript);
      setCaptureState("live");
      setMessage("Listening");
    };

    recognition.onerror = (event) => {
      if (recognitionRef.current !== recognition) return;
      recognitionRef.current = null;
      stopMicrophone();
      setCaptureState("error");
      const errorMessages = {
        "not-allowed": "Microphone permission was not granted.",
        "service-not-allowed": "Live transcription was blocked by the browser.",
        "no-speech": "No speech was heard. Press ⌘J to try again."
      };
      setMessage(errorMessages[event.error] || "Voice input could not start.");
    };

    recognition.onend = () => {
      if (recognitionRef.current !== recognition) return;
      recognitionRef.current = null;
      stopMicrophone();
      setCaptureState("idle");
      setMessage(transcriptRef.current ? "Transcript paused." : "Listening ended. Press ⌘J to try again.");
    };

    try {
      // Both requests start inside the Cmd/Ctrl+J user gesture. VoiceBeam receives the
      // stream, while the browser's speech recognizer supplies the visible transcript.
      const microphoneRequest = startMicrophone();
      recognition.start();
      const microphoneStream = await microphoneRequest;
      if (!microphoneStream && recognitionRef.current === recognition) {
        recognitionRef.current = null;
        recognition.stop();
        setCaptureState("error");
        setMessage("Microphone access is unavailable.");
      }
    } catch {
      if (recognitionRef.current === recognition) {
        recognitionRef.current = null;
        recognition.stop();
      }
      stopMicrophone();
      setCaptureState("error");
      setMessage("Voice input could not start.");
    }
  }, [captureState, startMicrophone, stopMicrophone]);

  useEffect(() => {
    const target = document.documentElement;
    const updateTheme = () => setTheme(target.dataset.theme === "dark" ? "dark" : "light");
    const observer = new MutationObserver(updateTheme);
    observer.observe(target, { attributes: true, attributeFilter: ["data-theme"] });
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    const dialog = document.querySelector("#voice-command-dialog");
    const resetOnClose = () => {
      stopCapture("Transcript paused.");
      setTranscript("");
      finalTranscriptRef.current = "";
      transcriptRef.current = "";
    };
    dialog?.addEventListener("close", resetOnClose);
    return () => {
      dialog?.removeEventListener("close", resetOnClose);
      stopCapture();
    };
  }, [stopCapture]);

  useEffect(() => {
    const openVoiceTranscript = () => {
      const dialog = document.querySelector("#voice-command-dialog");
      if (!dialog || dialog.open) return;
      setTranscript("");
      finalTranscriptRef.current = "";
      transcriptRef.current = "";
      setMessage("Starting microphone.");
      dialog.showModal();
      void startCapture();
    };
    window.semesterOpenVoiceCommand = openVoiceTranscript;
    return () => {
      if (window.semesterOpenVoiceCommand === openVoiceTranscript) delete window.semesterOpenVoiceCommand;
    };
  }, [startCapture]);

  const live = captureState === "starting" || captureState === "live" || microphoneState === "requesting";
  const displayText = transcript || (live ? "Listening…" : message);

  return <div className="voice-transcript-ui">
    <div className="dialog-heading voice-transcript-heading">
      <div>
        <span className="section-label">Voice</span>
        <h2 id="voice-command-title">Voice</h2>
      </div>
      <button className="icon-button" type="button" onClick={() => document.querySelector("#voice-command-dialog")?.close()}>Close</button>
    </div>

    <VoiceBeam
      type="default"
      stream={stream}
      level={() => live ? 0.08 : 0}
      active={live}
      colorVariant="colorful"
      theme={theme}
      borderRadius={0}
      strength={0.62}
      idle={live ? 0.07 : 0}
      flow={34}
      reach={0.95}
      spread={0.8}
    >
      <section className={`voice-transcript-stage is-${captureState}`} aria-live="polite" aria-atomic="true">
        <p className="voice-transcript-copy" data-empty={!transcript || undefined}>{displayText}</p>
      </section>
    </VoiceBeam>

    <p className="voice-transcript-status">{live ? "Listening" : message}</p>
    <p className="voice-transcript-hint">Press Esc to stop.</p>
  </div>;
}

const voiceRoot = document.querySelector("#voice-command-root");
if (voiceRoot) createRoot(voiceRoot).render(<VoiceTranscript />);

let checklistRoot = null;
window.semesterRenderChecklist = (host, tasks) => {
  if (!host) return;
  if (!checklistRoot || checklistRoot.host !== host) {
    checklistRoot?.root.unmount();
    checklistRoot = { host, root: createRoot(host) };
  }
  checklistRoot.root.render(
    <ChecklistSection
      tasks={tasks}
      onToggle={(id) => window.dispatchEvent(new CustomEvent("semester:checklist-toggle", { detail: { id } }))}
    />,
  );
};
window.dispatchEvent(new Event("semester:checklist-ready"));
