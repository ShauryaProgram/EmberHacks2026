import React, { useCallback, useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { ArrowUp, Mic, Square, X } from "lucide-react";
import { VoiceBeam, useMicrophone } from "voice-glow";
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
  const transcriptAreaRef = useRef(null);
  const responseAreaRef = useRef(null);
  const clarificationContextRef = useRef(null);
  const autoResumeRef = useRef(false);
  const [captureState, setCaptureState] = useState("idle");
  const [transcript, setTranscript] = useState("");
  const [message, setMessage] = useState("Press ⌘J to begin.");
  const [feedback, setFeedback] = useState(null);
  const [submitting, setSubmitting] = useState(false);
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

  const startCapture = useCallback(async ({ preserveFeedback = false } = {}) => {
    if (captureState === "starting" || captureState === "live") return;

    const Recognition = getSpeechRecognition();
    if (!Recognition) {
      setCaptureState("error");
      setMessage("Live transcription is not available in this browser.");
      setFeedback({ tone: "error", title: "Voice unavailable", message: "You can still type your request below." });
      return;
    }

    setCaptureState("starting");
    if (!preserveFeedback && !clarificationContextRef.current) setFeedback(null);
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
      const errorMessage = errorMessages[event.error] || "Voice input could not start.";
      setMessage(errorMessage);
      setFeedback({ tone: "error", title: "I couldn't hear that", message: `${errorMessage} You can type the request instead.` });
    };

    recognition.onend = () => {
      if (recognitionRef.current !== recognition) return;
      recognitionRef.current = null;
      stopMicrophone();
      setCaptureState("idle");
      setMessage(transcriptRef.current ? "Transcript paused." : "Listening ended. Press ⌘J to try again.");
    };

    try {
      // The first request starts inside the Cmd/Ctrl+J user gesture; clarification
      // replies reuse the permission already granted for that conversation.
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
      setFeedback({ tone: "error", title: "Microphone unavailable", message: "Voice input could not start, but you can type the request below." });
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
      clarificationContextRef.current = null;
      autoResumeRef.current = false;
      setFeedback(null);
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
      clarificationContextRef.current = null;
      autoResumeRef.current = false;
      setFeedback(null);
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

  useEffect(() => {
    if (!live) return;
    const frame = requestAnimationFrame(() => {
      const transcriptArea = transcriptAreaRef.current;
      if (transcriptArea) transcriptArea.scrollTop = transcriptArea.scrollHeight;
    });
    return () => cancelAnimationFrame(frame);
  }, [live, transcript]);

  useEffect(() => {
    if (!feedback) return;
    const frame = requestAnimationFrame(() => {
      const responseArea = responseAreaRef.current;
      if (responseArea) responseArea.scrollTop = responseArea.scrollHeight;
    });
    return () => cancelAnimationFrame(frame);
  }, [feedback]);

  useEffect(() => {
    if (submitting || live || feedback?.tone !== "question" || !autoResumeRef.current) return;
    autoResumeRef.current = false;
    void startCapture({ preserveFeedback: true });
  }, [feedback, live, startCapture, submitting]);

  const closeVoice = () => document.querySelector("#voice-command-dialog")?.close();
  const updateTranscript = (value) => {
    if (live) stopCapture("Transcript paused.");
    transcriptRef.current = value;
    finalTranscriptRef.current = value ? `${value.trim()} ` : "";
    setTranscript(value);
    if (!clarificationContextRef.current) setFeedback(null);
    setCaptureState("idle");
  };

  const submitCommand = useCallback(async () => {
    const text = transcriptRef.current.trim();
    if (!text || !window.semesterSubmitNaturalLanguage) return;
    const clarification = clarificationContextRef.current;
    const requestText = clarification
      ? [
          `Original request: ${clarification.request.slice(0, 2800)}`,
          `Ember asked: ${clarification.question.slice(0, 500)}`,
          `User answered: ${text.slice(0, 600)}`,
        ].join("\n")
      : text;
    stopCapture("Working on your calendar.");
    setSubmitting(true);
    setFeedback({ tone: "working", title: "Working on it", message: "Checking dates and your calendar…" });
    try {
      const result = await window.semesterSubmitNaturalLanguage(requestText);
      setMessage(result.message);
      if (result.ok) {
        setTranscript("");
        finalTranscriptRef.current = "";
        transcriptRef.current = "";
        clarificationContextRef.current = null;
        setFeedback({ tone: "success", title: result.changed ? "Calendar updated" : "Here you go", message: result.message });
      } else {
        clarificationContextRef.current = { request: requestText, question: result.message };
        setTranscript("");
        finalTranscriptRef.current = "";
        transcriptRef.current = "";
        autoResumeRef.current = true;
        setFeedback({ tone: "question", title: "One detail needed", message: result.message });
      }
    } catch (error) {
      const errorMessage = error?.message || "The calendar command could not be completed.";
      setMessage(errorMessage);
      setFeedback({ tone: "error", title: "That didn't work", message: errorMessage });
    } finally {
      setSubmitting(false);
    }
  }, [stopCapture]);

  return <div className="voice-console">
    <header className="voice-console-head">
      <div><span className="voice-kicker">Ember assistant</span><h2 id="voice-command-title">What can I help with?</h2></div>
      <button className="voice-icon-button" type="button" onClick={closeVoice} aria-label="Close voice input" title="Close"><X size={18} strokeWidth={1.8} /></button>
    </header>
    <section ref={responseAreaRef} className={`voice-response is-${feedback?.tone || captureState}`}>
      <span className="voice-state"><i aria-hidden="true"></i>{submitting ? "Thinking" : live ? "Listening" : transcript ? "Ready" : "Ask Ember"}</span>
      <textarea ref={transcriptAreaRef} className="voice-transcript" value={transcript} onChange={(event) => updateTranscript(event.target.value)} aria-label="Calendar request" placeholder={feedback?.tone === "question" ? "Answer Ember’s question…" : live ? "Listening…" : "Say or type a calendar request…"} rows="3" />
      {feedback
        ? <div className={`voice-feedback is-${feedback.tone}`} role="status" aria-live="polite"><strong>{feedback.title}</strong><p>{feedback.message}</p></div>
        : <small>“Gym every weekday at 7 AM” · “Delete my dentist appointment tomorrow”</small>}
    </section>
    <footer className="voice-console-footer">
      <div className="voice-orb-control">
        <VoiceBeam type="default" stream={stream} level={() => live ? 0.1 : 0} active={live} colorVariant="colorful" theme={theme} borderRadius={999} strength={0.72} idle={live ? 0.09 : 0} flow={38} reach={1} spread={0.82}>
          <button className={`voice-orb${live ? " is-live" : ""}`} type="button" onClick={() => live ? stopCapture() : void startCapture()} aria-label={live ? "Pause voice input" : "Start voice input"} title={live ? "Pause" : "Speak"}>
            {live ? <Square size={19} fill="currentColor" strokeWidth={1.5} /> : <Mic size={23} strokeWidth={1.8} />}
          </button>
        </VoiceBeam>
        <span>{live ? "Tap to pause" : "Tap to speak"}</span>
      </div>
      <button className="voice-submit" type="button" disabled={!transcript.trim() || submitting} onClick={submitCommand}><span>{submitting ? "Working" : "Send"}</span><ArrowUp size={17} strokeWidth={2} /></button>
    </footer>
  </div>;
}

function VoiceLauncher() {
  return <button className="voice-island" type="button" onClick={() => window.semesterOpenVoiceCommand?.()} aria-label="Speak to Ember" title="Speak to Ember (Command J)">
    <span className="voice-island-icon" aria-hidden="true"><Mic size={17} strokeWidth={1.9} /></span><span>Speak to Ember</span><kbd>⌘J</kbd>
  </button>;
}

const voiceRoot = document.querySelector("#voice-command-root");
if (voiceRoot) createRoot(voiceRoot).render(<VoiceTranscript />);
const voiceLauncherRoot = document.querySelector("#voice-launcher-root");
if (voiceLauncherRoot) createRoot(voiceLauncherRoot).render(<VoiceLauncher />);
