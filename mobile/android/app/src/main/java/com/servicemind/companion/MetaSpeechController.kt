package com.servicemind.companion

import android.app.Activity
import android.os.Bundle
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import androidx.lifecycle.LifecycleCoroutineScope
import com.meta.wearable.dat.core.Wearables
import com.meta.wearable.dat.core.selectors.AutoDeviceSelector
import com.meta.wearable.dat.core.session.DeviceSession
import com.meta.wearable.dat.core.session.DeviceSessionState
import com.meta.wearable.dat.mockdevice.MockDeviceKit
import com.meta.wearable.dat.mockdevice.api.GlassesModel
import com.meta.wearable.dat.mockdevice.api.MockGlasses
import com.meta.wearable.dat.speech.Speech
import com.meta.wearable.dat.speech.addSpeech
import com.meta.wearable.dat.speech.removeSpeech
import java.util.Locale
import java.util.UUID
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.collect
import kotlinx.coroutines.launch

/** MockDeviceKit speech bridge. Injected text uses the SDK transcription flow. */
class MetaSpeechController(
    private val activity: Activity,
    private val scope: LifecycleCoroutineScope,
    private val onStatus: (String) -> Unit,
    private val onTranscript: (String, Boolean, Long) -> Unit,
    private val onCancelWindow: (Long, String) -> Unit,
    private val onReady: () -> Unit,
) {
    private val mockKit = MockDeviceKit.getInstance(activity.applicationContext)
    private var glasses: MockGlasses? = null
    private var session: DeviceSession? = null
    private var speech: Speech? = null
    private var activeWindowId: Long? = null
    private var pendingPromptId: String? = null
    private var pendingPromptWindow: Long? = null
    private val utteranceCallbacks = mutableMapOf<String, () -> Unit>()
    private var listening = false
    private var closed = false
    private var ttsReady = false
    private var timeoutJob: Job? = null
    private var sessionStateJob: Job? = null
    private var sessionErrorJob: Job? = null
    private var transcriptionJob: Job? = null
    private var speechErrorJob: Job? = null
    private var tts: TextToSpeech? = null

    init {
        val textToSpeech = TextToSpeech(activity) { result ->
            ttsReady = result == TextToSpeech.SUCCESS
            if (ttsReady) tts?.setLanguage(Locale.getDefault())
        }
        tts = textToSpeech
        if (ttsReady) textToSpeech.setLanguage(Locale.getDefault())
        textToSpeech.setOnUtteranceProgressListener(object : UtteranceProgressListener() {
            override fun onStart(utteranceId: String) = Unit
            override fun onDone(utteranceId: String) {
                resumeAfterPrompt(utteranceId)
                finishUtterance(utteranceId)
            }
            override fun onError(utteranceId: String) {
                resumeAfterPrompt(utteranceId)
                finishUtterance(utteranceId)
            }
            override fun onStop(utteranceId: String, interrupted: Boolean) {
                finishUtterance(utteranceId)
            }
        })
        connectMockDevice()
    }

    private fun connectMockDevice() {
        try {
            mockKit.enable()
            val paired = mockKit.pairGlasses(GlassesModel.RAYBAN_META)
            if (paired.isFailure) {
                showStatus("Mock glasses pairing failed")
                return
            }
            glasses = paired.getOrThrow().also {
                it.powerOn()
                it.unfold()
                it.don()
            }
            showStatus("Connecting to simulated Ray-Ban Meta glasses…")

            val created = Wearables.createSession(AutoDeviceSelector())
            if (created.isFailure) {
                showStatus("Mock session failed: ${created.errorOrNull()?.description ?: "unknown error"}")
                return
            }
            val activeSession = created.getOrThrow()
            session = activeSession
            sessionStateJob = scope.launch {
                activeSession.state.collect { state ->
                    if (state == DeviceSessionState.STARTED && speech == null) attachSpeech(activeSession)
                    else if (state != DeviceSessionState.STARTED) {
                        activeWindowId?.let { cancelListening(it, "Mock glasses session paused; listen again.") }
                        if (state == DeviceSessionState.STOPPED) showStatus("Mock glasses session stopped")
                    }
                }
            }
            sessionErrorJob = scope.launch {
                activeSession.errors.collect { error ->
                    activeWindowId?.let { cancelListening(it, "Mock glasses session failed; no action taken.") }
                    showStatus("Mock session error: ${error.description}")
                }
            }
            activeSession.start()
        } catch (error: Exception) {
            showStatus("Mock glasses unavailable: ${error.message ?: "unknown error"}")
        }
    }

    private fun attachSpeech(activeSession: DeviceSession) {
        activeSession.addSpeech().onSuccess { attached ->
            if (closed) {
                attached.stop()
                activeSession.removeSpeech()
                return@onSuccess
            }
            speech = attached
            transcriptionJob = scope.launch {
                attached.transcriptions.collect { result ->
                    val windowId = activeWindowId ?: return@collect
                    if (!listening || result == null) return@collect
                    if (result.isFinal) {
                        stopListening(windowId)
                        dispatchTranscript(result.text, true, windowId)
                    } else {
                        dispatchTranscript(result.text, false, windowId)
                    }
                }
            }
            speechErrorJob = scope.launch {
                attached.errors.collect { error ->
                    if (error != null) {
                        activeWindowId?.let { cancelListening(it, "Mock speech failed; no action taken.") }
                        showStatus("Mock speech error: ${error.description}")
                    }
                }
            }
            showStatus("MockDevice ready · simulated glasses connected")
            activity.runOnUiThread { if (!closed) onReady() }
        }.onFailure { error, _ ->
            showStatus("Mock speech unavailable: ${error.description}")
        }
    }

    fun startListening(windowId: Long) {
        if (closed) return
        if (activeWindowId != null) {
            showStatus("Already listening; wait for that window to finish")
            return
        }
        if (speech == null) {
            showStatus("Waiting for MockDevice speech…")
            onCancelWindow(windowId, "Simulated glasses are not ready; nothing changed.")
            return
        }
        activeWindowId = windowId
        if (!ttsReady || tts == null) {
            beginListening(windowId)
            return
        }
        val utteranceId = "listen-${UUID.randomUUID()}"
        pendingPromptId = utteranceId
        pendingPromptWindow = windowId
        val result = tts?.speak("Listening for six seconds.", TextToSpeech.QUEUE_FLUSH, Bundle(), utteranceId)
        if (result == TextToSpeech.ERROR) {
            pendingPromptId = null
            pendingPromptWindow = null
            beginListening(windowId)
        }
    }

    private fun resumeAfterPrompt(utteranceId: String) {
        activity.runOnUiThread {
            if (pendingPromptId == utteranceId) {
                val windowId = pendingPromptWindow
                pendingPromptId = null
                pendingPromptWindow = null
                if (windowId != null) beginListening(windowId)
            }
        }
    }

    private fun beginListening(windowId: Long) {
        val current = speech
        if (closed || activeWindowId != windowId || current == null) return
        if (listening) return
        listening = true
        val result = current.start()
        if (result.isFailure) {
            listening = false
            activeWindowId = null
            val reason = "Could not start simulated speech; nothing changed."
            onCancelWindow(windowId, reason)
            showStatus("$reason ${result.errorOrNull()?.description ?: ""}".trim())
            return
        }
        showStatus("Listening on simulated glasses · 6 seconds")
        timeoutJob?.cancel()
        timeoutJob = scope.launch {
            delay(6_000)
            if (listening && activeWindowId == windowId) {
                stopListening(windowId)
                val reason = "No transcript received; nothing changed. Tap Listen to try again."
                onCancelWindow(windowId, reason)
                showStatus(reason)
                speak("No transcript received. Nothing changed.")
            }
        }
    }

    fun injectTranscript(text: String, windowId: Long) {
        if (!listening || activeWindowId != windowId) {
            showStatus("Tap Listen before injecting a mock transcript")
            return
        }
        if (text.isBlank() || text.length > 120) {
            showStatus("Transcript must contain 1–120 characters")
            return
        }
        try {
            glasses?.services?.speech?.simulateTranscription(text, isFinal = true, confidence = 0.9f)
                ?: showStatus("Mock speech is not connected")
        } catch (error: Exception) {
            showStatus("Mock transcript failed: ${error.message ?: "unknown error"}")
        }
    }

    fun cancelListening(windowId: Long, reason: String) {
        if (activeWindowId != windowId) return
        activeWindowId = null
        listening = false
        timeoutJob?.cancel()
        timeoutJob = null
        if (pendingPromptWindow == windowId) {
            pendingPromptId = null
            pendingPromptWindow = null
            tts?.stop()
        }
        speech?.stop()
        showStatus(reason)
        onCancelWindow(windowId, reason)
    }

    fun speak(text: String, onDone: () -> Unit = {}) {
        if (closed) { onDone(); return }
        activeWindowId?.let { cancelListening(it, "Speaking; no action taken.") }
        pendingPromptId = null
        pendingPromptWindow = null
        if (!ttsReady || tts == null) {
            activity.runOnUiThread(onDone)
            return
        }
        val utteranceId = "reply-${UUID.randomUUID()}"
        utteranceCallbacks[utteranceId] = onDone
        if (tts?.speak(text, TextToSpeech.QUEUE_FLUSH, null, utteranceId) == TextToSpeech.ERROR) {
            finishUtterance(utteranceId)
        }
    }

    fun cancelSpeechOutput() {
        pendingPromptId = null
        pendingPromptWindow = null
        tts?.stop()
    }

    private fun finishUtterance(utteranceId: String) {
        val callback = synchronized(utteranceCallbacks) { utteranceCallbacks.remove(utteranceId) } ?: return
        activity.runOnUiThread(callback)
    }

    private fun stopListening(windowId: Long) {
        if (activeWindowId != windowId) return
        activeWindowId = null
        listening = false
        timeoutJob?.cancel()
        timeoutJob = null
        speech?.stop()
    }

    private fun dispatchTranscript(text: String, isFinal: Boolean, windowId: Long) {
        activity.runOnUiThread { if (!closed) onTranscript(text, isFinal, windowId) }
    }

    private fun showStatus(text: String) {
        activity.runOnUiThread { if (!closed) onStatus(text) }
    }

    fun close() {
        activeWindowId?.let { cancelListening(it, "App closed; no action taken.") }
        closed = true
        pendingPromptId = null
        pendingPromptWindow = null
        timeoutJob?.cancel()
        transcriptionJob?.cancel()
        speechErrorJob?.cancel()
        sessionStateJob?.cancel()
        sessionErrorJob?.cancel()
        speech?.stop()
        session?.removeSpeech()
        session?.stop()
        mockKit.disable()
        tts?.stop()
        synchronized(utteranceCallbacks) { utteranceCallbacks.clear() }
        tts?.shutdown()
    }
}
