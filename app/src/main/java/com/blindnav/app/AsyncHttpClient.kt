package com.blindnav.app

import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.net.HttpURLConnection
import java.net.SocketTimeoutException
import java.net.URL
import java.util.concurrent.ArrayBlockingQueue
import java.util.concurrent.Callable
import java.util.concurrent.FutureTask
import java.util.concurrent.ScheduledFuture
import java.util.concurrent.ScheduledThreadPoolExecutor
import java.util.concurrent.ThreadFactory
import java.util.concurrent.ThreadPoolExecutor
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger

/** Bounded native HTTP transport. Callbacks must only enqueue lightweight UI work.
 * The UI consumer must recheck its SafetyGate ticket when executing posted work.
 * cancelAll/close suppress callbacks; the page owns rejecting cancelled promises.
 */
class AsyncHttpClient(
    private val token: () -> Long,
    private val allowed: (Long) -> Boolean,
    private val deliver: (String, String) -> Unit,
    private val connectionFactory: (URL) -> HttpURLConnection = {
        it.openConnection() as HttpURLConnection
    }
) : AutoCloseable {
    private class Request(val id: String, val ticket: Long) {
        // Accessed only under lock; identity is this object, never the reusable id.
        var terminal = false
        var connection: HttpURLConnection? = null
        var future: FutureTask<Unit>? = null
        var deadline: ScheduledFuture<*>? = null
        val expiresAt = System.nanoTime() + TimeUnit.SECONDS.toNanos(20)
    }

    private val lock = Any()
    private val active = mutableMapOf<String, Request>()
    private var closed = false
    private val sequence = AtomicInteger()
    private val threads = ThreadFactory { work ->
        Thread(work, "blindnav-http-${sequence.incrementAndGet()}").apply { isDaemon = true }
    }
    // Cancelled uncooperative connections still occupy one of these two workers.
    // At most two current requests may wait behind them; no unbounded task queue.
    private val workers = ThreadPoolExecutor(2, 2, 0L, TimeUnit.MILLISECONDS,
        ArrayBlockingQueue<Runnable>(2), threads)
    private val deadlines = ScheduledThreadPoolExecutor(1, threads).apply {
        removeOnCancelPolicy = true
    }
    // A platform disconnect must not block the caller or the deadline clock.
    private val cleanup = ThreadPoolExecutor(2, 2, 0L, TimeUnit.MILLISECONDS,
        ArrayBlockingQueue<Runnable>(2), threads)

    fun request(id: String, method: String, url: String, headersJson: String, body: String?) {
        val ticket = token()
        synchronized(lock) {
            if (!allowed(ticket)) return
            if (closed) { emit(id, error("closed")); return }
            if (active.size >= 2 || active.containsKey(id)) {
                emit(id, error("busy")); return
            }
            val task = Request(id, ticket)
            active[id] = task
            val future = FutureTask(Callable { execute(task, method, url, headersJson, body) })
            task.future = future
            task.deadline = deadlines.schedule({ timeout(task) }, 20, TimeUnit.SECONDS)
            workers.execute(future)
        }
    }

    fun cancelAll() {
        val cancelled = synchronized(lock) { retireAll() }
        cancelled.forEach { interruptAndDisconnect(it) }
    }

    override fun close() {
        val cancelled = synchronized(lock) {
            if (closed) return
            closed = true
            retireAll()
        }
        cancelled.forEach { interruptAndDisconnect(it) }
        workers.shutdownNow()
        deadlines.shutdownNow()
        // Already submitted disconnects are allowed to finish.
        cleanup.shutdown()
    }

    private fun retireAll(): List<Request> {
        val retired = active.values.toList()
        active.clear()
        retired.forEach {
            it.terminal = true
            it.deadline?.cancel(false)
            it.future?.let { future -> future.cancel(true); workers.remove(future) }
        }
        return retired
    }

    private fun current(task: Request): Boolean =
        !task.terminal && active[task.id] === task && allowed(task.ticket)

    private fun execute(task: Request, method: String, rawUrl: String, headersJson: String, body: String?) {
        var connection: HttpURLConnection? = null
        try {
            if (!synchronized(lock) { current(task) }) return
            val url = validateUrl(rawUrl) ?: run { complete(task, error("invalid_request")); return }
            if (method != "GET" && method != "POST") {
                complete(task, error("invalid_request")); return
            }
            val headers = try { parseHeaders(headersJson) } catch (_: Exception) {
                complete(task, error("invalid_request")); return
            }
            if (method == "GET" && !body.isNullOrEmpty()) {
                complete(task, error("invalid_request")); return
            }
            if (!synchronized(lock) { current(task) }) return
            connection = connectionFactory(url)
            synchronized(lock) {
                task.connection = connection
                if (!current(task)) return
            }
            connection.requestMethod = method
            connection.connectTimeout = 8_000
            connection.readTimeout = 12_000
            connection.instanceFollowRedirects = false
            headers.forEach { (name, value) -> connection.setRequestProperty(name, value) }
            if (!synchronized(lock) { current(task) }) return
            if (method == "POST" && body != null) {
                connection.doOutput = true
                connection.outputStream.use { it.write(body.toByteArray(Charsets.UTF_8)) }
            }
            val status = connection.responseCode
            if (status !in 200..299) {
                complete(task, error("HTTP $status"))
                return
            }
            val bytes = ByteArrayOutputStream()
            connection.inputStream.use { stream ->
                val chunk = ByteArray(8192)
                while (true) {
                    if (!synchronized(lock) { current(task) }) return
                    val count = stream.read(chunk)
                    if (count < 0) break
                    if (count > MAX_RESPONSE_BYTES - bytes.size()) {
                        complete(task, error("response_too_large")); return
                    }
                    bytes.write(chunk, 0, count)
                }
            }
            complete(task, bytes.toString("UTF-8"))
        } catch (_: SocketTimeoutException) {
            complete(task, error("timeout"))
        } catch (_: Exception) {
            // Exception text can contain URLs, credentials or remote response data.
            complete(task, error("network_error"))
        } finally {
            synchronized(lock) {
                if (active[task.id] === task) {
                    active.remove(task.id)
                    task.terminal = true
                    task.deadline?.cancel(false)
                }
                task.connection = null
            }
            runCatching { connection?.disconnect() }
        }
    }

    private fun complete(task: Request, text: String, cancelWork: Boolean = false): Boolean = synchronized(lock) {
        if (task.terminal || active[task.id] !== task) return false
        val permitted = allowed(task.ticket)
        // Retire cancelled queue entries before a new request can acquire lock.
        if (cancelWork) task.future?.let { it.cancel(true); workers.remove(it) }
        active.remove(task.id)
        task.terminal = true
        task.deadline?.cancel(false)
        if (permitted) emit(task.id, if (System.nanoTime() >= task.expiresAt) error("timeout") else text)
        true
    }

    private fun timeout(task: Request) {
        if (complete(task, error("timeout"), cancelWork = true)) interruptAndDisconnect(task)
    }

    private fun interruptAndDisconnect(task: Request) {
        task.future?.let { it.cancel(true); workers.remove(it) }
        val connection = synchronized(lock) { task.connection } ?: return
        // Queue is bounded. If occupied by pathological platform disconnects,
        // the worker's finally block remains the fallback owner of this resource.
        runCatching { cleanup.execute { runCatching { connection.disconnect() } } }
    }

    private fun emit(id: String, text: String) {
        runCatching { deliver(id, text) }
    }

    private fun validateUrl(raw: String): URL? = try {
        URL(raw).takeIf {
            it.protocol == "https" && it.host in ALLOWED_HOSTS &&
                it.userInfo == null && (it.port == -1 || it.port == 443) &&
                it.ref == null && raw.none(Char::isWhitespace)
        }
    } catch (_: Exception) { null }

    private fun parseHeaders(text: String): List<Pair<String, String>> {
        if (text.isBlank()) return emptyList()
        val json = JSONObject(text)
        return json.keys().asSequence().map { name ->
            require(HEADER_NAME.matches(name) && !name.equals("Host", ignoreCase = true))
            val value = json.getString(name)
            require(value.none { it == '\r' || it == '\n' || it == '\u0000' })
            name to value
        }.toList()
    }

    private fun error(code: String) = JSONObject().put("error", code).toString()

    companion object {
        private const val MAX_RESPONSE_BYTES = 2 * 1024 * 1024
        private val ALLOWED_HOSTS = setOf("restapi.amap.com", "api.deepseek.com")
        private val HEADER_NAME = Regex("[!#$%&'*+.^_`|~0-9a-zA-Z-]+")
    }
}
