package com.servicemind.companion

import java.util.Locale

sealed interface VoiceCommand {
    data object Accept : VoiceCommand
    data object Decline : VoiceCommand
    data object Complete : VoiceCommand
    data object Dessert : VoiceCommand
    data object Water : VoiceCommand
    data object NotReady : VoiceCommand
    data class Availability(val value: String) : VoiceCommand
}

object VoiceCommandParser {
    private val phrases = mapOf(
        "accept" to VoiceCommand.Accept,
        "accept task" to VoiceCommand.Accept,
        "take task" to VoiceCommand.Accept,
        "take it" to VoiceCommand.Accept,
        "i accept" to VoiceCommand.Accept,
        "i will take it" to VoiceCommand.Accept,
        "i ll take it" to VoiceCommand.Accept,
        "decline" to VoiceCommand.Decline,
        "decline task" to VoiceCommand.Decline,
        "pass" to VoiceCommand.Decline,
        "complete" to VoiceCommand.Complete,
        "complete task" to VoiceCommand.Complete,
        "done" to VoiceCommand.Complete,
        "finished" to VoiceCommand.Complete,
        "dessert" to VoiceCommand.Dessert,
        "request dessert" to VoiceCommand.Dessert,
        "i want dessert" to VoiceCommand.Dessert,
        "water" to VoiceCommand.Water,
        "request water" to VoiceCommand.Water,
        "i want water" to VoiceCommand.Water,
        "not ready" to VoiceCommand.NotReady,
        "not ready yet" to VoiceCommand.NotReady,
        "they are not ready" to VoiceCommand.NotReady,
        "available" to VoiceCommand.Availability("available"),
        "set available" to VoiceCommand.Availability("available"),
        "i am available" to VoiceCommand.Availability("available"),
        "i m available" to VoiceCommand.Availability("available"),
        "busy" to VoiceCommand.Availability("busy"),
        "set busy" to VoiceCommand.Availability("busy"),
        "i am busy" to VoiceCommand.Availability("busy"),
        "i m busy" to VoiceCommand.Availability("busy"),
    )

    /** Only complete, explicitly recognized phrases map to an action. */
    fun parse(text: String): VoiceCommand? {
        if (text.length > 120) return null
        val normalized = text.lowercase(Locale.ROOT)
            .replace(Regex("[^a-z0-9\\s]"), " ")
            .trim()
            .replace(Regex("\\s+"), " ")
        return phrases[normalized]
    }
}
