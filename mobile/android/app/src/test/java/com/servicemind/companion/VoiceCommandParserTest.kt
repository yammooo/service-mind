package com.servicemind.companion

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class VoiceCommandParserTest {
    @Test
    fun recognizesExplicitAliasesAfterNormalization() {
        assertEquals(VoiceCommand.Accept, VoiceCommandParser.parse("  I'LL take it! "))
        assertEquals(VoiceCommand.Decline, VoiceCommandParser.parse("Decline task."))
        assertEquals(VoiceCommand.Complete, VoiceCommandParser.parse("DONE"))
        assertEquals(VoiceCommand.Dessert, VoiceCommandParser.parse("Request dessert"))
        assertEquals(VoiceCommand.Water, VoiceCommandParser.parse("I want water."))
        assertEquals(VoiceCommand.NotReady, VoiceCommandParser.parse("They are not ready!"))
        assertEquals(VoiceCommand.Availability("available"), VoiceCommandParser.parse("I'm available"))
        assertEquals(VoiceCommand.Availability("busy"), VoiceCommandParser.parse("Set busy"))
    }

    @Test
    fun unknownAndOverlongPhrasesDoNotMapToActions() {
        assertNull(VoiceCommandParser.parse("maybe take the task if it is nearby"))
        assertNull(VoiceCommandParser.parse("water and dessert"))
        assertNull(VoiceCommandParser.parse("a".repeat(121)))
    }
}
