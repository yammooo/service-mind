package com.servicemind.companion

import org.junit.Assert.assertEquals
import org.junit.Assert.fail
import org.junit.Test

class ServiceMindApiTest {
    @Test
    fun acceptsHttpAndHttpsOriginsAndTrimsTrailingSlash() {
        assertEquals("http://10.0.2.2:7001", ServiceMindApi.validateBaseUrl(" http://10.0.2.2:7001/ "))
        assertEquals("https://service.example.test", ServiceMindApi.validateBaseUrl("https://service.example.test"))
    }

    @Test
    fun rejectsPathQueryUserInfoAndUnsupportedScheme() {
        listOf(
            "http://service.example.test/api",
            "http://service.example.test?debug=true",
            "http://user@service.example.test",
            "ftp://service.example.test",
            "not a URL",
        ).forEach { url ->
            try {
                ServiceMindApi.validateBaseUrl(url)
                fail("Expected invalid URL to be rejected: $url")
            } catch (_: IllegalArgumentException) {
                // Expected.
            }
        }
    }
}
