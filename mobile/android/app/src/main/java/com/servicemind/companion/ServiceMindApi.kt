package com.servicemind.companion

import org.json.JSONObject
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URI
import java.net.URL
import java.net.URLEncoder

object ServiceMindApi {
    fun getState(baseUrl: String): JSONObject = request(baseUrl, "GET", "/api/state")

    fun setAvailability(baseUrl: String, workerId: String, availability: String): JSONObject =
        request(baseUrl, "POST", "/api/workers/${segment(workerId)}/availability", json("availability" to availability))

    fun taskAction(baseUrl: String, taskId: String, action: String, workerId: String): JSONObject {
        require(action in setOf("accept", "decline", "complete"))
        return request(baseUrl, "POST", "/api/tasks/${segment(taskId)}/$action", json("worker_id" to workerId))
    }

    fun requestItem(baseUrl: String, workerId: String, type: String): JSONObject {
        require(type in setOf("dessert", "water"))
        return request(baseUrl, "POST", "/api/tables/1/requests", json("worker_id" to workerId, "request_type" to type))
    }

    fun notReady(baseUrl: String): JSONObject =
        request(baseUrl, "POST", "/api/tables/1/correction", json("correction" to "not_ready"))

    fun validateBaseUrl(baseUrl: String): String {
        val uri = try { URI(baseUrl.trim().trimEnd('/')) } catch (_: Exception) {
            throw IllegalArgumentException("Enter a valid backend URL")
        }
        require(uri.scheme in setOf("http", "https") && !uri.host.isNullOrBlank()) {
            "Backend URL must start with http:// or https://"
        }
        require(uri.rawUserInfo == null && uri.rawQuery == null && uri.rawFragment == null && uri.path.isNullOrEmpty()) {
            "Enter only the backend origin, such as http://10.0.2.2:7001"
        }
        return uri.toString().trimEnd('/')
    }

    private fun request(baseUrl: String, method: String, path: String, body: JSONObject? = null): JSONObject {
        val base = validateBaseUrl(baseUrl)
        val connection = URL(base + path).openConnection() as HttpURLConnection
        try {
            connection.requestMethod = method
            connection.connectTimeout = 3_000
            connection.readTimeout = 3_000
            connection.setRequestProperty("Accept", "application/json")
            connection.setRequestProperty("Cache-Control", "no-cache")
            if (body != null) {
                connection.doOutput = true
                connection.setRequestProperty("Content-Type", "application/json; charset=utf-8")
                connection.outputStream.use { it.write(body.toString().toByteArray(Charsets.UTF_8)) }
            }
            val code = connection.responseCode
            val stream = if (code in 200..299) connection.inputStream else connection.errorStream
            val text = stream?.bufferedReader(Charsets.UTF_8)?.use { it.readText() }.orEmpty()
            if (code !in 200..299) {
                val detail = try { JSONObject(text).optString("detail") } catch (_: Exception) { "" }
                throw IOException(detail.ifBlank { "Backend returned HTTP $code" })
            }
            return JSONObject(text)
        } finally {
            connection.disconnect()
        }
    }

    private fun json(vararg values: Pair<String, String>) = JSONObject().apply {
        values.forEach { (key, value) -> put(key, value) }
    }

    private fun segment(value: String): String = URLEncoder.encode(value, Charsets.UTF_8.name())
}
