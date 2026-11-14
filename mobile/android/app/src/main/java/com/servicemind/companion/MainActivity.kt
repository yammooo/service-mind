package com.servicemind.companion

import androidx.activity.ComponentActivity
import androidx.lifecycle.lifecycleScope
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.text.Editable
import android.text.InputType
import android.text.TextWatcher
import android.view.View
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import org.json.JSONArray
import org.json.JSONObject
import java.util.concurrent.Executors

class MainActivity : ComponentActivity() {
    private data class ContextSnapshot(val url: String, val workerId: String, val generation: Int)
    private data class VoiceTarget(
        val context: ContextSnapshot,
        val windowId: Long,
        val taskId: String?,
        val taskStatus: String?,
        val offeredTo: String?,
        val assignedTo: String?,
    )

    private val io = Executors.newSingleThreadExecutor()
    private val main = Handler(Looper.getMainLooper())
    @Volatile private var stateFresh = false
    private var polling = false
    private var loadingState = false
    @Volatile private var backendUrl = "http://10.0.2.2:7001"
    @Volatile private var workerId = "A"
    @Volatile private var connectionGeneration = 0
    @Volatile private var urlDirty = false
    private var nextVoiceWindowId = 0L
    private var state: JSONObject? = null
    private var activeVoiceTarget: VoiceTarget? = null
    private var invalidVoiceMessage: String? = null
    @Volatile private var selectedTaskId: String? = null
    private var startListening: ((Long) -> Unit)? = null
    private var injectMockTranscript: ((String, Long) -> Unit)? = null
    private var cancelSpeechWindow: ((Long, String) -> Unit)? = null
    private var say: ((String, () -> Unit) -> Unit)? = null
    private var speechController: MetaSpeechController? = null
    private var mutationInFlight = false
    private var announcementInProgress = false
    private var announcementToken = 0L
    private val announcedTasks = mutableSetOf<String>()

    private lateinit var urlInput: EditText
    private lateinit var status: TextView
    private lateinit var tableStatus: TextView
    private lateinit var workerStatus: TextView
    private lateinit var taskList: LinearLayout
    private lateinit var workerAButton: Button
    private lateinit var workerBButton: Button
    private lateinit var availabilityButton: Button
    private lateinit var dessertButton: Button
    private lateinit var waterButton: Button
    private lateinit var notReadyButton: Button
    private lateinit var listenButton: Button
    private lateinit var mockTranscriptInput: EditText
    private lateinit var mockTranscriptButton: Button

    private val poll = object : Runnable {
        override fun run() {
            refreshState()
            if (polling) main.postDelayed(this, 1_000)
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        backendUrl = getPreferences(MODE_PRIVATE).getString("backend_url", backendUrl) ?: backendUrl
        showUi()
        initializeSpeech()
    }

    override fun onStart() {
        super.onStart()
        polling = true
        poll.run()
    }

    override fun onStop() {
        polling = false
        main.removeCallbacks(poll)
        cancelAnnouncement()
        speechController?.cancelSpeechOutput()
        invalidateVoiceWindow("App paused; listen again before using a voice command.")
        super.onStop()
    }

    override fun onDestroy() {
        speechController?.close()
        io.shutdownNow()
        super.onDestroy()
    }

    private fun showUi() {
        val content = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(20), dp(18), dp(20), dp(24))
        }
        setContentView(ScrollView(this).apply { addView(content) })

        content.addView(label("ServiceMind", 26f))
        content.addView(label("Worker companion · voice actions run after a final transcript", 14f))
        content.addView(label("Worker", 18f, top = 18))
        val workers = row().also(content::addView)
        workerAButton = button("Worker A") { chooseWorker("A") }
        workerBButton = button("Worker B") { chooseWorker("B") }
        workers.addView(workerAButton, weight())
        workers.addView(workerBButton, weight())

