package com.blindnav.app

import android.content.Context
import android.graphics.Bitmap
import android.util.Base64
import android.util.Log
import com.blindnav.app.last10m.EvidenceSource
import com.blindnav.app.last10m.TargetPoiProfile
import com.blindnav.app.last10m.VisionContext
import com.blindnav.app.last10m.VisionPrompt
import com.blindnav.app.last10m.VisionReasoner
import com.blindnav.app.last10m.Last10mVisionResult
import com.blindnav.app.last10m.VisionResultParser
import java.io.ByteArrayOutputStream
import java.net.HttpURLConnection
import java.net.URL
import java.util.concurrent.atomic.AtomicBoolean

/**
 * 云端 VLM 视觉源（任务书第 5 节第一选择 / 第 6 节）。
 *
 * 用的是**项目里已经在用的那个服务**：DeepSeek 的 key 早就在 App 自己的
 * `assets/index.html` 里（对话层一直在调），`AsyncHttpClient` 的主机白名单里也早就有
 * `api.deepseek.com`。今晚不注册新平台、不比供应商、不换 key，只是把这条已经存在的通道
 * 接到最后十米上。
 *
 * 三条工程边界：
 *  1. **单次调用答完多项**（门/关系/地面/门类型/按钮/场景摘要），
 *     避免四个字段来自四张不同时刻的画面，拼出一条现实中没同时存在过的证据链；
 *  2. **`thinking` 必须 disabled**：实测不禁思考时，输出 token 会被推理吃光，
 *     content 直接为空——那不是"模型没看见"，是协议用错了；
 *  3. **失败与不可信要分开**：网络/HTTP/超时返回 null（编排器降级到回放或人工），
 *     返回了但 JSON 不合法则由解析层给 `parseOk=false` 的整体 UNKNOWN。
 *
 * 这个类只产"事实候选"，不做任何到达/运动/安全判断；
 * 结论仍然只能由 `Last10mStateMachine` + `ArrivalAuthority` 出。
 */
