package com.blindnav.app.shadow

import android.content.Context
import android.os.Build
import android.util.Log
import org.json.JSONObject
import java.io.BufferedWriter
import java.io.File
import java.io.FileWriter
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.TimeZone
import java.util.concurrent.ArrayBlockingQueue
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicLong

/**
 * 真机传感器/延迟日志（任务书第六节）。
 *
 * 只写文件，不播报，不参与任何决策。事件类型：
 *   SESSION  一次会话的开始/结束、设备与 ARCore 能力、开关状态
 *   FRAME    一帧的四种时间戳 + 分辨率 + 旋转 + 相机/深度可用性
 *   POSE     tracking state、failure reason、平移旋转、内参
 *   DEPTH    可用性、分辨率、覆盖率、置信度分布、近距离统计
 *   INFERENCE 某个模型在某帧上的 preprocess/inference/postprocess 耗时
 *   SHADOW   影子决策：would_have_said / would_stop / lateral / heading / path health
 *   CAPTURE  最后十米关系数据的一条记录
 *
 * 落盘位置：Android/data/com.blindnav.nav/files/shadowlog/session-&lt;id&gt;/
 * 取回方式：adb pull（见 tools/android/pull_shadow_log.sh）
 */
class BlindNavSensorLogger(private val context: Context) {

    private val stampFormat = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss.SSSZ", Locale.US)
        .apply { timeZone = TimeZone.getTimeZone("UTC") }

    val sessionDir: File = File(logRoot(context), "session-" + SESSION_ID).apply { mkdirs() }
    private val eventsFile = File(sessionDir, "events.jsonl")
    private val queue = ArrayBlockingQueue<String>(QUEUE_CAPACITY)
    private val dropped = AtomicLong(0)
    private val written = AtomicLong(0)
    @Volatile private var running = true
    private val latencies = HashMap<String, LatencyAccumulator>()

    private val writer = Thread({ run() }, "BlindNavSensorLogger").apply {
        isDaemon = true
        start()
    }

    private fun run() {
        var writerOk: BufferedWriter? = null
        try {
            writerOk = BufferedWriter(FileWriter(eventsFile, true), 64 * 1024)
            val out = writerOk
            val batch = ArrayList<String>(256)
            while (running || queue.isNotEmpty()) {
                val first = queue.poll(200, TimeUnit.MILLISECONDS) ?: continue
                batch.add(first)
                queue.drainTo(batch, BATCH_LIMIT)
                for (line in batch) { out.write(line); out.write("\n") }
                out.flush()
                written.addAndGet(batch.size.toLong())
                batch.clear()
            }
        } catch (e: Throwable) {
            Log.w(TAG, "sensor logger stopped: ${e.message}")
        } finally {
            runCatching { writerOk?.close() }
        }
    }

    /** 一条事件。时间戳一律由调用方给（见 [SensorClock]），这里只补 wall-clock 便于人读。 */
    fun log(type: String, fields: Map<String, Any?>) {
        if (!running) return
        val o = JSONObject()
        o.put("wall", stampFormat.format(Date()))
        o.put("type", type)
        for ((k, v) in fields) if (v != null) o.put(k, v)
        if (!queue.offer(o.toString())) dropped.incrementAndGet()
    }

    fun latency(stage: String): LatencyAccumulator = synchronized(latencies) {
        latencies.getOrPut(stage) { LatencyAccumulator(stage) }
    }

    /** 会话结束时写 meta.json：设备能力、开关、各阶段延迟分位数、丢事件计数。 */
    fun close(extra: Map<String, Any?> = emptyMap()) {
        val summary = JSONObject()
        summary.put("session_id", SESSION_ID)
        summary.put("events_written", written.get())
        summary.put("events_dropped", dropped.get())
        summary.put("queue_capacity", QUEUE_CAPACITY)
        val lat = JSONObject()
        synchronized(latencies) { for ((k, v) in latencies) lat.put(k, v.json()) }
        summary.put("latency_ms", lat)
        for ((k, v) in extra) if (v != null) summary.put(k, v)
        // meta 直接同步写：close 之后 adb 就要拉走了，不能排在队列后面。
        runCatching { File(sessionDir, "meta.json").writeText(summary.toString(2)) }
        running = false
        runCatching { writer.join(1500) }
    }

    /**
     * 有界延迟样本池。
     *
     * 三个刻意的取舍：
     *  1. **存微秒 Double，不存整毫秒**：亚毫秒阶段（背投影、mask 索引）截断成整 ms 会全变 0。
     *  2. **随机替换而不是顺序轮转**：轮转覆盖会和 30FPS 定周期混叠，分位数会偏向某个相位。
     *  3. **额外给前半/后半中位数**：热降频是"越跑越慢"，全局中位数会把两个体制平均掉，
     *     正好回答不了任务书第十八节要问的问题。
     */
    class LatencyAccumulator(private val name: String, private val capacity: Int = 8000) {
        private val samples = ArrayList<Double>(capacity)
        private var seen = 0L
        private var total = 0.0
        private var max = 0.0
        private val random = java.util.Random(0x5EEDL)

        @Synchronized fun add(nanos: Long) {
            val micros = nanos / 1000.0
            seen += 1
            total += micros
            if (micros > max) max = micros
            if (samples.size < capacity) samples.add(micros)
            else {
                val replace = random.nextInt(seen.toInt().coerceAtMost(Int.MAX_VALUE - 1))
                if (replace < capacity) samples[replace] = micros
            }
        }

        @Synchronized fun json(): JSONObject {
            val sorted = samples.sorted()
            fun pct(p: Double): Double = if (sorted.isEmpty()) -1.0
            else sorted[((sorted.size - 1) * p).toInt().coerceIn(0, sorted.size - 1)]
            val half = sorted.size / 2
            return JSONObject()
                .put("name", name)
                .put("count", seen)
                .put("unit", "us")
                .put("mean_us", if (seen == 0L) 0.0 else total / seen)
                .put("max_us", max)
                .put("median_us", pct(0.50))
                .put("p90_us", pct(0.90))
                .put("p95_us", pct(0.95))
                .put("first_half_median_us", if (half > 0) sorted[half / 2] else -1.0)
                .put("second_half_median_us", if (half > 0) sorted[half + half / 2] else -1.0)
                .put("reservoir", sorted.size)
        }
    }

    companion object {
        private const val TAG = "BlindNavShadow"
        private const val QUEUE_CAPACITY = 20_000
        private const val BATCH_LIMIT = 512

        /** 进程级会话号：同一次 App 运行内所有 session 目录可区分，且带 UTC 起始时间便于对齐视频。 */
        val SESSION_ID: String = SimpleDateFormat("yyyyMMdd-HHmmss", Locale.US)
            .apply { timeZone = TimeZone.getTimeZone("UTC") }.format(Date()) +
            "-" + android.os.Process.myPid()

        fun logRoot(context: Context): File =
            File(context.getExternalFilesDir(null) ?: context.filesDir, "shadowlog").apply { mkdirs() }

        /** 设备能力快照（任务书交付项 B）。不采集任何个人标识。 */
        fun deviceCapability(context: Context): JSONObject = JSONObject()
            .put("manufacturer", Build.MANUFACTURER)
            .put("model", Build.MODEL)
            .put("device", Build.DEVICE)
            .put("android_sdk", Build.VERSION.SDK_INT)
            .put("abi", Build.SUPPORTED_ABIS?.firstOrNull())
            .put("app_version", runCatching {
                context.packageManager.getPackageInfo(context.packageName, 0).versionName
            }.getOrNull())
    }
}