        content.addView(label("Backend URL", 18f, top = 16))
        val endpointRow = row().also(content::addView)
        urlInput = EditText(this).apply {
            setSingleLine(true)
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_URI
            setText(backendUrl)
            hint = "http://10.0.2.2:7001"
            contentDescription = "ServiceMind backend URL"
        }
        urlInput.addTextChangedListener(object : TextWatcher {
            override fun beforeTextChanged(s: CharSequence?, start: Int, count: Int, after: Int) = Unit
            override fun onTextChanged(s: CharSequence?, start: Int, before: Int, count: Int) = Unit
            override fun afterTextChanged(s: Editable?) {
                val dirty = s?.toString()?.trim()?.trimEnd('/') != backendUrl
                if (dirty && !urlDirty) {
                    stateFresh = false
                    invalidateVoiceWindow("Backend URL changed during listening; connect and listen again.")
                    status.text = "Backend URL changed; tap Connect to refresh"
                    renderState()
                }
                urlDirty = dirty
            }
        })
        endpointRow.addView(urlInput, weight())
        endpointRow.addView(button("Connect") { connect() })

        status = label("Connecting…", 14f, top = 8).also(content::addView)
        tableStatus = label("Waiting for fresh backend state", 18f, top = 16).also(content::addView)
        workerStatus = label("Worker state unavailable", 16f, top = 6).also(content::addView)
        availabilityButton = button("Set availability") { toggleAvailability() }.also(content::addView)

        content.addView(label("Tasks", 20f, top = 20))
        taskList = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        content.addView(taskList)

        content.addView(label("Requests and correction", 18f, top = 16))
        val requestRow = row().also(content::addView)
        dessertButton = button("Dessert") { requestItem("dessert") }
        waterButton = button("Water") { requestItem("water") }
        requestRow.addView(dessertButton, weight())
        requestRow.addView(waterButton, weight())
        notReadyButton = button("Not ready") { notReady() }.also(content::addView)

        content.addView(label("Voice", 18f, top = 16))
        content.addView(label("Meta DAT MockDevice · simulated glasses; no live microphone", 13f))
        listenButton = button("Talk to simulated glasses") { beginVoiceWindow() }.also(content::addView)
        mockTranscriptInput = EditText(this).apply {
            setSingleLine(true)
            filters = arrayOf(android.text.InputFilter.LengthFilter(120))
            hint = "Final transcript for mock tests"
            contentDescription = "Mock transcript to send through the Meta speech simulator"
        }.also(content::addView)
        mockTranscriptButton = button("Inject mock transcript") {
            val transcript = mockTranscriptInput.text.toString().trim()
            val windowId = activeVoiceTarget?.windowId
            when {
                transcript.isBlank() -> showMessage("Enter a mock transcript first")
                windowId == null -> showMessage("Tap Listen on glasses before injecting a transcript")
                injectMockTranscript == null -> showMessage("Mock speech is not ready")
                else -> injectMockTranscript?.invoke(transcript, windowId)
            }
        }.also(content::addView)

