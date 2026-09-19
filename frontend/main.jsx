import React, { useCallback, useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { ArrowUp, Mic, Square, X } from "lucide-react";
import { VoiceBeam } from "voice-glow";
import "./voice-input.css";
import "./app.js";

function getSpeechRecognition() {
  return window.SpeechRecognition || window.webkitSpeechRecognition;
}

function usesSafariSpeechRecognition() {
  return /Apple/i.test(navigator.vendor || "") && /Safari/i.test(navigator.userAgent || "");
}

function VoiceTranscript() {
  const recognitionRef = useRef(null);
  const finalTranscriptRef = useRef("");
  const transcriptRef = useRef("");
  const transcriptAreaRef = useRef(null);
  const responseAreaRef = useRef(null);
  const clarificationContextRef = useRef(null);
  const captureWantedRef = useRef(false);
  const recognitionTimerRef = useRef(null);
  const [captureState, setCaptureState] = useState("idle");
  const [transcript, setTranscript] = useState("");
  const [message, setMessage] = useState("Press ⌘J to begin.");
  const [feedback, setFeedback] = useState(null);
  const [submitting, setSubmitting] = useState(false);
  const [theme, setTheme] = useState(() => document.documentElement.dataset.theme === "dark" ? "dark" : "light");

  const clearRecognitionTimer = useCallback(() => {
    if (recognitionTimerRef.current) clearTimeout(recognitionTimerRef.current);
    recognitionTimerRef.current = null;
  }, []);

  const releaseRecognition = useCallback(() => {
    clearRecognitionTimer();
    const recognition = recognitionRef.current;
    recognitionRef.current = null;
    if (recognition) {
      recognition.onend = null;
      recognition.abort();
    }
  }, [clearRecognitionTimer]);

  const stopCapture = useCallback((nextMessage = "Transcript paused.") => {
    captureWantedRef.current = false;
    releaseRecognition();
    setCaptureState("idle");
    setMessage(nextMessage);
  }, [releaseRecognition]);

  const startCapture = useCallback(({ preserveFeedback = false, retryCount = 0 } = {}) => {
    if (recognitionRef.current) return;

    const Recognition = getSpeechRecognition();
    if (!Recognition) {
      setCaptureState("error");
      setMessage("Live transcription is not available in this browser.");
      setFeedback({ tone: "error", title: "Voice unavailable", message: "You can still type your request below." });
      return;
    }

    captureWantedRef.current = true;
    setCaptureState("starting");
    if (!preserveFeedback && !clarificationContextRef.current) setFeedback(null);
    setMessage(retryCount ? "Retrying dictation." : "Starting microphone.");

    const recognition = new Recognition();
    const safariSpeech = usesSafariSpeechRecognition();
    recognition.continuous = !safariSpeech;
    recognition.interimResults = true;
    recognition.lang = navigator.language || "en-US";
    recognitionRef.current = recognition;

    const failCapture = (errorMessage) => {
      captureWantedRef.current = false;
      clearRecognitionTimer();
      if (recognitionRef.current === recognition) recognitionRef.current = null;
      recognition.onend = null;
      try { recognition.abort(); } catch { /* recognition may not have started */ }
      setCaptureState("error");
      setMessage(errorMessage);
      const clarification = clarificationContextRef.current;
      setFeedback(clarification
        ? { tone: "question", title: "Tap to answer", message: `${clarification.question} Dictation didn't start automatically; tap the microphone and try again, or type your answer.` }
        : { tone: "error", title: "Microphone unavailable", message: `${errorMessage} You can type your request instead.` });
    };

    const retryCapture = () => {
      if (recognitionRef.current !== recognition) return;
      clearRecognitionTimer();
      recognitionRef.current = null;
      recognition.onend = null;
      try { recognition.abort(); } catch { /* recognition may already be ended */ }
      if (!captureWantedRef.current) return;
      if (safariSpeech) {
        failCapture("Safari didn't receive any speech.");
        return;
      }
      if (retryCount >= 2) {
        failCapture("Dictation could not start.");
        return;
      }
      setCaptureState("starting");
      setMessage("Retrying dictation.");
      recognitionTimerRef.current = setTimeout(() => {
        recognitionTimerRef.current = null;
        startCapture({ preserveFeedback: true, retryCount: retryCount + 1 });
      }, 300);
    };

    recognition.onstart = () => {
      if (recognitionRef.current !== recognition) return;
      clearRecognitionTimer();
      setCaptureState("live");
      setMessage("Listening");
      recognitionTimerRef.current = setTimeout(retryCapture, 12000);
    };

    recognition.onresult = (event) => {
      clearRecognitionTimer();
      let interim = "";
      let finalText = finalTranscriptRef.current;
      let hasFinalResult = false;
      for (let index = event.resultIndex; index < event.results.length; index += 1) {
        const spoken = event.results[index][0].transcript;
        if (event.results[index].isFinal) {
          finalText += `${spoken} `;
          hasFinalResult = true;
        }
        else interim += spoken;
      }
      finalTranscriptRef.current = finalText;
      const nextTranscript = `${finalText}${interim}`.trim();
      transcriptRef.current = nextTranscript;
      setTranscript(nextTranscript);
      if (safariSpeech && hasFinalResult) {
        captureWantedRef.current = false;
        recognitionRef.current = null;
        recognition.onend = null;
        recognition.stop();
        setCaptureState("idle");
        setMessage("Ready to send.");
        return;
      }
      setCaptureState("live");
      setMessage("Listening");
      if (safariSpeech) recognitionTimerRef.current = setTimeout(retryCapture, 8000);
    };

    recognition.onerror = (event) => {
      if (recognitionRef.current !== recognition) return;
      const errorMessages = {
        "not-allowed": "Microphone permission was not granted.",
        "service-not-allowed": "Live transcription was blocked by the browser.",
        "audio-capture": "No working microphone was found."
      };
      if (errorMessages[event.error]) failCapture(errorMessages[event.error]);
      else retryCapture();
    };

    recognition.onend = () => {
      if (recognitionRef.current !== recognition) return;
      if (safariSpeech && transcriptRef.current) {
        clearRecognitionTimer();
        captureWantedRef.current = false;
        recognitionRef.current = null;
        setCaptureState("idle");
        setMessage("Ready to send.");
        return;
      }
      retryCapture();
    };

    try {
      // The first request starts inside the Cmd/Ctrl+J user gesture; clarification
      // replies reuse the permission already granted for that conversation.
      recognition.start();
    } catch {
      retryCapture();
      return;
    }
    clearRecognitionTimer();
    recognitionTimerRef.current = setTimeout(retryCapture, 5000);
  }, [clearRecognitionTimer]);

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

  const starting = captureState === "starting";
  const live = captureState === "live";
  const captureActive = starting || live;
  const awaitingAnswer = Boolean(clarificationContextRef.current);

  useEffect(() => {
    if (!captureActive) return;
    const frame = requestAnimationFrame(() => {
      const transcriptArea = transcriptAreaRef.current;
      if (transcriptArea) transcriptArea.scrollTop = transcriptArea.scrollHeight;
    });
    return () => cancelAnimationFrame(frame);
  }, [captureActive, transcript]);

  useEffect(() => {
    if (!feedback) return;
    const frame = requestAnimationFrame(() => {
      const responseArea = responseAreaRef.current;
      if (responseArea) responseArea.scrollTop = responseArea.scrollHeight;
    });
    return () => cancelAnimationFrame(frame);
  }, [feedback]);

  const closeVoice = () => document.querySelector("#voice-command-dialog")?.close();
  const updateTranscript = (value) => {
    if (captureActive) stopCapture("Transcript paused.");
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
          `Ordo asked: ${clarification.question.slice(0, 500)}`,
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
      <div><span className="voice-kicker">Ordo assistant</span><h2 id="voice-command-title">What can I help with?</h2></div>
      <button className="voice-icon-button" type="button" onClick={closeVoice} aria-label="Close voice input" title="Close"><X size={18} strokeWidth={1.8} /></button>
    </header>
    <section ref={responseAreaRef} className={`voice-response is-${feedback?.tone || captureState}`}>
      <span className="voice-state"><i aria-hidden="true"></i>{submitting ? "Thinking" : starting ? "Starting…" : live ? "Listening" : transcript ? "Ready" : awaitingAnswer ? "Tap mic to answer" : "Ask Ordo"}</span>
      <textarea ref={transcriptAreaRef} className="voice-transcript" value={transcript} onChange={(event) => updateTranscript(event.target.value)} aria-label="Calendar request" placeholder={live ? "Listening…" : starting ? "Connecting to microphone…" : awaitingAnswer ? "Tap the microphone, then speak…" : "Say or type a calendar request…"} rows="3" />
      {feedback
        ? <div className={`voice-feedback is-${feedback.tone}`} role="status" aria-live="polite"><strong>{feedback.title}</strong><p>{feedback.message}</p></div>
        : <small>“Gym every weekday at 7 AM” · “Delete my dentist appointment tomorrow”</small>}
    </section>
    <footer className="voice-console-footer">
      <div className="voice-orb-control">
        <VoiceBeam type="default" level={() => live ? 0.1 : 0} active={captureActive} colorVariant="colorful" theme={theme} borderRadius={999} strength={0.72} idle={live ? 0.09 : 0} flow={38} reach={1} spread={0.82}>
          <button className={`voice-orb${captureActive ? " is-live" : ""}`} type="button" onClick={() => captureActive ? stopCapture() : void startCapture({ preserveFeedback: awaitingAnswer })} aria-label={captureActive ? "Pause voice input" : awaitingAnswer ? "Answer by voice" : "Start voice input"} title={captureActive ? "Pause" : "Speak"}>
            {captureActive ? <Square size={19} fill="currentColor" strokeWidth={1.5} /> : <Mic size={23} strokeWidth={1.8} />}
          </button>
        </VoiceBeam>
        <span>{starting ? "Starting…" : live ? "Tap to pause" : awaitingAnswer ? "Tap to answer" : "Tap to speak"}</span>
      </div>
      <button className="voice-submit" type="button" disabled={!transcript.trim() || submitting} onClick={submitCommand}><span>{submitting ? "Working" : "Send"}</span><ArrowUp size={17} strokeWidth={2} /></button>
    </footer>
  </div>;
}

function VoiceLauncher() {
  return <button className="voice-island" type="button" onClick={() => window.semesterOpenVoiceCommand?.()} aria-label="Speak to Ordo" title="Speak to Ordo (Command J)">
    <span className="voice-island-icon" aria-hidden="true"><Mic size={17} strokeWidth={1.9} /></span><span>Speak to Ordo</span><kbd>⌘J</kbd>
  </button>;
}

const voiceRoot = document.querySelector("#voice-command-root");
if (voiceRoot) createRoot(voiceRoot).render(<VoiceTranscript />);
const voiceLauncherRoot = document.querySelector("#voice-launcher-root");
if (voiceLauncherRoot) createRoot(voiceLauncherRoot).render(<VoiceLauncher />);