class DeepSeekVisionReasoner(
    context: Context,
    private val connectTimeoutMs: Int = 5_000,
    private val readTimeoutMs: Int = 20_000,
    private val model: String = "deepseek-flash",
    private val maxTokens: Int = 1_000
) : VisionReasoner {

    private val appContext = context.applicationContext
    private val closed = AtomicBoolean(false)

    override val source: EvidenceSource = EvidenceSource.REMOTE_VLM

    override fun analyze(jpeg: ByteArray?, frameId: Long, timestampMs: Long,
                         target: TargetPoiProfile, context: VisionContext): Last10mVisionResult? {
        if (closed.get() || jpeg == null || jpeg.isEmpty()) return null
        val key = VlmKeyProvider.key(appContext)
        if (key == null) {
            Log.w(TAG, "VLM_SKIPPED reason=NO_KEY —— 没有可用 key，整源退出，由编排器降级")
            return null
        }
        val started = System.nanoTime()
        val body = buildBody(jpeg, target, context)
        val raw = runCatching { post(body, key) }.getOrElse {
            Log.w(TAG, "VLM_FAILED reason=${it.javaClass.simpleName}:${it.message?.take(80)}")
            return null
        }
        val latency = (System.nanoTime() - started) / 1_000_000L
        if (raw == null) {
            Log.w(TAG, "VLM_FAILED reason=empty_or_non2xx latency=${latency}ms")
            return null
        }
        val content = extractContent(raw)
        if (content.isNullOrBlank()) {
            // 典型症状：thinking 没关，completion token 全花在推理上，content 是空的。
            Log.w(TAG, "VLM_FAILED reason=empty_content latency=${latency}ms raw=${raw.take(200)}")
            return Last10mVisionResult.unknown(frameId, timestampMs, source, "EMPTY_CONTENT")
        }
        val result = VisionResultParser.parse(content, frameId, timestampMs, source, latency)
        Log.w(TAG, "VLM_OK frame=$frameId latency=${latency}ms doors=${result.doors.size} " +
            "texts=${result.texts.size} relations=${result.relations.size} " +
            "ground=${result.ground.visible} type=${result.doorType} button=${result.buttonLocation} " +
            "violations=${result.violations}")
        return result
    }

    private fun buildBody(jpeg: ByteArray, target: TargetPoiProfile,
                          context: VisionContext): String {
        val b64 = Base64.encodeToString(jpeg, Base64.NO_WRAP)
        val user = VisionPrompt.user(target, context)
        // 手写 JSON 转义，避免为了一个字符串拼接再引一个依赖
        return "{" +
            "\"model\":\"$model\"," +
            "\"temperature\":0," +
            "\"max_tokens\":$maxTokens," +
            "\"thinking\":{\"type\":\"disabled\"}," +
            "\"response_format\":{\"type\":\"json_object\"}," +
            "\"messages\":[{\"role\":\"system\",\"content\":${jsonString(VisionPrompt.SYSTEM)}}," +
            "{\"role\":\"user\",\"content\":[" +
            "{\"type\":\"text\",\"text\":${jsonString(user)}}," +
            "{\"type\":\"image_url\",\"image_url\":{\"url\":\"data:image/jpeg;base64,$b64\"}}" +
            "]}]}"
    }

    private fun post(body: String, apiKey: String): String? {
        val url = URL(ENDPOINT)
        // 与 AsyncHttpClient 同一份主机白名单：新代码不给自己开任意出网的口子
        if (url.host != ALLOWED_HOST) {
            Log.w(TAG, "VLM_FAILED reason=host_not_allowed ${url.host}")
            return null
        }
        val conn = url.openConnection() as HttpURLConnection
        return try {
            conn.requestMethod = "POST"
            conn.connectTimeout = connectTimeoutMs
            conn.readTimeout = readTimeoutMs
            conn.doOutput = true
            conn.setRequestProperty("Content-Type", "application/json")
            // key 只进请求头。日志里永远只打长度，不打内容。
            conn.setRequestProperty("Authorization", "Bearer $apiKey")
            conn.outputStream.use { it.write(body.toByteArray(Charsets.UTF_8)) }
            val code = conn.responseCode
            val stream = if (code in 200..299) conn.inputStream else conn.errorStream
            val text = stream?.readBytes()?.toString(Charsets.UTF_8)
            if (code !in 200..299) {
                Log.w(TAG, "VLM_FAILED http=$code body=${text?.take(160)}")
                null
            } else text
        } catch (e: Throwable) {
            Log.w(TAG, "VLM_FAILED ${e.javaClass.simpleName}:${e.message?.take(80)}")
            null
        } finally {
            runCatching { conn.disconnect() }
        }
    }

    private fun extractContent(raw: String): String? {
        // 结构：{"choices":[{"message":{"content":"..."}}]}，用项目自带的 MiniJson 解
        val root = runCatching { com.blindnav.app.last10m.MiniJson.parseObject(raw) }.getOrNull() ?: return null
        val choices = (root["choices"] as? List<*>) ?: return null
        val first = choices.firstOrNull() as? Map<*, *> ?: return null
        val message = first["message"] as? Map<*, *> ?: return null
        return message["content"] as? String
    }

    override fun close() { closed.set(true) }

    companion object {
        private const val TAG = "BlindNavLast10m"
        const val ALLOWED_HOST = "api.deepseek.com"
        const val ENDPOINT = "https://api.deepseek.com/chat/completions"

        /** 与电脑阶段 benchmark 同一个取景口径：长边 768、quality 88，避免两侧结果不可比。 */
        const val MAX_SIDE = 768
        const val JPEG_QUALITY = 88

        /** 帧 → JPEG 字节（等比缩到长边 [MAX_SIDE]）。*/
        fun encodeJpeg(source: Bitmap, maxSide: Int = MAX_SIDE, quality: Int = JPEG_QUALITY): ByteArray {
            val scale = minOf(1.0, maxSide.toDouble() / maxOf(source.width, source.height))
            val bitmap = if (scale < 1.0) {
                runCatching {
                    Bitmap.createScaledBitmap(source,
                        maxOf(1, (source.width * scale).toInt()),
                        maxOf(1, (source.height * scale).toInt()), true)
                }.getOrDefault(source)
            } else source
            val out = ByteArrayOutputStream()
            bitmap.compress(Bitmap.CompressFormat.JPEG, quality, out)
            if (bitmap !== source) bitmap.recycle()
            return out.toByteArray()
        }

        private fun jsonString(s: String): String {
            val sb = StringBuilder("\"")
            for (c in s) {
                when (c) {
                    '"' -> sb.append("\\\"")
                    '\\' -> sb.append("\\\\")
                    '\n' -> sb.append("\\n")
                    '\r' -> sb.append("\\r")
                    '\t' -> sb.append("\\t")
                    else -> if (c < ' ') sb.append("\\u").append(String.format("%04x", c.code)) else sb.append(c)
                }
            }
            return sb.append('"').toString()
        }
    }
}

/**
 * 从 App 自己的 assets 里取 DeepSeek key。
 *
 * 这里没有新增密钥，也没有把它复制到第二个地方：
 * 对话层一直在用 `assets/index.html` 里那把 `const DS = "sk-…"`，
 * 现在只是让 Kotlin 侧能读同一份。找不到就返回 null，整源退出（不编一个假 key）。
 */
object VlmKeyProvider {

    @Volatile private var cached: String? = null
    @Volatile private var looked = false

    fun key(context: Context): String? {
        if (looked) return cached
        synchronized(this) {
            if (looked) return cached
            val text = runCatching {
                context.applicationContext.assets.open("index.html")
                    .readBytes().toString(Charsets.UTF_8)
            }.getOrNull()
            val match = text?.let { Regex("""\b(?:const|let|var)\s+DS\s*=\s*['"]([^'"]{8,})['"]""").find(it) }
            cached = match?.groupValues?.get(1)
            looked = true
            Log.w(TAG, "VLM_KEY ${if (cached == null) "NOT_FOUND（云端视觉源整源退出）" else "found(len=" + cached?.length + ")"}")
            return cached
        }
    }

    fun reset() { cached = null; looked = false }

    private const val TAG = "BlindNavLast10m"
}