        workerAButton.isAllCaps = false
        workerBButton.isAllCaps = false
        chooseWorker(workerId)
        renderState()
    }

    private fun chooseWorker(id: String) {
        if (workerId != id) {
            workerId = id
            selectedTaskId = null
            cancelAnnouncement()
            invalidateVoiceWindow("Worker changed during listening; nothing changed. Listen again.")
        }
        workerAButton.isEnabled = id != "A"
        workerBButton.isEnabled = id != "B"
        speechController?.cancelSpeechOutput()
        renderState()
    }

    private fun connect() {
        try {
            val nextUrl = ServiceMindApi.validateBaseUrl(urlInput.text.toString())
            urlInput.setText(nextUrl)
            getPreferences(MODE_PRIVATE).edit().putString("backend_url", nextUrl).apply()
            connectionGeneration++
            backendUrl = nextUrl
            urlDirty = false
            cancelAnnouncement()
            state = null
            stateFresh = false
            selectedTaskId = null
            invalidateVoiceWindow("Backend changed during listening; nothing changed. Listen again.")
            speechController?.cancelSpeechOutput()
            status.text = "Connecting to $backendUrl"
            renderState()
            refreshState()
        } catch (error: Exception) {
            status.text = error.message ?: "Invalid backend URL"
        }
    }

    private fun refreshState() {
        if (loadingState || urlDirty) return
        loadingState = true
        val context = contextSnapshot()
        io.execute {
            try {
                val result = ServiceMindApi.getState(context.url)
                main.post {
                    if (!isCurrent(context)) return@post
                    state = result
                    stateFresh = true
                    if (activeVoiceTarget == null) status.text = "Connected · ${context.url.removePrefix("http://").removePrefix("https://")}"
                    renderState()
                }
            } catch (error: Exception) {
                main.post {
                    if (!isCurrent(context)) return@post
                    stateFresh = false
                    invalidateVoiceWindow("Backend state became stale during listening; nothing changed. Listen again.")
                    status.text = "Backend unavailable · state is stale${error.message?.let { ": $it" } ?: ""}"
                    renderState()
                }
            } finally {
                main.post { loadingState = false }
            }
        }
    }

    private fun renderState() {
        val current = state
        val fresh = stateFresh && current != null && !urlDirty
        val staleSuffix = if (fresh) "" else " · stale"
        val table = current?.optJSONObject("table")
        val customers = table?.optJSONArray("customers") ?: JSONArray()
        val plates = (0 until customers.length()).mapNotNull { index ->
            customers.optJSONObject(index)?.let { "${it.optString("plate_id")}: ${it.optString("food_state")}" }
        }.joinToString(" · ")
        tableStatus.text = if (current == null) "Waiting for fresh backend state" else
            "Table 1 · ${table?.optString("status", "unknown") ?: "unknown"}$staleSuffix\n$plates"

        val workers = current?.optJSONArray("workers") ?: JSONArray()
        val selectedWorker = (0 until workers.length()).mapNotNull { workers.optJSONObject(it) }
            .firstOrNull { it.optString("id") == workerId }
        val availability = selectedWorker?.optString("availability", "unknown") ?: "unknown"
        workerStatus.text = "Worker $workerId · $availability$staleSuffix"
        availabilityButton.text = if (availability == "available") "Set busy" else "Set available"
        val canMutate = fresh && !mutationInFlight && !announcementInProgress && activeVoiceTarget == null
        availabilityButton.isEnabled = canMutate
        dessertButton.isEnabled = canMutate
        waterButton.isEnabled = canMutate
        notReadyButton.isEnabled = canMutate
        listenButton.isEnabled = fresh && startListening != null && activeVoiceTarget == null &&
            !mutationInFlight && !announcementInProgress
        setMockTranscriptControls(fresh && injectMockTranscript != null && activeVoiceTarget != null && !mutationInFlight)

        taskList.removeAllViews()
        val tasks = current?.optJSONArray("tasks") ?: JSONArray()
        val relevant = (0 until tasks.length()).mapNotNull { tasks.optJSONObject(it) }.filter {
            (it.optString("status") == "offered" && it.optString("offered_to") == workerId) ||
                (it.optString("status") == "assigned" && it.optString("worker_id") == workerId)
        }
        if (stateFresh && selectedTaskId != null && relevant.none { it.optString("id") == selectedTaskId }) {
            selectedTaskId = null
            invalidateVoiceWindow("Selected task changed during listening; nothing changed. Listen again.")
        }
        if (relevant.isEmpty()) {
            taskList.addView(label(if (fresh) "No tasks for Worker $workerId" else "No current task actions while state is stale", 14f, top = 4))
        }
        relevant.forEach { task ->
            val taskId = task.optString("id")
            val type = task.optString("type")
            val title = if (type == "check_table") "Check Table 1" else "${task.optString("request_type").replaceFirstChar { it.uppercase() }} at Table 1"
            val card = LinearLayout(this).apply {
                orientation = LinearLayout.VERTICAL
                setPadding(dp(12), dp(8), dp(12), dp(8))
            }
            card.addView(label("$title · ${task.optString("status")}", 16f))
            val selected = selectedTaskId == taskId
            val selectButton = button(if (selected) "Selected for voice" else "Use for voice") {
                if (canMutate) selectTaskForVoice(taskId)
            }.apply { isEnabled = canMutate }
            card.addView(selectButton)
            val actions = row().also(card::addView)
            if (task.optString("status") == "offered") {
                actions.addView(button("Accept") { taskAction(taskId, "accept") }.apply { isEnabled = canMutate }, weight())
                actions.addView(button("Decline") { taskAction(taskId, "decline") }.apply { isEnabled = canMutate }, weight())
            } else {
                actions.addView(button("Complete") { taskAction(taskId, "complete") }.apply { isEnabled = canMutate }, weight())
            }
            taskList.addView(card)
        }
        announceNewTasks()
    }

    private fun setMockTranscriptControls(enabled: Boolean) {
        mockTranscriptInput.isEnabled = enabled
        mockTranscriptButton.isEnabled = enabled
        mockTranscriptInput.visibility = if (enabled && injectMockTranscript != null) View.VISIBLE else View.GONE
        mockTranscriptButton.visibility = if (enabled && injectMockTranscript != null) View.VISIBLE else View.GONE
    }

    private fun selectTaskForVoice(id: String) {
        selectedTaskId = if (selectedTaskId == id) null else id
        invalidateVoiceWindow("Task selection changed during listening; nothing changed. Listen again.")
        renderState()
    }

    private fun toggleAvailability() {
        val current = state ?: return showMessage("Connect to the backend first")
        val selectedWorker = (current.optJSONArray("workers") ?: JSONArray()).let { list ->
            (0 until list.length()).mapNotNull { list.optJSONObject(it) }.firstOrNull { it.optString("id") == workerId }
        } ?: return showMessage("Worker state unavailable")
        val next = if (selectedWorker.optString("availability") == "available") "busy" else "available"
        mutate("Availability changed") { url, id -> ServiceMindApi.setAvailability(url, id, next) }
    }

    private fun requestItem(type: String) = mutate("${type.replaceFirstChar { it.uppercase() }} requested") { url, id ->
        ServiceMindApi.requestItem(url, id, type)
    }

    private fun notReady() = mutate("Not ready reported") { url, _ -> ServiceMindApi.notReady(url) }

    private fun taskAction(id: String, action: String) = mutate("Task $action") { url, worker ->
        ServiceMindApi.taskAction(url, id, action, worker)
    }

    private fun mutate(success: String, action: (String, String) -> JSONObject) {
        if (!stateFresh || urlDirty || mutationInFlight) return showMessage("State is stale or an action is already pending")
        submitMutation(contextSnapshot(), success, action)
    }

    private fun submitMutation(
        context: ContextSnapshot,
        success: String,
        action: (String, String) -> JSONObject,
        voiceTarget: VoiceTarget? = null,
        spokenResponse: String? = null,
    ) {
        if (!stateFresh || urlDirty || mutationInFlight) return showMessage("State is stale or an action is already pending")
        mutationInFlight = true
        status.text = "Sending…"
        renderState()
        io.execute {
            if (!stateFresh || !isCurrent(context) || (voiceTarget != null && selectedTaskId != voiceTarget.taskId)) {
                main.post {
                    mutationInFlight = false
                    if (isCurrent(context)) status.text = "Context changed or state is stale; nothing changed"
                    renderState()
                }
                return@execute
            }
            try {
                val result = action(context.url, context.workerId)
                main.post {
                    mutationInFlight = false
                    if (!isCurrent(context)) {
                        renderState()
                        return@post
                    }
                    state = result
                    stateFresh = true
                    status.text = success
                    if (spokenResponse != null && say != null && polling) {
                        val token = ++announcementToken
                        announcementInProgress = true
                        renderState()
                        say?.invoke(spokenResponse) {
                            main.post {
                                if (token != announcementToken) return@post
                                announcementInProgress = false
                                renderState()
                                if (isCurrent(context) && polling) announceNewTasks()
                            }
                        }
                    } else {
                        announcementInProgress = false
                        renderState()
                        if (polling) announceNewTasks()
                    }
                }
            } catch (error: Exception) {
                main.post {
                    mutationInFlight = false
                    if (isCurrent(context)) {
                        stateFresh = false
                        status.text = "Action result not confirmed; refreshing backend state"
                        renderState()
                        if (spokenResponse != null && say != null && polling) {
                            val token = ++announcementToken
                            announcementInProgress = true
                            renderState()
                            say?.invoke("I couldn't confirm that action. Check the current state.") {
                                main.post {
                                    if (token == announcementToken) {
                                        announcementInProgress = false
                                        renderState()
                                    }
                                }
                            }
                        }
                        refreshState()
                    }
                }
            }
        }
    }

    private fun beginVoiceWindow() {
        if (!stateFresh || state == null || urlDirty || mutationInFlight) return showMessage("Wait for a fresh backend poll before listening")
        val start = startListening ?: return showMessage("Speech is not ready")
        val selectedTask = selectedTaskId?.let { findTask(state, it) }
        val voiceTarget = VoiceTarget(
            context = contextSnapshot(),
            windowId = ++nextVoiceWindowId,
            taskId = selectedTaskId,
            taskStatus = selectedTask?.optString("status"),
            offeredTo = selectedTask?.optString("offered_to"),
            assignedTo = selectedTask?.optString("worker_id"),
        )
        invalidVoiceMessage = null
        activeVoiceTarget = voiceTarget
        status.text = "Preparing to listen for Worker $workerId"
        renderState()
        start(voiceTarget.windowId)
    }

    /** Pass the window ID from setSpeechActions' start callback; late results from older windows are ignored. */
    fun processTranscript(text: String, isFinal: Boolean, windowId: Long) {
        runOnUiThread {
            val target = activeVoiceTarget
            if (target == null) {
                if (isFinal) {
                    status.text = invalidVoiceMessage ?: "No active listening window; nothing changed"
                    invalidVoiceMessage = null
                }
                return@runOnUiThread
            }
            if (target.windowId != windowId) {
                if (isFinal) status.text = "Late transcript ignored; nothing changed"
                return@runOnUiThread
            }
            if (!isFinal) {
                status.text = "Hearing: $text"
                return@runOnUiThread
            }
            activeVoiceTarget = null
            if (!isCurrent(target.context) || target.taskId != selectedTaskId) {
                status.text = "Worker, backend, or task changed during listening; nothing changed. Listen again."
                say?.invoke("Worker, backend, or task changed. Nothing changed.") {}
                renderState()
                return@runOnUiThread
            }
            if (!stateFresh) {
                status.text = "Backend state is stale; nothing changed. Listen again after refresh."
                say?.invoke("Backend state is stale. Nothing changed.") {}
                renderState()
                return@runOnUiThread
            }
            status.text = "Heard: $text"
            when (val command = VoiceCommandParser.parse(text)) {
                VoiceCommand.Accept -> taskForVoice(target, "offered", "accept")
                VoiceCommand.Decline -> taskForVoice(target, "offered", "decline")
                VoiceCommand.Complete -> taskForVoice(target, "assigned", "complete")
                VoiceCommand.Dessert -> voiceRequest(target, "dessert")
                VoiceCommand.Water -> voiceRequest(target, "water")
                VoiceCommand.NotReady -> submitMutation(
                    target.context,
                    "Not ready reported",
                    { url, _ -> ServiceMindApi.notReady(url) },
                    voiceTarget = target,
                    spokenResponse = "Not ready reported.",
                )
                is VoiceCommand.Availability -> submitMutation(
                    target.context,
                    "Availability changed",
                    { url, id -> ServiceMindApi.setAvailability(url, id, command.value) },
                    voiceTarget = target,
                    spokenResponse = "Worker is ${command.value}.",
                )
                null -> {
                    status.text = "No matching command; nothing changed"
                    say?.invoke("No matching command. Nothing changed.") {}
                }
            }
            renderState()
        }
    }

    /** The speech adapter must cancel the same window ID when its response window times out or speech stops. */
    fun cancelVoiceWindow(windowId: Long, reason: String = "Listening ended; no action taken.") {
        runOnUiThread {
            if (activeVoiceTarget?.windowId == windowId) invalidateVoiceWindow(reason)
        }
    }

    /** The speech adapter starts an SDK window and routes its final callbacks with that same ID. */
    fun setSpeechActions(
        start: (Long) -> Unit,
        injectMock: ((String, Long) -> Unit)? = null,
        cancel: ((Long, String) -> Unit)? = null,
        speak: ((String, () -> Unit) -> Unit)? = null,
    ) {
        startListening = start
        injectMockTranscript = injectMock
        cancelSpeechWindow = cancel
        say = speak
        renderState()
    }

    private fun voiceRequest(target: VoiceTarget, type: String) {
        val spoken = "${type.replaceFirstChar { it.uppercase() }} requested."
        submitMutation(
            target.context,
            "${type.replaceFirstChar { it.uppercase() }} requested",
            { url, id -> ServiceMindApi.requestItem(url, id, type) },
            voiceTarget = target,
            spokenResponse = spoken,
        )
    }

    private fun taskForVoice(target: VoiceTarget, expectedStatus: String, action: String) {
        val taskId = target.taskId
        if (taskId == null) {
            val message = "Select a task with Use for voice, then listen again. Nothing changed."
            status.text = message
            say?.invoke(message) {}
            return
        }
        val task = findTask(state, taskId)
        val currentlyMatches = task != null && when (expectedStatus) {
            "offered" -> task.optString("status") == "offered" && task.optString("offered_to") == target.context.workerId
            else -> task.optString("status") == "assigned" && task.optString("worker_id") == target.context.workerId
        }
        val matchedAtListen = when (expectedStatus) {
            "offered" -> target.taskStatus == "offered" && target.offeredTo == target.context.workerId
            else -> target.taskStatus == "assigned" && target.assignedTo == target.context.workerId
        }
        if (!currentlyMatches || !matchedAtListen) {
            val message = "Selected task changed. Nothing changed. Select it again and listen."
            status.text = message
            say?.invoke(message) {}
            return
        }
        val response = when (action) {
            "accept" -> "Task accepted."
            "decline" -> "Task declined."
            else -> "Task completed."
        }
        submitMutation(
            target.context,
            "Task $action",
            { url, worker -> ServiceMindApi.taskAction(url, taskId, action, worker) },
            voiceTarget = target,
            spokenResponse = response,
        )
    }

    private fun findTask(current: JSONObject?, id: String): JSONObject? {
        val tasks = current?.optJSONArray("tasks") ?: return null
        return (0 until tasks.length()).mapNotNull { tasks.optJSONObject(it) }.firstOrNull { it.optString("id") == id }
    }

    private fun contextSnapshot() = ContextSnapshot(backendUrl, workerId, connectionGeneration)

    private fun isCurrent(context: ContextSnapshot): Boolean =
        context.generation == connectionGeneration && context.url == backendUrl && context.workerId == workerId && !urlDirty

    private fun invalidateVoiceWindow(reason: String) {
        val target = activeVoiceTarget ?: return
        activeVoiceTarget = null
        invalidVoiceMessage = reason
        status.text = reason
        cancelSpeechWindow?.invoke(target.windowId, reason)
        renderState()
    }

    private fun initializeSpeech() {
        lateinit var controller: MetaSpeechController
        controller = MetaSpeechController(
            activity = this,
            scope = lifecycleScope,
            onStatus = { message -> status.text = message },
            onTranscript = ::processTranscript,
            onCancelWindow = ::cancelVoiceWindow,
            onReady = {
                main.post {
                    setSpeechActions(
                        start = controller::startListening,
                        injectMock = controller::injectTranscript,
                        cancel = controller::cancelListening,
                        speak = controller::speak,
                    )
                }
            },
        )
        speechController = controller
    }

    private fun announceNewTasks() {
        if (!polling || !stateFresh || urlDirty || mutationInFlight || activeVoiceTarget != null || announcementInProgress) return
        val speaker = say ?: return
        val tasks = state?.optJSONArray("tasks") ?: return
        val next = (0 until tasks.length()).mapNotNull { tasks.optJSONObject(it) }.firstOrNull {
            it.optString("status") == "offered" && it.optString("offered_to") == workerId &&
                "${workerId}:${it.optString("id")}" !in announcedTasks
        } ?: return
        val taskId = next.optString("id")
        val context = contextSnapshot()
        val key = "${workerId}:$taskId"
        val description = if (next.optString("type") == "check_table") "check Table 1" else
            "fulfill the ${next.optString("request_type")} request at Table 1"
        val token = ++announcementToken
        selectedTaskId = taskId
        announcementInProgress = true
        renderState()
        speaker("Worker ${context.workerId}, new task: $description. I will listen after this message. Say accept or decline.") {
            main.post {
                if (token != announcementToken) return@post
                announcementInProgress = false
                val currentTask = findTask(state, taskId)
                val stillOffered = currentTask?.optString("status") == "offered" &&
                    currentTask.optString("offered_to") == context.workerId
                if (polling && isCurrent(context) && stateFresh && stillOffered && activeVoiceTarget == null) {
                    announcedTasks += key
                    renderState()
                    beginVoiceWindow()
                } else {
                    if (!stillOffered) selectedTaskId = null
                    renderState()
                    announceNewTasks()
                }
            }
        }
    }

    private fun cancelAnnouncement() {
        announcementToken++
        announcementInProgress = false
    }

    private fun showMessage(text: String) { status.text = text }

    private fun label(text: String, size: Float, top: Int = 0) = TextView(this).apply {
        this.text = text
        textSize = size
        if (top > 0) setPadding(0, dp(top), 0, 0)
    }

    private fun button(text: String, action: () -> Unit) = Button(this).apply {
        this.text = text
        isAllCaps = false
        setOnClickListener { action() }
    }

    private fun row() = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
    private fun weight() = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f)
    private fun dp(value: Int) = (value * resources.displayMetrics.density).toInt()
}
